#!/usr/bin/env python3
"""sync-hooks-json.py — single source of truth for the hooks.json dispatch locator.

hooks/hooks.json embeds one plugin-root locator loop per hook command because
neither runtime injects a guaranteed plugin-root env var into hook commands.
Those commands were historically copy-pasted by hand (six copies that could
drift silently). THIS script is now the single source: the locator candidates
below are the ONLY place an install path is edited, and `--write` regenerates
every hook command in hooks/hooks.json from them.

    python3 scripts/sync-hooks-json.py --write   # regenerate hooks/hooks.json
    python3 scripts/sync-hooks-json.py --check   # verify hooks.json is in sync (CI)

Usage: python3 sync-hooks-json.py (--write|--check) [hooks.json]

The candidate list is cross-checked against hooks/scripts/run-hook.sh by
scripts/check-plugin.sh §1b — run-hook.sh remains the authoritative DISCOVERY
logic (two-phase, glob-newest-first), while this file owns the DISPATCH list
that has to be reachable from static JSON.

Matcher scope is checked here too: PreToolUse/PostToolUse entries must stay
scoped to write tools (a dropped matcher would fire the guard on every
read-only call and multiply per-session firings). SessionStart/Stop are
session-level and intentionally unscoped.
"""

import argparse
import json
import re
import sys
from pathlib import Path

# --- Canonical dispatch locators (SINGLE SOURCE OF TRUTH) ---------------------
# Tokens are the shell candidate roots the hook command loop searches, in the
# exact order hooks.json must embed them. Edit here, then run --write.
# Layout note (Claude Code v2.1.x, verified 2026-09-30 on a live project):
# the versioned cache is ~/.claude/plugins/cache/<market>/<plugin>/<version>/
# (e.g. cache/metodoloji/metodoloji/1.2.5/) and the live checkout is
# ~/.claude/plugins/marketplaces/metodoloji/. The old
# cache/yunusgungor/metodoloji/* path never exists on this layout — it stays
# ONLY as a last-resort legacy fallback. Fixed checkouts come before versioned
# globs so a stale cache can never shadow a live checkout.
_CANDIDATES = (
    '"$CLAUDE_PLUGIN_ROOT"',
    '"$METODOLOJI_PLUGIN_ROOT"',
    '"."',
    '"$PWD"',
    '"/workspace"',
    '"/workspace/metodoloji"',
    '"$HOME/.claude/plugins/marketplaces/metodoloji"',
    '"$HOME/.claude/plugins/cache/metodoloji/metodoloji/"*',
    '"$HOME/.claude/plugins/cache/metodoloji/"*/*',
    '"$HOME/.claude/plugins/cache/yunusgungor/metodoloji/"*',
    '"$HOME/.openhands/plugins/installed/metodoloji"',
)

# Hook name -> whether the command forwards the runtime tool name as "$1".
# bootstrap needs no tool name; every PreToolUse/PostToolUse/Stop hook forwards
# the tool name it was fired for (unused by hook-entry.sh, kept for parity).
_HOOKS = {
    "bootstrap": False,
    "pre": True,
    "guard": True,
    "quality": True,
    "deploy": True,
    "audit": True,
    "stop": True,
}

# Hooks dispatched from hooks.json (subset of _HOOKS). check()/regenerate()
# validate exactly this set so the merged PreToolUse entry doesn't trip
# "missing dispatch hook" errors for the retired per-gate entries.
# NOTE: PreToolUse is served by ONE combined entry ("pre" → guard+quality+
# deploy in a single python process) instead of three separate guard/quality/
# deploy processes per tool call. guard/quality/deploy stay registered (and
# in hook-entry.sh + main.py) as the engine's internal gate names and for
# direct/manual invocation — but hooks.json no longer dispatches them.
_DISPATCHED_HOOKS = ("bootstrap", "pre", "audit", "stop")

_HOOK_RE = re.compile(r'run-hook\.sh"\s+([a-z_]+)')

_DEFAULT_MANIFEST = Path(__file__).resolve().parent.parent / "hooks" / "hooks.json"


def hook_command(hook: str) -> str:
    """Build the canonical hook command for `hook` from _CANDIDATES."""
    if hook not in _HOOKS:
        raise ValueError(f"unknown hook {hook!r} (expected one of {sorted(_HOOKS)})")
    arg = ' "$1"' if _HOOKS[hook] else ""
    return (
        "sh -c 'for d in "
        + " ".join(_CANDIDATES)
        + '; do [ -f "$d/hooks/scripts/run-hook.sh" ] && exec sh '
        + '"$d/hooks/scripts/run-hook.sh" '
        + hook
        + arg
        + "; done; exit 0'"
    )


def _walk_command_nodes(obj):
    """Yield every dict node that carries a hook "command" string."""
    if isinstance(obj, dict):
        if "command" in obj:
            yield obj
        for v in obj.values():
            yield from _walk_command_nodes(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_command_nodes(v)


def _expected(node: dict) -> tuple[str, str | None] | None:
    """Classify a hook command node.

    Returns None for a node that is not a run-hook.sh dispatch command, else
    (hook, canonical). canonical is None when the command is dispatch-shaped
    but names an UNKNOWN hook (hand-renamed) — callers report it explicitly
    instead of guessing or crashing."""
    m = _HOOK_RE.search(node["command"])
    if not m:
        return None
    hook = m.group(1)
    if hook not in _HOOKS:
        return hook, None
    return hook, hook_command(hook)


def regenerate(manifest: Path) -> None:
    data = json.loads(manifest.read_text(encoding="utf-8"))
    seen = set()
    unknown = set()
    for node in _walk_command_nodes(data):
        got = _expected(node)
        if got is None:
            print(f"  skip non-dispatch command: {node['command'][:60]!r}")
            continue
        hook, canonical = got
        if canonical is None:
            unknown.add(hook)
            continue
        node["command"] = canonical
        seen.add(hook)
    missing = sorted(set(_DISPATCHED_HOOKS) - seen)
    if missing:
        print(f"  ERROR: dispatch commands for {missing} not found in hooks.json")
    if unknown:
        print(f"  ERROR: unknown dispatch hook name(s) {sorted(unknown)} in hooks.json")
    if missing or unknown:
        sys.exit(1)
    manifest.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"[OK] hooks.json regenerated from canonical locator: {manifest}")


# Tool-gate entries must stay matcher-scoped to write tools. An entry
# without a matcher fires on EVERY tool call (reads included); one that
# lost "Write" no longer covers the canonical write tool. Either regression
# silently multiplies per-session hook firings.
#
# "PowerShell" is required alongside "Write": Claude Code exposes a
# `PowerShell` shell tool on Windows, and a matcher that omits it leaves every
# PowerShell write (`Set-Content src/a.py …`) outside the guard — a real
# session on win32 ran its shell work through `PowerShell` and the gate never
# saw a single call. The union must cover BOTH shell vocabularies (Bash and
# PowerShell) so platform cannot pick the enforcement level.
_SCOPED_EVENTS = ("PreToolUse", "PostToolUse")


def _check_matcher_scope(data: dict) -> int:
    problems = 0
    events = data.get("hooks", {})
    for event in _SCOPED_EVENTS:
        entries = events.get(event, [])
        if not entries:
            problems += 1
            print(f"  MISS: {event} has no hook entries")
            continue
        for entry in entries:
            matcher = entry.get("matcher", "")
            if not matcher:
                problems += 1
                print(f"  MISS: {event} entry fires unscoped (no matcher) — "
                      "scope it to write tools")
                continue
            for required in ("Write", "PowerShell"):
                if required not in matcher:
                    problems += 1
                    print(f"  MISS: {event} matcher {matcher!r} does not cover "
                          f"the {required} tool")
    return problems


def check(manifest: Path) -> int:
    data = json.loads(manifest.read_text(encoding="utf-8"))
    problems = 0
    seen = set()
    unknown = set()
    drifted = []
    for node in _walk_command_nodes(data):
        got = _expected(node)
        if got is None:
            continue
        hook, canonical = got
        if canonical is None:
            unknown.add(hook)
            continue
        seen.add(hook)
        if node["command"] != canonical:
            drifted.append(hook)
    if drifted:
        problems += 1
        print(f"  MISS: hook command(s) {sorted(drifted)} drifted from the canonical locator")
        print("        run: python3 scripts/sync-hooks-json.py --write")
    missing = sorted(set(_DISPATCHED_HOOKS) - seen)
    if missing:
        problems += 1
        print(f"  MISS: dispatch commands for {missing} missing from hooks.json")
    if unknown:
        problems += 1
        print(f"  MISS: unknown dispatch hook name(s) {sorted(unknown)} in hooks.json")
    problems += _check_matcher_scope(data)
    if not problems:
        print(f"[OK] hooks.json dispatch commands byte-identical to canonical locator ({len(seen)} hooks)")
        print("[OK] PreToolUse/PostToolUse matcher-scoped to write tools")
    return 1 if problems else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Regenerate/verify hooks.json dispatch commands")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--write", action="store_true", help="rewrite hooks.json from the canonical locator")
    g.add_argument("--check", action="store_true", help="verify hooks.json matches the canonical locator")
    ap.add_argument("manifest", nargs="?", type=Path, default=_DEFAULT_MANIFEST,
                    help="path to hooks.json (default: alongside this script's repo)")
    args = ap.parse_args()

    if not args.manifest.is_file():
        print(f"ERROR: manifest not found: {args.manifest}")
        return 1
    if args.write:
        regenerate(args.manifest)
        return 0
    return check(args.manifest)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""The command-not-found signature is a shell diagnostic (E-179).

    python3 scripts/bench/bench_audit_signature_shapes.py --samples docs/experiments/E-179/raw/samples.jsonl
    python3 scripts/bench/bench_audit_signature_shapes.py --broken   # rule-7 control

E-177 narrowed the glob signature to what a shell prints but left its sibling
`command_not_found` as a bare phrase match anywhere in the output. That phrase
is ordinary content in this checkout: `hooks/engine/modules/audit.py` carries it
in the failure table and in the hint text, so a SUCCESSFUL

    grep -rn "command not found" hooks/engine/modules/audit.py

is classified as a failed command (reproduced 2026-11-17), and the shipped
trail already holds `sed`/`cat` reads of module source recorded as
`command_not_found`. The class is only correct when the output has the shape a
shell prints — a shell name at line start, then the phrase on the same line.

Every probe drives the tree's own module, and the expected verdict is stated
once here. `--broken` runs the same probes against the pre-E-177 tree at
205fa6d, whose bare phrase regex fires on every prose payload.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
PREFIX_REV = "205fa6d"  # last commit before the failure table was narrowed at all
BROKEN = False

_PREFIX_CACHE: pathlib.Path | None = None


def _tree() -> pathlib.Path:
    return ROOT if not BROKEN else _prefix_tree()


def _prefix_tree() -> pathlib.Path:
    global _PREFIX_CACHE
    if _PREFIX_CACHE is not None:
        return _PREFIX_CACHE
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="audit-signature-prefix-"))
    out = subprocess.run(["git", "-C", str(ROOT), "archive", PREFIX_REV],
                         capture_output=True, stdin=subprocess.DEVNULL)
    if out.returncode != 0:
        raise RuntimeError(f"cannot archive {PREFIX_REV}: {out.stderr[:200]!r}")
    subprocess.run(["tar", "-x", "-C", str(tmp)], input=out.stdout, check=True)
    _PREFIX_CACHE = tmp
    return tmp


# The expected verdict per probe, stated once. None means "no signature".
EXPECTED = {
    "bash_line_flat": "command_not_found",
    "bash_line_nested": "command_not_found",
    "zsh_trailing": "command_not_found",
    "sh_numbered": "command_not_found",
    "preceded_by_output": "command_not_found",
    "glob_still_routed": "glob_nomatch",
    "module_source": None,
    "grep_hit_line": None,
    "docstring_prose": None,
    "harness_label": None,
    "fish_vocabulary": None,
    "audit_prose_silent": None,
}

# The real trail row (2026-11-17): the shell names the word, then the phrase.
_NESTED_BASH_LINE = {
    "output": {"exit": 0, "truncated": False,
               "output": "/bin/bash: line 7: _prints_only: command not found\n"
                         "'python3 -c \"print(1)\"' ([], False)"},
    "status": "ok",
}

_INPUTS = {
    "bash_line_flat": "bash: line 1: rg: command not found",
    "bash_line_nested": _NESTED_BASH_LINE,
    "zsh_trailing": {"stdout": "zsh: command not found: rg", "stderr": ""},
    "sh_numbered": "sh: 1: foo: command not found",
    "preceded_by_output": "total 8\n=== procs ===\nbash: foo: command not found\n",
    "glob_still_routed": "zsh: no matches found: docs/*.md",
    "grep_hit_line": {
        "stdout": '104:#   B-002 S1  `$M` set in an earlier call expands empty '
                  '→ command not found\n'
                  '137:     re.compile(r"(?i)command not found"),\n',
        "stderr": "",
    },
    "docstring_prose": {
        "stdout": "The hint ends with the words command not found, which is "
                  "documentation of the class, not a shell error.",
        "stderr": "",
    },
    "harness_label": {"stdout": "", "stderr": "",
                      "returnCodeInterpretation": "command not found"},
    "fish_vocabulary": {"stdout": "fish: Unknown command: rg", "stderr": ""},
}

# The module's own source mentions the phrase; reading it must stay silent.
_MODULE_SOURCE_PROBE = "module_source"


def _end_to_end(name: str) -> tuple[bool, str]:
    """Drive audit() itself: the agent-visible warning and the trail row."""
    root = pathlib.Path(tempfile.mkdtemp(prefix="audit-signature-root-"))
    try:
        os.environ["CLAUDE_PROJECT_DIR"] = str(root)
        os.environ.pop("OPENHANDS_PROJECT_DIR", None)
        from modules.audit import audit
        payload = {"tool_name": "Bash",
                   "tool_input": {"command": 'grep -rn "command not found" hooks'},
                   "tool_response": dict(_INPUTS["grep_hit_line"])}
        res = audit(payload)
        log = root / ".metodoloji" / "logs" / "hook-audit.log"
        rows = ([json.loads(line) for line in
                 log.read_text(encoding="utf-8").splitlines()] if log.is_file() else [])
        warns = list(res.get("methodology_warnings") or [])
        ok = not rows and not warns
        return ok, f"rows={len(rows)} warnings={warns}"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _driver(name: str, tree: pathlib.Path) -> tuple[bool, str]:
    sys.path.insert(0, str(tree / "hooks" / "engine"))
    sys.path.insert(0, str(tree / "hooks" / "scripts"))

    if name.startswith("audit_"):
        return _end_to_end(name)

    from modules.audit import command_failure
    if name == _MODULE_SOURCE_PROBE:
        source = (tree / "hooks" / "engine" / "modules" / "audit.py").read_text(
            encoding="utf-8")
        payload = {"stdout": source, "stderr": ""}
    else:
        payload = _INPUTS[name]
    got = command_failure("terminal", payload)
    kind = got[0] if got else None
    want = EXPECTED[name]
    return kind == want, f"kind={kind} want={want}"


CASES = list(EXPECTED)


def _run_probe(name: str) -> tuple[bool, str]:
    proc = subprocess.run(
        [sys.executable, str(pathlib.Path(__file__).resolve()),
         "--driver", name, "--tree", str(_tree())],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180, stdin=subprocess.DEVNULL)
    try:
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return False, f"driver crashed: {proc.stderr.strip()[:200]}"
    return bool(payload["ok"]), str(payload["detail"])


def main() -> int:
    global BROKEN
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--broken", action="store_true", help="rule-7 control arm")
    ap.add_argument("--samples", help="write per-check JSONL samples here")
    ap.add_argument("--driver", help=argparse.SUPPRESS)
    ap.add_argument("--tree", help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.driver:
        ok, detail = _driver(args.driver, pathlib.Path(args.tree))
        print(json.dumps({"ok": ok, "detail": detail}, ensure_ascii=False))
        return 0

    BROKEN = args.broken
    results = []
    for name in CASES:
        try:
            ok, detail = _run_probe(name)
        except Exception as exc:  # a crashed probe is a failed check, never a pass
            ok, detail = False, f"probe crashed: {type(exc).__name__}: {exc}"
        results.append({"check": name, "ok": bool(ok), "detail": detail})
        print(f"  {'ok  ' if ok else 'FAIL'}  {name}: {detail}")

    passed = sum(1 for r in results if r["ok"])
    if args.samples:
        path = pathlib.Path(args.samples)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n"
                                for r in results), encoding="utf-8")
    print(f"command_signature_accuracy={passed / len(results):.2f} "
          f"({passed}/{len(results)})")
    if _PREFIX_CACHE is not None:
        shutil.rmtree(_PREFIX_CACHE, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Record-skeleton installer — the one-time init, runnable in any harness.

`/metodoloji:init` is a slash command: Claude Code exposes it, other runtimes
(OpenHands, plain terminals) do not. A real session (2026-09-21) ran PRD →
architecture → spec → epics with the skeleton still absent, because every
SessionStart could only *suggest* the slash command and nothing could execute
it. This script is the executable half of that contract, so the hint bootstrap
prints is a copy-pasteable command everywhere:

    python3 {metodoloji-root}/bmad/scripts/skeleton.py --status
    python3 {metodoloji-root}/bmad/scripts/skeleton.py --install

Install is idempotent and never overwrites an existing file (pass --force to
replace copies deliberately). Completion is recorded by
{project-root}/.metodoloji/initialized — the marker bootstrap.sh and
check-plugin.sh §6d read to prove init happened exactly once.

Exit codes: --status exits 0 for a consistent board (installed, or a project
that never initialized — init is opt-in) and 1 on drift (skeleton without
marker, or marker without skeleton). --install exits 0 when the skeleton ends
up in place, 1 when it could not write.
"""

import argparse
import datetime as _dt
import json
import os
import pathlib
import re
import shutil
import sys

# The skeleton contract, in one place: commands/init.md documents it, this
# script executes it, check-plugin.sh §6d verifies its outcome — and every
# restatement of the constants below (the runbook's directory list and template
# table, the session-start probe loop) is measured against them by
# declaration_problems() / --audit-declarations, so "keep them in step" is a
# check and not a request (E-047).
DIRECTORIES = (
    "docs/experiments",
    "docs/development/stories",
    "docs/development/incidents",
    "docs/development/structural",
    "docs/quality",
    "docs/research",
    "docs/research/external",
    "docs/design",
    "docs/design/prds",
    "docs/design/ux-designs",
    "docs/design/architecture",
    "scratch",
)

# (template under {metodoloji-root}/templates, destination under {project-root})
TEMPLATE_PAIRS = (
    ("_template_E.md", "docs/experiments/_template.md"),
    ("_template_BD.md", "docs/research/_template.md"),
    ("external-context-README.md", "docs/research/external/README.md"),
    ("external-context.md", "docs/research/external/_template.md"),
    ("_template_C.md", "docs/design/_template.md"),
    ("_template_IR.md", "docs/development/_template_IR.md"),
    ("_template_SP.md", "docs/development/_template_SP.md"),
    ("_template_QR.md", "docs/development/_template_QR.md"),
    ("_template_PR.md", "docs/development/_template_PR.md"),
    ("_template_S.md", "docs/development/stories/_template_S.md"),
    ("_template_PM.md", "docs/development/incidents/_template_PM.md"),
    ("_template_STRUCT.md", "docs/development/structural/_template_STRUCT.md"),
    ("README.md", "docs/development/README.md"),
    ("tech-debt.md", "docs/development/tech-debt.md"),
    ("scratch-README.md", "scratch/README.md"),
)

# Any ONE of these proves the skeleton was installed (the §6d probe set).
SKELETON_PROBES = (
    "docs/experiments/_template.md",
    "docs/development/_template_IR.md",
    "docs/development/stories/_template_S.md",
)

# E-047: the contract above is DECLARED in three shipped places besides this
# one, and this module is the only canonical owner:
#   * `commands/init.md` lists the directories and the template table a reader
#     trusts (step 2/3) and promised the runbook and the script "can never
#     drift" — while nothing measured it;
#   * `hooks/scripts/bootstrap.sh` probes the same three paths at SessionStart
#     and said "keep the two in step";
#   * `scripts/check-plugin.sh` §6d carried a literal second copy of the probe
#     set (and a stale comment counting "two template paths").
# Drift there is silent by construction: a probe set that gains a path here
# leaves the session hint advertising a FULL init on a project whose (new)
# skeleton exists, and leaves §6d reporting ERROR on a healthy skeleton that
# lacks the old trio — the mailjs 2026-09-23 shape, one level up. The copies
# cannot be removed everywhere (bootstrap.sh is a per-session hot path and must
# not spawn a second interpreter), so the agreement is measured instead:
# `declaration_problems()` is that measurement and `--audit-declarations` is
# its CLI (run by check-plugin.sh §6d, driven by the bench).
DECLARATION_SITES = (
    "commands/init.md",
    "hooks/scripts/bootstrap.sh",
    "scripts/check-plugin.sh",
)

MARKER_REL = ".metodoloji/initialized"


def plugin_root() -> pathlib.Path:
    """{metodoloji-root} — derived from this file's location, never guessed."""
    return pathlib.Path(__file__).resolve().parent.parent.parent


def plugin_version(root: pathlib.Path | None = None) -> str:
    manifest = (root or plugin_root()) / ".claude-plugin" / "plugin.json"
    try:
        return str(json.loads(manifest.read_text(encoding="utf-8")).get("version")
                   or "unknown")
    except (OSError, ValueError):
        return "unknown"


def resolve_project_root(explicit: str | None) -> pathlib.Path:
    """Explicit flag → runtime env var → cwd (blackboard.py's convention)."""
    if explicit:
        return pathlib.Path(explicit).expanduser().resolve()
    for var in ("CLAUDE_PROJECT_DIR", "OPENHANDS_PROJECT_DIR"):
        value = os.environ.get(var)
        if value and value.strip():
            return pathlib.Path(value).expanduser().resolve()
    return pathlib.Path.cwd().resolve()


def _read_site(plugin: pathlib.Path, rel: str) -> str:
    try:
        return (plugin / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _runbook_block(text: str, start: str, end: str) -> str:
    """The runbook region between two phrases (empty when either is gone)."""
    begin = text.find(start)
    if begin < 0:
        return ""
    begin += len(start)
    stop = text.find(end, begin)
    return text[begin:] if stop < 0 else text[begin:stop]


def _declared_directories(runbook: str) -> list[str]:
    """The directory bullets of the init runbook's step 2 (backticked, trailing /).

    Scoped to the step: the same document legitimately mentions `docs/bmad/`
    ("do not create it in the target project"), which is not a skeleton
    directory and must not read as one.
    """
    block = _runbook_block(runbook, "The installer creates these directories",
                           "The installer copies these templates")
    return [p.rstrip("/") for p in re.findall(r"`([A-Za-z0-9_./-]+/)`", block)]


def _declared_template_pairs(runbook: str) -> list[tuple[str, str]]:
    """The `templates/<src>` → `<dest>` rows of the init runbook's step 3."""
    block = _runbook_block(runbook, "The installer copies these templates",
                           "The init marker is written by")
    return re.findall(
        r"`\{metodoloji-root\}/templates/([^`]+)`\s*(?:→|->)\s*`([^`]+)`", block)


def _loop_tokens(text: str, pattern: str) -> list[str]:
    """The whitespace-separated list of a `for <var> in <list>; do` loop."""
    match = re.search(pattern, text)
    return match.group(1).split() if match else []


def _pair_diff(declared: dict[str, str], canonical: dict[str, str]) -> str:
    missing = sorted(set(canonical) - set(declared))
    extra = sorted(set(declared) - set(canonical))
    moved = [f"{src}: {declared[src]} != {canonical[src]}"
             for src in sorted(set(declared) & set(canonical))
             if declared[src] != canonical[src]]
    parts = []
    if missing:
        parts.append("undocumented " + ", ".join(missing))
    if extra:
        parts.append("documented-only " + ", ".join(extra))
    if moved:
        parts.append("wrong destination " + "; ".join(moved))
    return " | ".join(parts) if parts else "different rows"


def declaration_problems(plugin: pathlib.Path | None = None) -> list[str]:
    """Every shipped restatement of the skeleton contract that drifted (E-047).

    The declared sides are the init runbook (directories + template table), the
    session-start probe loop, and §6d of the plugin audit — compared against
    this module's DIRECTORIES / TEMPLATE_PAIRS / SKELETON_PROBES. §6d must also
    spend no second copy of the probe set AND must actually run this audit, so a
    fix that stops calling it is caught instead of going decorative.
    """
    root = pathlib.Path(plugin) if plugin is not None else plugin_root()
    problems: list[str] = []

    runbook = _read_site(root, "commands/init.md")
    if not runbook:
        problems.append("commands/init.md: unreadable — the declared directory "
                        "list and template table cannot be audited")
    else:
        declared_dirs = _declared_directories(runbook)
        if set(declared_dirs) != set(DIRECTORIES):
            problems.append(
                "commands/init.md: documented directories differ from "
                "skeleton.DIRECTORIES — documented-only %s, canonical-only %s"
                % (sorted(set(declared_dirs) - set(DIRECTORIES)) or "none",
                   sorted(set(DIRECTORIES) - set(declared_dirs)) or "none"))
        declared_pairs = _declared_template_pairs(runbook)
        if len(declared_pairs) != len(set(declared_pairs)):
            problems.append("commands/init.md: the template table repeats a row")
        elif dict(declared_pairs) != dict(TEMPLATE_PAIRS):
            problems.append(
                "commands/init.md: documented template table differs from "
                "skeleton.TEMPLATE_PAIRS — %s"
                % _pair_diff(dict(declared_pairs), dict(TEMPLATE_PAIRS)))

    bootstrap = _read_site(root, "hooks/scripts/bootstrap.sh")
    if not bootstrap:
        problems.append("hooks/scripts/bootstrap.sh: unreadable — the "
                        "session-start probe set cannot be audited")
    else:
        probes = _loop_tokens(bootstrap, r"for _f in ([^;]+); do")
        if set(probes) != set(SKELETON_PROBES):
            problems.append(
                "hooks/scripts/bootstrap.sh: SessionStart probes %s but "
                "skeleton.SKELETON_PROBES is %s — the hint and the installer "
                "would disagree about what an installed skeleton looks like"
                % (sorted(probes) or "nothing", list(SKELETON_PROBES)))

    audit = _read_site(root, "scripts/check-plugin.sh")
    if not audit:
        problems.append("scripts/check-plugin.sh: unreadable — §6d cannot be "
                        "audited")
    else:
        section = _runbook_block(audit, "== 6d) Init marker integrity",
                                 "== 6d2)")
        if "--audit-declarations" not in audit:
            problems.append(
                "scripts/check-plugin.sh: §6d no longer runs "
                "skeleton.py --audit-declarations — the declaration audit is "
                "decorative and these copies can drift again unnoticed")
        literal = [t for t in _loop_tokens(section, r"for \w+ in ([^;]+); do")
                   if "_template" in t]
        if literal:
            problems.append(
                "scripts/check-plugin.sh: §6d carries a second copy of the "
                "probe set (%s) — read it from skeleton.py --probe-paths "
                "instead of restating it" % ", ".join(literal))
    return problems


def read_marker(project_root: pathlib.Path) -> dict:
    """Parse the init marker into a dict (absent/unreadable → {})."""
    try:
        text = (project_root / MARKER_REL).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        # UnicodeDecodeError (E-009): a BINARY marker is unreadable, not
        # absent — status() re-checks with marker_unreadable() so the two
        # are never conflated.
        return {}
    marker: dict = {}
    for line in text.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            marker[key.strip()] = value.strip()
    return marker


def marker_unreadable(project_root: pathlib.Path) -> bool:
    """True when the marker EXISTS but is not valid UTF-8 (E-009).

    read_marker must not crash and must not silently call a binary marker
    "missing"; status() reports this as its own problem line so the operator
    (and orient, through status) sees "repair the marker", not "not installed".
    """
    try:
        (project_root / MARKER_REL).read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return True
    except OSError:
        return False
    return False


def status(project_root: pathlib.Path) -> dict:
    """Report the skeleton against the marker — both drift directions."""
    root = plugin_root()
    missing_dirs = [d for d in DIRECTORIES if not (project_root / d).is_dir()]
    missing_templates = [
        dest for src, dest in TEMPLATE_PAIRS
        if not (project_root / dest).is_file() and (root / "templates" / src).is_file()
    ]
    # A template we ship but cannot find under templates/ is a plugin problem,
    # not a project problem — surface it instead of silently skipping it.
    absent_sources = [src for src, _ in TEMPLATE_PAIRS
                      if not (root / "templates" / src).is_file()]
    marker = read_marker(project_root)
    unreadable = marker_unreadable(project_root)
    skeleton = any((project_root / p).is_file() for p in SKELETON_PROBES)
    problems = []
    if unreadable:
        problems.append(
            ".metodoloji/initialized is unreadable (not valid UTF-8) — "
            "re-run skeleton.py --install to repair it")
    if skeleton and not marker and not unreadable:
        problems.append("skeleton installed but .metodoloji/initialized is missing")
    if marker and not skeleton:
        problems.append("marker present but no skeleton copy found — stale marker")
    if marker and skeleton and (missing_dirs or missing_templates):
        missing = []
        if missing_dirs:
            missing.append("directories: " + ", ".join(missing_dirs))
        if missing_templates:
            missing.append("templates: " + ", ".join(missing_templates))
        problems.append(
            "record skeleton is incomplete — " + "; ".join(missing) +
            "; repair safely with skeleton.py --install (existing files are preserved)")
    if absent_sources:
        problems.append("plugin ships no templates/: " + ", ".join(absent_sources))
    return {
        "ok": not problems,
        "plugin_root": str(root),
        "plugin_version": plugin_version(root),
        "project_root": str(project_root),
        "installed": bool(skeleton and marker),
        "skeleton_present": skeleton,
        "marker_present": bool(marker),
        "marker": marker,
        "missing_directories": missing_dirs,
        "missing_templates": missing_templates,
        "missing_template_sources": absent_sources,
        "problems": problems,
    }


def _write_marker(project_root: pathlib.Path) -> dict:
    root = plugin_root()
    marker_path = project_root / MARKER_REL
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    marker_path.write_text(
        f"initialized_at: {stamp}\nplugin_version: {plugin_version(root)}\n",
        encoding="utf-8",
    )
    return read_marker(project_root)


def install(project_root: pathlib.Path, *, force: bool = False) -> dict:
    """Create the skeleton and write the marker. Idempotent; never clobbers."""
    root = plugin_root()
    created_dirs, copied, skipped = [], [], []
    for rel in DIRECTORIES:
        target = project_root / rel
        if target.is_dir():
            continue
        try:
            target.mkdir(parents=True, exist_ok=True)
            created_dirs.append(rel)
        except OSError as exc:
            print(f"skeleton: cannot create {rel}: {exc}", file=sys.stderr)
            return {"ok": False, "error": f"mkdir {rel}: {exc}"}
    for src_name, dest_rel in TEMPLATE_PAIRS:
        source = root / "templates" / src_name
        target = project_root / dest_rel
        if target.exists() and not force:
            skipped.append(dest_rel)
            continue
        if not source.is_file():
            print(f"skeleton: template missing in plugin: {src_name}", file=sys.stderr)
            return {"ok": False, "error": f"missing template {src_name}"}
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)  # byte-exact: no text-mode newline churn
            copied.append(dest_rel)
        except OSError as exc:
            print(f"skeleton: cannot copy {dest_rel}: {exc}", file=sys.stderr)
            return {"ok": False, "error": f"copy {dest_rel}: {exc}"}
    # The marker records the FIRST init, so a redundant install must not
    # churn initialized_at (only --force rewrites it).
    marker = read_marker(project_root)
    marker_written = False
    if force or not marker:
        marker = _write_marker(project_root)
        marker_written = True
    return {"ok": True, "root": str(project_root),
            "directories_created": created_dirs, "templates_copied": copied,
            "templates_skipped": skipped, "marker": marker,
            "marker_written": marker_written,
            "next_step": "/metodoloji:audit (health check)"}


def _print_status(state: dict) -> None:
    print(f"record skeleton: {'installed' if state['installed'] else 'NOT installed'}")
    print(f"  project_root: {state['project_root']}")
    print(f"  plugin_root:  {state['plugin_root']} (v{state['plugin_version']})")
    print(f"  marker: {'present' if state['marker_present'] else 'absent'}"
          + (f" (initialized_at: {state['marker'].get('initialized_at', '?')})"
             if state["marker_present"] else ""))
    if state["missing_directories"]:
        print(f"  missing directories ({len(state['missing_directories'])}): "
              + ", ".join(state["missing_directories"]))
    if state["missing_templates"]:
        print(f"  missing templates ({len(state['missing_templates'])}): "
              + ", ".join(state["missing_templates"]))
    for problem in state["problems"]:
        print(f"  [ERROR] {problem}")
    if not state["installed"]:
        print("  → install once: python3 "
              f"{state['plugin_root']}/bmad/scripts/skeleton.py --install")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Record-skeleton status/install (the executable half of "
                    "/metodoloji:init; safe to re-run).",
    )
    parser.add_argument("--project-root", "-p", default=None,
                        help="Target project root (default: CLAUDE_PROJECT_DIR / "
                             "OPENHANDS_PROJECT_DIR / cwd)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--status", action="store_true",
                       help="Report skeleton + marker state (default when no action)")
    group.add_argument("--install", action="store_true",
                       help="Create directories, copy missing templates, write the marker")
    group.add_argument("--probe-paths", action="store_true",
                       help="Print the installed-skeleton probe paths (one per line): "
                            "the set a caller must NOT restate (E-047)")
    group.add_argument("--audit-declarations", action="store_true",
                       help="Fail when a shipped restatement of this contract "
                            "(init runbook, session-start probe, check-plugin §6d) "
                            "has drifted from this module (E-047)")
    parser.add_argument("--force", action="store_true",
                        help="With --install: replace existing template copies and the marker")
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    args = parser.parse_args()

    if args.probe_paths:
        print("\n".join(SKELETON_PROBES))
        return 0
    if args.audit_declarations:
        drift = declaration_problems()
        for problem in drift:
            print(f"[ERROR] {problem}")
        if not drift:
            print("declaration sites agree with the contract in this module: "
                  f"{len(DIRECTORIES)} directories, {len(TEMPLATE_PAIRS)} template "
                  f"pairs, {len(SKELETON_PROBES)} probes — "
                  + ", ".join(DECLARATION_SITES))
        return 1 if drift else 0

    project_root = resolve_project_root(args.project_root)
    if args.install:
        result = install(project_root, force=args.force)
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            if result["ok"]:
                print(f"record skeleton installed at {result['root']}")
                print(f"  directories created: {len(result['directories_created'])}")
                print(f"  templates copied:    {len(result['templates_copied'])}"
                      f" (skipped existing: {len(result['templates_skipped'])})")
                print(f"  marker: {MARKER_REL} "
                      f"(initialized_at {result['marker'].get('initialized_at', '?')}, "
                      f"plugin_version {result['marker'].get('plugin_version', '?')})")
                print(f"  next: {result['next_step']}")
            else:
                print(f"skeleton install failed: {result.get('error')}", file=sys.stderr)
        return 0 if result["ok"] else 1

    state = status(project_root)
    if args.json:
        print(json.dumps(state, indent=2, ensure_ascii=False))
    else:
        _print_status(state)
    # Exit 1 only on drift or an incomplete install — a project that never
    # initialized is a valid state (init is explicitly opt-in), so it exits 0
    # with "NOT installed" and the exact command to fix it.
    return 1 if state["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())

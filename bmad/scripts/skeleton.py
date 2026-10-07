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
import shutil
import sys

# The skeleton contract, in one place: commands/init.md documents it, this
# script executes it, check-plugin.sh §6d verifies its outcome. Keep the three
# in step — §6d probes the same two template paths as SKELETON_PROBES.
DIRECTORIES = (
    "docs/experiments",
    "docs/development/stories",
    "docs/quality",
    "docs/research",
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
    ("_template_C.md", "docs/design/_template.md"),
    ("_template_IR.md", "docs/development/_template_IR.md"),
    ("_template_SP.md", "docs/development/_template_SP.md"),
    ("_template_QR.md", "docs/development/_template_QR.md"),
    ("_template_PR.md", "docs/development/_template_PR.md"),
    ("_template_S.md", "docs/development/stories/_template_S.md"),
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


def read_marker(project_root: pathlib.Path) -> dict:
    """Parse the init marker into a dict (absent → {})."""
    try:
        text = (project_root / MARKER_REL).read_text(encoding="utf-8")
    except OSError:
        return {}
    marker: dict = {}
    for line in text.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            marker[key.strip()] = value.strip()
    return marker


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
    skeleton = any((project_root / p).is_file() for p in SKELETON_PROBES)
    problems = []
    if skeleton and not marker:
        problems.append("skeleton installed but .metodoloji/initialized is missing")
    if marker and not skeleton:
        problems.append("marker present but no skeleton copy found — stale marker")
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
    parser.add_argument("--force", action="store_true",
                        help="With --install: replace existing template copies and the marker")
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    args = parser.parse_args()

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

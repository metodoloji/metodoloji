#!/usr/bin/env python3
"""A decided record's plan is history, not an operation a session resumes (E-189).

Ground truth measured live on this tree, 2026-11-21:

    Plan progress (GRP): E-005.md 1/5 planned files on disk (4 pending);
    E-008.md 2/5 planned files on disk (3 pending); E-009.md 2/11 planned files
    on disk (9 pending) — extend an operation's plan with …

Three experiments that had been decided (`Status: completed`) were announced at
every SessionStart as live work, and their planned paths (`bmad/...`) had since
moved to `hooks/...`, so the counts could never fall to zero. Enumerating the
whole record tree: 122 records had pending planned files, and every one of them
was decided (120 completed, 2 REJECTED) — zero open operations. The open list is
name-sorted and capped at three, so those decided records occupied every slot a
genuinely open plan would need.

Claim, closed set: the plan surfaces (SessionStart clause, shared open list) name
only records whose own Status/Decision says the operation is still open; the
derivation itself (planned files on disk), the shipped Stop note for a touched
record (silent for a decided record, measured through stop._plan_progress_note
after E-190 retired the formatter this probe used to drive), and a statusless
record's visibility are as this record states.

Control: `--broken` runs the same probes against the pre-change tree
(`PREFIX_REV`), which surfaces decided records as open plans with no verdict.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import tarfile
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
PREFIX_REV = "69d1309"  # the parent of the commit that ships the decided-record rule

PROBES = (
    "decided_plan_silent",
    "open_plan_surfaced",
    "rejected_decision_silent",
    "cap_reserved_for_open_work",
    "progress_derivation_unchanged",
    "one_formatter_two_surfaces",
    "closed_vocabulary",
    "open_vocabulary",
    "stop_note_silent_for_decided",
    "no_plan_no_line",
)

_CLOSED = ("completed", "approved", "superseded", "withdrawn", "refused")
_OPEN = ("planned", "draft", "in-progress")


# ---------------------------------------------------------------- fixtures
def _imports(tree: pathlib.Path) -> None:
    sys.path.insert(0, str(tree / "hooks" / "engine"))
    sys.path.insert(0, str(tree))


def _record(root: pathlib.Path, eid: str = "E-100", status: str | None = "planned",
            decision: str | None = None, with_plan: bool = True) -> pathlib.Path:
    recs = root / "docs" / "experiments"
    recs.mkdir(parents=True, exist_ok=True)
    status_line = f"- **Status:** {status}\n" if status is not None else ""
    decision_line = f"- **Decision:** {decision}\n" if decision else ""
    plan_block = ("\n## Implementation Plan (GRP)\n\n"
                  "- **Planned Files:** src/a.py, src/b.py\n"
                  "- **Planned Tests:** —\n"
                  "- **Known Gaps:** —\n"
                  "- **Amendments:** none\n") if with_plan else ""
    path = recs / f"{eid}.md"
    path.write_text(
        f"## Experiment: {eid} — plan fixture\n" + status_line
        + "- **Code Scope:** src/**\n" + decision_line + plan_block,
        encoding="utf-8")
    return path


def _project(base: pathlib.Path) -> pathlib.Path:
    root = base / "project"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    return root


def _context(root: pathlib.Path) -> str:
    from modules.audit import session_start
    return session_start({"cwd": str(root)})["additionalContext"]


def _clause(ctx: str) -> str:
    marker = "Plan progress (GRP): "
    if marker not in ctx:
        return ""
    return ctx.split(marker, 1)[1].split(" — extend", 1)[0]


# ---------------------------------------------------------------- probes
def _probe(name: str, tree: pathlib.Path, base: pathlib.Path) -> tuple[bool, str]:
    os.environ.pop("BMAD_GATE_KEY", None)
    _imports(tree)
    from modules import plan

    if name == "decided_plan_silent":
        root = _project(base)
        _record(root, status="completed")
        ctx = _context(root)
        ok = "Plan progress (GRP)" not in ctx
        return ok, f"clause={_clause(ctx)!r}"

    if name == "open_plan_surfaced":
        root = _project(base)
        _record(root, status="in-progress")
        clause = _clause(_context(root))
        ok = clause.startswith("E-100.md 1/2 planned files on disk (1 pending)")
        return ok, f"clause={clause!r}"

    if name == "rejected_decision_silent":
        root = _project(base)
        _record(root, status="in-progress", decision="REJECTED — H-100: measured=0.2 <= 0.9")
        ctx = _context(root)
        ok = "Plan progress (GRP)" not in ctx
        return ok, f"clause={_clause(ctx)!r}"

    if name == "cap_reserved_for_open_work":
        root = _project(base)
        for eid in ("E-005", "E-008", "E-009"):
            _record(root, eid=eid, status="completed")
        _record(root, eid="E-500", status="in-progress")
        clause = _clause(_context(root))
        ok = "E-500.md" in clause and "E-005.md" not in clause
        return ok, f"clause={clause!r}"

    if name == "progress_derivation_unchanged":
        root = _project(base)
        rec = _record(root, status="completed")
        prog = plan.plan_progress(str(rec), str(root))
        ok = prog == {"done": 1, "pending": 1, "planned": 2}
        return ok, f"progress={prog}"

    if name == "one_formatter_two_surfaces":
        root = _project(base)
        _record(root, status="planned")
        _record(root, eid="E-900", status="completed")
        clause = _clause(_context(root))
        shared = plan.open_plans_text(str(root / "docs" / "experiments"), str(root))
        ok = clause == shared and "E-900.md" not in shared
        return ok, f"clause={clause!r} shared={shared!r}"

    if name == "closed_vocabulary":
        results = {}
        for status in _CLOSED:
            root = _project(base / status)
            _record(root, status=status)
            results[status] = plan.open_plans(str(root / "docs" / "experiments"), str(root))
        bad = {s: len(r) for s, r in results.items() if r}
        return not bad, f"surfaced={bad or 'none'}"

    if name == "open_vocabulary":
        results = {}
        for status in _OPEN + (None,):
            key = status or "no-status"
            root = _project(base / key)
            _record(root, status=status)
            results[key] = [r["name"] for r in
                            plan.open_plans(str(root / "docs" / "experiments"), str(root))]
        bad = {s: r for s, r in results.items() if r != ["E-100.md"]}
        return not bad, f"missing={bad or 'none'}"

    if name == "stop_note_silent_for_decided":
        # E-190: the shipped Stop note is the surface here — the old probe drove
        # plan.progress_line, which no production path ever called, so it could
        # not report on the stop report at all. The note is silent for a decided
        # record (E-189's rule) and the code-writes note is not.
        from modules import stop
        root = _project(base)
        _record(root, status="completed")
        note = stop._plan_progress_note(str(root), ["src/a.py"])
        ok = note == ""
        return ok, f"note={note!r}"

    if name == "no_plan_no_line":
        root = _project(base)
        _record(root, status="in-progress", with_plan=False)
        ctx = _context(root)
        rows = plan.open_plans(str(root / "docs" / "experiments"), str(root))
        ok = "Plan progress (GRP)" not in ctx and rows == []
        return ok, f"rows={len(rows)}"

    raise SystemExit(f"unknown probe: {name}")


# ---------------------------------------------------------------- driver mode
def driver(name: str, tree: pathlib.Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ok, detail = _probe(name, tree, pathlib.Path(tmp))
    print(json.dumps({"ok": ok, "detail": detail}, ensure_ascii=False))


# ---------------------------------------------------------------- bench mode
def _prefix_tree(base: pathlib.Path) -> pathlib.Path:
    archive = base / "prefix.tar"
    with archive.open("wb") as handle:
        subprocess.run(["git", "archive", PREFIX_REV], cwd=ROOT,
                       stdout=handle, stdin=subprocess.DEVNULL, check=True)
    tree = base / "prefix"
    tree.mkdir()
    with tarfile.open(archive) as tar:
        tar.extractall(tree)
    return tree


def _run(tree: pathlib.Path, samples: pathlib.Path | None, broken: bool) -> bool:
    rows, passed = [], 0
    for name in PROBES:
        result = subprocess.run(
            [sys.executable, str(pathlib.Path(__file__).resolve()), "--driver", name,
             "--tree", str(tree)], capture_output=True, text=True, cwd=str(tree),
            stdin=subprocess.DEVNULL)
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            payload = {"ok": False, "detail": (result.stderr or result.stdout)[-200:]}
        ok = bool(payload.get("ok"))
        passed += 1 if ok else 0
        rows.append({"probe": name, "ok": ok, "detail": payload.get("detail", "")})
        print(f"  {'PASS' if ok else 'FAIL'}  {name}  — {payload.get('detail', '')}")
    total = len(PROBES)
    value = passed / total
    print(f"plan_after_decision_accuracy={value:.2f} ({passed}/{total})")
    if samples is not None:
        samples.parent.mkdir(parents=True, exist_ok=True)
        with samples.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps({"broken": broken, **row}, ensure_ascii=False) + "\n")
    return passed == total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=pathlib.Path, default=None)
    parser.add_argument("--broken", action="store_true")
    parser.add_argument("--driver", default=None)
    parser.add_argument("--tree", type=pathlib.Path, default=None)
    args = parser.parse_args()

    if args.driver:
        driver(args.driver, args.tree or ROOT)
        return 0

    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        tree = _prefix_tree(base) if args.broken else ROOT
        print(f"tree: {tree}")
        ok = _run(tree, args.samples, args.broken)
    # The control is EXPECTED to miss the claim; a non-zero exit would read as a
    # crashed measurement instead of a measured refutation (rule 7).
    return 0 if (ok or args.broken) else 1


if __name__ == "__main__":
    raise SystemExit(main())

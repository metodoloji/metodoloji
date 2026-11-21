#!/usr/bin/env python3
"""Every plan surface is one shipped path, and each is measured through itself (E-190).

Found on this tree, 2026-11-21, while re-reading the record that had just been
approved:

* `modules/plan.py` exported `progress_line`, whose docstring opened with
  ``# Stop-report hook surface`` — but nothing shipped ever called it. The
  shipped Stop note (`stop._plan_progress_note`) formats through
  `open_plan_line`, the same function the SessionStart clause and the digest use.
* E-189's bench probe `stop_report_keeps_counts` drove exactly that dead
  function and reported PASS, so the record's Census sentence "one asserts the
  Stop report still counts a decided record's plan" was false about the shipped
  surface: with E-189 applied, the real Stop note is silent for a decided
  record (verified: ``''``) while the dead formatter still printed
  ``plan progress: 1/2 planned files on disk (1 pending)``.

That is the reachability rule (E-154) turned on a measurement: a probe that
reaches no shipped path cannot report on one, and a formatter with no caller is
not a surface. The class is the same one E-188/E-189 closed for claims — a
surface repeating something that is no longer true — applied to the bench that
certified the claim.

Claim, closed set: all three plan surfaces (SessionStart clause, shared open
list/digest, Stop note) name OPEN records only, format the identical fragment
for the same record, and no plan formatter exists without a shipped caller.

Control: `--broken` runs the same probes against the pre-E-188/189/190 tree
(`PREFIX_REV`), which surfaces decided records on every surface and still ships
the uncalled formatter.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import tarfile
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
PREFIX_REV = "69d1309"  # the tree before the decided-plan rule and this cleanup

PROBES = (
    "stop_note_silent_for_decided",
    "stop_note_names_open_record",
    "stop_note_uses_the_shared_fragment",
    "session_clause_matches_open_list",
    "stop_note_skips_a_decided_cover",
    "no_formatter_without_a_caller",
    "open_record_derivation_unchanged",
    "stop_note_capped",
    "decided_record_silent_on_every_surface",
    "stop_note_empty_without_writes",
)


# ---------------------------------------------------------------- fixtures
def _imports(tree: pathlib.Path) -> None:
    sys.path.insert(0, str(tree / "hooks" / "engine"))
    sys.path.insert(0, str(tree))


def _record(root: pathlib.Path, eid: str = "E-100", status: str = "planned",
            planned: str = "src/a.py, src/b.py") -> pathlib.Path:
    recs = root / "docs" / "experiments"
    recs.mkdir(parents=True, exist_ok=True)
    path = recs / f"{eid}.md"
    path.write_text(
        f"## Experiment: {eid} — plan fixture\n- **Status:** {status}\n"
        "- **Code Scope:** src/**\n\n## Implementation Plan (GRP)\n\n"
        f"- **Planned Files:** {planned}\n- **Amendments:** none\n",
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
    _imports(tree)
    from modules import plan, stop

    if name == "stop_note_silent_for_decided":
        root = _project(base)
        _record(root, status="completed")
        note = stop._plan_progress_note(str(root), ["src/a.py"])
        return note == "", f"note={note!r}"

    if name == "stop_note_names_open_record":
        root = _project(base)
        _record(root, status="in-progress")
        note = stop._plan_progress_note(str(root), ["src/a.py"])
        ok = "E-100.md 1/2 planned files on disk (1 pending)" in note
        return ok, f"note={note!r}"

    if name == "stop_note_uses_the_shared_fragment":
        root = _project(base)
        rec = _record(root, status="planned")
        note = stop._plan_progress_note(str(root), ["src/a.py"])
        shared = plan.open_plan_line(str(rec), str(root)) or ""
        ok = bool(shared) and shared in note
        return ok, f"note={note[:90]!r} shared={shared!r}"

    if name == "session_clause_matches_open_list":
        root = _project(base)
        _record(root, status="planned")
        _record(root, eid="E-900", status="completed")
        clause = _clause(_context(root))
        shared = plan.open_plans_text(str(root / "docs" / "experiments"), str(root))
        ok = clause == shared and "E-900.md" not in shared
        return ok, f"clause={clause!r} shared={shared!r}"

    if name == "stop_note_skips_a_decided_cover":
        root = _project(base)
        _record(root, eid="E-010", status="completed", planned="src/a.py")
        _record(root, eid="E-500", status="planned", planned="src/a.py, src/b.py")
        note = stop._plan_progress_note(str(root), ["src/a.py"])
        ok = "E-500.md" in note and "E-010.md" not in note
        return ok, f"note={note[:120]!r}"

    if name == "no_formatter_without_a_caller":
        dead = [n for n in ("progress_line",) if hasattr(plan, n)]
        return not dead, f"dead={dead or 'none'}"

    if name == "open_record_derivation_unchanged":
        root = _project(base)
        rec = _record(root, status="planned")
        prog = plan.plan_progress(str(rec), str(root))
        return prog == {"done": 1, "pending": 1, "planned": 2}, f"progress={prog}"

    if name == "stop_note_capped":
        root = _project(base)
        writes = []
        for n, eid in enumerate(("E-101", "E-102", "E-103", "E-104"), start=1):
            rel = f"src/f{n}.py"
            (root / rel).write_text("x = 1\n", encoding="utf-8")
            _record(root, eid=eid, status="planned", planned=f"{rel}, src/missing.py")
            writes.append(rel)
        note = stop._plan_progress_note(str(root), writes)
        names = note.count(".md")
        ok = names == 3 and "\n" not in note
        return ok, f"names={names} len={len(note)}"

    if name == "decided_record_silent_on_every_surface":
        root = _project(base)
        _record(root, status="completed")
        note = stop._plan_progress_note(str(root), ["src/a.py"])
        ctx = _context(root)
        rows = plan.open_plans(str(root / "docs" / "experiments"), str(root))
        ok = note == "" and "Plan progress (GRP)" not in ctx and rows == []
        return ok, f"note={note!r} rows={len(rows)} clause={'Plan progress' in ctx}"

    if name == "stop_note_empty_without_writes":
        root = _project(base)
        _record(root, status="planned")
        note = stop._plan_progress_note(str(root), [])
        return note == "", f"note={note!r}"

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
    print(f"plan_surface_agreement_accuracy={value:.2f} ({passed}/{total})")
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

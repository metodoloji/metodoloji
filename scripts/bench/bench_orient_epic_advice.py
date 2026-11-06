#!/usr/bin/env python3
"""Bench: does the digest ever claim an epic is finished when the plan says otherwise?

E-114 (H-114). The sprint-status file lists only the stories that already have a
draft — its own SCOPE NOTE says so — while epic status is computed over the full
plan. The digest counted membership alone and therefore advised
"every story done — roll the epic up to done" for an epic whose plan still named
unwritten slices. This bench builds fixture projects and asks the shipped digest
what it says; every case is a statement a reader could act on wrongly.

Usage:
    python scripts/bench/bench_orient_epic_advice.py                 # measure
    python scripts/bench/bench_orient_epic_advice.py --broken        # control
    python scripts/bench/bench_orient_epic_advice.py --samples FILE  # + samples
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import tempfile

_REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "hooks" / "engine"))
sys.path.insert(0, str(_REPO / "bmad" / "scripts"))

from modules import state  # noqa: E402
import orient  # noqa: E402


def _project(tmp: pathlib.Path, sprint: str, plan: str | None) -> pathlib.Path:
    native = tmp / "docs" / "development" / "native"
    native.mkdir(parents=True, exist_ok=True)
    (native / "sprint-status.yaml").write_text(sprint, encoding="utf-8")
    if plan is not None:
        planning = tmp / "docs" / "planning"
        planning.mkdir(parents=True, exist_ok=True)
        (planning / "epics.md").write_text(plan, encoding="utf-8")
    return tmp


def _steps(root: pathlib.Path) -> str:
    return " \n".join(orient.build(root)["next_steps"])


def _legacy_epic_steps(root: pathlib.Path) -> str:
    """The pre-fix advice, replicated exactly: membership only, plan unread."""
    s = state.sprint_summary(root)
    out = []
    for epic_key, prog in sorted((s.get("epic_progress") or {}).items()):
        status = (s.get("epics") or {}).get(epic_key, "")
        if prog["total"] and status == "in-progress" and prog["done"] == prog["total"]:
            out.append(f"{epic_key}: every story done — roll the epic up to done "
                       f"in sprint-status and run bmad-retrospective")
        elif prog["total"] and status == "done" and prog["done"] < prog["total"]:
            out.append(f"{epic_key}: marked done but "
                       f"{prog['total'] - prog['done']} story/stories are not")
    return " \n".join(out)


def _summary(root: pathlib.Path, legacy: bool) -> dict:
    s = state.sprint_summary(root)
    if legacy:
        # The pre-fix view: the plan was never read, so every caller believed
        # the sprint file's membership was the whole epic.
        s["epic_plan"] = {}
    return s


SPRINT_ALL_DONE = ("development_status:\n"
                   "  epic-1: in-progress\n"
                   "  1-1-a: done\n"
                   "  1-2-b: done\n")

PLAN_THREE = ("## EP-1 — Denetim izi\n\n"
              "### S-101 — a\n\n### S-102 — b\n\n### S-103 — c\n")

PLAN_TWO = "## EP-1 — x\n\n### S-101 — a\n### S-102 — b\n"


def _case_plan_exceeds_board(tmp, legacy):
    """Two listed stories done, three planned slices: a roll-up would be a lie."""
    root = _project(tmp, SPRINT_ALL_DONE, PLAN_THREE)
    if legacy:
        return "every story done" not in _legacy_epic_steps(root)
    steps = _steps(root)
    return ("every story done" not in steps
            and "plans 3 slices" in steps
            and "plan→record mapping" in steps
            and "epics.md" in steps)


def _case_plan_present_asks_mapping(tmp, legacy):
    root = _project(tmp, SPRINT_ALL_DONE, PLAN_TWO)
    if legacy:
        return "plans 2 slices" in _legacy_epic_steps(root)
    steps = _steps(root)
    return "plans 2 slices" in steps and "before rolling the epic up" in steps


def _case_no_plan_is_named(tmp, legacy):
    root = _project(tmp, SPRINT_ALL_DONE, None)
    if legacy:
        return "no epic plan on disk" in _legacy_epic_steps(root)
    steps = _steps(root)
    return "no epic plan on disk" in steps and "roll the epic up to done" in steps


def _case_unrecognised_plan_is_unknown(tmp, legacy):
    root = _project(tmp, SPRINT_ALL_DONE, "# Plan\n\nNo epic headings here.\n")
    if legacy:
        return "no epic plan on disk" in _legacy_epic_steps(root)
    steps = _steps(root)
    return "no epic plan on disk" in steps


def _case_marked_done_with_gaps_still_flagged(tmp, legacy):
    root = _project(tmp,
                    "development_status:\n"
                    "  epic-2: done\n"
                    "  2-1-a: done\n"
                    "  2-2-b: in-progress\n",
                    "## EP-2 — x\n\n### S-201 — a\n### S-202 — b\n")
    steps = _steps(root)
    return "marked done but 1" in steps


def _case_plan_count_respects_boundary(tmp, legacy):
    root = _project(tmp, SPRINT_ALL_DONE,
                    PLAN_THREE + "\n## EP-5 — x\n\n### S-501 — c\n")
    if legacy:
        # The pre-fix corpus had no plan reader at all, so this could not pass.
        return False
    plan = state.epic_plan_slices(root)
    return plan["epic-1"]["slices"] == 3 and plan["epic-5"]["slices"] == 1


def _case_absent_plan_is_empty_not_zero(tmp, legacy):
    if legacy:
        return False
    return state.epic_plan_slices(tmp) == {}


def _case_sprint_line_asks_mapping(tmp, legacy):
    root = _project(tmp, SPRINT_ALL_DONE, PLAN_TWO)
    line = state.format_sprint_line(_summary(root, legacy))
    if legacy:
        return "stories all done" in line and "plan has" not in line
    return ("plan has 2 slices" in line and "verify mapping" in line
            and "stories all done" not in line)


def _case_sprint_line_plain_without_plan(tmp, legacy):
    root = _project(tmp, SPRINT_ALL_DONE, None)
    line = state.format_sprint_line(_summary(root, legacy))
    if legacy:
        return "plan has" not in line
    return "stories all done" in line and "roll up" in line


CASES = (
    _case_plan_exceeds_board,
    _case_plan_present_asks_mapping,
    _case_no_plan_is_named,
    _case_unrecognised_plan_is_unknown,
    _case_marked_done_with_gaps_still_flagged,
    _case_plan_count_respects_boundary,
    _case_absent_plan_is_empty_not_zero,
    _case_sprint_line_asks_mapping,
    _case_sprint_line_plain_without_plan,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--broken", action="store_true",
                    help="measure the pre-fix behaviour (the control)")
    ap.add_argument("--samples", help="write one JSON object per case here")
    args = ap.parse_args()

    results = []
    for case in CASES:
        with tempfile.TemporaryDirectory() as d:
            results.append(bool(case(pathlib.Path(d), args.broken)))
    passed = sum(results)
    total = len(results)

    if args.samples:
        out = pathlib.Path(args.samples)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as fh:
            for case, ok in zip(CASES, results):
                fh.write(json.dumps({"case": case.__name__, "ok": ok}) + "\n")

    label = "broken" if args.broken else "shipped"
    print(f"digest_epic_advice_accuracy={passed / total:.4f} ({passed}/{total})")
    print(f"mode={label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

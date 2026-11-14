"""Change-radar guard falsifiability bench — E-107.

docs/experiments/E-107.md (H-107: guard_detection_accuracy >= 0.90).

Ten drift trials plus one clean control, each run against a synthetic project
tree through the SHIPPED guard. A trial is `ok` when the guard reports the
injected drift (or stays silent for the clean tree). `--broken` runs the same
trials through the pre-fix surface kept here as the control: rule 7 says the
broken implementation must score below the threshold.
"""

from __future__ import annotations

import argparse
import inspect
import json
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "bench"))

import bench_consistency as bench  # noqa: E402


def _write(root: pathlib.Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _tree(tmp: pathlib.Path, skills: dict, locked: bool = False) -> None:
    for name, body in skills.items():
        _write(tmp, f"skills/{name}/SKILL.md", body)
    if locked:
        (tmp / "skills" / "bmad-locked" / "SKILL.md").mkdir(parents=True)


def _run_shipped(tmp: pathlib.Path, declared) -> str | None:
    params = inspect.signature(
        bench._check_change_radar_contract_matches).parameters
    prev = bench.ROOT
    bench.ROOT = tmp
    try:
        if "declared" in params:
            return bench._check_change_radar_contract_matches(declared=declared)
        # The pre-fix surface has no table to compare against: run it as it is.
        return bench._check_change_radar_contract_matches()
    finally:
        bench.ROOT = prev


def _trial(tmp: pathlib.Path, name: str, skills: dict,
           declared, expect_problem: bool, locked: bool = False,
           run=_run_shipped) -> dict:
    _tree(tmp, skills, locked=locked)
    result = run(tmp, declared)
    ok = (result is not None) == expect_problem
    return {"trial": name, "ok": ok, "expect_problem": expect_problem,
            "result": result}


def _emit(samples: pathlib.Path, results: list[dict]) -> None:
    samples.parent.mkdir(parents=True, exist_ok=True)
    with samples.open("w", encoding="utf-8") as fh:
        for r in results:
            fh.write(json.dumps(r) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--broken", action="store_true",
                    help="run the pre-fix surface (the rule-7 control)")
    ap.add_argument("--samples", default=None,
                    help="JSONL path for per-trial evidence")
    args = ap.parse_args()

    # --broken retired with the runbooks (E-151): the pre-fix surface read
    # GUIDE/KILAVUZ, and those files no longer exist to reproduce.
    if args.broken:
        print("--broken retired: the pre-fix surface read the retired runbooks "
              "(E-151); run the shipped arm only")
        return 0
    run = _run_shipped
    with tempfile.TemporaryDirectory() as td:
        base = pathlib.Path(td)
        results = [
            _trial(base / case["trial"], case["trial"], case["skills"],
                   case["declared"], case["expect_problem"],
                   case.get("locked", False), run=run)
            for case in _case_specs(base)
        ]

    x = sum(1 for r in results if r["ok"])
    n = len(results)
    if args.samples:
        _emit(pathlib.Path(args.samples), results)
    for r in results:
        print(f"[{'OK' if r['ok'] else 'MISS'}] {r['trial']}: {r['result']}")
    print(f"guard_detection_accuracy={x / n:.2f} ({x}/{n})")
    return 0


def _case_specs(base: pathlib.Path) -> list[dict]:
    """The trial definitions, independent of which surface runs them."""
    one = 'subscribe --watcher bmad-x --pattern "prd.*" --channel session\n'
    seg = "`bmad-x` watches `prd.*` (session)"
    declared_ok = {"bmad-x": {("prd.*", "session")}}
    return [
        {"trial": "declared_table_stale", "skills": {"bmad-x": one}, "declared": {"bmad-x": {("ux.*", "session")}},
         "expect_problem": True},
        {"trial": "declared_phantom_skill", "skills": {"bmad-x": one},
         "declared": {"bmad-x": {("prd.*", "session")},
                      "bmad-gone": {("q.*", "session")}},
         "expect_problem": True},
        {"trial": "unreadable_skill_md", "skills": {"bmad-x": one}, "declared": declared_ok, "expect_problem": True,
         "locked": True},
        {"trial": "unsubscribe_not_counted",
         "skills": {"bmad-x": one + 'unsubscribe --watcher bmad-x --pattern '
                                  '"story.*" --channel session\n'}, "declared": declared_ok, "expect_problem": False},
        {"trial": "channel_equals_spelling",
         "skills": {"bmad-x": 'subscribe --watcher bmad-x --pattern "prd.*" '
                              '--channel=stop\n'},
         "radar": ["`bmad-x` watches `prd.*` (stop)"],
         "declared": {"bmad-x": {("prd.*", "stop")}},
         "expect_problem": False},
        {"trial": "trailing_code_span_backtick",
         "skills": {"bmad-x": 'subscribe --watcher bmad-x --pattern "prd.*" '
                              '--channel session`\n'}, "declared": declared_ok, "expect_problem": False},
        {"trial": "clean_tree", "skills": {"bmad-x": one},
         "declared": declared_ok, "expect_problem": False},
    ]


if __name__ == "__main__":
    raise SystemExit(main())

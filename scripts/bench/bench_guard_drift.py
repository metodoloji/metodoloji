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

# The pre-fix surface, kept verbatim as the control.
_OLD_SUB_RE = re.compile(
    r'subscribe --watcher (\S+) --pattern "([^"]+)" --channel (\S+)')
_OLD_RADAR_RE = re.compile(
    r"`([a-z][\w-]+)`\s*(?:watches|→)\s*`([^`]+)`\s*\((session|stop)\)")


def _write(root: pathlib.Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _tree(tmp: pathlib.Path, skills: dict, radar: list, locked: bool = False) -> None:
    for name, body in skills.items():
        _write(tmp, f"skills/{name}/SKILL.md", body)
    _write(tmp, "GUIDE.md", "- change radar: " + ", ".join(radar) + "\n")
    _write(tmp, "KILAVUZ.md", "- değişim radarı: " + ", ".join(radar) + "\n")
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


def _run_legacy(tmp: pathlib.Path, declared) -> str | None:
    """The 043e7ba surface: old regex, no table cross-check, silent OSError."""
    shipped: dict[str, set] = {}
    for md in sorted((tmp / "skills").glob("*/SKILL.md")):
        try:
            txt = md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for watcher, pat, chan in _OLD_SUB_RE.findall(txt):
            shipped.setdefault(watcher, set()).add((pat, chan))
    if not shipped:
        return "no skill ships a subscribe line"
    problems: list[str] = []
    for rel in ("GUIDE.md", "KILAVUZ.md"):
        lines = (tmp / rel).read_text(encoding="utf-8").splitlines()
        bullet = next((ln for ln in lines
                       if "change radar" in ln or "değişim radarı" in ln), None)
        if bullet is None:
            problems.append(f"{rel}: no change-radar bullet found")
            continue
        claimed: dict[str, set] = {}
        for skill, pats, chan in _OLD_RADAR_RE.findall(bullet):
            claimed.setdefault(skill, set()).update(
                (p.strip(), chan) for p in pats.split(",") if p.strip())
        for skill, want in sorted(shipped.items()):
            got = claimed.get(skill)
            if got is None:
                problems.append(f"{rel}: omits `{skill}`")
            elif got != want:
                problems.append(f"{rel}: `{skill}` differs")
        for skill in sorted(set(claimed) - set(shipped)):
            problems.append(f"{rel}: claims `{skill}` which ships nothing")
    return "; ".join(problems) if problems else None


def _trial(tmp: pathlib.Path, name: str, skills: dict, radar: list,
           declared, expect_problem: bool, locked: bool = False,
           run=_run_shipped) -> dict:
    _tree(tmp, skills, radar, locked=locked)
    result = run(tmp, declared)
    ok = (result is not None) == expect_problem
    return {"trial": name, "ok": ok, "expect_problem": expect_problem,
            "result": result}


def _cases(tmp: pathlib.Path) -> list[dict]:
    one = 'subscribe --watcher bmad-x --pattern "prd.*" --channel session\n'
    seg = "`bmad-x` watches `prd.*` (session)"
    declared_ok = {"bmad-x": {("prd.*", "session")}}
    out: list[dict] = []

    # 1-4: pre-existing drifts that must stay caught.
    out.append(_trial(tmp / "t1", "doc_wrong_pattern",
                      {"bmad-x": one}, ["`bmad-x` watches `ux.*` (session)"],
                      declared_ok, True))
    out.append(_trial(tmp / "t2", "doc_omits_skill",
                      {"bmad-x": one}, [], declared_ok, True))
    out.append(_trial(tmp / "t3", "doc_phantom_skill",
                      {"bmad-x": one}, [seg, "`bmad-y` watches `prd.*` (session)"],
                      declared_ok, True))
    out.append(_trial(
        tmp / "t4", "skill_undocumented_pair",
        {"bmad-x": one + 'subscribe --watcher bmad-x --pattern "ux.*" '
                         '--channel session\n'},
        [seg], {"bmad-x": {("prd.*", "session"),
                           ("ux.*", "session")}}, True))

    # 5-6: declared-table drifts the pre-fix surface cannot see.
    out.append(_trial(tmp / "t5", "declared_table_stale",
                      {"bmad-x": one}, [seg],
                      {"bmad-x": {("ux.*", "session")}}, True))
    out.append(_trial(tmp / "t6", "declared_phantom_skill",
                      {"bmad-x": one}, [seg],
                      {"bmad-x": {("prd.*", "session")},
                       "bmad-gone": {("q.*", "session")}}, True))

    # 7: a measurement failure must be reported, not skipped.
    out.append(_trial(tmp / "t7", "unreadable_skill_md",
                      {"bmad-x": one}, [seg], declared_ok, True, locked=True))

    # 8-10: CLI spellings.
    out.append(_trial(
        tmp / "t8", "unsubscribe_not_counted",
        {"bmad-x": one + 'unsubscribe --watcher bmad-x --pattern "story.*" '
                         '--channel session\n'},
        [seg], declared_ok, False))
    out.append(_trial(
        tmp / "t9", "channel_equals_spelling",
        {"bmad-x": 'subscribe --watcher bmad-x --pattern "prd.*" '
                   '--channel=stop\n'},
        ["`bmad-x` watches `prd.*` (stop)"],
        {"bmad-x": {("prd.*", "stop")}}, False))
    out.append(_trial(
        tmp / "t10", "trailing_code_span_backtick",
        {"bmad-x": 'subscribe --watcher bmad-x --pattern "prd.*" '
                   '--channel session`\n'},
        [seg], declared_ok, False))

    # 11: the clean tree stays silent.
    out.append(_trial(tmp / "t11", "clean_tree", {"bmad-x": one}, [seg],
                      declared_ok, False))
    return out


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

    run = _run_legacy if args.broken else _run_shipped
    with tempfile.TemporaryDirectory() as td:
        base = pathlib.Path(td)
        results = [
            _trial(base / case["trial"], case["trial"], case["skills"],
                   case["radar"], case["declared"], case["expect_problem"],
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
        {"trial": "doc_wrong_pattern", "skills": {"bmad-x": one},
         "radar": ["`bmad-x` watches `ux.*` (session)"],
         "declared": declared_ok, "expect_problem": True},
        {"trial": "doc_omits_skill", "skills": {"bmad-x": one},
         "radar": [], "declared": declared_ok, "expect_problem": True},
        {"trial": "doc_phantom_skill", "skills": {"bmad-x": one},
         "radar": [seg, "`bmad-y` watches `prd.*` (session)"],
         "declared": declared_ok, "expect_problem": True},
        {"trial": "skill_undocumented_pair",
         "skills": {"bmad-x": one + 'subscribe --watcher bmad-x --pattern '
                                  '"ux.*" --channel session\n'},
         "radar": [seg],
         "declared": {"bmad-x": {("prd.*", "session"),
                                 ("ux.*", "session")}},
         "expect_problem": True},
        {"trial": "declared_table_stale", "skills": {"bmad-x": one},
         "radar": [seg], "declared": {"bmad-x": {("ux.*", "session")}},
         "expect_problem": True},
        {"trial": "declared_phantom_skill", "skills": {"bmad-x": one},
         "radar": [seg],
         "declared": {"bmad-x": {("prd.*", "session")},
                      "bmad-gone": {("q.*", "session")}},
         "expect_problem": True},
        {"trial": "unreadable_skill_md", "skills": {"bmad-x": one},
         "radar": [seg], "declared": declared_ok, "expect_problem": True,
         "locked": True},
        {"trial": "unsubscribe_not_counted",
         "skills": {"bmad-x": one + 'unsubscribe --watcher bmad-x --pattern '
                                  '"story.*" --channel session\n'},
         "radar": [seg], "declared": declared_ok, "expect_problem": False},
        {"trial": "channel_equals_spelling",
         "skills": {"bmad-x": 'subscribe --watcher bmad-x --pattern "prd.*" '
                              '--channel=stop\n'},
         "radar": ["`bmad-x` watches `prd.*` (stop)"],
         "declared": {"bmad-x": {("prd.*", "stop")}},
         "expect_problem": False},
        {"trial": "trailing_code_span_backtick",
         "skills": {"bmad-x": 'subscribe --watcher bmad-x --pattern "prd.*" '
                              '--channel session`\n'},
         "radar": [seg], "declared": declared_ok, "expect_problem": False},
        {"trial": "clean_tree", "skills": {"bmad-x": one}, "radar": [seg],
         "declared": declared_ok, "expect_problem": False},
    ]


if __name__ == "__main__":
    raise SystemExit(main())

"""Measure the training harness for the bmad-research-experiment skill.

    python scripts/bench/bench_skill_training.py            # healthy
    python scripts/bench/bench_skill_training.py --broken   # rule-7 control

The subject is the *judge*, not the model: `evals/skill_runner.py` decides
whether an answer obeys the skill's rules from each case's `checks`, and that
verdict is what the optimization kernel scores. So every case is judged twice —
once on a compliant answer (`good`) that must pass, once on the answer the skill
exists to prevent (`bad`) that must fail. A judge that accepts the bad answer
would let the kernel accept a candidate that taught nothing, and the run would
report progress while the document got worse.

Deterministic, offline, stdlib only: no endpoint is contacted. The control
(`--broken`) replaces the judge with one that accepts everything, which is the
failure mode being guarded against.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "bmad-research-experiment"
CASES = SKILL / "evals" / "training-cases.jsonl"
SAMPLES = ROOT / "docs/experiments/E-101/raw/samples.jsonl"


def load_runner():
    """Import the judge from the skill's eval scripts (hyphenated dir, no package)."""
    path = SKILL / "evals" / "skill_runner.py"
    spec = importlib.util.spec_from_file_location("skill_runner", path)
    if spec is None or spec.loader is None:  # pragma: no cover - structural
        raise SystemExit(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["skill_runner"] = module
    spec.loader.exec_module(module)
    return module


def judge_factory(module, broken: bool):
    if broken:
        return lambda text, checks: (True, [])  # accepts anything, incl. a lie
    return module.grade_answer


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--broken", action="store_true",
                        help="replace the judge with one that accepts everything")
    parser.add_argument("--samples", default="docs/experiments/E-101/raw/samples.jsonl",
                        help="where to write per-verdict evidence; each record owns its own file")
    args = parser.parse_args()
    samples = ROOT / args.samples

    module = load_runner()
    judge = judge_factory(module, args.broken)
    rows = [json.loads(line) for line in CASES.read_text(encoding="utf-8").splitlines()
            if line.strip()]

    samples.parent.mkdir(parents=True, exist_ok=True)
    total = passed = 0
    with samples.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            for label, field, want in (("good", "good", True), ("bad", "bad", False)):
                text = str(row.get(field, ""))
                ok, notes = judge(text, row.get("checks", []))
                hit = (ok == want)
                total += 1
                passed += int(hit)
                fh.write(json.dumps({
                    "case": row["id"], "answer": label, "expected": want,
                    "judged": ok, "ok": hit, "notes": notes[:2],
                }, sort_keys=True) + "\n")

    covered = sum(1 for row in rows if row.get("checks"))
    print(f"training_accuracy={passed / total:.2f} ({passed}/{total}) "
          f"cases={len(rows)} covered={covered}/{len(rows)} "
          f"sample_file={samples.relative_to(ROOT).as_posix()}")
    if args.broken:
        # The control is *expected* to score low; the gate reads the number and
        # decides. Exiting non-zero here would look like a harness failure and
        # the gate would refuse the record before comparing anything.
        return 0
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())

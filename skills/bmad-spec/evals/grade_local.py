#!/usr/bin/env python3
"""Deterministic grader for research-experiment eval runs (stdlib only).

Reads a run dir's transcripts (case dirs hold transcript.jsonl written by
run_evals.py) and grades each case against the `checks` in cases.json:
  {"must_any": [...], "must_not_any": [...]} per check — all checks must pass.
Matching is case-insensitive substring on the transcript text. Writes
grading.json per case dir (same shape as the LLM grader: expectations +
summary + rubric_feedback) so reporting stays uniform.

Usage: grade_local.py --cases CASES.json --run-dir RUN_DIR
Exit 0 iff every graded case passes.
"""
import argparse
import json
import sys
from pathlib import Path


def norm(t: str) -> str:
    return " ".join(t.lower().split())


def grade_case(case: dict, text: str) -> dict:
    t = norm(text)
    expectations = []
    for i, chk in enumerate(case.get("checks", [])):
        must_any = [norm(x) for x in chk.get("must_any", [])]
        must_not = [norm(x) for x in chk.get("must_not_any", [])]
        ok_any = not must_any or any(x in t for x in must_any)
        ok_not = not any(x in t for x in must_not)
        passed = ok_any and ok_not
        bits = []
        if must_any:
            bits.append("any of " + str(chk["must_any"]))
        if must_not:
            bits.append("none of " + str(chk["must_not_any"]))
        ev = ("matched" if passed else "MISSING") + ": " + "; ".join(bits)
        snippet = text.strip().replace("\n", " ")[:200]
        expectations.append({
            "text": case.get("rubric", [""])[i]
            if i < len(case.get("rubric", [])) else f"check {i}",
            "passed": passed,
            "evidence": ev + (f" :: {snippet}" if snippet else ""),
        })
    passed_n = sum(1 for e in expectations if e["passed"])
    return {
        "case_id": case.get("id"),
        "expectations": expectations,
        "summary": {"passed": passed_n, "failed": len(expectations) - passed_n,
                    "total": len(expectations),
                    "pass_rate": passed_n / max(1, len(expectations))},
        "rubric_feedback": {"weak": [], "uncovered": [],
                            "overall": "deterministic substring checks"},
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--cases", required=True, type=Path)
    p.add_argument("--run-dir", required=True, type=Path)
    args = p.parse_args()
    data = json.loads(args.cases.read_text(encoding="utf-8"))
    cases = data["cases"] if isinstance(data, dict) else data
    all_ok = True
    for case in cases:
        cid = str(case.get("id"))
        tpath = args.run_dir / "skill" / cid / "transcript.jsonl"
        text = ""
        if tpath.is_file():
            for line in tpath.read_text(encoding="utf-8",
                                        errors="replace").splitlines():
                try:
                    evt = json.loads(line)
                except ValueError:
                    text += " " + line
                    continue
                msg = evt.get("message", {}) if isinstance(evt, dict) else {}
                for item in msg.get("content", []) or []:
                    if isinstance(item, dict) and item.get("type") == "text":
                        text += " " + str(item.get("text", ""))
        report = grade_case(case, text)
        (args.run_dir / "skill" / cid / "grading.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8")
        ok = report["summary"]["failed"] == 0
        all_ok &= ok
        print(f"[{'PASS' if ok else 'FAIL'}] {cid}: "
              f"{report['summary']['passed']}/{report['summary']['total']}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

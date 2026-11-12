#!/usr/bin/env python3
"""E-130 bench — a comparison claim's control arm must be judged by McNemar.

The defect this bench pins: Rule 7 evaluated the control with `evaluate()`,
which calls `parse_claim()` and reads the right-hand side of the claim as a
number. A comparison claim ('a_rate > b_rate') has a metric NAME on the right,
and its decision comes from paired verdicts plus a McNemar test. So every
comparison record that declared a control died with
"threshold in '...' is not a number", and Rule 7 — the gate's own
anti-self-deception check — silently never ran on exactly the records that
adjudicate a design choice.

Five closed cases, each driving the REAL gate CLI (subprocess), not a
reimplementation:

  1. comparison record, control compares equal      -> APPROVED, Falsifiability verified
  2. comparison record, control also wins clearly   -> REFUSED, no Decision, exit 3
  3. comparison record, control declared, no file   -> exit 2, names --control-samples
  4. threshold record, control fails the threshold  -> APPROVED (old path intact)
  5. threshold record, control also clears it       -> REFUSED (old path intact)

`--broken` grades the PRE-FIX gate, read out of git (`git show HEAD:…`) rather
than from a live path: after the repair the shipped gate passes every case, so
a control pointed at the tree would measure the fix twice. The pre-fix copy is
asserted to actually lack the fix, and the bench refuses to score a revision
that already carries it.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(os.path.dirname(HERE))
GATE_REL = "skills/bmad-research-experiment/scripts/run_experiment.py"
GATE = os.path.join(PLUGIN_ROOT, GATE_REL)

PROBE = '''#!/usr/bin/env python3
"""Deterministic probe for E-130: emits metric lines and a paired file."""
import argparse, json

ap = argparse.ArgumentParser()
ap.add_argument("--mode", default="comparison")
ap.add_argument("--a-ok", type=int, default=20)
ap.add_argument("--b-ok", type=int, default=8)
ap.add_argument("--n", type=int, default=20)
ap.add_argument("--samples", default="")
ap.add_argument("--broken", action="store_true")
a = ap.parse_args()

n = a.n
a_ok = a.a_ok if not a.broken else a.b_ok
b_ok = a.b_ok
if a.mode == "threshold":
    print(f"probe_rate={a_ok / n:.4f} ({a_ok}/{n})")
else:
    print(f"alpha_rate={a_ok / n:.4f} ({a_ok}/{n})")
    print(f"beta_rate={b_ok / n:.4f} ({b_ok}/{n})")
if a.samples:
    with open(a.samples, "w", encoding="utf-8") as fh:
        for i in range(n):
            fh.write(json.dumps({"case": i, "a": i < a_ok, "b": i < b_ok}) + "\\n")
'''

REC_COMPARISON = """## Experiment: E-9xx — comparison control probe

- **Date:** 24.10.2026
- **Status:** planned
- **Theory:** a comparison claim has no threshold; its control must be judged
  on its own paired verdicts.
- **Hypothesis:** H-9xx: "alpha_rate > beta_rate"
- **Measurement Metrics:** alpha_rate > beta_rate over paired cases
- **Experiment Design:** one probe, intact and broken.
- **Code Scope:** none
- **Lineage:** none
- **Raw Samples:** {samples}
"""

REC_THRESHOLD = """## Experiment: E-9yy — threshold control probe

- **Date:** 24.10.2026
- **Status:** planned
- **Theory:** the threshold path must keep working after the comparison fix.
- **Hypothesis:** H-9yy: "probe_rate >= 0.90"
- **Measurement Metrics:** probe_rate >= 0.90
- **Experiment Design:** one probe, intact and broken.
- **Code Scope:** none
- **Lineage:** none
- **Raw Samples:** none — the probe has no per-sample stream; this case
  exercises the threshold control path, not the sample cross-check
"""


def has_fix(path: str) -> bool:
    """True when this gate build knows about a comparison control's paired file."""
    try:
        src = open(path, encoding="utf-8").read()
    except OSError:
        return False
    return "--control-samples" in src and "evaluate_control_comparison" in src


def prefix_gate_from_git(rev: str, dest: str) -> str:
    """Write the gate as it was at `rev` and return the path."""
    proc = subprocess.run(["git", "show", f"{rev}:{GATE_REL}"], cwd=PLUGIN_ROOT,
                          capture_output=True, text=True, encoding="utf-8",
                          stdin=subprocess.DEVNULL)
    if proc.returncode != 0:
        raise SystemExit(f"REFUSING: cannot read {GATE_REL} at {rev}: "
                         f"{proc.stderr.strip()[:200]}")
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write(proc.stdout)
    return dest


def run_case(idx: int, label: str, project: str, record_text: str,
             gate: str, run_args: list, control_args: list, extra: list,
             expect: dict) -> dict:
    exp = os.path.join(project, "docs", "experiments")
    os.makedirs(exp, exist_ok=True)
    rec = os.path.join(exp, "E-9%02d.md" % idx)
    samples = os.path.join(project, "samples.jsonl")
    with open(rec, "w", encoding="utf-8") as fh:
        fh.write(record_text.replace("{samples}", samples))
    os.makedirs(os.path.join(project, "scripts", "bench"), exist_ok=True)
    with open(os.path.join(project, "scripts", "bench", "probe.py"), "w",
              encoding="utf-8") as fh:
        fh.write(PROBE)

    probe = [sys.executable, "scripts/bench/probe.py"]
    # {samples} names the file the PROBE writes, so it must be interpolated in
    # the commands as well as in the record — a literal placeholder makes the
    # probe write a file called "{samples}" and the gate reports the real path
    # as unreadable, which reads as a bench bug rather than a plumbing one.
    run_cmd = " ".join(probe + run_args).replace("{samples}", samples)
    cmd = [sys.executable, gate, "--record", rec,
           "--run", run_cmd,
           "--no-blackboard"] + extra
    if control_args is not None:
        cmd += ["--control", " ".join(probe + control_args).replace("{samples}", samples)]

    env = dict(os.environ)
    env.pop("CLAUDE_PROJECT_DIR", None)
    env.pop("OPENHANDS_PROJECT_DIR", None)
    proc = subprocess.run(cmd, cwd=project, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env,
                          timeout=180, stdin=subprocess.DEVNULL)
    text = open(rec, encoding="utf-8").read() if os.path.exists(rec) else ""
    blob = (proc.stdout or "") + (proc.stderr or "")

    ok = True
    reasons = []
    if proc.returncode != expect["code"]:
        ok = False
        reasons.append(f"exit {proc.returncode} != {expect['code']}")
    if expect.get("decision"):
        if f"**Decision:** {expect['decision']}" not in text:
            ok = False
            reasons.append(f"missing Decision {expect['decision']}")
    if expect.get("no_decision"):
        if "**Decision:**" in text:
            ok = False
            reasons.append("Decision written for an unfalsifiable bench")
    if expect.get("falsifiability"):
        if f"**Falsifiability:** {expect['falsifiability']}" not in text:
            ok = False
            reasons.append(f"missing Falsifiability {expect['falsifiability']}")
    if expect.get("output_has"):
        if expect["output_has"] not in blob:
            ok = False
            reasons.append(f"output lacks {expect['output_has']!r}")

    return {"case": idx, "label": label, "ok": ok, "reasons": reasons,
            "exit": proc.returncode, "tail": blob[-300:]}


def grade(gate: str) -> list:
    results = []
    tmp = tempfile.mkdtemp(prefix="e130-")
    try:
        p1 = os.path.join(tmp, "c1"); os.makedirs(p1)
        results.append(run_case(
            1, "controlled comparison, no separation -> APPROVED",
            p1, REC_COMPARISON, gate,
            ["--mode", "comparison", "--a-ok", "20", "--b-ok", "8", "--n", "20",
             "--samples", "{samples}"],
            ["--mode", "comparison", "--a-ok", "8", "--b-ok", "8", "--n", "20",
             "--samples", os.path.join(tmp, "c1-control.jsonl")],
            ["--control-samples", os.path.join(tmp, "c1-control.jsonl")],
            {"code": 0, "decision": "APPROVED", "falsifiability": "verified — control"}))

        p2 = os.path.join(tmp, "c2"); os.makedirs(p2)
        results.append(run_case(
            2, "comparison control also wins -> REFUSED", p2, REC_COMPARISON, gate,
            ["--mode", "comparison", "--a-ok", "20", "--b-ok", "8", "--n", "20",
             "--samples", "{samples}"],
            ["--mode", "comparison", "--a-ok", "20", "--b-ok", "8", "--n", "20",
             "--samples", os.path.join(tmp, "c2-control.jsonl")],
            ["--control-samples", os.path.join(tmp, "c2-control.jsonl")],
            {"code": 3, "no_decision": True, "falsifiability": "REFUSED"}))

        p3 = os.path.join(tmp, "c3"); os.makedirs(p3)
        results.append(run_case(
            3, "comparison control without its paired file -> named error", p3,
            REC_COMPARISON, gate,
            ["--mode", "comparison", "--a-ok", "20", "--b-ok", "8", "--n", "20",
             "--samples", "{samples}"],
            ["--mode", "comparison", "--a-ok", "8", "--b-ok", "8", "--n", "20"],
            [],
            {"code": 2, "output_has": "--control-samples"}))

        p4 = os.path.join(tmp, "c4"); os.makedirs(p4)
        results.append(run_case(
            4, "threshold control still fails the threshold -> APPROVED", p4,
            REC_THRESHOLD, gate,
            ["--mode", "threshold", "--a-ok", "20", "--b-ok", "1", "--n", "20"],
            ["--mode", "threshold", "--a-ok", "1", "--b-ok", "1", "--n", "20"],
            [],
            {"code": 0, "decision": "APPROVED", "falsifiability": "verified — control"}))

        p5 = os.path.join(tmp, "c5"); os.makedirs(p5)
        results.append(run_case(
            5, "threshold control also clears it -> REFUSED", p5, REC_THRESHOLD, gate,
            ["--mode", "threshold", "--a-ok", "20", "--b-ok", "1", "--n", "20"],
            ["--mode", "threshold", "--a-ok", "20", "--b-ok", "1", "--n", "20"],
            [],
            {"code": 3, "no_decision": True, "falsifiability": "REFUSED"}))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", default="")
    ap.add_argument("--broken", action="store_true")
    ap.add_argument("--control-rev", default="HEAD",
                    help="git revision holding the pre-fix gate (control arm)")
    args = ap.parse_args()

    if args.broken:
        if not has_fix(GATE):
            print("REFUSING: the shipped gate carries no comparison-control path; "
                  "the control arm has nothing to regress against.", file=sys.stderr)
            return 2
        tmp = tempfile.mkdtemp(prefix="e130-prefix-")
        try:
            prefix = prefix_gate_from_git(args.control_rev,
                                          os.path.join(tmp, "run_experiment.py"))
            if has_fix(prefix):
                print(f"REFUSING: control NOT REPRODUCIBLE — {args.control_rev} already "
                      f"carries the fix, so the control arm would measure it twice. "
                      f"Pass --control-rev <sha> from before the repair.", file=sys.stderr)
                return 2
            results = grade(prefix)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    else:
        if not has_fix(GATE):
            print("REFUSING: the gate still reads a comparison control as a scalar; "
                  "there is nothing to grade.", file=sys.stderr)
            return 2
        results = grade(GATE)

    n = len(results)
    ok = sum(1 for r in results if r["ok"])
    for r in results:
        print(f"  case {r['case']}: {'PASS' if r['ok'] else 'FAIL'} — {r['label']}"
              + ("" if r["ok"] else " | " + "; ".join(r["reasons"])))
        if not r["ok"] and os.environ.get("E130_VERBOSE"):
            print("      tail: " + r["tail"].replace("\n", "\n            "))
    print(f"comparison_control_contract_accuracy={ok / n:.4f} ({ok}/{n})")

    if args.samples:
        parent = os.path.dirname(os.path.abspath(args.samples))
        os.makedirs(parent, exist_ok=True)
        with open(args.samples, "w", encoding="utf-8") as fh:
            for r in results:
                fh.write(json.dumps({"case": r["case"], "ok": r["ok"]},
                                    ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

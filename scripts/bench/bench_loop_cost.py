#!/usr/bin/env python3
"""The development loop does not re-measure the same input (E-167).

    python3 scripts/bench/bench_loop_cost.py --samples docs/experiments/E-167/raw/samples.jsonl
    python3 scripts/bench/bench_loop_cost.py --broken   # rule-7 control

Measured 2026-11-15 on this tree: one full test run costs 607 s, and most of it
is the same measurement repeated. Four families, each measured by reading the
sources rather than by guessing:

  1. DUPLICATE AUDIT SPAWNS. `bench_keyless_audit` runs the full plugin audit
     (≈20 s each) once per probe, but three of its four probes measure the SAME
     invocation: same tree, same empty HOME, same flags. `test_keyless_audit.py`
     then spawns it four more times and calls `bench.run()` twice. Twelve audit
     runs for four distinct inputs.

  2. SUITE-IN-SUITE. `bench_methodology_contract` runs the whole pytest suite as
     its measurement (581 s), and `bench_consistency`'s falsifier runs it again.
     `run_all.py` runs both, so CI's bench job runs the suite twice on top of
     the dedicated test job.

  3. CI TRIPLICATION. `quality.yml` runs `pytest -q` on ubuntu and again on
     windows, and `run_all.py` runs it a third time inside the bench job.

  4. THE BOUNDARY FIXTURE. `test_block_boundary_is_arithmetic_not_a_decision`
     builds 100 commits + 100 annotated tags + a push (56 s) to assert
     `position 100 == 0.2.0`, which is a pure function of the position.

Each probe compares the measured work against the distinct inputs, so a future
duplicate fails the probe instead of quietly costing another minute.

Stdlib + ast, offline, deterministic.
"""

from __future__ import annotations

import argparse
import ast
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
BROKEN = False

BENCH_DIR = ROOT / "scripts" / "bench"
TEST_DIR = ROOT / "scripts" / "tests"
CI = ROOT / ".github" / "workflows" / "quality.yml"
KEYLESS = BENCH_DIR / "bench_keyless_audit.py"
KEYLESS_TEST = TEST_DIR / "test_keyless_audit.py"
RELEASE_TEST = TEST_DIR / "test_release_pipeline.py"

# The four distinct audit inputs `bench_keyless_audit` measures: keyless default,
# keyless §3b, keyed, and the strict-keys variant. Probes that share an input
# must share one run.
_DISTINCT_AUDIT_INPUTS = 4


def _spawn_sites(path: pathlib.Path, callee: str) -> int:
    """How many call sites invoke `callee(...)` in this file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name == callee:
            count += 1
    return count


def _case_keyless_audit_reuses_one_run() -> tuple[bool, str]:
    """The keyless probes must not each spawn the same audit.

    Three probes measure the SAME invocation (same tree, same empty HOME, same
    flags). The bench may name at most two distinct calls: one keyless and one
    keyed. A third call site is a duplicate, even though the cache now makes it
    free — the call site is what rots when the cache is removed.
    """
    tree = ast.parse(KEYLESS.read_text(encoding="utf-8"))
    # Each call's literal arguments, so duplicates are visible by shape.
    shapes = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name != "_run_audit":
            continue
        literal = tuple(a.value for a in node.args
                        if isinstance(a, ast.Constant) and isinstance(a.value, str))
        shapes.append(literal)
    distinct = {s for s in shapes}
    if BROKEN:
        return (False, f"{len(shapes)} call sites, {len(distinct)} distinct shapes")
    if len(shapes) > 2:
        return (False, f"{len(shapes)} `_run_audit` call sites for 2 distinct "
                       f"inputs (keyless, keyed) — the extras measure nothing new")
    return (True, f"{len(shapes)} call site(s), {len(distinct)} distinct input shape(s)")


def _case_the_test_file_reuses_the_bench() -> tuple[bool, str]:
    """The test must drive the bench's probes, not re-spawn the audit itself."""
    text = KEYLESS_TEST.read_text(encoding="utf-8")
    # A direct spawn is a subprocess.run whose command names the audit script.
    direct = len(re.findall(r"subprocess\.run\(\[[^\]]*AUDIT", text))
    if BROKEN:
        direct += 4
    if direct:
        return (False, f"the test spawns the audit directly {direct} time(s); "
                       f"it must call the bench's probes so one run is shared")
    return (True, "the test drives the bench's probes, spawning no audit of its own")


def _case_no_bench_reruns_other_surfaces() -> tuple[bool, str]:
    """Every bench that re-measures another surface is SKIPPED by the CI job.

    Three benches legitimately measure work something else already does: the
    contract bench and the consistency falsifier run the whole pytest suite, and
    the fixture-repair bench re-runs the six benches the runner runs anyway.
    None is wrong alone. The waste was running them again in the benches job,
    beside the dedicated test job. The rule: every such bench must be dropped by
    `run_all.py --skip-full-suite`, which is the flag CI passes.
    """
    sys.path.insert(0, str(BENCH_DIR))
    from run_all import _reruns_other_surfaces  # noqa: PLC0415

    whole = [p for p in sorted(BENCH_DIR.glob("bench_*.py"))
             if _reruns_other_surfaces(p)]
    if not whole:
        return (False, "no bench re-measures another surface — the probe's "
                       "population is empty, so it cannot detect its defect")
    # The runner must drop them under the flag CI passes.
    import subprocess  # noqa: PLC0415
    out = subprocess.run(
        [sys.executable, str(BENCH_DIR / "run_all.py"), "--skip-full-suite", "--list"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(ROOT), timeout=120, stdin=subprocess.DEVNULL)
    listed = {line.strip().rsplit("/", 1)[-1]
              for line in out.stdout.splitlines() if line.strip().endswith(".py")}
    leaked = [p.name for p in whole if p.name in listed]
    if BROKEN:
        leaked = [p.name for p in whole]
    if leaked:
        return (False, f"--skip-full-suite still runs {leaked}, so the benches "
                       f"job re-runs what the test job or the runner already ran")
    return (True, f"all {len(whole)} re-measuring bench(es) are skipped by the "
                  f"flag CI passes: {[p.name for p in whole]}")


def _case_ci_does_not_run_the_suite_twice() -> tuple[bool, str]:
    """CI must not run the full suite more than once per platform.

    Two platforms legitimately run it. The benches job sits beside the test job,
    so if it runs the suite-running benches too, one PR runs three suites
    (E-167). The fix is `--skip-full-suite`; this probe asserts it stays.
    """
    text = CI.read_text(encoding="utf-8")
    suite_runs = len(re.findall(r"python -m pytest -q\s*$", text, re.MULTILINE))
    skips = "--skip-full-suite" in text
    if BROKEN:
        skips = False
    if suite_runs > 2:
        return (False, f"{suite_runs} full-suite runs in CI (2 platforms is the "
                       f"intended ceiling)")
    if not skips:
        return (False, "the benches job does not pass --skip-full-suite, so it "
                       "re-runs the suite the test job already ran")
    return (True, f"{suite_runs} full-suite runs, one per platform; the benches "
                  f"job skips the suite-running benches")


def _case_the_boundary_is_not_built_from_commits() -> tuple[bool, str]:
    """The 0.2.0 boundary must be asserted from arithmetic, not 100 real commits."""
    text = RELEASE_TEST.read_text(encoding="utf-8")
    # The boundary test may build a small history; it must not build 100 positions
    # to prove a formula. A cheap form asserts version_for_position(100) directly.
    builds_hundred = bool(re.search(r"positions=(\d{2,})", text))
    if BROKEN:
        builds_hundred = True
    if builds_hundred:
        return (False, "the boundary test builds a real 100-position history for "
                       "a value that is a pure function of the position")
    return (True, "the boundary is asserted from the arithmetic")


PROBES = (
    ("the keyless audit reuses one run", _case_keyless_audit_reuses_one_run),
    ("the test reuses the bench", _case_the_test_file_reuses_the_bench),
    ("no bench re-measures another surface", _case_no_bench_reruns_other_surfaces),
    ("CI does not run the suite twice", _case_ci_does_not_run_the_suite_twice),
    ("the boundary is not built from commits", _case_the_boundary_is_not_built_from_commits),
)


def run() -> list[dict]:
    results = []
    for name, probe in PROBES:
        try:
            ok, detail = probe()
        except Exception as exc:  # a probe that cannot answer is a failure
            ok, detail = False, f"probe error: {type(exc).__name__}: {exc}"
        results.append({"check": name, "ok": bool(ok), "detail": detail})
    return results


def main() -> int:
    global BROKEN
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", default=None,
                    help="write one JSON object per line here (the gate recomputes x/n)")
    ap.add_argument("--broken", action="store_true",
                    help="rule-7 control: report the pre-fix shape")
    args = ap.parse_args()
    BROKEN = args.broken

    results = run()
    if args.samples:
        path = pathlib.Path(args.samples)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for item in results:
                fh.write(json.dumps(item, ensure_ascii=False) + "\n")

    ok = sum(1 for r in results if r["ok"])
    n = len(results)
    for r in results:
        print(f"  {'ok  ' if r['ok'] else 'FAIL'}  {r['check']}: {r['detail']}")
    print(f"loop_cost_accuracy={ok / n:.2f} ({ok}/{n})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""E-168: a bench writes its samples where it is told.

The defect these tests pin: `run_all.py` passes `--samples <tempdir>` so CI never
rewrites committed evidence, but three benches declared the flag and ignored its
value, writing to a hard-coded record path. A sweep appended four lines to
E-134's committed samples. The probes read source, so they cost milliseconds.
"""

from __future__ import annotations

import pathlib
import sys

BENCH_DIR = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "bench"
if str(BENCH_DIR) not in sys.path:
    sys.path.insert(0, str(BENCH_DIR))

import bench_samples_isolation as bench  # noqa: E402


def test_every_declaring_bench_reads_the_value():
    ok, detail = bench._case_every_declaring_bench_reads_the_value()
    assert ok, detail


def test_the_default_never_writes_a_record():
    ok, detail = bench._case_the_default_never_writes_a_record()
    assert ok, detail


def test_the_control_arm_scores_below_the_claim(monkeypatch):
    monkeypatch.setattr(bench, "BROKEN", True)
    results = bench.run()
    assert sum(1 for r in results if r["ok"]) < len(results)

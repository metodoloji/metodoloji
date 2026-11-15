"""E-167: the development loop must not re-measure the same input.

The defect these tests pin: one full run cost 607 s, and most of it was the same
measurement repeated — the keyless audit spawned twelve times for two distinct
inputs, the whole pytest suite run twice inside the benches job, and a 100-commit
history built to assert an arithmetic boundary. The probes read source, so they
cost milliseconds; the expensive proof lives in the bench's `--control` arm.
"""

from __future__ import annotations

import pathlib
import sys

BENCH_DIR = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "bench"
if str(BENCH_DIR) not in sys.path:
    sys.path.insert(0, str(BENCH_DIR))

import bench_loop_cost as bench  # noqa: E402


def test_the_keyless_audit_reuses_one_run():
    ok, detail = bench._case_keyless_audit_reuses_one_run()
    assert ok, detail


def test_the_test_file_reuses_the_bench():
    ok, detail = bench._case_the_test_file_reuses_the_bench()
    assert ok, detail


def test_no_bench_reruns_other_surfaces():
    ok, detail = bench._case_no_bench_reruns_other_surfaces()
    assert ok, detail


def test_ci_does_not_run_the_suite_twice():
    ok, detail = bench._case_ci_does_not_run_the_suite_twice()
    assert ok, detail


def test_the_boundary_is_not_built_from_commits():
    ok, detail = bench._case_the_boundary_is_not_built_from_commits()
    assert ok, detail

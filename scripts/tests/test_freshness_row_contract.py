"""E-165: the freshness row carries every key its consumers index.

The defect these tests pin: `guard._record_freshness` delegates to
`freshness.annotate`, and `scope_gate.scope_advisory` indexes `fresh['commit']`
out of that row. `annotate` did not return `commit`, so a scope-matched APPROVED
record that read STALE raised `KeyError: 'commit'` on the write path. The probes
derive both sides from source, so this test also fails if a future consumer
starts indexing a key the producer does not return.
"""

from __future__ import annotations

import pathlib
import sys

BENCH_DIR = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "bench"
if str(BENCH_DIR) not in sys.path:
    sys.path.insert(0, str(BENCH_DIR))

import bench_freshness_row_contract as bench  # noqa: E402


def test_every_consumed_key_is_produced():
    ok, detail = bench._case_every_consumed_key_is_produced()
    assert ok, detail


def test_the_live_annotate_carries_the_contract():
    ok, detail = bench._case_the_live_annotate_carries_the_contract()
    assert ok, detail


def test_the_control_arm_scores_below_the_claim(monkeypatch):
    monkeypatch.setattr(bench, "BROKEN", True)
    results = bench.run()
    assert sum(1 for r in results if r["ok"]) < len(results)

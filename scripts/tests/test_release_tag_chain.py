"""E-155: the release chain's probes, driven on synthetic state.

The chain itself is repository refs, not files, so a unit test cannot build a
history. These tests drive the probe decisions on closed state maps; the live
chain is measured by the bench, which the gate runs.
"""

from __future__ import annotations

import pathlib
import sys

BENCH_DIR = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "bench"
if str(BENCH_DIR) not in sys.path:
    sys.path.insert(0, str(BENCH_DIR))

import bench_release_tags as bench  # noqa: E402


def _state(shas, tags):
    return {"shas": shas, "positions": {sha: i for i, sha in enumerate(shas)},
            "tags": tags}


def test_a_gap_inside_the_published_run_is_caught():
    # v0.1.2 is published, so v0.1.1 may not be missing between 0 and 2.
    state = _state(["a", "b", "c"], {"v0.1.0": "a", "v0.1.2": "c"})
    ok, detail = bench._case_positions(state)
    assert not ok and "v0.1.1" in detail


def test_stopping_short_of_head_is_legal():
    # The owner rule allows the chain to lag HEAD; only gaps are defects.
    state = _state(["a", "b", "c"], {"v0.1.0": "a", "v0.1.1": "b"})
    ok, detail = bench._case_positions(state)
    assert ok and "0..1" in detail


def test_a_misplaced_tag_is_caught():
    state = _state(["a", "b", "c"], {"v0.1.0": "a", "v0.1.1": "c", "v0.1.2": "c"})
    ok, detail = bench._case_positions(state)
    assert not ok and "at #2 not #1" in detail


def test_a_tag_outside_the_history_is_caught():
    state = _state(["a", "b"], {"v0.1.0": "a", "v0.9.9": "elsewhere"})
    ok, detail = bench._case_containment(state)
    assert not ok and "v0.9.9" in detail


def test_an_impossible_number_is_caught():
    state = _state(["a", "b"], {"v0.1.0": "a", "v0.1.100": "b"})
    ok, detail = bench._case_producible(state)
    assert not ok and "v0.1.100" in detail


def test_the_newest_release_must_hold_its_number(monkeypatch):
    state = _state(["a", "b"], {"v0.1.0": "a", "v0.1.1": "b"})
    monkeypatch.setattr(bench, "_versions_at",
                        lambda sha: {"pyproject.toml": "0.1.0"})
    ok, detail = bench._case_newest_tree(state)
    assert not ok and "v0.1.1" in detail
    monkeypatch.setattr(bench, "_versions_at",
                        lambda sha: {"pyproject.toml": "0.1.1"})
    ok, _ = bench._case_newest_tree(state)
    assert ok


def test_the_live_chain_passes_every_probe():
    results = bench.run(bench._state())
    assert all(result["ok"] for result in results), [
        result for result in results if not result["ok"]]

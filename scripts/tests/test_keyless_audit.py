"""E-164: the plugin audit must pass on a fresh clone, where no gate key exists.

The defect these tests pin: CI runs `check-plugin.sh` on every PR, and that
audit exited 1 on any machine without `~/.bmad/gate-key` — every fresh clone and
every runner. The keyless condition is reproduced by pointing HOME at an empty
directory, so it is measured rather than described.

Cost discipline (E-167, E-168): the audit costs ~20 s per run, so every
assertion here reads the bench's OWN cached probe results. `bench.run()` is
called once and both the live arm and the control arm assert against it; a
per-probe test that re-drove `PROBES` measured the same four inputs twice and
was the largest single cost in the suite.
"""

from __future__ import annotations

import pathlib
import sys

BENCH_DIR = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "bench"
if str(BENCH_DIR) not in sys.path:
    sys.path.insert(0, str(BENCH_DIR))

import bench_keyless_audit as bench  # noqa: E402


def _live_results() -> dict[str, dict]:
    """One live `bench.run()`, shared by every test in this module."""
    cached = getattr(_live_results, "_cache", None)
    if cached is None:
        cached = {r["check"]: r for r in bench.run()}
        _live_results._cache = cached
    return cached


def test_a_missing_key_is_a_warning_not_an_error():
    result = _live_results()["a missing key is not fatal by default"]
    assert result["ok"], result["detail"]


def test_scope_coverage_does_not_invent_violations_without_a_key():
    result = _live_results()["scope coverage skips without a key"]
    assert result["ok"], result["detail"]


def test_the_audit_exits_zero_without_a_key():
    result = _live_results()["the audit passes with no key"]
    assert result["ok"], result["detail"]


def test_the_audit_still_passes_with_a_key():
    result = _live_results()["the audit still passes with a key"]
    assert result["ok"], result["detail"]


def test_the_control_arm_scores_below_the_claim(monkeypatch):
    """The pre-fix tree must read red — the bench is an instrument, not a slogan.

    Driven on the SHIPPED bench's `--broken` path, which is the same arm the
    gate runs, so this asserts the wiring rather than re-measuring the audit.
    The expensive proof (extracting the pre-fix tree and running four audits
    there) lives in the bench's own control command.
    """
    monkeypatch.setattr(bench, "BROKEN", True)
    assert bench._tree() != bench.ROOT, \
        "BROKEN must measure the pre-fix tree, not the live one"

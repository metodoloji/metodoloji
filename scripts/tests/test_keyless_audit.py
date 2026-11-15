"""E-164: the plugin audit must pass on a fresh clone, where no gate key exists.

The defect these tests pin: CI runs `check-plugin.sh` on every PR, and that
audit exited 1 on any machine without `~/.bmad/gate-key` — every fresh clone and
every runner. Each probe drives the real script with HOME pointed at an empty
directory, so the keyless condition is reproduced rather than described.

The tests drive the BENCH's probes, never the audit directly: the audit costs
~20 s and the bench already caches one run per distinct input (E-167). Spawning
it here as well measured the same four inputs a second time and was 43% of the
whole suite's wall clock.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile

BENCH_DIR = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "bench"
if str(BENCH_DIR) not in sys.path:
    sys.path.insert(0, str(BENCH_DIR))

import bench_keyless_audit as bench  # noqa: E402

AUDIT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "check-plugin.sh"


def _audit_without_key(*extra: str) -> subprocess.CompletedProcess:
    """One keyless audit run, shared with the bench's own cache."""
    return bench._run_audit(bench._tree(), bench._home_without_keys(), *extra)


def test_a_missing_key_is_a_warning_not_an_error():
    proc = _audit_without_key()
    out = (proc.stdout or "") + (proc.stderr or "")
    assert "[ERROR] gate key MISSING" not in out
    assert "[WARN] gate key not installed" in out


def test_scope_coverage_does_not_invent_violations_without_a_key():
    proc = _audit_without_key()
    out = (proc.stdout or "") + (proc.stderr or "")
    assert "SKIPPED: no gate key" in out
    assert "MISS:" not in out


def test_the_audit_exits_zero_without_a_key():
    assert _audit_without_key().returncode == 0


def test_strict_keys_still_fails_without_a_key():
    """The escape hatch is preserved: a release job can demand provenance."""
    assert _audit_without_key("--strict-keys").returncode != 0


def test_the_live_tree_passes_every_probe():
    for name, probe in bench.PROBES:
        ok, detail = probe()
        assert ok, f"{name}: {detail}"


def test_the_control_arm_scores_below_the_claim(monkeypatch):
    monkeypatch.setattr(bench, "BROKEN", True)
    monkeypatch.setattr(bench, "_PREFIX_CACHE", None)
    monkeypatch.setattr(bench, "_AUDIT_CACHE", {})
    results = bench.run()
    assert sum(1 for r in results if r["ok"]) < len(results)

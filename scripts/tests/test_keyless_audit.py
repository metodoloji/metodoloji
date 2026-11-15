"""E-164: the plugin audit must pass on a fresh clone, where no gate key exists.

The defect these tests pin: CI runs `check-plugin.sh` on every PR, and that
audit exited 1 on any machine without `~/.bmad/gate-key` — every fresh clone and
every runner. Each probe drives the real script with HOME pointed at an empty
directory, so the keyless condition is reproduced rather than described.
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
    home = tempfile.mkdtemp(prefix="keyless-test-")
    env = {**os.environ, "HOME": home, "USERPROFILE": home}
    env.pop("BMAD_GATE_KEY", None)
    env.pop("BMAD_PEER_GATE_KEY", None)
    return subprocess.run(["sh", str(AUDIT), *extra], cwd=str(AUDIT.parents[1]),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=900, stdin=subprocess.DEVNULL,
                          env=env)


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
    results = bench.run()
    assert sum(1 for r in results if r["ok"]) < len(results)

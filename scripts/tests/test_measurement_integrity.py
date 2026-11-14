"""E-153: a check that cannot run reports a skip, never a failure.

The defect these tests pin: the consistency bench ran `sys.executable -m pytest`
unconditionally, so a host whose pytest lives in `.venv` got three red checks
for a suite that never ran, and the OpenCode bridge check blamed the code for
its own uninstalled declared dependency. The skip must name the environment,
and it must never swallow a real verdict.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

BENCH_DIR = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "bench"
if str(BENCH_DIR) not in sys.path:
    sys.path.insert(0, str(BENCH_DIR))

import bench_consistency  # noqa: E402
import bench_measurement_integrity as bench  # noqa: E402


@pytest.fixture(autouse=True)
def _forget_the_resolved_interpreter():
    """The resolver caches one answer per process; each test asks afresh."""
    bench_consistency._PYTEST_PYTHON.clear()
    yield
    bench_consistency._PYTEST_PYTHON.clear()


def test_the_resolver_finds_an_interpreter_that_can_import_pytest():
    python = bench_consistency._pytest_interpreter()
    assert python, "no interpreter with pytest was found on this host"
    proc = subprocess.run([python, "-m", "pytest", "--version"],
                          capture_output=True, text=True, timeout=120,
                          stdin=subprocess.DEVNULL)
    assert proc.returncode == 0


def test_the_pytest_checks_skip_when_no_interpreter_has_pytest(monkeypatch):
    monkeypatch.setenv("METODOLOJI_PYTEST_PYTHON", "")

    falsifier = bench_consistency._run_pytest()
    assert isinstance(falsifier, bench_consistency._SkippedCheck)
    assert "pytest" in falsifier.reason

    count = bench_consistency._check_documented_test_count_is_honest()
    assert isinstance(count, bench_consistency._SkippedCheck)
    assert "pytest" in count.reason


def test_the_registry_isolation_check_skips_when_no_interpreter_has_pytest(monkeypatch):
    if not (bench_consistency.ROOT / "docs/research/external").is_dir():
        pytest.skip("this checkout has no external-context registry")
    monkeypatch.setenv("METODOLOJI_PYTEST_PYTHON", "")
    verdict = bench_consistency._run_registry_isolation()
    assert isinstance(verdict, bench_consistency._SkippedCheck)
    assert "pytest" in verdict.reason


def test_a_genuinely_red_suite_is_still_a_failure(monkeypatch):
    class _Done:
        returncode = 1
        stdout = "1 failed, 0 passed in 0.01s\n"
        stderr = ""

    def fake_run(argv, **kwargs):
        if "--version" in argv:  # the resolver's probe must still succeed
            return type("_Ok", (), {"returncode": 0, "stdout": "pytest 9.2.0\n",
                                    "stderr": ""})()
        return _Done()

    monkeypatch.setattr(bench_consistency.subprocess, "run", fake_run)
    verdict = bench_consistency._run_pytest()
    assert not isinstance(verdict, bench_consistency._SkippedCheck), (
        "a red suite must never be reported as an unmeasured check")
    assert isinstance(verdict, str) and "exited 1" in verdict


def test_opencode_verdict_distinguishes_missing_dependency_from_failure(tmp_path):
    missing_module = ("Error [ERR_MODULE_NOT_FOUND]: Cannot find package "
                      "'@opencode-ai/plugin'")

    skipped = bench_consistency._opencode_verdict(tmp_path, 1, missing_module)
    assert isinstance(skipped, bench_consistency._SkippedCheck)
    assert "npm install" in skipped.reason

    (tmp_path / "node_modules" / "@opencode-ai" / "plugin").mkdir(parents=True)
    installed = bench_consistency._opencode_verdict(tmp_path, 1, missing_module)
    assert isinstance(installed, str) and "node --test exited 1" in installed

    assertion = bench_consistency._opencode_verdict(
        tmp_path / "no-deps", 1, "not ok 1 - the payload mapping is wrong")
    assert isinstance(assertion, str) and "node --test exited 1" in assertion


def test_every_probe_in_the_bench_passes_on_this_tree():
    results = bench.run(bench_consistency)
    assert all(result["ok"] for result in results), [
        result for result in results if not result["ok"]]

"""E-162: every shipped bench is reachable from a surface CI runs.

The defect these tests pin: 43 benches shipped and CI ran one, so four benches
stayed red for days after commit 2064fa9 emptied README/LICENSE/SECURITY. The
rule has three parts, and each is falsifiable on a throwaway tree.
"""

from __future__ import annotations

import pathlib
import sys

BENCH_DIR = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "bench"
if str(BENCH_DIR) not in sys.path:
    sys.path.insert(0, str(BENCH_DIR))

import bench_bench_binding as bench  # noqa: E402


def test_ci_without_the_runner_is_caught(tmp_path, monkeypatch):
    monkeypatch.setattr(bench, "_tree", lambda: tmp_path)
    ci = tmp_path / bench.CI_REL
    ci.parent.mkdir(parents=True)
    ci.write_text("jobs:\n  python:\n    steps:\n      - run: python -m pytest -q\n",
                  encoding="utf-8")
    ok, detail = bench._case_ci_runs_the_runner()
    assert not ok and "run_all.py" in detail


def test_a_commented_out_step_does_not_count(tmp_path, monkeypatch):
    monkeypatch.setattr(bench, "_tree", lambda: tmp_path)
    ci = tmp_path / bench.CI_REL
    ci.parent.mkdir(parents=True)
    ci.write_text("# - run: python scripts/bench/run_all.py\njobs: {}\n", encoding="utf-8")
    ok, _ = bench._case_ci_runs_the_runner()
    assert not ok


def test_ci_running_the_runner_passes(tmp_path, monkeypatch):
    monkeypatch.setattr(bench, "_tree", lambda: tmp_path)
    ci = tmp_path / bench.CI_REL
    ci.parent.mkdir(parents=True)
    ci.write_text("      - run: python scripts/bench/run_all.py\n", encoding="utf-8")
    ok, _ = bench._case_ci_runs_the_runner()
    assert ok


def test_a_shipped_bench_outside_the_population_is_caught(tmp_path, monkeypatch):
    """The runner derives its population; a bench it skips is a bench nobody runs."""
    monkeypatch.setattr(bench, "_tree", lambda: tmp_path)
    runner = tmp_path / bench.RUNNER_REL
    runner.parent.mkdir(parents=True)
    (runner).write_text(
        "import argparse\nap = argparse.ArgumentParser()\n"
        "ap.add_argument('--list', action='store_true')\n"
        "ap.add_argument('--only', action='append')\n"
        "ap.add_argument('--json')\n"
        "ap.parse_args()\nprint('scripts/bench/bench_one.py')\nprint('1 bench(es)')\n",
        encoding="utf-8")
    (runner.parent / "bench_skipped.py").write_text("print('x')\n", encoding="utf-8")
    ok, detail = bench._case_runner_enumerates_every_bench()
    assert not ok and "bench_skipped.py" in detail


def test_a_red_bench_fails_the_runner(tmp_path, monkeypatch):
    """A gate that passes a 0.00 aggregate is not a gate."""
    monkeypatch.setattr(bench, "_tree", lambda: tmp_path)
    runner = tmp_path / bench.RUNNER_REL
    runner.parent.mkdir(parents=True)
    # A runner that always passes, whatever the bench prints.
    runner.write_text(
        "import argparse\n"
        "ap = argparse.ArgumentParser()\n"
        "ap.add_argument('--list', action='store_true')\n"
        "ap.add_argument('--only', action='append')\n"
        "ap.add_argument('--json')\n"
        "ap.parse_args()\n"
        "print('all good')\n", encoding="utf-8")
    ok, detail = bench._case_a_red_bench_is_reported_red()
    assert not ok and "cannot fail" in detail


def test_the_live_tree_passes_every_probe():
    for name, probe in bench.PROBES:
        ok, detail = probe()
        assert ok, f"{name}: {detail}"


def test_the_control_arm_scores_below_the_claim(monkeypatch):
    monkeypatch.setattr(bench, "BROKEN", True)
    monkeypatch.setattr(bench, "_PREFIX_CACHE", None)
    results = bench.run()
    assert sum(1 for r in results if r["ok"]) < len(results)

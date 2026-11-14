"""E-156: a shipped bench that cannot start is a dead instrument.

The defect these tests pin: four benches imported the deleted `optimization`
package and died before measuring anything, and nothing noticed because no
surface runs a bench. The probes are driven on throwaway trees so the rule is
falsifiable without touching the shipped instruments.
"""

from __future__ import annotations

import pathlib
import sys

BENCH_DIR = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "bench"
if str(BENCH_DIR) not in sys.path:
    sys.path.insert(0, str(BENCH_DIR))

import bench_instrument_health as bench  # noqa: E402


def _tree(tmp_path: pathlib.Path, benches: dict[str, str]) -> pathlib.Path:
    directory = tmp_path / "scripts" / "bench"
    directory.mkdir(parents=True)
    for name, body in benches.items():
        (directory / name).write_text(body, encoding="utf-8")
    return tmp_path


def test_a_syntax_broken_bench_is_caught(tmp_path):
    tree = _tree(tmp_path, {"bench_bad.py": "def broken(:\n    pass\n"})
    ok, detail = bench._case_every_bench_parses(tree)
    assert not ok and "bench_bad.py" in detail


def test_an_import_of_a_removed_subsystem_is_caught(tmp_path):
    tree = _tree(tmp_path, {"bench_dead.py":
                            "from optimization import spec\nprint(spec)\n"})
    ok, detail = bench._case_every_import_resolves(tree)
    assert not ok and "optimization" in detail
    assert "bench_dead.py" in detail


def test_a_module_loaded_by_path_is_not_accused(tmp_path):
    (tmp_path / "scripts" / "bench").mkdir(parents=True)
    (tmp_path / "hooks").mkdir()
    (tmp_path / "hooks" / "engine.py").write_text("", encoding="utf-8")
    (tmp_path / "scripts" / "bench" / "bench_ok.py").write_text(
        "from engine import value\nprint(value)\n", encoding="utf-8")
    ok, _ = bench._case_every_import_resolves(tmp_path)
    assert ok


def test_a_cli_bench_that_cannot_answer_help_is_caught(tmp_path):
    tree = _tree(tmp_path, {"bench_cli.py":
                            "import argparse\nraise SystemExit(3)\n"})
    ok, detail = bench._case_every_cli_bench_answers_help(tree)
    assert not ok and "bench_cli.py" in detail and "exit 3" in detail


def test_a_cli_bench_that_answers_help_passes(tmp_path):
    tree = _tree(tmp_path, {"bench_cli.py":
                            "import argparse\n"
                            "argparse.ArgumentParser().parse_args()\n"})
    ok, _ = bench._case_every_cli_bench_answers_help(tree)
    assert ok


def test_the_live_bench_family_is_healthy():
    results = bench.run(bench.ROOT)
    assert all(result["ok"] for result in results), [
        result for result in results if not result["ok"]]

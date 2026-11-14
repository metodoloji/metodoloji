"""E-158: a live surface that names a missing artifact is a dead instruction.

The defect these tests pin: commit 2064fa9 deleted the optimization subsystem
and the eval shim, and four live surfaces kept pointing into the hole — two
shipped eval adapters, one shipped bench's input set, two shell scripts and one
skill doc. None of them was caught by a rule, because the reference is
executable config rather than prose. Each probe is driven on a throwaway tree so
the rule is falsifiable without touching the shipped instruments.
"""

from __future__ import annotations

import json
import pathlib
import sys

BENCH_DIR = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "bench"
if str(BENCH_DIR) not in sys.path:
    sys.path.insert(0, str(BENCH_DIR))

import bench_retired_references as bench  # noqa: E402

def _adapter(root: pathlib.Path, token: str, name: str = "adapter.json") -> None:
    directory = root / "skills" / "demo" / "evals"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(json.dumps(
        {"invocation": ["python3", token, "{prompt}", "{cwd}"]}), encoding="utf-8")

def test_a_dead_adapter_target_is_caught(tmp_path):
    _adapter(tmp_path, "{repo-root}/skills/demo/evals/gone.py")
    ok, detail = bench._case_adapter_targets(tmp_path)
    assert not ok and "gone.py" in detail

def test_a_live_adapter_target_passes(tmp_path):
    _adapter(tmp_path, "{repo-root}/skills/demo/evals/run.py")
    (tmp_path / "skills" / "demo" / "evals" / "run.py").write_text("", encoding="utf-8")
    ok, _ = bench._case_adapter_targets(tmp_path)
    assert ok

def test_a_shell_script_invoking_a_missing_module_is_caught(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "x.sh").write_text(
        '( cd "$ROOT" && "$PY" -m ghost.cli train )\n', encoding="utf-8")
    ok, detail = bench._case_shell_module_targets(tmp_path)
    assert not ok and "ghost.cli" in detail

def test_a_shell_script_invoking_a_shipped_module_passes(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "x.sh").write_text(
        '"$PY" -m tools.cli --help\n"$PY" -m pytest -q\n', encoding="utf-8")
    (tmp_path / "tools").mkdir()
    ok, _ = bench._case_shell_module_targets(tmp_path)
    assert ok

def test_a_bench_whose_input_is_gone_is_caught(tmp_path):
    directory = tmp_path / "scripts" / "bench"
    directory.mkdir(parents=True)
    (directory / "bench_x.py").write_text(
        "import pathlib\n"
        "ROOT = pathlib.Path(__file__).resolve().parents[2]\n"
        "CASES = ROOT / 'cases' / 'gone.jsonl'\n", encoding="utf-8")
    ok, detail = bench._case_bench_inputs(tmp_path)
    assert not ok and "CASES" in detail and "gone.jsonl" in detail

def test_a_bench_input_that_ships_passes_and_revisions_are_not_paths(tmp_path):
    directory = tmp_path / "scripts" / "bench"
    directory.mkdir(parents=True)
    (directory / "cases").mkdir()
    (directory / "cases" / "ok.jsonl").write_text("", encoding="utf-8")
    (directory / "bench_x.py").write_text(
        "import pathlib\n"
        "ROOT = pathlib.Path(__file__).resolve().parents[2]\n"
        "CASES = ROOT / 'scripts' / 'bench' / 'cases' / 'ok.jsonl'\n"
        "PREFIX_REV = 'ac0c05b'\n"
        "_PHANTOM = ROOT / 'skills' / 'ghost' / 'SKILL.md'\n", encoding="utf-8")
    ok, detail = bench._case_bench_inputs(tmp_path)
    assert ok, detail

def test_a_doc_naming_a_missing_eval_path_is_caught(tmp_path):
    skill = tmp_path / "skills" / "demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "see `skills/demo/evals/gone.py` for the shim\n", encoding="utf-8")
    ok, detail = bench._case_eval_doc_paths(tmp_path)
    assert not ok and "gone.py" in detail

def test_the_live_tree_passes_every_probe():
    results = bench.run(bench.ROOT)
    assert all(result["ok"] for result in results), [
        result for result in results if not result["ok"]]

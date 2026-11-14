"""E-154: a shipped audit must be bound to a surface CI runs.

The defect these tests pin one file large: `scripts/check-runtime-coverage.py`
shipped, had no test, was named by nothing, and read a doc E-152 had retired —
so the audit existed only to crash. The general rule is measured by the bench;
these tests drive its branches on throwaway trees so a future orphan cannot
pass by being left out of a documentary list.
"""

from __future__ import annotations

import pathlib
import sys

BENCH_DIR = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "bench"
if str(BENCH_DIR) not in sys.path:
    sys.path.insert(0, str(BENCH_DIR))

import bench_audit_binding as bench  # noqa: E402


def _tree(tmp_path: pathlib.Path, audits=("check-a.sh",), *, runner: str = "",
          ci: str = "") -> pathlib.Path:
    (tmp_path / "scripts").mkdir()
    for name in audits:
        (tmp_path / "scripts" / name).write_text("# audit\n", encoding="utf-8")
    if "check-plugin.sh" not in audits:
        (tmp_path / "scripts" / "check-plugin.sh").write_text(runner, encoding="utf-8")
    workflow = tmp_path / ".github" / "workflows"
    workflow.mkdir(parents=True)
    (workflow / "quality.yml").write_text(ci, encoding="utf-8")
    return tmp_path


def test_the_family_is_derived_from_disk(tmp_path):
    tree = _tree(tmp_path, audits=("check-a.sh", "check-b.py", "notes.txt"))
    # check-plugin.sh is the helper's runner AND a shipped audit: the family is
    # derived, so every check-* file in scripts/ belongs to it.
    assert bench._family(tree) == ["check-a.sh", "check-b.py", "check-plugin.sh"]


def test_an_audit_no_runner_names_is_an_orphan(tmp_path):
    tree = _tree(tmp_path, runner="", ci="")
    ok, detail = bench._case_every_audit_is_reachable(tree)
    assert not ok and "check-a.sh" in detail


def test_a_comment_mention_does_not_bind_an_audit(tmp_path):
    tree = _tree(tmp_path, runner="# check-a.sh runs in CI one day\n", ci="")
    ok, detail = bench._case_every_audit_is_reachable(tree)
    assert not ok and "check-a.sh" in detail


def test_a_named_audit_that_does_not_ship_is_caught(tmp_path):
    tree = _tree(tmp_path, ci="run: sh scripts/check-ghost.sh\n")
    ok, detail = bench._case_every_named_audit_ships(tree)
    assert not ok and "check-ghost.sh" in detail


def test_a_half_dead_auditor_pair_is_caught(tmp_path):
    tree = _tree(tmp_path, audits=("check-runtime-coverage.py",))
    ok, detail = bench._case_the_retired_auditor_is_gone(tree)
    assert not ok and "check-runtime-coverage.py" in detail


def test_the_live_tree_is_fully_bound():
    results = bench.run(bench.ROOT)
    assert all(result["ok"] for result in results), [
        result for result in results if not result["ok"]]

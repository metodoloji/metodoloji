"""E-161: a shipped surface carries content, and no check is vacuously satisfiable.

The defect these tests pin: commit 2064fa9 emptied README/LICENSE/SECURITY, and
E-152 emptied the four `_AUDIT_*` tuples in `bench_consistency` to match — so
the only instrument that reads README for a living iterated over nothing and
passed. Each probe is driven on a throwaway tree so the rule is falsifiable
without touching the shipped instruments.
"""

from __future__ import annotations

import pathlib
import sys

BENCH_DIR = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "bench"
if str(BENCH_DIR) not in sys.path:
    sys.path.insert(0, str(BENCH_DIR))

import bench_shipped_surfaces as bench  # noqa: E402


def _surface(root: pathlib.Path, name: str, body: str) -> None:
    (root / name).write_text(body, encoding="utf-8")


def test_an_empty_reader_facing_file_is_caught(tmp_path, monkeypatch):
    monkeypatch.setattr(bench, "_tree", lambda: tmp_path)
    _surface(tmp_path, "README.md", "")
    _surface(tmp_path, "LICENSE", "MIT License\n")
    _surface(tmp_path, "SECURITY.md", "# Security\n")
    ok, detail = bench._case_reader_facing_files_are_nonempty()
    assert not ok and "README.md" in detail


def test_nonempty_reader_facing_files_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(bench, "_tree", lambda: tmp_path)
    for name in ("README.md", "LICENSE", "SECURITY.md"):
        _surface(tmp_path, name, "content\n")
    ok, _ = bench._case_reader_facing_files_are_nonempty()
    assert ok


def test_a_license_claim_without_a_license_body_is_caught(tmp_path, monkeypatch):
    monkeypatch.setattr(bench, "_tree", lambda: tmp_path)
    _surface(tmp_path, "LICENSE", "")
    manifest = tmp_path / "plugin.yaml"
    manifest.write_text('license: MIT\n', encoding="utf-8")
    ok, detail = bench._case_license_claim_matches_the_file()
    assert not ok and "MIT" in detail


def test_an_empty_audit_tuple_is_caught(tmp_path, monkeypatch):
    """The E-152 defect: a check whose population is empty reports a pass it never measured."""
    monkeypatch.setattr(bench, "_tree", lambda: tmp_path)
    target = tmp_path / "scripts" / "bench"
    target.mkdir(parents=True)
    (target / "bench_consistency.py").write_text(
        "_AUDIT_INVENTORY_DOCS = ()\n_AUDIT_COUNT_CLAIMS = (('README.md', 'x'),)\n",
        encoding="utf-8")
    _surface(tmp_path, "README.md", "# readme\n")
    ok, detail = bench._case_no_vacuous_check()
    assert not ok and "_AUDIT_INVENTORY_DOCS" in detail


def test_a_named_document_that_does_not_ship_is_caught(tmp_path, monkeypatch):
    monkeypatch.setattr(bench, "_tree", lambda: tmp_path)
    target = tmp_path / "scripts" / "bench"
    target.mkdir(parents=True)
    (target / "bench_consistency.py").write_text(
        "_AUDIT_INVENTORY_DOCS = ('GUIDE.md',)\n", encoding="utf-8")
    ok, detail = bench._case_no_vacuous_check()
    assert not ok and "GUIDE.md" in detail


def test_the_live_tree_passes_every_probe():
    ok, detail = bench._case_the_live_tree_passes_every_probe()
    assert ok, detail


def test_the_control_arm_scores_below_the_claim(monkeypatch):
    monkeypatch.setattr(bench, "BROKEN", True)
    monkeypatch.setattr(bench, "_PREFIX_CACHE", None)
    results = bench.run()
    assert sum(1 for r in results if r["ok"]) < len(results)

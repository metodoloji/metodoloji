"""Tests for hooks/scripts/chain-gate.py — record resolution by id.

The gate resolves the records a run cites (experiment, IR, SP, story). It used
to build `<id>.md` literally, so a title-slug record the inventory accepts
(`E-056-gate-heading-guard.md`) and a differently padded spelling (`SP-9` vs
`SP-009.md`) both read as "record not found" — the gate then failed a chain
whose record was on disk.
"""

import importlib.util
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[3]
SCRIPT = PLUGIN / "hooks" / "scripts" / "chain-gate.py"

_spec = importlib.util.spec_from_file_location("chain_gate", SCRIPT)
chain_gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(chain_gate)


def test_record_resolves_a_slug_named_experiment(tmp_path):
    exp = tmp_path / "docs" / "experiments"
    exp.mkdir(parents=True)
    rec = exp / "E-056-gate-heading-guard.md"
    rec.write_text("# E-056", encoding="utf-8")
    assert chain_gate._record(tmp_path, "experiment", "E-056") == rec


def test_record_resolves_a_differently_padded_sprint(tmp_path):
    dev = tmp_path / "docs" / "development"
    dev.mkdir(parents=True)
    rec = dev / "SP-009.md"
    rec.write_text("# SP-009", encoding="utf-8")
    assert chain_gate._record(tmp_path, "sp", "SP-9") == rec


def test_record_resolves_a_slug_named_story(tmp_path):
    stories = tmp_path / "docs" / "development" / "stories"
    stories.mkdir(parents=True)
    rec = stories / "S-005-audit-trail.md"
    rec.write_text("# S-005", encoding="utf-8")
    assert chain_gate._record(tmp_path, "story", "S-005") == rec


def test_record_does_not_ground_a_neighbouring_id(tmp_path):
    """A shared prefix is not the same record: E-001 vs E-010.md."""
    exp = tmp_path / "docs" / "experiments"
    exp.mkdir(parents=True)
    (exp / "E-010.md").write_text("# E-010", encoding="utf-8")
    assert chain_gate._record(tmp_path, "experiment", "E-001") is None


def test_record_refuses_an_ambiguous_id(tmp_path):
    """Two files for one id are an ambiguous anchor, not a resolution."""
    exp = tmp_path / "docs" / "experiments"
    exp.mkdir(parents=True)
    (exp / "E-001.md").write_text("# E-001", encoding="utf-8")
    (exp / "E-001-copy.md").write_text("# E-001 again", encoding="utf-8")
    assert chain_gate._record(tmp_path, "experiment", "E-001") is None


def test_record_resolves_a_story_filed_under_an_archive(tmp_path):
    """A home is a tree: `stories/archive/S-004.md` is the story, not absence.

    The lookup globbed one level, so a run whose story had been archived failed
    its first gate with "no story record" while the file was on disk.
    """
    archive = tmp_path / "docs" / "development" / "stories" / "archive"
    archive.mkdir(parents=True)
    rec = archive / "S-004-done.md"
    rec.write_text("# S-004", encoding="utf-8")
    assert chain_gate._record(tmp_path, "story", "S-004") == rec


def test_story_identity_resolves_a_story_moved_to_an_archive(tmp_path, monkeypatch):
    archive = tmp_path / "docs" / "development" / "stories" / "archive"
    archive.mkdir(parents=True)
    rec = archive / "S-004-done.md"
    rec.write_text("# S-004", encoding="utf-8")
    monkeypatch.setattr(chain_gate, "_SLUG", "S-004")
    assert chain_gate._story(tmp_path) == rec


def test_record_ignores_a_filename_the_gate_calls_a_note(tmp_path):
    """A lowercase stem is not a record under the filename grammar."""
    exp = tmp_path / "docs" / "experiments"
    exp.mkdir(parents=True)
    (exp / "e-056.md").write_text("# note", encoding="utf-8")
    assert chain_gate._record(tmp_path, "experiment", "E-056") is None

"""A structural declaration is a valid AC reference — and it must resolve.

Structural scope was honoured at the write gate but not at the story/commit
gate: the validator demanded `Experiment: E-NNN` on every acceptance criterion,
so a story whose code was declared structural (rather than measured) could not
be recorded as `review` at all. The only way through was to cite an experiment
that had not measured it — a fabricated link, which is exactly what the
methodology exists to prevent.

The fix teaches both dialects (bracket field and bridge table) that STRUCT-NNN
is a reference, and adds the rule the shape alone would miss: the cited
declaration has to exist on disk.
"""

import pathlib
import sys

_HOOKS = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HOOKS))

from modules.guard import (  # noqa: E402
    _dangling_structural_reason,
    _orphan_story_reason,
    _parse_ac_metadata,
    _validate_story_metadata,
)

TABLE = """## Story: S-900 — fixture

| Field | Value |
| --- | --- |
| Status | review |

## Acceptance Criteria

| AC | Status | Experiment | Type | Measured | Verify |
| --- | --- | --- | --- | --- | --- |
| AC-001 | ✅ passed | %s | agent-verifiable | true | build |
| AC-002 | ✅ passed | %s | agent-verifiable | true | test |
"""

BRACKET = """## Story: S-901 — fixture

- **Status:** review

## Acceptance Criteria

- [AC-001] **Given** a shell **When** it builds **Then** it exits 0
  - Experiment: %s
  - Type: agent-verifiable
  - Measured: true
  - Verify: build
"""


def _with_declaration(root, name="STRUCT-001"):
    d = pathlib.Path(root) / "docs" / "development" / "structural"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text("# Structural Scope\n- **Status:** declared\n",
                                  encoding="utf-8")
    return str(root)


def test_table_dialect_parses_a_structural_reference():
    cells = _parse_ac_metadata(TABLE % ("STRUCT-001", "STRUCT-001"))
    assert [c["experiment"] for c in cells] == ["STRUCT-001", "STRUCT-001"]


def test_bracket_dialect_parses_a_structural_reference():
    cells = _parse_ac_metadata(BRACKET % "STRUCT-001")
    assert cells[0]["experiment"] == "STRUCT-001"
    assert _validate_story_metadata(BRACKET % "STRUCT-001")[0]


def test_a_resolved_declaration_is_not_an_orphan(tmp_path):
    root = _with_declaration(tmp_path)
    content = TABLE % ("STRUCT-001", "STRUCT-001")
    assert _orphan_story_reason(content) == ""
    assert _dangling_structural_reason(content, root) == ""


def test_a_missing_declaration_is_refused(tmp_path):
    """Teaching the shape without resolving it would be the same silence again."""
    content = TABLE % ("STRUCT-404", "STRUCT-404")
    assert _orphan_story_reason(content) == ""  # the shape is valid…
    reason = _dangling_structural_reason(content, str(tmp_path))  # …the file is not
    assert "STRUCT-404" in reason and "no declaration exists" in reason


def test_an_ac_with_no_reference_is_still_an_orphan():
    """The structural door must not become a door with no lock."""
    content = TABLE % ("—", "—")
    assert "no experiment reference" in _orphan_story_reason(content)


def test_experiment_references_still_work():
    """Regression: E-NNN keeps its old meaning in both dialects."""
    content = TABLE % ("E-042", "E-042")
    assert [c["experiment"] for c in _parse_ac_metadata(content)] == ["E-042", "E-042"]
    assert _orphan_story_reason(content) == ""

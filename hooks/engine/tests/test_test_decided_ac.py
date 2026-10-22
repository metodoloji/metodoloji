"""The third category: a criterion decided by a named test.

Two categories existed and neither fits a specification. "The pin's opacity is
0.18 + 0.82·α^0.6" has nothing to discover (not an experiment) and is plainly
visible to a user (not a structural declaration). Before `Test:` such a
criterion had only two homes, both dishonest: cite an experiment that never
measured it, or call it structural and claim nobody can observe it. Both were
observed in a real project, which is why this exists.

The rule is a reference that resolves, the same as E and STRUCT: `path::name`,
where the name must actually appear in the file. "The tests cover it" is the
claim this field is built to make checkable.
"""

import pathlib
import sys

_HOOKS = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HOOKS))

from modules.guard import (  # noqa: E402
    _dangling_test_problem,
    _orphan_story_reason,
    _parse_ac_metadata,
    _validate_methodology_chain,
)

STORY = """# Story: S-900 — fixture

- **Date:** 22.10.2026
- **Status:** in-progress

## Acceptance Criteria

- [AC-001] **Given** α **When** it is painted **Then** opacity is the curve
  - Test: tests/encoding.test.ts::encodes opacity at alpha 0
  - Type: agent-verifiable
  - Measured: true
  - Verify: unit test
"""

TABLE_STORY = """# Story: S-901 — fixture

- **Date:** 22.10.2026
- **Status:** in-progress

## Acceptance Criteria

| AC | Status | Experiment | Test | Type | Measured | Verify |
| --- | --- | --- | --- | --- | --- | --- |
| AC-001 | pending | — | tests/encoding.test.ts::encodes opacity at alpha 0 | agent-verifiable | true | unit test |
"""

TEST_BODY = """import { describe, it, expect } from "vitest";

describe("encoding", () => {
  it("encodes opacity at alpha 0", () => {
    expect(encodeOpacity(0)).toBeCloseTo(0.18);
  });
});
"""


def _project(tmp_path, with_test=True, body=TEST_BODY):
    tests = tmp_path / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    if with_test:
        (tests / "encoding.test.ts").write_text(body, encoding="utf-8")
    return str(tmp_path)


def test_a_test_reference_resolves_and_is_not_an_orphan(tmp_path):
    root = _project(tmp_path)
    assert _dangling_test_problem(STORY, root) == ""
    assert _orphan_story_reason(STORY) == ""
    ok, reason = _validate_methodology_chain(STORY, "docs/development/stories/S-900.md", root)
    assert ok, reason


def test_the_table_dialect_parses_the_test_column(tmp_path):
    cells = _parse_ac_metadata(TABLE_STORY)
    assert cells and cells[0]["test"] == "tests/encoding.test.ts::encodes opacity at alpha 0"


def test_a_missing_test_file_is_refused(tmp_path):
    root = _project(tmp_path, with_test=False)
    reason = _dangling_test_problem(STORY, root)
    assert "does not exist" in reason


def test_a_name_that_is_not_in_the_file_is_refused(tmp_path):
    """The name is the point: a file-level citation would hide a missing test."""
    root = _project(tmp_path, body="it('some other test', () => {});\n")
    reason = _dangling_test_problem(STORY, root)
    assert "contains no test named" in reason


def test_a_file_only_reference_is_refused(tmp_path):
    root = _project(tmp_path)
    story = STORY.replace("tests/encoding.test.ts::encodes opacity at alpha 0",
                          "tests/encoding.test.ts")
    assert "not a `path::name` reference" in _dangling_test_problem(story, root)


def test_a_test_citation_is_not_checked_against_withdrawn_experiments(tmp_path):
    """A mixed story: one AC test-decided, one experiment-decided and inert."""
    (tmp_path / "docs" / "experiments").mkdir(parents=True)
    (tmp_path / "docs" / "experiments" / "E-901.md").write_text(
        "- **Status:** withdrawn\n", encoding="utf-8")
    _project(tmp_path)
    story = STORY + (
        "- [AC-002] **Given** a map **When** it loads **Then** it paints\n"
        "  - Experiment: E-901\n"
        "  - Type: agent-verifiable\n"
        "  - Measured: true\n"
        "  - Verify: bench\n"
    )
    ok, reason = _validate_methodology_chain(story, "docs/development/stories/S-900.md", str(tmp_path))
    assert not ok
    assert "E-901" in reason and "withdrawn" in reason


def test_a_story_with_neither_reference_is_still_an_orphan(tmp_path):
    story = STORY.replace("  - Test: tests/encoding.test.ts::encodes opacity at alpha 0\n", "")
    assert "no experiment reference" in _orphan_story_reason(story)

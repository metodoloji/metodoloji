"""A cited experiment must be able to back the criterion.

The chain validator's docstring has always said "Every AC must reference an
approved experiment E-NNN". The code checked only that the reference was not
empty, so a story could cite a record that was *withdrawn* — measured, then
retracted because the measurement could not fail — and every gate read it as
green. Found on a real project: three stories all cited the same withdrawn
E-004, and the acceptance matrix pointing at a record that decided nothing
looked complete.

The distinction these tests protect is the one the methodology exists for: a
reference is a claim that something was decided, and a withdrawn or rejected
record decided nothing.
"""

import pathlib
import sys

_HOOKS = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HOOKS))

from modules.guard import (  # noqa: E402
    _cited_experiment_problem,
    _validate_methodology_chain,
)

APPROVED_RECORD = """## Experiment: E-900 — fixture

- **Date:** 22.10.2026
- **Status:** APPROVED
- **Hypothesis:** H-900: "rate >= 0.95"
- **Decision:** APPROVED — H-900: measured=1.0 >= threshold=0.95
"""

WITHDRAWN_RECORD = """## Experiment: E-901 — fixture

- **Date:** 22.10.2026
- **Status:** withdrawn — measured, but not falsifiable at the budget
"""

REJECTED_RECORD = """## Experiment: E-902 — fixture

- **Date:** 22.10.2026
- **Status:** completed
- **Decision:** REJECTED — H-902: measured=0.0 >= threshold=0.95 (gate FAIL)
"""

PLANNED_RECORD = """## Experiment: E-903 — fixture

- **Date:** 22.10.2026
- **Status:** planned
"""

STORY = """# Story: S-900 — fixture

- **Date:** 22.10.2026
- **Status:** in-progress

## Acceptance Criteria

- [AC-001] **Given** a shell **When** it builds **Then** it exits 0
  - Experiment: {ref}
  - Type: agent-verifiable
  - Measured: false
  - Verify: build
"""


def _project(tmp_path, records):
    exp = pathlib.Path(tmp_path) / "docs" / "experiments"
    exp.mkdir(parents=True, exist_ok=True)
    for name, body in records.items():
        (exp / f"{name}.md").write_text(body, encoding="utf-8")
    return str(tmp_path)


def test_an_approved_record_backs_a_criterion(tmp_path):
    root = _project(tmp_path, {"E-900": APPROVED_RECORD})
    assert _cited_experiment_problem("E-900", root) == ""


def test_a_withdrawn_record_cannot_back_a_criterion(tmp_path):
    """The case that exposed the hole: withdrawn means 'I retracted the claim'."""
    root = _project(tmp_path, {"E-901": WITHDRAWN_RECORD})
    problem = _cited_experiment_problem("E-901", root)
    assert "withdrawn" in problem
    assert "cannot back an acceptance criterion" in problem


def test_a_rejected_decision_cannot_back_a_criterion(tmp_path):
    """Status may say 'completed' while the gate said REJECTED — read the decision."""
    root = _project(tmp_path, {"E-902": REJECTED_RECORD})
    assert "rejected" in _cited_experiment_problem("E-902", root)


def test_a_planned_record_cannot_back_a_criterion(tmp_path):
    """A plan is not a result; citing one claims evidence that does not exist yet."""
    root = _project(tmp_path, {"E-903": PLANNED_RECORD})
    assert "planned" in _cited_experiment_problem("E-903", root)


def test_a_missing_record_cannot_back_a_criterion(tmp_path):
    root = _project(tmp_path, {})
    assert "no record exists" in _cited_experiment_problem("E-904", root)


def test_the_chain_denies_a_story_citing_a_withdrawn_record(tmp_path):
    root = _project(tmp_path, {"E-901": WITHDRAWN_RECORD})
    ok, reason = _validate_methodology_chain(
        STORY.format(ref="E-901"), "docs/development/stories/S-900.md", root
    )
    assert not ok
    assert "E-901" in reason and "withdrawn" in reason


def test_the_chain_still_passes_an_approved_reference(tmp_path):
    """Regression: the rule must not turn every story red."""
    root = _project(tmp_path, {"E-900": APPROVED_RECORD})
    ok, reason = _validate_methodology_chain(
        STORY.format(ref="E-900"), "docs/development/stories/S-900.md", root
    )
    assert ok, reason


def test_a_structural_reference_is_not_treated_as_an_experiment(tmp_path):
    """STRUCT-001 is resolved by its own probe; it must not be reported missing here."""
    root = _project(tmp_path, {})
    ok, reason = _validate_methodology_chain(
        STORY.format(ref="STRUCT-001"), "docs/development/stories/S-900.md", root
    )
    assert ok, reason

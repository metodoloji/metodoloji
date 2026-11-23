"""The experiment stage resolves the story's basis, not just frontmatter refs.

The story template offers four claims: a measured `Experiment: E-NNN`, a
declared `Experiment: STRUCT-NNN`, a specification `Test: path::name`, and a
`[HYPOTHESIS]` with no code permission. `chain-gate.py --stage experiment`
used to read ONLY the frontmatter `experiment_refs`, so a legitimate
STRUCT-only story failed as "no measured basis", an AC-level E reference was
invisible, and a test-decided criterion could not enter the chain at all.

These tests pin the stage to the same parsers/resolvers the write and story
gates use, in both directions: a valid basis enters, a malformed one fails
with a reason that names the defect.
"""

import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
HOOK_SCRIPTS = ROOT / "hooks" / "scripts"

spec = importlib.util.spec_from_file_location(
    "chain_gate_basis", HOOK_SCRIPTS / "chain-gate.py")
chain_gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(chain_gate)


STRUCT_STORY = """# Story: S-001 — scaffold

- **Status:** review
- **Rigor Tier:** 3

## Acceptance Criteria

- [AC-001] **Given** a shell **When** it builds **Then** it exits 0
  - Experiment: STRUCT-001
  - Type: agent-verifiable
  - Measured: true
  - Verify: build
"""

TEST_STORY = """# Story: S-001 — specification

- **Status:** review
- **Rigor Tier:** 3

## Acceptance Criteria

- [AC-001] **Given** a 600px viewport **When** the map renders **Then** the pin is 60px wide
  - Test: tests/test_map.py::test_pin_width
  - Type: agent-verifiable
  - Measured: true
  - Verify: pytest tests/test_map.py::test_pin_width
"""

NO_BASIS_STORY = """# Story: S-001 — nothing declared

- **Status:** review
- **Rigor Tier:** %s

## Acceptance Criteria

- [AC-001] **Given** x **When** y **Then** z
  - Experiment: —
  - Type: agent-verifiable
  - Measured: false
  - Verify: manual
"""


@pytest.fixture
def project(tmp_path):
    (tmp_path / "docs" / "development" / "stories").mkdir(parents=True)
    chain_gate._SLUG = ""
    return tmp_path


def _story(project, text):
    path = project / "docs" / "development" / "stories" / "S-001.md"
    path.write_text(text, encoding="utf-8")


def _declaration(project, status="declared",
                 justification="Scaffold only: no path here asserts anything measurable.",
                 scope="src/**"):
    d = project / "docs" / "development" / "structural"
    d.mkdir(parents=True, exist_ok=True)
    (d / "STRUCT-001.md").write_text(
        "# Structural Scope: STRUCT-001\n"
        f"- **Status:** {status}\n"
        f"- **Justification:** {justification}\n"
        f"- **Scope:** {scope}\n",
        encoding="utf-8")


def test_struct_only_story_enters_the_chain(project, capsys):
    _story(project, STRUCT_STORY)
    _declaration(project)
    assert chain_gate._gate_experiment(project) == 0
    assert "structural declaration(s) resolve" in capsys.readouterr().out


def test_struct_declaration_must_be_reviewable(project, capsys):
    _story(project, STRUCT_STORY)
    _declaration(project, justification="we needed to move")
    assert chain_gate._gate_experiment(project) == 1
    out = capsys.readouterr().out
    assert "STRUCT-001" in out and "Justification" in out


def test_struct_declaration_must_exist(project, capsys):
    _story(project, STRUCT_STORY)
    assert chain_gate._gate_experiment(project) == 1
    assert "no declaration exists" in capsys.readouterr().out


def test_test_decided_specification_enters_the_chain(project, capsys):
    _story(project, TEST_STORY)
    (project / "tests").mkdir()
    (project / "tests" / "test_map.py").write_text(
        "def test_pin_width():\n    assert True\n", encoding="utf-8")
    assert chain_gate._gate_experiment(project) == 0
    assert "test-decided specification(s) resolve" in capsys.readouterr().out


def test_a_dangling_test_name_is_refused(project, capsys):
    _story(project, TEST_STORY)
    (project / "tests").mkdir()
    (project / "tests" / "test_map.py").write_text(
        "def test_something_else():\n    assert True\n", encoding="utf-8")
    assert chain_gate._gate_experiment(project) == 1
    out = capsys.readouterr().out
    assert "test_pin_width" in out and "no test named" in out


def test_tier_3_without_any_basis_is_refused(project, capsys):
    _story(project, NO_BASIS_STORY % "3")
    assert chain_gate._gate_experiment(project) == 1
    assert "no measured, declared or test-decided basis" in capsys.readouterr().out


def test_tier_1_without_a_basis_is_the_documented_exception(project, capsys):
    _story(project, NO_BASIS_STORY % "1")
    assert chain_gate._gate_experiment(project) == 0
    assert "Rigor Tier 1" in capsys.readouterr().out

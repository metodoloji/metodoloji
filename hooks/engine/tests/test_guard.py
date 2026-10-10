"""Tests for hooks/engine/modules/guard.py — gate record checks + story validation."""

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_HOOKS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HOOKS))

from modules.guard import (  # noqa: E402
    _check_gate_records,
    _find_done_stories_without_qr,
    _parse_experiment_refs,
    _parse_ac_metadata,
)


def _make_project(stories=(), records=()):
    """Create a temp project tree with stories and records.

    records is a list of (record_key, story_key) tuples; each record DECLARES the
    story it covers with the canonical `| Story | <key> |` row (what
    scripts/create-qr-record.py writes) — the gate matches that declaration, not
    any mention of the key in the record's body.
    """
    td = tempfile.TemporaryDirectory()
    root = Path(td.name)
    (root / "docs/development/stories").mkdir(parents=True)
    (root / "docs/quality").mkdir(parents=True)
    for key, status in stories:
        (root / "docs/development/stories" / f"{key}.md").write_text(
            f"## Story: {key}\n- **Status:** {status}\n", encoding="utf-8")
    for rec_key, story_key in records:
        (root / "docs/quality" / f"{rec_key}.md").write_text(
            f"# Quality Record: {rec_key}\n\n| Field | Value |\n|------|-------|\n"
            f"| Story | {story_key} |\n| Status | APPROVED |\n", encoding="utf-8")
    return td, root


# --- hermetic commit-time fixture (TD-015) -------------------------------------
# The scope-coverage mirror reads its file set from `git -C <root> diff/ls-files`
# and its scopes from `<root>/docs/experiments/E-*.md`. Reading the plugin's own
# working tree made these tests a property of the developer's checkout: they
# passed only on a clean tree, so any uncommitted edit reported a false miss —
# and a genuine scope regression hid behind that noise. The fixture is its own
# git repo, so the verdict depends on nothing outside it.

SCOPE_FIXTURE_KEY = "scope-fixture-key"
SCOPE_FIXTURE_PROBE = "bmad/scripts/scope_probe_fake.py"
SCOPE_FIXTURE_EDITED = "bmad/scripts/baseline_fixture.py"


def _fixture_git(args, cwd):
    """git in the fixture with an inline identity (never global config)."""
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30,
        stdin=subprocess.DEVNULL)


def _write_approved_scope_record(root, scope, did="E-001"):
    """An APPROVED record whose genuine token binds `scope` — no measurement run.

    The token is the gate's own HMAC computed with the key this module exports
    (`BMAD_GATE_KEY`), so the record verifies without touching the developer's
    real gate key, and a record is exactly what the mirror reads.
    """
    gate_scripts = (Path(__file__).resolve().parents[3]
                    / "skills" / "bmad-research-experiment" / "scripts")
    if str(gate_scripts) not in sys.path:
        sys.path.insert(0, str(gate_scripts))
    import run_experiment as gate_mod
    claim = "fixture_metric >= 0.9"
    tok = gate_mod.gate_token(claim, 1.0, did,
                              SCOPE_FIXTURE_KEY.encode("utf-8"))
    rec = root / "docs" / "experiments" / f"{did}-scope-fixture.md"
    rec.write_text(
        f"## Experiment: {did} — scope fixture\n"
        "- **Status:** completed\n"
        "- **Theory:** hermetic scope-coverage fixture\n"
        f'- **Hypothesis:** H-001: "{claim}"\n'
        f"- **Measurement Metrics:** {claim}\n"
        "- **Experiment Design:** test fixture\n"
        f"- **Code Scope:** {scope}\n"
        "- **Raw Results:** measured=1.0; n=35\n"
        "- **Uncertainty:** none\n"
        "- **Metric:** consistent\n"
        "- **Decision:** APPROVED — H-001: measured=1.0 >= threshold=0.9\n"
        f'- **Gate Evidence:** measured=1.0 claim="{claim}" {tok}\n'
        "- **Next Step:** Proceed to Code\n",
        encoding="utf-8")
    return rec


def _scope_repo(tmp_path, monkeypatch, scope=None, probe=SCOPE_FIXTURE_PROBE,
                edit_tracked=False):
    """A commit-time repo fixture: baseline commit + a deterministic edit set.

    scope: the Code Scope of an APPROVED record to plant (None -> no record at
           all, so every edit is uncovered).
    probe: path of an untracked edit to plant (None -> a clean tree).
    edit_tracked: also modify a tracked file after the baseline commit.
    """
    monkeypatch.setenv("BMAD_GATE_KEY", SCOPE_FIXTURE_KEY)
    root = tmp_path / "scope-repo"
    (root / "docs" / "experiments").mkdir(parents=True)
    (root / "bmad" / "scripts").mkdir(parents=True)
    (root / "README.md").write_text("baseline\n", encoding="utf-8")
    (root / SCOPE_FIXTURE_EDITED).write_text("# baseline\n", encoding="utf-8")
    _fixture_git(["init", "-q"], root)
    _fixture_git(["add", "-A"], root)
    _fixture_git(["-c", "user.email=fixture@example.invalid",
                  "-c", "user.name=fixture", "commit", "-q", "-m", "baseline"],
                 root)
    if edit_tracked:
        (root / SCOPE_FIXTURE_EDITED).write_text("# baseline\n# edited\n",
                                                 encoding="utf-8")
    if probe:
        target = root / probe
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# probe\n", encoding="utf-8")
    if scope is not None:
        _write_approved_scope_record(root, scope)
    return root


def test_gate_ir_missing_denies():
    td, root = _make_project(stories=[("S-001", "done")])
    try:
        res = _check_gate_records(td.name, "git commit blocked")
        assert res["decision"] == "deny"
        assert "Implementation Readiness" in res["reason"]
    finally:
        td.cleanup()


def test_gate_qr_missing_denies():
    td, root = _make_project(stories=[("S-001", "done")])
    try:
        (root / "docs/development/IR-001.md").write_text("# IR\nready", encoding="utf-8")
        res = _check_gate_records(td.name, "git commit blocked")
        assert res["decision"] == "deny"
        assert "Quality Record" in res["reason"]
    finally:
        td.cleanup()


def test_gate_allow_when_records_present():
    td, root = _make_project(stories=[("S-001", "done")],
                             records=[("QR-001", "S-001")])
    try:
        (root / "docs/development/IR-001.md").write_text("# IR\nready", encoding="utf-8")
        res = _check_gate_records(td.name, "git commit blocked")
        assert res["decision"] == "allow"
    finally:
        td.cleanup()


def test_deploy_requires_pr():
    td, root = _make_project(stories=[("S-001", "done")],
                             records=[("QR-001", "S-001")])
    try:
        (root / "docs/development/IR-001.md").write_text("# IR\nready", encoding="utf-8")
        res = _check_gate_records(td.name, "Deploy blocked", include_pr=True)
        assert res["decision"] == "deny"
        assert "Production Readiness" in res["reason"]
        # adding PR (referencing the story) makes deploy pass
        (root / "docs/development/PR-001.md").write_text(
            "# Production Readiness: PR-001\n\n- **Stories:** S-001\n- **Status:** READY\n",
            encoding="utf-8")
        res2 = _check_gate_records(td.name, "Deploy blocked", include_pr=True)
        assert res2["decision"] == "allow"
    finally:
        td.cleanup()


def test_find_done_stories_without_qr():
    td, root = _make_project(stories=[("S-001", "done"), ("S-002", "in-progress")])
    try:
        missing = _find_done_stories_without_qr(td.name)
        assert "S-001" in missing
        assert "S-002" not in missing  # not done
    finally:
        td.cleanup()


def test_parse_experiment_refs():
    content = """---
id: S-001
experiment_refs:
  - id: E-001
    scope: src/**
    status: APPROVED
---
## Story
"""
    refs = _parse_experiment_refs(content)
    assert len(refs) == 1
    assert refs[0]["id"] == "E-001"
    assert refs[0]["status"] == "APPROVED"


def test_parse_experiment_refs_empty():
    assert _parse_experiment_refs("## Story\nNo frontmatter") == []


def test_parse_ac_metadata():
    content = """## Acceptance Criteria
- [AC-001] **Given** X **When** Y **Then** Z
  - Experiment: E-001
  - Type: agent-verifiable
  - Measured: true
  - Verify: curl http://x
- [AC-002] **Given** X **When** Y **Then** Z
  - Experiment: —
  - Type: user-evaluable
  - Measured: false
  - Verify: manual
  - [HYPOTHESIS]
"""
    acs = _parse_ac_metadata(content)
    assert len(acs) == 2
    assert acs[0]["id"] == "AC-001"
    assert acs[0]["experiment"] == "E-001"
    assert acs[0]["type"] == "agent-verifiable"
    assert acs[1]["is_hypothesis"] is True


def test_parse_ac_metadata_no_section():
    assert _parse_ac_metadata("no ac here") == []


def test_parse_task_ac_refs():
    from modules.guard import _parse_task_ac_refs
    content = """## Technical Tasks
- [ ] implement login AC: AC-001
- [x] fix bug AC: AC-002 AC: AC-003
  - nested subtask AC: AC-999 (not a top-level task)
"""
    tasks = _parse_task_ac_refs(content)
    # Only top-level '- [ ]' / '- [x]' lines are captured; indented subtasks are not.
    assert len(tasks) == 2
    assert tasks[0]["ac_refs"] == ["AC-001"]
    assert tasks[1]["ac_refs"] == ["AC-002", "AC-003"]


def test_validate_story_metadata_missing_fields():
    from modules.guard import _validate_story_metadata
    content = """## Story: S-001
## Acceptance Criteria
- [AC-001] Given X When Y Then Z
"""
    valid, reason = _validate_story_metadata(content)
    assert valid is False
    assert "missing Type field" in reason


def test_validate_story_metadata_ok():
    from modules.guard import _validate_story_metadata
    content = """## Story: S-001
---
experiment_refs:
  - id: E-001
    status: APPROVED
---
## Acceptance Criteria
- [AC-001] Given X When Y Then Z
  - Experiment: E-001
  - Type: agent-verifiable
  - Measured: true
  - Verify: curl http://x
## Technical Tasks
- [ ] do it AC: AC-001
## Definition of Done
- [ ] DoD-001 Verify: manual
"""
    valid, reason = _validate_story_metadata(content)
    assert valid is True, reason


def test_validate_story_metadata_dod_verify_on_next_line():
    """DoD Verify may live on the indented sub-line of a checkbox item."""
    from modules.guard import _validate_story_metadata
    content = """## Story: S-001
## Acceptance Criteria
- [AC-001] Given X When Y Then Z
  - Type: agent-verifiable
  - Measured: true
  - Verify: manual
## Technical Tasks
- [ ] do it AC: AC-001
## Definition of Done
- [ ] DoD-001: All ACs satisfied (AC: AC-001)
  - Verify: pytest tests/
  - Evidence: test output
"""
    valid, reason = _validate_story_metadata(content)
    assert valid is True, reason


def test_validate_story_metadata_dod_token_item_verify_on_next_line():
    """Template-style token items (- [DoD-001] …) with sub-line Verify pass."""
    from modules.guard import _validate_story_metadata
    content = """## Story: S-001
## Definition of Done
- [DoD-001] All acceptance criteria met (AC: AC-001)
  - Verify: pytest tests/
- [DoD-002] Code review done and approved
  - Verify: QR-001 record exists
"""
    valid, reason = _validate_story_metadata(content)
    assert valid is True, reason


def test_validate_story_metadata_dod_ignores_table_rows():
    """Story-mode DoD validation does not treat QR-style markdown tables as
    DoD items (that format belongs to QR records; audit checks it instead)."""
    from modules.guard import _validate_story_metadata
    content = """## Story: S-001
## Definition of Done
- [ ] DoD-001 Verify: manual
| DoD-002 | ✅ passed | curl output | 2026-08-20 |
"""
    valid, reason = _validate_story_metadata(content)
    assert valid is True, reason


def test_validate_story_metadata_dod_missing_verify():
    """A DoD item with no inline or sub-line Verify field is flagged."""
    from modules.guard import _validate_story_metadata
    content = """## Story: S-001
## Definition of Done
- [ ] DoD-001: All ACs satisfied (AC: AC-001)
  - Evidence: test output
"""
    valid, reason = _validate_story_metadata(content)
    assert valid is False
    assert "missing Verify field" in reason


def test_validate_story_metadata_dod_token_item_missing_verify():
    """Template-style token item without any Verify field is flagged."""
    from modules.guard import _validate_story_metadata
    content = """## Story: S-001
## Definition of Done
- [DoD-001] All acceptance criteria met (AC: AC-001)
- [DoD-002] Code review done and approved
  - Verify: QR-001 record exists
"""
    valid, reason = _validate_story_metadata(content)
    assert valid is False
    assert "missing Verify field" in reason


def test_validate_story_metadata_hypothesis_skips_experiment():
    from modules.guard import _validate_story_metadata
    content = """## Acceptance Criteria
- [AC-001] Given X When Y Then Z
  - Experiment: —
  - Type: user-evaluable
  - Measured: false
  - Verify: manual
  - [HYPOTHESIS]
"""
    # With no experiment_refs and a [HYPOTHESIS] AC, the missing Experiment
    # field is not flagged.
    valid, reason = _validate_story_metadata(content)
    assert valid is True, reason


def test_is_git_commit():
    from modules.guard import _is_git_commit
    assert _is_git_commit("git commit -am 'x'") is True
    assert _is_git_commit("git commit --amend") is True
    assert _is_git_commit("git status") is False
    assert _is_git_commit("ls") is False


def test_find_done_stories_without_ir():
    from modules.guard import _find_done_stories_without_ir
    td, root = _make_project(stories=[("S-001", "done")])
    try:
        missing = _find_done_stories_without_ir(td.name)
        assert "S-001" in missing
        (root / "docs/development").mkdir(exist_ok=True)
        (root / "docs/development/IR-001.md").write_text("# IR\n", encoding="utf-8")
        assert _find_done_stories_without_ir(td.name) == []
    finally:
        td.cleanup()


def test_find_done_stories_without_qr_excludes_templates():
    from modules.guard import _find_done_stories_without_qr
    td, root = _make_project(stories=[("S-001", "done"), ("_template", "done")])
    try:
        # _template.md is not an S-NNN file; only real stories are checked.
        missing = _find_done_stories_without_qr(td.name)
        assert "S-001" in missing
        assert "_template" not in missing
    finally:
        td.cleanup()


# --- SP id matching (numeric, not substring) ----------------------------------
# A story row names its sprint by the id it was planned under (`SP-9`), while
# the record file the sprint-planning close writes is zero-padded
# (`SP-009.md`). The old string match false-denied the padded record; a bare
# substring would have been worse — `SP-12` matched `SP-123`'s name, a
# different sprint entirely. ID equality must be numeric and anchored.


def _sp_project(story_ref, record_names):
    td = tempfile.TemporaryDirectory()
    root = Path(td.name)
    (root / "docs/development/stories").mkdir(parents=True)
    (root / "docs/development").mkdir(parents=True, exist_ok=True)
    (root / "docs/development/stories/S-009-foo.md").write_text(
        f"# Story: S-009\nStatus: done\nSprint: {story_ref}\n", encoding="utf-8")
    for name in record_names:
        (root / "docs/development" / name).write_text(
            f"# SP\n{name}\n", encoding="utf-8")
    return td, root


def test_sp_id_matches_zero_padded_record_name():
    from modules.guard import _find_done_stories_without_sp
    td, _root = _sp_project("SP-9", ["SP-009.md"])
    try:
        assert _find_done_stories_without_sp(td.name) == []
    finally:
        td.cleanup()


def test_sp_id_does_not_match_a_superset_name():
    from modules.guard import _find_done_stories_without_sp
    td, _root = _sp_project("SP-12", ["SP-123.md"])
    try:
        assert "S-009" in _find_done_stories_without_sp(td.name)
    finally:
        td.cleanup()


def test_sp_id_does_not_match_a_different_sprint():
    from modules.guard import _find_done_stories_without_sp
    td, _root = _sp_project("SP-9", ["SP-003.md"])
    try:
        assert "S-009" in _find_done_stories_without_sp(td.name)
    finally:
        td.cleanup()


def test_sp_date_named_record_does_not_satisfy_another_sprint_of_the_same_year():
    # A date-named SP record (SP-2026-01-15) shares its leading number with
    # every other sprint of that year. A leading-number-only id match let the
    # November record satisfy a story naming the January sprint, so the SP gate
    # passed with the wrong record and no January record at all (2026-10-01
    # scratch run of bmad-sprint-planning).
    from modules.guard import _find_done_stories_without_sp
    td, _root = _sp_project("SP-2026-01-15", ["SP-2026-11-02.md"])
    try:
        assert "S-009" in _find_done_stories_without_sp(td.name)
    finally:
        td.cleanup()


def test_sp_date_named_record_matches_its_own_sprint():
    from modules.guard import _find_done_stories_without_sp
    td, _root = _sp_project("SP-2026-01-15", ["SP-2026-01-15.md"])
    try:
        assert _find_done_stories_without_sp(td.name) == []
    finally:
        td.cleanup()


def test_sp_date_named_record_matches_zero_padded_segments():
    from modules.guard import _find_done_stories_without_sp
    td, _root = _sp_project("SP-2026-1-5", ["SP-2026-01-05.md"])
    try:
        assert _find_done_stories_without_sp(td.name) == []
    finally:
        td.cleanup()


def _sp_project_with_records(story_ref, records: dict):
    """Like _sp_project but with explicit record NAME -> CONTENT control."""
    import tempfile
    from pathlib import Path
    td = tempfile.TemporaryDirectory()
    root = Path(td.name)
    (root / "docs/development/stories").mkdir(parents=True)
    (root / "docs/development/stories/S-009-foo.md").write_text(
        f"# Story: S-009\nStatus: done\nSprint: {story_ref}\n", encoding="utf-8")
    for name, content in records.items():
        (root / "docs/development" / name).write_text(content, encoding="utf-8")
    return td, root


def test_sp_id_does_not_match_a_forward_mention_in_another_record():
    # Sprint 1's record says "SP-002 opens next …". That mention is not sprint
    # 2's record: a story claiming SP-002 must still be denied until SP-002.md
    # exists (2026-10-01 second-sprint run; the shipped records really do carry
    # forward references like "carried to SP-004").
    from modules.guard import _find_done_stories_without_sp
    td, _root = _sp_project_with_records("SP-002", {
        "SP-001.md": "# Sprint: SP-001 — capture\n\n- **Next step:** SP-002 opens on epic-2\n",
    })
    try:
        assert "S-009" in _find_done_stories_without_sp(td.name)
    finally:
        td.cleanup()


def test_sp_id_matches_a_record_whose_heading_names_it():
    # The id may live in the record's own heading instead of its filename
    # (slug-named records). Identity still, not a body mention.
    from modules.guard import _find_done_stories_without_sp
    td, _root = _sp_project_with_records("SP-2", {
        "SP-recall-sprint.md": "# Sprint: SP-002 — recall\n\nbody\n",
    })
    try:
        assert _find_done_stories_without_sp(td.name) == []
    finally:
        td.cleanup()


def test_chain_check_rejects_a_forward_mention_in_another_record():
    from modules.guard import _validate_methodology_chain
    td, root = _sp_project_with_records("SP-002", {
        "SP-001.md": "# Sprint: SP-001 — capture\n\n- **Next step:** SP-002 opens on epic-2\n",
    })
    try:
        story = (root / "docs/development/stories/S-009-foo.md")
        _ok, reason = _validate_methodology_chain(
            story.read_text(encoding="utf-8"),
            "docs/development/stories/S-009-foo.md", td.name)
        assert "no SP record found" in reason
    finally:
        td.cleanup()


def test_chain_check_rejects_a_different_sprint_of_the_same_year():
    # guard._validate_methodology_chain matched the sprint id with a plain
    # substring test, so "SP-2026-01-15" was found "in" the November record's
    # content and the S->SP backreference passed with the wrong record. Both SP
    # matching sites must agree on what "the same sprint" means.
    from modules.guard import _validate_methodology_chain
    td, root = _sp_project("SP-2026-01-15", ["SP-2026-11-02.md"])
    try:
        story = (root / "docs/development/stories/S-009-foo.md")
        ok, reason = _validate_methodology_chain(
            story.read_text(encoding="utf-8"),
            "docs/development/stories/S-009-foo.md", td.name)
        assert ok is False and "SP-2026-01-15" in reason
    finally:
        td.cleanup()


def test_chain_check_accepts_its_own_sprint_record():
    # The fixture's SP record is deliberately bare, so the chain still fails
    # downstream (no IR backreference); what must NOT happen is the "no SP
    # record found" denial — the record named by the story's own id is accepted
    # and the validator moves on to the IR backreference.
    from modules.guard import _validate_methodology_chain
    td, root = _sp_project("SP-2026-01-15", ["SP-2026-01-15.md"])
    try:
        story = (root / "docs/development/stories/S-009-foo.md")
        _ok, reason = _validate_methodology_chain(
            story.read_text(encoding="utf-8"),
            "docs/development/stories/S-009-foo.md", td.name)
        assert "no SP record found" not in reason
        assert "does not reference any Implementation Readiness" in reason
    finally:
        td.cleanup()


def test_story_without_sp_ref_is_skipped_by_sp_gate():
    from modules.guard import _find_done_stories_without_sp
    td = tempfile.TemporaryDirectory()
    root = Path(td.name)
    (root / "docs/development/stories").mkdir(parents=True)
    (root / "docs/development/stories/S-009.md").write_text(
        "# Story: S-009\nStatus: done\n", encoding="utf-8")
    try:
        assert _find_done_stories_without_sp(td.name) == []
    finally:
        td.cleanup()


# --- native story root ----------------------------------------------------------
# The plugin config's implementation_artifacts default is
# docs/development/native; a story written there must not escape the gates that
# scan docs/development/stories (fikir deep-check 2026-10-01).


def test_native_story_root_is_scanned_for_done_stories():
    from modules.guard import _find_done_stories_without_qr
    td = tempfile.TemporaryDirectory()
    root = Path(td.name)
    (root / "docs/development/native").mkdir(parents=True)
    (root / "docs/quality").mkdir(parents=True)
    (root / "docs/development/native/1-1-bench-in-ci.md").write_text(
        "# Story 1.1: Consistency bench\n\nStatus: done\n\n"
        "- **Story key:** 1-1-bench-in-ci\n", encoding="utf-8")
    try:
        missing = _find_done_stories_without_qr(td.name)
        assert missing == ["1-1-bench-in-ci"]
    finally:
        td.cleanup()


def test_native_story_passes_when_qr_names_its_key():
    from modules.guard import _find_done_stories_without_qr
    td = tempfile.TemporaryDirectory()
    root = Path(td.name)
    (root / "docs/development/native").mkdir(parents=True)
    (root / "docs/quality").mkdir(parents=True)
    (root / "docs/development/native/1-1-bench-in-ci.md").write_text(
        "# Story 1.1: Consistency bench\n\nStatus: done\n\n"
        "- **Story key:** 1-1-bench-in-ci\n", encoding="utf-8")
    (root / "docs/quality/QR-001.md").write_text(
        "# Quality Record: QR-001\n\n| Story | 1-1-bench-in-ci |\n| Status | APPROVED |\n",
        encoding="utf-8")
    try:
        assert _find_done_stories_without_qr(td.name) == []
    finally:
        td.cleanup()


def test_native_root_skips_sprint_status_board():
    from modules.guard import _iter_story_files
    td = tempfile.TemporaryDirectory()
    root = Path(td.name)
    (root / "docs/development/native").mkdir(parents=True)
    (root / "docs/development/native/sprint-status.yaml").write_text(
        "development_status:\n  epic-1: done\n", encoding="utf-8")
    try:
        assert list(_iter_story_files(td.name)) == []
    finally:
        td.cleanup()


def test_guard_never_touches_blackboard(tmp_path, monkeypatch):
    """Faz 2b: guard() performs zero blackboard reads and zero blackboard writes.
    The write hot path is completely decoupled from the blackboard."""
    from modules.guard import guard
    from modules import config
    import modules.blackboard as bb

    monkeypatch.setattr(config, "blackboard_enabled", lambda: True)
    for fn in ("read_board", "stamp_tool_event", "post_alert", "write_key", "set_hot"):
        monkeypatch.setattr(bb, fn, lambda *a, _fn=fn, **k: pytest.fail(
            f"guard() must never call bb.{_fn}"))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)

    res = guard({"tool_name": "file_editor",
                 "tool_input": {"path": "scratch/other.md", "content": "x"}})
    assert res["decision"] == "allow"
    assert res.get("methodology_warnings") is None


def test_guard_fast_exit_skips_board_entirely(tmp_path, monkeypatch):
    """Hot path: a terminal call with no write targets makes no board read."""
    from modules.guard import guard
    from modules import config
    import modules.blackboard as bb

    monkeypatch.setattr(config, "blackboard_enabled", lambda: True)
    monkeypatch.setattr(bb, "read_board", lambda root: pytest.fail(
        "no board read is allowed on the no-target fast path"))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)

    # `cat` only reads: extract_bash_targets resolves no write target.
    res = guard({"tool_name": "terminal",
                 "tool_input": {"command": "cat src/app.py"}})
    assert res["decision"] == "allow"


def test_guard_unknown_tool_warns(tmp_path, monkeypatch):
    """A tool the engine does not understand warns instead of passing silently."""
    from modules.guard import guard
    res = guard({"tool_name": "SomeFutureTool", "tool_input": {}})
    assert res["decision"] == "allow"
    assert any("Unrecognized tool" in w
               for w in res.get("methodology_warnings", []))


@pytest.mark.parametrize("runtime", ["claude", "openhands"])
def test_guard_unknown_tool_warns_with_runtime(tmp_path, monkeypatch, runtime):
    """E-010: on the production runtimes (hook-entry.sh always passes one) an
    unrecognized tool must still be flagged — and only flagged: deny would
    hard-block a legitimate tool the engine simply does not model yet."""
    from modules.guard import guard
    monkeypatch.setenv("METODOLOJI_RUNTIME", runtime)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    res = guard({"tool_name": "SomeFutureTool",
                 "tool_input": {"path": "note.txt"}})
    assert res["decision"] == "allow", res
    assert any("Unrecognized tool" in w
               for w in res.get("methodology_warnings", []))


def test_guard_empty_command_allows_without_board(tmp_path, monkeypatch):
    """An empty terminal command is a no-op, not a gate subject."""
    from modules.guard import guard
    from modules import config
    import modules.blackboard as bb

    monkeypatch.setattr(config, "blackboard_enabled", lambda: True)
    monkeypatch.setattr(bb, "read_board", lambda root: pytest.fail(
        "empty command must not touch the board"))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    res = guard({"tool_name": "terminal", "tool_input": {"command": "  "}})
    assert res["decision"] == "allow"


# --- pre(): combined PreToolUse gate (one process) ---------------------------

def test_pre_allows_when_every_gate_allows(tmp_path, monkeypatch):
    """pre() runs guard + quality + deploy and returns ONE allow decision."""
    from modules.guard import pre
    from modules import config
    import modules.blackboard as bb

    monkeypatch.setattr(config, "blackboard_enabled", lambda: True)
    monkeypatch.setattr(config, "hook_gate_mode", lambda key: "soft")
    monkeypatch.setattr(bb, "read_board", lambda root: {"keys": {}, "tags": []})
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)

    res = pre({"tool_name": "terminal", "tool_input": {"command": "ls"}})
    assert res["decision"] == "allow"


def test_pre_guard_deny_short_circuits_other_gates(tmp_path, monkeypatch):
    """A guard deny wins: quality/deploy are never consulted afterwards."""
    from modules.guard import pre
    from modules import guard as guard_mod
    import modules.blackboard as bb

    monkeypatch.setattr(bb, "read_board", lambda root: {"keys": {}, "tags": []})
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    # Gate-key reference in the command → guard denies (fail-closed path).
    monkeypatch.setattr(
        guard_mod, "quality",
        lambda payload: pytest.fail("quality() must not run after a guard deny"))
    monkeypatch.setattr(
        guard_mod, "deploy",
        lambda payload: pytest.fail("deploy() must not run after a guard deny"))

    res = pre({"tool_name": "terminal",
               "tool_input": {"command": "cat ~/.bmad/gate-key"}})
    assert res["decision"] == "deny"


def test_pre_merges_gate_warnings(monkeypatch):
    """Soft-gate warnings from guard and quality/deploy accumulate."""
    from modules.guard import pre
    from modules import guard as guard_mod

    monkeypatch.setattr(guard_mod, "guard",
                        lambda payload: {"decision": "allow",
                                         "methodology_warnings": ["from-guard"]})
    monkeypatch.setattr(guard_mod, "quality",
                        lambda payload: {"decision": "allow",
                                         "methodology_warnings": ["from-quality"]})
    monkeypatch.setattr(guard_mod, "deploy", lambda payload: {"decision": "allow"})

    res = pre({})
    assert res["decision"] == "allow"
    assert res["methodology_warnings"] == ["from-guard", "from-quality"]


def test_pre_later_gate_deny_carries_earlier_warnings(monkeypatch):
    """A deny from deploy still reports the warnings guard already produced."""
    from modules.guard import pre
    from modules import guard as guard_mod

    monkeypatch.setattr(guard_mod, "guard",
                        lambda payload: {"decision": "allow",
                                         "methodology_warnings": ["from-guard"]})
    monkeypatch.setattr(guard_mod, "quality", lambda payload: {"decision": "allow"})
    monkeypatch.setattr(guard_mod, "deploy",
                        lambda payload: {"decision": "deny",
                                         "reason": "deploy blocked"})

    res = pre({})
    assert res["decision"] == "deny"
    assert res["reason"] == "deploy blocked"
    assert res["methodology_warnings"] == ["from-guard"]


def test_pre_gate_crash_fails_open(monkeypatch):
    """One gate raising must not wedge the call (fail-open, as before)."""
    from modules.guard import pre
    from modules import guard as guard_mod

    monkeypatch.setattr(guard_mod, "guard", lambda payload: {"decision": "allow"})

    def _boom(payload):
        raise RuntimeError("gate exploded")

    monkeypatch.setattr(guard_mod, "quality", _boom)
    monkeypatch.setattr(guard_mod, "deploy", lambda payload: {"decision": "allow"})
    assert pre({})["decision"] == "allow"


def test_guard_soft_gate_warns_not_denies(tmp_path, monkeypatch):
    """Faz 2d: story editing in guard() is allowed; metadata gaps are enforced at commit time via quality()."""
    from modules.guard import guard, quality
    from modules import config
    # Force the soft-gate branch regardless of custom/config.toml.
    monkeypatch.setattr(config, "hook_gate_mode", lambda key: "soft")
    stories = tmp_path / "docs/development/stories"
    stories.mkdir(parents=True)
    content = (
        "## Story: S-001\n"
        "## Acceptance Criteria\n"
        "- [AC-001] Given X When Y Then Z\n"  # missing Type/Measured/Verify
        "## Technical Tasks\n"
        "- [ ] do it AC: AC-001\n"
        "## Definition of Done\n"
        "- [ ] DoD-001 Verify: manual\n"
    )
    (stories / "S-001.md").write_text(content, encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    # 1. Writing story is allowed without blocking
    res = guard({"tool_name": "file_editor",
                 "tool_input": {"path": "docs/development/stories/S-001.md",
                                "content": content}})
    assert res["decision"] == "allow"
    # 2. At commit time, soft gate warns
    res_q = quality({"tool_name": "terminal",
                     "tool_input": {"command": "git commit -m 'feat'"}})
    assert res_q["decision"] == "allow"
    assert any("missing Type field" in w
               for w in res_q.get("methodology_warnings", []))


# --- guard combination matrix -------------------------------------------------

def test_guard_hard_gate_denies_metadata(tmp_path, monkeypatch):
    """quality_gate=hard → story metadata gaps deny at commit time via quality()."""
    from modules.guard import guard, quality
    from modules import config
    monkeypatch.setattr(config, "hook_gate_mode", lambda key: "hard")
    stories = tmp_path / "docs/development/stories"
    stories.mkdir(parents=True)
    content = (
        "## Story: S-001\n"
        "## Acceptance Criteria\n"
        "- [AC-001] Given X When Y Then Z\n"  # missing Type/Measured/Verify
        "## Technical Tasks\n"
        "- [ ] do it AC: AC-001\n"
        "## Definition of Done\n"
        "- [ ] DoD-001 Verify: manual\n"
    )
    (stories / "S-001.md").write_text(content, encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    # Story writing itself allows (no interruption during typing)
    res = guard({"tool_name": "file_editor",
                 "tool_input": {"path": "docs/development/stories/S-001.md",
                                "content": content}})
    assert res["decision"] == "allow"
    # Commit gate denies
    res_q = quality({"tool_name": "terminal",
                     "tool_input": {"command": "git commit -m 'feat'"}})
    assert res_q["decision"] == "deny"
    assert "Story metadata validation failed" in res_q["reason"]


def test_guard_soft_gate_still_denies_missing_experiment(tmp_path, monkeypatch):
    """Even soft gate: a story whose frontmatter names a missing experiment is
    DENY — experiment_refs validity is strictness-independent."""
    from modules.guard import guard
    from modules import config
    monkeypatch.setattr(config, "hook_gate_mode", lambda key: "soft")
    stories = tmp_path / "docs/development/stories"
    stories.mkdir(parents=True)
    content = (
        "---\n"
        "experiment_refs:\n"
        "  - id: E-999\n"
        "    status: APPROVED\n"
        "---\n"
        "## Story: S-001\n"
        "## Acceptance Criteria\n"
        "- [AC-001] Given X When Y Then Z\n"
        "  - Experiment: E-999\n"
        "  - Type: agent-verifiable\n"
        "  - Measured: true\n"
        "  - Verify: curl http://x\n"
    )
    (stories / "S-001.md").write_text(content, encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    res = guard({"tool_name": "file_editor",
                 "tool_input": {"path": "docs/development/stories/S-001.md",
                                "content": content}})
    # E-999 record does not exist → experiment_refs invalid → DENY regardless
    # of the soft gate.
    assert res["decision"] == "deny"
    assert "Story experiment validation failed" in res["reason"]


def test_guard_mixed_gate_config_story_edit_not_blocked_by_deploy_guard(
        tmp_path, monkeypatch):
    """Regression: deploy_guard=hard must NOT block story metadata edits when
    quality_gate=soft. The story path reads quality_gate ONLY — deploy_guard
    governs deploy commands, not file writes."""
    from modules.guard import guard
    from modules import config

    def mixed_mode(key):
        return "hard" if key == "deploy_guard" else "soft"

    monkeypatch.setattr(config, "hook_gate_mode", mixed_mode)
    stories = tmp_path / "docs/development/stories"
    stories.mkdir(parents=True)
    content = (
        "## Story: S-001\n"
        "## Acceptance Criteria\n"
        "- [AC-001] Given X When Y Then Z\n"  # missing Type/Measured/Verify
        "## Technical Tasks\n"
        "- [ ] do it AC: AC-001\n"
        "## Definition of Done\n"
        "- [ ] DoD-001 Verify: manual\n"
    )
    (stories / "S-001.md").write_text(content, encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    # Story writing allows without blocking
    res = guard({"tool_name": "file_editor",
                 "tool_input": {"path": "docs/development/stories/S-001.md",
                                "content": content}})
    assert res["decision"] == "allow"
    # quality_gate=soft → warn-only on commit, allow despite deploy_guard=hard.
    from modules.guard import quality
    res_q = quality({"tool_name": "terminal",
                     "tool_input": {"command": "git commit -m 'x'"}})
    assert res_q["decision"] == "allow"
    assert any("missing Type field" in w
               for w in res_q.get("methodology_warnings", []))


def test_guard_mixed_gate_config_hard_quality_still_denies(tmp_path, monkeypatch):
    """The mirror direction: quality_gate=hard denies story metadata gaps even
    when deploy_guard=soft — the commit path follows quality_gate."""
    from modules.guard import guard, quality
    from modules import config

    def mixed_mode(key):
        return "hard" if key == "quality_gate" else "soft"

    monkeypatch.setattr(config, "hook_gate_mode", mixed_mode)
    stories = tmp_path / "docs/development/stories"
    stories.mkdir(parents=True)
    content = (
        "## Story: S-001\n"
        "## Acceptance Criteria\n"
        "- [AC-001] Given X When Y Then Z\n"
        "## Technical Tasks\n"
        "- [ ] do it AC: AC-001\n"
        "## Definition of Done\n"
        "- [ ] DoD-001 Verify: manual\n"
    )
    (stories / "S-001.md").write_text(content, encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    # Writing allows
    res = guard({"tool_name": "file_editor",
                 "tool_input": {"path": "docs/development/stories/S-001.md",
                                "content": content}})
    assert res["decision"] == "allow"
    # Commit denies
    res_q = quality({"tool_name": "terminal",
                     "tool_input": {"command": "git commit -m 'x'"}})
    assert res_q["decision"] == "deny"
    assert "Story metadata validation failed" in res_q["reason"]


def _write_story(tmp_path, key="S-001", status="done"):
    """Write a done story into tmp_path's docs tree (no records)."""
    (tmp_path / "docs/development/stories").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs/quality").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs/development/stories" / f"{key}.md").write_text(
        f"## Story: {key}\n- **Status:** {status}\n", encoding="utf-8")


def test_quality_soft_gate_warns_not_denies(tmp_path, monkeypatch):
    """quality_gate=soft → git commit with missing IR/QR is warn-only."""
    from modules.guard import quality
    from modules import config
    monkeypatch.setattr(config, "_hook_gate_value",
                        lambda key: "soft")
    _write_story(tmp_path)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    res = quality({"tool_name": "terminal",
                   "tool_input": {"command": "git commit -m 'x'"}})
    assert res["decision"] == "allow"
    assert any("Implementation Readiness" in w
               for w in res.get("methodology_warnings", []))


def test_quality_hard_gate_denies(tmp_path, monkeypatch):
    """quality_gate=hard → git commit with missing IR/QR is DENY."""
    from modules.guard import quality
    from modules import config
    monkeypatch.setattr(config, "_hook_gate_value",
                        lambda key: "hard")
    _write_story(tmp_path)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    res = quality({"tool_name": "terminal",
                   "tool_input": {"command": "git commit -m 'x'"}})
    assert res["decision"] == "deny"


def test_deploy_soft_gate_warns_not_denies(tmp_path, monkeypatch):
    """deploy_guard=soft → deploy with missing PR is warn-only."""
    from modules.guard import deploy
    from modules import config
    monkeypatch.setattr(config, "_hook_gate_value",
                        lambda key: "soft")
    _write_story(tmp_path)
    (tmp_path / "docs/quality/QR-001.md").write_text(
        "# Quality Record: QR-001\n\n| Story | S-001 |\n| Status | APPROVED |\n",
        encoding="utf-8")
    (tmp_path / "docs/development/IR-001.md").write_text("# IR\nready", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    res = deploy({"tool_name": "terminal",
                  "tool_input": {"command": "git push origin main"}})
    assert res["decision"] == "allow"
    assert any("Production Readiness" in w
               for w in res.get("methodology_warnings", []))


def test_deploy_hard_gate_denies(tmp_path, monkeypatch):
    """deploy_guard=hard → deploy with missing PR is DENY."""
    from modules.guard import deploy
    from modules import config
    monkeypatch.setattr(config, "_hook_gate_value",
                        lambda key: "hard")
    _write_story(tmp_path)
    (tmp_path / "docs/quality/QR-001.md").write_text(
        "# Quality Record: QR-001\n\n| Story | S-001 |\n| Status | APPROVED |\n",
        encoding="utf-8")
    (tmp_path / "docs/development/IR-001.md").write_text("# IR\nready", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    res = deploy({"tool_name": "terminal",
                  "tool_input": {"command": "git push origin main"}})
    assert res["decision"] == "deny"


def test_gates_read_independent_keys(tmp_path, monkeypatch):
    """quality_gate and deploy_guard are read independently: deploy_guard=hard
    must not make the quality gate hard (and vice versa)."""
    from modules.guard import quality, deploy
    from modules import config
    # Only deploy_guard is hard.
    monkeypatch.setattr(config, "_hook_gate_value",
                        lambda key: "hard" if key == "deploy_guard" else "soft")
    _write_story(tmp_path)  # no IR record → both gates fail the IR check
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    # quality gate soft → allow + warning (never deny)
    q = quality({"tool_name": "terminal",
                 "tool_input": {"command": "git commit -m 'x'"}})
    assert q["decision"] == "allow"
    assert any("Implementation Readiness" in w
               for w in q.get("methodology_warnings", []))
    # deploy gate hard → deny on the same IR gap
    d = deploy({"tool_name": "terminal",
                "tool_input": {"command": "git push origin main"}})
    assert d["decision"] == "deny"
    assert "Implementation Readiness" in d["reason"]


def test_guard_notes_story_filename_not_story(tmp_path, monkeypatch):
    """notes-S-001.md is an ordinary file, not a story — no metadata gate."""
    from modules.guard import guard
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    res = guard({"tool_name": "file_editor",
                 "tool_input": {"path": "docs/notes-S-001.md",
                                "content": "hello"}})
    assert res["decision"] == "allow"


def test_guard_frontmatter_body_rule_not_fence(tmp_path, monkeypatch):
    """A '---' rule inside the body must not truncate the frontmatter."""
    from modules.guard import _parse_experiment_refs
    content = ("---\n"
               "experiment_refs:\n"
               "  - id: E-001\n"
               "    status: APPROVED\n"
               "---\n"
               "## Body\n---\nrest\n")
    refs = _parse_experiment_refs(content)
    assert len(refs) == 1 and refs[0]["id"] == "E-001"


def test_guard_frontmatter_top_level_key_ends_refs():
    """status: draft after the refs is frontmatter, not part of the ref."""
    from modules.guard import _parse_experiment_refs
    content = ("---\n"
               "experiment_refs:\n"
               "  - id: E-001\n"
               "    status: APPROVED\n"
               "status: draft\n"
               "---\n")
    refs = _parse_experiment_refs(content)
    assert refs == [{"id": "E-001", "status": "APPROVED"}]


def test_guard_chain_cache_avoids_reread(tmp_path, monkeypatch):
    """Unchanged QR files are read once across repeated chain checks."""
    from modules.guard import _validate_methodology_chain, _cached_text
    import pathlib
    (tmp_path / "docs/quality").mkdir(parents=True)
    (tmp_path / "docs/development/stories").mkdir(parents=True)
    qr = tmp_path / "docs/quality/QR-001.md"
    qr.write_text("# Quality Record: QR-001\n\n| Story | S-001 |\n| Status | APPROVED |\n",
                  encoding="utf-8")
    content = "## Story: S-001\n- **Status:** done\n"
    _cached_text.cache_clear()
    reads = []
    orig_read = pathlib.Path.read_text
    def counting(self, *a, **k):
        reads.append(str(self))
        return orig_read(self, *a, **k)
    monkeypatch.setattr(pathlib.Path, "read_text", counting)
    _validate_methodology_chain(content, "docs/development/stories/S-001.md",
                                root=str(tmp_path))
    _validate_methodology_chain(content, "docs/development/stories/S-001.md",
                                root=str(tmp_path))
    assert reads.count(str(qr)) == 1


def test_guard_secret_context_still_denies(tmp_path, monkeypatch):
    """Access context (call/assign) still denies — narrowing only drops prose."""
    from modules.guard import guard
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    res = guard({"tool_name": "file_editor",
                 "tool_input": {"path": "scratch/notes.md",
                                "content": "x = load_secret('k')"}})
    assert res["decision"] == "deny"


def test_guard_secret_prose_no_longer_denies(tmp_path, monkeypatch):
    """Bare 'secret_env' in prose is not an access — no deny."""
    from modules.guard import guard
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    res = guard({"tool_name": "file_editor",
                 "tool_input": {"path": "scratch/notes.md",
                                "content": "the secret_env carries over"}})
    assert res["decision"] == "allow"


def test_guard_verify_cache_skips_reverify(tmp_path, monkeypatch):
    """Unchanged records verify once; second find_approved hits the cache."""
    import sys
    from modules.guard import find_approved, _VERIFY_CACHE
    guard_mod = sys.modules["modules.guard"]
    (tmp_path / "docs/experiments").mkdir(parents=True)
    (tmp_path / "docs/experiments/E-001.md").write_text("# E\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(guard_mod, "verify_record",
                        lambda rec: (calls.append(rec) or (0, "src/**")))
    monkeypatch.setattr(guard_mod, "_load_gate", lambda: True)
    monkeypatch.setattr(guard_mod, "gate",
                        type("G", (), {"scope_matches": staticmethod(lambda s, t: True)})(),
                        raising=False)
    _VERIFY_CACHE.clear()
    find_approved("src/a.py", root=str(tmp_path))
    find_approved("src/b.py", root=str(tmp_path))
    assert len(calls) == 1


def test_guard_code_guard_soft_warns_not_denies(tmp_path, monkeypatch):
    """code_guard=soft (brownfield): unapproved write warns, still allows."""
    from modules.guard import guard
    from modules import config
    monkeypatch.setattr(config, "hook_gate_mode",
                        lambda key: "soft" if key == "code_guard" else "hard")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    res = guard({"tool_name": "file_editor",
                 "tool_input": {"path": "src/main.py", "content": "print(1)"}})
    assert res["decision"] == "allow"
    assert any("No approved experiment record" in w
               for w in res.get("methodology_warnings", []))


def test_guard_code_guard_hard_still_denies(tmp_path, monkeypatch):
    """Default code_guard=hard: unapproved write still denies."""
    from modules.guard import guard
    from modules import config
    monkeypatch.setattr(config, "hook_gate_mode", lambda key: "hard")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    res = guard({"tool_name": "file_editor",
                 "tool_input": {"path": "src/main.py", "content": "print(1)"}})
    assert res["decision"] == "deny"


def test_guard_scope_inside_no_warning(tmp_path, monkeypatch):
    """A write inside the active scope gets no scope warning."""
    from modules.guard import guard
    from modules import config
    monkeypatch.setattr(config, "hook_gate_mode", lambda key: "soft")
    (tmp_path / "src/auth").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    res = guard({"tool_name": "file_editor",
                 "tool_input": {"path": "src/auth/login.py", "content": "x"}})
    # Free-zone? src/ is not free → needs approval → deny (no experiment record).
    # The scope check itself must not add a scope warning when inside scope.
    warns = res.get("methodology_warnings", [])
    assert not any("outside the active scope" in w for w in warns)


# --- out-of-project targets (2026-09-30 session regression) -------------------

def test_guard_out_of_project_target_note(tmp_path, monkeypatch):
    """A denied target outside the project root gets the honest scope note:
    scope matching runs on the full path, so only an absolute Code Scope
    entry in this project could cover it."""
    from modules.guard import guard
    from modules import config
    monkeypatch.setattr(config, "hook_gate_mode", lambda key: "hard")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    res = guard({"tool_name": "file_editor",
                 "tool_input": {"path": "D:/elsewhere/tool.py", "content": "print(1)"}})
    assert res["decision"] == "deny"
    assert "outside the project root" in res["reason"]
    assert "absolute Code Scope" in res["reason"]


def test_guard_installed_plugin_tree_note(tmp_path, monkeypatch):
    """Editing the INSTALLED plugin's own source from a consuming project is
    denied by design (plugin trees stay gated outside the methodology repo),
    but the deny must say so instead of implying a project-relative record."""
    from modules import config
    from modules.guard import guard
    plugin_root = tmp_path / "install"
    monkeypatch.setattr(config, "_METHODOLOGY_ROOT", plugin_root)
    monkeypatch.setattr(config, "hook_gate_mode", lambda key: "hard")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    target = plugin_root / "hooks" / "engine" / "modules" / "bash_targets.py"
    res = guard({"tool_name": "file_editor",
                 "tool_input": {"path": str(target), "content": "print(1)"}})
    assert res["decision"] == "deny"
    assert "metodoloji plugin tree" in res["reason"]
    assert "self-modification" in res["reason"]


def test_guard_board_write_command_allowed(tmp_path, monkeypatch):
    """The real session's board write (quoted script path + --value) must not
    be classified as a code write of blackboard.py."""
    from modules import config
    from modules.guard import guard
    monkeypatch.setattr(config, "hook_gate_mode", lambda key: "hard")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    cmd = ('python "C:/plugins/metodoloji/1.2.4/bmad/scripts/blackboard.py" '
           'write --key prd.x.pending --value complete')
    res = guard({"tool_name": "terminal", "tool_input": {"command": cmd}})
    assert res["decision"] == "allow"


# --- terminal story writes (content visibility) -----------------------------

def _story_with_missing_fields():
    return (
        "## Story: S-001\n"
        "## Acceptance Criteria\n"
        "- [AC-001] Given X When Y Then Z\n"  # missing Type/Measured/Verify
        "## Technical Tasks\n"
        "- [ ] do it AC: AC-001\n"
        "## Definition of Done\n"
        "- [ ] DoD-001 Verify: manual\n"
    )


def test_story_heredoc_body_extracts_payload():
    from modules.guard import _story_heredoc_body
    cmd = (
        "cat > docs/development/stories/S-002.md <<'EOF'\n"
        "## Story: S-002\n"
        "- **Status:** in-progress\n"
        "EOF\n"
    )
    body = _story_heredoc_body(cmd, "docs/development/stories/S-002.md")
    assert body == "## Story: S-002\n- **Status:** in-progress"


def test_story_heredoc_body_marker_after_redirect():
    from modules.guard import _story_heredoc_body
    cmd = (
        "cat <<'MD' > docs/development/stories/S-003.md\n"
        "## Story: S-003\n"
        "MD\n"
    )
    body = _story_heredoc_body(cmd, "docs/development/stories/S-003.md")
    assert body == "## Story: S-003"


def test_story_heredoc_body_other_target_returns_none():
    from modules.guard import _story_heredoc_body
    # Heredoc writes a DIFFERENT file; no story payload to validate.
    cmd = "cat > tmp/notes.txt <<'EOF'\nhello\nEOF\n"
    assert _story_heredoc_body(cmd, "docs/development/stories/S-002.md") is None
    # No heredoc at all.
    assert _story_heredoc_body("cp a.md b.md", "docs/x.md") is None


def _story_path(tmp_path, key):
    """Absolute path to a story inside tmp_path's docs tree."""
    d = tmp_path / "docs/development/stories"
    d.mkdir(parents=True, exist_ok=True)
    return (d / f"{key}.md").as_posix()


def test_guard_terminal_modify_existing_story_validates_content(tmp_path, monkeypatch):
    """Terminal write to existing story allows without interruption; quality() checks on commit."""
    from modules.guard import guard, quality
    from modules import config
    monkeypatch.setattr(config, "hook_gate_mode", lambda key: "soft")
    target = _story_path(tmp_path, "S-001")
    Path(target).write_text(_story_with_missing_fields(), encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    res = guard({"tool_name": "terminal",
                 "tool_input": {"command": f"echo x >> {target}"}})
    assert res["decision"] == "allow"
    # Commit gate catches metadata issue
    res_q = quality({"tool_name": "terminal",
                     "tool_input": {"command": "git commit -m 'feat'"}})
    assert res_q["decision"] == "allow"
    assert any("missing Type field" in w
               for w in res_q.get("methodology_warnings", []))


def test_guard_terminal_modify_existing_story_hard_gate_denies(tmp_path, monkeypatch):
    """quality_gate=hard: commit denies on an invalid story."""
    from modules.guard import guard, quality
    from modules import config
    monkeypatch.setattr(config, "hook_gate_mode", lambda key: "hard")
    target = _story_path(tmp_path, "S-001")
    Path(target).write_text(_story_with_missing_fields(), encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    # Guard allows write
    res = guard({"tool_name": "terminal",
                 "tool_input": {"command": f"echo x >> {target}"}})
    assert res["decision"] == "allow"
    # Commit gate denies
    res_q = quality({"tool_name": "terminal",
                     "tool_input": {"command": "git commit -m 'feat'"}})
    assert res_q["decision"] == "deny"
    assert "Story metadata validation failed" in res_q["reason"]


def test_guard_terminal_heredoc_creation_denies_bad_experiment(tmp_path, monkeypatch):
    """A story CREATED by a terminal heredoc is validated from its payload:
    an experiment_refs pointing at a missing record denies (mode-independent)."""
    from modules.guard import guard
    from modules import config
    monkeypatch.setattr(config, "hook_gate_mode", lambda key: "soft")
    target = _story_path(tmp_path, "S-002")
    cmd = (
        f"cat > {target} <<'EOF'\n"
        "---\n"
        "experiment_refs:\n"
        "  - id: E-999\n"
        "    status: APPROVED\n"
        "---\n"
        "## Story: S-002\n"
        "EOF\n"
    )
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    res = guard({"tool_name": "terminal", "tool_input": {"command": cmd}})
    assert res["decision"] == "deny"
    assert "Story experiment validation failed" in res["reason"]


def test_guard_terminal_heredoc_creation_valid_payload_allows(tmp_path, monkeypatch):
    """A heredoc-created story WITHOUT experiment_refs and with valid AC
    metadata passes in soft mode without the bypass warning."""
    from modules.guard import guard
    from modules import config
    monkeypatch.setattr(config, "hook_gate_mode", lambda key: "soft")
    target = _story_path(tmp_path, "S-004")
    cmd = (
        f"cat > {target} <<'EOF'\n"
        "## Story: S-004\n"
        "## Acceptance Criteria\n"
        "- [AC-001] Given X When Y Then Z\n"
        "  - Type: agent-verifiable\n"
        "  - Measured: true\n"
        "  - Verify: curl http://x\n"
        "EOF\n"
    )
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    res = guard({"tool_name": "terminal", "tool_input": {"command": cmd}})
    assert res["decision"] == "allow"
    assert not any("content is not visible" in w
                   for w in res.get("methodology_warnings", []))


def test_guard_terminal_opaque_creation_warns_bypass(tmp_path, monkeypatch):
    """A terminal story creation whose payload is NOT visible (no heredoc)
    allows but flags that content validation did not run here."""
    from modules.guard import guard
    from modules import config
    monkeypatch.setattr(config, "hook_gate_mode", lambda key: "hard")
    src = _story_path(tmp_path, "_template_S")
    dst = _story_path(tmp_path, "S-005")
    Path(src).write_text("## Story: S-NEW\n", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    res = guard({"tool_name": "terminal",
                 "tool_input": {"command": f"cp {src} {dst}"}})
    assert res["decision"] == "allow"
    assert any("content is not visible" in w
               for w in res.get("methodology_warnings", []))


def test_guard_terminal_existing_story_unreadable_warns(tmp_path, monkeypatch):
    """An existing story that cannot be read (OSError, e.g. permissions) is not
    silently skipped: guard flags it exactly like an opaque creation."""
    import sys as _sys
    from modules.guard import guard
    from modules import config
    monkeypatch.setattr(config, "hook_gate_mode", lambda key: "hard")
    target = _story_path(tmp_path, "S-001")
    Path(target).write_text("## Story: S-001\n", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)

    class _Unreadable:
        """pathlib.Path stand-in: exists but its content cannot be read."""
        def __init__(self, *a, **k):
            pass
        def is_file(self):
            return True
        def read_text(self, *a, **k):
            raise OSError("permission denied")

    class _FakePathlib:
        Path = _Unreadable

    guard_mod = _sys.modules["modules.guard"]
    monkeypatch.setattr(guard_mod, "pathlib", _FakePathlib())
    res = guard({"tool_name": "terminal",
                 "tool_input": {"command": f"echo x >> {target}"}})
    assert res["decision"] == "allow"
    assert any("content is not visible" in w
               for w in res.get("methodology_warnings", []))


# === NEW TESTS for CRITICAL/HIGH/MEDIUM fixes (MEDIUM #12 / ISSUE #71) ===


def test_check_duplicate_record_ids_detects_duplicates():
    """E-011: the same record ID in a second location is a duplicate at creation.

    The pre-E-011 implementation globbed ONE directory — where a duplicate
    filename cannot exist — so this scenario was unreachable; the old test said
    as much ("filenames can't have duplicates in same dir, so this test is
    N/A") and only asserted that a lone file is unique.
    """
    from modules.guard import _check_duplicate_record_ids
    td = tempfile.TemporaryDirectory()
    root = Path(td.name)
    try:
        stories = root / "docs/development/stories"
        (stories / "archive").mkdir(parents=True)
        (stories / "S-001.md").write_text("# Story: S-001\n", encoding="utf-8")
        (stories / "archive" / "S-001.md").write_text("# S-001 archived copy\n", encoding="utf-8")
        unique, reason = _check_duplicate_record_ids(root, "docs/development/stories/S-001.md")
        assert unique is True  # edit path: existing file skips the scan
        assert not reason

        # A record living in the other artifacts tree is a real duplicate when
        # the file being written does not exist yet (creation path).
        other_tree = root / "bmad-output/implementation-artifacts"
        other_tree.mkdir(parents=True)
        (other_tree / "QR-002.md").write_text("# QR\n", encoding="utf-8")
        unique2, reason2 = _check_duplicate_record_ids(root, "docs/quality/QR-002.md")
        assert unique2 is False and "bmad-output" in reason2
    finally:
        td.cleanup()


def test_check_duplicate_record_ids_creation_vs_edit():
    """Edit path skips the project scan; a brand-new file still pays it."""
    from modules.guard import _check_duplicate_record_ids
    td = tempfile.TemporaryDirectory()
    root = Path(td.name)
    try:
        other_tree = root / "docs/development" / "archive"
        other_tree.mkdir(parents=True)
        (other_tree / "E-001.md").write_text("# E-001 archived\n", encoding="utf-8")

        # Creation: file does not exist → scan runs → duplicate caught.
        unique, reason = _check_duplicate_record_ids(root, "docs/experiments/E-001.md")
        assert unique is False

        # Edit: file now exists → scan skipped → unique.
        (root / "docs/experiments").mkdir(parents=True)
        (root / "docs/experiments" / "E-001.md").write_text("# E-001\n", encoding="utf-8")
        unique, reason = _check_duplicate_record_ids(root, "docs/experiments/E-001.md")
        assert unique is True and not reason
    finally:
        td.cleanup()


def test_check_duplicate_record_ids_unique_file_not_flagged():
    """E-011 false-block guard: a lone record file is unique."""
    from modules.guard import _check_duplicate_record_ids
    td = tempfile.TemporaryDirectory()
    root = Path(td.name)
    try:
        (root / "docs/experiments").mkdir(parents=True)
        (root / "docs/experiments" / "E-001.md").write_text("# E-001\n", encoding="utf-8")
        is_unique, reason = _check_duplicate_record_ids(root, "docs/experiments/E-001.md")
        assert is_unique is True and not reason
    finally:
        td.cleanup()


def test_check_duplicate_record_ids_skips_non_record_filenames():
    """Filenames that are not record IDs are never scanned or flagged."""
    from modules.guard import _check_duplicate_record_ids
    td = tempfile.TemporaryDirectory()
    root = Path(td.name)
    try:
        stories = root / "docs/development/stories"
        (stories / "archive").mkdir(parents=True)
        for name in ("notes-S-001.md", "_template_S.md", "note-1.md", "README.md"):
            (stories / name).write_text("# note\n", encoding="utf-8")
            (stories / "archive" / name).write_text("# note\n", encoding="utf-8")
            is_unique, reason = _check_duplicate_record_ids(
                root, f"docs/development/stories/{name}")
            assert is_unique is True and not reason, (name, reason)
    finally:
        td.cleanup()


def test_validate_methodology_chain_bounds_limits_iterations():
    """Test that _validate_methodology_chain respects MAX_STORY_COUNT bounds (MEDIUM #11)."""
    from modules.guard import _validate_methodology_chain
    from modules.config import MAX_STORY_COUNT
    
    td = tempfile.TemporaryDirectory()
    root = Path(td.name)
    try:
        # Create story with many sprint plans
        (root / "docs/development").mkdir(parents=True)
        (root / "docs/stories").mkdir(parents=True)
        
        story_content = "# Story\nStatus: review\nSP-001"
        
        # Create MAX_STORY_COUNT + 100 dummy stories (way over limit)
        for i in range(MAX_STORY_COUNT + 100):
            (root / "docs/stories" / f"S-{i:04d}.md").write_text(
                f"# Story {i}\n", encoding="utf-8")
        
        # Validation should complete without hanging (bounds enforced)
        valid, reason = _validate_methodology_chain(story_content, "test.md", root)
        # Result depends on content, but should not error or hang
        assert isinstance(valid, bool)
    finally:
        td.cleanup()


def test_validation_bounds_defined():
    """Test that the used MAX_* validation bounds are defined in config (MEDIUM #11)."""
    from modules.config import MAX_STORY_COUNT, MAX_DUPLICATE_CHECK_RECORDS

    # All should be positive integers
    assert MAX_STORY_COUNT > 0
    assert MAX_DUPLICATE_CHECK_RECORDS > 0

    # Sanity check: limits should be reasonable
    assert MAX_STORY_COUNT >= 100


# --- scope-coverage mirror (check-plugin §3b at commit time) -------------------

def test_scope_coverage_catches_uncovered_protected_file(tmp_path, monkeypatch):
    """An edited file in a protected tree outside every approved scope is a MISS,
    and a tree git cannot answer for fails open instead of reading as clean."""
    from modules.guard import _check_scope_coverage
    bare = tmp_path / "bare"
    bare.mkdir()
    # No repo (git exits 129) -> fail-open: ran=False, no misses invented.
    assert _check_scope_coverage(str(bare)) == ([], False)
    root = _scope_repo(tmp_path, monkeypatch, scope=None)
    misses, ran = _check_scope_coverage(str(root))
    assert ran is True
    assert SCOPE_FIXTURE_PROBE in misses


def test_quality_warns_on_scope_miss_at_commit_time(tmp_path, monkeypatch):
    """quality() on a git commit surfaces the uncovered file as a warn-only
    methodology_warning — announce, never block (the write gate owns blocks).
    The record-chain checks are stubbed allow: when the chain itself denies,
    that deny returns early ON PURPOSE (a blocking issue outranks the advice);
    this test isolates the scope-mirror wiring."""
    import modules.guard as g
    monkeypatch.setattr(g, "_check_gate_records",
                        lambda root, action, include_pr=False: {"decision": "allow"})
    monkeypatch.setattr(g, "_check_all_stories_quality",
                        lambda root, action: {"decision": "allow"})
    monkeypatch.setattr(g, "_check_qr_table_coherence",
                        lambda root, action: {"decision": "allow"})
    # Keep the board untouched: the mirror now also posts to the "scope"
    # channel; stub both board calls so this test only pins the transcript
    # warning (the posting contract has its own dedicated tests).
    import modules.blackboard as bb
    monkeypatch.setattr(bb, "consume_alerts", lambda root, ch: [])
    monkeypatch.setattr(bb, "post_alert",
                        lambda *a, **k: {"ok": True})
    # Fixture: an APPROVED record exists, but its scope covers neither the
    # untracked probe nor the modified tracked file (both are reported).
    root = _scope_repo(tmp_path, monkeypatch, scope="src/**", edit_tracked=True)
    res = g.quality({"tool_name": "terminal",
                     "tool_input": {"command": "git commit -m 'x'"},
                     "cwd": str(root)})
    assert res["decision"] == "allow"  # announce-only at commit time
    warn = " ".join(res.get("methodology_warnings", []))
    assert "SCOPE COVERAGE" in warn
    assert "scope_probe_fake" in warn      # untracked edit
    assert SCOPE_FIXTURE_EDITED in warn     # modified tracked edit


def test_quality_chain_deny_shadows_scope_warning(tmp_path, monkeypatch):
    """Ordering contract: a record-chain deny returns before the scope mirror —
    a blocking issue outranks the advice, and the mirror must not dilute it."""
    import modules.guard as g
    monkeypatch.setattr(g, "_check_gate_records",
                        lambda root, action, include_pr=False:
                        {"decision": "deny", "reason": "chain broken"})
    from modules import config
    monkeypatch.setattr(config, "_hook_gate_value", lambda key: "soft")
    root = _scope_repo(tmp_path, monkeypatch, scope=None)
    res = g.quality({"tool_name": "terminal",
                     "tool_input": {"command": "git commit -m 'x'"},
                     "cwd": str(root)})
    assert res["decision"] == "allow"  # soft converts the deny
    assert any("chain broken" in w for w in res.get("methodology_warnings", []))
    assert not any("SCOPE COVERAGE" in w
                   for w in res.get("methodology_warnings", []))


def test_scope_coverage_clean_tree_has_no_warning(tmp_path, monkeypatch):
    """No warning when the tree is clean, and none when every edit sits inside an
    approved scope. Hermetic (TD-015): this used to read the plugin's own working
    tree, so an uncommitted edit anywhere in the checkout turned it red."""
    import modules.guard as g
    monkeypatch.setattr(g, "_check_gate_records",
                        lambda root, action, include_pr=False: {"decision": "allow"})
    monkeypatch.setattr(g, "_check_all_stories_quality",
                        lambda root, action: {"decision": "allow"})
    monkeypatch.setattr(g, "_check_qr_table_coherence",
                        lambda root, action: {"decision": "allow"})
    # (a) nothing edited at all
    clean = _scope_repo(tmp_path, monkeypatch, scope="bmad/scripts/**", probe=None)
    res = g.quality({"tool_name": "terminal",
                     "tool_input": {"command": "git commit -m 'x'"},
                     "cwd": str(clean)})
    assert not any("SCOPE COVERAGE" in w
                   for w in res.get("methodology_warnings", []))
    # (b) an edit inside the approved scope
    covered = _scope_repo(tmp_path / "covered", monkeypatch,
                          scope="bmad/scripts/**", edit_tracked=True)
    res2 = g.quality({"tool_name": "terminal",
                      "tool_input": {"command": "git commit -m 'x'"},
                      "cwd": str(covered)})
    assert not any("SCOPE COVERAGE" in w
                   for w in res2.get("methodology_warnings", []))


def test_scope_coverage_fail_open_on_persistent_oserror(monkeypatch):
    """WinError-6 contract: if every git attempt raises OSError (the pytest
    capture + invalid-handle race on Windows), the mirror fails OPEN with an
    empty miss list — advisory code must never raise or block."""
    import modules.guard as g
    import subprocess as sp
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise OSError(6, "The handle is invalid")

    monkeypatch.setattr(sp, "run", boom)
    assert g._check_scope_coverage(".") == ([], False)
    # The first git command exhausts its 3-attempt budget, then the mirror
    # fails open immediately: it is the same git binary, so hammering the
    # second command cannot help.
    assert calls["n"] == 3


def test_scope_coverage_retry_recovers_from_transient_oserror(tmp_path, monkeypatch):
    """A transient OSError on the first attempt is retried; the next success
    still yields the correct miss list (retry does not swallow results)."""
    import modules.guard as g
    import subprocess as sp

    real_root = _scope_repo(tmp_path, monkeypatch, scope=None)
    state = {"attempts": 0, "first": True}

    def flaky(args, **k):
        if state["first"] and "diff" in args:
            state["first"] = False
            raise OSError(6, "The handle is invalid")

        class Out:
            returncode = 0

            def __init__(self, s):
                self.stdout = s
        state["attempts"] += 1
        if "diff" in args:
            return Out("bmad/scripts/scope_probe_fake.py\n")
        return Out("")  # untracked: file gets added to diff below

    monkeypatch.setattr(sp, "run", flaky)
    misses, ran = g._check_scope_coverage(str(real_root))
    assert ran is True
    assert state["attempts"] >= 1
    # diff already lists the probe (untracked call returns empty); the probe
    # sits outside every approved scope -> recovered run still reports the MISS.
    assert misses == [SCOPE_FIXTURE_PROBE]


def test_scope_miss_posts_replacing_board_alert(tmp_path, monkeypatch):
    """A commit-time miss posts ONE 'scope' alert to the board, replacing any
    previous one (consume-then-post): MAX_ALERTS=32 is a shared pool, and
    repeated uncovered commits must not evict waiting hand-off signals."""
    import modules.guard as g
    import modules.blackboard as bb
    monkeypatch.setattr(g, "_check_gate_records",
                        lambda root, action, include_pr=False: {"decision": "allow"})
    monkeypatch.setattr(g, "_check_all_stories_quality",
                        lambda root, action: {"decision": "allow"})
    monkeypatch.setattr(g, "_check_qr_table_coherence",
                        lambda root, action: {"decision": "allow"})
    # Board in an isolated project dir so the real repo board is untouched.
    proj = tmp_path / "proj"
    proj.mkdir()
    calls = {"consume": 0, "post": 0}
    monkeypatch.setattr(bb, "consume_alerts",
                        lambda root, ch: calls.__setitem__("consume", calls["consume"] + 1) or [])
    monkeypatch.setattr(bb, "post_alert",
                        lambda root, ch, kind, text, **k:
                        calls.__setitem__("post", calls["post"] + 1) or {"ok": True})
    root = _scope_repo(tmp_path, monkeypatch, scope=None)
    res = g.quality({"tool_name": "terminal",
                     "tool_input": {"command": "git commit -m 'x'"},
                     "cwd": str(root)})
    assert res["decision"] == "allow"
    assert calls == {"consume": 1, "post": 1}  # replace semantics held


def test_scope_alert_skipped_when_board_disabled(tmp_path, monkeypatch):
    """blackboard=off turns the alert posting into a no-op; the transcript
    warning still fires (the hook output is the primary surface)."""
    import modules.guard as g
    import modules.config as cfg
    monkeypatch.setattr(g, "_check_gate_records",
                        lambda root, action, include_pr=False: {"decision": "allow"})
    monkeypatch.setattr(g, "_check_all_stories_quality",
                        lambda root, action: {"decision": "allow"})
    monkeypatch.setattr(g, "_check_qr_table_coherence",
                        lambda root, action: {"decision": "allow"})
    monkeypatch.setattr(cfg, "blackboard_enabled", lambda: False)
    posted = []
    import modules.blackboard as bb
    monkeypatch.setattr(bb, "post_alert",
                        lambda *a, **k: posted.append(a) or {"ok": True})
    root = _scope_repo(tmp_path, monkeypatch, scope=None)
    res = g.quality({"tool_name": "terminal",
                     "tool_input": {"command": "git commit -m 'x'"},
                     "cwd": str(root)})
    assert res["decision"] == "allow"
    assert any("SCOPE COVERAGE" in w for w in res.get("methodology_warnings", []))
    assert posted == []  # disabled board: no post, no noise


def test_scope_alert_board_failure_never_blocks_commit(tmp_path, monkeypatch):
    """A raising blackboard module is swallowed: the commit proceeds with the
    transcript warning; board failure must not break the commit path."""
    import modules.guard as g
    monkeypatch.setattr(g, "_check_gate_records",
                        lambda root, action, include_pr=False: {"decision": "allow"})
    monkeypatch.setattr(g, "_check_all_stories_quality",
                        lambda root, action: {"decision": "allow"})
    monkeypatch.setattr(g, "_check_qr_table_coherence",
                        lambda root, action: {"decision": "allow"})
    # Break blackboard_enabled itself — the broad except must catch it.
    import modules.config as cfg
    def boom():
        raise RuntimeError("config explosion")
    monkeypatch.setattr(cfg, "blackboard_enabled", boom)
    root = _scope_repo(tmp_path, monkeypatch, scope=None)
    res = g.quality({"tool_name": "terminal",
                     "tool_input": {"command": "git commit -m 'x'"},
                     "cwd": str(root)})
    assert res["decision"] == "allow"
    assert any("SCOPE COVERAGE" in w for w in res.get("methodology_warnings", []))


def test_session_start_peeks_scope_debt_readonly(tmp_path):
    """session_start surfaces a persisted 'scope' alert as a SCOPE DEBT line
    in additionalContext — announce-only (pending, never consumed)."""
    import modules.audit as au
    import modules.blackboard as bb
    proj = tmp_path / "proj"
    proj.mkdir()
    bb.post_alert(str(proj), "scope", "warn", "SCOPE COVERAGE: x/y.py — outside every approved Code Scope",
                  sender="quality-gate")
    res = au.session_start({"cwd": str(proj)})
    ctx = res["additionalContext"]
    assert "SCOPE DEBT" in ctx
    assert "x/y.py" in ctx
    # Read-only contract: the alert must survive the peek.
    assert len(bb.pending_alerts(str(proj), "scope")) == 1


def test_session_start_no_scope_debt_line_on_clean_board(tmp_path):
    """No 'scope' alerts -> no SCOPE DEBT fragment (the fixed-sentence
    contract for fresh projects stays intact)."""
    import modules.audit as au
    proj = tmp_path / "proj"
    proj.mkdir()
    res = au.session_start({"cwd": str(proj)})
    assert "SCOPE DEBT" not in res["additionalContext"]


def test_clean_commit_clears_scope_debt_alert(tmp_path, monkeypatch):
    """Remediation contract: once the tree verifies clean (git answered, no
    misses), the 'scope' channel is consumed — the debt is cleared. This is
    the ONLY path that clears it besides explicit consume."""
    import modules.guard as g
    import modules.blackboard as bb
    proj = tmp_path / "proj"
    proj.mkdir()
    posted = []
    monkeypatch.setattr(bb, "post_alert",
                        lambda *a, **k: posted.append(a) or {"ok": True})
    consumed = {"n": 0}
    real_consume = bb.consume_alerts
    monkeypatch.setattr(bb, "consume_alerts",
                        lambda root, ch: consumed.__setitem__("n", consumed["n"] + 1)
                        or real_consume(root, ch))
    # A bare (non-git) directory: git exits 129 -> ran=False -> clean-clear
    # must NOT fire (an unverifiable tree is not a clean tree).
    res = g.quality({"tool_name": "terminal",
                     "tool_input": {"command": "git commit -m 'x'"},
                     "cwd": str(proj)})
    assert res["decision"] == "allow"
    assert consumed["n"] == 0  # not ran -> no clear


def test_git_outage_never_clears_scope_debt(tmp_path, monkeypatch):
    """WinError-6 contract, board side: a mirror run that never got git to
    answer (ran=False) must not consume the 'scope' channel — an
    infrastructure failure cannot silently forgive a real coverage gap."""
    import modules.guard as g
    import modules.blackboard as bb
    import subprocess as sp
    monkeypatch.setattr(g, "_check_gate_records",
                        lambda root, action, include_pr=False: {"decision": "allow"})
    monkeypatch.setattr(g, "_check_all_stories_quality",
                        lambda root, action: {"decision": "allow"})
    monkeypatch.setattr(g, "_check_qr_table_coherence",
                        lambda root, action: {"decision": "allow"})
    # The fixture needs real git, so build it BEFORE the outage is simulated.
    root = _scope_repo(tmp_path, monkeypatch, scope=None)

    def boom(*a, **k):
        raise OSError(6, "The handle is invalid")

    monkeypatch.setattr(sp, "run", boom)
    consumed = {"n": 0}
    monkeypatch.setattr(bb, "consume_alerts",
                        lambda root, ch: consumed.__setitem__("n", consumed["n"] + 1) or [])
    res = g.quality({"tool_name": "terminal",
                     "tool_input": {"command": "git commit -m 'x'"},
                     "cwd": str(root)})
    assert res["decision"] == "allow"
    assert consumed["n"] == 0  # outage preserved the debt


def test_guard_directory_target_names_the_directory(tmp_path, monkeypatch):
    """`cp bench.c docs/arge` gates the bare dir: the deny must say it is a
    directory (2026-09-25 LIMX: 36 identical denies, agent never learned)."""
    from modules import config
    from modules.guard import guard
    monkeypatch.setattr(config, "_hook_gate_value", lambda key: "hard")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    (tmp_path / "docs" / "arge").mkdir(parents=True)
    res = guard({"tool_name": "terminal",
                 "tool_input": {"command": "cp bench.c docs/arge"}})
    assert res["decision"] == "deny"
    assert "directory" in res["reason"]


def test_guard_retry_escalation_after_repeated_denies(tmp_path, monkeypatch):
    """3 prior attempts at one target → the 4th deny counts them and names the
    next step (2026-09-25 LIMX: 10 identical gemv_bench.c denies)."""
    from modules import config
    from modules.guard import guard
    monkeypatch.setattr(config, "_hook_gate_value", lambda key: "hard")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    log = tmp_path / ".metodoloji" / "logs" / "hook-audit.log"
    log.parent.mkdir(parents=True)
    line = '{"tool": "terminal", "input": {"command": "cp a scripts/bench/g.c"}}\n'
    log.write_text(line * 3, encoding="utf-8")
    res = guard({"tool_name": "terminal",
                 "tool_input": {"command": "cp a scripts/bench/g.c"}})
    assert res["decision"] == "deny"
    assert "attempt #4" in res["reason"]


def test_guard_secret_deny_names_the_alternative(tmp_path, monkeypatch):
    """`ls ~/.bmad/gate-key` denies — but must point at the digest/verify
    instead of leaving the agent to guess (2026-09-25 LIMX secret block)."""
    from modules.guard import guard
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    res = guard({"tool_name": "terminal",
                 "tool_input": {"command": "ls ~/.bmad/gate-key"}})
    assert res["decision"] == "deny"
    assert "orient digest" in res["reason"] and "never print" in res["reason"]


def test_guard_write_path_creates_no_board_state(tmp_path, monkeypatch):
    """F2 hot-path contract: the per-write guard path (deny or allow) never
    creates or mutates board state in the project root. Board I/O lives only
    at the session edges (session_start/stop) and the commit-time scope
    mirror — between them the write path is board-free by construction."""
    from modules.guard import guard
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    res = guard({"tool_name": "file_editor",
                 "tool_input": {"path": "src/app.py", "content": "print(1)"}})
    assert res["decision"] in ("allow", "deny")
    assert not (tmp_path / ".metodoloji").exists(), \
        "guard() write path created board state in the project root"


# --- staged-instrument allowance (first-experiment deadlock) ------------------
# Under code_guard=hard with zero approved records, a bench under
# scripts/bench/ could never be written — yet the gate refuses free-zone
# benches and --run is the only measurement path (no bench → no measurement
# → no approval → no bench). Benches stage BEFORE approval under a
# well-formed undecided record whose Code Scope covers the bench path;
# product code stays closed until VERIFIED.

_STAGED_RECORD = """## Experiment: E-002 — staged fixture
- **Status:** planned
- **Theory:** fixture theory
- **Hypothesis:** H-002: "fixture_rate >= 0.90"
- **Measurement Metrics:** fixture_rate >= 0.90
- **Experiment Design:** fixture design
- **Code Scope:** scripts/bench/bench_e002_fixture.py
- **Raw Results:** <gate writes>
- **Decision:** <gate writes: APPROVED | REJECTED — reason>
"""


def _staged_project(tmp_path, record_text=_STAGED_RECORD):
    exp = tmp_path / "docs" / "experiments"
    exp.mkdir(parents=True)
    (exp / "E-002.md").write_text(record_text, encoding="utf-8")
    return tmp_path


def test_guard_staged_bench_allowed_under_undecided_record(tmp_path, monkeypatch):
    """Bench write stages pre-approval when the undecided record covers it."""
    from modules.guard import guard
    _staged_project(tmp_path)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    res = guard({"tool_name": "file_editor",
                 "tool_input": {"path": "scripts/bench/bench_e002_fixture.py",
                                "content": "print(1)"}})
    assert res["decision"] == "allow"
    assert any("staged instrument" in w
               for w in res.get("methodology_warnings", []))


def test_guard_product_code_closed_with_only_staged_record(tmp_path, monkeypatch):
    """Staging never unlocks product code — apps/** still needs VERIFIED."""
    from modules.guard import guard
    _staged_project(tmp_path)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    res = guard({"tool_name": "file_editor",
                 "tool_input": {"path": "apps/web/src/app.ts",
                                "content": "export const x = 1;"}})
    assert res["decision"] == "deny"


def test_guard_bench_denied_without_staged_record(tmp_path, monkeypatch):
    """No matching undecided record → bench denied with staging guidance."""
    from modules.guard import guard
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    res = guard({"tool_name": "file_editor",
                 "tool_input": {"path": "scripts/bench/bench_e002_fixture.py",
                                "content": "print(1)"}})
    assert res["decision"] == "deny"
    assert "stage BEFORE approval" in res["reason"]


def test_guard_decided_record_does_not_stage(tmp_path, monkeypatch):
    """A REJECTED record (scope matched) stages nothing — re-open, don't reuse."""
    from modules.guard import guard
    decided = _STAGED_RECORD.replace(
        "- **Decision:** <gate writes: APPROVED | REJECTED — reason>",
        "- **Decision:** REJECTED — H-002: measured=0.10 < threshold=0.9 (gate FAIL)")
    _staged_project(tmp_path, record_text=decided)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    res = guard({"tool_name": "file_editor",
                 "tool_input": {"path": "scripts/bench/bench_e002_fixture.py",
                                "content": "print(1)"}})
    assert res["decision"] == "deny"


# --- secret guard: bare dir listing vs key access -----------------------------
# Real-session false positive: an exploration chain containing `ls .bmad/`
# was denied as a "gate key reference", killing the whole chained command.
# Key presence is already public (orient digest `gate_key:` line) — only
# key CONTENT is secret.

def test_secret_ref_allows_bare_bmad_listing():
    from modules.guard import _secret_ref
    assert not _secret_ref("ls .bmad/ 2>/dev/null")
    assert not _secret_ref("ls ~/.bmad")
    assert not _secret_ref(
        'ls -la && echo "---" && cat CLAUDE.md 2>/dev/null | head -n 100; '
        'echo "==="; ls docs/ 2>/dev/null; ls .bmad/ 2>/dev/null')


# --- record↔story identity: a declaration, never a body mention -----------------
# The same looseness fixed for SP ids lived in the QR/PR lookups: a story key
# ANYWHERE in a record's body counted as that record. The shipped QR-015 named
# 1-1-bench-in-ci only inside a regression note about a moved test path, and that
# mention alone stood in for the story's quality record (2026-10-01 audit).

def _record_covers_project(story_key, record_name, record_body, status="done"):
    td = tempfile.TemporaryDirectory()
    root = Path(td.name)
    (root / "docs/development/stories").mkdir(parents=True)
    (root / "docs/quality").mkdir(parents=True)
    (root / "docs/development/stories" / f"{story_key}.md").write_text(
        f"# Story: {story_key}\nStatus: {status}\n", encoding="utf-8")
    (root / "docs/quality" / record_name).write_text(record_body, encoding="utf-8")
    return td, root


def _make_native_story_project():
    td = tempfile.TemporaryDirectory()
    root = Path(td.name)
    (root / "docs/development/native").mkdir(parents=True)
    (root / "docs/quality").mkdir(parents=True)
    (root / "docs/development/native/1-1-bench-in-ci.md").write_text(
        "# Story 1.1: Consistency bench\n\nStatus: done\n\n"
        "- **Story key:** 1-1-bench-in-ci\n", encoding="utf-8")
    return td, root


def test_qr_body_mention_does_not_count_as_a_quality_record():
    from modules.guard import _find_done_stories_without_qr
    td, _root = _record_covers_project(
        "S-001", "QR-015.md",
        "# Quality Record: QR-015\n\n- Regression: the S-001 fixture path moved.\n")
    try:
        assert _find_done_stories_without_qr(td.name) == ["S-001"]
    finally:
        td.cleanup()


def test_qr_declaration_row_covers_the_story():
    from modules.guard import _find_done_stories_without_qr
    td, _root = _record_covers_project(
        "S-001", "QR-015.md",
        "# Quality Record: QR-015\n\n| Story | S-001 |\n| Status | APPROVED |\n")
    try:
        assert _find_done_stories_without_qr(td.name) == []
    finally:
        td.cleanup()


def test_qr_declaration_accepts_a_story_path():
    from modules.guard import _find_done_stories_without_qr
    td, _root = _record_covers_project(
        "S-060-scope-guard-hermeticity", "QR-016.md",
        "# Quality Record: QR-016\n\n"
        "| Story | docs/development/stories/S-060-scope-guard-hermeticity.md |\n")
    try:
        assert _find_done_stories_without_qr(td.name) == []
    finally:
        td.cleanup()


def _generated_record_project(qr_body):
    """A native draft, its generated methodology record, and one QR record.

    The real shape: the native story stays in `review` (it is the work item) and
    the generated record it mirrors reaches `done` — but every generator writes
    the QR against the NATIVE story (`create-qr-record.py --story <native>`), so
    the done record can only be covered through the `| Native Story |` hop.
    """
    td = tempfile.TemporaryDirectory()
    root = Path(td.name)
    stories = root / "docs/development/stories"
    stories.mkdir(parents=True)
    (root / "docs/quality").mkdir(parents=True)
    (stories / "S-056-gate-id-guard.md").write_text(
        "# Story: S-056 — Gate refuses a record whose Experiment id names no run\n\n"
        "- **Status:** review\n- **Sprint:** SP-002\n", encoding="utf-8")
    (stories / "S-057.md").write_text(
        "# Methodology Record: S-057\n\n"
        "| Field | Value |\n|------|-------|\n"
        "| Status | done |\n"
        "| Native Story | docs/development/stories/S-056-gate-id-guard.md |\n",
        encoding="utf-8")
    (root / "docs/quality/QR-014.md").write_text(qr_body, encoding="utf-8")
    return td, root


def test_generated_record_is_covered_through_its_native_story():
    """A QR that declares S-056 covers its generated record S-057.

    scripts/sync-story-qr.py already resolves a generated record's QR through
    exactly this hop ("a methodology record owns its QR through the native story
    it names"); the gate read a different rule, so the shipped QR-014/015/016
    left S-057/S-059/S-061 looking unrecorded while the reconciler called them
    owned (2026-10-01 dogfood audit).
    """
    from modules.guard import _find_done_stories_without_qr
    td, _root = _generated_record_project(
        "# Quality Record: QR-014\n\n"
        "| Story | docs/development/stories/S-056-gate-id-guard.md |\n"
        "| Status | APPROVED |\n")
    try:
        assert _find_done_stories_without_qr(td.name) == []
    finally:
        td.cleanup()


def test_native_story_hop_does_not_follow_a_body_mention():
    """The hop follows a DECLARATION, never a mention: a QR that merely names
    S-056 in its body must not cover the generated record either."""
    from modules.guard import _find_done_stories_without_qr
    td, _root = _generated_record_project(
        "# Quality Record: QR-014\n\n"
        "- Regression: the S-056-gate-id-guard fixture path moved.\n")
    try:
        assert _find_done_stories_without_qr(td.name) == ["S-057"]
    finally:
        td.cleanup()


def test_native_story_hop_does_not_cover_an_unrelated_record():
    """The hop reaches one story, not every record of that kind."""
    from modules.guard import _find_done_stories_without_qr
    td, _root = _generated_record_project(
        "# Quality Record: QR-014\n\n| Story | S-099 |\n| Status | APPROVED |\n")
    try:
        assert _find_done_stories_without_qr(td.name) == ["S-057"]
    finally:
        td.cleanup()


def test_native_story_hop_agrees_with_the_reconciler():
    """Contract with the shipped reader: the guard's hop and sync-story-qr.py
    must read the same `| Native Story |` row, or the gate and the reconciler
    disagree about who owns a generated record's QR."""
    import re as _re
    root = Path(__file__).resolve().parents[3]
    sync_src = (root / "scripts" / "sync-story-qr.py").read_text(
        encoding="utf-8", errors="replace")
    row_re = _re.search(r'^NATIVE_STORY_RE = re\.compile\(r"([^"]+)"',
                        sync_src, _re.MULTILINE)
    assert row_re, "sync-story-qr.py no longer declares a Native-Story reader"
    row = "| Native Story | docs/development/stories/S-056-gate-id-guard.md |"
    assert _re.search(row_re.group(1), row, _re.MULTILINE)
    from modules.guard import _declared_story_names
    assert "s-056-gate-id-guard" in _declared_story_names(row)


def test_native_story_is_denied_when_the_qr_only_mentions_it():
    from modules.guard import _find_done_stories_without_qr
    td, root = _make_native_story_project()
    try:
        (root / "docs/quality/QR-015.md").write_text(
            "# QR-015\n\n- Regression: the 1-1-bench-in-ci fixture path moved.\n",
            encoding="utf-8")
        assert _find_done_stories_without_qr(td.name) == ["1-1-bench-in-ci"]
    finally:
        td.cleanup()


def test_qr_field_declaration_covers_the_story():
    # The QR template's own form: `- **Story:** [S-id reference, e.g. S-001]`.
    from modules.guard import _find_done_stories_without_qr
    td, _root = _record_covers_project(
        "S-001", "QR-001.md",
        "# Quality Review: QR-001 — Capture box\n\n"
        "- **Date:** 2026-10-01\n- **Status:** APPROVED\n- **Story:** S-001\n")
    try:
        assert _find_done_stories_without_qr(td.name) == []
    finally:
        td.cleanup()


def test_wrong_declaration_does_not_cover_another_story():
    # The record declares S-002; S-001 must not be covered by it.
    from modules.guard import _find_done_stories_without_qr
    td, _root = _record_covers_project(
        "S-001", "QR-002.md",
        "# Quality Record: QR-002\n\n| Story | S-002 |\n| Status | APPROVED |\n")
    try:
        assert _find_done_stories_without_qr(td.name) == ["S-001"]
    finally:
        td.cleanup()


def test_pr_must_declare_its_stories():
    from modules.guard import _find_done_stories_without_pr
    td = tempfile.TemporaryDirectory()
    root = Path(td.name)
    (root / "docs/development/stories").mkdir(parents=True)
    (root / "docs/development/stories/S-001.md").write_text(
        "# Story: S-001\nStatus: done\n", encoding="utf-8")
    pr = root / "docs/development/PR-001.md"
    try:
        pr.write_text("# Production Readiness: PR-001\n\n- Release note: ships S-001\n",
                      encoding="utf-8")
        assert _find_done_stories_without_pr(td.name) == ["S-001"]
        pr.write_text("# Production Readiness: PR-001\n\n- **Stories:** S-001\n",
                      encoding="utf-8")
        assert _find_done_stories_without_pr(td.name) == []
    finally:
        td.cleanup()


def test_declaration_reads_the_sp_template_stories_block():
    """A `- **Stories:**` label owns its indented sub-bullets.

    The SP template lists story keys exactly that way, and the record that lists
    a story is the record that covers it.
    """
    from modules.guard import _declared_story_names
    names = _declared_story_names(
        "# Sprint: SP-001 — capture\n\n"
        "- **Stories:** [S-id list + priority]\n"
        "  - S-054: Blackboard integration completion — Priority: High\n"
        "  - S-055: Methodology record for S-054 (generated) — Priority: Low\n"
        "  - **Total:** [6 story points]\n"
        "- **Capacity:** [points]\n")
    assert {"s-054", "s-055"} <= names
    # The next top-level field ends the block.
    assert "s-056" not in names


def test_qr_generator_row_is_a_declaration_the_gate_accepts():
    """Contract with the shipped writer: its `| Story | … |` row must parse.

    scripts/create-qr-record.py writes the row and scripts/sync-story-qr.py
    trusts it; if either the row's label or the engine's matcher drifts, this
    fails (the loose body match this replaced was what hid such drift).
    """
    from modules.guard import _declared_story_names
    import re as _re
    root = Path(__file__).resolve().parents[3]
    src = (root / "scripts" / "create-qr-record.py").read_text(
        encoding="utf-8", errors="replace")
    row = _re.search(r"\|\s*Story\s*\|\s*\{[^}]+\}\s*\|", src)
    assert row, "create-qr-record.py no longer writes a | Story | row"
    rendered = _re.sub(r"\{[^}]+\}", "1-1-bench-in-ci", row.group(0))
    assert _declared_story_names(rendered) == {"1-1-bench-in-ci"}
    # The sync tool's own reader must agree on the same row.
    sync_src = (root / "scripts" / "sync-story-qr.py").read_text(
        encoding="utf-8", errors="replace")
    qr_story = _re.search(r'^QR_STORY_RE = re\.compile\(r"([^"]+)"', sync_src,
                          _re.MULTILINE)
    assert qr_story, "sync-story-qr.py no longer declares a Story-row reader"
    assert _re.search(qr_story.group(1).replace('\\s', r'\s'), rendered,
                      _re.MULTILINE)


def _load_qr_generator():
    import importlib.util
    root = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location(
        "create_qr_record_e2e", root / "scripts" / "create-qr-record.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_qr_generated_for_a_methodology_record_satisfies_the_gate():
    """E-028: the gate's own remedy must produce a record the gate accepts.

    The gate tells the operator to run
    `create-qr-record.py --story docs/development/stories/S-XXX.md`. The
    generator resolved the story key only from the legacy prose heading
    (`# Story 1.1: Title`), so for a methodology record (`# Methodology
    Record: S-NNN`) it wrote `| Story |  |` — a declaration covering nothing,
    which left the gate denying the story forever. Run the real generator, then
    the real gate.
    """
    qr_gen = _load_qr_generator()
    td = tempfile.TemporaryDirectory()
    root = Path(td.name)
    stories = root / "docs/development/stories"
    stories.mkdir(parents=True)
    (root / "docs/quality").mkdir(parents=True)
    (stories / "S-002.md").write_text(
        "# Methodology Record: S-002\n\n"
        "| Field | Value |\n|------|-------|\n"
        "| Status | done |\n| Story Title | Resolve cyan alias |\n"
        "| Native Story | docs/development/native/1-2-resolve-cyan-accent-alias.md |\n\n"
        "## Definition of Done\n\n"
        "- [DoD-001] Alias removed\n  - Verify: bench\n",
        encoding="utf-8")
    try:
        content = (stories / "S-002.md").read_text(encoding="utf-8")
        meta = qr_gen.extract_story_metadata(content)
        items = qr_gen.extract_dod_items(content)
        assert meta["story_key"] == "S-002", meta
        qr_path = qr_gen.create_qr_record(meta, items, 1, root)
        assert "| Story | S-002 |" in qr_path.read_text(encoding="utf-8")
        from modules.guard import _find_done_stories_without_qr
        assert _find_done_stories_without_qr(td.name) == []
    finally:
        td.cleanup()


def test_qr_generated_for_a_template_story_covers_it():
    """E-028: a story written from the shipped template is covered too.

    The template heading is `# Story: S-XXX — Title`; the old generator's
    pattern matched none of it, so the same empty-declaration dead-end hit
    every real story.
    """
    qr_gen = _load_qr_generator()
    td = tempfile.TemporaryDirectory()
    root = Path(td.name)
    stories = root / "docs/development/stories"
    stories.mkdir(parents=True)
    (root / "docs/quality").mkdir(parents=True)
    (stories / "S-003.md").write_text(
        "# Story: S-003 — Template story\n\nStatus: done\n\n"
        "- [DoD-001] Done\n  - Verify: y\n", encoding="utf-8")
    try:
        content = (stories / "S-003.md").read_text(encoding="utf-8")
        meta = qr_gen.extract_story_metadata(content)
        assert meta["story_key"] == "S-003", meta
        qr_gen.create_qr_record(meta, qr_gen.extract_dod_items(content), 1, root)
        from modules.guard import _find_done_stories_without_qr
        assert _find_done_stories_without_qr(td.name) == []
    finally:
        td.cleanup()


def test_secret_ref_denies_key_access_and_dir_consumers():
    from modules.guard import _secret_ref
    assert _secret_ref("ls ~/.bmad/gate-key")
    assert _secret_ref("cat ~/.bmad/gate-key")
    assert _secret_ref("cat ~/.bmad/*")
    assert _secret_ref("cp -r ~/.bmad /tmp/x")
    assert _secret_ref("grep -r token ~/.bmad/")
    assert _secret_ref("echo $(cat ~/.bmad/gate-key)")
    assert _secret_ref("echo $BMAD_GATE_KEY")
    assert _secret_ref("cd ~/.bmad && ls")

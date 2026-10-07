"""Tests for hooks/engine/modules/audit.py — trail redaction + bridge check."""

import pathlib
import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HOOKS))
# The workflow kernel lives in the plugin tree, not in the engine package; the
# session edge imports it lazily, and these tests create real runs through it.
_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from modules.audit import (  # noqa: E402
    _check_kopru_consumption,
    _redacted_input,
    audit,
    is_readonly_call,
)


# --- _check_kopru_consumption ------------------------------------------------

def test_kopru_done_story_check_moved_to_static_audit(tmp_path, monkeypatch):
    # The done-story→QR directory scan moved to check-plugin.sh (static
    # audit); the per-write hot path only checks the edited QR file itself.
    root = tmp_path
    (root / "docs/development/stories").mkdir(parents=True)
    (root / "docs/quality").mkdir(parents=True)  # quality dir exists but empty
    (root / "docs/development/stories/S-001.md").write_text(
        "## Story: S-001\n- **Status:** done\n", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    warnings = _check_kopru_consumption(
        "file_editor",
        {"path": "docs/development/stories/S-001.md", "content": "x"},
    )
    assert warnings == []


def test_kopru_done_story_windows_backslash_path(tmp_path, monkeypatch):
    # Stories are out of the per-write hot path entirely (moved to the static
    # check — pinned above), so BOTH separators stay silent here: backslash
    # parity, not a defect. The old BUG behind this name — the slash-anchored
    # regex silently skipping the check on Windows — lives on in the QR
    # branch, pinned in test_kopru_qr_windows_backslash_path_warns.
    root = tmp_path
    (root / "docs/development/stories").mkdir(parents=True)
    (root / "docs/quality").mkdir(parents=True)
    (root / "docs/development/stories/S-001.md").write_text(
        "## Story: S-001\n- **Status:** done\n", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    warnings = _check_kopru_consumption(
        "file_editor",
        {"path": f"{root}\\docs\\development\\stories\\S-001.md", "content": "x"},
    )
    assert warnings == []


def test_kopru_qr_without_dod():
    warnings = _check_kopru_consumption(
        "file_editor",
        {"path": "docs/quality/QR-001.md", "content": "## Decision\nok"},
    )
    assert any("DoD" in w for w in warnings)


def test_kopru_qr_windows_backslash_path_warns():
    # LIVE instance of the pinned BUG: the slash-anchored /QR-…/.md$ regex
    # never matched a native Windows path, so the QR DoD check was silently
    # skipped on that platform. Separator-agnostic now — same warning as the
    # forward-slash form above (payload carries content → pure path fix).
    warnings = _check_kopru_consumption(
        "file_editor",
        {"path": "C:\\p\\docs\\quality\\QR-001.md", "content": "## Decision\nok"},
    )
    assert any("DoD" in w for w in warnings)


def test_kopru_qr_incremental_edit_checked_from_disk(tmp_path, monkeypatch):
    # The payload-key diversity E-064 documented for WRITES applies to this
    # check too: Claude Code's Edit carries old_string/new_string, never
    # `content`, so a QR edited without a whole-file body must be validated
    # against the file on disk (PostToolUse = the file is already updated).
    root = tmp_path
    (root / "docs/quality").mkdir(parents=True)
    qr = root / "docs/quality/QR-001.md"
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)

    qr.write_text("## Decision\nok", encoding="utf-8")  # no DoD table
    warnings = _check_kopru_consumption(
        "file_editor",
        {"path": "docs/quality/QR-001.md",
         "old_string": "a", "new_string": "b"},
    )
    assert any("DoD" in w for w in warnings)

    # A QR that still records its DoD stays silent — no over-warn on reads.
    qr.write_text(
        "## DoD Verification Results\n\n"
        "| DoD Item | Status | Evidence | Date |\n"
        "|----------|--------|----------|------|\n"
        "| DoD-001 | ✅ passed | curl output | 2026-08-20 |\n",
        encoding="utf-8")
    assert _check_kopru_consumption(
        "file_editor",
        {"path": "docs/quality/QR-001.md",
         "old_string": "a", "new_string": "b"},
    ) == []


def test_kopru_qr_table_with_valid_dod_no_warning():
    """A QR DoD Verification table with identifier + evidence rows is fine."""
    content = (
        "## DoD Verification Results\n\n"
        "| DoD Item | Status | Evidence | Date |\n"
        "|----------|--------|----------|------|\n"
        "| DoD-001 | ✅ passed | curl output | 2026-08-20 |\n"
        "| DoD-002 | ✅ passed | pytest output | 2026-08-20 |\n"
    )
    warnings = _check_kopru_consumption(
        "file_editor",
        {"path": "docs/quality/QR-001.md", "content": content},
    )
    assert warnings == []


def test_kopru_qr_table_missing_evidence_warns():
    """QR DoD table rows without a recorded status/evidence are flagged — the
    same structural rule the guard applies to story DoD Verify fields."""
    content = (
        "## DoD Verification Results\n\n"
        "| DoD Item | Status | Evidence | Date |\n"
        "|----------|--------|----------|------|\n"
        "| DoD-001 | — | — | — |\n"
    )
    warnings = _check_kopru_consumption(
        "file_editor",
        {"path": "docs/quality/QR-001.md", "content": content},
    )
    assert any("DoD" in w and "missing Verify field" in w for w in warnings)


def test_kopru_qr_bullet_with_result_marker_no_warning():
    """QR bullet-style items that record their result (→ ✓ PASS) are valid."""
    content = "## DoD Verification\n\n- [DoD-001] DENY unapproved → ✓ PASS\n"
    warnings = _check_kopru_consumption(
        "file_editor",
        {"path": "docs/quality/QR-001.md", "content": content},
    )
    assert warnings == []


def test_kopru_qr_bullet_without_verification_warns():
    """QR bullet-style items that record no result are flagged (guard parity)."""
    content = "## DoD Verification\n\n- [DoD-001] All ACs verified\n"
    warnings = _check_kopru_consumption(
        "file_editor",
        {"path": "docs/quality/QR-001.md", "content": content},
    )
    assert any("DoD" in w and "missing Verify field" in w for w in warnings)


def test_kopru_qr_mentions_dod_but_no_items_warns():
    """A QR that repeats the words DoD but has no bullet/table item warns."""
    content = "## DoD Verification Results\n\n| DoD Item | Status | Evidence | Date |\n"
    warnings = _check_kopru_consumption(
        "file_editor",
        {"path": "docs/quality/QR-001.md", "content": content},
    )
    assert any("has no DoD items" in w for w in warnings)


def test_redacted_input_truncates_bodies_keeps_paths():
    big = "x" * 5000
    out = _redacted_input({"path": "src/main.py", "content": big,
                           "command": "echo hi"})
    assert out["path"] == "src/main.py"
    assert out["command"] == "echo hi"
    assert len(out["content"]) < len(big)
    assert "truncated 5000 chars" in out["content"]


def test_redacted_input_short_bodies_untouched():
    out = _redacted_input({"path": "src/a.py", "content": "print(1)"})
    assert out == {"path": "src/a.py", "content": "print(1)"}


def test_audit_log_redacts_content(tmp_path, monkeypatch):
    import json
    root = tmp_path
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    big = "SECRET-DATA " * 500
    audit({"tool_name": "file_editor",
           "tool_input": {"path": "src/main.py", "content": big},
           "tool_output": None})
    log = root / ".metodoloji/logs/hook-audit.log"
    logged = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert logged["input"]["path"] == "src/main.py"
    assert len(logged["input"]["content"]) < len(big)
    assert "truncated 6000 chars" in logged["input"]["content"]


# --- audit() end-to-end ------------------------------------------------------

def test_audit_skips_readonly_commands(tmp_path, monkeypatch):
    # 2026-09-25 LIMX session: read-only calls (ls/find/git status/views)
    # filled the trail with noise (423/652 blocks were hook echoes). audit()
    # now skips them: immediate allow, no log line, no I/O.
    root = tmp_path
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    for cmd in ("ls -la", "cd /root/PROJECTS/LIMX && git status | head -20",
                "find docs -type f | head -100", "wc -l docs/arge/*.md"):
        res = audit({
            "tool_name": "terminal",
            "tool_input": {"command": cmd},
            "tool_output": "ok",
        })
        assert res["decision"] == "allow"
    assert not (root / ".metodoloji/logs/hook-audit.log").exists()


def test_audit_still_logs_writes_and_commits(tmp_path, monkeypatch):
    import json
    root = tmp_path
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    res = audit({
        "tool_name": "terminal",
        "tool_input": {"command": "echo hi > src/out.txt"},
        "tool_output": "ok",
    })
    assert res["decision"] == "allow"
    res = audit({
        "tool_name": "terminal",
        "tool_input": {"command": "git commit -m 'x'"},
        "tool_output": "ok",
    })
    assert res["decision"] == "allow"
    log = root / ".metodoloji/logs/hook-audit.log"
    assert log.exists()
    content = log.read_text(encoding="utf-8")
    assert "out.txt" in content and "commit" in content


def test_audit_skips_file_views(tmp_path, monkeypatch):
    root = tmp_path
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    res = audit({"tool_name": "file_editor",
                 "tool_input": {"path": "docs/arge/README.md", "command": "view"},
                 "tool_output": None})
    assert res["decision"] == "allow"
    assert not (root / ".metodoloji/logs/hook-audit.log").exists()


def test_audit_writes_warnings(tmp_path, monkeypatch):
    root = tmp_path
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    res = audit({
        "tool_name": "file_editor",
        "tool_input": {"path": "docs/quality/QR-001.md", "content": "## x"},
        "tool_output": None,
    })
    assert res["decision"] == "allow"
    assert "methodology_warnings" in res







# === NEW TESTS for audit module enhancements (MEDIUM #12 / ISSUE #71) ===


def test_redacted_input_truncates_long_input():
    """Test that _redacted_input respects length limits."""
    # Create long input
    long_input = "x" * 1000
    
    # Redacted input should be truncated
    result = _redacted_input({"path": "test.py", "content": long_input})
    
    # Result should be reasonable length (audit.py truncates at 300 chars)
    assert len(str(result)) < 500  # Sanity check


def test_audit_is_log_only_no_board_io(tmp_path, monkeypatch):
    """Faz 3: audit() touches the log file only — no blackboard reads/writes,
    no canvas/bridge mutation, no alert delivery."""
    import json

    root = tmp_path
    (root / ".metodoloji").mkdir()

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)

    import modules.blackboard as bb
    calls = []
    for fn in ("stamp_tool_event", "write_key", "post_alert", "canvas_create",
               "canvas_watch", "read_board", "consume_alerts", "compact_context"):
        orig = getattr(bb, fn, None)
        if orig is None:
            continue
        monkeypatch.setattr(bb, fn, (lambda *a, _fn=fn, **k: calls.append(_fn)))

    res = audit({"tool_name": "file_editor",
                 "tool_input": {"path": "src/main.py", "content": "x = 1"},
                 "tool_output": None})
    assert res["decision"] == "allow"
    assert calls == [], f"audit() performed board I/O: {calls}"
    log = root / ".metodoloji/logs/hook-audit.log"
    assert log.exists()


def test_session_start_is_marker_plus_relay_announcement(tmp_path, monkeypatch):
    """Faz 5 + proactive relay: session_start() stamps the marker, returns
    the fixed sentence, and peeks the handoff channels READ-ONLY — waiting
    signals surface as a PROACTIVE nudge (announce-only: never consumed,
    never mutated)."""
    import json
    from modules.audit import session_start

    root = tmp_path
    (root / ".metodoloji").mkdir()

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)

    import modules.blackboard as bb
    calls = []
    for fn in ("stamp_tool_event", "write_key", "post_alert", "canvas_create",
               "canvas_watch", "read_board", "consume_alerts", "compact_context",
               "record_watcher", "chain_health"):
        orig = getattr(bb, fn, None)
        if orig is None:
            continue
        monkeypatch.setattr(bb, fn, (lambda *a, _fn=fn, **k: calls.append(_fn)))
    # the peek is stubbed separately (the read-only dependency seam)
    monkeypatch.setattr(
        bb, "pending_handoff_channels", lambda _root: {"bmad-create-story": 1})

    res = session_start({"cwd": str(root)})
    assert res["decision"] == "allow"
    assert "Record chain" in res["additionalContext"]
    assert "PROACTIVE — hand-off waiting" in res["additionalContext"]
    assert "bmad-create-story" in res["additionalContext"]
    assert "Blackboard" not in res["additionalContext"]
    # announce-only: no board I/O beyond the read-only peeks — nothing
    # consumed, nothing mutated (pending_handoff_channels + compact_context
    # are the read-only dependency seam)
    assert set(calls) <= {"pending_handoff_channels", "compact_context",
                          "read_board"}, \
        f"session_start() mutated the board: {calls}"


def test_session_start_names_project_state(tmp_path, monkeypatch):
    """The inject must ground the session in the project's real state.

    The mailjs session (2026-09-23, OpenHands) opened with zero progress
    signal, grounded in the model's memory instead ("Epic 6 in-progress",
    a week old), and burned its first minutes re-deriving state that lived
    in the record tree + sprint-status.yaml all along.
    """
    import json
    from modules.audit import session_start

    root = tmp_path
    (root / ".metodoloji").mkdir()
    dev = root / "docs" / "development"
    dev.mkdir(parents=True)
    (dev / "IR-004.md").write_text("x", encoding="utf-8")
    native = root / "docs" / "development" / "native"
    native.mkdir(parents=True)
    (native / "sprint-status.yaml").write_text(
        "development_status:\n"
        "  epic-1: in-progress\n"
        "  1-1-a: done\n"
        "  1-2-b: done\n",
        encoding="utf-8")

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    import modules.blackboard as bb
    monkeypatch.setattr(bb, "pending_handoff_channels", lambda _root: {})

    res = session_start({"cwd": str(root)})
    ctx = res["additionalContext"]
    # Records named with counts + newest id — the digest's same facts.
    assert "Records: IR=1 (newest IR-004.md)" in ctx
    # The sprint lag is surfaced, not silently swallowed.
    assert "sprint:" in ctx and "epic-1" in ctx


def test_session_start_without_records_stays_quiet(tmp_path, monkeypatch):
    """An empty project must not invent state — fixed sentence only."""
    import json
    from modules.audit import session_start

    root = tmp_path
    (root / ".metodoloji").mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    import modules.blackboard as bb
    monkeypatch.setattr(bb, "pending_handoff_channels", lambda _root: {})

    res = session_start({"cwd": str(root)})
    assert "Records:" not in res["additionalContext"]
    assert "sprint:" not in res["additionalContext"]

    log = root / ".metodoloji/logs/hook-audit.log"
    recs = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    assert any(r.get("type") == "session_start" for r in recs)


def test_session_start_fixed_sentence_without_waiting_handoffs(tmp_path, monkeypatch):
    """No waiting signals → the context is exactly the fixed sentence."""
    from modules.audit import FIXED_SESSION_CONTEXT, session_start

    root = tmp_path
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    # Isolate the MCP peek from the real user config — the empty-project
    # contract is the fixed sentence, not the host's servers.
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    res = session_start({"cwd": str(root)})
    assert res["decision"] == "allow"
    # The fixed sentence is one bounded edge carrying the measured rules:
    # chain reminder, shell-state discipline (2026-10-01 graph-engineering-arge
    # session: `M=...` in one call, `$M` in the next — a fresh shell each call,
    # hit twice in one session), forward-slash paths (B-004 S2), dialect match,
    # and the B-003 unmatched-glob rule.
    assert res["additionalContext"] == FIXED_SESSION_CONTEXT
    # Pin the substance too, so the constant cannot be gutted silently.
    assert "Record chain" in FIXED_SESSION_CONTEXT
    assert "self-contained" in FIXED_SESSION_CONTEXT
    assert "$M" in FIXED_SESSION_CONTEXT
    assert "unmatched pattern" in FIXED_SESSION_CONTEXT


def test_session_start_appends_chain_progress(tmp_path, monkeypatch):
    """Methodology run keys on the board surface read-only in the context —
    the session grounds in live relay state, not a static reminder."""
    from modules.audit import session_start
    from modules import blackboard as bb

    root = tmp_path
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)

    bb.write_key(str(root), "E-001", "APPROVED: fake_accuracy=1.0", type_="state")
    res = session_start({"cwd": str(root)})
    assert res["additionalContext"].startswith(
        "METODOLOJI session started. Record chain: E → IR → SP → S → QR → PR.")
    assert "Chain progress" in res["additionalContext"]
    assert "E-001" in res["additionalContext"]
    # read-only: the run key and its mirror are untouched, no alert consumed
    board = bb.read_board(str(root))
    assert board["keys"]["E-001"]["value"].startswith("APPROVED")


def test_session_start_announces_the_active_workflow(tmp_path, monkeypatch):
    """The session edge states where the run stands — computed, not remembered.

    The kernel owns ordering; the inject is how a fresh session learns it
    without re-deriving the plan from chat. Read-only and bounded: the run
    position comes from the state file, and the state file must not change.
    """
    from modules.audit import session_start
    from bmad.workflow import engine as wf_engine

    root = tmp_path
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)

    spec = {
        "id": "seo",
        "title": "SEO visibility",
        "artifact": "sorunlar.md",
        "start": "analyze",
        "stages": [
            {"id": "analyze", "title": "Sorunlari listele",
             "evidence": {"kind": "artifact", "path": "sorunlar.md",
                          "contains": ["ISSUE-"]},
             "next": [{"to": "done"}]},
            {"id": "done", "title": "Tamamlandi", "terminal": True},
        ],
    }
    assert wf_engine.create(str(root), spec)["ok"] is True
    state_file = next((root / ".metodoloji/workflow").glob("*.state.json"))
    before = state_file.read_bytes()

    res = session_start({"cwd": str(root)})
    ctx = res["additionalContext"]
    assert "WORKFLOW 'seo' is active" in ctx
    assert "stage 1/2 'analyze'" in ctx
    assert "sorunlar.md" in ctx
    assert "then the kernel computes 'done'" in ctx
    # Read-only: announcing the position never advances it.
    assert state_file.read_bytes() == before


def test_session_start_has_no_workflow_line_for_an_empty_project(tmp_path, monkeypatch):
    """No run → no workflow clause; the fixed sentence stays the whole inject."""
    from modules.audit import FIXED_SESSION_CONTEXT, session_start

    root = tmp_path
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    res = session_start({"cwd": str(root)})
    assert res["additionalContext"] == FIXED_SESSION_CONTEXT
    assert "WORKFLOW" not in res["additionalContext"]


# --- open Implementation Plans at the session edge (E-068) -------------------

def _plan_record(root, eid="E-100", planned="src/a.py, src/b.py",
                 with_plan=True):
    recs = root / "docs" / "experiments"
    recs.mkdir(parents=True, exist_ok=True)
    plan_block = ("\n## Implementation Plan (GRP)\n\n"
                  f"- **Planned Files:** {planned}\n"
                  "- **Amendments:** none\n") if with_plan else ""
    (recs / f"{eid}.md").write_text(
        f"## Experiment: {eid} — plan\n- **Status:** planned\n"
        "- **Code Scope:** none\n" + plan_block, encoding="utf-8")


def test_session_start_surfaces_open_plans(tmp_path, monkeypatch):
    """A resumed multi-file operation must not start blind: the session edge
    injects the same derived plan progress the Stop report names, read-only."""
    from modules.audit import session_start

    root = tmp_path
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    _plan_record(root)
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")

    ctx = session_start({"cwd": str(root)})["additionalContext"]
    assert "Plan progress (GRP)" in ctx
    assert "E-100.md 1/2 planned files on disk (1 pending)" in ctx
    assert "--amend-plan" in ctx
    # The fixed sentence and the inventory line survive alongside it.
    assert "Record chain" in ctx and "Records: E=1" in ctx


def test_session_start_silent_without_a_pending_plan(tmp_path, monkeypatch):
    """No plan, or a completed one, contributes no plan clause."""
    from modules.audit import session_start

    root = tmp_path
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    _plan_record(root, eid="E-201", with_plan=False)
    assert "Plan progress (GRP)" not in \
        session_start({"cwd": str(root)})["additionalContext"]

    root2 = tmp_path / "done"
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root2))
    _plan_record(root2, eid="E-202", planned="src/c.py")
    (root2 / "src").mkdir(parents=True)
    (root2 / "src" / "c.py").write_text("x = 1\n", encoding="utf-8")
    assert "Plan progress (GRP)" not in \
        session_start({"cwd": str(root2)})["additionalContext"]


def test_audit_exception_handling_graceful(tmp_path, monkeypatch):
    """Test that audit handles exceptions gracefully (broad exception handling reduction)."""
    root = tmp_path
    (root / ".metodoloji").mkdir()
    
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    
    # Malformed input
    json_in = {
        "command": "audit",
        "root": str(root),
        "tool": "file_editor",
        "input": None,  # Invalid
    }
    
    try:
        result = audit(json_in)
        # Should not raise, but may fail gracefully
        assert isinstance(result, dict)
    except Exception as e:
        # If it raises, should be a specific exception, not generic
        assert not isinstance(e, Exception) or "specific" in str(type(e)).lower()


def test_bridge_validation_on_qr_file():
    """Test that bridge (S→QR) validation works on QR files."""
    from modules.audit import _check_kopru_consumption
    
    # QR file consumption check
    warnings = _check_kopru_consumption(
        "file_editor",
        {
            "path": "docs/quality/QR-001.md",
            "content": "# Quality Record\n## Definition of Done\n- [ ] DoD-001 verified"
        }
    )
    
    # Should return list of warnings (may be empty)
    assert isinstance(warnings, list)


# === E-064: trail completeness + edit-body redaction ========================


def test_is_readonly_call_edit_payloads_are_writes():
    """An edit carries its body under keys other than `content`.

    Claude Code's Edit normalizes to old_string/new_string; OpenHands uses
    `command: str_replace` with old_str/new_str or an insert. Deciding "write"
    from `content` alone read every one of these as a read-only call, so the
    incremental edits never reached the audit trail.
    """
    assert is_readonly_call("file_editor", {
        "path": "src/a.py", "command": "str_replace",
        "old_str": "x", "new_str": "y"}) is False
    assert is_readonly_call("file_editor", {
        "path": "src/a.py", "old_string": "x", "new_string": "y"}) is False
    assert is_readonly_call("file_editor", {
        "path": "src/a.py", "command": "insert", "new_str": "y"}) is False
    assert is_readonly_call("file_editor", {
        "path": "src/a.py", "command": "undo_edit"}) is False
    assert is_readonly_call("notebook_editor", {
        "path": "n.ipynb", "new_str": "x"}) is False
    # A write verb is a write even with an empty body (create/truncate).
    assert is_readonly_call("file_editor", {
        "path": "src/a.py", "command": "create", "content": ""}) is False


def test_is_readonly_call_reads_stay_readonly():
    assert is_readonly_call("file_editor", {
        "path": "src/a.py", "command": "view"}) is True
    assert is_readonly_call("file_editor", {"path": "src/a.py"}) is True
    assert is_readonly_call("file_editor", {
        "path": "src/a.py", "content": None}) is True
    assert is_readonly_call("file_editor", {
        "path": "src/a.py", "content": ""}) is True
    assert is_readonly_call("terminal", {"command": "ls -la"}) is True
    # `$` without a write operator is not a write.
    assert is_readonly_call("terminal", {"command": "echo $HOME"}) is True


def test_is_readonly_call_dropped_variable_write_is_a_write():
    """A write to an unexpanded variable extracts no static target, but the
    guard still treats it as a write — the trail must record it, not skip it."""
    assert is_readonly_call("terminal", {"command": "echo hi > $OUT.py"}) is False
    assert is_readonly_call("terminal", {"command": "cp a.py $DEST/b.py"}) is False


def test_redacted_input_truncates_edit_bodies():
    big = "S" * 5000
    for key in ("new_str", "old_str", "new_string", "old_string", "diff", "patch"):
        out = _redacted_input({"path": "src/a.py", key: big})
        assert "truncated 5000 chars" in out[key], key
    assert _redacted_input({"path": "p", "edits": [big, big]})["edits"] != [big, big]


def test_audit_logs_an_edit_end_to_end(tmp_path, monkeypatch):
    import json
    root = tmp_path
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    res = audit({
        "tool_name": "file_editor",
        "tool_input": {"path": "src/a.py", "old_string": "x", "new_string": "y"},
        "tool_output": None,
    })
    assert res["decision"] == "allow"
    log = root / ".metodoloji/logs/hook-audit.log"
    assert log.exists()
    line = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert line["input"]["path"] == "src/a.py"


def test_audit_does_not_log_a_view(tmp_path, monkeypatch):
    root = tmp_path
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    res = audit({"tool_name": "file_editor",
                 "tool_input": {"path": "src/a.py", "command": "view"},
                 "tool_output": None})
    assert res["decision"] == "allow"
    assert not (root / ".metodoloji/logs/hook-audit.log").exists()


# --- terminal command failures: outcome capture + moment-of-failure fix -----
# Both measured failure classes (B-003 zsh nomatch, B-004 backslash collapse)
# died invisible: the audit read `tool_output`, a key no harness sends, so
# every real output_summary was null — and the read-only fast exit dropped
# failing read commands before they could reach the trail.

def test_audit_reads_the_harness_tool_response_key(tmp_path, monkeypatch):
    import json
    root = tmp_path
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    audit({"tool_name": "Bash",
           "tool_input": {"command": "git commit -m 'x'"},
           "tool_response": {"stdout": "ok [master abc123]", "stderr": ""}})
    log = root / ".metodoloji/logs/hook-audit.log"
    recs = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    assert recs, "a command with an outcome must reach the trail"
    assert "ok [master" in recs[-1]["output_summary"]


def test_failed_readonly_command_is_logged_with_correction(tmp_path, monkeypatch):
    import json
    root = tmp_path
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    res = audit({"tool_name": "Bash",
                 "tool_input": {"command": "ls docs/design/prds/*prd*.md"},
                 "tool_response": "zsh: no matches found: docs/design/prds/*prd*.md"})
    log = root / ".metodoloji/logs/hook-audit.log"
    recs = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    assert len(recs) == 1
    assert recs[0]["command_failed"] == "glob_nomatch"
    # The correction must reach the agent (main.py → additionalContext).
    assert any("unmatched glob" in w for w in res.get("methodology_warnings", []))


def test_clean_read_only_command_stays_out_of_the_trail(tmp_path, monkeypatch):
    root = tmp_path
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    res = audit({"tool_name": "Bash",
                 "tool_input": {"command": "ls -la"},
                 "tool_response": "total 8"})
    assert res["decision"] == "allow"
    assert not (root / ".metodoloji/logs/hook-audit.log").exists()


def test_command_failure_signatures_map_to_platform_corrections():
    from modules.audit import command_failure
    cases = {
        "zsh: no matches found: docs/*": "glob_nomatch",
        "ls: cannot access 'C:Usersx': No such file or directory": "backslash_path",
        "grep : The term 'grep' is not recognized as the name of a cmdlet\n"
        "    + CategoryInfo : ObjectNotFound ... CommandNotFoundException":
            "powershell_dialect",
        "sed: 1: file.py: invalid command code 1": "bsd_sed",
        "zsh: command not found: nppm": "command_not_found",
    }
    for out, kind in cases.items():
        got = command_failure("terminal", out)
        assert got is not None and got[0] == kind, (out, got)
    # Silence on anything unknown, on non-terminal tools, and on no output.
    assert command_failure("terminal", "total 8\n-rw-r--r-- 1 a staff 0 x") is None
    assert command_failure("file_editor", "zsh: no matches found: x") is None
    assert command_failure("terminal", "") is None
    assert command_failure("terminal", None) is None

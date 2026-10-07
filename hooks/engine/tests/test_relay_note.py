"""Relay-note tests — the hook layer re-announces waiting downstream batons.

The 2026-09-23 mailjs session posted dev-story's code-review hand-off and
forgot it one turn later: skills post batons via `mirror --to`, but between
the session edges (stop/session_start) nothing re-announced them mid-session.
The fix ships one bounded note on the every-turn path (guard/pre) through
PostToolUse. These tests falsify every claim in that contract.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules import blackboard as bb  # noqa: E402
from modules import mirror as mir  # noqa: E402
from modules import guard as guard_mod  # noqa: E402

MAIN_PY = Path(__file__).resolve().parent.parent / "main.py"


@pytest.fixture()
def root():
    with tempfile.TemporaryDirectory() as td:
        yield td


def _seed(root, pairs, sender="bmad-dev-story"):
    for skill, key, note in pairs:
        bb.post_handoff(root, skill, key, note=note, sender=sender)


# --- blackboard.recent_handoffs -------------------------------------------------
def test_recent_handoffs_relay_only_freshest_first(root):
    _seed(root, [
        ("bmad-dev-story", "story.1-2", "story ready-for-dev"),
        ("bmad-code-review", "story.1-1", "dev complete — story in review"),
        ("random-skill-not-in-relay", "x-1", "off-relay noise"),
    ])
    time.sleep(0.02)  # make the second post strictly newer
    hits = bb.recent_handoffs(root, limit=3)
    assert [h["skill"] for h in hits] == ["bmad-code-review", "bmad-dev-story"]
    assert all(set(h) == {"skill", "from", "note", "age_s"} for h in hits)
    assert all(isinstance(h["age_s"], int) and h["age_s"] >= 0 for h in hits)
    assert hits[0]["from"] == "story.1-1"


def test_recent_handoffs_limit_is_clamped_not_deadly(root):
    _seed(root, [("bmad-dev-story", "story.1-1", "n1")])
    assert bb.recent_handoffs(root, limit=0)          # 0 → floor 1, not crash
    assert len(bb.recent_handoffs(root, limit=99)) == 1  # ceiling 10
    assert bb.recent_handoffs(root, limit=1)          # exact


def test_recent_handoffs_empty_board(root):
    assert bb.recent_handoffs(root) == []


# --- mirror.pending_note ---------------------------------------------------------
def test_pending_note_announces_freshest_baton(root):
    _seed(root, [("bmad-code-review", "story.1-1", "dev complete — story in review")])
    note = mir.pending_note(root)
    assert note and "PROACTIVE relay" in note
    assert "bmad-code-review" in note and "story.1-1" in note
    assert "handoffs --skill" in note  # the consumer action is named


def test_pending_note_throttles_identical_repeat(root):
    _seed(root, [("bmad-dev-story", "story.1-2", "story ready-for-dev")])
    assert mir.pending_note(root)
    assert mir.pending_note(root) is None  # same table within the window


def test_pending_note_reannounces_when_table_changes(root):
    _seed(root, [("bmad-code-review", "story.1-1", "dev complete")])
    assert mir.pending_note(root)
    bb.consume_alerts(root, "handoff.bmad-code-review")
    bb.post_handoff(root, "bmad-code-review", "story.1-3",
                    note="dev complete again", sender="bmad-dev-story")
    note = mir.pending_note(root)
    assert note and "story.1-3" in note


def test_pending_note_targeted_to_addressed_skill(root):
    _seed(root, [("bmad-dev-story", "story.1-2", "story ready-for-dev")])
    note = mir.pending_note(root, to="bmad-dev-story")
    assert note and "addressed to you (bmad-dev-story)" in note
    other = mir.pending_note(root, to="bmad-code-review")
    assert other is None  # nothing addressed there


def test_pending_note_disabled_and_empty_paths(root, monkeypatch):
    assert mir.pending_note(root) is None  # empty board → no note
    assert mir.pending_note(None) is None
    monkeypatch.setenv("METODOLOJI_NO_BLACKBOARD", "1")
    _seed(root, [("bmad-dev-story", "story.1-2", "x")])
    assert mir.pending_note(root) is None  # kill switch honored


# --- guard/pre ships the note ------------------------------------------------------
def test_pre_ships_relay_note_as_warning(root):
    _seed(root, [("bmad-dev-story", "story.1-2", "story ready-for-dev")])
    res = guard_mod.pre({"tool_name": "terminal",
                         "tool_input": {"command": "echo hi"},
                         "cwd": str(root)})
    assert res["decision"] == "allow"  # announce-only, never blocks
    warns = res.get("methodology_warnings") or []
    assert any("PROACTIVE relay" in w and "bmad-dev-story" in w for w in warns)


def test_pre_second_identical_turn_is_throttled(root):
    _seed(root, [("bmad-dev-story", "story.1-2", "story ready-for-dev")])
    payload = {"tool_name": "terminal",
               "tool_input": {"command": "echo hi"}, "cwd": str(root)}
    first = guard_mod.pre(dict(payload))
    second = guard_mod.pre(dict(payload))
    assert any("PROACTIVE relay" in w for w in first.get("methodology_warnings") or [])
    assert not any("PROACTIVE relay" in w for w in second.get("methodology_warnings") or [])


def test_pre_clean_board_carries_no_relay_warning(root):
    res = guard_mod.pre({"tool_name": "terminal",
                         "tool_input": {"command": "echo hi"},
                         "cwd": str(root)})
    warns = res.get("methodology_warnings") or []
    assert not any("PROACTIVE relay" in w for w in warns)


# --- main.py e2e: the note reaches the model via PostToolUse -----------------------
def test_main_pre_surfaces_relay_note(root):
    _seed(root, [("bmad-dev-story", "story.1-2", "story ready-for-dev")])
    env = {**os.environ, "OPENHANDS_PROJECT_DIR": str(root)}
    payload = {"tool_name": "terminal", "tool_input": {"command": "echo hi"}}
    r = subprocess.run([sys.executable, str(MAIN_PY), "pre"],
                       input=json.dumps(payload), capture_output=True,
                       text=True, env=env, timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    ctx = out["hookSpecificOutput"]["additionalContext"] or ""
    assert "PROACTIVE relay" in ctx and "bmad-dev-story" in ctx


def test_pending_note_throttle_ignores_age_roll(root):
    """E-048 contract: the throttle keys on the TABLE STATE, not the rendered
    text. The note embeds '(Nm ago)' stamps that roll forward every minute, so
    a note-text comparison re-announced an unchanged relay at every minute
    boundary. The digest strips age stamps — an unchanged table stays silent
    even when the rendered age drifts."""
    _seed(root, [("bmad-code-review", "story.1-1", "dev complete")])
    first = mir.pending_note(root)
    assert first and "0m ago" in first
    # Age rolls 0m -> 1m (snapshot surgery: backdate past the first minute
    # boundary); the table itself is unchanged. Under the OLD per-note-text
    # throttle this re-announced every minute boundary; the digest keys on
    # the table identity, so the rolled rendering is SILENCED.
    paths = bb.board_paths(root)
    raw = json.loads(Path(paths["snapshot"]).read_text(encoding="utf-8"))
    for al in raw.get("alerts", []):
        if al.get("kind") == "handoff":
            al["ts"] = time.time() - 119
    Path(paths["snapshot"]).write_text(json.dumps(raw), encoding="utf-8")
    assert mir.pending_note(root) is None  # age roll alone never re-announces
    # A REAL change still re-announces instantly (different digest).
    bb.post_handoff(root, "bmad-ux", "prd.v9", note="ux next")
    again = mir.pending_note(root)
    assert again is not None and "bmad-ux" in again


def test_pending_note_state_file_is_digest_keyed(root):
    """The state file carries {digest, ts} — the table identity, not the
    rendered text — and a foreign (old note-keyed) state is treated as empty."""
    _seed(root, [("bmad-code-review", "story.1-1", "dev complete")])
    assert mir.pending_note(root)
    state_path = Path(bb.board_paths(root)["dir"]) / "relay-note.state"
    st = json.loads(state_path.read_text(encoding="utf-8"))
    assert "digest" in st and "note" not in st
    # Old-format state (pre-E-048) is foreign: the next call announces once
    # and re-keys the file.
    state_path.write_text(json.dumps({"note": "old", "ts": time.time()}),
                          encoding="utf-8")
    assert mir.pending_note(root) is not None
    st2 = json.loads(state_path.read_text(encoding="utf-8"))
    assert "digest" in st2

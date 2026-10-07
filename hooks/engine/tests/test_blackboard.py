"""Blackboard tests — full CLI/core combination matrix.

Covers: write/read/tag/untag/contribute/hot/stats, caps and expiry,
event-sourced snapshot rebuild, atomicity under concurrent writers,
unicode content, fail-open behavior (missing dir, corrupt JSON),
and the config gate (custom/config.toml [hooks] blackboard).
"""

import concurrent.futures
import json
import os
import pathlib
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules import blackboard as bb  # noqa: E402

CLI = Path(__file__).resolve().parent.parent.parent.parent / "bmad" / "scripts" / "blackboard.py"


@pytest.fixture()
def root():
    with tempfile.TemporaryDirectory() as td:
        yield td


# --- basic lifecycle -----------------------------------------------------------
def test_write_and_read_key(root):
    ack = bb.write_key(root, "prd.acme", "v1", type_="state")
    assert ack["ok"] is True and ack["key"] == "prd.acme" and ack["hot"] is False
    assert ack["root"] == os.path.abspath(root)  # ack names the board it hit (R4)
    board = bb.read_board(root)
    assert board["keys"]["prd.acme"]["value"] == "v1"
    assert board["keys"]["prd.acme"]["type"] == "state"


def test_write_overwrites_and_keeps_single_entry(root):
    bb.write_key(root, "k", "a")
    bb.write_key(root, "k", "b")
    board = bb.read_board(root)
    assert len(board["keys"]) == 1
    assert board["keys"]["k"]["value"] == "b"


def test_empty_key_rejected(root):
    ack = bb.write_key(root, "  ", "v")
    assert ack["ok"] is False
    assert "error" in ack


def test_value_length_capped(root):
    ack = bb.write_key(root, "k", "x" * (bb.MAX_VALUE_LEN + 100))
    assert ack["ok"] is True
    board = bb.read_board(root)
    assert len(board["keys"]["k"]["value"]) == bb.MAX_VALUE_LEN


def test_hot_set_and_clear(root):
    bb.write_key(root, "a", "1")
    ack = bb.set_hot(root, "a")
    assert ack == {"ok": True, "hot": "a"}
    assert bb.read_board(root)["hot"] == "a"
    bb.set_hot(root, None)
    assert bb.read_board(root)["hot"] is None


def test_only_one_hot_key(root):
    bb.write_key(root, "a", "1", hot=True)
    bb.write_key(root, "b", "2", hot=True)
    assert bb.read_board(root)["hot"] == "b"


def test_write_non_hot_on_hot_key_reports_dirty(root):
    bb.write_key(root, "a", "1", hot=True)
    ack = bb.write_key(root, "a", "2")
    assert ack.get("dirty_notice")


def test_tag_add_remove_and_duplicate(root):
    assert bb.add_tag(root, "crm") == {"ok": True, "tag": "crm"}
    assert bb.add_tag(root, "crm")["duplicate"] is True
    assert bb.remove_tag(root, "crm") == {"ok": True, "tag": "crm"}
    assert bb.remove_tag(root, "crm")["absent"] is True


def test_untag_missing_tag_is_ok(root):
    assert bb.remove_tag(root, "nope")["ok"] is True


def test_contribute_and_order(root):
    bb.add_contribution(root, "a", "did a")
    bb.add_contribution(root, "b", "did b")
    board = bb.read_board(root)
    assert [c["who"] for c in board["contributions"]] == ["a", "b"]


def test_contribute_requires_who(root):
    assert bb.add_contribution(root, " ", "x")["ok"] is False


def test_compact_context_shape(root):
    bb.write_key(root, "prd.acme", "v", type_="state", hot=True)
    bb.add_tag(root, "t")
    bb.add_contribution(root, "w", "what")
    bb.write_key(root, "scope", "src/auth", type_="state")
    bb.write_key(root, "status", "active", type_="state")
    ctx = bb.compact_context(root)
    assert ctx["hot"] == "prd.acme"
    assert ctx["hot_meta"]["type"] == "state"
    assert ctx["tags"] == ["t"]
    assert ctx["contributions"][-1]["who"] == "w"
    assert ctx["key_count"] == 3
    assert ctx["focus"] == {"scope": "src/auth", "status": "active"}
    assert "keys" not in ctx  # bounded contract: never dumps all values
    assert "intent" not in ctx  # removed mirror: scope/status only


def test_write_run_key_mirrors_methodology_chain_progress(root):
    # The close-out contract (write the stage's run key) mirrors
    # methodology.last_* — the injected context must surface them in
    # E→IR→SP→S→QR→PR order so a session sees how far the relay progressed.
    bb.write_key(root, "E-001", "APPROVED: m=1.0")
    bb.write_key(root, "S-003", "done — QR queued")
    ctx = bb.compact_context(root)
    assert list(ctx["methodology"].items()) == [
        ("E", "E-001: APPROVED: m=1.0"), ("S", "S-003: done — QR queued")]
    # the mirror is event-sourced: the snapshot carries it, replay reproduces it
    board = bb.read_board(root)
    assert board["keys"]["methodology.last_story"]["value"].startswith("S-003")
    # Missing/corrupt board → empty dict, never a missing key or crash
    ctx2 = bb.compact_context(os.path.join(root, "never-created"))
    assert ctx2["methodology"] == {}


def test_write_mirror_not_hot_and_no_recurse(root):
    # A run-key write mirrors exactly once: the mirror value itself carries
    # the run-key prefix, so recursion must be structurally impossible
    # (the mirror rides _apply_event, never write_key), must never steal
    # the hot focus, and the ack stays the caller's key.
    ack = bb.write_key(root, "E-001", "APPROVED", hot=True)
    assert ack["key"] == "E-001"
    board = bb.read_board(root)
    assert board["hot"] == "E-001"
    assert board["keys"]["methodology.last_experiment"]["value"] == "E-001: APPROVED"
    events = [json.loads(l) for l in open(
        bb.board_paths(root)["events"], encoding="utf-8")]
    writes = [e for e in events if e.get("event") == "write"]
    assert sorted(w["key"] for w in writes) == [
        "E-001", "methodology.last_experiment"]


def test_write_mirror_never_double_stamps_the_run_key(root):
    """A value that already opens with the run key keeps a single identity.

    The IR close-out writes `IR-2026-09-28 READY — …` (its value *is* the verdict
    line), so the relay stamp used to produce
    `IR-2026-09-28: IR-2026-09-28 READY — …` — the digest's chain line then showed
    the run key twice, which reads like two runs. The stamp adds the identity only
    when the value is missing it.
    """
    bb.write_key(root, "IR-2026-09-28", "IR-2026-09-28 READY — E-058 approved")
    bb.write_key(root, "E-059", "APPROVED: measured=1.0")
    board = bb.read_board(root)
    assert board["keys"]["methodology.last_ir"]["value"] == \
        "IR-2026-09-28 READY — E-058 approved"
    # the ordinary shape still gets its identity stamped
    assert board["keys"]["methodology.last_experiment"]["value"] == \
        "E-059: APPROVED: measured=1.0"


def test_hot_write_corrects_a_finished_bridge_status(root):
    # Real session 2026-09-21: the previous run closed with status=complete,
    # then the UX run went hot without touching status — bmad-help read
    # "work is done" mid-discovery. Opening a run (hot key) must stop the
    # finished status from claiming the new run is finished.
    bb.write_key(root, "status", "complete", type_="state")
    ack = bb.write_key(root, "ux.mailjs", "discovery started", hot=True)
    assert ack["status"] == "in-progress"
    assert bb.compact_context(root)["focus"]["status"] == "in-progress"


def test_hot_write_never_invents_a_status_claim(root):
    # Unset status is not a claim: a run opening on a fresh board stays
    # untouched (an explicit --key status write is the only claim), and an
    # in-progress status is left exactly as the run wrote it.
    bb.write_key(root, "ux.mailjs", "start", hot=True)
    assert "status" not in bb.read_board(root)["keys"]
    bb.write_key(root, "status", "in-progress", type_="state")
    bb.write_key(root, "ux.mailjs", "more", hot=True)
    assert bb.read_board(root)["keys"]["status"]["value"] == "in-progress"


def test_doctor_flags_hot_run_with_finished_status(root):
    bb.write_key(root, "status", "complete", type_="state")
    bb.write_key(root, "ux.mailjs", "start")
    bb.set_hot(root, "ux.mailjs")  # hot without a write: the pre-fix path
    doc = bb.doctor(root)
    assert any("is hot" in w for w in doc["warnings"])
    assert doc["checks"]["focus"]["status"] == "warn"
    # A run that marks itself in-progress clears the finding.
    bb.write_key(root, "ux.mailjs", "still going", hot=True)
    doc2 = bb.doctor(root)
    assert not any("is hot" in w for w in doc2["warnings"])
    assert doc2["checks"]["focus"]["status"] == "ok"


def test_write_non_run_key_leaves_mirror_untouched(root):
    # Only methodology run keys mirror; an ordinary key write emits exactly
    # one event (no surprise mirror), and the chain context stays empty.
    bb.write_key(root, "status", "complete")
    ctx = bb.compact_context(root)
    assert ctx["methodology"] == {}
    events = [json.loads(l) for l in open(
        bb.board_paths(root)["events"], encoding="utf-8")]
    assert len(events) == 1


def test_bridge_keys_survive_key_cap(root):
    bb.write_key(root, "scope", "src/auth", type_="state")
    bb.write_key(root, "status", "active", type_="state")
    for i in range(bb.MAX_KEYS + 5):
        bb.write_key(root, f"fill{i:03d}", "x")
    board = bb.read_board(root)
    assert len(board["keys"]) == bb.MAX_KEYS
    assert "scope" in board["keys"] and "status" in board["keys"]


def test_ghost_bridge_keys_are_gone(root):
    """bridge.last_* were ghost keys: declared in _BRIDGE_KEYS, read nowhere,
    written nowhere, zero event-log matches. The protection entries are
    removed — nothing may resurrect them (a real feature re-adds both sides
    of its contract, reader and writer)."""
    bb.write_key(root, "bridge.last_story", "S-001.md", type_="state")
    for i in range(bb.MAX_KEYS + 5):
        bb.write_key(root, f"fill{i:03d}", "x")
    board = bb.read_board(root)
    assert len(board["keys"]) == bb.MAX_KEYS
    assert "bridge.last_story" not in board["keys"]
    for ghost in ("bridge.last_story", "bridge.last_qr",
                  "bridge.last_pr", "bridge.last_ir"):
        assert ghost not in bb._BRIDGE_KEYS


def test_methodology_mirror_keys_survive_key_cap(root):
    """The mirror keys are live state (write_key stamps them from every
    methodology run-key write; compact_context reads them in chain order) —
    a flooded board must never evict the chain position."""
    for prefix in ("E-", "IR-", "SP-", "S-", "QR-", "PR-"):
        bb.write_key(root, f"{prefix}001", "done")
    for i in range(bb.MAX_KEYS + 5):
        bb.write_key(root, f"fill{i:03d}", "x")
    board = bb.read_board(root)
    assert len(board["keys"]) == bb.MAX_KEYS
    for _, mirror in bb._METHODOLOGY_MIRROR_KEYS:
        assert mirror in board["keys"]


def test_stats(root):
    bb.write_key(root, "k", "v")
    s = bb.stats(root)
    assert s["keys"] == 1 and s["events"] >= 1 and s["hot"] is None


# --- caps & expiry -------------------------------------------------------------
def test_keys_cap_expires_oldest(root):
    for i in range(bb.MAX_KEYS + 10):
        bb.write_key(root, f"k{i:03d}", str(i))
    board = bb.read_board(root)
    assert len(board["keys"]) == bb.MAX_KEYS
    assert "k000" not in board["keys"]  # oldest expired
    assert f"k{bb.MAX_KEYS + 9:03d}" in board["keys"]


def test_keys_cap_expires_hot_never_dangles(root):
    bb.write_key(root, "hold", "x")
    for i in range(bb.MAX_KEYS):
        bb.write_key(root, f"fill{i:03d}", "x")
    # force 'hold' to be oldest + hot
    board = bb.read_board(root)
    bb.set_hot(root, "hold")
    bb.write_key(root, "zzz-newest", "x")
    # expire everything by flooding past cap
    for i in range(bb.MAX_KEYS + 5):
        bb.write_key(root, f"flood{i:03d}", "x", type_="flood")
    board = bb.read_board(root)
    if "hold" not in board["keys"]:
        assert board["hot"] != "hold"  # never dangles


def test_tags_cap(root):
    for i in range(bb.MAX_TAGS + 5):
        bb.add_tag(root, f"tag{i:03d}")
    assert len(bb.read_board(root)["tags"]) == bb.MAX_TAGS


def test_contributions_cap(root):
    for i in range(bb.MAX_CONTRIBUTIONS + 5):
        bb.add_contribution(root, f"w{i:03d}", "x")
    assert len(bb.read_board(root)["contributions"]) == bb.MAX_CONTRIBUTIONS


def test_events_log_capped(root):
    for i in range(60):
        bb.write_key(root, "same.key", f"v{i}")
    s = bb.stats(root)
    assert s["events"] <= bb.MAX_EVENTS


# --- event sourcing & rebuild --------------------------------------------------
def test_snapshot_rebuild_from_events(root):
    bb.write_key(root, "k", "v")
    bb.add_tag(root, "t")
    bb.add_contribution(root, "w", "x")
    bb.set_hot(root, "k")
    os.remove(bb.board_paths(root)["snapshot"])
    board = bb.read_board(root)  # triggers rebuild
    assert board["keys"]["k"]["value"] == "v"
    assert board["hot"] == "k"
    assert board["tags"] == ["t"]
    assert board["contributions"][0]["who"] == "w"
    assert os.path.exists(bb.board_paths(root)["snapshot"])


def test_corrupt_snapshot_rebuilds(root):
    bb.write_key(root, "k", "v")
    p = bb.board_paths(root)["snapshot"]
    Path(p).write_text("{corrupt json", encoding="utf-8")
    board = bb.read_board(root)
    assert board["keys"]["k"]["value"] == "v"


def test_event_log_survives_garbage_lines(root):
    ev = bb.board_paths(root)["events"]
    bb.write_key(root, "k", "v")
    with open(ev, "a", encoding="utf-8") as f:
        f.write("GARBAGE LINE\n\n")
    bb.write_key(root, "k2", "v2")
    board = bb.read_board(root)
    assert "k" in board["keys"] and "k2" in board["keys"]


def test_no_snapshot_write_when_no_mutation_on_read(root):
    bb.write_key(root, "k", "v")
    snap = bb.board_paths(root)["snapshot"]
    mtime1 = os.path.getmtime(snap)
    bb.read_board(root)
    mtime2 = os.path.getmtime(snap)
    assert mtime1 == mtime2  # reads do not rewrite the snapshot


# --- atomicity & concurrency ---------------------------------------------------
def test_concurrent_writers_all_persist(root):
    def write_chunk(n):
        for i in range(5):
            bb.write_key(root, f"w{n}.k{i}", f"v{n}-{i}")
        return n

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(write_chunk, range(6)))
    board = bb.read_board(root)
    assert len(board["keys"]) == 30


def test_snapshot_never_left_truncated(root):
    # Simulate a crash by killing mid-write is hard in-process; instead verify
    # every path a partial writer could leave behind is cleaned or ignored.
    bb.write_key(root, "k", "v")
    paths = bb.board_paths(root)
    Path(paths["snapshot"] + ".tmp").write_text("junk", encoding="utf-8")
    board = bb.read_board(root)  # tmp ignored
    assert board["keys"]["k"]["value"] == "v"


# --- fail-open -----------------------------------------------------------------
def test_missing_dir_returns_empty_board(root):
    board = bb.read_board(os.path.join(root, "no", "such", "dir"))
    assert board["keys"] == {} and board["hot"] is None


def test_compact_context_on_empty_project(root):
    ctx = bb.compact_context(os.path.join(root, "never-created"))
    assert ctx["hot"] is None and ctx["tags"] == [] and ctx["key_count"] == 0


def test_stats_on_missing_project(root):
    s = bb.stats(os.path.join(root, "never-created"))
    assert s["events"] == 0 and s["keys"] == 0


# --- unicode -------------------------------------------------------------------
def test_unicode_roundtrip(root):
    bb.write_key(root, "prd.ürün", "AMAÇ: Türkçe karakterler ✓ 中文")
    bb.add_contribution(root, "yazar", "taslak yazdı — karar: ✓")
    board = bb.read_board(root)
    assert board["keys"]["prd.ürün"]["value"].startswith("AMAÇ")
    assert board["contributions"][0]["what"].endswith("✓")


# --- CLI integration -----------------------------------------------------------
def _run_cli(root, *args):
    import subprocess
    return subprocess.run(
        [sys.executable, str(CLI), *args, "--project-root", root],
        capture_output=True, text=True, timeout=60,
        stdin=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def test_cli_full_lifecycle(tmp_path):
    r = _run_cli(str(tmp_path), "write", "--key", "prd.x", "--value", "V",
                 "--type", "state", "--hot")
    assert json.loads(r.stdout)["ok"] is True
    r = _run_cli(str(tmp_path), "tag", "--tag", "t")
    assert json.loads(r.stdout)["ok"] is True
    r = _run_cli(str(tmp_path), "contribute", "--who", "skill", "--what", "did")
    assert json.loads(r.stdout)["ok"] is True
    r = _run_cli(str(tmp_path), "read", "--context")
    ctx = json.loads(r.stdout)
    assert ctx["hot"] == "prd.x" and ctx["tags"] == ["t"] and ctx["key_count"] == 1
    r = _run_cli(str(tmp_path), "read", "--key", "prd.x")
    assert json.loads(r.stdout)["value"] == "V"
    r = _run_cli(str(tmp_path), "hot", "--clear")
    assert json.loads(r.stdout)["hot"] is None
    r = _run_cli(str(tmp_path), "untag", "--tag", "t")
    assert json.loads(r.stdout)["ok"] is True
    r = _run_cli(str(tmp_path), "stats")
    assert json.loads(r.stdout)["keys"] == 1


def test_cli_read_missing_key_ok(tmp_path):
    r = _run_cli(str(tmp_path), "read", "--key", "absent.key")
    out = json.loads(r.stdout)
    assert out["ok"] is True and out["value"] is None
    assert r.returncode == 0


def test_cli_empty_project_exit_zero(tmp_path):
    r = _run_cli(str(tmp_path), "read", "--context")
    assert r.returncode == 0
    assert json.loads(r.stdout)["hot"] is None


def test_cli_write_then_write_hot_dirty_notice(tmp_path):
    _run_cli(str(tmp_path), "write", "--key", "a", "--value", "1", "--hot")
    r = _run_cli(str(tmp_path), "write", "--key", "a", "--value", "2")
    assert "dirty_notice" in json.loads(r.stdout)


def test_cli_hot_requires_key_or_clear(tmp_path):
    r = _run_cli(str(tmp_path), "hot")
    assert r.returncode == 1
    assert json.loads(r.stdout)["ok"] is False


# --- engine integration ---------------------------------------------------------
def _engine(tmp_monkeypatch, root):
    """Import engine modules with the project root pinned."""
    import importlib
    tmp_monkeypatch.setenv("CLAUDE_PROJECT_DIR", root)
    audit_mod = importlib.import_module("modules.audit")
    stop_mod = importlib.import_module("modules.stop")
    config_mod = importlib.import_module("modules.config")
    return audit_mod, stop_mod, config_mod


# Faz 3/5: engine hot-path is log-only — it never reads/writes the board.
# Board state stays untouched by session_start()/audit(); the skill-side CLI
# (blackboard.py) owns all graph tracking. These tests pin the separation.
def test_session_start_leaves_board_untouched(tmp_path, monkeypatch):
    audit_mod, _, _ = _engine(monkeypatch, str(tmp_path))
    # Isolate the session-start MCP peek from the real user config — the host's
    # servers must not leak into this exact-sentence contract.
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    bb.write_key(str(tmp_path), "prd.acme", "v1", hot=True)
    bb.add_tag(str(tmp_path), "crm")
    before = bb.read_board(str(tmp_path))
    out = audit_mod.session_start({"cwd": str(tmp_path)})
    # The sentence lives as one exported constant (audit.FIXED_SESSION_CONTEXT):
    # the chain reminder plus the shell-state discipline clause.
    assert out["additionalContext"] == audit_mod.FIXED_SESSION_CONTEXT
    after = bb.read_board(str(tmp_path))
    assert after["keys"] == before["keys"]
    assert after["tags"] == before["tags"]
    assert after["watchers"] == before["watchers"]


def test_session_start_stamps_marker_only(tmp_path, monkeypatch):
    import json
    audit_mod, _, _ = _engine(monkeypatch, str(tmp_path))
    out = audit_mod.session_start({"cwd": str(tmp_path)})
    assert "Blackboard" not in out["additionalContext"]
    board = bb.read_board(str(tmp_path))
    assert "session_start" not in board["watchers"]  # no watcher stamp anymore
    log = tmp_path / ".metodoloji" / "logs" / "hook-audit.log"
    recs = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    assert any(r.get("type") == "session_start" for r in recs)


def test_audit_leaves_board_untouched(tmp_path, monkeypatch):
    audit_mod, _, _ = _engine(monkeypatch, str(tmp_path))
    audit_mod.audit({"cwd": str(tmp_path), "hook_event_name": "PostToolUse",
                     "tool_name": "Write",
                     "tool_input": {"file_path": "docs/a.md", "content": "x"}})
    board = bb.read_board(str(tmp_path))
    assert "last_tool.file_editor" not in board["keys"]


def test_audit_stamp_gated_off(tmp_path, monkeypatch):
    audit_mod, _, config_mod = _engine(monkeypatch, str(tmp_path))
    monkeypatch.setattr(config_mod, "blackboard_enabled", lambda: False)
    audit_mod.audit({"cwd": str(tmp_path), "hook_event_name": "PostToolUse",
                     "tool_name": "Write",
                     "tool_input": {"file_path": "docs/a.md", "content": "x"}})
    board = bb.read_board(str(tmp_path))
    assert "last_tool.file_editor" not in board["keys"]


def test_forget_key_removes_and_survives_rebuild(root):
    """forget_key retires a key, the removal survives a snapshot rebuild
    (event-sourced), and protected bridge keys / the hot focus are refused."""
    bb.write_key(root, "story.old-run", "x")
    out = bb.forget_key(root, "story.old-run")
    assert out["ok"] is True and out["removed"] is True
    assert "story.old-run" not in bb.read_board(root)["keys"]
    # event-sourced: a rebuild reproduces the removal (no drift)
    os.remove(bb.board_paths(root)["snapshot"])
    assert "story.old-run" not in bb.read_board(root)["keys"]
    # protected bridge key and the hot focus are refused
    assert bb.forget_key(root, "status")["ok"] is False
    bb.write_key(root, "story.hot", "y", hot=True)
    assert bb.forget_key(root, "story.hot")["ok"] is False


def test_stamp_tool_event_survives_snapshot_rebuild(tmp_path, monkeypatch):
    # Event-sourced: last_tool.* returns from replay and survives a deleted
    # snapshot (regression guard for the old snapshot-only fold).
    import importlib.util, sys as _sys
    _engine(monkeypatch, str(tmp_path))
    bb.stamp_tool_event(str(tmp_path), "file_editor", "docs/a.md")
    board = bb.read_board(str(tmp_path))
    assert board["keys"]["last_tool.file_editor"]["value"] == "docs/a.md"
    os.remove(bb.board_paths(str(tmp_path))["snapshot"])  # force rebuild
    board2 = bb.read_board(str(tmp_path))
    assert board2["keys"]["last_tool.file_editor"]["value"] == "docs/a.md"


def test_stamp_tool_event_no_tool_ok(tmp_path):
    out = bb.stamp_tool_event(str(tmp_path), "", "x")
    assert out["ok"] is True


def test_watch_matches_normalizes_rel_abs(tmp_path):
    assert bb._watch_matches("docs/", "docs/a.md") is True
    assert bb._watch_matches("docs", "docs/a.md") is True
    assert bb._watch_matches("docs/", "skills/x.md") is False
    assert bb._watch_matches("./docs/", "docs/a.md") is True
    assert bb._watch_matches("docs/", "docs\\a.md") is True  # backslash equalized


def test_stop_report_names_story_without_board_notice(tmp_path, monkeypatch):
    # Faz 4: stop reports the in-progress story but never reads the board —
    # hot keys, canvases and alerts are skill-CLI concerns.
    audit_mod, stop_mod, config_mod = _engine(monkeypatch, str(tmp_path))
    monkeypatch.setattr(config_mod, "hook_gate_mode", lambda key: "hard")
    # seed: in-progress story (fresh sprint-status) + hot board key
    sprint = tmp_path / "bmad-output" / "implementation-artifacts"
    sprint.mkdir(parents=True)
    (sprint / "sprint-status.yaml").write_text(
        "stories:\n  1-1-alpha: in-progress\n", encoding="utf-8")
    bb.write_key(str(tmp_path), "story.1-1-alpha", "wip", hot=True)
    out = stop_mod.stop({"cwd": str(tmp_path), "hook_event_name": "Stop"})
    assert out["decision"] == "allow"
    assert "1-1-alpha" in out.get("reason", "")
    assert "story.1-1-alpha" not in out.get("reason", "")  # no board notice


def test_stop_allow_has_no_board_notice(tmp_path, monkeypatch):
    audit_mod, stop_mod, _ = _engine(monkeypatch, str(tmp_path))
    out = stop_mod.stop({"cwd": str(tmp_path), "hook_event_name": "Stop"})
    assert out["decision"] == "allow"


def test_engine_smoke_no_memlog_regression(tmp_path, monkeypatch):
    """The removed systems stay removed under the new integration."""
    audit_mod, stop_mod, _ = _engine(monkeypatch, str(tmp_path))
    out = audit_mod.session_start({"cwd": str(tmp_path)})
    assert "memlog" not in out["additionalContext"].lower()
    assert "intent" not in out and "scope" not in out


# ==============================================================================
# v2 — lists
# ==============================================================================
def test_list_add_creates_and_appends(root):
    assert bb.list_add(root, "todos", "first")["count"] == 1
    ack = bb.list_add(root, "todos", "second")
    assert ack["count"] == 2
    board = bb.read_board(root)
    assert board["keys"]["todos"]["value"] == ["first", "second"]
    assert board["keys"]["todos"]["type"] == "list"


def test_list_remove_by_item_and_index(root):
    for it in ("a", "b", "c"):
        bb.list_add(root, "L", it)
    assert bb.list_remove(root, "L", item="b")["count"] == 2
    assert bb.list_remove(root, "L", index=0)["count"] == 1
    assert bb.read_board(root)["keys"]["L"]["value"] == ["c"]


def test_list_remove_missing_ok(root):
    bb.list_add(root, "L", "a")
    assert bb.list_remove(root, "L", item="zz")["removed"] == 0
    assert bb.list_remove(root, "absent", item="a")["ok"] is True


def test_list_remove_requires_selector(root):
    assert bb.list_remove(root, "L")["ok"] is False


def test_list_items_capped(root):
    for i in range(bb.MAX_LIST_ITEMS + 5):
        bb.list_add(root, "L", f"i{i:03d}")
    val = bb.read_board(root)["keys"]["L"]["value"]
    assert len(val) == bb.MAX_LIST_ITEMS
    assert val[0] == "i005"  # oldest expired


def test_list_empty_key_or_item_rejected(root):
    assert bb.list_add(root, " ", "x")["ok"] is False
    assert bb.list_add(root, "k", " ")["ok"] is False


def test_list_clear_empties_and_keeps_key(root):
    for it in ("a", "b", "c"):
        bb.list_add(root, "L", it)
    ack = bb.list_clear(root, "L")
    assert ack == {"ok": True, "key": "L", "cleared": 3}
    board = bb.read_board(root)
    assert board["keys"]["L"]["value"] == []
    assert board["keys"]["L"]["type"] == "list"


def test_list_clear_reusable_after(root):
    bb.list_add(root, "L", "a")
    bb.list_clear(root, "L")
    assert bb.list_add(root, "L", "b")["count"] == 1


def test_list_clear_missing_or_text_key_ok(root):
    assert bb.list_clear(root, "absent")["cleared"] == 0
    bb.write_key(root, "t", "text")
    assert bb.list_clear(root, "t")["cleared"] == 0  # text key untouched


def test_list_clear_survives_rebuild(root):
    bb.list_add(root, "L", "a")
    bb.list_clear(root, "L")
    os.remove(bb.board_paths(root)["snapshot"])
    assert bb.read_board(root)["keys"]["L"]["value"] == []


def test_cli_list_clear(tmp_path):
    _run_cli(str(tmp_path), "list-add", "--key", "t", "--item", "x")
    r = _run_cli(str(tmp_path), "list-clear", "--key", "t")
    assert json.loads(r.stdout)["cleared"] == 1
    r = _run_cli(str(tmp_path), "read", "--key", "t")
    assert json.loads(r.stdout)["value"] == []


def test_write_over_list_key_replaces_with_text(root):
    bb.list_add(root, "k", "a")
    bb.write_key(root, "k", "text now")
    board = bb.read_board(root)
    assert board["keys"]["k"]["value"] == "text now"


def test_list_survives_snapshot_rebuild(root):
    bb.list_add(root, "L", "one")
    bb.list_add(root, "L", "two")
    os.remove(bb.board_paths(root)["snapshot"])
    board = bb.read_board(root)
    assert board["keys"]["L"]["value"] == ["one", "two"]


def test_cli_list_roundtrip(tmp_path):
    _run_cli(str(tmp_path), "list-add", "--key", "t", "--item", "x1")
    _run_cli(str(tmp_path), "list-add", "--key", "t", "--item", "x2")
    r = _run_cli(str(tmp_path), "list-remove", "--key", "t", "--index", "0")
    assert json.loads(r.stdout)["count"] == 1
    r = _run_cli(str(tmp_path), "read", "--key", "t")
    assert json.loads(r.stdout)["value"] == ["x2"]


# ==============================================================================
# v2 — canvases (dynamic surfaces)
# ==============================================================================
def test_canvas_create_free_and_grid(root):
    a = bb.canvas_create(root, "free-one")
    assert a["ok"] and a["grid"] is None
    b = bb.canvas_create(root, "grid-one", grid="8x4")
    assert b["grid"] == [8, 4]
    assert set(bb.read_board(root)["canvases"]) == {"free-one", "grid-one"}


def test_canvas_create_duplicate_is_idempotent(root):
    bb.canvas_create(root, "c")
    ack = bb.canvas_create(root, "c", grid="3x3")
    assert ack["existed"] is True
    assert len(bb.read_board(root)["canvases"]) == 1


def test_canvas_set_and_read(root):
    bb.canvas_create(root, "map")
    ack = bb.canvas_set(root, "map", "A1", "nucleus", kind="decision", x=0, y=0)
    assert ack["cells"] == 1
    cv = bb.read_canvas(root, "map")
    cell = cv["cells"]["A1"]
    assert cell["content"] == "nucleus" and cell["kind"] == "decision"
    assert cell["x"] == 0 and cell["y"] == 0


def test_canvas_set_missing_canvas_is_noop(root):
    assert bb.canvas_set(root, "ghost", "A1", "x")["ok"] is True
    assert bb.read_board(root)["canvases"] == {}


def test_canvas_requires_name_and_cell(root):
    assert bb.canvas_set(root, "", "A1", "x")["ok"] is False
    assert bb.canvas_set(root, "c", "", "x")["ok"] is False


def test_canvas_cell_cap_expires_oldest(root):
    bb.canvas_create(root, "c")
    for i in range(bb.MAX_CELLS + 5):
        bb.canvas_set(root, "c", f"cell{i:04d}", str(i))
    cells = bb.read_canvas(root, "c")["cells"]
    assert len(cells) == bb.MAX_CELLS
    assert "cell0000" not in cells  # oldest expired


def test_canvas_auto_cells_capped_separately(root):
    bb.canvas_create(root, "c")
    bb.canvas_watch(root, "c", "docs/")
    for i in range(bb.MAX_AUTO_CELLS + 5):
        ack = bb.stamp_tool_event(root, "file_editor", f"docs/f{i}.md")
        assert ack["ok"]
    cv = bb.read_canvas(root, "c")
    autos = [c for c, v in cv["cells"].items() if v["kind"] == "auto"]
    assert len(autos) == bb.MAX_AUTO_CELLS


def test_canvas_remove_move_resize_clear(root):
    bb.canvas_create(root, "c")
    bb.canvas_set(root, "c", "A", "1")
    assert bb.canvas_remove(root, "c", "A")["ok"]
    assert "A" not in bb.read_canvas(root, "c")["cells"]
    bb.canvas_set(root, "c", "A", "1")
    m = bb.canvas_move(root, "c", "A", "B")
    assert m["moved"] is True
    cv = bb.read_canvas(root, "c")["cells"]
    assert "A" not in cv and cv["B"]["content"] == "1"
    assert bb.canvas_resize(root, "c", "10x10")["grid"] == [10, 10]
    assert bb.canvas_clear(root, "c")["cleared"] == 1
    assert bb.read_canvas(root, "c")["cells"] == {}


def test_canvas_focus_and_dangling_never_surfaces(root):
    bb.canvas_create(root, "c")
    assert bb.canvas_focus(root, "c")["hot_canvas"] == "c"
    assert bb.read_board(root)["hot_canvas"] == "c"
    bb.canvas_focus(root, None)
    assert bb.read_board(root)["hot_canvas"] is None
    # dangling: focus an unknown canvas → read clears it
    bb.canvas_focus(root, "ghost")
    assert bb.read_board(root)["hot_canvas"] is None


def test_canvas_focus_requires_name_or_clear(root):
    r = _run_cli(str(root), "canvas", "focus")  # no --name, no --clear
    assert r.returncode == 1


def test_canvas_watch_registers_and_unregisters(root):
    bb.canvas_create(root, "c")
    ack = bb.canvas_watch(root, "c", "docs/")
    assert ack["watch"] == ["docs/"]
    ack = bb.canvas_watch(root, "c", "skills/")
    assert ack["watch"] == ["docs/", "skills/"]
    ack = bb.canvas_watch(root, "c", "docs/", remove=True)
    assert ack["watch"] == ["skills/"]


def test_canvas_watch_requires_path(root):
    assert bb.canvas_watch(root, "c", " ")["ok"] is False


def test_stamp_touch_lands_auto_cells(root):
    bb.canvas_create(root, "c")
    bb.canvas_watch(root, "c", "docs/")
    out = bb.stamp_tool_event(root, "file_editor", "docs/a.md")
    assert out["touched"] == 1 and out["canvases"] == ["c"]
    cell = bb.read_canvas(root, "c")["cells"]["docs/a.md"]
    assert cell["kind"] == "auto" and "a.md" in cell["content"]
    # outside prefix → no touch
    assert bb.stamp_tool_event(root, "file_editor", "skills/x/SKILL.md")["touched"] == 0


def test_stamp_touch_empty_board_ok(root):
    out = bb.stamp_tool_event(root, "bash", "anything.txt")
    assert out["ok"] and out["touched"] == 0


def test_canvas_touch_feeds_watching_canvas(root):
    """The skill-side feed entry point lands auto cells (engine is blackboard-free)."""
    bb.canvas_create(root, "doc-map")
    bb.canvas_watch(root, "doc-map", "docs/")
    out = bb.canvas_touch(root, "docs/design/spec.md", tool="bmad-architecture")
    assert out["touched"] == 1 and out["canvases"] == ["doc-map"]
    cell = bb.read_canvas(root, "doc-map")["cells"]["docs/design/spec.md"]
    assert cell["kind"] == "auto"
    assert cell["content"] == "bmad-architecture: spec.md"


def test_canvas_touch_requires_path(root):
    assert bb.canvas_touch(root, "   ")["ok"] is False


def test_canvas_touch_without_watchers_is_noop(root):
    assert bb.canvas_touch(root, "docs/a.md")["touched"] == 0


def test_canvas_touch_cli_roundtrip(tmp_path):
    """The documented CLI feed path works end to end."""
    assert _run_cli(str(tmp_path), "canvas", "create", "--name", "c").returncode == 0
    assert _run_cli(str(tmp_path), "canvas", "watch", "--name", "c",
                    "--path", "docs/").returncode == 0
    r = _run_cli(str(tmp_path), "canvas", "touch", "--path", "docs/a.md",
                 "--tool", "bmad-ux")
    assert r.returncode == 0
    ack = json.loads(r.stdout)
    assert ack["touched"] == 1 and ack["canvases"] == ["c"]


def test_canvas_survives_snapshot_rebuild(root):
    bb.canvas_create(root, "c", grid="4x4")
    bb.canvas_set(root, "c", "A1", "payload")
    bb.canvas_watch(root, "c", "docs/")
    os.remove(bb.board_paths(root)["snapshot"])
    cv = bb.read_canvas(root, "c")
    assert cv["grid"] == [4, 4]
    assert cv["cells"]["A1"]["content"] == "payload"
    assert cv["watch"] == ["docs/"]


def test_canvas_concurrent_sets_all_persist(root):
    bb.canvas_create(root, "c")

    def set_chunk(n):
        for i in range(5):
            bb.canvas_set(root, "c", f"w{n}.c{i}", f"v{n}-{i}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(set_chunk, range(6)))
    cells = bb.read_canvas(root, "c")["cells"]
    assert len(cells) == 30  # nothing lost under concurrent mutation


def test_canvases_cap_evicts_oldest_not_focused(root):
    for i in range(bb.MAX_CANVASES):
        bb.canvas_create(root, f"c{i:02d}")
        bb.canvas_set(root, f"c{i:02d}", "tick", str(i))
    bb.canvas_focus(root, "c00")  # protect the focused one
    bb.canvas_create(root, "new-one")
    names = set(bb.read_board(root)["canvases"])
    assert len(names) <= bb.MAX_CANVASES
    assert "c00" in names  # focused survivor
    assert "new-one" in names


def test_cli_canvas_lifecycle(tmp_path):
    _run_cli(str(tmp_path), "canvas", "create", "--name", "m", "--grid", "4x4")
    _run_cli(str(tmp_path), "canvas", "set", "--name", "m", "--cell", "A1",
             "--content", "core", "--kind", "decision")
    _run_cli(str(tmp_path), "canvas", "watch", "--name", "m", "--path", "docs/")
    r = _run_cli(str(tmp_path), "canvas-read", "--name", "m")
    cv = json.loads(r.stdout)
    assert cv["grid"] == [4, 4] and cv["cells"]["A1"]["content"] == "core"
    assert cv["watch"] == ["docs/"]
    r = _run_cli(str(tmp_path), "canvas", "move", "--name", "m", "--cell", "A1", "--to", "B2")
    assert json.loads(r.stdout)["moved"] is True
    _run_cli(str(tmp_path), "canvas", "focus", "--name", "m")
    r = _run_cli(str(tmp_path), "read", "--context")
    ctx = json.loads(r.stdout)
    assert ctx["hot_canvas"]["name"] == "m"
    _run_cli(str(tmp_path), "canvas", "focus", "--clear")
    r = _run_cli(str(tmp_path), "stats")
    s = json.loads(r.stdout)
    assert s["canvases"] == 1 and s["cells"] == 1


# ==============================================================================
# v2 — graph links
# ==============================================================================
def test_link_and_neighbors_both_directions(root):
    assert bb.link(root, "prd.x", "arch.y", relation="informs")["ok"]
    out = bb.neighbors(root, "prd.x")
    assert out == [{"node": "arch.y", "relation": "informs", "direction": "out"}]
    out = bb.neighbors(root, "arch.y")
    assert out == [{"node": "prd.x", "relation": "informs", "direction": "in"}]


def test_link_duplicate_ignored(root):
    bb.link(root, "a", "b")
    bb.link(root, "a", "b")
    assert len(bb.read_board(root)["links"]) == 1


def test_link_self_rejected(root):
    assert bb.link(root, "a", "a")["ok"] is False
    assert bb.link(root, "", "b")["ok"] is False


def test_unlink_specific_relation_or_all(root):
    bb.link(root, "a", "b", relation="r1")
    bb.link(root, "a", "b", relation="r2")
    ack = bb.unlink(root, "a", "b", relation="r1")
    assert ack["removed"] == 1
    ack = bb.unlink(root, "a", "b")
    assert ack["removed"] == 1
    assert bb.read_board(root)["links"] == []


def test_neighbors_relation_filter(root):
    bb.link(root, "a", "b", "x")
    bb.link(root, "a", "c", "y")
    out = bb.neighbors(root, "a", relation="y")
    assert [n["node"] for n in out] == ["c"]


def test_links_capped(root):
    for i in range(bb.MAX_LINKS + 5):
        bb.link(root, f"k{i:04d}", f"j{i:04d}")
    assert len(bb.read_board(root)["links"]) == bb.MAX_LINKS


def test_links_survive_snapshot_rebuild(root):
    bb.link(root, "a", "b", "rel")
    os.remove(bb.board_paths(root)["snapshot"])
    assert bb.read_board(root)["links"][0]["relation"] == "rel"


def test_cli_link_roundtrip(tmp_path):
    _run_cli(str(tmp_path), "link", "--a", "p", "--b", "q", "--relation", "blocks")
    r = _run_cli(str(tmp_path), "neighbors", "--node", "p")
    out = json.loads(r.stdout)
    assert out["neighbors"][0]["node"] == "q"
    _run_cli(str(tmp_path), "unlink", "--a", "p", "--b", "q")
    r = _run_cli(str(tmp_path), "neighbors", "--node", "p")
    assert json.loads(r.stdout)["neighbors"] == []


# ==============================================================================
# v2 — subscriptions & alerts (proactive routing)
# ==============================================================================
def test_subscription_routes_matching_key_update(root):
    bb.subscribe(root, "watcher1", "prd.*", channel="stop")
    bb.write_key(root, "prd.acme", "v")
    alerts = bb.pending_alerts(root, "stop")
    assert len(alerts) == 1
    assert "prd.acme" in alerts[0]["text"]


def test_subscription_nonmatching_no_alert(root):
    bb.subscribe(root, "w", "prd.*", channel="stop")
    bb.write_key(root, "ux.flow", "v")
    assert bb.pending_alerts(root, "stop") == []


def test_alert_dedup_identical(root):
    bb.subscribe(root, "w", "prd.*")
    bb.write_key(root, "prd.x", "v1")
    bb.write_key(root, "prd.x", "v2")  # same channel+kind+text → dedup
    assert len(bb.pending_alerts(root, "session")) == 1


def test_unsubscribe_stops_routing(root):
    bb.subscribe(root, "w", "prd.*")
    bb.unsubscribe(root, "w", "prd.*")
    bb.write_key(root, "prd.x", "v")
    assert bb.pending_alerts(root) == []


def test_consume_clears_only_target_channel(root):
    bb.subscribe(root, "w", "prd.*", channel="stop")
    bb.subscribe(root, "w2", "prd.*", channel="deploy")
    bb.write_key(root, "prd.x", "v")
    take = bb.consume_alerts(root, "stop")
    assert len(take) == 1
    assert bb.pending_alerts(root, "deploy")  # untouched
    assert bb.pending_alerts(root, "stop") == []


def test_canvas_subscription_routes_canvas_alerts(root):
    bb.subscribe(root, "w", "canvas:*")
    bb.canvas_create(root, "map")
    bb.canvas_set(root, "map", "A1", "x")
    alerts = bb.pending_alerts(root, "session")
    assert any("canvas 'map'" in a["text"] for a in alerts)


def test_alerts_capped(root):
    for i in range(bb.MAX_ALERTS + 10):
        bb.post_alert(root, "session", "info", f"m{i:03d}")
    alerts = bb.pending_alerts(root, "session")
    assert len(alerts) == bb.MAX_ALERTS
    assert alerts[0]["text"] == "m010"  # oldest expired


def test_post_alert_requires_text(root):
    assert bb.post_alert(root, "session", "info", "  ")["ok"] is False


def test_alerts_survive_snapshot_rebuild(root):
    bb.post_alert(root, "stop", "warn", "pending risk")
    os.remove(bb.board_paths(root)["snapshot"])
    alerts = bb.pending_alerts(root, "stop")
    assert alerts[0]["text"] == "pending risk"


def test_cli_alert_flow(tmp_path):
    _run_cli(str(tmp_path), "notify", "--channel", "stop", "--kind", "risk",
             "--text", "danger ahead")
    r = _run_cli(str(tmp_path), "alerts", "--channel", "stop")
    assert "danger ahead" in r.stdout
    r = _run_cli(str(tmp_path), "consume", "--channel", "stop")
    out = json.loads(r.stdout)
    assert out["alerts"][0]["text"] == "danger ahead"
    r = _run_cli(str(tmp_path), "alerts")
    assert json.loads(r.stdout)["alerts"] == []


def test_cli_subscribe_roundtrip(tmp_path):
    r = _run_cli(str(tmp_path), "subscribe", "--watcher", "w", "--pattern", "prd.*")
    assert json.loads(r.stdout)["ok"] is True
    _run_cli(str(tmp_path), "write", "--key", "prd.z", "--value", "1")
    r = _run_cli(str(tmp_path), "alerts")
    assert "prd.z" in r.stdout
    _run_cli(str(tmp_path), "unsubscribe", "--watcher", "w", "--pattern", "prd.*")
    _run_cli(str(tmp_path), "consume", "--channel", "session")
    _run_cli(str(tmp_path), "write", "--key", "prd.z2", "--value", "1")
    r = _run_cli(str(tmp_path), "alerts")
    assert json.loads(r.stdout)["alerts"] == []


# ==============================================================================
# v2 — compact context & stats enrichment
# ==============================================================================
def test_compact_context_lists_preview_and_graph_counts(root):
    bb.write_key(root, "prd.x", "A" * 200, hot=True)
    bb.list_add(root, "L", "i")
    bb.canvas_create(root, "c")
    bb.canvas_set(root, "c", "A1", "x")
    bb.link(root, "prd.x", "arch.y")
    bb.subscribe(root, "w", "prd.*")
    ctx = bb.compact_context(root)
    assert ctx["hot_meta"]["preview"] == "A" * 120  # bounded preview
    assert ctx["canvas_count"] == 1 and ctx["links"] == 1
    assert ctx["subscriptions"] == 1 and ctx["neighbors"] == ["arch.y"]
    assert ctx["key_count"] == 2


def test_compact_context_hot_list_preview(root):
    bb.list_add(root, "L", "a")
    bb.set_hot(root, "L")
    ctx = bb.compact_context(root)
    assert ctx["hot_meta"]["preview"] == "list[1]"


def test_compact_context_hot_canvas_summary(root):
    bb.canvas_create(root, "m", grid="2x2", focus=True)
    bb.canvas_set(root, "m", "A1", "x")
    bb.canvas_watch(root, "m", "docs/")
    bb.stamp_tool_event(root, "file_editor", "docs/z.md")
    ctx = bb.compact_context(root)
    hc = ctx["hot_canvas"]
    assert hc["name"] == "m" and hc["cells"] == 2 and hc["auto"] == 1
    assert hc["grid"] == [2, 2] and hc["watch"] == ["docs/"]


def test_stats_counts_lists_and_cells(root):
    bb.list_add(root, "L", "a")
    bb.canvas_create(root, "c")
    bb.canvas_set(root, "c", "A1", "x")
    bb.canvas_set(root, "c", "A2", "y")
    s = bb.stats(root)
    assert s["lists"] == 1 and s["canvases"] == 1 and s["cells"] == 2
    assert s["hot_canvas"] is None


# ==============================================================================
# v2 — engine arms: proactive session injection, real-time touch, stop alerts
# ==============================================================================
def test_session_start_does_not_deliver_session_alerts(tmp_path, monkeypatch):
    # Faz 5: alerts stay on the board for the skill CLI to consume; the hook
    # never delivers (and never consumes) them.
    audit_mod, _, _ = _engine(monkeypatch, str(tmp_path))
    bb.post_alert(str(tmp_path), "session", "risk", "deadline slipping")
    bb.post_alert(str(tmp_path), "session", "info", "second one")
    out = audit_mod.session_start({"cwd": str(tmp_path)})
    assert "[risk]" not in out["additionalContext"]
    assert len(bb.pending_alerts(str(tmp_path), "session")) == 2


def test_session_start_ignores_hot_canvas(tmp_path, monkeypatch):
    audit_mod, _, _ = _engine(monkeypatch, str(tmp_path))
    bb.canvas_create(str(tmp_path), "live-map", grid="4x4", focus=True)
    bb.canvas_set(str(tmp_path), "live-map", "A1", "hub")
    out = audit_mod.session_start({"cwd": str(tmp_path)})
    assert "live-map" not in out["additionalContext"]


def test_audit_does_not_feed_watching_canvas(tmp_path, monkeypatch):
    # Faz 3: per-write watches are not fed by the hook; skills own canvases.
    audit_mod, _, _ = _engine(monkeypatch, str(tmp_path))
    bb.canvas_create(str(tmp_path), "doc-map")
    bb.canvas_watch(str(tmp_path), "doc-map", "docs/")
    audit_mod.audit({"cwd": str(tmp_path), "hook_event_name": "PostToolUse",
                     "tool_name": "Write",
                     "tool_input": {"file_path": "docs/new-page.md", "content": "x"}})
    assert "docs/new-page.md" not in bb.read_canvas(str(tmp_path), "doc-map")["cells"]


def test_stop_ignores_canvas_and_announces_stop_alerts(tmp_path, monkeypatch):
    # Faz 4 + §5 delivery surface: canvas focus stays out of the report, but
    # stop-channel alerts are the stop edge's OWN delivery channel — surfaced
    # read-only (BLACKBOARD.md §5 "stop → surfaced at stop"), still
    # unconsumed: alerts stay on the board for the addressed watcher's CLI.
    audit_mod, stop_mod, config_mod = _engine(monkeypatch, str(tmp_path))
    monkeypatch.setattr(config_mod, "hook_gate_mode", lambda key: "hard")
    sprint = tmp_path / "bmad-output" / "implementation-artifacts"
    sprint.mkdir(parents=True)
    (sprint / "sprint-status.yaml").write_text(
        "stories:\n  1-1-alpha: in-progress\n", encoding="utf-8")
    bb.canvas_create(str(tmp_path), "wip-map", focus=True)
    bb.post_alert(str(tmp_path), "stop", "risk", "unresolved conflict")
    out = stop_mod.stop({"cwd": str(tmp_path), "hook_event_name": "Stop"})
    assert out["decision"] == "allow"
    assert "wip-map" not in out.get("reason", "")
    assert "unresolved conflict" in out.get("reason", "")
    assert "consume --channel stop" in out.get("reason", "")
    # unconsumed: alerts stay for the skill CLI
    assert len(bb.pending_alerts(str(tmp_path), "stop")) == 1


def _seed_block_story(tmp_path):
    """Seed an in-progress story that stop reports (hard gate)."""
    sprint = tmp_path / "bmad-output" / "implementation-artifacts"
    sprint.mkdir(parents=True, exist_ok=True)
    (sprint / "sprint-status.yaml").write_text(
        "stories:\n  1-1-alpha: in-progress\n", encoding="utf-8")


def test_stop_reports_story_and_ignores_handoffs(tmp_path, monkeypatch):
    """Faz 4: unclaimed handoffs are not stop's business — the story is
    reported, the handoff signal still waits for its addressed skill."""
    audit_mod, stop_mod, config_mod = _engine(monkeypatch, str(tmp_path))
    monkeypatch.setattr(config_mod, "hook_gate_mode", lambda key: "hard")
    _seed_block_story(tmp_path)
    bb.post_handoff(str(tmp_path), "bmad-ux", "prd.acme", "PRD final")
    out = stop_mod.stop({"cwd": str(tmp_path), "hook_event_name": "Stop"})
    assert out["decision"] == "allow"
    assert "1-1-alpha" in out.get("reason", "")
    assert "PROACTIVE" not in out.get("reason", "")
    assert len(bb.pending_handoffs(str(tmp_path), "bmad-ux")) == 1


def test_stop_allow_when_handoffs_all_claimed(tmp_path, monkeypatch):
    audit_mod, stop_mod, config_mod = _engine(monkeypatch, str(tmp_path))
    monkeypatch.setattr(config_mod, "hook_gate_mode", lambda key: "hard")
    out = stop_mod.stop({"cwd": str(tmp_path), "hook_event_name": "Stop"})
    assert out["decision"] == "allow"
    bb.post_handoff(str(tmp_path), "bmad-ux", "prd.acme", "PRD final")
    bb.consume_alerts(str(tmp_path), "handoff.bmad-ux")  # shake completed
    out2 = stop_mod.stop({"cwd": str(tmp_path), "hook_event_name": "Stop"})
    assert "PROACTIVE" not in out2.get("reason", "")


def test_stop_handoff_warning_excludes_bmad_help(tmp_path, monkeypatch):
    audit_mod, stop_mod, config_mod = _engine(monkeypatch, str(tmp_path))
    monkeypatch.setattr(config_mod, "hook_gate_mode", lambda key: "hard")
    _seed_block_story(tmp_path)
    bb.post_handoff(str(tmp_path), "bmad-help", "anything", "n")
    out = stop_mod.stop({"cwd": str(tmp_path), "hook_event_name": "Stop"})
    assert "PROACTIVE" not in out.get("reason", "")  # help-only waiting never warns


def test_stop_handoff_warning_does_not_block_alone(tmp_path, monkeypatch):
    """The warning is a nudge, never a block: allow stays allow with it."""
    audit_mod, stop_mod, config_mod = _engine(monkeypatch, str(tmp_path))
    monkeypatch.setattr(config_mod, "hook_gate_mode", lambda key: "hard")
    bb.post_handoff(str(tmp_path), "bmad-ux", "prd.acme", "PRD final")
    out = stop_mod.stop({"cwd": str(tmp_path), "hook_event_name": "Stop"})
    assert out["decision"] == "allow"  # nothing else to deny on
    assert out.get("reason") is None


def test_stop_allow_no_canvas_notice(tmp_path, monkeypatch):
    audit_mod, stop_mod, _ = _engine(monkeypatch, str(tmp_path))
    bb.canvas_create(str(tmp_path), "quiet")  # exists but not focused
    out = stop_mod.stop({"cwd": str(tmp_path), "hook_event_name": "Stop"})
    assert out["decision"] == "allow"
    assert "wip-map" not in out.get("reason", "")


# ==============================================================================
# v2 — fail-open hardening
# ==============================================================================
def test_stamp_touch_missing_project_ok(root):
    ghost = os.path.join(root, "never")
    assert bb.stamp_tool_event(ghost, "t", "p")["ok"] is True


def test_read_canvas_missing_project_ok(root):
    cv = bb.read_canvas(os.path.join(root, "never"), "c")
    assert cv["exists"] is False and cv["cells"] == {}


def test_pending_alerts_missing_project_ok(root):
    assert bb.pending_alerts(os.path.join(root, "never")) == []


def test_consume_alerts_never_raises(root):
    # even when the board lock/mutation path fails, consumption is a list
    assert bb.consume_alerts(os.path.join(root, "never"), "x") == []


def test_unicode_in_lists_and_canvas_cells(root):
    bb.list_add(root, "L", "Türkçe ✓ 中文")
    bb.canvas_create(root, "harita")
    bb.canvas_set(root, "harita", "hücre-1", "AMAÇ: ✓")
    board = bb.read_board(root)
    assert board["keys"]["L"]["value"] == ["Türkçe ✓ 中文"]
    assert bb.read_canvas(root, "harita")["cells"]["hücre-1"]["content"] == "AMAÇ: ✓"


# ==============================================================================
# v2.1 — hand-off chain handshake (prd → ux → architecture)
# ==============================================================================
def test_handoff_routes_to_skill_channel(root):
    ack = bb.post_handoff(root, "bmad-ux", "prd.acme",
                          note="PRD final — see prd.md")
    assert ack["ok"] is True
    pending = bb.pending_handoffs(root, "bmad-ux")
    assert len(pending) == 1
    assert pending[0]["kind"] == "handoff"
    assert pending[0]["text"].startswith("prd.acme:")
    assert "PRD final" in pending[0]["text"]


def test_handoff_channels_isolated_per_skill(root):
    bb.post_handoff(root, "bmad-ux", "prd.acme", "n1")
    bb.post_handoff(root, "bmad-architecture", "ux.acme", "n2")
    assert bb.pending_handoffs(root, "bmad-architecture")[0]["text"].startswith("ux.acme")
    assert bb.pending_handoffs(root, "bmad-ux")[0]["text"].startswith("prd.acme")
    assert bb.pending_handoffs(root, "bmad-dev-story") == []


def test_handoff_waiting_counts_and_consume_once(root):
    bb.post_handoff(root, "bmad-ux", "prd.acme", "n1")
    bb.post_handoff(root, "bmad-ux", "prd.acme", "n2")
    bb.post_handoff(root, "bmad-architecture", "ux.acme", "n3")
    waiting = bb.pending_handoff_channels(root)
    assert waiting == {"bmad-ux": 2, "bmad-architecture": 1}
    take = bb.consume_alerts(root, "handoff.bmad-ux")
    assert len(take) == 2
    assert bb.pending_handoff_channels(root) == {"bmad-architecture": 1}


def test_handoff_requires_to_and_from_key(root):
    assert bb.post_handoff(root, "", "k", "n")["ok"] is False
    assert bb.post_handoff(root, "bmad-ux", " ", "n")["ok"] is False


def test_handoff_survives_rebuild_and_does_not_resurrect(root):
    bb.post_handoff(root, "bmad-ux", "prd.acme", "n1")
    os.remove(bb.board_paths(root)["snapshot"])
    assert len(bb.pending_handoffs(root, "bmad-ux")) == 1  # rebuild keeps it
    bb.consume_alerts(root, "handoff.bmad-ux")
    os.remove(bb.board_paths(root)["snapshot"])
    assert bb.pending_handoffs(root, "bmad-ux") == []  # consumption evented


def test_chain_prd_to_arch_full_handshake(root):
    """The full prd→ux→arch relay on one board."""
    # prd run closes → signals ux
    bb.post_handoff(root, "bmad-ux", "prd.acme", "PRD final")
    # ux run opens, sees the signal, consumes it (shake 1)
    assert bb.pending_handoff_channels(root) == {"bmad-ux": 1}
    bb.consume_alerts(root, "handoff.bmad-ux")
    assert bb.pending_handoff_channels(root) == {}
    # ux run closes → signals architecture
    bb.post_handoff(root, "bmad-architecture", "ux.acme", "UX final")
    # arch run opens, sees it, consumes (shake 2)
    assert bb.pending_handoffs(root, "bmad-architecture")[0]["text"].startswith("ux.acme")
    bb.consume_alerts(root, "handoff.bmad-architecture")
    assert bb.pending_handoff_channels(root) == {}


def test_session_start_announces_waiting_relay_handoffs(tmp_path, monkeypatch):
    # Proactive relay (restored to the documented behavior): a waiting
    # delivery-relay signal is announced in the session-start context —
    # announce-only: the signal still waits for its addressed skill.
    audit_mod, _, _ = _engine(monkeypatch, str(tmp_path))
    bb.post_handoff(str(tmp_path), "bmad-ux", "prd.acme", "PRD final")
    out = audit_mod.session_start({"cwd": str(tmp_path)})
    assert "PROACTIVE — hand-off waiting" in out["additionalContext"]
    assert "bmad-ux (1)" in out["additionalContext"]
    # announce-only invariant preserved at the board level: signal still waits
    assert len(bb.pending_handoffs(str(tmp_path), "bmad-ux")) == 1


def test_session_start_announces_bmad_help_result_channel(tmp_path, monkeypatch):
    # bmad-help routes the claim to the operator, it never produces — its
    # result-reporting channel must still surface or the baton rots silently.
    audit_mod, _, _ = _engine(monkeypatch, str(tmp_path))
    bb.post_handoff(str(tmp_path), "bmad-help", "anything", "n")
    out = audit_mod.session_start({"cwd": str(tmp_path)})
    assert "bmad-help (1)" in out["additionalContext"]


def test_session_start_carries_proactive_warning_for_relay_signals(tmp_path, monkeypatch):
    audit_mod, _, _ = _engine(monkeypatch, str(tmp_path))
    bb.post_handoff(str(tmp_path), "bmad-ux", "prd.acme", "PRD final")
    bb.post_handoff(str(tmp_path), "bmad-architecture", "ux.acme", "UX final")
    ctx = audit_mod.session_start({"cwd": str(tmp_path)})["additionalContext"]
    assert "PROACTIVE" in ctx
    assert "unclaimed" in ctx
    assert "bmad-ux (1)" in ctx and "bmad-architecture (1)" in ctx


def test_session_start_no_proactive_warning_when_clear(tmp_path, monkeypatch):
    audit_mod, _, _ = _engine(monkeypatch, str(tmp_path))
    bb.post_handoff(str(tmp_path), "bmad-ux", "prd.acme", "PRD final")
    bb.consume_alerts(str(tmp_path), "handoff.bmad-ux")  # handshake completed
    ctx = audit_mod.session_start({"cwd": str(tmp_path)})["additionalContext"]
    assert "PROACTIVE" not in ctx
    assert "hand-off waiting" not in ctx


def test_session_start_ignores_off_relay_handoffs(tmp_path, monkeypatch):
    # Off-relay receivers stay out of the session-start nudge by design —
    # the CLI (`handoffs` without --skill) is the full waiting table.
    audit_mod, _, _ = _engine(monkeypatch, str(tmp_path))
    bb.post_handoff(str(tmp_path), "bmad-NOT-a-skill", "eval.x", "n")
    ctx = audit_mod.session_start({"cwd": str(tmp_path)})["additionalContext"]
    assert "PROACTIVE" not in ctx  # off-relay waiting never warns


def test_cli_handoff_roundtrip(tmp_path):
    r = _run_cli(str(tmp_path), "handoff", "--to", "bmad-ux",
                 "--from-key", "prd.acme", "--note", "PRD final")
    assert json.loads(r.stdout)["ok"] is True
    r = _run_cli(str(tmp_path), "handoffs")
    assert json.loads(r.stdout)["waiting"] == {"bmad-ux": 1}
    r = _run_cli(str(tmp_path), "handoffs", "--skill", "bmad-ux")
    assert "prd.acme" in r.stdout
    r = _run_cli(str(tmp_path), "consume", "--channel", "handoff.bmad-ux")
    assert len(json.loads(r.stdout)["alerts"]) == 1
    r = _run_cli(str(tmp_path), "handoffs")
    assert json.loads(r.stdout)["waiting"] == {}


def test_cli_handoff_requires_to_and_from_key(tmp_path):
    r = _run_cli(str(tmp_path), "handoff", "--to", "", "--from-key", "k")
    assert r.returncode == 1 and json.loads(r.stdout)["ok"] is False
    r = _run_cli(str(tmp_path), "handoff", "--to", "bmad-ux")  # missing --from-key
    assert r.returncode != 0  # argparse usage error (rc=2)
    assert not r.stdout.strip()  # no partial output


def test_chain_health_idle_when_no_signals(root):
    h = bb.chain_health(root)
    assert h["ok"] is True
    # 7 relay skills -> 6 stage-to-stage hops + the declared terminal gate
    assert len(h["chain"]) == 6 + len(bb.CHAIN_TERMINAL)
    assert len(h["methodology_chain"]) == 5  # 6 stages -> 5 hops
    assert all(hop["waiting"] == 0 and hop["consumed"] == 0 and hop["status"] == "idle"
               for hop in h["chain"])
    assert all(hop["waiting"] == 0 and hop["consumed"] == 0 and hop["status"] == "idle"
               for hop in h["methodology_chain"])
    assert h["extra"] == [] and h["total_waiting"] == 0 and h["stale"] == []


def test_chain_health_counts_waiting_and_consumed_per_hop(root):
    bb.post_handoff(root, "bmad-ux", "prd.acme", "n1")
    bb.consume_alerts(root, "handoff.bmad-ux")          # prd->ux consumed
    bb.post_handoff(root, "bmad-ux", "prd.acme", "n2")
    bb.consume_alerts(root, "handoff.bmad-ux")
    bb.post_handoff(root, "bmad-ux", "prd.new", "n3")   # still waiting
    bb.post_handoff(root, "bmad-architecture", "ux.acme", "n4")  # ux->arch waiting
    h = bb.chain_health(root)
    hops = {(hop["from"], hop["to"]): hop for hop in h["chain"]}
    assert hops[("bmad-prd", "bmad-ux")]["consumed"] == 2
    assert hops[("bmad-prd", "bmad-ux")]["waiting"] == 1
    assert hops[("bmad-prd", "bmad-ux")]["status"] == "waiting"
    assert hops[("bmad-ux", "bmad-architecture")]["waiting"] == 1
    assert hops[("bmad-architecture", "bmad-spec")]["status"] == "idle"
    assert h["total_waiting"] == 2


def test_chain_health_clear_status_all_consumed(root):
    bb.post_handoff(root, "bmad-ux", "prd.acme", "n1")
    bb.consume_alerts(root, "handoff.bmad-ux")
    h = bb.chain_health(root)
    assert h["chain"][0]["status"] == "clear"
    assert h["chain"][0]["waiting"] == 0 and h["chain"][0]["consumed"] == 1
    assert h["total_waiting"] == 0


def test_declared_terminal_hop_is_first_class_not_extra(root):
    """The delivery relay's gate (dev-story → code-review) is a DECLARED hop:
    a baton waiting there reports under `chain` (marked terminal), never as
    off-chain 'extra' noise — extra is reserved for genuinely unknown
    receivers. It is also claimable: the gate consumes it like any other hop.
    """
    bb.post_handoff(root, "bmad-code-review", "story.1-1-x", "dev complete",
                    sender="bmad-dev-story")
    h = bb.chain_health(root)
    terminal = [r for r in h["chain"] if r.get("terminal")]
    assert [(r["from"], r["to"]) for r in terminal] == [
        ("bmad-dev-story", "bmad-code-review")]
    assert terminal[0]["waiting"] == 1 and terminal[0]["status"] == "waiting"
    assert h["extra"] == [] and h["total_waiting"] == 1
    assert bb.pending_handoff_channels(root) == {"bmad-code-review": 1}
    bb.consume_alerts(root, "handoff.bmad-code-review")
    h = bb.chain_health(root)
    assert [r for r in h["chain"] if r.get("terminal")][0]["status"] == "clear"


def test_explicit_sender_beats_the_shared_run_key_prefix(root):
    """`story.` is written by BOTH create-story and dev-story, so the prefix
    alone mis-credits the dev close-out. An explicit sender wins; a malformed
    one is ignored (fail-open back to the prefix, never trusted verbatim)."""
    text = "story.1-1-x: dev complete"
    assert bb._signal_sender(text) == "bmad-create-story"
    assert bb._signal_sender(text, explicit="bmad-dev-story") == "bmad-dev-story"
    assert bb._signal_sender(text, explicit="not a skill") == "bmad-create-story"

    bb.post_handoff(root, "bmad-code-review", "story.1-1-x", "dev complete",
                    sender="bmad-dev-story")
    h = bb.chain_health(root)
    hops = {(r["from"], r["to"]): r for r in h["chain"]}
    assert hops[("bmad-dev-story", "bmad-code-review")]["waiting"] == 1
    assert h["extra"] == []


def test_governance_return_edge_is_claimable(root):
    """The loop's return edge (retro → experiment) carries a REAL baton: the
    experiment stage peeks its channel, so a lesson can be claimed instead of
    waiting until it goes stale."""
    bb.post_handoff(root, "bmad-research-experiment", "retro.4",
                    "lesson: raise the cache TTL")
    assert len(bb.pending_handoffs(root, "bmad-research-experiment")) == 1
    bb.consume_alerts(root, "handoff.bmad-research-experiment")
    h = bb.chain_health(root)
    hops = {(r["from"], r["to"]): r for r in h["sub_chains"]["governance"]}
    assert hops[("bmad-retrospective", "bmad-research-experiment")]["status"] == "clear"
    assert h["total_waiting"] == 0


def test_wds_terminal_phase_claims_its_baton(root):
    """The WDS pipeline's last phase is a registered hop receiver: its baton is
    declared under the wds sub-chain (never 'extra') and it is announced as
    waiting work."""
    bb.post_handoff(root, "wds-7-design-system", "wds.asset.acme", "assets done")
    h = bb.chain_health(root)
    hops = {(r["from"], r["to"]): r for r in h["sub_chains"]["wds"]}
    assert hops[("wds-6-asset-generation", "wds-7-design-system")]["waiting"] == 1
    assert h["extra"] == []
    assert bb.pending_handoff_channels(root) == {"wds-7-design-system": 1}


def test_chain_health_surfaces_unknown_receivers_as_extra(root):
    bb.post_handoff(root, "some-future-skill", "ux.acme", "n1")
    h = bb.chain_health(root)
    assert h["chain"][0]["waiting"] == 0  # ux->arch hop untouched
    assert len(h["extra"]) == 1
    assert h["extra"][0] == {"from": "bmad-ux", "to": "some-future-skill",
                             "waiting": 1, "consumed": 0, "status": "waiting"}
    assert h["total_waiting"] == 1


def test_chain_health_unknown_sender_attributes_and_never_crashes(root):
    # A from-key outside every known namespace (e.g. the QR skill's plain
    # 'quality-record' key) must attribute to 'unknown' — not crash the
    # tuple sort on None.
    ack = bb.post_handoff(root, "bmad-production-readiness", "quality-record", "QR-001 approved")
    assert ack["ok"] is True
    h = bb.chain_health(root)  # must not raise TypeError
    assert h["ok"] is True
    assert len(h["extra"]) == 1
    assert h["extra"][0] == {"from": "unknown", "to": "bmad-production-readiness",
                             "waiting": 1, "consumed": 0, "status": "waiting"}
    assert h["total_waiting"] == 1


def test_handoff_channel_fits_longest_skill_name(root):
    # 'handoff.' + bmad-check-implementation-readiness (35 chars) = 43 chars —
    # the old generic 40-char channel cap truncated the receiver's name out of
    # its own reach (pending_handoffs never matched, stop never saw the stage).
    long_skill = "bmad-check-implementation-readiness"
    assert len(long_skill) > 32  # guards the cap at the source
    bb.post_handoff(root, long_skill, "E-001", "approved")
    assert bb.pending_handoffs(root, long_skill), "signal must reach its own receiver"
    h = bb.chain_health(root)
    # E- prefix attributes to bmad-research-experiment: first-class
    # methodology hop now, no longer 'extra'.
    assert h["methodology_chain"][0]["to"] == long_skill
    assert h["methodology_chain"][0]["waiting"] == 1
    bb.consume_alerts(root, f"handoff.{long_skill}")
    h = bb.chain_health(root)
    assert h["methodology_chain"][0]["waiting"] == 0 and h["methodology_chain"][0]["consumed"] == 1


def test_chain_health_consumption_is_not_double_counted(root):
    bb.post_handoff(root, "bmad-ux", "prd.acme", "n1")
    bb.consume_alerts(root, "handoff.bmad-ux")
    bb.consume_alerts(root, "handoff.bmad-ux")  # second consume of empty channel
    h = bb.chain_health(root)
    assert h["chain"][0]["consumed"] == 1  # not 2


def test_chain_health_methodology_relay_first_class(root):
    """METHODOLOGY_CHAIN is a first-class ordered relay (E->IR->SP->S->QR->PR),
    not just prefix attribution under 'extra'."""
    bb.post_handoff(root, "bmad-check-implementation-readiness", "E-001", "approved")
    bb.post_handoff(root, "bmad-sprint-planning", "IR-2026-01-01", "READY")
    bb.consume_alerts(root, "handoff.bmad-sprint-planning")
    h = bb.chain_health(root)
    assert len(h["methodology_chain"]) == 5
    hops = {(hop["from"], hop["to"]): hop for hop in h["methodology_chain"]}
    assert ("bmad-research-experiment",
            "bmad-check-implementation-readiness") in hops
    assert hops[("bmad-research-experiment",
                 "bmad-check-implementation-readiness")]["waiting"] == 1
    assert hops[("bmad-research-experiment",
                 "bmad-check-implementation-readiness")]["status"] == "waiting"
    # sprint-planning hop: one consumed, none waiting -> clear
    assert hops[("bmad-check-implementation-readiness",
                 "bmad-sprint-planning")]["consumed"] == 1
    assert hops[("bmad-check-implementation-readiness",
                 "bmad-sprint-planning")]["waiting"] == 0
    assert hops[("bmad-check-implementation-readiness",
                 "bmad-sprint-planning")]["status"] == "clear"
    assert h["total_waiting"] == 1
    # no methodology signal leaks into extra
    assert all(e["to"] != "bmad-sprint-planning" for e in h["extra"])


def test_methodology_relay_end_to_end_pins_proactive_path(tmp_path, monkeypatch):
    """The full E→IR→SP→S→QR→PR relay, exercised end to end: every stage
    peeks, consumes, binds its run key (which mirrors methodology.last_*),
    and signals the next hop; the bridge fans out to the delivery relay.
    Pins the proactive surfaces with it — stop/session_start announce the
    waiting baton (never consuming it) and go quiet once every shake is
    complete, and doctor's verdict tracks the same truth."""
    root = str(tmp_path)
    audit_mod, stop_mod, _ = _engine(monkeypatch, root)

    # E closes: bind the run key, signal readiness
    bb.write_key(root, "E-013", "APPROVED: m=1.0")
    bb.post_handoff(root, "bmad-check-implementation-readiness", "E-013", "approved")

    for skill, run_key, to_skill in (
            ("bmad-check-implementation-readiness", "IR-1", "bmad-sprint-planning"),
            ("bmad-sprint-planning", "SP-1", "bmad-create-story")):
        assert len(bb.pending_handoffs(root, skill)) == 1   # peek
        bb.consume_alerts(root, f"handoff.{skill}")         # consume IS the shake
        bb.write_key(root, run_key, "stage close-out")      # bind + relay mirror
        bb.post_handoff(root, to_skill, run_key, "next stage")

    # S (the bridge) fans out: delivery (dev-story) + methodology (QR)
    bb.consume_alerts(root, "handoff.bmad-create-story")
    bb.write_key(root, "story.EX-9", "context ready")
    bb.post_handoff(root, "bmad-dev-story", "story.EX-9", "ready-for-dev")
    bb.post_handoff(root, "bmad-quality-record", "story.EX-9", "queued for QR")

    bb.consume_alerts(root, "handoff.bmad-quality-record")
    bb.write_key(root, "QR-1", "APPROVED")
    bb.post_handoff(root, "bmad-production-readiness", "QR-1", "approved")
    bb.consume_alerts(root, "handoff.bmad-production-readiness")
    bb.write_key(root, "PR-1", "READY")

    h = bb.chain_health(root)
    assert all(hop["waiting"] == 0 and hop["consumed"] > 0
               for hop in h["methodology_chain"])
    # only unclaimed baton left is the bridge's delivery fan-out
    assert [f"{x['from']}->{x['to']}" for x in h["chain"] if x["waiting"]] == [
        "bmad-create-story->bmad-dev-story"]
    # the relay mirror shows the chain position in E→IR→SP→S→QR→PR order
    assert list(bb.compact_context(root)["methodology"].keys()) == [
        "E", "IR", "SP", "S", "QR", "PR"]
    d = bb.doctor(root)
    # the one unclaimed baton is the bridge's deliberate delivery fan-out:
    # SIGNAL, not health (verdict stays HEALTHY while the relay waits)
    assert d["verdict"] == "HEALTHY"
    assert d["checks"]["chain"]["verdict"] == "SIGNAL"
    assert any("bmad-create-story->bmad-dev-story" in w for w in d["signal_warnings"])

    # proactive surfaces: a waiting methodology baton is announced, not consumed
    bb.post_handoff(root, "bmad-quality-record", "S-9", "second story queued")
    out = stop_mod.stop({"cwd": root, "hook_event_name": "Stop"})
    assert "PROACTIVE — hand-off waiting" in out["reason"]
    assert "bmad-quality-record (1)" in out["reason"]
    ss = audit_mod.session_start({"cwd": root})["additionalContext"]
    assert "PROACTIVE — hand-off waiting" in ss
    assert "bmad-quality-record (1)" in ss
    assert len(bb.pending_handoffs(root, "bmad-quality-record")) == 1  # untouched

    # every shake complete → PROACTIVE nudge quiet, doctor healthy; the
    # chain progress stays visible (read-only relay position, not a nudge)
    bb.consume_alerts(root, "handoff.bmad-quality-record")
    bb.consume_alerts(root, "handoff.bmad-dev-story")
    ss2 = audit_mod.session_start({"cwd": root})["additionalContext"]
    assert ss2.startswith(
        "METODOLOJI session started. Record chain: E → IR → SP → S → QR → PR.")
    assert "PROACTIVE" not in ss2
    assert "Chain progress" in ss2 and "PR-1: READY" in ss2
    assert bb.chain_health(root)["total_waiting"] == 0
    assert bb.doctor(root)["verdict"] == "HEALTHY"


def test_chain_health_stale_escalation_runtime_path(root):
    """Stale escalation is computed off the real event log (chain_health),
    not a manually aged dict — the Stop/session_start path calls this."""
    bb.post_handoff(root, "bmad-ux", "prd.acme", "fresh")
    old = bb.HANDOFF_SIGNAL_TTL_SECONDS + 3600
    import time as _time
    h = bb.chain_health(root, now=_time.time() + old)
    assert len(h["stale"]) == 1
    assert h["stale"][0]["to"] == "bmad-ux"
    assert h["stale"][0]["age_seconds"] >= int(old)
    # doctor on the same board (fresh now): no stale yet. A genuinely old
    # signal (backdated event AND snapshot ts — TD-014: snapshot is the
    # live truth, a log-only backdate stages a divergence) surfaces through
    # the real runtime path: chain_health -> doctor warnings + checks.chain.stale.
    h2 = bb.chain_health(root)
    assert h2["stale"] == []
    import json as _json
    paths = bb.board_paths(root)
    _old = _time.time() - (bb.HANDOFF_SIGNAL_TTL_SECONDS + 7200)
    with open(paths["events"], encoding="utf-8") as f:
        lines = f.read().splitlines()
    assert lines, "expected at least the handoff event"
    ev = _json.loads(lines[-1])
    ev["ts"] = _old
    lines[-1] = _json.dumps(ev, ensure_ascii=False)
    with open(paths["events"], "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(paths["snapshot"], encoding="utf-8") as f:
        _snap = _json.load(f)
    for _a in _snap.get("alerts", []):
        if _a.get("channel") == "handoff.bmad-ux":
            _a["ts"] = _old
    with open(paths["snapshot"], "w", encoding="utf-8") as f:
        _json.dump(_snap, f)
    d = bb.doctor(root)
    assert any("STALE" in w for w in d["warnings"])
    assert d["checks"]["chain"]["stale"] != []
    assert d["checks"]["chain"]["stale"][0]["to"] == "bmad-ux"


def test_chain_health_bridge_fanout_first_class(root):
    """create-story -> quality-record is the documented bridge fan-out and a
    first-class methodology hop (S->QR): it must NOT leak into 'extra'."""
    bb.post_handoff(root, "bmad-quality-record", "S-001", "queued")
    h = bb.chain_health(root)
    hops = {(hop["from"], hop["to"]): hop for hop in h["methodology_chain"]}
    assert hops[("bmad-create-story", "bmad-quality-record")]["waiting"] == 1
    assert h["extra"] == []
    assert h["total_waiting"] == 1


# v2.2 — phase-based sub-chains (discovery, alt-dev, …)
# ==============================================================================
def test_sub_chains_registry_declares_every_phase():
    """SUB_CHAINS is the single registry every diagnostic follows: each phase
    declares at least one hop, and no hop is a self-loop (a skill handing off
    to itself is a wiring bug, not a relay)."""
    assert {"discovery", "alt_dev", "testing", "governance", "gds", "wds"} <= set(bb.SUB_CHAINS)
    for name, hops in bb.SUB_CHAINS.items():
        assert hops, f"{name} declares no hops"
        for sender, receiver in hops:
            assert sender and receiver and sender != receiver


def test_chain_adjacent_is_known_but_relay_free():
    """The infrastructure/system layer is registered so its signals are never
    'extra' noise and its skills are lint-checked — but it declares no hop: a
    tool that produced a chain hop would be a wiring bug (installers build
    modules, not methodology records). Every name must be a real skill."""
    assert "bmad-help" in bb.CHAIN_ADJACENT
    assert "bmad-loop-sweep" in bb.CHAIN_ADJACENT and "memory" in bb.CHAIN_ADJACENT
    assert not (bb.CHAIN_ADJACENT & set(bb.sub_chain_skills()))  # disjoint from relays
    for skill in bb.CHAIN_ADJACENT:
        assert (Path(bb.__file__).parents[3] / "skills" / skill
                / "SKILL.md").is_file(), skill


def test_chain_adjacent_signals_surface_not_extra_noise(root):
    """A signal addressed to a chain-adjacent skill (eval-runner, help) is
    first-class diagnostic data: it surfaces in chain-health under a
    'chain-adjacent' sender row and in the session-start nudge — it is NOT
    'extra' noise. Only genuinely unknown receivers land in 'extra'."""
    bb.post_handoff(root, "bmad-eval-runner", "eval.x", "eval done")
    bb.post_handoff(root, "bmad-NOT-a-skill", "x.y", "unknown")
    h = bb.chain_health(root)
    adjacent = {e["to"]: e for e in h["extra"] if e["from"] == "chain-adjacent"}
    assert adjacent["bmad-eval-runner"]["waiting"] == 1
    unknown = {e["to"]: e for e in h["extra"] if e["from"] == "unknown"}
    assert unknown["bmad-NOT-a-skill"]["waiting"] == 1
    assert bb.pending_handoff_channels(root) == {"bmad-eval-runner": 1}


def test_sub_chain_signals_are_first_class_not_extra(root):
    """An alt-dev hop (quick-dev -> code-review) surfaces under its own phase
    and never as off-chain 'extra' noise — the same guarantee the delivery and
    methodology relays give. It is also NOT part of the E→IR→SP→S→QR→PR
    relay, so it must not stamp a methodology position."""
    bb.post_handoff(root, "bmad-code-review", "quickdev.3-2-digest", "quick-dev done")
    h = bb.chain_health(root)
    hops = {(hop["from"], hop["to"]): hop for hop in h["sub_chains"]["alt_dev"]}
    assert hops[("bmad-quick-dev", "bmad-code-review")]["waiting"] == 1
    assert hops[("bmad-quick-dev", "bmad-code-review")]["status"] == "waiting"
    assert h["extra"] == []
    assert h["total_waiting"] == 1
    # the sender attributes via its run-key namespace, not as 'unknown'
    assert bb._signal_sender("quickdev.3-2-digest: quick-dev done") == "bmad-quick-dev"
    assert bb._signal_sender("devauto.slug: dev-auto done") == "bmad-dev-auto"


def test_cis_facilitators_feed_the_product_brief(root):
    """The CIS creative/strategy tools are brainstorming's siblings: a session
    that produced a direction feeds the brief, attributed to its own namespace
    (and never as off-chain 'extra' noise)."""
    bb.post_handoff(root, "bmad-product-brief",
                    "cis.design.2026-09-17-onboarding", "design thinking complete")
    h = bb.chain_health(root)
    hops = {(hop["from"], hop["to"]): hop for hop in h["sub_chains"]["discovery"]}
    assert hops[("bmad-cis-design-thinking", "bmad-product-brief")]["waiting"] == 1
    assert h["extra"] == []
    assert bb._signal_sender("cis.design.x: done") == "bmad-cis-design-thinking"
    assert bb._signal_sender("cis.innovation.x: done") == "bmad-cis-innovation-strategy"
    assert bb._signal_sender("cis.solving.x: done") == "bmad-cis-problem-solving"
    assert bb._signal_sender("cis.story.x: done") == "bmad-cis-storytelling"


def test_dev_persona_is_the_third_alt_dev_branch(root):
    """The dev persona implements and carries the S→QR→PR bridge itself, so it
    converges on the same formal review gate as quick-dev and dev-auto — and
    like them it stamps no methodology position (a branch, not a stage)."""
    bb.post_handoff(root, "bmad-code-review", "agentdev.3-2-digest",
                    "dev persona complete")
    h = bb.chain_health(root)
    hops = {(hop["from"], hop["to"]): hop for hop in h["sub_chains"]["alt_dev"]}
    assert hops[("bmad-agent-dev", "bmad-code-review")]["waiting"] == 1
    assert hops[("bmad-quick-dev", "bmad-code-review")]["status"] == "idle"
    assert h["extra"] == []
    assert bb._signal_sender("agentdev.3-2-digest: done") == "bmad-agent-dev"


def test_sub_chain_hop_drains_when_consumed(root):
    bb.post_handoff(root, "bmad-code-review", "devauto.slug", "dev-auto done")
    bb.consume_alerts(root, "handoff.bmad-code-review")
    h = bb.chain_health(root)
    hops = {(hop["from"], hop["to"]): hop for hop in h["sub_chains"]["alt_dev"]}
    assert hops[("bmad-dev-auto", "bmad-code-review")]["consumed"] == 1
    assert hops[("bmad-dev-auto", "bmad-code-review")]["status"] == "clear"
    assert h["total_waiting"] == 0


def test_testing_sub_chain_feeders_fan_in_on_the_quality_record(root):
    """The TEA toolbox feeds ONE record: nine feeder hops into the quality
    record, each attributed to its own run-key namespace and none of them
    leaking into 'extra'."""
    bb.post_handoff(root, "bmad-quality-record", "test.trace.R-1",
                    "traceability gate: PASS")
    bb.post_handoff(root, "bmad-quality-record", "test.e2e.checkout", "tests generated")
    h = bb.chain_health(root)
    hops = {(hop["from"], hop["to"]): hop for hop in h["sub_chains"]["testing"]}
    assert hops[("bmad-testarch-trace", "bmad-quality-record")]["waiting"] == 1
    assert hops[("bmad-qa-generate-e2e-tests", "bmad-quality-record")]["waiting"] == 1
    assert h["extra"] == []
    assert bb._signal_sender("test.trace.R-1: gate PASS") == "bmad-testarch-trace"
    assert bb._signal_sender("test.e2e.checkout: done") == "bmad-qa-generate-e2e-tests"


def test_teach_me_testing_routes_into_the_tea_chain(root):
    """TEA Academy's close-out is a real baton, not decoration: the framework
    run is a routed receiver, so the signal surfaces in the start nudge."""
    bb.post_handoff(root, "bmad-testarch-framework", "teach.ada", "academy complete")
    h = bb.chain_health(root)
    hops = {(hop["from"], hop["to"]): hop for hop in h["sub_chains"]["testing"]}
    assert hops[("bmad-teach-me-testing", "bmad-testarch-framework")]["waiting"] == 1
    assert h["extra"] == []
    assert bb.pending_handoff_channels(root) == {"bmad-testarch-framework": 1}
    assert bb._signal_sender("teach.ada: academy complete") == "bmad-teach-me-testing"


def test_gds_vertical_mirrors_the_relay_shape(root):
    """The game vertical re-uses the canonical patterns: ideation fans in on
    the brief, QA tools feed the review gate (the QR feeder pattern — their
    BRIDGE adds evidence to the review's QR record, never a record of their
    own), dev variants converge on the review, retro re-enters planning. No
    GDS hop may stamp a methodology position: the vertical runs beside the
    E→IR→SP→S→QR→PR relay, never inside it."""
    bb.post_handoff(root, "gds-code-review", "gds.test.review.R-1", "test review done")
    bb.post_handoff(root, "gds-code-review", "gds.playtest.epic-1", "playtest plan complete")
    bb.post_handoff(root, "gds-sprint-planning", "gds.retro.4", "retro lessons")
    h = bb.chain_health(root)
    hops = {(hop["from"], hop["to"]): hop for hop in h["sub_chains"]["gds"]}
    assert hops[("gds-test-review", "gds-code-review")]["waiting"] == 1
    # the playtest plan is a feeder too: its BRIDGE lands findings in the same
    # QR, so it must signal the review instead of sitting off-chain
    assert hops[("gds-playtest-plan", "gds-code-review")]["waiting"] == 1
    assert hops[("gds-retrospective", "gds-sprint-planning")]["waiting"] == 1
    assert h["extra"] == []
    # sender attribution via the vertical's own namespace
    assert bb._signal_sender("gds.dev.3-1-boss-ai: dev done") == "gds-dev-story"
    assert bb._signal_sender("gds.quickdev.slug: quick dev done") == "gds-quick-dev"
    assert bb._signal_sender("gds.playtest.epic-1: plan complete") == "gds-playtest-plan"
    # the feeder is a hop member now, not a chain-adjacent surface
    assert "gds-playtest-plan" not in bb.CHAIN_ADJACENT
    assert "gds-playtest-plan" in bb.sub_chain_skills()
    # the vertical never touches the methodology relay
    board = bb.read_board(root)
    assert not [k for k in board["keys"] if k.startswith("methodology.last_")]


def test_wds_pipeline_flows_and_evolution_reenters(root):
    """Freya's design pipeline relays setup→signoff→brief→trigger→scenario→ux→
    dev→asset→design-system, and brownfield evolution re-enters at the brief —
    the WDS loop. All first-class, none as 'extra' noise."""
    bb.post_handoff(root, "wds-5-agentic-development", "wds.ux.checkout-flow", "delivery package ready")
    bb.post_handoff(root, "wds-1-project-brief", "wds.evolve.2026-09", "cycle findings")
    h = bb.chain_health(root)
    hops = {(hop["from"], hop["to"]): hop for hop in h["sub_chains"]["wds"]}
    assert hops[("wds-4-ux-design", "wds-5-agentic-development")]["waiting"] == 1
    assert hops[("wds-8-product-evolution", "wds-1-project-brief")]["waiting"] == 1
    assert h["extra"] == []
    assert bb._signal_sender("wds.brief.acme: brief ready") == "wds-1-project-brief"


def test_gds_wds_verticals_never_stamp_methodology_relay(root):
    """The verticals are parallel worlds, not methodology stages: no gds./wds.
    run key may stamp a methodology.last_* position."""
    from modules.mirror import mirror
    mirror(root, "gds.gdd.acme", "GDD complete", to="gds-ux")
    mirror(root, "wds.brief.acme", "brief complete", to="wds-2-trigger-mapping")
    board = bb.read_board(root)
    stamped = [k for k in board["keys"] if k.startswith("methodology.last_")]
    assert stamped == [], f"vertical keys stamped relay positions: {stamped}"


def test_governance_sub_chain_closes_the_methodology_loop(root):
    """The governance hops are what make the relay a loop instead of a line:
    retrospective hands its lessons back to the experiment origin, and a course
    correction lands on sprint planning, which owns the backlog. Both surface
    under their own phase (never as off-chain 'extra')."""
    bb.post_handoff(root, "bmad-research-experiment", "retro.4", "retro complete")
    bb.post_handoff(root, "bmad-sprint-planning", "change.2026-09-17",
                    "Sprint change proposal: Major scope")
    h = bb.chain_health(root)
    hops = {(hop["from"], hop["to"]): hop for hop in h["sub_chains"]["governance"]}
    assert hops[("bmad-retrospective", "bmad-research-experiment")]["waiting"] == 1
    assert hops[("bmad-correct-course", "bmad-sprint-planning")]["waiting"] == 1
    assert h["extra"] == []
    assert h["total_waiting"] == 2
    # a governance hop re-enters at an existing relay skill; it must NOT be
    # counted as an extra position in the E→IR→SP→S→QR→PR relay itself
    assert not [k for k in bb.read_board(root)["keys"]
                if k.startswith("methodology.last_")]
    assert bb._signal_sender("retro.4: done") == "bmad-retrospective"
    assert bb._signal_sender("change.2026-09-17: proposal") == "bmad-correct-course"


# ==============================================================================
# End-to-end scenario: the GDS playtest feeder + the GDS advisor personas
# ==============================================================================
PLUGIN_ROOT = Path(__file__).resolve().parents[3]


def _resolve_customization(key: str, skill_dir: str):
    """Run the real resolver CLI and return the resolved block for `key`."""
    import subprocess
    r = subprocess.run(
        [sys.executable, str(PLUGIN_ROOT / "hooks" / "engine" / "resolve_customization.py"),
         "--skill", str(PLUGIN_ROOT / "skills" / skill_dir), "--key", key],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
        stdin=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)[key]


def test_scenario_gds_playtest_feeds_the_review_and_personas_route_it(tmp_path, monkeypatch):
    """One GDS session, end to end, exercising both vertical repairs together:

    1. the playtest plan closes out through the instruction its own registry
       TOML resolves (`workflow.on_complete`) — declared hop, no residue;
    2. the feeder adds evidence to the review's QR without opening a record of
       its own and without stamping a methodology position;
    3. session_start ANNOUNCES the waiting baton (routed, announce-only) and
       the review run is the one that claims it;
    4. the persona that routes this project (an advisor, previously filed as a
       tool) resolves its agent block, dispatches into real skills and grounds
       in the board read-only.
    """
    import re
    import shlex
    import subprocess

    root = str(tmp_path)

    # --- 1. execute the resolved close-out instruction (not a hand-built one) --
    instruction = _resolve_customization("workflow.on_complete", "gds-playtest-plan")
    command = re.search(r"`([^`]+)`", instruction).group(1)
    argv = shlex.split(
        command.replace("{metodoloji-root}", PLUGIN_ROOT.as_posix())
               .replace("{project-root}", Path(root).as_posix())
               .replace("<slug>", "epic-1"))
    assert argv[0] == "python3"
    argv[0] = sys.executable  # same interpreter, no PATH assumption
    run = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                         timeout=60, stdin=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    ack = json.loads(run.stdout)
    assert ack["ok"] is True and ack["handed_off"] is True
    assert ack["key"] == "gds.playtest.epic-1"

    # --- 2. QR feeding: declared hop, no record of its own, no relay stamp ----
    h = bb.chain_health(root)
    hops = {(r["from"], r["to"]): r for r in h["sub_chains"]["gds"]}
    assert hops[("gds-playtest-plan", "gds-code-review")]["waiting"] == 1
    assert h["extra"] == [] and h["total_waiting"] == 1
    assert set(bb.read_board(root)["keys"]) == {"gds.playtest.epic-1"}
    assert not [k for k in bb.read_board(root)["keys"] if k.startswith("methodology.last_")]
    baton = bb.pending_handoffs(root, "gds-code-review")
    assert baton and baton[0]["text"].startswith("gds.playtest.epic-1")
    assert bb.pending_handoff_channels(root) == {"gds-code-review": 1}

    # --- 3. session announcement: routed, never consumed --------------------
    audit_mod, _, _ = _engine(monkeypatch, root)
    ctx = audit_mod.session_start({"cwd": root})["additionalContext"]
    assert "PROACTIVE — hand-off waiting" in ctx
    assert "gds-code-review (1)" in ctx
    assert len(bb.pending_handoffs(root, "gds-code-review")) == 1  # announce-only

    # --- 4. the advisor personas route: menu → real skills, board read-only --
    skills_dir = PLUGIN_ROOT / "skills"
    for persona in ("gds-agent-game-architect", "gds-agent-game-designer",
                    "gds-agent-tech-writer"):
        agent = _resolve_customization("agent", persona)
        assert agent["name"] and agent["icon"] and agent["menu"]
        for item in agent["menu"]:
            target = item.get("skill")
            if target:
                assert (skills_dir / target / "SKILL.md").is_file(), f"{persona} → {target}"
        text = (skills_dir / persona / "SKILL.md").read_text(encoding="utf-8")
        # The grounding call is the one read-only digest (it replaced the old
        # read --context + handoffs pair); the baton listing survives only as
        # the fallback for when the digest could not run.
        assert "orient.py" in text and "handoffs --project-root" in text
        assert "read --context" not in text
        # a persona can neither claim a baton nor post one: it produces no record
        assert "consume --channel" not in text and "mirror --to" not in text

    # --- 5. the review run claims the baton: hop closes, board drains --------
    peek = json.loads(_run_cli(root, "handoffs", "--skill", "gds-code-review").stdout)
    assert len(peek["handoffs"]) == 1
    assert peek["handoffs"][0]["text"].startswith("gds.playtest.epic-1")
    _run_cli(root, "consume", "--channel", "handoff.gds-code-review")
    h = bb.chain_health(root)
    hops = {(r["from"], r["to"]): r for r in h["sub_chains"]["gds"]}
    assert hops[("gds-playtest-plan", "gds-code-review")]["status"] == "clear"
    assert h["total_waiting"] == 0 and h["extra"] == []
    assert "gds-code-review (1)" not in audit_mod.session_start(
        {"cwd": root})["additionalContext"]


def test_scenario_full_chain_prd_to_deploy_signals_flow(tmp_path, monkeypatch):
    """The delivery + methodology relay end to end, PRD → deploy. Every closing
    command below is the one its SKILL.md instructs (verbatim run keys), so the
    test proves signals actually FLOW: each baton appears in the next stage's
    handoffs and drains on consume, the story heartbeat fans out to BOTH
    downstream runs, the dev close-out is sender-attributed to the terminal
    review gate, methodology.last_* mirrors advance stage by stage, and
    session_start announces the waiting batons until they are consumed.
    """
    root = str(tmp_path)

    def mirror(key, value, to, note, sender=None):
        argv = ["mirror", "--key", key, "--value", value, "--to", to,
                "--note", note]
        if sender:
            argv += ["--sender", sender]
        out = json.loads(_run_cli(root, *argv).stdout)
        assert out["ok"] is True and out["handed_off"] is True, out

    def hop(frm, to):
        h = bb.chain_health(root)
        for rows in ([h["chain"], h["methodology_chain"]]
                     + list(h["sub_chains"].values())):
            for r in rows:
                if (r["from"], r["to"]) == (frm, to):
                    return r
        raise AssertionError(f"undeclared hop {frm} -> {to}")

    def handshake(skill):
        peek = json.loads(_run_cli(root, "handoffs", "--skill", skill).stdout)
        assert peek["handoffs"], f"no baton waiting for {skill}"
        _run_cli(root, "consume", "--channel", f"handoff.{skill}")
        return [h["text"] for h in peek["handoffs"]]

    # 1-5. The delivery relay, one close-out per SKILL.md: PRD → UX →
    #       architecture → spec → epics → story. Each run key is the skill's
    #       own (`prd.<slug>`, `ux.<slug>`, …); each baton waits exactly one
    #       handshake and drains on consume.
    prev = "bmad-prd"
    for key, to, note in [
        ("prd.acme", "bmad-ux", "PRD final — see prd.md"),
        ("ux.acme", "bmad-architecture", "UX final — DESIGN.md/EXPERIENCE.md"),
        ("architecture.acme", "bmad-spec", "Spine final — 4 ADs adopted"),
        ("spec.acme", "bmad-create-epics-and-stories",
         "SPEC.md final — 5 capabilities"),
        ("epics.acme", "bmad-create-story",
         "epics.md final — start with Epic 1 Story 1"),
    ]:
        mirror(key, note, to, note)
        assert hop(prev, to)["waiting"] == 1
        texts = handshake(to)
        assert texts[0].startswith(key)
        assert hop(prev, to)["consumed"] == 1
        prev = to

    # 6. The bridge: create-story's close-out is ONE heartbeat with TWO
    #    signals — dev gets the story, quality-record queues the QR.
    story = "story.1-1-auth"
    mirror(story, "context ready — story file final", "bmad-dev-story",
           "story ready-for-dev — file: docs/stories/1-1-auth.md")
    mirror(story, "context ready — story file final", "bmad-quality-record",
           "story 1-1-auth queued — create the QR record once review/done")
    assert hop("bmad-create-story", "bmad-dev-story")["waiting"] == 1
    assert hop("bmad-create-story", "bmad-quality-record")["waiting"] == 1
    assert "1-1-auth" in bb.read_board(root)["keys"][
        "methodology.last_story"]["value"]
    audit_mod, _, _ = _engine(monkeypatch, root)
    ctx = audit_mod.session_start({"cwd": root})["additionalContext"]
    assert "PROACTIVE — hand-off waiting" in ctx
    assert "bmad-dev-story (1)" in ctx and "bmad-quality-record (1)" in ctx
    handshake("bmad-dev-story")
    handshake("bmad-quality-record")

    # 7. The terminal gate: dev-story hands off with --sender, because `story.`
    #    is create-story's namespace too — without the stamp the baton would
    #    land in 'extra' as an unknown sender instead of the declared hop.
    mirror(story, "dev complete — story in review, QR created",
           "bmad-code-review",
           "Dev complete — story 1-1-auth in review, QR created",
           sender="bmad-dev-story")
    gate = hop("bmad-dev-story", "bmad-code-review")
    assert gate["waiting"] == 1 and gate.get("terminal") is True
    assert bb.chain_health(root)["extra"] == []  # attributed, not orphaned
    handshake("bmad-code-review")

    # 8-9. The methodology relay closes: QR approval hands off to production
    #      readiness; the PR heartbeat is write-only — the deploy is the end.
    mirror("QR-001", "APPROVED for S-001 — record: docs/quality/QR-001.md",
           "bmad-production-readiness",
           "QR-001 approved for S-001. Ready for production readiness check.")
    handshake("bmad-production-readiness")
    out = json.loads(_run_cli(root, "mirror", "--key", "PR-2026-09-18",
                              "--value",
                              "READY: v1.0.0 — record: docs/deployment/PR-001.md").stdout)
    assert out["ok"] is True and out["handed_off"] is False

    # Everything landed: no waiting baton anywhere, every declared hop clear,
    # the relay remembers the run keys it advanced through.
    h = bb.chain_health(root)
    assert h["total_waiting"] == 0 and h["extra"] == [] and h["stale"] == []
    assert all(r["status"] == "clear" for r in h["chain"])
    # this run only traveled the S→QR→PR section of the methodology relay:
    # those hops are clear, the untouched E→IR→SP→S ones report idle (not
    # waiting) — 'clear' would mean a handshake happened, which it did not.
    assert not any(r["waiting"] for r in h["methodology_chain"])
    by_pair = {(r["from"], r["to"]): r for r in h["methodology_chain"]}
    assert by_pair[("bmad-create-story", "bmad-quality-record")]["status"] == "clear"
    assert by_pair[("bmad-quality-record", "bmad-production-readiness")]["status"] == "clear"
    progress = bb.doctor(root)["checks"]["methodology"]["progress"]
    assert progress["S"].startswith("story.1-1-auth")
    assert progress["QR"].startswith("QR-001")
    assert progress["PR"].startswith("PR-2026-09-18")
    assert "PROACTIVE" not in audit_mod.session_start(
        {"cwd": root})["additionalContext"]


def test_scenario_discovery_fan_in_converges_on_the_prd_entry(tmp_path, monkeypatch):
    """The discovery side, end to end: five independent sources fan in on ONE
    brief, the brief synthesizes them into a single hardened artifact, and the
    PRD entry consumes the merge (brief + PRFAQ's direct line) before handing
    off into the canonical delivery chain. Every closing command is the skill's
    own (verbatim run keys from its SKILL.md / finalize / verdict / step-06).
    """
    root = str(tmp_path)

    def mirror(key, value, to, note):
        out = json.loads(_run_cli(
            root, "mirror", "--key", key, "--value", value,
            "--to", to, "--note", note).stdout)
        assert out["ok"] is True and out["handed_off"] is True, out

    # --- 1. five sources close, all converging on the brief ------------------
    sources = [
        ("brainstorm.relay-topic", "synthesis final — see brainstorm/relay-topic.md"),
        ("forge.relay-idea", "HARDENED — see forge-report.html"),
        ("research.market.relay-competition", "market report — see research/market.md"),
        ("research.domain.relay-domain", "domain synthesis — see research/domain.md"),
        ("research.tech.relay-stack", "tech synthesis — see research/tech.md"),
    ]
    for key, value in sources:
        mirror(key, value, "bmad-product-brief", f"{value}")

    # the discovery sub-chain holds all five; ONE receiver accumulates them
    sender_of = {"brainstorm.": "bmad-brainstorming",
                 "forge.": "bmad-forge-idea",
                 "research.market.": "bmad-market-research",
                 "research.domain.": "bmad-domain-research",
                 "research.tech.": "bmad-technical-research"}
    h = bb.chain_health(root)
    hops = {(r["from"], r["to"]): r for r in h["sub_chains"]["discovery"]}
    for key, _ in sources:
        sender = sender_of[next(p for p in sender_of if key.startswith(p))]
        assert hops[(sender, "bmad-product-brief")]["waiting"] == 1
    assert h["extra"] == []
    assert bb.pending_handoff_channels(root) == {"bmad-product-brief": 5}

    # session_start announces the pile, then the brief run claims it in ONE
    # handshake — the channel drains whole, five hops clear at once
    audit_mod, _, _ = _engine(monkeypatch, root)
    ctx = audit_mod.session_start({"cwd": root})["additionalContext"]
    assert "bmad-product-brief (5)" in ctx
    peek = json.loads(_run_cli(
        root, "handoffs", "--skill", "bmad-product-brief").stdout)
    texts = sorted(h["text"] for h in peek["handoffs"])
    assert len(texts) == 5
    assert all(texts[i].startswith(key)
               for i, (key, _) in enumerate(sorted(sources)))
    _run_cli(root, "consume", "--channel", "handoff.bmad-product-brief")
    h = bb.chain_health(root)
    assert h["total_waiting"] == 0
    # the five exercised hops are clear; the untouched CIS facilitator hops
    # report idle (no handshake happened there — 'clear' would be a lie)
    exercised = {(s, "bmad-product-brief") for s in sender_of.values()}
    for r in h["sub_chains"]["discovery"]:
        pair = (r["from"], r["to"])
        assert r["status"] == ("clear" if pair in exercised else "idle")

    # --- 2. the merge at the chain entry: brief + PRFAQ's direct line --------
    mirror("brief.relay-product", "brief hardened — see brief.md",
           "bmad-prd", "brief final — the five discovery inputs are folded in")
    mirror("prfaq.relay-product", "verdict: GO — see prfaq.md",
           "bmad-prd", "PRFAQ verdict — pull the market framing from prfaq.md")
    assert bb.pending_handoff_channels(root) == {"bmad-prd": 2}
    ctx = audit_mod.session_start({"cwd": root})["additionalContext"]
    assert "bmad-prd (2)" in ctx
    _run_cli(root, "consume", "--channel", "handoff.bmad-prd")

    # --- 3. the merged entry hands off into the canonical chain --------------
    mirror("prd.relay-product", "PRD final — see prd.md", "bmad-ux",
           "PRD final — see prd.md; discovery findings live in the brief")
    hop = next(r for r in bb.chain_health(root)["chain"]
               if (r["from"], r["to"]) == ("bmad-prd", "bmad-ux"))
    assert hop["waiting"] == 1
    _run_cli(root, "consume", "--channel", "handoff.bmad-ux")

    # everything landed: board drained, discovery stamped no methodology
    # position (brainstorm./forge./brief./prfaq./research.* are not stage
    # keys), the session opens silent.
    h = bb.chain_health(root)
    assert h["total_waiting"] == 0 and h["extra"] == [] and h["stale"] == []
    assert not [k for k in bb.read_board(root)["keys"]
                if k.startswith("methodology.last_")]
    assert bb.doctor(root)["checks"]["methodology"]["progress"] == {}
    assert "PROACTIVE" not in audit_mod.session_start(
        {"cwd": root})["additionalContext"]


def test_sub_chain_receivers_surface_in_pending_channels(root):
    """Session-start routing follows the registry: a waiting brief (discovery
    fan-in) and a waiting review gate (alt-dev fan-in) are both announced."""
    bb.post_handoff(root, "bmad-product-brief", "brainstorm.topic", "final")
    bb.post_handoff(root, "bmad-code-review", "quickdev.x", "done")
    assert bb.pending_handoff_channels(root) == {"bmad-product-brief": 1,
                                                "bmad-code-review": 1}
    assert bb.sub_chain_skills() >= {"bmad-product-brief", "bmad-code-review",
                                     "bmad-quick-dev", "bmad-dev-auto"}


def test_doctor_lists_sub_chain_waiting_hops(root):
    """doctor aggregates waiting hops from EVERY declared relay, sub-chains
    included — adding a phase needs no diagnostic change. A waiting baton is
    the designed post-close state: it is SIGNAL, not a health warning."""
    bb.post_handoff(root, "bmad-code-review", "quickdev.x", "done")
    d = bb.doctor(root)
    hops = d["checks"]["chain"]["waiting_hops"]
    assert any(h["from"] == "bmad-quick-dev" and h["to"] == "bmad-code-review"
               for h in hops)
    assert d["checks"]["chain"]["total_waiting"] == 1
    assert d["checks"]["chain"]["verdict"] == "SIGNAL"
    assert d["checks"]["chain"]["status"] == "signal"
    assert any("bmad-quick-dev->bmad-code-review" in w for w in d["signal_warnings"])
    assert not any("bmad-quick-dev->bmad-code-review" in w for w in d["warnings"])
    assert d["verdict"] == "HEALTHY"


def test_chain_methodology_end_to_end_full_handshake(root):
    """The full E->IR->SP->S->QR->PR relay on one board: every hop posts,
    peeks and consumes in order; methodology_chain drains to clear."""
    bb.post_handoff(root, "bmad-check-implementation-readiness", "E-001", "approved")
    assert bb.pending_handoff_channels(root) == {"bmad-check-implementation-readiness": 1}
    bb.consume_alerts(root, "handoff.bmad-check-implementation-readiness")
    bb.post_handoff(root, "bmad-sprint-planning", "IR-2026-01-01", "READY")
    assert bb.pending_handoffs(root, "bmad-sprint-planning")[0]["text"].startswith("IR-2026-01-01")
    bb.consume_alerts(root, "handoff.bmad-sprint-planning")
    bb.post_handoff(root, "bmad-create-story", "SP-2026-01-01", "queue ready")
    bb.consume_alerts(root, "handoff.bmad-create-story")
    bb.post_handoff(root, "bmad-dev-story", "story.1-1-x", "ready-for-dev")
    bb.post_handoff(root, "bmad-quality-record", "story.1-1-x", "queued")
    bb.consume_alerts(root, "handoff.bmad-dev-story")
    bb.consume_alerts(root, "handoff.bmad-quality-record")
    bb.post_handoff(root, "bmad-production-readiness", "QR-001", "approved")
    bb.consume_alerts(root, "handoff.bmad-production-readiness")
    h = bb.chain_health(root)
    assert h["total_waiting"] == 0
    assert all(hop["status"] == "clear" for hop in h["methodology_chain"])
    # the bridge fan-out consumed too: no residue anywhere
    assert h["extra"] == []


def test_cli_chain_health(tmp_path):
    r = _run_cli(str(tmp_path), "chain-health")
    h = json.loads(r.stdout)
    assert h["ok"] is True and len(h["chain"]) == 6 + len(bb.CHAIN_TERMINAL)
    _run_cli(str(tmp_path), "handoff", "--to", "bmad-ux",
             "--from-key", "prd.acme", "--note", "n")
    r = _run_cli(str(tmp_path), "chain-health")
    h = json.loads(r.stdout)
    assert h["chain"][0]["waiting"] == 1 and h["total_waiting"] == 1


# ==============================================================================
# v2.2 — doctor (one-glance diagnostic)
# ==============================================================================
# --- handoff-replace: correct a waiting signal's note WITHOUT dropping it ----
def test_replace_handoff_corrects_note_in_place(root):
    """The 2026-09-30 opencode session corrected a close-out note by consuming
    the channel and re-posting — the bridge silently disappeared for every
    downstream reader in between. replace_handoff keeps the signal waiting."""
    bb.post_handoff(root, "bmad-ux", "prd.acme", "PRD final — Q1... no, Q6")
    r = bb.replace_handoff(root, "bmad-ux", "prd.acme", "PRD final — Q6")
    assert r["ok"] is True and r["replaced"] is True
    waiting = bb.pending_handoffs(root, "bmad-ux")
    assert len(waiting) == 1  # the bridge never dropped
    assert waiting[0]["text"] == "prd.acme: PRD final — Q6"


def test_replace_handoff_survives_snapshot_rebuild(root):
    """The edit is event-sourced (alert-edit): replay folds it back, so the
    corrected note — not the original — survives a snapshot rebuild."""
    import os
    bb.post_handoff(root, "bmad-ux", "prd.acme", "typo note")
    bb.replace_handoff(root, "bmad-ux", "prd.acme", "fixed note")
    os.remove(bb.board_paths(root)["snapshot"])
    waiting = bb.pending_handoffs(root, "bmad-ux")
    assert len(waiting) == 1 and waiting[0]["text"] == "prd.acme: fixed note"


def test_replace_handoff_with_no_waiting_signal_posts_fresh(root):
    """First-write semantics: a correction against an empty channel posts the
    signal (replaced False). A consumed handshake is never resurrected — a
    replacement must not bring a finished handshake back to waiting."""
    r = bb.replace_handoff(root, "bmad-ux", "prd.acme", "note")
    assert r["ok"] is True and r["replaced"] is False
    assert len(bb.pending_handoffs(root, "bmad-ux")) == 1
    bb.post_handoff(root, "bmad-spec", "prd.acme", "x")
    bb.consume_alerts(root, "handoff.bmad-spec")
    # a consumed handshake is never resurrected: a replace against an empty
    # channel would post fresh — asserted above — so this names a DIFFERENT
    # key than the consumed one (prd.acme has no waiting bmad-spec signal)
    # and checks the empty-channel path stays first-write, not resurrection.
    r2 = bb.replace_handoff(root, "bmad-spec", "other.run", "y")
    assert r2["ok"] is True and r2["replaced"] is False
    # the consumed prd.acme bridge stays consumed; only the fresh signal waits
    texts = [a["text"] for a in bb.pending_handoffs(root, "bmad-spec")]
    assert texts == ["other.run: y"]


def test_replace_handoff_keeps_original_ts(root):
    """The corrected signal keeps its original timestamp — staleness/age stays
    truthful instead of resetting on every correction."""
    import time as _time
    bb.post_handoff(root, "bmad-ux", "prd.acme", "original")
    original_ts = bb.pending_handoffs(root, "bmad-ux")[0]["ts"]
    _time.sleep(0.01)
    bb.replace_handoff(root, "bmad-ux", "prd.acme", "corrected")
    assert bb.pending_handoffs(root, "bmad-ux")[0]["ts"] == original_ts


def test_replace_handoff_cli_roundtrip(tmp_path):
    import json as _json
    cli = (Path(__file__).resolve().parent.parent.parent.parent
           / "bmad" / "scripts" / "blackboard.py")
    _run_cli(str(tmp_path), "handoff", "--to", "bmad-ux",
             "--from-key", "prd.acme", "--note", "drafting typo")
    r = _run_cli(str(tmp_path), "handoff-replace", "--to", "bmad-ux",
                 "--from-key", "prd.acme", "--note", "corrected note")
    ack = _json.loads(r.stdout)
    assert r.returncode == 0 and ack["ok"] is True and ack["replaced"] is True
    assert bb.pending_handoffs(str(tmp_path), "bmad-ux")[0]["text"].endswith("corrected note")


def test_doctor_healthy_on_empty_board(root):
    d = bb.doctor(root)
    assert d["ok"] is True and d["verdict"] == "HEALTHY"
    assert d["warnings"] == []
    c = d["checks"]
    assert c["gate"]["on"] is True or c["gate"]["known"] is False
    assert c["snapshot"]["exists"] is False
    assert c["events"]["exists"] is False
    assert c["drift"]["in_sync"] is True
    assert c["chain"]["total_waiting"] == 0
    assert c["methodology"]["progress"] == {}


def test_doctor_reports_methodology_relay_progress(root):
    bb.write_key(root, "E-001", "APPROVED: fake_accuracy=1.0", type_="state")
    d = bb.doctor(root)
    assert d["checks"]["methodology"]["progress"]["E"].startswith("E-001")


def test_doctor_healthy_on_lived_in_board(root):
    bb.write_key(root, "prd.acme", "v", hot=True)
    bb.canvas_create(root, "m", grid="8x8", focus=True)
    bb.canvas_set(root, "m", "A1", "x")
    bb.list_add(root, "prd.acme.pending", "t")
    bb.link(root, "prd.acme", "arch.acme")
    bb.post_handoff(root, "bmad-ux", "prd.acme", "n")
    bb.consume_alerts(root, "handoff.bmad-ux")  # complete the shake
    d = bb.doctor(root)
    assert d["verdict"] == "HEALTHY"
    assert d["checks"]["focus"]["hot"] == "prd.acme"
    assert d["checks"]["focus"]["hot_canvas"] == "m"
    assert d["checks"]["drift"]["in_sync"] is True


def test_doctor_warns_on_cap_pressure(root):
    for i in range(bb.MAX_KEYS):
        bb.write_key(root, f"k{i:03d}", "x")
    d = bb.doctor(root)
    assert d["verdict"] == "NEEDS ATTENTION"
    assert any("keys 128/128" in w for w in d["warnings"])


def test_doctor_warns_on_tmp_residue(root):
    bb.write_key(root, "k", "v")
    open(bb.board_paths(root)["snapshot"] + ".1.2.tmp", "w").write("junk")
    open(bb.board_paths(root)["events"] + ".3.4.tmp", "w").write("junk")
    d = bb.doctor(root)
    assert d["verdict"] == "NEEDS ATTENTION"
    assert any("2 leftover .tmp" in w for w in d["warnings"])


def test_doctor_warns_on_unclaimed_handoffs(root):
    """A waiting baton is the designed post-close state, not a defect: the
    2026-09-30 PRD close-out surfaced NEEDS ATTENTION whose only warning was
    its own deliberate hand-off. Waiting signals are report-only
    (signal_warnings + chain verdict SIGNAL) — verdict stays HEALTHY."""
    bb.post_handoff(root, "bmad-ux", "prd.acme", "n")
    d = bb.doctor(root)
    assert d["verdict"] == "HEALTHY"  # a deliberate signal is not a health failure
    assert d["checks"]["chain"]["verdict"] == "SIGNAL"
    assert any("unclaimed hand-off" in w and "PROACTIVE" in w
               for w in d["signal_warnings"])


def test_doctor_stale_signal_is_health_warning(root):
    """Stale (TTL-expired) batons DO poison the verdict — a >24h unclaimed
    signal means a crashed/hung skill, unlike a fresh deliberate hand-off."""
    import time as _time
    bb.post_handoff(root, "bmad-ux", "prd.acme", "n")
    _old = _time.time() - (bb.HANDOFF_SIGNAL_TTL_SECONDS + 7200)
    paths = bb.board_paths(root)
    with open(paths["events"], encoding="utf-8") as f:
        lines = f.read().splitlines()
    ev = json.loads(lines[-1])
    ev["ts"] = _old
    lines[-1] = json.dumps(ev, ensure_ascii=False)
    with open(paths["events"], "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(paths["snapshot"], encoding="utf-8") as f:
        snap = json.load(f)
    for a in snap.get("alerts", []):
        if a.get("channel") == "handoff.bmad-ux":
            a["ts"] = _old
    with open(paths["snapshot"], "w", encoding="utf-8") as f:
        json.dump(snap, f)
    d = bb.doctor(root)
    assert d["verdict"] == "NEEDS ATTENTION"
    assert any("STALE" in w for w in d["warnings"])
    assert d["checks"]["chain"]["verdict"] == "NEEDS ATTENTION"


# --- watch plane: the skill contract stamps its stage automatically ----------
def test_hot_run_key_write_stamps_watcher(root):
    """A hot run-key write is the run's opening heartbeat — write_key derives
    the watch event from the same prefix→skill table chain_health attributes
    with, so the watcher plane has a writer for every stage the contract
    touches (record_watcher's producer side). Bridge keys stamp nothing."""
    ack = bb.write_key(root, "prd.acme", "PRD v1 discovery", type_="state", hot=True)
    assert ack["ok"] is True
    assert "bmad-prd" in ack.get("watchers", [])
    board = bb.read_board(root)
    assert "bmad-prd" in board["watchers"]
    # event-sourced: the stamp survives a snapshot rebuild
    os.remove(bb.board_paths(root)["snapshot"])
    assert "bmad-prd" in bb.read_board(root)["watchers"]


def test_hot_non_stage_key_stamps_no_watcher(root):
    bb.write_key(root, "purpose", "dev S-001", type_="state", hot=True)
    assert "watchers" not in bb.write_key(
        root, "scope", "src/**", type_="state", hot=True)
    assert bb.read_board(root)["watchers"] == {}


def test_methodology_stage_watch_stamp_via_mirror_heartbeat(root):
    """The gate's automatic heartbeat (E-<id>) carries hot=False today; the
    skill-side activation contract (write --hot) is the stamped path. A
    methodology run key still resolves its stage when hot."""
    bb.write_key(root, "IR-20260928", "readiness: READY", type_="state", hot=True)
    assert "bmad-check-implementation-readiness" in bb.read_board(root)["watchers"]


# --- graph plane: focused canvas binds its hot run key -----------------------
def test_focused_canvas_derives_owns_link(root):
    """canvas create --focus under a hot run key derives one `owns` edge —
    the graph plane carries the canvas↔run wiring neighbors() reads without
    any hand-posted link. Event-sourced and replay-stable."""
    bb.write_key(root, "ux.mailjs", "UX in discovery", type_="state", hot=True)
    ack = bb.canvas_create(root, "ux.mailjs.map", grid="12x8", focus=True)
    assert ack["focused"] is True
    nb = bb.neighbors(root, "ux.mailjs")
    assert {"node": "canvas:ux.mailjs.map", "relation": "owns",
            "direction": "out"} in nb
    # reverse direction reads too
    nb2 = bb.neighbors(root, "canvas:ux.mailjs.map")
    assert {"node": "ux.mailjs", "relation": "owns", "direction": "in"} in nb2
    # replay-stable
    os.remove(bb.board_paths(root)["snapshot"])
    nb3 = bb.neighbors(root, "ux.mailjs")
    assert any(n["relation"] == "owns" and n["node"] == "canvas:ux.mailjs.map"
               for n in nb3)


def test_focused_canvas_without_hot_key_makes_no_link(root):
    bb.canvas_create(root, "solo.map", focus=True)
    assert bb.read_board(root)["links"] == []


def test_owner_rerun_replaces_owns_edge(root):
    bb.write_key(root, "ux.a", "v1", type_="state", hot=True)
    bb.canvas_create(root, "m.map", focus=True)
    bb.write_key(root, "ux.b", "v2", type_="state", hot=True)
    bb.canvas_create(root, "m.map", focus=True)  # same canvas, new owner
    edges = [l for l in bb.read_board(root)["links"] if l["relation"] == "owns"]
    assert len(edges) == 1 and edges[0]["a"] == "ux.b"


# --- session edges announce their delivery channels (never consume) ----------
def test_session_start_announces_session_channel_alerts(tmp_path, monkeypatch):
    """BLACKBOARD.md §5: 'session → surfaced at session start'. The edge
    announces read-only; the watcher clears with consume --channel session."""
    import modules.audit as audit_mod
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    bb.subscribe(tmp_path, "ops", "prd.*", channel="session")
    bb.write_key(tmp_path, "prd.acme", "v1")  # routed into 'session'
    assert len(bb.pending_alerts(tmp_path, "session")) == 1
    ctx = audit_mod.session_start({"cwd": str(tmp_path)})["additionalContext"]
    assert "session channel" in ctx and "consume --channel session" in ctx
    assert len(bb.pending_alerts(tmp_path, "session")) == 1  # untouched


def test_stop_announces_stop_channel_alerts(tmp_path, monkeypatch):
    """BLACKBOARD.md §5: 'stop → surfaced at stop'. Report-only; the
    producer clears the channel itself."""
    stop_mod = _stop_engine(tmp_path, monkeypatch)
    bb.post_alert(tmp_path, "stop", "risk", "NFR unconfirmed")
    out = stop_mod.stop({"cwd": str(tmp_path), "hook_event_name": "Stop"})
    assert out["decision"] == "allow"
    assert "stop channel" in out["reason"] and "consume --channel stop" in out["reason"]
    assert len(bb.pending_alerts(tmp_path, "stop")) == 1  # not consumed


def test_stop_quiet_when_stop_channel_empty(tmp_path, monkeypatch):
    stop_mod = _stop_engine(tmp_path, monkeypatch)
    out = stop_mod.stop({"cwd": str(tmp_path), "hook_event_name": "Stop"})
    assert "stop channel" not in out.get("reason", "")


# --- doctor: event-log bound surfaces the rotate path -------------------------
def test_doctor_warns_at_event_log_bound(root):
    """At MAX_EVENTS the truncation guard has already dropped history; doctor
    names it and points at `rotate` — the operator surface for the silent
    _truncate_events_if_needed bound."""
    paths = bb.board_paths(root)
    os.makedirs(os.path.dirname(paths["events"]), exist_ok=True)
    with open(paths["events"], "w", encoding="utf-8") as f:
        for i in range(bb.MAX_EVENTS):
            f.write(json.dumps({"event": "watch", "watcher": f"w{i}",
                                "ts": 1.0}) + "\n")
    d = bb.doctor(root)
    assert any("event log at bound" in w and "rotate" in w for w in d["warnings"])
    assert d["verdict"] == "NEEDS ATTENTION"
    # below the bound → silent
    with open(paths["events"], "w", encoding="utf-8") as f:
        for i in range(10):
            f.write(json.dumps({"event": "watch", "watcher": f"w{i}",
                                "ts": 1.0}) + "\n")
    d2 = bb.doctor(root)
    assert not any("event log at bound" in w for w in d2["warnings"])


# --- touch stamps carry the hook phase ----------------------------------------
def test_touch_stamp_records_hook_phase(root):
    bb.canvas_create(root, "c2")
    bb.canvas_watch(root, "c2", "docs/")
    ack = bb.stamp_tool_event(root, "file_editor", "docs/a.md",
                              hook_event="PostToolUse")
    assert ack["ok"] is True
    board = bb.read_board(root)
    stamp = board["keys"]["last_tool.file_editor"]
    assert stamp["hook_event"] == "PostToolUse"
    # event-sourced: the phase survives replay
    os.remove(bb.board_paths(root)["snapshot"])
    stamp2 = bb.read_board(root)["keys"]["last_tool.file_editor"]
    assert stamp2["hook_event"] == "PostToolUse"


def test_doctor_detects_snapshot_drift(root):
    bb.write_key(root, "k", "v")
    # tamper with the snapshot behind the event log's back
    paths = bb.board_paths(root)
    with open(paths["snapshot"], encoding="utf-8") as f:
        data = json.load(f)
    data["keys"]["ghost"] = {"value": "x", "type": "note", "updated": 1.0}
    with open(paths["snapshot"], "w", encoding="utf-8") as f:
        json.dump(data, f)
    d = bb.doctor(root)
    assert d["checks"]["drift"]["in_sync"] is False
    assert d["verdict"] == "NEEDS ATTENTION"
    assert any("drift" in w and "rebuild" in w for w in d["warnings"])


def test_no_blackboard_set_command_in_source(root):
    """There is NO `blackboard.py set` command — the verb is `write`. A `set`
    in doc/comment/skill text makes the LLM emit an invalid command."""
    import pathlib
    repo = pathlib.Path(__file__).resolve().parent.parent.parent.parent
    self_file = pathlib.Path(__file__).resolve()
    offenders = []
    for pat in ("hooks/engine/**/*.py", "skills/**/*.md", "docs/*.md"):
        for p in pathlib.Path(repo).glob(pat):
            if p.resolve() == self_file:
                continue  # this guard text, not a real usage
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if "blackboard.py set " in line or "blackboard.py set --" in line:
                    offenders.append(f"{p.relative_to(repo)}:{i}: {line.strip()}")
    assert not offenders, "blackboard.py set komutu yok, write kullan:\n" + "\n".join(offenders)


def test_doctor_ignores_tool_stamped_keys_in_drift(root):
    """last_tool.* keys are folded from `tool` events and carry type "tool" —
    drift norm excludes them, so a tool stamp never reads as drift."""
    bb.write_key(root, "k", "v")
    paths = bb.board_paths(root)
    with open(paths["snapshot"], encoding="utf-8") as f:
        data = json.load(f)
    data["keys"]["last_tool.file_editor"] = {
        "value": "docs/a.md", "type": "tool", "updated": 1.0}
    with open(paths["snapshot"], "w", encoding="utf-8") as f:
        json.dump(data, f)
    d = bb.doctor(root)
    assert d["checks"]["drift"]["in_sync"] is True


def test_doctor_counts_garbage_event_lines(root):
    bb.write_key(root, "k", "v")
    with open(bb.board_paths(root)["events"], "a", encoding="utf-8") as f:
        f.write("GARBAGE\n")
    d = bb.doctor(root)
    assert d["checks"]["events"]["garbage"] == 1
    assert d["checks"]["events"]["status"] == "warn"
    assert d["verdict"] == "NEEDS ATTENTION"


def test_doctor_reports_watch_paths(root):
    bb.canvas_create(root, "m")
    bb.canvas_watch(root, "m", "docs/")
    d = bb.doctor(root)
    w = d["checks"]["watch"]["paths"]
    assert w == [{"canvas": "m", "path": "docs/", "exists": os.path.exists("docs/")}]


def test_cli_doctor_panel_and_json(tmp_path):
    _run_cli(str(tmp_path), "write", "--key", "k", "--value", "v", "--hot")
    r = _run_cli(str(tmp_path), "doctor")
    assert "blackboard doctor" in r.stdout
    assert "verdict: HEALTHY" in r.stdout
    assert "focus     hot: k" in r.stdout
    r = _run_cli(str(tmp_path), "doctor", "--json")
    j = json.loads(r.stdout)
    assert j["ok"] is True and j["checks"]["snapshot"]["version"] == 3


def test_v1_snapshot_upgrade_keeps_data(root):
    """A v1 snapshot (no graph sections) loads cleanly and gains defaults."""
    bb.write_key(root, "old", "data")
    paths = bb.board_paths(root)
    with open(paths["snapshot"], encoding="utf-8") as f:
        data = json.load(f)
    data["version"] = 1
    for k in ("links", "canvases", "subscriptions", "alerts"):
        data.pop(k, None)
    with open(paths["snapshot"], "w", encoding="utf-8") as f:
        json.dump(data, f)
    board = bb.read_board(root)
    assert board["keys"]["old"]["value"] == "data"
    assert board["links"] == [] and board["canvases"] == {}


# ==============================================================================
# Proactive relay surface (session_start / stop) — announce-only nudges
# ==============================================================================
def test_pending_handoff_channels_routed_skills_only(root):
    # Routed: the methodology relay, the delivery relay (incl. terminal
    # receiver), bmad-help's report channel AND the chain-adjacent surfaces
    # (a known skill's waiting signal always surfaces — extra is only for
    # genuinely unknown receivers). Off-relay channels are
    # the session-start nudge's blind spot by design (the CLI lists all).
    bb.post_handoff(root, "bmad-create-story", "SP-001", "sprint ready")
    bb.post_handoff(root, "bmad-ux", "prd.acme", "delivery relay — announced")
    bb.post_handoff(root, "bmad-code-review", "bmad-dev-story", "review ready")
    bb.post_handoff(root, "bmad-help", "PR-001", "PR done — report to user")
    bb.post_handoff(root, "bmad-eval-runner", "eval.x", "chain-adjacent — announced")
    bb.post_handoff(root, "bmad-NOT-a-skill", "x.y", "unknown — silent")
    counts = bb.pending_handoff_channels(root)
    assert counts == {"bmad-create-story": 1, "bmad-ux": 1,
                      "bmad-code-review": 1, "bmad-help": 1,
                      "bmad-eval-runner": 1}


def _stop_engine(tmp_path, monkeypatch):
    """stop() with the project root pinned and gates forced hard."""
    import importlib
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    stop_mod = importlib.import_module("modules.stop")
    config_mod = importlib.import_module("modules.config")
    monkeypatch.setattr(config_mod, "hook_gate_mode", lambda key: "hard")
    return stop_mod


def test_stop_surface_methodology_handoffs(tmp_path, monkeypatch):
    stop_mod = _stop_engine(tmp_path, monkeypatch)
    bb.post_handoff(tmp_path, "bmad-create-story", "SP-001", "sprint ready")
    out = stop_mod.stop({"cwd": str(tmp_path), "hook_event_name": "Stop"})
    assert out["decision"] == "allow"
    assert "PROACTIVE — hand-off waiting" in out["reason"]
    assert "bmad-create-story (1)" in out["reason"]
    # announce-only: the signal still waits for its addressed skill
    assert len(bb.pending_handoffs(tmp_path, "bmad-create-story")) == 1


def test_stop_handoff_warning_excludes_tool_chain(tmp_path, monkeypatch):
    stop_mod = _stop_engine(tmp_path, monkeypatch)
    bb.post_handoff(tmp_path, "bmad-ux", "prd.acme", "PRD final")
    out = stop_mod.stop({"cwd": str(tmp_path), "hook_event_name": "Stop"})
    assert "PROACTIVE" not in out.get("reason", "")


def test_stop_handoff_warning_excludes_bmad_help(tmp_path, monkeypatch):
    stop_mod = _stop_engine(tmp_path, monkeypatch)
    bb.post_handoff(tmp_path, "bmad-help", "PR-001", "PR ready")
    out = stop_mod.stop({"cwd": str(tmp_path), "hook_event_name": "Stop"})
    assert "PROACTIVE" not in out.get("reason", "")


def test_stop_ignores_kind_stop_alerts_as_handoffs(tmp_path, monkeypatch):
    stop_mod = _stop_engine(tmp_path, monkeypatch)
    bb.post_alert(tmp_path, "handoff.bmad-create-story", "stop", "diagnostic only")
    out = stop_mod.stop({"cwd": str(tmp_path), "hook_event_name": "Stop"})
    assert "PROACTIVE" not in out.get("reason", "")


def test_stop_handoff_warning_does_not_block_alone_methodology(tmp_path, monkeypatch):
    """The nudge rides the allow report; it never blocks by itself."""
    stop_mod = _stop_engine(tmp_path, monkeypatch)
    bb.post_handoff(tmp_path, "bmad-quality-record", "S-001", "story queued")
    out = stop_mod.stop({"cwd": str(tmp_path), "hook_event_name": "Stop"})
    assert out["decision"] == "allow"
    assert "PROACTIVE" in out["reason"]


def test_doctor_detects_duplicate_record_ids_real_layout(tmp_path):
    # The scan reads the canonical layout (E-011 contract): a same-ID copy
    # under the wrong directory is reported — with the legacy per-type
    # table (docs/stories, docs/quality-records, …) this check never ran.
    (tmp_path / "docs" / "experiments").mkdir(parents=True)
    (tmp_path / "docs" / "quality").mkdir(parents=True)
    (tmp_path / "docs" / "experiments" / "E-001.md").write_text(
        "# E-001", encoding="utf-8")
    (tmp_path / "docs" / "quality" / "E-001.md").write_text(
        "# E-001 copy", encoding="utf-8")  # duplicate in the wrong dir
    (tmp_path / "docs" / "quality" / "QR-001.md").write_text(
        "# QR-001", encoding="utf-8")
    d = bb.doctor(str(tmp_path))
    assert any("Duplicate record ID 'E-001'" in w for w in d["warnings"])
    # a lone record must never read as a duplicate
    (tmp_path / "docs" / "quality" / "E-001.md").unlink()
    d2 = bb.doctor(str(tmp_path))
    assert not any("Duplicate record ID" in w for w in d2["warnings"])


# ==============================================================================
# Live-board repair (ops exercise): retire a pre-protocol diagnostic stamp
# ==============================================================================
def test_retire_legacy_diag_key_clears_all_channels(root):
    """Legacy residue retires through the documented ops path: `consume
    --channel C` clears EVERY alert on that channel (the consume fold
    matches by channel, not channel+kind), and the pre-protocol diagnostic
    stamp is retired by an evented empty write. The replay reproduces both
    (no drift)."""
    bb.write_key(root, "_diag.last_guard_failure", "legacy stamp", type_="note")
    bb.post_alert(root, "stop", "warn", "legacy residue 1")
    bb.post_alert(root, "stop", "info", "legacy residue 2")
    bb.post_alert(root, "guard", "warn", "legacy residue 3")
    taken = bb.consume_alerts(root, "stop")
    assert len(taken) == 2  # every kind on the channel goes in one consume
    assert bb.pending_alerts(root, "stop") == []
    assert len(bb.consume_alerts(root, "guard")) == 1
    bb.write_key(root, "_diag.last_guard_failure", "")  # evented key retire
    assert bb.read_board(root)["keys"]["_diag.last_guard_failure"]["value"] == ""
    d = bb.doctor(root)
    assert d["checks"]["drift"]["in_sync"] is True  # replay agrees

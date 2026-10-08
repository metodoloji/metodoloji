"""Tests for hooks/engine/modules/stop.py and hooks/engine/main.py dispatch.

Faz 4 contract: stop() NEVER denies. It stamps a session_stop marker and
returns allow, with a warn-only report naming in-progress stories and
session code writes. Enforcement lives at write time (guard) and commit
time (quality/deploy gates).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HOOKS))

from modules.stop import _in_progress_stories, stop  # noqa: E402

MAIN_PY = _HOOKS / "main.py"


def _env(root, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)


def _seed_audit_log(root, records):
    log = root / ".metodoloji/logs/hook-audit.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _seed_session_start(root, session_id=None):
    import time
    from modules.stop import _SESSION_MARKER_TYPE
    log = root / ".metodoloji/logs/hook-audit.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    rec = {"type": _SESSION_MARKER_TYPE, "timestamp": time.time()}
    if session_id is not None:
        rec["session_id"] = session_id
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


# --- _in_progress_stories ------------------------------------------------------

def test_story_status_no_sprint_file(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    assert _in_progress_stories(str(tmp_path)) == []


def test_story_status_in_progress_reported(tmp_path, monkeypatch):
    cand = tmp_path / "bmad-output/implementation-artifacts"
    cand.mkdir(parents=True)
    (cand / "sprint-status.yaml").write_text(
        "stories:\n  1-2-login: in-progress\n  3-4-export: done\n",
        encoding="utf-8",
    )
    _env(tmp_path, monkeypatch)
    assert _in_progress_stories(str(tmp_path)) == ["1-2-login"]


def test_story_status_canonical_docs_path(tmp_path, monkeypatch):
    # Seçenek A kanonik: docs/development/native/sprint-status.yaml first
    # (skills compose it as {implementation_artifacts}/sprint-status.yaml).
    cand = tmp_path / "docs/development/native"
    cand.mkdir(parents=True)
    (cand / "sprint-status.yaml").write_text(
        "stories:\n  1-2-login: in-progress\n  3-4-export: done\n",
        encoding="utf-8",
    )
    _env(tmp_path, monkeypatch)
    assert _in_progress_stories(str(tmp_path)) == ["1-2-login"]


def test_story_status_canonical_wins_by_mtime(tmp_path, monkeypatch):
    # Both canonical and legacy present → newest mtime wins.
    import time
    legacy = tmp_path / "bmad-output/implementation-artifacts"
    legacy.mkdir(parents=True)
    (legacy / "sprint-status.yaml").write_text(
        "stories:\n  1-2-old: in-progress\n", encoding="utf-8")
    old = time.time() - 3600
    os.utime(legacy / "sprint-status.yaml", (old, old))
    cand = tmp_path / "docs/development/native"
    cand.mkdir(parents=True)
    (cand / "sprint-status.yaml").write_text(
        "stories:\n  9-9-new: in-progress\n", encoding="utf-8")
    _env(tmp_path, monkeypatch)
    assert _in_progress_stories(str(tmp_path)) == ["9-9-new"]


def test_story_status_all_done_empty(tmp_path, monkeypatch):
    cand = tmp_path / "_bmad-output/implementation-artifacts"
    cand.mkdir(parents=True)
    (cand / "sprint-status.yaml").write_text(
        "stories:\n  1-2-login: done\n", encoding="utf-8")
    _env(tmp_path, monkeypatch)
    assert _in_progress_stories(str(tmp_path)) == []


def test_story_status_metodoloji_fallback(tmp_path, monkeypatch):
    cand = tmp_path / ".metodoloji"
    cand.mkdir(parents=True)
    (cand / "sprint-status.yaml").write_text(
        "stories:\n  5-6-auth: in-progress\n", encoding="utf-8")
    _env(tmp_path, monkeypatch)
    assert _in_progress_stories(str(tmp_path)) == ["5-6-auth"]


def test_story_status_stale_ignored(tmp_path, monkeypatch):
    # sprint-status older than the session marker is a brownfield leftover.
    import time
    cand = tmp_path / ".metodoloji"
    cand.mkdir(parents=True)
    (cand / "sprint-status.yaml").write_text(
        "stories:\n  1-2-login: in-progress\n", encoding="utf-8")
    old = time.time() - 3600
    os.utime(cand / "sprint-status.yaml", (old, old))
    _env(tmp_path, monkeypatch)
    _seed_session_start(tmp_path)  # marker newer than the stale status
    assert _in_progress_stories(str(tmp_path)) == []


def test_story_status_no_marker_reads_file(tmp_path, monkeypatch):
    # No session marker (old bootstrap) → file read as-is.
    cand = tmp_path / ".metodoloji"
    cand.mkdir(parents=True)
    (cand / "sprint-status.yaml").write_text(
        "stories:\n  1-2-login: in-progress\n", encoding="utf-8")
    _env(tmp_path, monkeypatch)
    assert _in_progress_stories(str(tmp_path)) == ["1-2-login"]


# --- stop(): always allow --------------------------------------------------------

def test_stop_allows_clean_tree(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    res = stop({})
    assert res["decision"] == "allow"
    assert "reason" not in res


def test_stop_allows_free_zone_code(tmp_path, monkeypatch):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "explore.py").write_text("x = 1\n", encoding="utf-8")
    _env(tmp_path, monkeypatch)
    _seed_audit_log(tmp_path, [
        {"tool": "file_editor", "input": {"path": "scratch/explore.py"}},
    ])
    res = stop({})
    assert res["decision"] == "allow"


def test_stop_reports_unapproved_code_instead_of_denying(tmp_path, monkeypatch):
    # Unapproved code is the guard's job at write time; stop only reports it.
    from modules import config
    monkeypatch.setattr(config, "hook_gate_mode", lambda key: "hard")
    (tmp_path / "src").mkdir()
    (tmp_path / "src/main.py").write_text("print(1)\n", encoding="utf-8")
    _env(tmp_path, monkeypatch)
    _seed_audit_log(tmp_path, [
        {"tool": "file_editor", "input": {"path": "src/main.py"}},
    ])
    res = stop({})
    assert res["decision"] == "allow"
    assert "src/main.py" in res.get("reason", "")


def test_stop_reports_in_progress_story(tmp_path, monkeypatch):
    cand = tmp_path / ".metodoloji"
    cand.mkdir(parents=True)
    (cand / "sprint-status.yaml").write_text(
        "stories:\n  1-2-login: in-progress\n", encoding="utf-8")
    _env(tmp_path, monkeypatch)
    res = stop({})
    assert res["decision"] == "allow"
    assert "1-2-login" in res.get("reason", "")


def test_stop_hook_active_allows(tmp_path, monkeypatch):
    # stop_hook_active=true (Claude re-fire) must close SILENTLY: any reason
    # becomes additionalContext, Claude continues the turn, Stop re-fires —
    # an infinite loop. No reason, no extra session_stop marker either.
    (tmp_path / "src").mkdir()
    (tmp_path / "src/main.py").write_text("print(1)\n", encoding="utf-8")
    _env(tmp_path, monkeypatch)
    _seed_audit_log(tmp_path, [
        {"tool": "file_editor", "input": {"path": "src/main.py"}},
    ])
    log = tmp_path / ".metodoloji/logs/hook-audit.log"
    before = log.read_text(encoding="utf-8")
    res = stop({"stop_hook_active": True})
    assert res["decision"] == "allow"
    assert "reason" not in res
    assert log.read_text(encoding="utf-8") == before


def test_stop_hook_active_allows_with_stories_pending(tmp_path, monkeypatch):
    # Even with in-progress stories, a re-fired Stop stays silent.
    cand = tmp_path / ".metodoloji"
    cand.mkdir(parents=True)
    (cand / "sprint-status.yaml").write_text(
        "stories:\n  1-2-login: in-progress\n", encoding="utf-8")
    _env(tmp_path, monkeypatch)
    res = stop({"stop_hook_active": True})
    assert res == {"decision": "allow"}


def test_stop_skips_free_zone_writes(tmp_path, monkeypatch):
    # Regression (live find): an init-only session whose sole write is
    # .metodoloji/initialized reported "code written this session:
    # .metodoloji/initialized" — a free-zone file is never code.
    from modules.stop import _session_code_writes
    _env(tmp_path, monkeypatch)
    _seed_audit_log(tmp_path, [
        {"tool": "file_editor", "input": {"path": ".metodoloji/initialized"}},
        {"tool": "file_editor", "input": {"path": "scratch/explore.py"}},
        {"tool": "file_editor", "input": {"path": "docs/experiments/E-001.md"}},
    ])
    assert _session_code_writes(str(tmp_path)) == []
    res = stop({})
    assert res["decision"] == "allow"
    assert "reason" not in res


def _report_records(root):
    log = root / ".metodoloji/logs/hook-audit.log"
    return [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]


def test_stop_report_emitted_once_per_session(tmp_path, monkeypatch):
    """The report is a wrap-up nudge, not a per-turn watchdog.

    Live session (graph-engineering-arge, 2026-10-01): the identical report
    fired three times ("Ran 2 stop hooks" each turn) and each repeat cost a
    wrap-up turn. Returning a reason makes the model continue → Stop re-fires;
    stop_hook_active covers the documented path, but a multi-turn session or a
    duplicate registration re-enters without it.
    """
    (tmp_path / "src").mkdir()
    (tmp_path / "src/main.py").write_text("print(1)\n", encoding="utf-8")
    _env(tmp_path, monkeypatch)
    _seed_session_start(tmp_path, session_id="sess-a")
    _seed_audit_log(tmp_path, [
        {"tool": "file_editor", "input": {"path": "src/main.py"}},
    ])
    first = stop({})
    assert "src/main.py" in first.get("reason", "")
    assert "earlier this session" in first["reason"]  # not "this turn"
    second = stop({})
    assert second == {"decision": "allow"}, second
    # The session_stop marker still lands on every call — only the nudge is deduped.
    assert sum(1 for r in _report_records(tmp_path)
               if r.get("type") == "session_stop") == 2


def test_stop_report_fires_again_for_a_new_session(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    for name in ("main.py", "other.py"):
        (tmp_path / "src" / name).write_text("print(1)\n", encoding="utf-8")
    _env(tmp_path, monkeypatch)
    _seed_session_start(tmp_path, session_id="sess-a")
    _seed_audit_log(tmp_path, [
        {"tool": "file_editor", "input": {"path": "src/main.py"}},
    ])
    assert "src/main.py" in stop({}).get("reason", "")
    # A new session marker re-opens the report for this session's own writes.
    _seed_session_start(tmp_path, session_id="sess-b")
    _seed_audit_log(tmp_path, [
        {"tool": "file_editor", "input": {"path": "src/other.py"}},
    ])
    res = stop({})
    assert "src/other.py" in res.get("reason", "")


def test_stop_report_without_a_session_marker_is_not_deduped(tmp_path, monkeypatch):
    """A session we cannot identify must not have its report suppressed."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src/main.py").write_text("print(1)\n", encoding="utf-8")
    _env(tmp_path, monkeypatch)
    _seed_audit_log(tmp_path, [
        {"tool": "file_editor", "input": {"path": "src/main.py"}},
    ])
    assert "src/main.py" in stop({}).get("reason", "")
    assert "src/main.py" in stop({}).get("reason", "")  # legacy: no id, no dedupe


def test_stop_stamps_stop_marker(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    stop({})
    log = tmp_path / ".metodoloji/logs/hook-audit.log"
    recs = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    assert any(r.get("type") == "session_stop" for r in recs)


def test_stop_previous_session_touches_ignored(tmp_path, monkeypatch):
    # Touches before the session_start marker don't count: yesterday's
    # work must not pollute today's report.
    (tmp_path / "src").mkdir()
    (tmp_path / "src/old.py").write_text("x=1\n", encoding="utf-8")
    _env(tmp_path, monkeypatch)
    _seed_audit_log(tmp_path, [
        {"tool": "file_editor", "input": {"path": "src/old.py"}},
    ])
    _seed_session_start(tmp_path)
    res = stop({})
    assert res["decision"] == "allow"
    assert "old.py" not in res.get("reason", "")


def test_stop_ignores_shell_variable_targets(tmp_path, monkeypatch):
    # Regression (live find): a heredoc rewrite command containing
    # "$spool_file" must not produce a literal "$spool_file" entry.
    from modules.stop import _session_code_writes
    _env(tmp_path, monkeypatch)
    _seed_audit_log(tmp_path, [
        {"tool": "terminal", "input": {"command": "cat > $spool_file << 'EOF'\nx\nEOF"}},
        {"tool": "file_editor", "input": {"path": "$out/main.py"}},
    ])
    assert _session_code_writes(str(tmp_path)) == []


def test_stop_allows_preexisting_brownfield_code(tmp_path, monkeypatch):
    # Regression: files that exist on disk but were NOT touched this session
    # (no audit-log record) are not reported.
    (tmp_path / "src").mkdir()
    (tmp_path / "src/main.py").write_text("print(1)\n", encoding="utf-8")
    (tmp_path / "prisma.config.ts").write_text("export default {}\n", encoding="utf-8")
    _env(tmp_path, monkeypatch)
    res = stop({})
    assert res["decision"] == "allow"
    assert "main.py" not in res.get("reason", "")


def test_stop_tail_bound_respected(tmp_path, monkeypatch):
    # _SESSION_TAIL_LINES bounds the read: stop stays O(session) — only the
    # newest lines are considered, older history is cut.
    import sys
    stop_mod = sys.modules["modules.stop"]
    monkeypatch.setattr(stop_mod, "_SESSION_TAIL_LINES", 5)
    _env(tmp_path, monkeypatch)
    _seed_audit_log(tmp_path, [
        {"tool": "file_editor", "input": {"path": "src/old.py"}},
    ] * 3 + [
        {"tool": "file_editor", "input": {"path": "src/new.py"}},
    ] * 5)
    assert stop_mod._session_code_writes(str(tmp_path)) == ["src/new.py"]


# --- GRP plan progress in the stop report (E-066) ----------------------------

def _seed_plan_record(root, eid="E-066", planned="src/a.py, src/b.py",
                      with_plan=True):
    recs = root / "docs" / "experiments"
    recs.mkdir(parents=True, exist_ok=True)
    plan_block = ("\n## Implementation Plan (GRP)\n\n"
                  f"- **Planned Files:** {planned}\n"
                  "- **Amendments:** none\n") if with_plan else ""
    (recs / f"{eid}.md").write_text(
        f"## Experiment: {eid} — plan\n- **Status:** planned\n"
        "- **Code Scope:** none\n" + plan_block,
        encoding="utf-8")


def test_stop_reports_plan_progress(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/a.py").write_text("x = 1\n", encoding="utf-8")
    _seed_plan_record(tmp_path)
    _env(tmp_path, monkeypatch)
    _seed_session_start(tmp_path, session_id="sess-plan")
    _seed_audit_log(tmp_path, [
        {"tool": "file_editor", "input": {"path": "src/a.py"}},
    ])
    res = stop({})
    assert res["decision"] == "allow"
    reason = res.get("reason", "")
    assert "plan progress (GRP)" in reason
    assert "E-066.md" in reason
    assert "1/2" in reason and "1 pending" in reason
    # informational only — the note never claims approval (E-066 contract)
    assert "verified" not in reason.lower()
    assert "approved" not in reason.lower()


def test_stop_no_plan_note_without_a_plan(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/a.py").write_text("x = 1\n", encoding="utf-8")
    _seed_plan_record(tmp_path, with_plan=False)
    _env(tmp_path, monkeypatch)
    _seed_session_start(tmp_path, session_id="sess-noplan")
    _seed_audit_log(tmp_path, [
        {"tool": "file_editor", "input": {"path": "src/a.py"}},
    ])
    reason = stop({}).get("reason", "")
    assert "src/a.py" in reason          # code-writes note still fires
    assert "plan progress" not in reason


def test_stop_no_plan_note_for_free_zone_writes(tmp_path, monkeypatch):
    (tmp_path / "scratch").mkdir()
    _seed_plan_record(tmp_path)
    _env(tmp_path, monkeypatch)
    _seed_session_start(tmp_path, session_id="sess-free")
    _seed_audit_log(tmp_path, [
        {"tool": "file_editor", "input": {"path": "scratch/x.py"}},
    ])
    assert stop({}) == {"decision": "allow"}


def _main_stop(project_root, payload):
    import subprocess
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(project_root)
    return subprocess.run(
        [sys.executable, str(MAIN_PY), "stop"],
        input=json.dumps(payload), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30, env=env,
        cwd=str(_HOOKS.parent))


def test_main_stop_carries_plan_progress_envelope(tmp_path):
    """main.py stop maps the plan note into the Stop additionalContext and
    never emits a block decision."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src/a.py").write_text("x = 1\n", encoding="utf-8")
    _seed_plan_record(tmp_path, eid="E-066")
    _seed_session_start(tmp_path, session_id="sess-env")
    _seed_audit_log(tmp_path, [
        {"tool": "file_editor", "input": {"path": "src/a.py"}},
    ])
    r = _main_stop(tmp_path, {"cwd": str(tmp_path)})
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["hookEventName"] == "Stop"
    assert "plan progress" in out["hookSpecificOutput"].get("additionalContext", "")
    assert out.get("decision") != "block"


# --- main() dispatch --------------------------------------------------------
# ponytail: subprocess dispatch tests live here (engine entry contract),
# not in a new file — one place for "main.py speaks the hook schema".

def test_main_bad_stdin_stop_blocks(tmp_path):
    import subprocess
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(tmp_path)
    env["HOOK_TYPE"] = "stop"
    r = subprocess.run(
        [sys.executable, str(MAIN_PY)],
        input="not-json",
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        env=env, cwd=str(_HOOKS.parent),
    )
    out = json.loads(r.stdout)
    assert out["decision"] == "block"


def test_main_bad_stdin_guard_denies(tmp_path):
    import subprocess
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(tmp_path)
    env["HOOK_TYPE"] = "guard"
    r = subprocess.run(
        [sys.executable, str(MAIN_PY)],
        input="not-json",
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        env=env, cwd=str(_HOOKS.parent),
    )
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_main_session_start_returns_context(tmp_path):
    import subprocess
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(tmp_path)
    r = subprocess.run(
        [sys.executable, str(MAIN_PY), "session_start"],
        input=json.dumps({}),
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        env=env, cwd=str(_HOOKS.parent),
    )
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "METODOLOJI" in out["hookSpecificOutput"].get("additionalContext", "")

def _run_main(args, stdin_data, project_root=None):
    """Run main.py in a subprocess.

    project_root pins CLAUDE_PROJECT_DIR for the child. Without it the
    engine resolves no project root and stamps state next to cwd — which
    polluted the PLUGIN tree (hooks/.metodoloji/) instead of a sandbox.
    """
    env = dict(os.environ)
    if project_root is not None:
        env["CLAUDE_PROJECT_DIR"] = str(project_root)
    return subprocess.run(
        [sys.executable, str(MAIN_PY), *args],
        input=json.dumps(stdin_data),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env=env,
        cwd=str(_HOOKS.parent),
    )


def test_main_dispatch_guard(tmp_path):
    r = _run_main(["guard"], {"tool_name": "terminal", "tool_input": {"command": "ls"}},
                  project_root=tmp_path)
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_main_dispatch_pre(tmp_path):
    """The merged PreToolUse mode returns one decision for a Bash/terminal call."""
    r = _run_main(["pre"], {"tool_name": "terminal", "tool_input": {"command": "ls"}},
                  project_root=tmp_path)
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_main_bad_stdin_pre_denies(tmp_path):
    """pre is fail-closed like guard: unparseable input must never allow."""
    import subprocess
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(tmp_path)
    env["HOOK_TYPE"] = "pre"
    r = subprocess.run(
        [sys.executable, str(MAIN_PY)],
        input="not-json",
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        env=env, cwd=str(_HOOKS.parent),
    )
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_main_dispatch_unknown_hook_allows(tmp_path):
    r = _run_main(["nonexistent-hook"], {"tool_name": "terminal"},
                  project_root=tmp_path)
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_main_dispatch_bad_stdin_allows():
    r = subprocess.run(
        [sys.executable, str(MAIN_PY)],
        input="not-json",
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        cwd=str(_HOOKS.parent),
    )
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_main_dispatch_runtime_flag(tmp_path, monkeypatch):
    # --runtime=openhands must be accepted and the engine must still decide.
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(tmp_path)
    r = subprocess.run(
        [sys.executable, str(MAIN_PY), "--runtime=openhands", "guard"],
        input=json.dumps({"tool_name": "terminal", "tool_input": {"command": "ls"}}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        env=env,
        cwd=str(_HOOKS.parent),
    )
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_main_dispatch_quality_non_commit_allows(tmp_path):
    r = _run_main(["quality"], {"tool_name": "terminal",
                                "tool_input": {"command": "ls -la"}},
                  project_root=tmp_path)
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_main_dispatch_stop_allows_with_report(tmp_path):
    """Stop via main.py: allow + Stop envelope, never block."""
    (tmp_path / "src").mkdir()
    r = _run_main(["stop"], {"cwd": str(tmp_path)},
                  project_root=tmp_path)
    out = json.loads(r.stdout)
    assert out.get("decision") in (None, "allow") or "decision" not in out
    assert out["hookSpecificOutput"]["hookEventName"] == "Stop"
    assert out["hookSpecificOutput"].get("additionalContext") is None or isinstance(
        out["hookSpecificOutput"].get("additionalContext"), str)


# --- main() input trust boundary (E-002) ------------------------------------
# The fail-closed contract covers BOTH bad-input classes: unparseable JSON
# (covered above) and VALID JSON that is not an object. The second class used
# to reach the handler and raise inside normalize_hook_input
# ("'list' object has no attribute 'get'"), printing a traceback and NO
# decision — a crashed PreToolUse hook the runner may read as an allow.

_NON_OBJECT_STDIN = ("[]", '"x"', "null", "5")


def _main_with_stdin(mode, payload, project_root):
    import subprocess
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(project_root)
    return subprocess.run(
        [sys.executable, str(MAIN_PY), mode],
        input=payload, capture_output=True, text=True, encoding="utf-8",
        timeout=30, env=env, cwd=str(_HOOKS.parent),
    )


def test_main_non_object_stdin_fail_closed(tmp_path):
    """Non-object JSON must deny/block on every fail-closed hook, never crash."""
    checkers = {
        "stop": lambda o: o["decision"] == "block",
        "guard": lambda o: o["hookSpecificOutput"]["permissionDecision"] == "deny",
        "pre": lambda o: o["hookSpecificOutput"]["permissionDecision"] == "deny",
    }
    for payload in _NON_OBJECT_STDIN:
        for mode, ok in checkers.items():
            r = _main_with_stdin(mode, payload, tmp_path)
            assert r.returncode == 0, (mode, payload, r.stderr)
            out = json.loads(r.stdout)
            assert ok(out), (mode, payload, out)


def test_main_non_object_stdin_open_hook_allows(tmp_path):
    """A non-blocking hook keeps its fail-open policy on non-object JSON."""
    r = _main_with_stdin("audit", "[]", tmp_path)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_main_malformed_shapes_never_crash_decide_instead(tmp_path):
    """E-003: dict payloads with anti-shape routing values must return a
    decision on every hook mode — never a traceback/exit≠0.

    The deny on the malformed path is the CONTRACT here (the value "123"
    resolves outside the sandbox project), deny-over-allow is guaranteed by
    utils._coerce_tool_input; the pinned half is the no-crash/always-decide
    half. Free-zone shape (scratch/) still allows.
    """
    cases = {
        "guard": [
            {"tool_name": "terminal", "tool_input": "ls"},
            {"tool_name": "terminal", "tool_input": ["ls"]},
            {"tool_name": "terminal", "tool_input": {"command": ["ls"]}},
            {"tool_name": "file_editor", "tool_input": {"path": 123}},
        ],
        "stop": [{"cwd": str(tmp_path), "tool_input": "weird"}],
        "pre": [{"tool_name": "terminal", "tool_input": "ls"}],
        "audit": [{"tool_input": ["x"]}],
    }
    for mode, payloads in cases.items():
        for payload in payloads:
            r = _main_with_stdin(mode, json.dumps(payload), tmp_path)
            assert r.returncode == 0, (mode, payload, r.stderr)
            out = json.loads(r.stdout)
            assert "hookEventName" in out.get("hookSpecificOutput", {}), (mode, payload)


def test_main_malformed_path_still_fail_closed_not_crash(tmp_path):
    """The deny-decision half: a malformed numeric path must DENY (not crash,
    not allow) — the fail-closed contract survives the coercion."""
    r = _main_with_stdin(
        "guard",
        json.dumps({"tool_name": "file_editor", "tool_input": {"path": 123}}),
        tmp_path,
    )
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_main_malformed_free_zone_command_still_allows(tmp_path):
    """Coercion must not over-block: a real command string in a free-zone
    write still allows (guard behavior unchanged on well-formed shapes)."""
    r = _main_with_stdin(
        "guard",
        json.dumps({"tool_name": "file_editor",
                    "tool_input": {"path": str(tmp_path / "scratch" / "a.py"),
                                   "content": "x=1"}}),
        tmp_path,
    )
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"


# --- top-level payload shape (E-006) -----------------------------------------
# `cwd` is read by utils.repo_root() straight off the wire. A mistyped one
# (123 / ["x"] / {"a": 1}) used to raise inside _msys_to_native(): exit 1,
# empty stdout, NO decision — a no-decision turn the runner may read as an
# allow. hook-entry.sh only `_fail`s when python/engine is missing, so the
# engine itself must always decide.

_MALFORMED_CWD_MODES = ("guard", "pre", "quality", "deploy", "audit", "stop",
                        "session_start")


def test_main_malformed_cwd_decides_not_crash(tmp_path):
    """E-006: with both project-dir env vars absent, `cwd` is the only root
    signal, so this payload probes the seam on every hook mode: each must
    return rc=0 with a parseable decision envelope — never a traceback.

    The subprocess cwd is tmp_path so the fallback root (process cwd) lands in
    the sandbox, not in the plugin tree.
    """
    import subprocess
    env = dict(os.environ)
    env.pop("CLAUDE_PROJECT_DIR", None)
    env.pop("OPENHANDS_PROJECT_DIR", None)
    payload = json.dumps({"tool_name": "terminal",
                          "tool_input": {"command": "ls"}, "cwd": 123})
    for mode in _MALFORMED_CWD_MODES:
        r = subprocess.run(
            [sys.executable, str(MAIN_PY), mode],
            input=payload, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30, env=env, cwd=str(tmp_path),
        )
        assert r.returncode == 0, (mode, r.stderr)
        assert "Traceback" not in r.stderr, (mode, r.stderr)
        out = json.loads(r.stdout)  # a decision envelope, not empty stdout
        assert "hookEventName" in out.get("hookSpecificOutput", {}), (mode, out)


# --- recursion-class input (E-007) -------------------------------------------
# Three sites, one policy: a deep or crashing turn must END in a decision.
#  1) json.load raised RecursionError past the parser limit (~20k) — uncaught
#     by (JSONDecodeError, ValueError, EOFError) -> ALL SEVEN modes rc=1.
#  2) a routing value nested >=500 deep recursed without bound in
#     _coerce_json_scalar -> guard/pre/quality/deploy/audit rc=1 (fail-open).
#  3) any other handler exception escaped main()'s unguarded handler call.

_ALL_HOOK_MODES = ("guard", "pre", "quality", "deploy", "audit", "stop",
                   "session_start")


def _deep_command_raw(depth):
    """A raw stdin payload whose command is `depth` nested lists."""
    inner = '"ls"'
    for _ in range(depth):
        inner = "[" + inner + "]"
    return '{"tool_name": "terminal", "tool_input": {' + \
           '"command": ' + inner + '}}'


def _run_mode_raw(mode, raw, project_root):
    env = dict(os.environ)
    env.pop("CLAUDE_PROJECT_DIR", None)
    env.pop("OPENHANDS_PROJECT_DIR", None)
    env["HOOK_TYPE"] = mode
    return subprocess.run(
        [sys.executable, str(MAIN_PY), mode],
        input=raw, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=60, env=env, cwd=str(project_root),
    )


def _assert_decides(mode, r):
    assert r.returncode == 0, (mode, r.stderr[-400:])
    assert "Traceback" not in r.stderr, (mode, r.stderr[-400:])
    out = json.loads(r.stdout)  # a decision envelope, not empty stdout
    assert "hookEventName" in out.get("hookSpecificOutput", {}), (mode, out)


def test_main_deep_coercion_payload_decides_not_crash(tmp_path):
    """Depth-1000 command parses, then hits the coerce seam: every mode must
    still decide (pre-E-007 the five coercing modes exited 1 with no output)."""
    raw = _deep_command_raw(1000)
    for mode in _ALL_HOOK_MODES:
        _assert_decides(mode, _run_mode_raw(mode, raw, tmp_path))


def test_main_parser_overflow_payload_decides_not_crash(tmp_path):
    """Depth-20000: json.load itself raises RecursionError — uncaught on every
    mode before E-007. The input boundary must absorb it as bad input."""
    raw = _deep_command_raw(20000)
    for mode in _ALL_HOOK_MODES:
        _assert_decides(mode, _run_mode_raw(mode, raw, tmp_path))


def test_main_fail_closed_hooks_deny_on_parser_overflow(tmp_path):
    """The deny half of the parser contract: the recursion class behaves like
    bad input — stop blocks, guard/pre deny (never allow, never crash)."""
    raw = _deep_command_raw(20000)
    for mode, want in (("stop", "block"), ("guard", "deny"), ("pre", "deny")):
        r = _run_mode_raw(mode, raw, tmp_path)
        assert r.returncode == 0, (mode, r.stderr[-400:])
        out = json.loads(r.stdout)
        if want == "block":
            assert out.get("decision") == "block", (mode, out)
        else:
            assert out["hookSpecificOutput"]["permissionDecision"] == "deny", (mode, out)


def test_main_handler_crash_still_decides(monkeypatch, capsys):
    """E-007 boundary: an escaped handler exception mirrors the input policy —
    stop/guard/pre fail-closed, the report-only hooks allow — never a
    traceback/no-decision turn (main.py's handler call used to be unguarded)."""
    import io
    import main as engine_main

    def _boom(payload):
        raise RuntimeError("handler exploded")

    payload = json.dumps({"tool_name": "terminal",
                          "tool_input": {"command": "ls"}})
    for mode, check in (
        ("guard", lambda o: o["hookSpecificOutput"]["permissionDecision"] == "deny"),
        ("pre", lambda o: o["hookSpecificOutput"]["permissionDecision"] == "deny"),
        ("stop", lambda o: o.get("decision") == "block"),
        ("quality", lambda o: o["hookSpecificOutput"]["permissionDecision"] == "allow"),
        ("deploy", lambda o: o["hookSpecificOutput"]["permissionDecision"] == "allow"),
        ("audit", lambda o: o["hookSpecificOutput"]["hookEventName"] == "PostToolUse"),
    ):
        monkeypatch.setattr(engine_main, "_load_handler", lambda ht: _boom)
        monkeypatch.setenv("HOOK_TYPE", mode)
        # pytest's own argv carries positional args _resolve_hook_type would
        # read as a mode — pin argv to a bare engine invocation.
        monkeypatch.setattr(sys, "argv", ["main.py"])
        monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
        capsys.readouterr()  # drain
        engine_main.main()
        out = json.loads(capsys.readouterr().out)
        assert check(out), (mode, out)
        assert "hookEventName" in out.get("hookSpecificOutput", {}), (mode, out)

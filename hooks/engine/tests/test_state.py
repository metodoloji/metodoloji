"""Tests for hooks/engine/modules/state.py — the single read-only state source.

Three surfaces used to answer "where are we" from three parsers (the
mailjs OpenHands session, 2026-09-23, grounded in the model's memory
instead and re-derived its whole state): record counting, sprint-status
parsing and the session-start sentence now all delegate here. These tests
falsify the ways a single source can still lie: epic keys read as stories,
`10-…` joining epic 1, absent files claimed as found, and the inject line
overflowing its bounded budget.
"""

import json
import os
import sys
import time
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HOOKS))

from modules import state  # noqa: E402


# --- record_inventory ----------------------------------------------------------

def test_record_inventory_counts_kinds_and_skips_other_files(tmp_path):
    dev = tmp_path / "docs" / "development"
    dev.mkdir(parents=True)
    (dev / "IR-001.md").write_text("x", encoding="utf-8")
    (dev / "SP-001.md").write_text("x", encoding="utf-8")
    (dev / "SP-002.md").write_text("x", encoding="utf-8")
    (dev / "QR-009.md").write_text("x", encoding="utf-8")  # legacy QR home
    (dev / "README.md").write_text("x", encoding="utf-8")  # not a record
    inv = state.record_inventory(tmp_path)
    assert inv["IR"]["count"] == 1 and inv["IR"]["newest"] == "IR-001.md"
    assert inv["SP"]["count"] == 2 and inv["SP"]["newest"] == "SP-002.md"
    assert inv["QR"]["count"] == 1
    assert inv["QR"]["dirs"] == ["docs/development"]  # legacy home named
    assert "S" not in inv and "E" not in inv  # nothing invented


def test_record_inventory_empty_project_is_empty_dict(tmp_path):
    assert state.record_inventory(tmp_path) == {}


def test_record_inventory_counts_title_slugged_records(tmp_path):
    """Slugged records are records: the gate writes `E-056-gate-heading.md`.

    A bare `^E-N.md$` pattern made every slugged record invisible to routing
    (five E records reported as "none yet" on the plugin's own repo).
    """
    exp = tmp_path / "docs" / "experiments"
    exp.mkdir(parents=True)
    (exp / "E-056-gate-heading-guard.md").write_text("x", encoding="utf-8")
    (exp / "E-001.md").write_text("x", encoding="utf-8")
    (exp / "_template.md").write_text("x", encoding="utf-8")  # not a record
    stories = tmp_path / "docs" / "development" / "stories"
    stories.mkdir(parents=True)
    (stories / "S-056-gate-id-guard.md").write_text("x", encoding="utf-8")
    inv = state.record_inventory(tmp_path)
    assert inv["E"]["count"] == 2
    assert "S" in inv and inv["S"]["count"] == 1


# --- sprint_summary -------------------------------------------------------------

def _write_sprint(tmp_path, body):
    d = tmp_path / "docs" / "development" / "native"
    d.mkdir(parents=True, exist_ok=True)
    (d / "sprint-status.yaml").write_text(body, encoding="utf-8")


def test_sprint_summary_absent_file_says_so(tmp_path):
    s = state.sprint_summary(tmp_path)
    assert s["found"] is False
    assert s["reason"] == "no sprint-status file"
    assert "docs/development/native/sprint-status.yaml" in s["paths_checked"]


def test_sprint_summary_empty_file_reports_no_entries(tmp_path):
    _write_sprint(tmp_path, "generated: 2026-09-23\nproject: x\n")
    s = state.sprint_summary(tmp_path)
    assert s["found"] is False
    assert "no development_status entries" in s["reason"]


def test_sprint_summary_separates_story_and_epic_keys(tmp_path):
    _write_sprint(tmp_path,
                  "development_status:\n"
                  "  epic-6: in-progress\n"
                  "  6-1-sec: done\n"
                  "  6-3-quar: in-progress\n")
    s = state.sprint_summary(tmp_path)
    assert s["found"] is True
    assert s["epics"] == {"epic-6": "in-progress"}
    assert s["stories"] == {"6-1-sec": "done", "6-3-quar": "in-progress"}
    assert s["epic_progress"]["epic-6"] == {"done": 1, "total": 2, "started": 2}


def test_sprint_summary_digit_boundary_ten_never_joins_epic_one(tmp_path):
    _write_sprint(tmp_path,
                  "development_status:\n"
                  "  epic-1: done\n"
                  "  1-1-a: done\n"
                  "  epic-10: done\n"
                  "  10-1-x: done\n")
    s = state.sprint_summary(tmp_path)
    assert s["epic_progress"]["epic-1"] == {"done": 1, "total": 1, "started": 1}
    assert s["epic_progress"]["epic-10"] == {"done": 1, "total": 1, "started": 1}


def test_sprint_summary_comment_legend_lines_are_not_entries(tmp_path):
    # The shipped template's legend documented the vocabulary in comments;
    # a bare grep counted those as stories (check-methodology §6 drift bug #1).
    _write_sprint(tmp_path,
                  "#   - in-progress: Developer actively working\n"
                  "#   - done: Story completed\n"
                  "development_status:\n"
                  "  1-1-a: done\n")
    s = state.sprint_summary(tmp_path)
    assert s["stories"] == {"1-1-a": "done"}
    assert "in-progress" not in s["counts"]


def test_sprint_summary_epic_lag_flags_are_in_the_line(tmp_path):
    # The mailjs shape: epic claimed in-progress while every story is done.
    _write_sprint(tmp_path,
                  "development_status:\n"
                  "  epic-1: in-progress\n"
                  "  1-1-a: done\n"
                  "  1-2-b: done\n")
    line = state.format_sprint_line(state.sprint_summary(tmp_path))
    assert "epic-1" in line and "roll up to done" in line


def test_sprint_line_planned_sprint_is_not_flagged(tmp_path):
    # The bmad-sprint-planning close (2026-10-01 scratch run): every epic reads
    # `backlog` — the template default, correct until the first story lands —
    # while their stories are listed. That is a planned sprint, not a lag, so
    # no epic may be flagged (the story counts themselves stay).
    _write_sprint(tmp_path,
                  "development_status:\n"
                  "  epic-1: backlog\n"
                  "  1-1-a: backlog\n"
                  "  1-2-b: backlog\n"
                  "  epic-1-retrospective: optional\n"
                  "  epic-2: backlog\n"
                  "  2-1-c: backlog\n")
    line = state.format_sprint_line(state.sprint_summary(tmp_path))
    assert "⚠" not in line
    assert "backlog" in line


def test_sprint_line_flags_epic_backlog_with_started_story(tmp_path):
    # Same shape, one story advanced: now the epic container really is behind.
    _write_sprint(tmp_path,
                  "development_status:\n"
                  "  epic-1: backlog\n"
                  "  1-1-a: ready-for-dev\n"
                  "  1-2-b: backlog\n")
    line = state.format_sprint_line(state.sprint_summary(tmp_path))
    assert "epic-1" in line and "bump the epic" in line


# --- mcp_inventory ------------------------------------------------------------


def _home(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    return tmp_path


def test_mcp_inventory_reads_project_and_user_sources(tmp_path, monkeypatch):
    _home(tmp_path / "home", monkeypatch)
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {
            "github": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"]},
            "broken": {"command": "x", "disabled": True},
        }}), encoding="utf-8")
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "config.toml").write_text(
        "[mcp_servers.docs]\ncommand = \"python\"\nargs = [\"-m\", \"mcp_server_docs\"]\n",
        encoding="utf-8")
    (home / ".openhands").mkdir(parents=True)
    (home / ".openhands" / "mcp.json").write_text(
        json.dumps({"mcpServers": {"browser": {"url": "http://127.0.0.1:8931/mcp"}}}),
        encoding="utf-8")
    inv = state.mcp_inventory(tmp_path)
    names = [s["name"] for s in inv["servers"]]
    assert "github" in names and "broken" in names and "docs" in names
    assert "browser" in names  # the first-class runtime's user-level file
    oh = next(s for s in inv["servers"] if s["name"] == "browser")
    assert oh["harness"] == "openhands" and oh["source"].endswith(".openhands/mcp.json")
    assert inv["count"] == 4
    gh = next(s for s in inv["servers"] if s["name"] == "github")
    assert gh["enabled"] is True and gh["transport"] == ""  # never invented
    assert "github" in gh["target"] or "npx" in gh["target"]
    assert inv["sources_checked"] == ["./.mcp.json", "./.vscode/mcp.json",
                                      "./.cursor/mcp.json", "~/.claude.json",
                                      "~/.codex/config.toml", "~/.openhands/mcp.json",
                                      "~/.cursor/mcp.json"]


def test_mcp_inventory_disabled_last_and_flagged(tmp_path, monkeypatch):
    _home(tmp_path / "home", monkeypatch)
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {
        "zeta": {"command": "a"}, "beta": {"command": "b", "disabled": True},
        "alpha": {"command": "c"},
    }}), encoding="utf-8")
    inv = state.mcp_inventory(tmp_path)
    order = [(s["name"], s["enabled"]) for s in inv["servers"]]
    assert order == [("alpha", True), ("zeta", True), ("beta", False)]


def test_mcp_inventory_vscode_servers_shape_and_malformed_json(tmp_path, monkeypatch):
    _home(tmp_path / "home", monkeypatch)
    (tmp_path / ".vscode").mkdir()
    (tmp_path / ".vscode" / "mcp.json").write_text(
        json.dumps({"servers": {"fs": {"command": "mcp-server-fs"}}}), encoding="utf-8")
    (tmp_path / ".cursor").mkdir()
    (tmp_path / ".cursor" / "mcp.json").write_text("{not json", encoding="utf-8")
    inv = state.mcp_inventory(tmp_path)
    assert [s["name"] for s in inv["servers"]] == ["fs"]  # malformed source hides only itself
    assert inv["count"] == 1


def test_mcp_inventory_empty_project_names_sources_checked(tmp_path, monkeypatch):
    _home(tmp_path / "home", monkeypatch)
    inv = state.mcp_inventory(tmp_path)
    assert inv == {"servers": [], "count": 0,
                   "sources_checked": ["./.mcp.json", "./.vscode/mcp.json",
                                       "./.cursor/mcp.json", "~/.claude.json",
                                       "~/.codex/config.toml", "~/.openhands/mcp.json",
                                       "~/.cursor/mcp.json"]}


def test_mcp_inventory_reads_claude_local_scope_for_this_project_only(tmp_path, monkeypatch):
    """`claude mcp add` (default local scope) writes under ~/.claude.json
    'projects'.{path}.mcpServers — reachable for THIS project, invisible for
    every other project's session."""
    home = _home(tmp_path / "home", monkeypatch)
    home.mkdir(parents=True, exist_ok=True)
    other = tmp_path / "other-repo"
    other.mkdir()
    (home / ".claude.json").write_text(json.dumps({"projects": {
        str(tmp_path).replace("\\", "/"): {"mcpServers": {
            "local-dev": {"command": "npx", "args": ["local-mcp"]}}},
        str(other).replace("\\", "/"): {"mcpServers": {
            "not-mine": {"command": "x"}}},
    }}), encoding="utf-8")
    inv = state.mcp_inventory(tmp_path)
    names = [s["name"] for s in inv["servers"]]
    assert names == ["local-dev"]  # this project's local scope, not the other's
    assert inv["servers"][0]["source"].endswith("(this project)")
    other_inv = state.mcp_inventory(other)
    assert [s["name"] for s in other_inv["servers"]] == ["not-mine"]


def test_mcp_inventory_project_scope_wins_over_user_scope(tmp_path, monkeypatch):
    """The same name declared at two scopes: the closest declaration wins and
    the server is listed ONCE — the session runs the project's version."""
    home = _home(tmp_path / "home", monkeypatch)
    home.mkdir(parents=True, exist_ok=True)
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {
        "exa": {"command": "project-version"}}}), encoding="utf-8")
    (home / ".claude.json").write_text(json.dumps({"mcpServers": {
        "exa": {"command": "user-version"},
        "only-user": {"command": "u"},
    }}), encoding="utf-8")
    inv = state.mcp_inventory(tmp_path)
    by_name = {s["name"]: s for s in inv["servers"]}
    assert inv["count"] == 2  # deduped, not listed twice
    assert by_name["exa"]["target"] == "project-version"
    assert by_name["exa"]["source"].startswith("project:")
    assert by_name["only-user"]["source"].startswith("user:")


def test_mcp_inventory_local_scope_beats_user_global_in_same_file(tmp_path, monkeypatch):
    """The same name at ~/.claude.json's user-global level AND this project's
    local scope: the closer local entry wins (harness precedence), listed once."""
    home = _home(tmp_path / "home", monkeypatch)
    home.mkdir(parents=True, exist_ok=True)
    (home / ".claude.json").write_text(json.dumps({
        "mcpServers": {"dup": {"command": "user-global-version"}},
        "projects": {
            str(tmp_path).replace("\\", "/"): {"mcpServers": {
                "dup": {"command": "local-version"}}},
        },
    }), encoding="utf-8")
    inv = state.mcp_inventory(tmp_path)
    assert inv["count"] == 1
    assert inv["servers"][0]["target"] == "local-version"
    assert inv["servers"][0]["source"].endswith("(this project)")


def test_mcp_steering_line_bounded_and_empty(tmp_path):
    assert state.mcp_steering_line({"servers": []}) == ""
    servers = [{"name": f"s{i}", "enabled": True} for i in range(6)]
    line = state.mcp_steering_line({"servers": servers})
    assert line.startswith("mcp: 6 active server(s) — s0, s1, s2, s3, …")
    assert len(line) <= 200
    disabled_only = state.mcp_steering_line(
        {"servers": [{"name": "x", "enabled": False}]})
    assert disabled_only == ""


def test_sprint_line_empty_project_is_empty(tmp_path):
    assert state.format_sprint_line(state.sprint_summary(tmp_path)) == ""


def test_sprint_line_is_bounded(tmp_path):
    keys = "\n".join(f"  1-{i}-story-{i}: done" for i in range(1, 60))
    _write_sprint(tmp_path, f"development_status:\n{keys}\n")
    line = state.format_sprint_line(state.sprint_summary(tmp_path))
    assert len(line) <= 200


# --- stop.py delegation ---------------------------------------------------------

def test_stop_in_progress_scan_uses_the_same_parser(tmp_path, monkeypatch):
    """stop's stale-safety wraps state.sprint_summary — same keys, same file."""
    import importlib
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    from modules import stop as stop_mod
    importlib.reload(stop_mod)
    _write_sprint(tmp_path,
                  "development_status:\n"
                  "  1-2-login: in-progress\n"
                  "  1-3-out: done\n"
                  "  epic-1: in-progress\n")
    assert stop_mod._in_progress_stories(str(tmp_path)) == ["1-2-login"]
    # The epic key must never surface as a story (the mailjs mis-read).
    assert "epic-1" not in stop_mod._in_progress_stories(str(tmp_path))


def test_stop_stale_leftover_still_ignored_after_delegation(tmp_path, monkeypatch):
    import importlib
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    from modules import stop as stop_mod
    importlib.reload(stop_mod)
    _write_sprint(tmp_path, "development_status:\n  1-2-login: in-progress\n")
    old = time.time() - 3600
    os.utime(tmp_path / "docs/development/native/sprint-status.yaml", (old, old))
    log = tmp_path / ".metodoloji/logs/hook-audit.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text('{"type": "session_start", "timestamp": ' + str(time.time()) + "}\n",
                   encoding="utf-8")
    assert stop_mod._in_progress_stories(str(tmp_path)) == []

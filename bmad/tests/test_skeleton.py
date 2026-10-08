"""Tests for bmad/scripts/skeleton.py — the executable half of one-time init.

A real session (2026-09-21, /root/mailjs) ran the whole planning chain with the
record skeleton absent: every SessionStart could only *suggest*
`/metodoloji:init`, a slash command no other runtime exposes, so nothing ever
executed it and the suggestion repeated forever. skeleton.py gives that
contract an executable form (`--install` / `--status`), which is what the hook
hint, `/metodoloji:init` and check-plugin.sh §6d now all point at.

These tests pin the three properties the script exists for: install is
one-time and non-clobbering, the marker and the skeleton never drift apart
silently, and the status output is honest about which side is missing.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[2]
SCRIPT = PLUGIN / "bmad" / "scripts" / "skeleton.py"

_spec = importlib.util.spec_from_file_location("skeleton", SCRIPT)
sk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sk)


def _run(args, cwd=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, encoding="utf-8", cwd=str(cwd or PLUGIN),
        stdin=subprocess.DEVNULL,  # pytest's fd capture leaves stdin unusable on Windows
    )


def test_status_on_empty_project_is_not_installed_but_not_drift(tmp_path):
    state = sk.status(tmp_path)
    assert state["installed"] is False
    assert state["skeleton_present"] is False and state["marker_present"] is False
    assert state["problems"] == []  # never initialized is a valid state
    assert set(sk.SKELETON_PROBES) <= set(state["missing_templates"])
    assert "scratch" in state["missing_directories"]


def test_install_creates_skeleton_byte_exact_and_writes_marker(tmp_path):
    result = sk.install(tmp_path)
    assert result["ok"] is True and result["marker_written"] is True
    for rel in sk.DIRECTORIES:
        assert (tmp_path / rel).is_dir(), rel
    for src_name, dest_rel in sk.TEMPLATE_PAIRS:
        # byte-exact: text-mode copies would rewrite LF to CRLF on Windows and
        # break check-plugin §6c copy identity.
        assert (tmp_path / dest_rel).read_bytes() == \
            (PLUGIN / "templates" / src_name).read_bytes(), dest_rel
    marker = (tmp_path / sk.MARKER_REL).read_text(encoding="utf-8")
    assert "initialized_at:" in marker and "plugin_version:" in marker
    assert sk.status(tmp_path)["installed"] is True


def test_install_is_idempotent_and_preserves_local_edits(tmp_path):
    sk.install(tmp_path)
    marker_before = (tmp_path / sk.MARKER_REL).read_text(encoding="utf-8")
    edited = tmp_path / "docs/experiments/_template.md"
    edited.write_text("hand-edited template\n", encoding="utf-8")

    again = sk.install(tmp_path)
    assert again["directories_created"] == [] and again["templates_copied"] == []
    assert len(again["templates_skipped"]) == len(sk.TEMPLATE_PAIRS)
    assert again["marker_written"] is False  # one-time: never churns initialized_at
    assert (tmp_path / sk.MARKER_REL).read_text(encoding="utf-8") == marker_before
    assert edited.read_text(encoding="utf-8") == "hand-edited template\n"

    forced = sk.install(tmp_path, force=True)
    assert forced["marker_written"] is True
    assert forced["templates_copied"] != []
    assert edited.read_bytes() == (PLUGIN / "templates" / "_template_E.md").read_bytes()


def test_status_reports_both_drift_directions(tmp_path):
    # skeleton without marker (the regression that made every skill re-hint init)
    sk.install(tmp_path)
    (tmp_path / sk.MARKER_REL).unlink()
    state = sk.status(tmp_path)
    assert state["problems"] == [
        "skeleton installed but .metodoloji/initialized is missing"]
    assert state["installed"] is False

    # marker without skeleton (stale marker init would short-circuit over)
    fresh = tmp_path / "stale"
    fresh.mkdir()
    (fresh / ".metodoloji").mkdir()
    (fresh / sk.MARKER_REL).write_text(
        "initialized_at: 2026-01-01T00:00:00Z\nplugin_version: 1.0.0\n",
        encoding="utf-8")
    assert sk.status(fresh)["problems"] == [
        "marker present but no skeleton copy found — stale marker"]


def test_cli_install_then_status_json(tmp_path):
    r = _run(["--install", "--project-root", str(tmp_path)])
    assert r.returncode == 0, r.stderr
    assert "record skeleton installed" in r.stdout

    r = _run(["--status", "--json", "--project-root", str(tmp_path)])
    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout)
    assert payload["installed"] is True and payload["problems"] == []


def test_cli_status_exit_codes_and_hint(tmp_path):
    r = _run(["--status", "--project-root", str(tmp_path)])
    assert r.returncode == 0  # opt-in init: an uninitialized project is not a failure
    assert "NOT installed" in r.stdout
    assert "skeleton.py --install" in r.stdout  # the hint must be executable

    (tmp_path / "docs/experiments").mkdir(parents=True)
    (tmp_path / "docs/experiments/_template.md").write_text("x", encoding="utf-8")
    r = _run(["--status", "--project-root", str(tmp_path)])
    assert r.returncode == 1  # skeleton present, marker missing → drift
    assert "[ERROR]" in r.stdout


def test_project_root_falls_back_to_env_then_cwd(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    assert sk.resolve_project_root(None) == tmp_path.resolve()
    monkeypatch.delenv("CLAUDE_PROJECT_DIR")
    monkeypatch.setenv("OPENHANDS_PROJECT_DIR", str(tmp_path))
    assert sk.resolve_project_root(None) == tmp_path.resolve()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "")  # blank env is not a root
    assert sk.resolve_project_root(str(tmp_path)) == tmp_path.resolve()


def test_plugin_root_is_derived_not_guessed():
    assert sk.plugin_root() == PLUGIN
    assert sk.plugin_version() == json.loads(
        (PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )["version"]


# --- undecodable marker (E-009) -----------------------------------------------
# UnicodeDecodeError escaped read_marker's `except OSError`: a BINARY marker
# tracebacks in --status/--install and through orient (which reads it via
# _skeleton.status). Unreadable must never be conflated with missing.

def _write_binary_marker(tmp_path) -> Path:
    marker = tmp_path / ".metodoloji" / "initialized"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_bytes(b"\xff\xfe\x00 binary")
    return marker


def test_binary_marker_is_unreadable_not_missing(tmp_path):
    _write_binary_marker(tmp_path)
    assert sk.read_marker(tmp_path) == {}          # no crash
    assert sk.marker_unreadable(tmp_path) is True
    state = sk.status(tmp_path)
    assert state["marker_present"] is False
    assert any("unreadable" in p for p in state["problems"])
    # never the wrong diagnosis: present-but-unreadable is not "missing"
    assert not any("is missing" in p for p in state["problems"])


def test_marker_unreadable_false_when_absent_or_valid(tmp_path):
    assert sk.marker_unreadable(tmp_path) is False  # absent
    marker = tmp_path / ".metodoloji" / "initialized"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("initialized_at: x\n", encoding="utf-8")
    assert sk.marker_unreadable(tmp_path) is False  # decodes fine


def test_cli_status_binary_marker_reports_unreadable_not_crash(tmp_path):
    _write_binary_marker(tmp_path)
    r = _run(["--status", "--json", "--project-root", str(tmp_path)])
    assert "Traceback" not in r.stderr, r.stderr
    data = json.loads(r.stdout)
    assert any("unreadable" in p for p in data["problems"])
    assert r.returncode in (0, 1)  # never an unhandled crash

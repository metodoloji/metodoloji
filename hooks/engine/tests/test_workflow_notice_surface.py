"""The hook envelope carries the human channel (E-175).

`additionalContext` is injected into the model; `systemMessage` is what the
harness renders to the person at the keyboard. These tests pin the envelope,
not the wording: every workflow edge that has something to say sets
`systemMessage`, and the PreToolUse threshold asks the user instead of deciding
for them.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_MAIN = _ROOT / "hooks" / "engine" / "main.py"
_CLI = _ROOT / "hooks" / "scripts" / "workflow.py"


def _hook(mode: str, payload: dict, runtime: str = "claude") -> dict:
    env = {**os.environ, "METODOLOJI_RUNTIME": runtime}
    result = subprocess.run(
        [sys.executable, str(_MAIN), mode],
        input=json.dumps(payload), capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout or "{}")


def _create_run(project: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(_CLI), "create", "--builtin", "methodology-chain",
         "--slug", "demo", "--project-root", str(project)],
        capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert result.returncode == 0, result.stderr


def test_session_open_and_close_show_the_position(tmp_path):
    _create_run(tmp_path)
    for mode in ("session_start", "stop"):
        out = _hook(mode, {"cwd": str(tmp_path)})
        assert "demo" in out.get("systemMessage", ""), (mode, out)
        assert "hookSpecificOutput" in out


def test_an_idle_project_stays_quiet(tmp_path):
    for mode in ("session_start", "stop"):
        assert "systemMessage" not in _hook(mode, {"cwd": str(tmp_path)}), mode


def test_a_kernel_mutation_is_announced_to_the_human(tmp_path):
    _create_run(tmp_path)
    command = (f"python3 {_CLI} complete --slug demo "
               "--stage experiment --project-root " + str(tmp_path))
    out = _hook("audit", {"cwd": str(tmp_path), "tool_name": "Bash",
                          "tool_input": {"command": command},
                          "tool_response": {"exit_code": 0}})
    assert "demo" in out.get("systemMessage", ""), out


def test_an_unrelated_command_stays_quiet(tmp_path):
    _create_run(tmp_path)
    out = _hook("audit", {"cwd": str(tmp_path), "tool_name": "Bash",
                          "tool_input": {"command": "npm run build"},
                          "tool_response": {"exit_code": 0}})
    assert "systemMessage" not in out, out


def test_a_forced_completion_asks_the_user(tmp_path):
    _create_run(tmp_path)
    command = (f"python3 {_CLI} complete --slug demo "
               "--stage experiment --force")
    out = _hook("pre", {"cwd": str(tmp_path), "tool_name": "Bash",
                        "tool_input": {"command": command}}, runtime="claude")
    assert out["hookSpecificOutput"]["permissionDecision"] == "ask", out

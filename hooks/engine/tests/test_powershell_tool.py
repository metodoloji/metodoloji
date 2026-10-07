"""PowerShell is a first-class shell tool: the guard must gate it like Bash.

Claude Code on Windows exposes `PowerShell(...)` calls, and a real win32 session
ran all of its shell work through it. Before `PowerShell` was in the tool
vocabulary the guard fell to the "unknown" branch (warn + allow), so
`Set-Content src/a.py …` bypassed the experiment gate entirely. These tests pin
the whole path: normalize → target extraction → gate decision.
"""

import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HOOKS))

from modules.guard import guard  # noqa: E402
from modules import config  # noqa: E402


def _code_project(tmp_path) -> Path:
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    return root


def _env(monkeypatch, root: Path) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)


def test_powershell_code_write_denied_under_hard_gate(tmp_path, monkeypatch):
    root = _code_project(tmp_path)
    _env(monkeypatch, root)
    monkeypatch.setattr(config, "hook_gate_mode", lambda key: "hard")
    res = guard({"tool_name": "PowerShell",
                 "tool_input": {"command": "Set-Content -Path src/a.py -Value x"}})
    assert res["decision"] == "deny"
    assert "src/a.py" in res["reason"]


def test_powershell_code_write_warns_under_soft_gate(tmp_path, monkeypatch):
    root = _code_project(tmp_path)
    _env(monkeypatch, root)
    monkeypatch.setattr(config, "hook_gate_mode", lambda key: "soft")
    res = guard({"tool_name": "PowerShell",
                 "tool_input": {"command": "Out-File -FilePath src/a.py"}})
    assert res["decision"] == "allow"
    assert any("src/a.py" in w for w in res.get("methodology_warnings", []))


def test_powershell_free_zone_write_allowed(tmp_path, monkeypatch):
    root = _code_project(tmp_path)
    _env(monkeypatch, root)
    monkeypatch.setattr(config, "hook_gate_mode", lambda key: "hard")
    res = guard({"tool_name": "PowerShell",
                 "tool_input": {"command": "Set-Content scratch/notes.md 'x'"}})
    assert res["decision"] == "allow"


def test_powershell_read_only_call_allowed(tmp_path, monkeypatch):
    root = _code_project(tmp_path)
    _env(monkeypatch, root)
    monkeypatch.setattr(config, "hook_gate_mode", lambda key: "hard")
    res = guard({"tool_name": "PowerShell",
                 "tool_input": {"command": "Get-ChildItem src"}})
    assert res["decision"] == "allow"

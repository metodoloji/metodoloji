"""OpenCode (V2) is a third runtime: its tool vocabulary must gate like the rest.

OpenCode names its tools in lower case — `write`, `edit`, `patch`, `shell`,
`read`, … — where Claude Code says `Write`/`Bash` and OpenHands says
`file_editor`/`terminal`. Before this vocabulary the names fell to the E-010
catch-all ("unknown" → warn + allow), so `write src/a.py` under a hard gate
bypassed the experiment gate and *every* `read` warned. These tests pin the whole
path: normalize → target extraction (including a multi-file `patch`) → decision,
plus the CLI contract the TypeScript bridge relies on.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HOOKS))

from modules.guard import guard  # noqa: E402
from modules import config  # noqa: E402
from modules.utils import (  # noqa: E402
    normalize_hook_input,
    _opencode_patch_paths,
)

_MAIN_PY = _HOOKS / "main.py"


def _code_project(tmp_path) -> Path:
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    return root


def _env(monkeypatch, root: Path) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.setenv("METODOLOJI_RUNTIME", "opencode")
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)


# --- normalize: the vocabulary -----------------------------------------------

def test_normalize_opencode_write_is_a_file_edit():
    norm = normalize_hook_input({"tool_name": "write",
                                 "tool_input": {"path": "src/a.py",
                                                "content": "x"}})
    assert norm["tool_name"] == "file_editor"
    assert norm["tool_input"]["path"] == "src/a.py"


def test_normalize_opencode_edit_is_a_file_edit():
    norm = normalize_hook_input({"tool_name": "edit",
                                 "tool_input": {"path": "src/a.py",
                                                "oldString": "x",
                                                "newString": "y"}})
    assert norm["tool_name"] == "file_editor"


def test_normalize_opencode_shell_is_a_terminal():
    norm = normalize_hook_input({"tool_name": "shell",
                                 "tool_input": {"command": "ls src"}})
    assert norm["tool_name"] == "terminal"
    assert norm["tool_input"]["command"] == "ls src"


def test_normalize_opencode_read_is_non_gated_without_warning():
    """A read-only tool must pass through as "" (allow, no `unknown` warning)."""
    norm = normalize_hook_input({"tool_name": "read",
                                 "tool_input": {"path": "src/a.py"}})
    assert norm["tool_name"] == ""


def test_normalize_opencode_write_accepts_filePath():
    """Documented builds carry the path as `filePath`, not `path`."""
    norm = normalize_hook_input({"tool_name": "write",
                                 "tool_input": {"filePath": "src/a.py",
                                                "content": "x"}})
    assert norm["tool_name"] == "file_editor"
    assert norm["tool_input"]["path"] == "src/a.py"


def test_normalize_opencode_bash_is_a_terminal():
    """The shell tool is `shell` in the shipped builds; `bash` is the alias."""
    norm = normalize_hook_input({"tool_name": "bash",
                                 "tool_input": {"command": "ls src"}})
    assert norm["tool_name"] == "terminal"


def test_normalize_opencode_apply_patch_is_the_write_tool():
    """The patch tool is named `patch`; `apply_patch` is accepted as an alias."""
    patch = ("*** Begin Patch\n*** Add File: src/a.py\n+x\n*** End Patch\n")
    norm = normalize_hook_input({"tool_name": "apply_patch",
                                 "tool_input": {"patchText": patch}})
    assert norm["tool_name"] == "file_editor"
    assert norm["tool_input"]["paths"] == ["src/a.py"]


def test_normalize_opencode_unknown_tool_is_flagged():
    norm = normalize_hook_input({"tool_name": "frobnicate", "tool_input": {}})
    assert norm["tool_name"] == "unknown"
    assert norm["raw_tool_name"] == "frobnicate"


def test_normalize_opencode_patch_collects_every_target():
    patch = (
        "*** Begin Patch\n"
        "*** Add File: src/a.py\n"
        "+x\n"
        "*** Update File: src/b.py\n"
        "@@\n"
        "*** Delete File: src/old.py\n"
        "*** End Patch\n"
    )
    norm = normalize_hook_input({"tool_name": "patch",
                                 "tool_input": {"patchText": patch}})
    assert norm["tool_name"] == "file_editor"
    assert norm["tool_input"]["paths"] == ["src/a.py", "src/b.py", "src/old.py"]
    assert norm["tool_input"]["path"] == "src/a.py"


def test_opencode_patch_paths_deduplicates_and_reads_moves():
    patch = ("*** Update File: src/a.py\n*** Move to: src/a2.py\n"
             "*** Update File: src/a.py\n")
    assert _opencode_patch_paths(patch) == ["src/a.py", "src/a2.py"]


# --- guard: the decisions ----------------------------------------------------

def test_opencode_write_code_denied_under_hard_gate(tmp_path, monkeypatch):
    root = _code_project(tmp_path)
    _env(monkeypatch, root)
    monkeypatch.setattr(config, "hook_gate_mode", lambda key: "hard")
    res = guard({"tool_name": "write",
                 "tool_input": {"path": "src/a.py", "content": "x"}})
    assert res["decision"] == "deny"
    assert "src/a.py" in res["reason"]


def test_opencode_shell_code_write_denied_under_hard_gate(tmp_path, monkeypatch):
    root = _code_project(tmp_path)
    _env(monkeypatch, root)
    monkeypatch.setattr(config, "hook_gate_mode", lambda key: "hard")
    res = guard({"tool_name": "shell",
                 "tool_input": {"command": 'echo x > src/a.py'}})
    assert res["decision"] == "deny"
    assert "src/a.py" in res["reason"]


def test_opencode_patch_gates_every_file_not_just_the_first(tmp_path, monkeypatch):
    """A patch that starts in the free zone but writes code must still deny."""
    root = _code_project(tmp_path)
    (root / "scratch").mkdir(parents=True)
    _env(monkeypatch, root)
    monkeypatch.setattr(config, "hook_gate_mode", lambda key: "hard")
    patch = ("*** Begin Patch\n"
             "*** Add File: scratch/notes.md\n"
             "+x\n"
             "*** Update File: src/a.py\n"
             "@@\n"
             "*** End Patch\n")
    res = guard({"tool_name": "patch", "tool_input": {"patchText": patch}})
    assert res["decision"] == "deny"
    assert "src/a.py" in res["reason"]


def test_opencode_free_zone_write_allowed(tmp_path, monkeypatch):
    root = _code_project(tmp_path)
    _env(monkeypatch, root)
    monkeypatch.setattr(config, "hook_gate_mode", lambda key: "hard")
    res = guard({"tool_name": "write",
                 "tool_input": {"path": "scratch/notes.md", "content": "x"}})
    assert res["decision"] == "allow"


def test_opencode_permission_fallback_gates_every_listed_path(tmp_path, monkeypatch):
    """The `permission.evaluate` layer rebuilds an `edit` action as a write to a
    `paths` list (one entry per file). Guarding only `path` would let a
    multi-file patch through, so the whole list must be checked."""
    root = _code_project(tmp_path)
    (root / "scratch").mkdir(parents=True)
    _env(monkeypatch, root)
    monkeypatch.setattr(config, "hook_gate_mode", lambda key: "hard")
    res = guard({"tool_name": "write",
                 "tool_input": {"path": "scratch/notes.md",
                                "paths": ["scratch/notes.md", "src/a.py"]}})
    assert res["decision"] == "deny"
    assert "src/a.py" in res["reason"]


def test_opencode_read_allows_without_warning(tmp_path, monkeypatch):
    root = _code_project(tmp_path)
    _env(monkeypatch, root)
    monkeypatch.setattr(config, "hook_gate_mode", lambda key: "hard")
    res = guard({"tool_name": "read", "tool_input": {"path": "src/a.py"}})
    assert res["decision"] == "allow"
    assert not res.get("methodology_warnings")


def test_opencode_unknown_tool_allows_with_warning(tmp_path, monkeypatch):
    root = _code_project(tmp_path)
    _env(monkeypatch, root)
    monkeypatch.setattr(config, "hook_gate_mode", lambda key: "hard")
    res = guard({"tool_name": "frobnicate", "tool_input": {}})
    assert res["decision"] == "allow"
    assert res.get("methodology_warnings")


def test_opencode_vocabulary_applies_without_the_runtime_env(tmp_path, monkeypatch):
    """The name alone is enough: the bridge need not set METODOLOJI_RUNTIME."""
    root = _code_project(tmp_path)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.delenv("METODOLOJI_RUNTIME", raising=False)
    monkeypatch.setattr(config, "hook_gate_mode", lambda key: "hard")
    res = guard({"tool_name": "write",
                 "tool_input": {"path": "src/a.py", "content": "x"}})
    assert res["decision"] == "deny"


# --- CLI: the exact contract the TypeScript bridge speaks ---------------------

def _run_main(args, payload, project_root):
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(project_root)
    env["METODOLOJI_RUNTIME"] = "opencode"
    return subprocess.run(
        [sys.executable, str(_MAIN_PY), *args],
        input=json.dumps(payload),
        capture_output=True, text=True, encoding="utf-8", timeout=60,
        cwd=str(_HOOKS.parent), env=env)


def test_main_accepts_runtime_opencode_and_denies(tmp_path):
    root = _code_project(tmp_path)
    r = _run_main(["--runtime=opencode", "guard"],
                  {"tool_name": "write",
                   "tool_input": {"path": "src/a.py", "content": "x"}},
                  project_root=root)
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_main_opencode_read_tool_allows(tmp_path):
    root = _code_project(tmp_path)
    r = _run_main(["--runtime=opencode", "guard"],
                  {"tool_name": "read", "tool_input": {"path": "src/a.py"}},
                  project_root=root)
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"

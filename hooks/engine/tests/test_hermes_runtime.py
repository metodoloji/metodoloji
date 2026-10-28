"""Hermes runtime contract: tool vocabulary, execute_code targets, the bridge.

What is pinned here is the seam, not the methodology logic:

* Hermes names one tool per action (``write_file``/``patch``/``execute_code``)
  where the other runtimes reuse ``Write``/``Bash``. A name that silently
  normalizes to "" is a tool the guard allows without judging — that is the
  fail-open class these tests exist to prevent.
* ``execute_code`` runs arbitrary Python in the session cwd, so its write
  targets have to be derived from the source. A missed target is a bypass; a
  guessed target (``if x > 3`` read as a redirect) denies real work.
* ``hermes_adapter.check_tool`` must spawn the engine only for gated tools,
  must block on an explicit denial, and must fail CLOSED when the engine is
  unreachable.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PLUGIN_ROOT))
sys.path.insert(0, str(PLUGIN_ROOT / "hooks" / "engine"))

from modules.utils import (  # noqa: E402
    _hermes_execute_code_targets,
    normalize_hook_input,
)


@pytest.fixture()
def hermes_runtime(monkeypatch):
    monkeypatch.setenv("METODOLOJI_RUNTIME", "hermes")
    return None


def _norm(tool_name, tool_input):
    return normalize_hook_input({"tool_name": tool_name, "tool_input": tool_input,
                                 "cwd": "/tmp/hermes-test-project"})


# --------------------------------------------------------------------------- #
# tool vocabulary
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("hermes_name,expected", [
    ("write_file", "file_editor"),
    ("patch", "file_editor"),
    ("multi_edit", "file_editor"),
    ("notebook_edit", "notebook_editor"),
    ("terminal", "terminal"),
])
def test_hermes_write_and_shell_tools_normalize(hermes_runtime, hermes_name, expected):
    norm = _norm(hermes_name, {"path": "src/a.py", "command": "ls"})
    assert norm["tool_name"] == expected


def test_write_file_path_lands_in_the_guard_target_field(hermes_runtime):
    norm = _norm("write_file", {"path": "src/a.py", "content": "x"})
    assert norm["tool_name"] == "file_editor"
    assert norm["tool_input"]["path"] == "src/a.py"


def test_patch_maps_onto_an_edit_payload(hermes_runtime):
    norm = _norm("patch", {"path": "src/a.py", "old_string": "a", "new_string": "b"})
    assert norm["tool_name"] == "file_editor"
    # audit.py counts an edit as a write on these keys — a patch that only
    # carried `content` would be logged as a read-only call.
    assert norm["tool_input"]["new_string"] == "b"


@pytest.mark.parametrize("hermes_name", [
    "read_file", "search_files", "web_search", "skill_view", "todo_list",
    "metodoloji_orient", "hindsight_recall",
])
def test_read_only_hermes_tools_stay_silent(hermes_runtime, hermes_name):
    # "" = non-gated: guard allows WITHOUT a warning, so a web_search-heavy
    # turn does not drown the injected context in "unrecognized tool" lines.
    assert _norm(hermes_name, {})["tool_name"] == ""


def test_unknown_hermes_tool_is_marked_not_allowed_silently(hermes_runtime):
    assert _norm("future_write_tool", {})["tool_name"] == "unknown"


def test_terminal_is_not_remapped_by_the_hermes_branch(hermes_runtime):
    # `terminal` is also an OpenHands name; the hermes branch must not corrupt
    # the payload it already understands.
    norm = _norm("terminal", {"command": "git status"})
    assert norm["tool_name"] == "terminal"
    assert norm["tool_input"]["command"] == "git status"


# --------------------------------------------------------------------------- #
# execute_code write-target derivation
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("code,expected_target", [
    ("open('src/a.py','w').write('x')", "src/a.py"),
    ("open(\"src/b.py\", \"a\").write('x')", "src/b.py"),
    ("from pathlib import Path\nPath('src/c.py').write_text('x')", "src/c.py"),
    ("import shutil\nshutil.copy('a', 'src/d.py')", "src/d.py"),
    ("import os\nos.rename('a', 'src/e.py')", "src/e.py"),
    ("df.to_csv('data/out.csv')", "data/out.csv"),
    ("import os\nos.system('echo hi > src/f.py')", "src/f.py"),
])
def test_execute_code_targets_are_extracted(code, expected_target):
    assert expected_target in _hermes_execute_code_targets(code)


@pytest.mark.parametrize("code", [
    "x = 5\nif x > 3:\n    print(x)",          # comparison, not a redirect
    "print(sum(range(10)))",                    # pure computation
    "rows = [r for r in open('in.csv')]",       # read-only open
])
def test_execute_code_does_not_invent_targets(code):
    assert _hermes_execute_code_targets(code) == []


def test_execute_code_with_a_literal_write_denies_like_write_file(tmp_path, hermes_runtime):
    """The real engine must see an execute_code write as a code write."""
    from hermes_adapter import run_engine

    payload = {
        "tool_name": "execute_code",
        "tool_input": {"code": "open('src/app.py','w').write('x')"},
        "cwd": str(tmp_path),
    }
    result = run_engine("pre", payload, cwd=str(tmp_path))
    assert result["decision"] == "deny", result
    assert "src/app.py" in result["reason"]


def test_execute_code_unresolvable_path_warns_instead_of_guessing(tmp_path, hermes_runtime):
    from hermes_adapter import run_engine

    # A path that only exists at runtime (input/env/argv) cannot be judged;
    # it stays a visible warning rather than a guessed deny.
    payload = {
        "tool_name": "execute_code",
        "tool_input": {"code": "p = input()\nopen(p,'w').write('x')"},
        "cwd": str(tmp_path),
    }
    result = run_engine("pre", payload, cwd=str(tmp_path))
    assert result["decision"] == "allow"
    assert result["warnings"], "a write with an unresolvable target must be reported"


# --------------------------------------------------------------------------- #
# adapter contract
# --------------------------------------------------------------------------- #
def test_payload_is_claude_shaped_but_keeps_hermes_names():
    import hermes_adapter as A

    payload = A.build_payload("write_file", {"path": "a.py"}, cwd="/tmp/x",
                              session_id="s1")
    assert payload["tool_name"] == "write_file"  # engine owns the mapping
    assert payload["tool_input"] == {"path": "a.py"}
    assert payload["cwd"] == "/tmp/x"
    assert payload["session_id"] == "s1"


def test_read_only_tool_never_spawns_the_engine(monkeypatch):
    import hermes_adapter as A

    def boom(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("engine spawned for a read-only tool")

    monkeypatch.setattr(A.subprocess, "run", boom)
    result = A.check_tool("read_file", {"path": "a.py"})
    assert result["decision"] == "allow"
    assert result["block"] is None


def test_gated_tool_denial_becomes_a_hermes_block(monkeypatch):
    import hermes_adapter as A

    monkeypatch.setattr(A, "run_engine", lambda *a, **k: A._result(
        "deny", "No approved experiment record for src/a.py"))
    result = A.check_tool("write_file", {"path": "src/a.py", "content": "x"})
    assert result["block"]["action"] == "block"
    assert "No approved experiment record" in result["block"]["message"]


def test_missing_engine_fails_closed(monkeypatch, tmp_path):
    import hermes_adapter as A

    monkeypatch.setattr(A, "ENGINE", tmp_path / "nope.py")
    result = A.check_tool("terminal", {"command": "echo x > src/a.py"})
    assert result["decision"] == "deny"
    assert result["block"]["action"] == "block"


def test_real_engine_denies_an_unapproved_code_write(tmp_path, hermes_runtime, monkeypatch):
    import hermes_adapter as A

    # check_tool resolves the project from agent_cwd(); pin it to the fixture so
    # the assertion is about the gate, not about this process's cwd.
    monkeypatch.setattr(A, "agent_cwd", lambda: str(tmp_path))
    result = A.check_tool("write_file", {"path": "src/app.py", "content": "x"},
                          session_id="t")
    assert result["decision"] == "deny", result
    assert result["block"]["action"] == "block"
    assert "src/app.py" in result["reason"]


@pytest.fixture()
def plugin_module():
    """Load the plugin package (root __init__.py) as Hermes would."""
    import importlib.util

    name = "metodoloji_plugin_under_test"
    spec = importlib.util.spec_from_file_location(
        name, PLUGIN_ROOT / "__init__.py",
        submodule_search_locations=[str(PLUGIN_ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    module.__path__ = [str(PLUGIN_ROOT)]
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_blocked_call_is_not_audited(plugin_module, monkeypatch):
    """A denied write must not reach the audit trail.

    post_tool_call still fires for a blocked call. Logging it made the engine's
    stop report announce "code written this session: src/app.py" for a write
    the guard had just denied — measured in a live Hermes session, not in vitro.
    """
    audited: list = []
    monkeypatch.setattr(plugin_module, "audit_tool_call", lambda *a, **k: audited.append(a))
    monkeypatch.setattr(plugin_module, "check_tool", lambda *a, **k: {
        "decision": "deny", "reason": "no record", "warnings": [],
        "block": {"action": "block", "message": "no record"},
    })

    block = plugin_module.on_pre_tool_call(
        "write_file", {"path": "src/app.py"}, task_id="t", tool_call_id="call-1")
    assert block["action"] == "block"
    plugin_module.on_post_tool_call(
        "write_file", {"path": "src/app.py"}, result="denied", task_id="t",
        tool_call_id="call-1")
    assert audited == [], "a denied write was written to the audit trail"

    # An allowed call with a different id still audits normally.
    plugin_module.on_post_tool_call(
        "write_file", {"path": "docs/notes.md"}, result="ok", task_id="t",
        tool_call_id="call-2")
    assert len(audited) == 1


def test_commands_resolve_under_package_import(plugin_module, monkeypatch):
    """Every slash command must work when the plugin is a package.

    `from hermes_tools import …` inside a handler raised
    "No module named 'hermes_tools'" in a live Hermes session: the modules are
    `hermes_plugins.<name>.hermes_tools`, so only relative imports resolve.
    """
    digest = {"success": True, "digest": {"project_root": "/tmp/proj",
                                          "plugin_version": "9.9.9", "records": {}}}
    monkeypatch.setattr(plugin_module, "handle_orient", lambda *a, **k: json.dumps(digest))
    monkeypatch.setattr(plugin_module, "handle_audit", lambda *a, **k: json.dumps(digest))
    monkeypatch.setattr(plugin_module, "handle_init", lambda *a, **k: json.dumps({
        "success": True, "project_root": "/tmp/proj",
        "install": {"ok": True, "root": "/tmp/proj", "directories_created": ["docs"]},
    }))
    monkeypatch.setattr(plugin_module, "handle_verify", lambda *a, **k: json.dumps({
        "success": True, "verdict": "VERIFIED", "record": "docs/experiments/E-001.md",
    }))
    monkeypatch.setattr(plugin_module, "handle_key", lambda *a, **k: json.dumps({
        "success": True, "present": True, "stdout": "[OK] gate key present",
    }))

    for name, handler, _description in plugin_module.COMMANDS:
        text = handler("docs/experiments/E-001.md")
        assert isinstance(text, str) and text.strip(), f"/{name} returned nothing"
    assert "/tmp/proj" in plugin_module._command_status("")
    assert "VERIFIED" in plugin_module._command_verify("docs/experiments/E-001.md")
    assert "Usage:" in plugin_module._command_verify("")


def test_manifest_version_matches_pyproject():
    pyproject = (PLUGIN_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    found = re.search(r'(?m)^version\s*=\s*"([^"]+)"', pyproject)
    assert found, "pyproject.toml has no version line"
    expected = found.group(1)
    manifest = (PLUGIN_ROOT / "plugin.yaml").read_text(encoding="utf-8")
    assert re.search(rf'(?m)^version:\s*{re.escape(expected)}\s*$', manifest), \
        "plugin.yaml version must match pyproject.toml (check-plugin §6d3)"


def _block_yaml(monkeypatch, blocked=("yaml", "hermes_yaml")):
    """Make every YAML loader unavailable, by both import mechanisms.

    ``importlib.import_module`` does not route through ``builtins.__import__``,
    so patching one of them leaves the other free to succeed — a "fails without
    PyYAML" test that quietly imports PyYAML anyway. Patch both, and assert the
    loader really is unreachable before testing what the adapter does without
    it.
    """
    import builtins
    import importlib

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name in blocked or name.split(".")[0] in blocked:
            raise ImportError(f"{name} blocked by test")
        return real_import(name, *args, **kwargs)

    def fake_import_module(name, *args, **kwargs):
        if name in blocked or name.split(".")[0] in blocked:
            raise ImportError(f"{name} blocked by test")
        return importlib.import_module(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr(importlib, "import_module", fake_import_module)
    for name in blocked:
        with pytest.raises(ImportError):
            importlib.import_module(name)


def test_plugin_version_is_reported_without_pyyaml(monkeypatch):
    """`plugin_version()` must not depend on a library Hermes does not ship.

    Hermes carries its YAML policy in ``hermes_yaml`` (ruamel) and has no
    PyYAML, so the adapter's bare ``import yaml`` raised in the gateway and the
    session header read ``metodoloji-plugin: hermes vunknown`` on every start.
    """
    import hermes_adapter as A

    expected = re.search(r'(?m)^version:\s*(\S+)\s*$',
                         (PLUGIN_ROOT / "plugin.yaml").read_text(encoding="utf-8")).group(1)
    _block_yaml(monkeypatch, blocked=("yaml",))
    assert A.plugin_version() == expected


def test_plugin_version_survives_a_host_with_no_yaml_library(monkeypatch):
    """Last resort is the manifest text itself, so the header is never silent."""
    import hermes_adapter as A

    expected = re.search(r'(?m)^version:\s*(\S+)\s*$',
                         (PLUGIN_ROOT / "plugin.yaml").read_text(encoding="utf-8")).group(1)
    _block_yaml(monkeypatch, blocked=("yaml", "hermes_yaml", "ruamel"))
    assert A.plugin_version() == expected


# --------------------------------------------------------------------------- #
# Hermes-difference solutions (each one closes a documented gap)
# --------------------------------------------------------------------------- #
def test_execute_code_resolves_a_variable_path(tmp_path, hermes_runtime):
    """`p = 'src/app.py'; open(p,'w')` used to be the blind spot — resolve it."""
    from hermes_adapter import run_engine

    payload = {
        "tool_name": "execute_code",
        "tool_input": {"code": "p = 'src/app.py'\nopen(p, 'w').write('x')"},
        "cwd": str(tmp_path),
    }
    result = run_engine("pre", payload, cwd=str(tmp_path))
    assert result["decision"] == "deny", result
    assert "src/app.py" in result["reason"]


def test_execute_code_deny_mode_blocks_unresolved_write(tmp_path, hermes_runtime, monkeypatch):
    import hermes_adapter as A

    monkeypatch.setitem(A.OPTIONS, "execute_code_guard", "deny")
    monkeypatch.setattr(A, "agent_cwd", lambda: str(tmp_path))
    result = A.check_tool("execute_code", {"code": "p = input()\nopen(p, 'w').write('x')"})
    assert result["decision"] == "deny"
    assert "not a literal" in result["reason"]
    assert result["block"]["action"] == "block"


def test_execute_code_warn_mode_keeps_the_blind_spot_visible(tmp_path, hermes_runtime, monkeypatch):
    import hermes_adapter as A

    monkeypatch.setitem(A.OPTIONS, "execute_code_guard", "warn")
    monkeypatch.setattr(A, "agent_cwd", lambda: str(tmp_path))
    result = A.check_tool("execute_code", {"code": "p = input()\nopen(p, 'w').write('x')"})
    assert result["decision"] == "allow"
    assert any("execute_code" in w for w in result["warnings"])


def test_process_manage_submit_is_gated_as_a_shell_channel(tmp_path, hermes_runtime):
    """PTY stdin is a shell: `submit` with a redirect must reach the gate."""
    from hermes_adapter import run_engine

    payload = {
        "tool_name": "process_manage",
        "tool_input": {"action": "submit", "process_id": "p1", "data": "echo x > src/t.py"},
        "cwd": str(tmp_path),
    }
    result = run_engine("pre", payload, cwd=str(tmp_path))
    assert result["decision"] == "deny", result
    assert "src/t.py" in result["reason"]


def test_process_manage_read_actions_are_not_gated(monkeypatch):
    import hermes_adapter as A

    def boom(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("engine spawned for a read-only process action")

    monkeypatch.setattr(A.subprocess, "run", boom)
    for action in ("list", "poll", "log", "wait"):
        result = A.check_tool("process_manage", {"action": action})
        assert result["decision"] == "allow"
        assert result["block"] is None


def test_gui_tool_policy(monkeypatch):
    import hermes_adapter as A

    monkeypatch.setitem(A.OPTIONS, "block_gui_tools", False)
    assert A.check_tool("computer_use", {"action": "screenshot"})["block"] is None

    monkeypatch.setitem(A.OPTIONS, "block_gui_tools", True)
    result = A.check_tool("computer_use", {"action": "click"})
    assert result["decision"] == "deny"
    assert result["block"]["action"] == "block"


def test_previous_stop_report_is_delivered_once(tmp_path, hermes_runtime):
    import hermes_adapter as A

    log_dir = tmp_path / ".metodoloji" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "hermes-stop.log").write_text(
        "--- 20261020_120000_abc\nStop report — in-progress story S-001 needs a QR.\n",
        encoding="utf-8",
    )
    first = A.session_context(session_id="s1", cwd=str(tmp_path))
    assert "Previous session report" in first
    assert "in-progress story S-001" in first
    assert (log_dir / "hermes-stop.log").read_text(encoding="utf-8") == "", \
        "the report must be consumed, not re-announced every session"

    second = A.session_context(session_id="s2", cwd=str(tmp_path))
    assert "Previous session report" not in second


def test_stop_report_carry_is_configurable(tmp_path, hermes_runtime, monkeypatch):
    import hermes_adapter as A

    log_dir = tmp_path / ".metodoloji" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "hermes-stop.log").write_text("--- s\nStop report — open plan P-1.\n",
                                             encoding="utf-8")
    monkeypatch.setitem(A.OPTIONS, "carry_stop_report", False)
    assert "Previous session report" not in A.session_context(session_id="s3", cwd=str(tmp_path))
    assert "open plan" in (log_dir / "hermes-stop.log").read_text(encoding="utf-8")


def test_skill_description_folds_triggers(plugin_module, monkeypatch):
    monkeypatch.setitem(plugin_module.OPTIONS, "include_skill_triggers", True)
    description = plugin_module._skill_description(
        {"description": "Facilitate brainstorming.", "triggers": ["/bmad-brainstorming", "help me ideate"]},
        "bmad-brainstorming",
    )
    assert "Facilitate brainstorming." in description
    assert "help me ideate" in description
    assert "/bmad-brainstorming" not in description  # slash form is noise in the index

    monkeypatch.setitem(plugin_module.OPTIONS, "include_skill_triggers", False)
    assert plugin_module._skill_description(
        {"description": "Facilitate brainstorming.", "triggers": ["ideate"]}, "x"
    ) == "Facilitate brainstorming."


def test_pre_verify_is_a_noop_by_default(plugin_module, monkeypatch):
    monkeypatch.setitem(plugin_module.OPTIONS, "nudge_on_stop", False)

    def boom(**kwargs):  # pragma: no cover - must not run
        raise AssertionError("stop report ran while the nudge is disabled")

    monkeypatch.setattr(plugin_module, "stop_report", boom)
    assert plugin_module.on_pre_verify(session_id="s", coding=True, attempt=0) is None


def test_pre_verify_nudges_once_on_actionable_report(plugin_module, monkeypatch):
    monkeypatch.setitem(plugin_module.OPTIONS, "nudge_on_stop", True)
    monkeypatch.setattr(
        plugin_module, "stop_report",
        lambda **kwargs: "Stop report — in-progress story S-001 has no QR.",
    )
    directive = plugin_module.on_pre_verify(session_id="s", coding=True, attempt=0)
    assert directive["action"] == "continue"
    assert "S-001" in directive["message"]
    # attempt>0: Hermes re-fires after each nudge; we must not loop.
    assert plugin_module.on_pre_verify(session_id="s", coding=True, attempt=1) is None


def test_pre_verify_ignores_informational_report(plugin_module, monkeypatch):
    monkeypatch.setitem(plugin_module.OPTIONS, "nudge_on_stop", True)
    monkeypatch.setattr(plugin_module, "stop_report",
                        lambda **kwargs: "Stop report — nothing open.")
    assert plugin_module.on_pre_verify(session_id="s", coding=True, attempt=0) is None


def test_identical_regenerated_report_is_not_re_delivered(tmp_path, hermes_runtime):
    """`stop` regenerates project-wide state each session; say it once.

    Waiting hand-offs are re-reported verbatim by every session end, so a
    clear-and-deliver carry-over would inject the same paragraph on every
    start. The digest marker keeps it to one delivery until it changes.
    """
    import hermes_adapter as A

    log_dir = tmp_path / ".metodoloji" / "logs"
    log_dir.mkdir(parents=True)
    log = log_dir / "hermes-stop.log"
    body = "Stop report — hand-off waiting: bmad-check-implementation-readiness (2)."
    log.write_text(f"--- s1\n{body}\n", encoding="utf-8")
    assert "hand-off waiting" in A.session_context(session_id="a", cwd=str(tmp_path))

    log.write_text(f"--- s2\n{body}\n", encoding="utf-8")  # regenerated, unchanged
    assert "Previous session report" not in A.session_context(session_id="b", cwd=str(tmp_path))

    log.write_text("--- s3\nStop report — in-progress story S-009 needs a QR.\n",
                   encoding="utf-8")
    assert "S-009" in A.session_context(session_id="c", cwd=str(tmp_path))

"""Tests for hooks/engine/modules/utils.py — path classification helpers."""

import os
import sys
from pathlib import Path

import pytest

_HOOKS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HOOKS))

from modules.utils import (  # noqa: E402
    extract_story_key_from_content,
    is_code_target,
    is_free,
    norm_path,
    rel_to_root,
    repo_root,
)


def test_norm_path_forward_slashes():
    assert norm_path("src\\foo.py") == "src/foo.py"
    assert norm_path("./src/foo.py") == "src/foo.py"
    assert norm_path("C:/x/src.py") == "/x/src.py"  # drive stripped, leading slash kept


def test_norm_path_repeated_dots():
    assert norm_path("././src/x.py") == "src/x.py"
    assert norm_path("") == ""


def test_norm_path_drive_variants():
    assert norm_path("c:/x.py") == "/x.py"
    assert norm_path("C:\\x\\y.py") == "/x/y.py"


def test_norm_path_msys_drive_form():
    """Git Bash '/c/Users/x' resolves to the native form on Windows.

    On POSIX '/c/Users/x' is a genuine absolute path and must stay untouched —
    the conversion is deliberately Windows-only.
    """
    if os.name == "nt":
        assert norm_path("/c/Users/x/src.py") == "/Users/x/src.py"
        assert norm_path("/cygdrive/c/Users/x/src.py") == "/Users/x/src.py"
        assert norm_path("/c") == ""
    else:
        assert norm_path("/c/Users/x/src.py") == "/c/Users/x/src.py"


def test_rel_to_root_msys_drive_form_matches_native_root():
    """Root and target must end up in the same form, or nothing relativizes."""
    if os.name == "nt":
        assert rel_to_root("C:/Users/x/proj", 
                           "/c/Users/x/proj/scratch/a.py") == "scratch/a.py"
    else:
        assert rel_to_root("/repo", "/c/Users/x/a.py") == "/c/Users/x/a.py"


def test_repo_root_normalizes_msys_drive(monkeypatch):
    """A Git Bash project root must abspath to the real drive, not 'C:\\c\\...'."""
    if os.name != "nt":
        pytest.skip("MSYS drive form only exists on Windows")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/c/proj")
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    # Drive-letter case is preserved from the input; Windows paths are case-insensitive.
    assert os.path.normcase(repo_root({})) == os.path.normcase(os.path.abspath("C:/proj"))


def test_is_free_free_zones():
    assert is_free("scratch/explore.py")
    assert is_free("tmp/t.txt")
    assert is_free("docs/README.md")
    assert is_free(".metodoloji/logs/hook-audit.log")
    assert is_free("_bmad/foo.py")
    # explore_* is free as a root-level FILE; a nested path stays gated.
    assert is_free("explore_x.py")
    assert not is_free("explore_evil/payload.py")
    assert not is_free("explore_main.py/foo")


def test_is_free_docs_raw():
    assert is_free("docs/foo/raw/data.json")


def test_is_free_infra_files():
    assert is_free("scripts/check-methodology.sh")
    assert is_free("skills/bmad-research-experiment/scripts/run_experiment.py")


def test_is_free_empty():
    assert is_free("") is True
    assert is_free("/") is True


def test_is_free_not_free():
    assert not is_free("src/main.py")
    assert not is_free("lib/engine/core.py")
    # Anything starting with explore_ is free (even a plain name).
    assert is_free("explore_main.py")
    # ... but only at the root level: an explore_* directory must NOT release
    # its subtree (free-zone bypass via explore_evil/payload.py).
    assert not is_free("explore_evil/payload.py")
    assert not is_free("explore_notes/inner/deep.py")


def test_plugin_trees_protected_in_ordinary_project(monkeypatch, tmp_path):
    """hooks/, scripts/, skills/ are plugin source trees.
    When the project root is NOT the methodology root, they are NOT free —
    the experiment gate applies."""
    from modules import config, utils
    monkeypatch.setattr(config, "_METHODOLOGY_ROOT", tmp_path)
    monkeypatch.setattr(utils, "repo_root", lambda json_in: str(tmp_path / "user-project"))
    assert not is_free("scripts/tool.py")
    assert not is_free("hooks/engine/main.py")
    assert not is_free("skills/bmad-dev-story/SKILL.md")


def test_plugin_trees_free_when_project_is_methodology_root(monkeypatch, tmp_path):
    """Self-modification: when the project root IS the methodology root,
    plugin source trees are released without experiment approval."""
    from modules import config, utils
    monkeypatch.setattr(config, "_METHODOLOGY_ROOT", tmp_path)
    monkeypatch.setattr(utils, "repo_root", lambda json_in: str(tmp_path))
    assert is_free("scripts/tool.py")
    assert is_free("hooks/engine/main.py")
    assert is_free("skills/bmad-dev-story/SKILL.md")
    assert is_free("custom/bmad-dev-story.toml")


def test_plugin_trees_protected_when_no_project_env(monkeypatch, tmp_path):
    """Fail-closed default: with no project-root env, plugin trees stay protected
    (the check must not silently release them)."""
    from modules import config, utils
    monkeypatch.setattr(config, "_METHODOLOGY_ROOT", tmp_path)
    # repo_root falls back to os.getcwd() — pin it away from the methodology root.
    monkeypatch.setattr(utils, "repo_root", lambda json_in: "/somewhere/else")
    assert not is_free("scripts/tool.py")
    assert not is_free("skills/anything.py")


def test_is_code_target_classification():
    # Code: unknown/executable extensions are code
    assert is_code_target("src/foo.py")
    assert is_code_target("lib/engine.py")
    assert is_code_target("Makefile")
    assert is_code_target("Dockerfile")
    assert is_code_target(".github/workflows/ci.yml")
    # Non-code: data/markup/assets
    assert not is_code_target("README.md")
    assert not is_code_target("data.json")
    assert not is_code_target("config.yaml")
    assert not is_code_target("image.png")
    assert not is_code_target(".gitignore")


def test_is_code_target_code_dirs():
    assert is_code_target("src/deep/file.py")
    assert is_code_target("lib/helper.ts")
    assert is_code_target("tools/build.py")
    assert is_code_target("core/x.go")
    assert is_code_target("app/main.py")


def test_is_code_target_dev_null():
    assert not is_code_target("dev/null")
    assert not is_code_target("dev/null/x")


def test_is_code_target_shell_junk_is_not_a_path():
    # Redirect destinations arrive glued to their terminator: `cmd > /dev/null;.`
    # reaches the classifier as "dev/null;.". The fail-closed tail used to read
    # the junk as an unlisted extension and report the phantom as code written
    # (live Stop report: "code written this session: /dev/null;.").
    assert not is_code_target("/dev/null;.")
    assert not is_code_target("dev/null;.")
    assert not is_code_target("out/$(date).py")
    assert not is_code_target("src/a.py&&b.py")
    # Real paths (no punctuation) stay gated.
    assert is_code_target("src/new.py")


def test_is_code_target_exec_config():
    assert is_code_target(".github/workflows/ci.yml")
    assert is_code_target("docker-compose.yml")
    assert is_code_target("package.json")


def test_norm_path_dotdot_and_slashes():
    assert norm_path("src//a.py") == "src/a.py"
    assert norm_path("scratch/../src/x.py") == "src/x.py"
    assert norm_path("  src/x.py  ") == "src/x.py"
    assert norm_path("C:\\x\\y.py") == "/x/y.py"


def test_rel_to_root_sibling_prefix_not_stripped():
    # /repo-evil/x under root /repo must NOT rebase as repo-internal.
    assert rel_to_root("/repo", "/repo-evil/x.py") == "/repo-evil/x.py"
    assert rel_to_root("/repo", "/repo/src/x.py") == "src/x.py"


def test_normalize_notebook_edit():
    from modules.utils import normalize_hook_input
    norm = normalize_hook_input({"tool_name": "NotebookEdit",
                                 "tool_input": {"file_path": "nb/x.ipynb"}})
    assert norm["tool_name"] == "notebook_editor"
    assert norm["tool_input"]["path"] == "nb/x.ipynb"


def test_normalize_unknown_tool_flagged():
    from modules.utils import normalize_hook_input
    norm = normalize_hook_input({"tool_name": "FutureTool",
                                 "tool_input": {}})
    assert norm["tool_name"] == "unknown"
    assert norm["raw_tool_name"] == "FutureTool"


def test_normalize_powershell_is_a_terminal_tool():
    """Claude Code's Windows shell tool must gate like `Bash`.

    A real win32 session ran its shell work through `PowerShell`. Before this
    mapping the name fell to the "unknown" branch, where guard WARNED and
    allowed — so `Set-Content src/a.py …` bypassed the experiment gate.
    """
    from modules.utils import normalize_hook_input
    norm = normalize_hook_input({"tool_name": "PowerShell",
                                 "tool_input": {"command": "Set-Content src/a.py 'x'"}})
    assert norm["tool_name"] == "terminal"
    assert norm["tool_input"]["command"] == "Set-Content src/a.py 'x'"
    # `cmd` alias is bridged to `command`, exactly like Bash.
    norm2 = normalize_hook_input({"tool_name": "PowerShell",
                                  "tool_input": {"cmd": "Out-File src/b.py"}})
    assert norm2["tool_name"] == "terminal"
    assert norm2["tool_input"]["command"] == "Out-File src/b.py"


@pytest.mark.parametrize("runtime", ["claude", "openhands"])
def test_normalize_unknown_tool_flagged_with_runtime(monkeypatch, runtime):
    """E-010: hook-entry.sh always passes a runtime, so the production path must
    mark an unrecognized name too — not only the runtime-less path the tests
    above exercise."""
    from modules.utils import normalize_hook_input
    monkeypatch.setenv("METODOLOJI_RUNTIME", runtime)
    norm = normalize_hook_input({"tool_name": "SomeFutureTool", "tool_input": {}})
    assert norm["tool_name"] == "unknown"
    assert norm["raw_tool_name"] == "SomeFutureTool"


@pytest.mark.parametrize("runtime", ["claude", "openhands", None])
def test_normalize_known_tools_unaffected_by_runtime(monkeypatch, runtime):
    """E-010 regression: the catch-all must not swallow known vocabularies."""
    from modules.utils import normalize_hook_input
    if runtime is None:
        monkeypatch.delenv("METODOLOJI_RUNTIME", raising=False)
    else:
        monkeypatch.setenv("METODOLOJI_RUNTIME", runtime)
    cases = {
        "Write": "file_editor",
        "Edit": "file_editor",
        "MultiEdit": "file_editor",
        "NotebookEdit": "notebook_editor",
        "Bash": "terminal",
        "PowerShell": "terminal",
        "file_editor": "file_editor",
        "terminal": "terminal",
        "notebook_editor": "notebook_editor",
    }
    for raw, expected in cases.items():
        assert normalize_hook_input({"tool_name": raw, "tool_input": {}})["tool_name"] == expected
    # Stop / SessionStart payloads carry no tool — they must stay non-tools.
    for raw in ("", None):
        assert normalize_hook_input({"tool_name": raw, "tool_input": {}})["tool_name"] in ("", None)


def test_is_code_target_src_md_not_code():
    # CODE_DIRS shortcut must not promote data files: src/*.md is docs.
    assert not is_code_target("src/README.md")
    assert not is_code_target("SRC/notes.md")
    assert not is_code_target("Src/data.json")


def test_is_code_target_toolchain_config_not_code():
    # Brownfield false-block class: bundler/ORM/TS configs are not app code.
    assert not is_code_target("prisma.config.ts")
    assert not is_code_target("backend/prisma.config.ts")
    assert not is_code_target("vite.config.ts")
    assert not is_code_target("tsconfig.json")
    assert not is_code_target("tsconfig.node.json")
    assert not is_code_target("package-lock.json")
    # Real code still gated, even next to configs.
    assert is_code_target("src/main.py")
    assert is_code_target("prisma/schema-helper.py")


def test_rel_to_root():
    assert rel_to_root("C:/proj", "C:/proj/src/x.py") == "src/x.py"
    assert rel_to_root("C:/proj", "src/x.py", "C:/proj") == "src/x.py"


def test_rel_to_root_outside_root_kept_abs():
    # A path outside the root stays as-is (normalized, not truncated).
    assert rel_to_root("C:/proj", "C:/other/x.py") == "/other/x.py"


def test_rel_to_root_empty():
    assert rel_to_root("C:/proj", "") == ""
    assert rel_to_root("C:/proj", None) == ""


def test_repo_root_env_priority(tmp_path):
    import os
    old = os.environ.pop("CLAUDE_PROJECT_DIR", None)
    envroot = str(tmp_path / "envroot")  # absolute on this platform
    os.environ["OPENHANDS_PROJECT_DIR"] = envroot
    try:
        assert repo_root({}) == os.path.abspath(envroot)
    finally:
        if old:
            os.environ["CLAUDE_PROJECT_DIR"] = old
        os.environ.pop("OPENHANDS_PROJECT_DIR", None)


def test_repo_root_json_cwd_fallback(tmp_path):
    import os
    old_c = os.environ.pop("CLAUDE_PROJECT_DIR", None)
    old_o = os.environ.pop("OPENHANDS_PROJECT_DIR", None)
    try:
        cwd = str(tmp_path / "from-json")  # absolute on this platform
        assert repo_root({"cwd": cwd}) == os.path.abspath(cwd)
    finally:
        if old_c:
            os.environ["CLAUDE_PROJECT_DIR"] = old_c
        if old_o:
            os.environ["OPENHANDS_PROJECT_DIR"] = old_o


# --- top-level payload seam (E-006) ------------------------------------------
# `cwd` arrives on the same wire channel as tool_input: a mistyped one (123,
# ["x"], {"a": 1}) must be SKIPPED, never crash repo_root — a traceback inside
# the handler is a no-decision turn the runner may read as an allow.

_NON_STRING_CWD = (123, 4.5, True, ["x"], {"a": 1}, None)


@pytest.mark.parametrize("bad_cwd", _NON_STRING_CWD)
def test_repo_root_non_string_cwd_falls_back(monkeypatch, bad_cwd):
    """A mistyped payload cwd is not a root signal: skip to process cwd (E-006)."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    # Before E-006 every shape but None raised inside _msys_to_native()
    # (AttributeError / TypeError) — no decision on guard/audit/stop.
    assert repo_root({"cwd": bad_cwd}) == repo_root({})


def test_repo_root_env_wins_over_malformed_cwd(monkeypatch, tmp_path):
    """The type guard preserves the documented priority: env root still wins."""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    assert repo_root({"cwd": 123}) == os.path.abspath(str(tmp_path))


def test_repo_root_string_cwd_still_honored(monkeypatch, tmp_path):
    """The guard must not widen: a string cwd keeps its exact behavior."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    cwd = str(tmp_path / "from-json")
    assert repo_root({"cwd": cwd}) == os.path.abspath(cwd)


def test_extract_story_key_colon():
    assert extract_story_key_from_content("# Story: S-001\n") == "S-001"


def test_extract_story_key_space():
    assert extract_story_key_from_content("# Story S-001\n") == "S-001"


def test_extract_story_key_no_match():
    assert extract_story_key_from_content("## Title\nbody") == ""


# --- tool_input shape hardening (E-003) --------------------------------------
# Hook input is a wire format from another process; malformed shapes must
# never crash the engine (traceback = no decision = fail-open risk).


def test_normalize_tool_input_non_object_becomes_command():
    """A scalar tool_input no longer satisfies any {path,command,content}
    contract, but it still carries one opaque 'stdin word/user typed a thing'
    value — carried as a synthetic terminal command instead of crashing on
    dict() coercion (E-003) or being dropped silently."""
    from modules.utils import normalize_hook_input
    norm = normalize_hook_input({"tool_name": "terminal", "tool_input": "ls"})
    assert norm["tool_input"] == {"command": "ls"}
    # Non-code-looking values never crash either.
    norm2 = normalize_hook_input({"tool_name": "terminal", "tool_input": ["ls"]})
    assert norm2["tool_input"] == {"command": "ls"}
    assert normalize_hook_input({"tool_name": "terminal", "tool_input": None})[
        "tool_input"] == {}


def test_normalize_malformed_values_coerced_not_crashing():
    """Crash-susceptible routing coefficients are stringified defensively."""
    from modules.utils import normalize_hook_input
    norm = normalize_hook_input({"tool_name": "terminal",
                                 "tool_input": {"command": ["cat", "README.md"]}})
    assert norm["tool_input"]["command"] == "cat README.md"
    # PowerShell `cmd` alias coerces the same way.
    norm2 = normalize_hook_input({"tool_name": "PowerShell",
                                  "tool_input": {"cmd": ["Out-File", "x"]}})
    assert norm2["tool_input"]["command"] == "Out-File x"
    norm3 = normalize_hook_input({"tool_name": "file_editor",
                                  "tool_input": {"path": 123}})
    assert norm3["tool_input"]["path"] == "123"
    norm4 = normalize_hook_input({"tool_name": "file_editor",
                                  "tool_input": {"path": True}})
    assert norm4["tool_input"]["path"] == "True"  # scalars coerce via str()
    # None routing key normalizes to empty ("" -> guard early-allows).
    norm5 = normalize_hook_input({"tool_name": "terminal", "tool_input": {"command": None}})
    assert norm5["tool_input"]["command"] == ""


def test_normalize_mapping_value_coerces_to_words():
    """A dict-typed routing key renders as deterministic k=v words."""
    from modules.utils import normalize_hook_input
    norm = normalize_hook_input({"tool_name": "terminal",
                                 "tool_input": {"command": {"cmd": "ls"}}})
    assert norm["tool_input"]["command"] == "cmd=ls"


def test_normalize_metadata_keys_stay_native():
    """Only keys the engine routes on are coerced; the rest pass through."""
    from modules.utils import normalize_hook_input
    norm = normalize_hook_input({"tool_name": "terminal",
                                 "tool_input": {"command": "ls",
                                                "timeout": 30}})
    assert norm["tool_input"]["timeout"] == 30

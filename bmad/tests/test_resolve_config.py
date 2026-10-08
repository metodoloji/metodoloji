"""Tests for bmad/scripts/resolve_config.py.

Run standalone: python3 -m pytest bmad/tests/test_resolve_config.py -q
Also registered under scripts/tests in pyproject.toml via this sibling path.
"""

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parent.parent.parent
    / "bmad" / "scripts" / "resolve_config.py"
)
_spec = importlib.util.spec_from_file_location("resolve_config", _SCRIPT)
rc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rc)
# Pure-function tests below use `rc`; resolver invocations load a fresh copy
# per call via _fresh_resolver (correct {metodoloji-root} per fake plugin).


def _write_toml(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_yaml(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture
def plugin(tmp_path):
    """A minimal fake plugin root with the required base config."""
    root = tmp_path / "plugin"
    _write_toml(
        root / "bmad" / "config.toml",
        """
[core]
project_name = "base-project"
output_folder = "{project-root}/docs"
document_output_language = "English"

[modules.tea]
risk_threshold = "p1"
test_stack_type = "auto"

[agents]
""",
    )
    # The resolver locates the plugin layers relative to its own path — copy it in.
    dest = root / "bmad" / "scripts"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy(_SCRIPT, dest / "resolve_config.py")
    return root


def _make_project(tmp_path: Path) -> Path:
    return tmp_path / "project"


def _in_process_argv(script: Path, project_root: Path, extra: tuple) -> list[str]:
    """Build argv for an in-process resolve_config.main() call.

    Single constructor: every test funnels through here, so a future CLI
    change touches one place instead of N subprocess command builders.
    """
    return [str(script), "--project-root", str(project_root), *extra]


def _run(plugin_root: Path, project_root: Path, *extra: str) -> dict:
    """Run the resolver IN-PROCESS (no subprocess).

    The plugin fixture copies resolve_config.py next to the fake plugin root
    so {metodoloji-root} derivation matches production. The copy is loaded
    FRESH per call (own namespace, own __file__) — a shared module import
    would pin {metodoloji-root} to the real repo and leak layers between
    tests. subprocess is deliberately avoided: repeated capture_output pipes
    exhaust inheritable handles on Windows (DuplicateHandle → WinError 6).
    """
    script = plugin_root / "bmad" / "scripts" / "resolve_config.py"
    assert script.is_file(), f"fixture copy missing: {script}"
    out = _run_argv(_fresh_resolver(script), _in_process_argv(script, project_root, extra))
    assert out["returncode"] == 0, out["stderr"]
    return json.loads(out["stdout"])


def _fresh_resolver(script: Path):
    """Load a FRESH resolve_config module from `script` (isolated namespace).

    main() derives {metodoloji-root} from its OWN __file__ — the fake plugin
    copy (fixture) vs the selfhost root must each resolve to their own tree.
    A shared module-level import would pin __file__ to the real repo for every
    test, so each call gets its own namespace (no sys.modules pollution —
    subprocess parity: a fresh interpreter per invocation).
    """
    import importlib.util

    name = f"resolve_config_{abs(hash(str(script))) % 10**8}"
    spec = importlib.util.spec_from_file_location(name, script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_argv(mod, argv: list[str]) -> dict:
    """Invoke mod.main() with argv, capturing stdout/stderr/exit code.

    main() calls parser.parse_args() (reads sys.argv) and sys.exit() on
    error — both are emulated: sys.argv is swapped, SystemExit is caught,
    stdout/stderr are captured. Returns
    {"returncode", "stdout", "stderr"}.
    """
    import contextlib
    import io

    old_argv = sys.argv
    sys.argv = list(argv)
    stdout, stderr = io.StringIO(), io.StringIO()
    returncode = 0
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            mod.main()
    except SystemExit as exc:
        returncode = exc.code if isinstance(exc.code, int) else 1
    finally:
        sys.argv = old_argv
    return {"returncode": returncode, "stdout": stdout.getvalue(), "stderr": stderr.getvalue()}


# ── Legacy YAML parser ───────────────────────────────────────────────

def test_parse_legacy_yaml_scalars_and_lists(tmp_path):
    f = tmp_path / "config.yaml"
    _write_yaml(f, """
# comment
test_artifacts: "{project-root}/bmad-output/test-artifacts"
tea_use_playwright_utils: true
tea_use_pactjs_utils: false
risk_threshold: p1
count: 3
ratio: 1.5
product_languages:
  - en
  - tr
empty_list:
""")
    data = rc.load_legacy_yaml(f)
    assert data["test_artifacts"] == "{project-root}/bmad-output/test-artifacts"
    assert data["tea_use_playwright_utils"] is True
    assert data["tea_use_pactjs_utils"] is False
    assert data["risk_threshold"] == "p1"
    assert data["count"] == 3
    assert data["ratio"] == 1.5
    assert data["product_languages"] == ["en", "tr"]
    assert data["empty_list"] == []


def test_parse_legacy_yaml_with_bom(tmp_path):
    f = tmp_path / "config.yaml"
    _write_yaml(f, "\ufeffuser_name: BOMUser\nrisk_threshold: p2\n")
    data = rc.load_legacy_yaml(f)
    assert data == {"user_name": "BOMUser", "risk_threshold": "p2"}


def test_split_legacy_core():
    data = {
        "user_name": "Yunus",
        "output_folder": "{project-root}/bmad-output",
        "risk_threshold": "p1",
    }
    core, module = rc._split_legacy_core(data)
    assert core == {"user_name": "Yunus", "output_folder": "{project-root}/bmad-output"}
    assert module == {"risk_threshold": "p1"}


# ── Legacy bridge behavior ───────────────────────────────────────────

def test_legacy_yaml_overrides_plugin_default(plugin, tmp_path):
    project = _make_project(tmp_path)
    # A project-root legacy config.yaml is a PROJECT setting: it must beat the
    # plugin's installer defaults even though it is YAML and they are TOML.
    _write_yaml(
        project / "bmad" / "tea" / "config.yaml",
        "risk_threshold: p0\ntest_stack_type: playwright\n",
    )
    out = _run(plugin, project, "--key", "modules.tea.risk_threshold")
    assert out == {"modules.tea.risk_threshold": "p0"}


def test_project_toml_beats_legacy_yaml(plugin, tmp_path):
    project = _make_project(tmp_path)
    _write_yaml(project / "bmad" / "tea" / "config.yaml", "risk_threshold: p0\n")
    _write_toml(project / "bmad" / "config.toml", '[modules.tea]\nrisk_threshold = "p2"\n')
    out = _run(plugin, project, "--key", "modules.tea.risk_threshold")
    assert out == {"modules.tea.risk_threshold": "p2"}


def test_legacy_yaml_and_plugin_toml_deep_merge(plugin, tmp_path):
    project = _make_project(tmp_path)
    # YAML overrides one key; the other plugin key survives.
    _write_yaml(project / "bmad" / "tea" / "config.yaml", "risk_threshold: p0\n")
    out = _run(plugin, project, "--key", "modules.tea")
    assert out["modules.tea"]["risk_threshold"] == "p0"
    assert out["modules.tea"]["test_stack_type"] == "auto"


def test_legacy_yaml_fills_missing_module_section(plugin, tmp_path):
    project = _make_project(tmp_path)
    _write_yaml(
        project / "bmad" / "gds" / "config.yaml",
        """
game_dev_experience: intermediate
user_name: Yunus
output_folder: "{project-root}/bmad-output"
""",
    )
    out = _run(plugin, project, "--key", "modules.gds", "--key", "core.user_name")
    assert out["modules.gds"]["game_dev_experience"] == "intermediate"
    # Core keys stamped by the legacy installer are routed to the CORE merge,
    # NOT kept in the module section — stale stamps must never shadow live core.
    assert "user_name" not in out["modules.gds"]
    assert out["core.user_name"] == "Yunus"


def test_legacy_core_fills_missing_core_section(plugin, tmp_path):
    project = _make_project(tmp_path)
    _write_yaml(
        project / "bmad" / "core" / "config.yaml",
        "user_name: Legacy\ncommunication_language: Turkish\n",
    )
    out = _run(plugin, project, "--key", "core")
    assert out["core"]["user_name"] == "Legacy"


def test_core_only_legacy_file_still_registers_module(plugin, tmp_path):
    project = _make_project(tmp_path)
    # bmad-loop-style legacy file: ONLY core stamps, no module-specific keys.
    # The module must still resolve (flat = core keys), not error.
    _write_yaml(
        project / "bmad" / "bmad-loop" / "config.yaml",
        'user_name: Yunus\noutput_folder: "{project-root}/bmad-output"\n',
    )
    out = _run(plugin, project, "--module", "bmad-loop")
    assert out["bmad-loop"]["user_name"] == "Yunus"
    assert out["bmad-loop"]["output_folder"] == "{project-root}/bmad-output"


def test_legacy_core_yaml_authority_over_module_stamps(plugin, tmp_path):
    project = _make_project(tmp_path)
    # Conflicting stamps: core/config.yaml is the authority for core keys;
    # per-module stamps only fill gaps.
    _write_yaml(project / "bmad" / "core" / "config.yaml", "user_name: CoreAuthority\n")
    _write_yaml(project / "bmad" / "tea" / "config.yaml", "user_name: StaleStamp\n")
    out = _run(plugin, project, "--key", "core.user_name")
    assert out == {"core.user_name": "CoreAuthority"}


def test_module_stamp_fills_gap_when_core_yaml_missing(plugin, tmp_path):
    project = _make_project(tmp_path)
    # No core/config.yaml — the module file's core stamp fills the gap in the
    # flat --module output (the realistic legacy skill query).
    _write_yaml(project / "bmad" / "tea" / "config.yaml", "user_name: FromStamp\n")
    out = _run(plugin, project, "--module", "tea")
    assert out["tea"]["user_name"] == "FromStamp"


def test_project_user_toml_beats_legacy_core(plugin, tmp_path):
    project = _make_project(tmp_path)
    _write_yaml(project / "bmad" / "core" / "config.yaml", "user_name: Legacy\n")
    _write_toml(project / "bmad" / "config.user.toml", '[core]\nuser_name = "Me"\n')
    out = _run(plugin, project, "--key", "core.user_name")
    assert out == {"core.user_name": "Me"}


# ── Project-root layers ──────────────────────────────────────────────

def test_project_bmad_toml_overrides_plugin_and_legacy(plugin, tmp_path):
    project = _make_project(tmp_path)
    _write_toml(
        project / "bmad" / "config.toml",
        '[modules.tea]\nrisk_threshold = "p0"\n',
    )
    out = _run(plugin, project, "--key", "modules.tea.risk_threshold")
    assert out == {"modules.tea.risk_threshold": "p0"}


def test_project_config_user_toml_overrides_project_team(plugin, tmp_path):
    project = _make_project(tmp_path)
    _write_toml(project / "bmad" / "config.toml", '[core]\nuser_name = "Team"\n')
    _write_toml(project / "bmad" / "config.user.toml", '[core]\nuser_name = "Me"\n')
    out = _run(plugin, project, "--key", "core.user_name")
    assert out == {"core.user_name": "Me"}


def test_bmad_output_layer_wins_over_project_bmad(plugin, tmp_path):
    # Legacy fallback: pre-migration bmad-output/ still beats bmad/ (backward compat).
    project = _make_project(tmp_path)
    _write_toml(project / "bmad" / "config.toml", '[core]\noutput_folder = "a"\n')
    _write_toml(project / "bmad-output" / "config.toml", '[core]\noutput_folder = "b"\n')
    out = _run(plugin, project, "--key", "core.output_folder")
    assert out == {"core.output_folder": "b"}


def test_docs_output_layer_wins_over_legacy_bmad_output(plugin, tmp_path):
    # Seçenek A kanonik: docs/ beats legacy bmad-output/ when both present.
    project = _make_project(tmp_path)
    _write_toml(project / "bmad-output" / "config.toml", '[core]\noutput_folder = "legacy"\n')
    _write_toml(project / "docs" / "config.toml", '[core]\noutput_folder = "canonical"\n')
    out = _run(plugin, project, "--key", "core.output_folder")
    assert out == {"core.output_folder": "canonical"}


def test_legacy_bmad_output_fallback_when_docs_absent(plugin, tmp_path):
    # Only legacy present → still honored (migration grace period).
    project = _make_project(tmp_path)
    _write_toml(project / "bmad-output" / "config.toml", '[core]\noutput_folder = "legacy-only"\n')
    out = _run(plugin, project, "--key", "core.output_folder")
    assert out == {"core.output_folder": "legacy-only"}


def test_project_user_output_beats_everything(plugin, tmp_path):
    project = _make_project(tmp_path)
    _write_toml(project / "bmad" / "config.toml", '[core]\nlanguage = "en"\n')
    _write_toml(project / "bmad-output" / "config.toml", '[core]\nlanguage = "tr"\n')
    _write_toml(project / "docs" / "config.user.toml", '[core]\nlanguage = "de"\n')
    out = _run(plugin, project, "--key", "core.language")
    assert out == {"core.language": "de"}


def test_project_layers_absent_is_fine(plugin, tmp_path):
    project = _make_project(tmp_path)  # no project files at all
    out = _run(plugin, project, "--key", "modules.tea.risk_threshold")
    assert out == {"modules.tea.risk_threshold": "p1"}


# ── --module flat output ─────────────────────────────────────────────

def test_module_flat_output_merges_core(plugin, tmp_path):
    project = _make_project(tmp_path)
    _write_toml(
        project / "bmad" / "config.toml",
        '[modules.wds]\nproject_type = "digital_product"\n',
    )
    out = _run(plugin, project, "--module", "wds")
    flat = out["wds"]
    assert flat["project_type"] == "digital_product"
    # core keys included (from plugin base)
    assert flat["output_folder"] == "{project-root}/docs"
    assert flat["project_name"] == "base-project"


def test_module_output_project_override_applies(plugin, tmp_path):
    project = _make_project(tmp_path)
    _write_toml(
        project / "bmad" / "config.toml",
        '[modules.tea]\nrisk_threshold = "p0"\n',
    )
    out = _run(plugin, project, "--module", "tea")
    assert out["tea"]["risk_threshold"] == "p0"


def test_module_unknown_module_errors(plugin, tmp_path):
    project = _make_project(tmp_path)
    script = plugin / "bmad" / "scripts" / "resolve_config.py"
    out = _run_argv(_fresh_resolver(script), _in_process_argv(script, project, ("--module", "nope")))
    assert out["returncode"] == 1
    assert "unknown module" in out["stderr"]


def test_module_via_legacy_yaml(plugin, tmp_path):
    project = _make_project(tmp_path)
    _write_yaml(
        project / "bmad" / "tea" / "config.yaml",
        "risk_threshold: p0\nuser_name: Yunus\n",
    )
    out = _run(plugin, project, "--module", "tea")
    assert out["tea"]["risk_threshold"] == "p0"
    assert out["tea"]["user_name"] == "Yunus"


# ── deep_merge sanity (unchanged semantics) ──────────────────────────

def test_deep_merge_tables_and_scalars():
    base = {"a": {"b": 1, "c": 2}, "list": [1, 2]}
    over = {"a": {"b": 10}, "list": [3]}
    assert rc.deep_merge(base, over) == {"a": {"b": 10, "c": 2}, "list": [1, 2, 3]}


def test_bridge_arrays_replace_not_append(plugin, tmp_path):
    project = _make_project(tmp_path)
    # Installer YAML carries the FULL value of a key, not a delta: the legacy
    # bridge must REPLACE arrays, not append ("en" + ["tr"] must not become
    # ["en", "tr"] — the real repo duplicated every list that way).
    _write_toml(plugin / "bmad" / "config.toml", '[modules.wds]\nproduct_languages = ["en"]\n')
    _write_yaml(project / "bmad" / "wds" / "config.yaml", "product_languages:\n  - tr\n")
    out = _run(plugin, project, "--module", "wds")
    assert out["wds"]["product_languages"] == ["tr"]


def test_same_file_layers_not_double_applied(tmp_path):
    # When the methodology runs AS its own project, {metodoloji-root} ==
    # {project-root} and plugin/project layers point at the SAME files.
    # Double-applying append-merged arrays duplicated every list (4 → 8).
    import shutil

    root = tmp_path / "selfhost"
    root.mkdir()
    _write_toml(
        root / "bmad" / "config.toml",
        '[modules.gds]\nprimary_platform = ["unity", "unreal", "godot", "other"]\n',
    )
    dest = root / "bmad" / "scripts"
    dest.mkdir(parents=True)
    shutil.copy(_SCRIPT, dest / "resolve_config.py")
    out = _run_argv(_fresh_resolver(dest / "resolve_config.py"), _in_process_argv(dest / "resolve_config.py", root, ("--module", "gds")))
    assert out["returncode"] == 0, out["stderr"]
    assert json.loads(out["stdout"])["gds"]["primary_platform"] == ["unity", "unreal", "godot", "other"]


def test_keyed_array_merge_by_code():
    base = {"agents": [{"code": "x", "name": "A"}, {"code": "y", "name": "B"}]}
    over = {"agents": [{"code": "y", "name": "B2"}]}
    merged = rc.deep_merge(base, over)
    assert merged["agents"] == [
        {"code": "x", "name": "A"},
        {"code": "y", "name": "B2"},
    ]


def test_modules_from_key_paths():
    assert rc._modules_from_key_paths(["modules.tea.risk_threshold", "core"]) == ["tea"]
    assert rc._modules_from_key_paths([]) == []


# --- undecodable layer (E-009) ------------------------------------------------
# UnicodeDecodeError is a ValueError but NOT a TOMLDecodeError: a BINARY layer
# escaped load_toml's handler and tracebacks. Same policy, codec message.

def test_load_toml_binary_layer_warns_not_crash(tmp_path, capsys):
    f = tmp_path / "config.toml"
    f.write_bytes(b"\xff\xfe\x00 broken")
    assert rc.load_toml(f) == {}
    err = capsys.readouterr().err
    assert "failed to parse" in err and str(f) in err


def test_load_toml_binary_required_layer_exits_one(tmp_path, capsys):
    f = tmp_path / "config.toml"
    f.write_bytes(b"\xff\xfe\x00 broken")
    with pytest.raises(SystemExit) as exc:
        rc.load_toml(f, required=True)
    assert exc.value.code == 1
    assert "failed to parse" in capsys.readouterr().err


def test_load_legacy_yaml_binary_layer_warns_not_crash(tmp_path, capsys):
    f = tmp_path / "config.yaml"
    f.write_bytes(b"\xff\xfe\x00 broken")
    assert rc.load_legacy_yaml(f) == {}
    assert "failed to decode" in capsys.readouterr().err

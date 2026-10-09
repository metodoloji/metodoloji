"""Tests for hooks/engine/modules/config.py — constants + invariants."""

import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HOOKS))

from modules.config import (  # noqa: E402
    _AGENT_ZONES,
    _first_existing,
    _HOOKS_CFG,
    _METHODOLOGY_ROOT,
    CODE_DIRS,
    FREE_PREFIXES,
    NON_CODE_EXTS,
    PLUGIN_FREE_PREFIXES,
    TAR_ARG_OPTS,
)


def test_methodology_root_is_repo_root():
    # config.py is at <root>/hooks/engine/modules/config.py → root is 4 parents up.
    # The checkout directory may be named after the repo ("metodoloji")
    # or by a local clone (e.g. "metodoloji") — pin the structural identity instead
    # of the directory's literal name.
    assert (_METHODOLOGY_ROOT / "hooks" / "engine").is_dir()
    assert (_METHODOLOGY_ROOT / "custom").is_dir()
    assert (_METHODOLOGY_ROOT / "bmad").is_dir()


def test_hooks_cfg_is_pinned_to_plugin_root():
    # F3 authority separation: hook enforcement reads the PLUGIN's own
    # custom/config.toml — never the target project's layers and never
    # cwd-relative. A target project must not be able to silently relax
    # the guard that watches it; personal *.user.toml layers don't apply.
    assert _HOOKS_CFG.is_absolute()
    assert _HOOKS_CFG.parent == _METHODOLOGY_ROOT / "custom"
    assert _HOOKS_CFG.name == "config.toml"


def test_free_prefixes_are_slashed():
    for p in FREE_PREFIXES:
        assert p.endswith("/"), f"free prefix should end with /: {p!r}"
    for p in PLUGIN_FREE_PREFIXES:
        assert p.endswith("/"), f"plugin free prefix should end with /: {p!r}"


def test_plugin_trees_not_in_plain_free_prefixes():
    # Plugin source trees must NOT be unconditionally free: in ordinary projects
    # they stay behind the experiment gate. utils.is_free() releases them only
    # when the project root IS the methodology root (self-modification).
    for tree in ("hooks/", "scripts/", "skills/"):
        assert tree not in FREE_PREFIXES, f"{tree} must be a conditional plugin-free prefix"
        assert tree in PLUGIN_FREE_PREFIXES
    for tooling in ("bmad/tests/",):
        assert tooling in PLUGIN_FREE_PREFIXES, \
            f"{tooling} is test tooling — free in self-modification only"


def test_agent_zones_are_free_prefixes():
    # Every agent zone (scratch/tmp/temp) must also be a free prefix, so free-zone
    # files are always exempt from approval.
    for z in _AGENT_ZONES:
        zone = z if z.endswith("/") else z + "/"
        assert zone in FREE_PREFIXES, f"agent zone {zone} not free"


def test_bench_zone_covers_free_prefixes_and_explore_files():
    """The gate's bench refusal must cover the guard's free surfaces.

    bench_in_free_zone is a standalone regex (the gate script runs without the
    engine), so this pins the parity contract directly on the modules it must
    mirror: every FREE_PREFIX (except VCS-internal .git/) plus the root-level
    explore_* file doctrine from utils.is_free. bench_consistency carries the
    same check as a counted live-doc problem; this unit test fails the suite
    itself on drift.
    """
    gate_scripts = _HOOKS.parents[1] / "skills" / "bmad-research-experiment" / "scripts"
    sys.path.insert(0, str(gate_scripts))
    import run_experiment as gate  # noqa: E402

    from modules.utils import is_free  # noqa: E402
    for prefix in FREE_PREFIXES:
        if prefix == ".git/":
            continue  # VCS-internal; not an agent-writable measurement vector
        assert gate.bench_in_free_zone(f"python3 {prefix}bench_probe.py") is True, (
            f"guard-free {prefix} hosts a gate-accepted bench")
    assert is_free("explore_probe.py") is True
    assert gate.bench_in_free_zone("python3 explore_probe.py") is True
    assert gate.bench_in_free_zone("python3 ./explore_probe.py") is True
    assert is_free("explore_dir/bench_probe.py") is False
    assert gate.bench_in_free_zone("python3 explore_dir/bench_probe.py") is False


def test_code_dirs_do_not_collide_with_non_code_exts():
    # CODE_DIRS are directory names, NON_CODE_EXTS are extensions — they should
    # never overlap in a way that misclassifies.
    assert "md" not in CODE_DIRS
    assert "src" not in NON_CODE_EXTS


def test_tar_arg_opts_is_frozenset():
    assert isinstance(TAR_ARG_OPTS, frozenset)
    assert "-f" in TAR_ARG_OPTS and "--directory" in TAR_ARG_OPTS


def test_first_existing():
    from modules.config import _first_existing
    assert _first_existing([Path("/nonexistent-a"), Path("/nonexistent-b")]) is None
    # With a real file, returns it.
    import tempfile
    with tempfile.NamedTemporaryFile() as f:
        assert _first_existing([Path(f.name)]) == Path(f.name)


def _clear_hooks_cache():
    from modules import config
    config._HOOKS_CACHE = None
    config._HOOKS_CACHE_KEY = None


def test_hook_gate_mode_caches_until_mtime_changes(tmp_path, monkeypatch):
    """_read_hooks_section caches per (path, mtime_ns, size): a rewrite that
    changes the file invalidates the cache, so config edits apply live."""
    import time
    from modules import config
    cfg = tmp_path / "config.toml"
    cfg.write_text('[hooks]\nquality_gate = "soft"\n', encoding="utf-8")
    monkeypatch.setattr(config, "_HOOKS_CFG", cfg)
    _clear_hooks_cache()
    assert config.hook_gate_mode("quality_gate") == "soft"
    # Rewrite with a different size (cache key includes size); mtime
    # granularity alone must not be trusted on coarse filesystems.
    cfg.write_text('[hooks]\nquality_gate = "hard"\ndeploy_guard = "hard"\n',
                   encoding="utf-8")
    assert config.hook_gate_mode("quality_gate") == "hard"
    assert config.hook_gate_mode("deploy_guard") == "hard"
    _clear_hooks_cache()


def test_blackboard_enabled_uses_cache(tmp_path, monkeypatch):
    from modules import config
    cfg = tmp_path / "config.toml"
    cfg.write_text('[hooks]\nblackboard = "off"\n', encoding="utf-8")
    monkeypatch.setattr(config, "_HOOKS_CFG", cfg)
    _clear_hooks_cache()
    assert config.blackboard_enabled() is False
    _clear_hooks_cache()


def test_hook_gate_mode_parses_hard_with_comment(tmp_path, monkeypatch):
    from modules import config
    # A [hooks] quality_gate = "hard" value followed by a # comment must parse.
    cfg = tmp_path / "config.toml"
    cfg.write_text('[hooks]\nquality_gate = "hard"   # "soft" (default) | "hard"\n',
                   encoding="utf-8")
    monkeypatch.setattr(config, "_HOOKS_CFG", cfg)
    assert config.hook_gate_mode("quality_gate") == "hard"


def test_hook_gate_mode_default_soft(tmp_path, monkeypatch):
    from modules import config
    cfg = tmp_path / "config.toml"
    cfg.write_text('[hooks]\nquality_gate = "soft"\n', encoding="utf-8")
    monkeypatch.setattr(config, "_HOOKS_CFG", cfg)
    assert config.hook_gate_mode("quality_gate") == "soft"


def test_binary_hooks_config_stays_fail_closed(tmp_path, monkeypatch):
    """A non-UTF-8 custom/config.toml must not kill the decision path (E-017).

    UnicodeDecodeError is a ValueError, not OSError, so it used to escape both
    `_read_hooks_section` and `_validate_hooks_config` — leaving the guard with
    NO decision, which a runner may read as an allow. The defaults must win so
    the code gate stays `hard` (fail-closed) and the audit still parses.
    """
    from modules import config
    cfg = tmp_path / "config.toml"
    cfg.write_bytes(b"\xff\xfe binary junk")
    monkeypatch.setattr(config, "_HOOKS_CFG", cfg)
    _clear_hooks_cache()
    assert config.hook_gate_mode("code_guard") == "hard"
    assert config.hook_gate_mode("quality_gate") == "soft"
    assert config._validate_hooks_config() == (True, "")
    _clear_hooks_cache()


def test_hook_gate_values_read_independently(tmp_path, monkeypatch):
    """quality_gate and deploy_guard are independent — hard on one must not
    leak to the other."""
    from modules import config
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[hooks]\nquality_gate = "soft"\ndeploy_guard = "hard"\n', encoding="utf-8")
    monkeypatch.setattr(config, "_HOOKS_CFG", cfg)
    assert config._hook_gate_value("quality_gate") == "soft"
    assert config._hook_gate_value("deploy_guard") == "hard"
    # hook_gate_mode is the public accessor — same independent semantics.
    assert config.hook_gate_mode("quality_gate") == "soft"
    assert config.hook_gate_mode("deploy_guard") == "hard"




def test_non_code_exts_includes_markup_and_assets():
    for ext in (".md", ".json", ".yaml", ".png", ".pdf", ".zip", ".sqlite"):
        assert ext in NON_CODE_EXTS, ext


def test_gate_defaults_code_stop_hard_quality_deploy_soft(tmp_path, monkeypatch):
    """New gates default safe: code/stop hard, quality/deploy soft."""
    from modules import config
    cfg = tmp_path / "config.toml"
    cfg.write_text('[hooks]\n', encoding="utf-8")
    monkeypatch.setattr(config, "_HOOKS_CFG", cfg)
    assert config.hook_gate_mode("code_guard") == "hard"
    assert config.hook_gate_mode("stop_guard") == "hard"
    assert config.hook_gate_mode("quality_gate") == "soft"
    assert config.hook_gate_mode("deploy_guard") == "soft"


def test_gate_explicit_soft_code_guard(tmp_path, monkeypatch):
    """Brownfield adoption: code_guard=soft parses and is independent."""
    from modules import config
    cfg = tmp_path / "config.toml"
    cfg.write_text('[hooks]\ncode_guard = "soft"\nstop_guard = "hard"\n',
                   encoding="utf-8")
    monkeypatch.setattr(config, "_HOOKS_CFG", cfg)
    assert config.hook_gate_mode("code_guard") == "soft"
    assert config.hook_gate_mode("stop_guard") == "hard"

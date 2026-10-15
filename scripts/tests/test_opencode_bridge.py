"""The OpenCode bridge must speak the engine's own contract (declared == executed).

The TypeScript plugin deliberately reimplements nothing: it forwards the raw
OpenCode tool name/input to `hooks/engine/main.py --runtime=opencode` and maps
the returned `hookSpecificOutput` back. That leaves exactly two things that
could drift silently between the bridge and the engine:

* the **fail-closed sentences** — a bridge-side engine failure must read
  byte-for-byte like the shell adapter's, or the model sees two different
  failures for one condition;
* the **OpenCode tool vocabulary** — the engine normalizes the tool names the
  bridge forwards, so a name the engine no longer knows would fall to the
  `unknown` branch (warn + allow) and open the write gate.

These tests pin both, plus the version-owned manifest joining the agreement set.
Pure text/JSON reads: no subprocess, no shell, cross-platform.
"""

import importlib.util
import json
import re
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location(
    "bump_version", PLUGIN / "scripts" / "bump-version.py")
bump = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bump)

# The two sentences hook-entry.sh / run-hook.sh print when the engine cannot run.
_NO_ENGINE = ("Methodology hook engine could not run (no python or missing "
              "engine) — fail-closed blocked.")
_NO_ROOT = "Methodology plugin root not found — fail-closed blocked."


def _read(rel: str) -> str:
    return (PLUGIN / rel).read_text(encoding="utf-8", errors="replace")


def _runtime() -> str:
    return _read("opencode-plugin/src/runtime.ts")


def test_fail_closed_sentences_match_the_shell_adapter():
    """Byte-identical fail-closed sentences across the shell and TS adapters."""
    entry = _read("hooks/scripts/hook-entry.sh")
    runner = _read("hooks/scripts/run-hook.sh")
    runtime = _runtime()
    assert _NO_ENGINE in entry, "hook-entry.sh no longer prints the engine sentence"
    assert _NO_ROOT in runner, "run-hook.sh no longer prints the plugin-root sentence"
    assert _NO_ENGINE in runtime, "runtime.ts lost the engine fail-closed sentence"
    assert _NO_ROOT in runtime, "runtime.ts lost the plugin-root fail-closed sentence"


def test_bridge_and_engine_agree_on_the_runtime_id():
    assert 'RUNTIME = "opencode"' in _runtime()
    assert '--runtime=${RUNTIME}' in _runtime()


def test_bridge_root_discovery_covers_the_shell_fallbacks():
    """runtime.ts resolves the same home-derived plugin roots the shell layer
    (the hooks.json dispatch loop and run-hook.sh) does, so an install made for
    another runtime is still found — explicit roots first, derived fallbacks
    after."""
    runtime = _runtime()
    body = runtime[runtime.index("function candidateRoots"):]
    body = body[:body.index("\n}")]
    for fragment in (".claude", "marketplaces", "cache", "yunusgungor",
                     ".openhands", "installed"):
        assert fragment in body, f"runtime.ts lost the {fragment!r} fallback"
    # the two-level cache scan (`cache/<org>/<name>/<version>`) is present
    assert "nestedChildDirs" in runtime
    # an explicit root must win over the derived fallbacks
    assert body.index("METODOLOJI_PLUGIN_ROOT") < body.index("marketplaces")


def test_engine_normalizes_the_tools_the_bridge_forwards():
    """Every gated OpenCode tool name has an engine mapping (no silent allow).

    The real V2 tool names are `patch` and `shell`; the bridge also forwards the
    older `apply_patch`/`bash` spellings, so both must stay mapped.
    """
    utils = _read("hooks/engine/modules/utils.py")
    write = re.search(r"_OPENCODE_WRITE_TOOLS\s*=\s*\{(.*?)\}", utils, re.S)
    assert write, "utils.py lost _OPENCODE_WRITE_TOOLS"
    for name in ("write", "edit", "patch", "apply_patch"):
        assert f'"{name}"' in write.group(1), f"engine no longer maps {name!r}"
    shell = re.search(r"_OPENCODE_SHELL_TOOLS\s*=\s*\{(.*?)\}", utils, re.S)
    assert shell, "utils.py lost _OPENCODE_SHELL_TOOLS"
    for name in ("shell", "bash"):
        assert f'"{name}"' in shell.group(1), f"engine no longer maps {name!r}"
    assert "_OPENCODE_READ_TOOLS" in utils


def test_bridge_forwards_raw_names_and_registers_every_hook():
    """The bridge must stay thin and register the four V2 hooks + fallback."""
    index = _read("opencode-plugin/src/index.ts")
    for needle in (
        'Plugin.define(',
        'ctx.tool.hook("execute.before"',
        'ctx.tool.hook("execute.after"',
        'ctx.session.hook("context"',
        "ctx.event.subscribe(",
        "ctx.session.synthetic(",
        'ctx.permission.hook("evaluate"',
        "ctx.tool.transform(",
    ):
        assert needle in index, f"index.ts no longer registers {needle!r}"
    # map.ts does NOT re-map names — the engine owns the vocabulary.
    mapped = _read("opencode-plugin/src/map.ts")
    assert "tool_name: typeof tool" in mapped
    for engine_name in ("file_editor", "terminal", "notebook_editor"):
        assert f'"{engine_name}"' not in mapped and f"'{engine_name}'" not in mapped, (
            f"map.ts duplicates the engine's tool mapping ({engine_name})")


def test_the_opencode_manifests_join_the_version_owned_set():
    for rel in (".opencode-plugin/plugin.json", "opencode-plugin/package.json"):
        assert rel in bump.MANIFESTS, f"{rel} left the version-owned set"
        assert (PLUGIN / rel).is_file(), f"{rel} is missing"
    # Both must be valid JSON and carry the agreed version.
    version = bump.read_version(PLUGIN)
    for rel in (".opencode-plugin/plugin.json", "opencode-plugin/package.json"):
        data = json.loads(_read(rel))
        assert data.get("version") == version, (rel, data.get("version"), version)

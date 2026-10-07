#!/usr/bin/env python3
"""
Resolve BMad's central config using an eight-layer merge.

Reads from eight layers (highest priority LAST):

  Plugin layers (installer-owned, ship with the methodology):
    1. {metodoloji-root}/bmad/config.toml              (installer team)
    2. {metodoloji-root}/bmad/config.user.toml         (installer user)
    3. {metodoloji-root}/custom/config.toml            (human-authored team, committed)
    4. {metodoloji-root}/custom/config.user.toml       (human-authored user, gitignored)

  Project layers (owned by the TARGET PROJECT — these WIN):
    5. {project-root}/bmad/config.toml                 (project team, committed)
    6. {project-root}/bmad/config.user.toml            (project user, gitignored)
    7. {project-root}/docs/config.toml                 (output-local team, committed — Seçenek A kanonik)
    8. {project-root}/docs/config.user.toml            (output-local user, gitignored)

  Legacy fallback (read-only, lower priority than 7-8, never written):
    7b. {project-root}/bmad-output/config.toml         (pre-migration team)
    8b. {project-root}/bmad-output/config.user.toml    (pre-migration user)

The project layers let a user configure BMad entirely from their own repo,
without touching the plugin. Values set there override plugin defaults.

Legacy YAML bridge (read-only): project-root per-module ``config.yaml`` files
written by older installers (e.g. ``{project-root}/bmad/tea/config.yaml``,
``{project-root}/bmad/core/config.yaml``) are merged as one layer BETWEEN the
plugin layers and the project TOML layers — they are project settings, so
they override plugin defaults and are overridden by any project TOML:

  - ``core`` section      ← {project-root}/bmad/core/config.yaml (+ config.user.yaml)
  - ``modules.<code>``    ← {project-root}/bmad/<code>/config.yaml (+ config.user.yaml)

Core keys stamped by the installer into module files (user_name,
output_folder, …) are routed ONLY to the core merge; core/config.yaml is the
authority for core keys and module-file stamps only fill gaps. Stamped
copies never enter the module section, so a stale stamp can never shadow a
live core value in ``--module`` output. The bridge is read per module code
requested (via ``--module``, implied by ``--key`` paths under
``modules.<code>``, or — in full-dump mode — discovered from the legacy
directories present); it never touches other sections.

Outputs merged JSON to stdout. Errors go to stderr.

Uses only the Python stdlib (`tomllib`) — no third-party dependencies.
BMad is standardizing on `uv run` to invoke scripts (uv provisions a suitable
interpreter for you); a plain `python3` on PATH still works during the
transition. Either runner needs Python 3.11+ for `tomllib`.

  uv run resolve_config.py --project-root /abs/path/to/project
  uv run resolve_config.py --project-root ... --key core
  uv run resolve_config.py --project-root ... --key agents
  uv run resolve_config.py --project-root ... --module tea
  uv run resolve_config.py --project-root ... --module tea --module wds

``--project-root`` is resolved with ``Path.resolve()``, i.e. relative to the
PROCESS's current working directory — a bare ``.`` works only when you invoke
the script FROM the project root. Safest: ``--project-root "$PWD"``.

``--module`` returns a FLAT object: shared ``core`` keys overlaid by that
module's own keys — exactly the shape the legacy per-module config.yaml files
had, so skills can migrate from "read bmad/tea/config.yaml" to
"run resolve_config.py --module tea" without restructuring their logic.

Merge rules (same as resolve_customization.py):
  - Scalars: override wins
  - Tables: deep merge
  - Arrays of tables where every item shares `code` or `id`: merge by that key
  - All other arrays: append

  Exception — the legacy YAML bridge REPLACES arrays instead of appending:
  installer YAML files carry full values, not deltas (see
  ``_legacy_bridge_merge``).
"""

import argparse
import json
import re
import sys
from pathlib import Path

try:
    import tomllib
except ImportError:
    sys.stderr.write(
        "error: Python 3.11+ is required (stdlib `tomllib` not found).\n"
    )
    sys.exit(3)


_MISSING = object()
_KEYED_MERGE_FIELDS = ("code", "id")

# Core keys duplicated by legacy installers into every per-module config.yaml
# (see the "Core Configuration Values" block in the shipped files).
_LEGACY_CORE_KEYS = frozenset(
    {
        "user_name",
        "communication_language",
        "document_output_language",
        "output_folder",
        "project_name",
    }
)


def load_toml(file_path: Path, required: bool = False) -> dict:
    if not file_path.exists():
        if required:
            sys.stderr.write(f"error: required config file not found: {file_path}\n")
            sys.exit(1)
        return {}
    try:
        with file_path.open("rb") as f:
            parsed = tomllib.load(f)
        if not isinstance(parsed, dict):
            return {}
        return parsed
    except tomllib.TOMLDecodeError as error:
        level = "error" if required else "warning"
        sys.stderr.write(f"{level}: failed to parse {file_path}: {error}\n")
        if required:
            sys.exit(1)
        return {}
    except OSError as error:
        level = "error" if required else "warning"
        sys.stderr.write(f"{level}: failed to read {file_path}: {error}\n")
        if required:
            sys.exit(1)
        return {}


# ─────────────────────────────────────────────────────────────────────
# Minimal YAML reader for the legacy per-module config.yaml bridge.
# The legacy files are flat installer output: scalar keys, block lists,
# comments. A tiny parser avoids a pyyaml dependency (stdlib-only rule);
# anything it cannot understand is skipped rather than guessed.
# ─────────────────────────────────────────────────────────────────────

_SCALAR_RE = re.compile(r"^([A-Za-z0-9_.-]+)\s*:\s*(.*?)\s*$")
_LIST_ITEM_RE = re.compile(r"^-\s+(.*?)\s*$")


def _parse_yaml_scalar(raw: str):
    value = raw.strip()
    if not value:
        return ""
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    lowered = value.lower()
    if lowered in ("true", "yes"):
        return True
    if lowered in ("false", "no"):
        return False
    if lowered in ("null", "~"):
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def load_legacy_yaml(file_path: Path) -> dict:
    """Parse a flat legacy config.yaml into a dict. Skip unparseable lines."""
    if not file_path.exists():
        return {}
    try:
        text = file_path.read_text(encoding="utf-8-sig")  # -sig strips a BOM
    except OSError:
        return {}
    data: dict = {}
    last_key: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        list_match = _LIST_ITEM_RE.match(stripped)
        if list_match and last_key is not None:
            current = data.get(last_key)
            if not isinstance(current, list):
                current = [] if current is None else [current]
                data[last_key] = current
            current.append(_parse_yaml_scalar(list_match.group(1)))
            continue
        kv_match = _SCALAR_RE.match(stripped)
        if kv_match:
            key, raw = kv_match.group(1), kv_match.group(2)
            if raw == "":
                data[key] = []
                last_key = key
            else:
                data[key] = _parse_yaml_scalar(raw)
                last_key = key
    return data


def _split_legacy_core(data: dict) -> tuple[dict, dict]:
    """Split a legacy per-module YAML into (core keys, module-specific keys)."""
    core = {k: v for k, v in data.items() if k in _LEGACY_CORE_KEYS}
    module = {k: v for k, v in data.items() if k not in _LEGACY_CORE_KEYS}
    return core, module


def load_legacy_module_section(project_root: Path, module_code: str) -> tuple[dict, dict]:
    """Legacy section for one module: {module-code}/config.yaml (+user).

    Returns (module_section, core_keys). The installer duplicated core keys
    (user_name, …) into every module file; those stamps are routed ONLY to
    the core merge (where later layers overwrite them) and are kept OUT of
    the module section, so a stale stamp can never shadow live core values
    in the --module flat output.
    """
    mod_dir = project_root / "bmad" / module_code
    section: dict = {}
    core: dict = {}
    for name in ("config.yaml", "config.user.yaml"):
        data = load_legacy_yaml(mod_dir / name)
        data_core, data_module = _split_legacy_core(data)
        core.update(data_core)
        section.update(data_module)
    return section, core


def discover_legacy_modules(project_root: Path) -> list[str]:
    """Module codes with legacy per-module YAML under {project-root}/bmad."""
    bmad_dir = project_root / "bmad"
    if not bmad_dir.is_dir():
        return []
    codes = []
    for child in sorted(bmad_dir.iterdir()):
        if not child.is_dir() or child.name in ("core", "scripts", "_config"):
            continue
        if (child / "config.yaml").exists() or (child / "config.user.yaml").exists():
            codes.append(child.name)
    return codes


def load_legacy_core_section(project_root: Path) -> dict:
    """Legacy fallback for the core section: core/config.yaml (+user)."""
    core_dir = project_root / "bmad" / "core"
    core: dict = {}
    for name in ("config.yaml", "config.user.yaml"):
        data = load_legacy_yaml(core_dir / name)
        for key in _LEGACY_CORE_KEYS:
            if key in data:
                core[key] = data[key]
    return core


def apply_legacy_bridge(project_root: Path, merged: dict, wanted_modules: list[str]) -> dict:
    """Apply project-root legacy YAML ON TOP of the plugin layers.

    Position in the sandwich: plugin TOML < project legacy YAML < project TOML.
    A project's legacy config.yaml is a project-root setting, so it beats the
    plugin defaults; the project's own TOML layers (applied after this) still
    beat the legacy YAML.
    """
    result = dict(merged)

    legacy_core = load_legacy_core_section(project_root)
    modules_table = dict(result.get("modules") or {})
    for code in wanted_modules:
        section, core_keys = load_legacy_module_section(project_root, code)
        # core/config.yaml is the authority for core keys; module-file stamps
        # only fill gaps (installer stamps are duplicates, never newer).
        for key, value in core_keys.items():
            legacy_core.setdefault(key, value)
        if section or core_keys:
            # Even a core-only legacy file (no module-specific keys) registers
            # the module, so `--module <code>` keeps resolving for old installs.
            modules_table[code] = _legacy_bridge_merge(
                dict(modules_table.get(code) or {}), section
            )
    if legacy_core:
        result["core"] = _legacy_bridge_merge(dict(result.get("core") or {}), legacy_core)
    if modules_table:
        result["modules"] = modules_table

    return result


def _modules_from_key_paths(keys: list[str]) -> list[str]:
    """Extract module codes from --key paths like modules.tea.risk_threshold."""
    codes: list[str] = []
    for key in keys:
        parts = key.split(".")
        if len(parts) >= 2 and parts[0] == "modules":
            codes.append(parts[1])
    return codes


def _detect_keyed_merge_field(items):
    if not items or not all(isinstance(item, dict) for item in items):
        return None
    for candidate in _KEYED_MERGE_FIELDS:
        if all(item.get(candidate) is not None for item in items):
            return candidate
    return None


def _merge_by_key(base, override, key_name):
    result = []
    index_by_key = {}
    for item in base:
        if not isinstance(item, dict):
            continue
        if item.get(key_name) is not None:
            index_by_key[item[key_name]] = len(result)
        result.append(dict(item))
    for item in override:
        if not isinstance(item, dict):
            result.append(item)
            continue
        key = item.get(key_name)
        if key is not None and key in index_by_key:
            result[index_by_key[key]] = dict(item)
        else:
            if key is not None:
                index_by_key[key] = len(result)
            result.append(dict(item))
    return result


def _merge_arrays(base, override):
    base_arr = base if isinstance(base, list) else []
    override_arr = override if isinstance(override, list) else []
    keyed_field = _detect_keyed_merge_field(base_arr + override_arr)
    if keyed_field:
        return _merge_by_key(base_arr, override_arr, keyed_field)
    return base_arr + override_arr


def deep_merge(base, override):
    if isinstance(base, dict) and isinstance(override, dict):
        result = dict(base)
        for key, over_val in override.items():
            if key in result:
                result[key] = deep_merge(result[key], over_val)
            else:
                result[key] = over_val
        return result
    if isinstance(base, list) and isinstance(override, list):
        return _merge_arrays(base, override)
    return override


def _legacy_bridge_merge(base: dict, override: dict) -> dict:
    """deep_merge variant for the legacy YAML bridge: arrays REPLACE.

    deep_merge's array rule is append (correct for additive overrides such as
    agent rosters). The bridge is different: installer YAML files hold the
    FULL value of every key, and the installer wrote identical values into
    both config.toml and config.yaml — appending there DUPLICATED every list
    (product_languages: [en, en]). At the bridge boundary arrays are replaced
    wholesale; TOML layers applied afterwards keep their own semantics.
    """
    if isinstance(base, dict) and isinstance(override, dict):
        result = dict(base)
        for key, over_val in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(over_val, dict):
                result[key] = _legacy_bridge_merge(result[key], over_val)
            else:
                result[key] = over_val
        return result
    return override


def extract_key(data, dotted_key: str):
    parts = dotted_key.split(".")
    current = data
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return _MISSING
    return current


def build_module_flat(core_section: dict, module_section: dict) -> dict:
    """Flatten shared core + one module into the legacy per-module shape.

    The module section wins: legacy core stamps never enter it (see
    load_legacy_module_section), so anything left there is a genuine
    module-level value — including deliberate per-module overrides of
    core-named keys, which are more specific than core.
    """
    flat = dict(core_section)
    flat.update(module_section)
    return flat


def resolve(project_root: Path | str, *, modules=(), keys=()) -> dict:
    """Merge the plugin + project config layers and shape the output.

    Importable twin of the CLI: `orient.py` needs one digest per session, and
    re-implementing the layer order there would drift from this one. The CLI
    below is a thin argparse shell over this function (behaviour unchanged).

    ``modules`` → flat core+module objects (repeatable), ``keys`` → dotted
    field paths, neither → the full merged dump.
    """
    modules = list(modules)
    keys = list(keys)
    project_root = Path(project_root).resolve()
    # {metodoloji-root} = derived from this script's location (plugin root).
    # When the plugin is installed, base configs live inside the plugin
    # ({metodoloji-root}/bmad, {metodoloji-root}/custom); the target project's
    # own layers live under {project-root}/bmad and {project-root}/docs
    # and OVERRIDE the plugin layers — that is the point of the project layers.
    # {project-root}/bmad-output/config.toml is the pre-migration legacy
    # fallback: read when present, always beaten by {project-root}/docs/.
    _script_dir = Path(__file__).resolve().parent  # .../bmad/scripts
    metodoloji_root = _script_dir.parent.parent     # plugin root
    bmad_dir = metodoloji_root / "bmad"
    custom_dir = metodoloji_root / "custom"
    project_bmad_dir = project_root / "bmad"
    project_output_dir = project_root / "docs"
    legacy_output_dir = project_root / "bmad-output"

    base_team = load_toml(bmad_dir / "config.toml", required=True)
    base_user = load_toml(bmad_dir / "config.user.toml")
    custom_team = load_toml(custom_dir / "config.toml")
    custom_user = load_toml(custom_dir / "config.user.toml")
    project_team = load_toml(project_bmad_dir / "config.toml")
    project_user = load_toml(project_bmad_dir / "config.user.toml")
    legacy_output_team = load_toml(legacy_output_dir / "config.toml")
    legacy_output_user = load_toml(legacy_output_dir / "config.user.toml")
    output_team = load_toml(project_output_dir / "config.toml")
    output_user = load_toml(project_output_dir / "config.user.toml")

    # Dedup guard: when the methodology runs AS its own project (this repo),
    # {metodoloji-root} == {project-root} and the plugin and project layers
    # resolve to the SAME files. Loading them twice double-applies array
    # append semantics (4-element lists became 8). Skip identical paths —
    # the later project layer has no new information to add.
    def _same_file(a: Path, b: Path) -> bool:
        return a.exists() and b.exists() and a.resolve() == b.resolve()

    if _same_file(bmad_dir / "config.toml", project_bmad_dir / "config.toml"):
        project_team = {}
    if _same_file(bmad_dir / "config.user.toml", project_bmad_dir / "config.user.toml"):
        project_user = {}
    if _same_file(custom_dir / "config.toml", project_bmad_dir / "config.toml"):
        project_team = {}
    if _same_file(custom_dir / "config.user.toml", project_bmad_dir / "config.user.toml"):
        project_user = {}

    merged = deep_merge(base_team, base_user)
    merged = deep_merge(merged, custom_team)
    merged = deep_merge(merged, custom_user)

    # Project-root legacy YAML sits between the plugin layers and the project
    # TOML layers: it overrides plugin defaults and is overridden by project TOML.
    wanted_modules = list(dict.fromkeys(modules + _modules_from_key_paths(keys)))
    if not modules and not keys:
        wanted_modules = discover_legacy_modules(project_root)  # full dump: bridge everything
    merged = apply_legacy_bridge(project_root, merged, wanted_modules)

    merged = deep_merge(merged, project_team)
    merged = deep_merge(merged, project_user)
    # Legacy output-local layers (bmad-output/) apply BEFORE the canonical
    # docs/ layers so a migrated project always wins over its own leftovers.
    merged = deep_merge(merged, legacy_output_team)
    merged = deep_merge(merged, legacy_output_user)
    merged = deep_merge(merged, output_team)
    merged = deep_merge(merged, output_user)

    if modules:
        core_section = merged.get("core") or {}
        output = {}
        for code in modules:
            if code == "core":
                # Legacy skills read core/config.yaml for shared settings only;
                # `--module core` is the flat equivalent of that file.
                output[code] = dict(core_section)
                continue
            section = (merged.get("modules") or {}).get(code)
            if section is None:
                sys.stderr.write(
                    f"error: unknown module '{code}' — no [modules.{code}] table in any layer "
                    f"and no legacy {project_root / 'bmad' / code / 'config.yaml'}\n"
                )
                sys.exit(1)
            output[code] = build_module_flat(core_section, section)
    elif keys:
        output = {}
        for key in keys:
            value = extract_key(merged, key)
            if value is not _MISSING:
                output[key] = value
    else:
        output = merged

    return output


def main():
    parser = argparse.ArgumentParser(
        description="Resolve BMad central config: four plugin layers + four project layers (project wins).",
    )
    parser.add_argument(
        "--project-root", "-p", required=True,
        help="Absolute path to the target project root; its config layers override the plugin's",
    )
    parser.add_argument(
        "--module", "-m", action="append", default=[],
        help="Module code (e.g. tea, wds) — output a flat core+module object (repeatable)",
    )
    parser.add_argument(
        "--key", "-k", action="append", default=[],
        help="Dotted field path to resolve (repeatable). Omit for full dump.",
    )
    args = parser.parse_args()

    output = resolve(args.project_root, modules=args.module, keys=args.key)
    sys.stdout.write(json.dumps(output, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()

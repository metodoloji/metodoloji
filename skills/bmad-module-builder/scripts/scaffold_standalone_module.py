#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Scaffold standalone module infrastructure into an existing skill.

Copies template files (module-setup.md, merge scripts) into the skill directory
and generates .plugin/plugin.json + .plugin/marketplace.json for distribution. The LLM writes
module.yaml and module-help.csv directly to the skill's assets/ folder before
running this script.
"""

import argparse
import json
import sys
from pathlib import Path


TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "assets" / "standalone-module-template"


def scaffold_standalone(skill_dir, module_code, module_name,
                        marketplace_dir=None, verbose=False) -> dict:
    """Scaffold standalone module infrastructure; return the JSON result dict.

    Pure function so tests can call it without a subprocess. `main()` shells out
    to it; behavior is unchanged.
    """
    skill_dir_path = Path(skill_dir).resolve()
    marketplace_dir_path = (
        Path(marketplace_dir).resolve() if marketplace_dir else skill_dir_path.parent
    )

    if not TEMPLATE_DIR.is_dir():
        return {"status": "error", "message": f"Template not found: {TEMPLATE_DIR}"}
    if not skill_dir_path.is_dir():
        return {"status": "error", "message": f"Skill directory not found: {skill_dir_path}"}
    if not (skill_dir_path / "SKILL.md").is_file():
        return {"status": "error", "message": f"No SKILL.md found in {skill_dir_path}"}
    if not (skill_dir_path / "assets" / "module.yaml").is_file():
        return {
            "status": "error",
            "message": f"assets/module.yaml not found in {skill_dir_path} — the LLM must write it before running this script",
        }

    files_created: list[str] = []
    files_skipped: list[str] = []
    warnings: list[str] = []

    assets_dir = skill_dir_path / "assets"
    assets_dir.mkdir(exist_ok=True)
    src_setup = TEMPLATE_DIR / "module-setup.md"
    dst_setup = assets_dir / "module-setup.md"
    if verbose:
        print(f"Copying module-setup.md to {dst_setup}", file=sys.stderr)
    dst_setup.write_bytes(src_setup.read_bytes())
    files_created.append("assets/module-setup.md")

    scripts_dir = skill_dir_path / "scripts"
    scripts_dir.mkdir(exist_ok=True)

    for script_name in ("merge-config.py", "merge-help-csv.py"):
        src = TEMPLATE_DIR / script_name
        dst = scripts_dir / script_name
        if dst.exists():
            msg = f"scripts/{script_name} already exists — skipped to avoid overwriting"
            files_skipped.append(f"scripts/{script_name}")
            warnings.append(msg)
            if verbose:
                print(f"SKIP: {msg}", file=sys.stderr)
        else:
            if verbose:
                print(f"Copying {script_name} to {dst}", file=sys.stderr)
            dst.write_bytes(src.read_bytes())
            dst.chmod(0o755)
            files_created.append(f"scripts/{script_name}")

    plugin_dir = marketplace_dir_path / ".plugin"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    marketplace_json = plugin_dir / "marketplace.json"

    module_yaml_path = skill_dir_path / "assets" / "module.yaml"
    module_description = ""
    module_version = "1.0.0"
    try:
        yaml_text = module_yaml_path.read_text(encoding="utf-8")
        for line in yaml_text.splitlines():
            stripped = line.strip()
            if stripped.startswith("description:"):
                module_description = stripped.split(":", 1)[1].strip().strip('"').strip("'")
            elif stripped.startswith("module_version:"):
                module_version = stripped.split(":", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass

    skill_dir_name = skill_dir_path.name
    marketplace_name = f"bmad-{module_code}"
    marketplace_data = {
        "name": marketplace_name,
        "owner": {"name": ""},
        "license": "",
        "homepage": "",
        "repository": "",
        "keywords": ["bmad"],
        "plugins": [
            {
                "name": marketplace_name,
                "source": "./",
                "description": module_description,
                "version": module_version,
                "author": {"name": ""},
                "skills": [f"./{skill_dir_name}"],
            }
        ],
    }

    if verbose:
        print(f"Writing marketplace.json to {marketplace_json}", file=sys.stderr)
    marketplace_json.write_text(
        json.dumps(marketplace_data, indent=2) + "\n", encoding="utf-8"
    )
    files_created.append(".plugin/marketplace.json")

    plugin_manifest = {
        "name": marketplace_name,
        "version": module_version,
        "description": module_description,
    }
    plugin_json = plugin_dir / "plugin.json"
    if verbose:
        print(f"Writing plugin.json to {plugin_json}", file=sys.stderr)
    plugin_json.write_text(
        json.dumps(plugin_manifest, indent=2) + "\n", encoding="utf-8"
    )
    files_created.append(".plugin/plugin.json")

    return {
        "status": "success",
        "skill_dir": str(skill_dir_path),
        "module_code": module_code,
        "files_created": files_created,
        "files_skipped": files_skipped,
        "warnings": warnings,
        "marketplace_json": str(marketplace_json),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scaffold standalone module infrastructure into an existing skill"
    )
    parser.add_argument("--skill-dir", required=True,
                        help="Path to the existing skill directory (must contain SKILL.md)")
    parser.add_argument("--module-code", required=True,
                        help="Module code (2-4 letter abbreviation, e.g. 'exc')")
    parser.add_argument("--module-name", required=True,
                        help="Module display name (e.g. 'Excalidraw Tools')")
    parser.add_argument("--marketplace-dir", default=None,
                        help="Directory to create .plugin/ in (defaults to skill-dir parent)")
    parser.add_argument("--verbose", action="store_true", help="Print progress to stderr")
    args = parser.parse_args()

    result = scaffold_standalone(
        args.skill_dir, args.module_code, args.module_name,
        args.marketplace_dir, args.verbose,
    )
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "success" else 2


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Scaffold a BMad module setup skill from template.

Copies the setup-skill-template into the target directory as {code}-setup/,
then writes the generated module.yaml and module-help.csv into the assets folder
and updates the SKILL.md frontmatter with the module's identity.
"""

import argparse
import json
import shutil
import sys
from pathlib import Path


TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "assets" / "setup-skill-template"


def scaffold_setup_skill(target_dir, module_code, module_name,
                         module_yaml, module_csv, verbose=False) -> dict:
    """Scaffold a setup skill; return the JSON result dict.

    Pure function so tests can call it without a subprocess. `main()` shells out
    to it; behavior is unchanged.
    """
    setup_skill_name = f"{module_code}-setup"
    target = Path(target_dir) / setup_skill_name

    if not TEMPLATE_DIR.is_dir():
        return {"status": "error", "message": f"Template not found: {TEMPLATE_DIR}"}
    for source_path in (module_yaml, module_csv):
        if not Path(source_path).is_file():
            return {"status": "error", "message": f"Source file not found: {source_path}"}
    target_dir_path = Path(target_dir)
    if not target_dir_path.is_dir():
        return {"status": "error", "message": f"Target directory not found: {target_dir_path}"}

    if target.exists():
        if verbose:
            print(f"Removing existing {setup_skill_name}/", file=sys.stderr)
        shutil.rmtree(target)

    if verbose:
        print(f"Copying template to {target}", file=sys.stderr)
    shutil.copytree(TEMPLATE_DIR, target)

    skill_md = target / "SKILL.md"
    content = skill_md.read_text(encoding="utf-8")
    content = content.replace("{setup-skill-name}", setup_skill_name)
    content = content.replace("{module-name}", module_name)
    content = content.replace("{module-code}", module_code)
    skill_md.write_text(content, encoding="utf-8")

    (target / "assets" / "module.yaml").write_text(
        Path(module_yaml).read_text(encoding="utf-8"), encoding="utf-8")
    (target / "assets" / "module-help.csv").write_text(
        Path(module_csv).read_text(encoding="utf-8"), encoding="utf-8")

    files_created = sorted(
        str(p.relative_to(target)) for p in target.rglob("*") if p.is_file()
    )

    return {
        "status": "success",
        "setup_skill": setup_skill_name,
        "location": str(target),
        "files_created": files_created,
        "files_count": len(files_created),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scaffold a BMad module setup skill from template"
    )
    parser.add_argument("--target-dir", required=True,
                        help="Directory to create the setup skill in (the user's skills folder)")
    parser.add_argument("--module-code", required=True,
                        help="Module code (2-4 letter abbreviation, e.g. 'cis')")
    parser.add_argument("--module-name", required=True,
                        help="Module display name (e.g. 'Creative Intelligence Suite')")
    parser.add_argument("--module-yaml", required=True,
                        help="Path to the generated module.yaml content file")
    parser.add_argument("--module-csv", required=True,
                        help="Path to the generated module-help.csv content file")
    parser.add_argument("--verbose", action="store_true", help="Print progress to stderr")
    args = parser.parse_args()

    result = scaffold_setup_skill(
        args.target_dir, args.module_code, args.module_name,
        args.module_yaml, args.module_csv, args.verbose,
    )
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "success" else 2


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Check that documented runtime coverage includes every mapped gate tool.

This is a static drift check, not a host-runtime integration test. It reads
the actual hook manifest and adapter mapping declarations, then checks that
the runtime matrix names those calls so the documented boundary cannot silently
lag behind a code change.
"""
from __future__ import annotations

import ast
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
MATRIX = ROOT / "docs" / "runtime-security-coverage.md"


def _assignments(path: pathlib.Path) -> dict[str, ast.AST]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            names = [target.id for target in node.targets
                     if isinstance(target, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
        else:
            continue
        for name in names:
            found[name] = node.value
    return found


def _literal_names(node: ast.AST | None) -> set[str]:
    if node is None:
        return set()
    if isinstance(node, ast.Dict):
        values = [key for key in node.keys]
    elif isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        values = list(node.elts)
    elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
          and node.func.id in {"set", "frozenset"} and node.args):
        return _literal_names(node.args[0])
    else:
        return set()
    out: set[str] = set()
    for value in values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            out.add(value.value)
    return out


def _hook_matchers() -> set[str]:
    manifest = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    tools: set[str] = set()
    for hook in manifest.get("hooks", {}).get("PreToolUse", []):
        matcher = hook.get("matcher", "")
        tools.update(token for token in matcher.split("|") if token)
    return tools


def _runtime_rows() -> dict[str, str]:
    rows: dict[str, str] = {}
    for line in MATRIX.read_text(encoding="utf-8").splitlines():
        for runtime in ("Claude Code", "OpenHands", "OpenCode", "Hermes"):
            if line.startswith(f"| {runtime} |"):
                rows[runtime] = line
    return rows


def main() -> int:
    rows = _runtime_rows()
    utils = _assignments(ROOT / "hooks" / "engine" / "modules" / "utils.py")
    hermes = _assignments(ROOT / "hermes_adapter.py")

    expected = {
        "Claude Code": _hook_matchers(),
        "OpenHands": {"file_editor", "terminal"},
        "OpenCode": (_literal_names(utils.get("_OPENCODE_WRITE_TOOLS"))
                     | _literal_names(utils.get("_OPENCODE_SHELL_TOOLS"))),
        "Hermes": (_literal_names(utils.get("_HERMES_WRITE_TOOLS"))
                   | _literal_names(utils.get("_HERMES_SHELL_TOOLS"))
                   | _literal_names(hermes.get("GATED_TOOLS"))),
    }
    errors: list[str] = []
    for runtime, names in expected.items():
        row = rows.get(runtime)
        if row is None:
            errors.append(f"runtime matrix row missing: {runtime}")
            continue
        if not names:
            errors.append(f"could not derive mapped tool names for {runtime}")
            continue
        missing = sorted(name for name in names if name not in row)
        if missing:
            errors.append(f"{runtime} row omits mapped calls: {', '.join(missing)}")
        else:
            print(f"[OK] {runtime}: documented {len(names)} mapped call names")
    if errors:
        for error in errors:
            print(f"[ERROR] {error}", file=sys.stderr)
        return 1
    print("[OK] runtime matrix matches hook/adaptor tool declarations")
    return 0


if __name__ == "__main__":
    sys.exit(main())

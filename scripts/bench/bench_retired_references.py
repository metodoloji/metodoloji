#!/usr/bin/env python3
"""No shipped surface names a retired artifact (E-158).

    python3 scripts/bench/bench_retired_references.py --samples docs/experiments/E-158/raw/samples.jsonl
    python3 scripts/bench/bench_retired_references.py --broken   # rule-7 control

E-152 retired dead docs and E-156 retired dead benches, but both rules read
PROSE. The surfaces that EXECUTE a named artifact were never measured, and
commit 2064fa9 left three dangling at once: both shipped eval adapters invoke
the `run_9router.py` shim that commit deleted (so `run_evals.py` answers
FileNotFoundError for every case of the two suites that still ship), a bench
imports a judge that no longer exists, and two shipped shell scripts invoke
`python -m optimization.cli` from the deleted package. Each is the E-152
half-removal one surface over: a live instruction that resolves to nothing.

The population is closed and DERIVED from the tree, never hand-listed — the
defect E-039 named. Four probes, each enumerating its whole family:

  1. every `{repo-root}` target of a shipped eval adapter;
  2. every `-m <module>` invocation in a shipped shell script;
  3. every public module-level `Path` constant of a shipped bench (its
     declared input files);
  4. every `skills/<x>/evals/<file>` path a shipped skill doc names.

`--broken` injects one phantom reference per family, so each probe can be seen
to fail on the same claim. Stdlib only, offline, deterministic.
"""

from __future__ import annotations

import argparse
import ast
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
BROKEN = False

# A `-m` invocation: the interpreter may be a bare name, a quoted variable or
# the host python (`"$PY" -m optimization.cli`), so the token is not pinned.
_RUNNER = re.compile(
    r'(?:"?\$\{?[A-Za-z_]\w*\}?"?|python[0-9.]*)\s+-m\s+([A-Za-z_][\w.]*)')
# Tool runners that live outside the tree: fixed so the probe never depends on
# what this host happens to have installed.
_EXTERNAL_RUNNERS = frozenset({
    "pip", "pytest", "venv", "virtualenv", "uv", "pipx", "build", "twine",
    "coverage", "mypy", "ruff", "black", "flake8", "pydoc",
})
_EVAL_DOC_PATH = re.compile(
    r"skills/[A-Za-z0-9_.-]+/evals/[A-Za-z0-9_./-]+\.(?:py|json|jsonl|md)")
# A bench constant named with a leading underscore is internal, or a deliberate
# `--broken` overlay (`_PHANTOM`), never a declared input.
_OVERLAY_PREFIX = "_"
_PHANTOM = ROOT / "skills" / "ghost-eval" / "evals" / "run_ghost.py"

def _exists(path: pathlib.Path) -> bool:
    if BROKEN and path == _PHANTOM:
        return True
    return path.exists()

# --- probe 1: eval adapters ---------------------------------------------------

def _case_adapter_targets(root: pathlib.Path) -> tuple[bool, str]:
    problems, checked = [], 0
    adapters = sorted((root / "skills").glob("*/evals/adapter*.json"))
    for path in adapters:
        rel = path.relative_to(root).as_posix()
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            problems.append(f"{rel}: unreadable ({exc})")
            continue
        invocation = cfg.get("invocation") if isinstance(cfg, dict) else None
        for token in invocation or []:
            if not isinstance(token, str) or "{repo-root}/" not in token:
                continue
            checked += 1
            target = root / token.split("{repo-root}/", 1)[1]
            if not _exists(target):
                problems.append(f"{rel} -> {token}")
    if BROKEN:
        checked += 1
        problems.append("phantom adapter -> {repo-root}/skills/ghost-eval/evals/run_ghost.py")
    if problems:
        return False, "; ".join(problems[:3])
    return True, f"{checked} repo-root target(s) across {len(adapters)} adapter(s) resolve"

# --- probe 2: shell scripts ---------------------------------------------------

def _shell_scripts(root: pathlib.Path) -> list[pathlib.Path]:
    return (sorted((root / "scripts").glob("*.sh"))
            + sorted((root / "hooks" / "scripts").glob("*.sh")))

def _module_ships(root: pathlib.Path, module: str) -> bool:
    top = module.split(".")[0]
    if top in sys.stdlib_module_names or top in _EXTERNAL_RUNNERS:
        return True
    return (root / top).is_dir() or (root / f"{top}.py").is_file()

def _case_shell_module_targets(root: pathlib.Path) -> tuple[bool, str]:
    problems, checked = [], 0
    scripts = _shell_scripts(root)
    for path in scripts:
        rel = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            problems.append(f"{rel}: unreadable ({exc})")
            continue
        for module in _RUNNER.findall(text):
            checked += 1
            if not _module_ships(root, module):
                problems.append(f"{rel} -> -m {module}")
    if BROKEN:
        checked += 1
        problems.append("phantom script -> -m ghost_eval.cli")
    if problems:
        return False, "; ".join(problems[:3])
    return True, f"{checked} -m target(s) across {len(scripts)} script(s) resolve"

# --- probe 3: bench input constants ------------------------------------------

def _path_value(node: ast.AST, env: dict[str, pathlib.Path]) -> pathlib.Path | None:
    """Evaluate a module-level path expression; a bare string is not a path.

    Only `/` chains rooted at a Path count, so a git rev (`PREFIX_REV`) or a
    version string is never mistaken for a file the bench reads.
    """
    if isinstance(node, ast.Name):
        return env.get(node.id)
    if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "Path" \
            and node.args and isinstance(node.args[0], ast.Constant):
        return pathlib.Path(str(node.args[0].value))
    if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "resolve" \
            and node.args:
        return _path_value(node.args[0], env)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _path_value(node.left, env)
        right = node.right
        if left is not None and isinstance(right, ast.Constant) \
                and isinstance(right.value, str):
            return left / right.value
    return None

def _bench_inputs(root: pathlib.Path) -> list[tuple[str, pathlib.Path]]:
    found: list[tuple[str, pathlib.Path]] = []
    for path in sorted((root / "scripts" / "bench").glob("bench_*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue  # bench_instrument_health owns an unparseable bench
        env: dict[str, pathlib.Path] = {"ROOT": root}
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                    and isinstance(node.targets[0], ast.Name):
                value = _path_value(node.value, env)
                if value is not None:
                    env[node.targets[0].id] = value
        for name, value in env.items():
            if name == "ROOT" or name.startswith(_OVERLAY_PREFIX):
                continue
            found.append((f"{path.name}:{name}", value))
    return found

def _case_bench_inputs(root: pathlib.Path) -> tuple[bool, str]:
    inputs = _bench_inputs(root)
    problems = [f"{label} -> {value.relative_to(root) if root in value.parents else value}"
                for label, value in inputs if not _exists(value)]
    if BROKEN:
        problems.append("phantom bench -> ghost/input.jsonl")
    if problems:
        return False, "; ".join(problems[:3])
    return True, f"{len(inputs)} declared bench input(s) resolve"

# --- probe 4: skill docs naming eval assets ----------------------------------

def _skill_docs(root: pathlib.Path) -> list[pathlib.Path]:
    return sorted((root / "skills").glob("**/*.md"))

def _case_eval_doc_paths(root: pathlib.Path) -> tuple[bool, str]:
    problems, checked = [], 0
    docs = _skill_docs(root)
    for path in docs:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = path.relative_to(root).as_posix()
        for token in _EVAL_DOC_PATH.findall(text):
            checked += 1
            if not _exists(root / token):
                problems.append(f"{rel} -> {token}")
    if BROKEN:
        checked += 1
        problems.append("phantom doc -> skills/ghost-eval/evals/run_ghost.py")
    if problems:
        return False, "; ".join(sorted(set(problems))[:3])
    return True, f"{checked} eval path(s) named by {len(docs)} skill doc(s) resolve"

PROBES = (
    ("every eval adapter target ships", _case_adapter_targets),
    ("every shell -m target ships", _case_shell_module_targets),
    ("every bench input ships", _case_bench_inputs),
    ("every eval path a skill doc names ships", _case_eval_doc_paths),
)

def run(root: pathlib.Path) -> list[dict]:
    results = []
    for name, probe in PROBES:
        try:
            ok, detail = probe(root)
        except Exception as exc:  # a crashed probe is a failed check, never a pass
            ok, detail = False, f"probe crashed: {type(exc).__name__}: {exc}"
        results.append({"check": name, "ok": bool(ok), "detail": detail})
    return results

def main() -> int:
    global BROKEN
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--broken", action="store_true", help="rule-7 control arm")
    parser.add_argument("--samples", help="write per-probe JSONL samples here")
    args = parser.parse_args()
    BROKEN = args.broken

    results = run(ROOT)
    for result in results:
        print(f"  {'ok  ' if result['ok'] else 'FAIL'}  {result['check']}: {result['detail']}")
    passed = sum(1 for result in results if result["ok"])
    if args.samples:
        path = pathlib.Path(args.samples)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(result, ensure_ascii=False) + "\n"
                                for result in results), encoding="utf-8")
    print(f"retired_reference_accuracy={passed / len(results):.2f} ({passed}/{len(results)})")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

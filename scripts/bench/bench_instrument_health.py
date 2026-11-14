#!/usr/bin/env python3
"""Every shipped bench can start (E-156).

    python3 scripts/bench/bench_instrument_health.py --samples docs/experiments/E-156/raw/samples.jsonl
    python3 scripts/bench/bench_instrument_health.py --broken   # rule-7 control

E-154 bound the audit family to surfaces CI runs. The family one directory over
was still unbound — and four of its 43 instruments could not start at all:
bench_optimization, bench_gate_feasibility, bench_promotion_integrity and
bench_upstream import the `optimization` package that commit 2064fa9 deleted,
so each dies with ImportError before parsing an argument. A bench that cannot
start is the same dead instruction as an unrun audit: the drift it exists for
is invisible until someone happens to run it.

Three probes over every `bench_*.py`: it parses, every non-stdlib import
resolves, and every CLI (argparse) bench answers `--help`. `--broken` runs the
same probes against the pre-change tree, which still ships the four.

Stdlib only, offline, deterministic.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
from importlib.machinery import PathFinder

ROOT = pathlib.Path(__file__).resolve().parents[2]
PREFIX_REV = "b36ede7"  # E-155: the last commit before the E-156 fix
BROKEN = False
HELP_TIMEOUT = 20

_PREFIX_CACHE: pathlib.Path | None = None


def _tree() -> pathlib.Path:
    global _PREFIX_CACHE
    if not BROKEN:
        return ROOT
    if _PREFIX_CACHE is not None:
        return _PREFIX_CACHE
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="instrument-health-"))
    out = subprocess.run(["git", "-C", str(ROOT), "archive", PREFIX_REV],
                         capture_output=True, stdin=subprocess.DEVNULL)
    if out.returncode != 0:
        raise RuntimeError(f"cannot archive {PREFIX_REV}: {out.stderr[:200]!r}")
    subprocess.run(["tar", "-x", "-C", str(tmp)], input=out.stdout, check=True)
    _PREFIX_CACHE = tmp
    return tmp


def _benches(root: pathlib.Path) -> list[pathlib.Path]:
    return sorted((root / "scripts" / "bench").glob("bench_*.py"))


def _search_paths(root: pathlib.Path) -> list[str]:
    return [str(root), str(root / "scripts"), str(root / "hooks" / "engine"),
            str(root / "scripts" / "bench")]


def _names_on_disk(root: pathlib.Path) -> set[str]:
    """Module-ish names that exist somewhere: the dynamic-loader escape.

    Four benches load a module by path (`spec_from_file_location`) instead of
    importing it from a package, so a strict find_spec would accuse them.
    """
    names: set[str] = set()
    skip = {".git", ".venv", "node_modules", "__pycache__"}
    for path in root.rglob("*"):
        if any(part in skip for part in path.parts):
            continue
        if path.suffix == ".py":
            names.add(path.stem)
        elif path.is_dir():
            names.add(path.name)
    return names


def _imports(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif (isinstance(node, ast.ImportFrom) and node.level == 0
              and node.module):
            names.add(node.module.split(".")[0])
    return names - sys.stdlib_module_names


def _dead_imports(root: pathlib.Path, path: pathlib.Path,
                  on_disk: set[str]) -> list[str]:
    search = _search_paths(root)
    dead = []
    for name in sorted(_imports(path)):
        if PathFinder.find_spec(name, search) is not None:
            continue
        if importlib.util.find_spec(name) is not None:
            continue
        if name in on_disk:
            continue
        dead.append(name)
    return dead


# --- the probes ---------------------------------------------------------------

def _case_every_bench_parses(root: pathlib.Path) -> tuple[bool, str]:
    benches = _benches(root)
    if not benches:
        return False, "scripts/bench ships no bench_*.py"
    broken = []
    for path in benches:
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            broken.append(f"{path.name}: {exc.msg} (line {exc.lineno})")
    if broken:
        return False, "; ".join(broken[:3])
    return True, f"all {len(benches)} benches parse"


def _case_every_import_resolves(root: pathlib.Path) -> tuple[bool, str]:
    on_disk = _names_on_disk(root)
    dead = {}
    for path in _benches(root):
        missing = _dead_imports(root, path, on_disk)
        if missing:
            dead[path.name] = missing
    if dead:
        shown = "; ".join(f"{name}: {names}" for name, names in list(dead.items())[:3])
        more = "…" if len(dead) > 3 else ""
        return False, (f"{len(dead)} bench(es) import a module that does not ship "
                       f"({shown}{more}) — they die before measuring anything")
    return True, f"every non-stdlib import of {len(_benches(root))} benches resolves"


def _case_every_cli_bench_answers_help(root: pathlib.Path) -> tuple[bool, str]:
    broken = []
    checked = 0
    for path in _benches(root):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "argparse" not in text:
            continue  # a script with no CLI is measured by the other probes
        checked += 1
        try:
            proc = subprocess.run(
                [sys.executable, str(path), "--help"], cwd=root,
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=HELP_TIMEOUT,
                stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            broken.append(f"{path.name}: --help timed out after {HELP_TIMEOUT}s")
            continue
        if proc.returncode != 0:
            tail = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()[-1:]
            broken.append(f"{path.name}: exit {proc.returncode}"
                          + (f" ({tail[0]})" if tail else ""))
    if broken:
        return False, "; ".join(broken[:3]) + ("…" if len(broken) > 3 else "")
    return True, f"all {checked} CLI benches answer --help"


PROBES = (
    ("every shipped bench parses", _case_every_bench_parses),
    ("every bench import resolves", _case_every_import_resolves),
    ("every CLI bench answers --help", _case_every_cli_bench_answers_help),
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

    results = run(_tree())
    for result in results:
        print(f"  {'ok  ' if result['ok'] else 'FAIL'}  {result['check']}: {result['detail']}")
    passed = sum(1 for result in results if result["ok"])
    if args.samples:
        path = pathlib.Path(args.samples)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(result, ensure_ascii=False) + "\n"
                                for result in results), encoding="utf-8")
    print(f"instrument_health_accuracy={passed / len(results):.2f} ({passed}/{len(results)})")
    if _PREFIX_CACHE is not None:
        shutil.rmtree(_PREFIX_CACHE, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""A check that cannot run is not a failed check (E-153).

    python3 scripts/bench/bench_measurement_integrity.py --samples docs/experiments/E-153/raw/samples.jsonl
    python3 scripts/bench/bench_measurement_integrity.py --broken   # rule-7 control

Measured 2026-11-14: `python3 scripts/bench/bench_consistency.py` reported
5/120 failed, and three of those five disappeared under the project's `.venv`
interpreter (118/120). The three — the pytest falsifier, the documented test
count and the registry isolation test — ran `sys.executable -m pytest`, got
"No module named pytest", and reported the suite as red. It never ran. The
OpenCode bridge check had the same shape: its suite imports the declared
dependency `@opencode-ai/plugin`, and a checkout without `npm install` read as
a code failure.

One rule, measured here over a closed set: a check that cannot run reports
SKIP with the environment named, and a check that CAN run is still allowed to
fail. `--broken` loads bench_consistency from the pre-change commit, where no
resolver exists and `sys.executable` is hard-coded, so the control scores low
for exactly the reasons this change names.

Deterministic, offline, stdlib only.
"""

from __future__ import annotations

import argparse
import importlib.util
import inspect
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
MODULE_REL = "scripts/bench/bench_consistency.py"
PREFIX_REV = "ac0c05b"  # E-152: the last commit before the E-153 fix
BROKEN = False

_PREFIX_CACHE: pathlib.Path | None = None


def _tree() -> pathlib.Path:
    global _PREFIX_CACHE
    if not BROKEN:
        return ROOT
    if _PREFIX_CACHE is not None:
        return _PREFIX_CACHE
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="measurement-integrity-"))
    out = subprocess.run(["git", "-C", str(ROOT), "archive", PREFIX_REV],
                         capture_output=True, stdin=subprocess.DEVNULL)
    if out.returncode != 0:
        raise RuntimeError(f"cannot archive {PREFIX_REV}: {out.stderr[:200]!r}")
    subprocess.run(["tar", "-x", "-C", str(tmp)], input=out.stdout, check=True)
    _PREFIX_CACHE = tmp
    return tmp


def _load(tree: pathlib.Path):
    path = tree / MODULE_REL
    spec = importlib.util.spec_from_file_location(
        "bench_consistency_e153" + ("_broken" if BROKEN else ""), path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- the probes ---------------------------------------------------------------

def _case_resolver_ships(mod) -> tuple[bool, str]:
    missing = [name for name in ("_pytest_candidates", "_pytest_interpreter")
               if not hasattr(mod, name)]
    if missing:
        return False, f"no interpreter resolver: missing {missing}"
    return True, "the resolver ships (_pytest_candidates + _pytest_interpreter)"


def _case_resolver_finds_pytest(mod) -> tuple[bool, str]:
    if not hasattr(mod, "_pytest_interpreter"):
        return False, "no resolver to probe"
    mod._PYTEST_PYTHON.clear()
    try:
        python = mod._pytest_interpreter()
    finally:
        mod._PYTEST_PYTHON.clear()
    if not python:
        return False, ("the resolver found no interpreter with pytest, although "
                       "this bench is itself running on a host that has one")
    proc = subprocess.run([python, "-m", "pytest", "--version"],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=120,
                          stdin=subprocess.DEVNULL)
    if proc.returncode != 0:
        return False, f"the resolved interpreter {python} cannot import pytest"
    return True, f"resolved {python}"


def _case_uses_resolver(mod, name: str, label: str) -> tuple[bool, str]:
    if not hasattr(mod, "_pytest_interpreter"):
        return False, f"{label}: no resolver seam"
    source = inspect.getsource(getattr(mod, name))
    if "_pytest_interpreter(" not in source:
        return False, f"{label}: does not resolve an interpreter"
    if "sys.executable" in source:
        return False, f"{label}: still hard-codes sys.executable"
    return True, f"{label}: measures under the resolved interpreter"


def _case_falsifier_uses_resolver(mod) -> tuple[bool, str]:
    return _case_uses_resolver(mod, "_run_pytest", "the falsifier")


def _case_documented_count_uses_resolver(mod) -> tuple[bool, str]:
    return _case_uses_resolver(mod, "_check_documented_test_count_is_honest",
                               "the documented-count check")


def _case_registry_isolation_uses_resolver(mod) -> tuple[bool, str]:
    return _case_uses_resolver(mod, "_run_registry_isolation",
                               "the registry-isolation check")


def _case_skips_without_pytest(mod, name: str, label: str) -> tuple[bool, str]:
    if not hasattr(mod, "_pytest_interpreter"):
        return False, (f"{label}: without a resolver an interpreter lacking pytest "
                       "is still reported as a failure")
    previous = os.environ.get("METODOLOJI_PYTEST_PYTHON")
    os.environ["METODOLOJI_PYTEST_PYTHON"] = ""
    mod._PYTEST_PYTHON.clear()
    try:
        verdict = getattr(mod, name)()
    finally:
        if previous is None:
            os.environ.pop("METODOLOJI_PYTEST_PYTHON", None)
        else:
            os.environ["METODOLOJI_PYTEST_PYTHON"] = previous
        mod._PYTEST_PYTHON.clear()
    if not isinstance(verdict, mod._SkippedCheck):
        return False, f"{label}: without pytest the check returned {verdict!r}, not a skip"
    if "pytest" not in verdict.reason:
        return False, f"{label}: the skip does not name pytest: {verdict.reason}"
    return True, f"{label}: skipped, and the reason names the environment"


def _case_falsifier_skips_without_pytest(mod) -> tuple[bool, str]:
    return _case_skips_without_pytest(mod, "_run_pytest", "the falsifier")


def _case_documented_count_skips_without_pytest(mod) -> tuple[bool, str]:
    return _case_skips_without_pytest(mod, "_check_documented_test_count_is_honest",
                                      "the documented-count check")


def _case_registry_isolation_skips_without_pytest(mod) -> tuple[bool, str]:
    return _case_skips_without_pytest(mod, "_run_registry_isolation",
                                      "the registry-isolation check")


class _FakeRun:
    def __init__(self, returncode: int, stdout: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


def _case_red_suite_still_fails(mod) -> tuple[bool, str]:
    """A skip path must not swallow a verdict: rc=1 still reads as failed."""
    if not hasattr(mod, "_pytest_interpreter"):
        return False, "no resolver seam; a red suite cannot be told from a host problem"
    real = mod.subprocess.run

    def fake(argv, **kwargs):
        if "--version" in argv:
            return _FakeRun(0, "pytest 9.2.0\n")
        return _FakeRun(1, "1 failed, 0 passed in 0.01s\n")

    mod._PYTEST_PYTHON.clear()
    mod.subprocess.run = fake
    try:
        verdict = mod._run_pytest()
    finally:
        mod.subprocess.run = real
        mod._PYTEST_PYTHON.clear()
    if isinstance(verdict, mod._SkippedCheck):
        return False, f"a red suite was reported as skipped: {verdict.reason}"
    if not isinstance(verdict, str) or "exited 1" not in verdict:
        return False, f"a red suite did not read as failed: {verdict!r}"
    return True, "a red suite still reads as failed"


def _case_opencode_missing_dependency_skips(mod) -> tuple[bool, str]:
    if not hasattr(mod, "_opencode_verdict"):
        return False, "no OpenCode verdict helper"
    with tempfile.TemporaryDirectory() as tmp:
        verdict = mod._opencode_verdict(
            pathlib.Path(tmp), 1,
            "Error [ERR_MODULE_NOT_FOUND]: Cannot find package '@opencode-ai/plugin'")
    if not isinstance(verdict, mod._SkippedCheck):
        return False, f"a missing declared dependency read as {verdict!r}, not a skip"
    if "npm install" not in verdict.reason:
        return False, f"the skip does not name the remedy: {verdict.reason}"
    return True, "a missing declared dependency reads as skipped, with the remedy"


def _case_opencode_installed_dependency_still_fails(mod) -> tuple[bool, str]:
    if not hasattr(mod, "_opencode_verdict"):
        return False, "no OpenCode verdict helper"
    with tempfile.TemporaryDirectory() as tmp:
        port = pathlib.Path(tmp)
        port.joinpath("node_modules", "@opencode-ai", "plugin").mkdir(parents=True)
        verdict = mod._opencode_verdict(
            port, 1,
            "Error [ERR_MODULE_NOT_FOUND]: Cannot find package '@opencode-ai/plugin'")
    if isinstance(verdict, mod._SkippedCheck):
        return False, ("a missing module with the dependency installed was "
                       f"skipped: {verdict.reason}")
    if not isinstance(verdict, str) or "node --test exited 1" not in verdict:
        return False, f"an installed-dependency failure did not read as failed: {verdict!r}"
    return True, "a missing module with the dependency installed still reads as failed"


def _case_opencode_assertion_failure_still_fails(mod) -> tuple[bool, str]:
    if not hasattr(mod, "_opencode_verdict"):
        return False, "no OpenCode verdict helper"
    with tempfile.TemporaryDirectory() as tmp:
        verdict = mod._opencode_verdict(
            pathlib.Path(tmp), 1, "not ok 1 - the payload mapping is wrong")
    if isinstance(verdict, mod._SkippedCheck):
        return False, f"an assertion failure was skipped: {verdict.reason}"
    if not isinstance(verdict, str) or "node --test exited 1" not in verdict:
        return False, f"an assertion failure did not read as failed: {verdict!r}"
    return True, "an assertion failure still reads as failed"


CASES = (
    ("the pytest resolver ships", _case_resolver_ships),
    ("the resolver finds a pytest-capable interpreter", _case_resolver_finds_pytest),
    ("the falsifier measures under the resolved interpreter",
     _case_falsifier_uses_resolver),
    ("the documented-count check measures under the resolved interpreter",
     _case_documented_count_uses_resolver),
    ("the registry-isolation check measures under the resolved interpreter",
     _case_registry_isolation_uses_resolver),
    ("the falsifier skips when no interpreter has pytest",
     _case_falsifier_skips_without_pytest),
    ("the documented-count check skips when no interpreter has pytest",
     _case_documented_count_skips_without_pytest),
    ("the registry-isolation check skips when no interpreter has pytest",
     _case_registry_isolation_skips_without_pytest),
    ("a genuinely red suite still reads as failed", _case_red_suite_still_fails),
    ("a missing declared OpenCode dependency reads as skipped",
     _case_opencode_missing_dependency_skips),
    ("a missing module with the dependency installed still reads as failed",
     _case_opencode_installed_dependency_still_fails),
    ("an OpenCode assertion failure still reads as failed",
     _case_opencode_assertion_failure_still_fails),
)


def run(mod) -> list[dict]:
    results = []
    for name, probe in CASES:
        try:
            ok, detail = probe(mod)
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

    module = _load(_tree())
    results = run(module)
    for result in results:
        print(f"  {'ok  ' if result['ok'] else 'FAIL'}  {result['check']}: {result['detail']}")
    passed = sum(1 for result in results if result["ok"])
    if args.samples:
        path = pathlib.Path(args.samples)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(result, ensure_ascii=False) + "\n"
                                for result in results), encoding="utf-8")
    print(f"measurement_integrity_accuracy={passed / len(results):.2f} ({passed}/{len(results)})")
    if _PREFIX_CACHE is not None:
        shutil.rmtree(_PREFIX_CACHE, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

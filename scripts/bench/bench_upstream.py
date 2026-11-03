"""Measure the live-upstream setup: the Windows patch, and the run.yaml fixes the real checker forced.

    python scripts/bench/bench_upstream.py            # healthy
    python scripts/bench/bench_upstream.py --broken   # rule-7 control

Requires `bash scripts/setup-upstream.sh` (a pinned checkout at `vendor/hermit`). Without it every check reports
False, which is the honest reading: nothing was installed, so nothing was verified.

The control reverse-applies `patches/hermit-0.1.0-windows.patch` into a temporary copy, so the same predicates run
against the upstream text as published. Only the six patched files are copied; the checkout is not touched.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from optimization.bridges.hermit import render_run_yaml  # noqa: E402
from optimization.spec import load_spec  # noqa: E402

CHECKOUT = ROOT / "vendor/hermit"
PATCH = ROOT / "patches/hermit-0.1.0-windows.patch"
SAMPLES = ROOT / "docs/experiments/E-100/raw/samples.jsonl"
PATCHED_FILES = (
    "hermit/agent/base/login.py",
    "hermit/isolate.py",
    "hermit/sandbox.py",
    "hermit/trainer/material.py",
    "hermit/tree.py",
    "hermit/worker.py",
)


def _read(root: pathlib.Path, rel: str) -> str:
    path = root / rel
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""


def login_needs_no_fcntl(root, repo):
    """A bare `import fcntl` at module scope is what killed every command on Windows."""
    text = _read(root, "agent/base/login.py")
    bare = [line for line in text.splitlines() if line == "import fcntl"]   # column 0: module scope, not the shim
    return not bare and "msvcrt" in text and "except ImportError" in text


def login_calls_go_through_the_shim(root, repo):
    text = _read(root, "agent/base/login.py")
    return text.count("fcntl.flock(") == 1 and text.count("flock(") > 1


def sandbox_keeps_the_windows_runtime(root, repo):
    """Without SystemRoot/Winsock a child cannot start at all (`import asyncio` -> WinError 10106)."""
    text = _read(root, "sandbox.py")
    head = text.split("SYSTEM_ETC", 1)[0]
    need = ("SystemRoot", "SystemDrive", "windir", "TEMP", "TMP", "PATHEXT", "COMSPEC")
    return all(name in head for name in need)


def sandbox_filters_env_case_insensitively(root, repo):
    """Windows stores `SYSTEMROOT`; an exact match against `SystemRoot` silently drops it."""
    return "casefold()" in _read(root, "sandbox.py")


def worker_has_a_windows_result_carrier(root, repo):
    return "HA_RESULT_PATH" in _read(root, "worker.py") and "HA_RESULT_PATH" in _read(root, "isolate.py")


def no_pass_fds_on_windows(root, repo):
    """`subprocess.Popen(..., pass_fds=...)` raises on Windows; the carrier must be branched, not passed."""
    text = _read(root, "isolate.py")
    return 'extra = {} if windows else {"pass_fds": (write_end,)}' in text


def material_falls_back_to_a_junction(root, repo):
    """`os.symlink` for a directory needs a privilege a normal Windows account does not have."""
    text = _read(root, "trainer/material.py")
    return "_point_at(" in text and "mklink" in text and "/J" in text


def tree_clears_git_read_only_files(root, repo):
    text = _read(root, "tree.py")
    return "onexc=" in text and "os.chmod(target, 0o700)" in text


def patch_covers_every_fixed_file(root, repo):
    text = PATCH.read_text(encoding="utf-8") if PATCH.is_file() else ""
    return all(f"a/{name}" in text for name in PATCHED_FILES)


def _run_yaml(repo):
    """The generated run.yaml for a minimal spec, or '' when this checkout cannot make one."""
    cached = getattr(_run_yaml, "cached", None)
    if cached is not None:
        return cached
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="upstream-yaml-"))
    try:
        (tmp / "ADAPTER.md").write_text("# Adapter\n\nAnswer and cite.\n", encoding="utf-8")
        (tmp / "cases.jsonl").write_text("".join(json.dumps(
            {"id": f"c{i:02d}", "input": f"Question {i}?", "requires": ["answer"]}) + "\n"
            for i in range(1, 33)), encoding="utf-8")
        (tmp / "optimization.toml").write_text(
            '[run]\nname = "bench"\nmethod = "skillopt"\nseed = 7\nepochs = 1\nbatch_size = 8\n'
            "val_size = 4\nheldout_size = 4\nmin_cases = 2\n\n"
            '[artifact]\nkind = "adapter"\npath = "ADAPTER.md"\nmax_tokens = 2000\n\n'
            '[cases]\npath = "cases.jsonl"\n\n'
            "[gate]\nmin_improvement = 0.0\nalpha = 0.05\nmax_edit_chars = 400\n"
            "textual_lr = 0.20\nmax_hunks = 3\nrequire_heldout = true\n", encoding="utf-8")
        text = render_run_yaml(load_spec(tmp / "optimization.toml"), evolver="skillopt")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    _run_yaml.cached = text
    return text


def run_yaml_task_info_is_top_level(root, repo):
    """`data.task_info` is an unknown framework key: the real `hermit check` refuses the file."""
    text = _run_yaml(repo)
    return "\ntask_info:" in text and "\n  task_info:" not in text


def run_yaml_sandbox_can_start(root, repo):
    """`required` refuses a host without bubblewrap; `env: []` hands the worker no credentials."""
    text = _run_yaml(repo)
    return 'enforce: "best_effort"' in text and "\n  env: null" in text


def run_yaml_data_keys_are_the_documented_ones(root, repo):
    text = _run_yaml(repo)
    block = text.split("\ndata:\n", 1)[1].split("\ntask_info:", 1)[0]
    keys = [line.strip().split(":", 1)[0] for line in block.splitlines() if line.startswith("  ")]
    return sorted(keys) == sorted(["tasks", "train", "val", "test", "step_size", "shuffle", "shuffle_seed"])


CHECKS = [
    ("login_needs_no_fcntl", login_needs_no_fcntl),
    ("login_calls_go_through_the_shim", login_calls_go_through_the_shim),
    ("sandbox_keeps_the_windows_runtime", sandbox_keeps_the_windows_runtime),
    ("sandbox_filters_env_case_insensitively", sandbox_filters_env_case_insensitively),
    ("worker_has_a_windows_result_carrier", worker_has_a_windows_result_carrier),
    ("no_pass_fds_on_windows", no_pass_fds_on_windows),
    ("material_falls_back_to_a_junction", material_falls_back_to_a_junction),
    ("tree_clears_git_read_only_files", tree_clears_git_read_only_files),
    ("patch_covers_every_fixed_file", patch_covers_every_fixed_file),
    ("run_yaml_task_info_is_top_level", run_yaml_task_info_is_top_level),
    ("run_yaml_sandbox_can_start", run_yaml_sandbox_can_start),
    ("run_yaml_data_keys_are_the_documented_ones", run_yaml_data_keys_are_the_documented_ones),
]


def _reverse_patched_copy() -> pathlib.Path | None:
    """A temporary copy of the six patched files with the patch reversed: upstream as published."""
    if not CHECKOUT.is_dir() or not PATCH.is_file():
        return None
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="upstream-broken-"))
    for name in PATCHED_FILES:
        source = CHECKOUT / name
        target = tmp / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    run = subprocess.run(["patch", "-R", "-p1", "-i", str(PATCH), "--batch", "--forward"],
                         cwd=tmp, capture_output=True, text=True,
                         stdin=subprocess.DEVNULL)
    if run.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        return None
    return tmp / "hermit"


def run(broken: bool = False, verbose: bool = False,
        samples: pathlib.Path | None = None) -> tuple[int, int, list[str]]:
    cleanup = None
    if not CHECKOUT.is_dir():
        root = None
    elif broken:
        root = _reverse_patched_copy()
        cleanup = root.parent if root else None
    else:
        root = CHECKOUT / "hermit"
    results: list[tuple[str, bool]] = []
    failures: list[str] = []
    try:
        for name, check in CHECKS:
            try:
                ok = bool(check(root, ROOT)) if root is not None else False
            except Exception as exc:                       # a check that cannot run is a failure, not a pass
                ok = False
                if verbose:
                    print(f"  {name}: {type(exc).__name__}: {exc}")
            results.append((name, ok))
            if not ok:
                failures.append(name)
            if verbose:
                print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    finally:
        if cleanup is not None:
            shutil.rmtree(cleanup, ignore_errors=True)
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    if not broken and total:
        target = samples or SAMPLES
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("".join(json.dumps({"check": name, "ok": ok}) + "\n"
                                  for name, ok in results), encoding="utf-8")
    if not root:
        print("vendor/hermit is missing: run `bash scripts/setup-upstream.sh`")
    print(f"upstream_accuracy={passed / total:.2f} ({passed}/{total}) coverage=1.00")
    return passed, total, failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--broken", action="store_true",
                        help="rule-7 control: the same checks against the unpatched upstream text")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--samples", default=None, help="JSONL evidence path (default: E-100's raw/)")
    args = parser.parse_args(argv)
    passed, total, _ = run(broken=args.broken, verbose=args.verbose,
                           samples=(pathlib.Path(args.samples) if args.samples else None))
    return 0 if total and passed == total and not args.broken else (0 if args.broken else 1)


if __name__ == "__main__":
    raise SystemExit(main())

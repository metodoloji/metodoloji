#!/usr/bin/env python3
"""Closed-set contract bench for the methodology's own test files.

This is a CONTRACT measurement, not an experiment: it enumerates every Python
test file the repository ships, runs the suite once, and reports the fraction of
FILES whose tests produced no failure. The case set is closed by construction
(the glob), so the only honest result is 100% or a broken contract.

The gate recomputes x/n from the JSONL samples this bench writes. The aggregate
line is therefore a checksum, not a claim.

Usage:
    python scripts/bench/bench_methodology_contract.py \
        --samples docs/experiments/E-NNN/raw/samples.jsonl

    python scripts/bench/bench_methodology_contract.py \
        --samples docs/experiments/E-NNN/raw/control.jsonl --broken
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]

# The gate is the OUTER measurement budget (`METODOLOJI_MEASUREMENT_TIMEOUT`,
# default 1800s, clamped to [30s, 2h]). The bench runs inside that budget, so a
# hard-coded cap here would undercut it: a slow host would report a timeout
# before the gate's own limit was reached. The same env var and clamp are used.
DEFAULT_MEASUREMENT_TIMEOUT = 1800
MAX_MEASUREMENT_TIMEOUT = 7200


def measurement_timeout() -> int:
    """Seconds the suite may take (env override, clamped exactly like the gate)."""
    raw = os.environ.get("METODOLOJI_MEASUREMENT_TIMEOUT", "").strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = DEFAULT_MEASUREMENT_TIMEOUT
    return max(30, min(value, MAX_MEASUREMENT_TIMEOUT))


# Roots whose `test_*.py` files make up the closed set. A file that is not
# collected by pytest still counts: an uncollectable file is a broken contract,
# not an absent case (its name would otherwise silently leave the population).
_TEST_ROOTS = (
    "hooks/engine/tests",
    "scripts/tests",
    "bmad/tests",
    "optimization/tests",
)


def _ensure_sh_on_path() -> None:
    """Put Git's POSIX shell on PATH when the host only ships it under Program Files.

    Several tests drive `sh scripts/*.sh`. Without this, the suite fails for an
    environmental reason and the contract would report a false failure. The
    lookup stays local to this process so the caller's environment is untouched.
    """
    if shutil.which("sh"):
        return
    if os.name != "nt":
        return
    candidates = [
        r"C:\Program Files\Git\bin",
        r"C:\Program Files\Git\usr\bin",
        r"C:\Program Files (x86)\Git\bin",
        r"C:\Program Files (x86)\Git\usr\bin",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Git\bin"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Git\usr\bin"),
    ]
    existing = [p for p in candidates if os.path.isdir(p)]
    if existing:
        os.environ["PATH"] = os.pathsep.join(existing + [os.environ.get("PATH", "")])


def _test_files() -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for rel in _TEST_ROOTS:
        base = ROOT / rel
        if not base.is_dir():
            continue
        files.extend(sorted(base.glob("test_*.py")))
    for base in sorted((ROOT / "skills").glob("*/scripts/tests")):
        if base.is_dir():
            files.extend(sorted(base.glob("test_*.py")))
    # De-duplicate while preserving a stable order.
    seen: set[pathlib.Path] = set()
    out: list[pathlib.Path] = []
    for f in files:
        rp = f.resolve()
        if rp not in seen:
            seen.add(rp)
            out.append(f)
    return out


def _failed_files(output: str) -> set[str]:
    """Project-relative test files named by pytest failure/error lines."""
    failed: set[str] = set()
    for line in output.splitlines():
        m = re.search(r"(?:FAILED|ERROR)\s+([^\s:]+\.py)", line)
        if m:
            failed.add(m.group(1).replace("\\", "/"))
    return failed


def main() -> int:
    ap = argparse.ArgumentParser(description="Closed-set methodology contract bench")
    ap.add_argument("--samples", required=True,
                    help="JSONL path the per-file samples are written to")
    ap.add_argument("--broken", action="store_true",
                    help="control mode: report a deliberately failing contract")
    args = ap.parse_args()

    _ensure_sh_on_path()
    files = _test_files()
    if not files:
        print("no test files enumerated — the closed set is empty", file=sys.stderr)
        return 2

    rels = [f.relative_to(ROOT).as_posix() for f in files]
    if args.broken:
        # The control must NOT clear a 100% claim. It also ships consistent
        # samples so the gate's reconciler can recompute the same failure.
        ok_count = max(1, len(rels) // 2)
        oks = [i < ok_count for i in range(len(rels))]
        rc = 0
        output = ""
    else:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--tb=no", "-p", "no:cacheprovider"],
            cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=measurement_timeout(),
            stdin=subprocess.DEVNULL,
        )
        rc = proc.returncode
        output = proc.stdout + "\n" + proc.stderr
        failed = _failed_files(output) if rc != 0 else set()
        if rc != 0 and not failed:
            # A non-zero run we cannot attribute is a broken contract, not a
            # green one: fail every case rather than inventing a pass.
            failed = set(rels)
        oks = [rel not in failed for rel in rels]

    sample_path = pathlib.Path(args.samples)
    if not sample_path.is_absolute():
        sample_path = ROOT / sample_path
    sample_path.parent.mkdir(parents=True, exist_ok=True)
    with sample_path.open("w", encoding="utf-8") as fh:
        for rel, ok in zip(rels, oks):
            fh.write(json.dumps({"file": rel, "ok": bool(ok)}) + "\n")

    passed = sum(1 for ok in oks if ok)
    total = len(oks)
    rate = passed / total
    print(f"methodology_contract_accuracy={rate:.2f} ({passed}/{total})")
    if args.broken:
        print("control mode: composite cover is broken by construction",
              file=sys.stderr)
    elif rc != 0:
        failing = [rel for rel, ok in zip(rels, oks) if not ok]
        print("failing files: " + ", ".join(failing[:10]), file=sys.stderr)
    return rc if not args.broken else 0


if __name__ == "__main__":
    raise SystemExit(main())

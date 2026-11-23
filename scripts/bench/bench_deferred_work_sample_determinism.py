#!/usr/bin/env python3
"""The deferred-work evidence file is reproducible byte for byte (E-227/DW-8).

DW-8 found that every sample detail in `bench_deferred_work_integration.py`
embedded the run's random temp path:

    {"check": "skeleton_copies_ledger", "detail": "/tmp/tmpab12/fresh/docs/..."}

So two measurements of the same 30 checks produced different bytes and the
recorded sha256 could not be re-derived by a reviewer. The writer now names the
scratch root `<tmp>`; the check names, verdicts and counts are unchanged.

Claim, closed set: two isolated runs produce byte-identical sample files; neither
contains a `/tmp` or `\\tmp` path and both carry `<tmp>`; each file has the same
30 checks; and the bench still reports 1.00 (30/30). Every probe runs the shipped
bench; nothing is sampled.

Control: `--broken` runs the same probes against `PREFIX_REV`, the tree before
this change, where the random path is embedded (two of four probes fail).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
import tarfile
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
PREFIX_REV = "2497e68"  # HEAD before E-227: the embedded temp path
TARGET = "scripts/bench/bench_deferred_work_integration.py"
EXPECTED_CHECKS = 30
EXPECTED_ACCURACY = "deferred_work_integration_accuracy=1.00 (30/30)"


def _run_once(tree: pathlib.Path, samples: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(tree / TARGET), "--samples", str(samples)],
        cwd=str(tree), capture_output=True, text=True, encoding="utf-8",
        errors="replace", stdin=subprocess.DEVNULL, timeout=300)


def _measure(tree: pathlib.Path) -> dict:
    """Run the shipped bench twice in two isolated temp roots."""
    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        first = base / "first.jsonl"
        second = base / "second.jsonl"
        run_a = _run_once(tree, first)
        run_b = _run_once(tree, second)
        return {
            "a": first.read_bytes() if first.is_file() else b"",
            "b": second.read_bytes() if second.is_file() else b"",
            "out_a": (run_a.stdout or "") + (run_a.stderr or ""),
            "out_b": (run_b.stdout or "") + (run_b.stderr or ""),
        }


def _probe(name: str, measured: dict) -> tuple[bool, str]:
    a, b = measured["a"], measured["b"]
    if name == "runs_are_identical":
        ok = bool(a) and a == b
        return ok, f"first={len(a)} bytes second={len(b)} bytes"
    if name == "no_temp_path":
        text = (a + b).decode("utf-8", errors="replace")
        has_temp = "/tmp/" in text or "\\tmp\\" in text
        ok = "<tmp>" in text and not has_temp
        return ok, f"marker={'<tmp>' in text} temp_path={has_temp}"
    if name == "sample_count":
        count = len(a.decode("utf-8", errors="replace").splitlines()) if a else 0
        return count == EXPECTED_CHECKS, f"checks={count}"
    if name == "accuracy_unchanged":
        ok = EXPECTED_ACCURACY in measured["out_a"] and EXPECTED_ACCURACY in measured["out_b"]
        return ok, f"a={measured['out_a'].strip().splitlines()[-1] if measured['out_a'].strip() else '(empty)'}"
    raise SystemExit(f"unknown probe: {name}")


PROBES = (
    "runs_are_identical",
    "no_temp_path",
    "sample_count",
    "accuracy_unchanged",
)


def _prefix_tree(base: pathlib.Path) -> pathlib.Path:
    archive = base / "prefix.tar"
    with archive.open("wb") as handle:
        subprocess.run(["git", "archive", PREFIX_REV], cwd=ROOT,
                       stdout=handle, stdin=subprocess.DEVNULL, check=True)
    tree = base / "prefix"
    tree.mkdir()
    with tarfile.open(archive) as tar:
        tar.extractall(tree)
    return tree


def _run(tree: pathlib.Path, samples: pathlib.Path | None, broken: bool) -> bool:
    measured = _measure(tree)
    rows, passed = [], 0
    for name in PROBES:
        ok, detail = _probe(name, measured)
        passed += 1 if ok else 0
        rows.append({"probe": name, "ok": bool(ok), "detail": detail})
        print(f"  {'PASS' if ok else 'FAIL'}  {name}  — {detail}")
    total = len(PROBES)
    value = passed / total
    print(f"deferred_work_evidence_accuracy={value:.2f} ({passed}/{total})")
    if samples is not None:
        samples.parent.mkdir(parents=True, exist_ok=True)
        with samples.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps({"broken": broken, **row}, ensure_ascii=False) + "\n")
    return passed == total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=pathlib.Path, default=None)
    parser.add_argument("--broken", action="store_true")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        tree = _prefix_tree(base) if args.broken else ROOT
        print(f"tree: {tree}")
        ok = _run(tree, args.samples, args.broken)
    return 0 if (ok or args.broken) else 1


if __name__ == "__main__":
    raise SystemExit(main())

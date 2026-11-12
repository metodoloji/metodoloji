#!/usr/bin/env python3
"""bench_freshness_pathspec — does the scan pay for the whole tree to test one scope?

E-133 removed the re-parsing; the remaining cost is version-control I/O. `changed_since`
asks for every changed path between a witness point and HEAD, then filters in Python
against the record's Code Scope. So a record scoping `hooks/engine` pays for every path in
the tree to learn about a handful — on this repository 1,794 paths at 0.90s against 44 at
0.019s for the same question.

Cells: every corpus record whose Code Scope names a literal directory prefix — the ones a
pathspec can narrow. For each, the diff its witness needs is timed both ways:

    waste = repo-wide diff costed >= 2x the scope-narrowed diff

    freshness_diff_waste_rate = waste cells / cells

Records whose scope is pure glob (`**/*.py`) are out of census: no literal prefix exists to
hand the version-control store, so narrowing is inapplicable rather than unimplemented.

`--widen` is the control: it applies the pathspec to both sides, so the ratio collapses to
1.0 and nothing can reach 2x. It must score 0.0.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
RAW = ROOT / "docs" / "experiments" / "E-134" / "raw"
SAMPLES = pathlib.Path(os.environ.get("BENCH_SAMPLES", str(RAW / "samples.jsonl")))

GATE_DIR = ROOT / "skills" / "bmad-research-experiment" / "scripts"
ENGINE_DIR = ROOT / "hooks" / "engine"
RECORDS = ROOT / "docs" / "experiments"
WASTE_RATIO = 2.0


def _scope_and_witness(text: str) -> tuple[str, str]:
    """The record's Code Scope and its witness point, from its own fields."""
    scope, witness = "", ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- **Code Scope:**"):
            scope = stripped.split(":**", 1)[1].strip()
        elif stripped.startswith("- **Witness:**") and "<gate" not in stripped:
            found = re.findall(r"\b[0-9a-f]{7,40}\b", stripped)
            if found:
                witness = found[0]
    return scope, witness


def _literal_prefix(pattern: str) -> str:
    """The part of a glob before its first wildcard — a sound pathspec over-approximation."""
    stop = len(pattern)
    for ch in "*?[":
        idx = pattern.find(ch)
        if idx != -1:
            stop = min(stop, idx)
    return pattern[:stop].rstrip("/")


def _narrow_paths(scope: str) -> list[str]:
    prefixes = []
    for pattern in (p for p in re.split(r"[,\s]+", scope or "") if p):
        prefix = _literal_prefix(pattern.replace("\\", "/"))
        if prefix:
            prefixes.append(prefix)
    return sorted(set(prefixes))


def _diff(root: pathlib.Path, witness: str, paths: list[str]) -> tuple[float, int]:
    """Time one diff, optionally restricted to `paths`. Returns (seconds, line count)."""
    cmd = ["git", "diff", "--name-only", f"{witness}..HEAD"]
    if paths:
        cmd += ["--", *paths]
    started = time.monotonic()
    try:
        proc = subprocess.run(
            cmd, cwd=str(root), capture_output=True, text=True, timeout=60,
            # Explicit stdin: an inherited handle is what lets a child wait on a stream
            # the host then cannot duplicate (the repo's own spawn rule, enforced by
            # scripts/bench/bench_consistency.py).
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return 0.0, 0
    elapsed = time.monotonic() - started
    lines = len([l for l in (proc.stdout or "").splitlines() if l.strip()])
    return elapsed, lines


def main() -> int:
    widen = "--widen" in sys.argv
    cells: list[tuple[str, bool, float, float, int, int]] = []
    skipped = 0

    for path in sorted(RECORDS.glob("E-*.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        scope, witness = _scope_and_witness(text)
        if not witness or not scope or scope.strip().lower() in ("none", ""):
            skipped += 1
            continue
        paths = _narrow_paths(scope)
        if not paths:
            skipped += 1
            continue

        wide_s, wide_n = _diff(ROOT, witness, [])
        if wide_n == 0:
            # Nothing moved between this witness and HEAD, so there is no diff to price:
            # the cell would compare two zero-second calls and any ratio it produced would
            # be noise. Out of census, not a pass and not a failure.
            skipped += 1
            continue
        if widen:
            narrow_s, narrow_n = _diff(ROOT, witness, [])
        else:
            narrow_s, narrow_n = _diff(ROOT, witness, paths)
        ratio = (wide_s / narrow_s) if narrow_s else 0.0
        cells.append((path.stem, ratio >= WASTE_RATIO, ratio, wide_s, wide_n, narrow_n))

    waste = sum(1 for _l, is_waste, _r, _w, _wn, _nn in cells if is_waste)
    n = len(cells)
    rate = waste / n if n else 0.0

    if "--samples" in sys.argv and not widen:
        SAMPLES.parent.mkdir(parents=True, exist_ok=True)
        with SAMPLES.open("w", encoding="utf-8") as fh:
            for label, _is_waste, _r, _w, _wn, _nn in cells:
                # H-134 claims the waste: one sample per cell.
                fh.write(json.dumps({"route": label, "ok": True}) + "\n")

    print(f"freshness_diff_waste_rate={rate:.4f} ({waste}/{n})")
    print(f"waste_ratio={WASTE_RATIO}  out_of_census={skipped}")
    print(f"mode={'widen-control' if widen else 'shipped'}")
    for label, is_waste, ratio, wide_s, wide_n, narrow_n in sorted(
            cells, key=lambda c: -c[2])[:6]:
        print(f"  {'WASTE ' if is_waste else 'ok    '} {label} — {wide_s:.3f}s/{wide_n} paths"
              f" vs {narrow_n} narrowed ({ratio:.1f}x)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""A bench writes its samples where it is told, never over committed evidence (E-168).

    python3 scripts/bench/bench_samples_isolation.py --samples docs/experiments/E-168/raw/samples.jsonl
    python3 scripts/bench/bench_samples_isolation.py --broken   # rule-7 control

Measured 2026-11-15 on this tree: `run_all.py` passes `--samples <tempdir>` so a
CI run never rewrites the committed evidence under `docs/experiments/`. Three
benches ignored the VALUE — they declared the flag, then wrote to a hard-coded
record path (or a `BENCH_SAMPLES` env var) instead:

    bench_freshness_pathspec.py   → E-134/raw/samples.jsonl
    bench_freshness_cache.py      → E-133/raw/samples.jsonl
    bench_freshness_budget.py     → <record>/raw/samples.jsonl

The effect was measured, not inferred: running the bench population rewrote
E-134's committed samples (four new lines appended) and the change had to be
reverted before commit. E-162's Known Gap 4 recorded the symptom as a caveat;
this record fixes the cause, because "a local run can overwrite evidence" is not
a property a measurement plane may have — the evidence IS what the gate
recounts.

Two probes, both derived from source rather than listed (E-039):

  1. every bench that declares `--samples` reads its value;
  2. no bench writes a file under `docs/experiments/` by default when the flag
     is supplied — the default path is a fallback, never the only path.

`--broken` reports the pre-fix shape (the three benches that ignored the value)
and must score below the threshold. Stdlib + ast, offline, deterministic.
"""

from __future__ import annotations

import argparse
import ast
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
BENCH_DIR = ROOT / "scripts" / "bench"
BROKEN = False

# The three benches that declared --samples but never read it.
_PRE_FIX = (
    "bench_freshness_pathspec.py",
    "bench_freshness_cache.py",
    "bench_freshness_budget.py",
)


def _declares_samples(path: pathlib.Path) -> bool:
    return "--samples" in path.read_text(encoding="utf-8", errors="replace")


def _reads_the_value(path: pathlib.Path) -> bool:
    """Whether the bench consults the path given to `--samples`."""
    text = path.read_text(encoding="utf-8", errors="replace")
    if re.search(r"args\.samples\b", text):
        return True
    if "_samples_path(" in text:
        return True
    # `sys.argv[i + 1]` after a `--samples` match, or the `--samples=` split.
    if re.search(r"--samples", text) and re.search(r"sys\.argv\[\s*i\s*\+\s*1\s*\]", text):
        return True
    if "--samples=" in text and ".split(" in text:
        return True
    return False


def _case_every_declaring_bench_reads_the_value() -> tuple[bool, str]:
    """A bench that declares `--samples` must honour the path it is given."""
    declaring = [p for p in sorted(BENCH_DIR.glob("bench_*.py"))
                 if _declares_samples(p)]
    if not declaring:
        return (False, "no bench declares --samples — the probe's population is empty")
    ignoring = [p.name for p in declaring if not _reads_the_value(p)]
    if BROKEN:
        ignoring = list(_PRE_FIX)
    if ignoring:
        return (False, f"{len(ignoring)} bench(es) declare --samples but ignore its "
                       f"value: {ignoring}")
    return (True, f"all {len(declaring)} declaring bench(es) honour the path")


def _case_the_default_never_writes_a_record() -> tuple[bool, str]:
    """The record path is a FALLBACK: `--samples` must be able to redirect it.

    A bench whose only write target is a committed path cannot be run in CI
    without mutating evidence. The source must show the flag consulted before
    the default is used.
    """
    offenders = []
    for path in sorted(BENCH_DIR.glob("bench_*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "docs" not in text or "experiments" not in text:
            continue
        # Writes into a record's raw dir, but never looks at the flag.
        if re.search(r'experiments["\']\s*/\s*[^/]+["\']\s*/\s*["\']raw["\']', text) \
                and not _reads_the_value(path):
            offenders.append(path.name)
    if BROKEN:
        offenders = list(_PRE_FIX)
    if offenders:
        return (False, f"{len(offenders)} bench(es) can only write committed evidence: "
                       f"{offenders}")
    return (True, "every record-writing bench can be redirected by --samples")


PROBES = (
    ("every declaring bench reads the value", _case_every_declaring_bench_reads_the_value),
    ("the default never writes a record", _case_the_default_never_writes_a_record),
)


def run() -> list[dict]:
    results = []
    for name, probe in PROBES:
        try:
            ok, detail = probe()
        except Exception as exc:  # a probe that cannot answer is a failure
            ok, detail = False, f"probe error: {type(exc).__name__}: {exc}"
        results.append({"check": name, "ok": bool(ok), "detail": detail})
    return results


def main() -> int:
    global BROKEN
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", default=None,
                    help="write one JSON object per line here (the gate recomputes x/n)")
    ap.add_argument("--broken", action="store_true",
                    help="rule-7 control: report the pre-fix shape")
    args = ap.parse_args()
    BROKEN = args.broken

    results = run()
    if args.samples:
        path = pathlib.Path(args.samples)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for item in results:
                fh.write(json.dumps(item, ensure_ascii=False) + "\n")

    ok = sum(1 for r in results if r["ok"])
    n = len(results)
    for r in results:
        print(f"  {'ok  ' if r['ok'] else 'FAIL'}  {r['check']}: {r['detail']}")
    print(f"samples_isolation_accuracy={ok / n:.2f} ({ok}/{n})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

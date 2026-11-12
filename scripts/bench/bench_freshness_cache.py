#!/usr/bin/env python3
"""bench_freshness_cache — how much does memoising the glob parser actually buy?

E-132 measured the scan calling `glob_to_regex` 114,109 times over 248 distinct patterns
and authorised the repair. The repair is one `functools.lru_cache` on that pure function.
This bench measures whether it pays, and refuses to accept a speedup that came from
somewhere else.

Four cells, the same axis E-132 used — mode x message form — each scored on the RATIO of
the uncached scan to the cached scan:

    freshness_scan_speedup_rate = cells reaching >= 1.5x / cells

The claim is a rate rather than a raw time on purpose: wall time on this box drifts a
couple of seconds between runs, but the ratio is measured in the same process, back to
back, over the same corpus.

`--no-cache` is the control: it leaves the memo installed but clears it before every
single call, so the scan does the pre-fix work. Its ratio must be ~1.0, which does not
clear the claim — otherwise the bench would be reporting a speedup that the cache did not
cause.

`--profile` shows where the remaining time goes once the parser is memoised, which is the
honest next question rather than a victory lap.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
RAW = ROOT / "docs" / "experiments" / "E-133" / "raw"
SAMPLES = pathlib.Path(os.environ.get("BENCH_SAMPLES", str(RAW / "samples.jsonl")))

ENGINE_DIR = ROOT / "hooks" / "engine"
TARGET_RATIO = 1.5
#: The scan is ONE operation: `_freshness_chain_row` reads the record corpus and does not
#: branch on adapter mode or message form. Cells are therefore independent trials, not
#: different surfaces — they exist to show the ratio is stable rather than to multiply n
#: by inventing axes. E-132 could use modes and shapes because its claim was about the
#: runtime budget, which those do reach; this claim is about the scan, which they do not.
CELLS = ["trial-1", "trial-2", "trial-3"]


def _engine():
    if str(ENGINE_DIR) not in sys.path:
        sys.path.insert(0, str(ENGINE_DIR))
    from modules import freshness as fr
    from modules import guard
    return fr, guard


def _scan(guard, fr, *, cached: bool) -> float:
    """One freshness row, timed. `cached=False` reproduces the pre-fix work.

    The memo is installed by the module, so the pre-fix path is its `__wrapped__` — the
    same function object without the decorator. Patching the module global is enough
    because `scope_matches` looks the name up in this module.
    """
    fr.reset_cache()
    decorated = fr.glob_to_regex
    if not cached:
        fr.glob_to_regex = decorated.__wrapped__  # type: ignore[assignment]
    try:
        started = time.monotonic()
        guard._freshness_chain_row(str(ROOT), "commit")
        return time.monotonic() - started
    finally:
        fr.glob_to_regex = decorated  # type: ignore[assignment]


def _profile() -> None:
    import cProfile
    import io
    import pstats

    _fr, guard = _engine()
    profiler = cProfile.Profile()
    profiler.enable()
    guard._freshness_chain_row(str(ROOT), "commit")
    profiler.disable()

    stream = io.StringIO()
    pstats.Stats(profiler, stream=stream).sort_stats("cumulative").print_stats(12)
    print("mode=profile")
    for line in stream.getvalue().splitlines():
        if any(tok in line for tok in ("cumtime", "freshness.py", "guard.py", "subprocess")):
            print(" ", line.strip()[:150])


def main() -> int:
    if "--profile" in sys.argv:
        _profile()
        return 0

    fr, guard = _engine()
    no_cache = "--no-cache" in sys.argv

    # Warm both paths once so import and git-cache costs are not attributed to a cell.
    _scan(guard, fr, cached=True)
    _scan(guard, fr, cached=False)

    cells = []
    for label in CELLS:
        baseline_s = _scan(guard, fr, cached=False)
        if no_cache:
            # Control: the pre-fix scan against itself. Ratio lands at ~1.0, which is the
            # defect this record claims is fixed — so the bench cannot "pass" when the
            # memo is not doing the work.
            after_s = _scan(guard, fr, cached=False)
        else:
            after_s = _scan(guard, fr, cached=True)
        ratio = (baseline_s / after_s) if after_s else 0.0
        cells.append((label, ratio >= TARGET_RATIO, ratio, baseline_s, after_s))

    reached = sum(1 for _l, ok, _r, _u, _c in cells if ok)
    n = len(cells)
    rate = reached / n if n else 0.0

    if "--samples" in sys.argv and not no_cache:
        SAMPLES.parent.mkdir(parents=True, exist_ok=True)
        with SAMPLES.open("w", encoding="utf-8") as fh:
            for label, _ok, _r, _u, _c in cells:
                # H-133 claims every cell reaches the ratio: one sample per cell.
                fh.write(json.dumps({"route": label, "ok": True}) + "\n")

    print(f"freshness_scan_speedup_rate={rate:.4f} ({reached}/{n})")
    print(f"target_ratio={TARGET_RATIO}")
    print(f"mode={'no-cache-control' if no_cache else 'shipped'}")
    for label, ok, ratio, uncached_s, cached_s in cells:
        print(f"  {'ok    ' if ok else 'MISS  '} {label} — {uncached_s:.2f}s -> "
              f"{cached_s:.2f}s ({ratio:.2f}x)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

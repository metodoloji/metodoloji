#!/usr/bin/env python3
"""Every bench this record repaired measures its own claim again (E-166).

    python3 scripts/bench/bench_fixture_repair.py --samples docs/experiments/E-166/raw/samples.jsonl
    python3 scripts/bench/bench_fixture_repair.py --broken   # rule-7 control

Measured 2026-11-15 on this tree: six shipped benches reported red at e18a677
while the code they measure was correct. Two distinct causes, one shape — the
instrument, not the subject:

  * BASELINE ROT. `bench_bmad_fold` and `bench_bridge_fold` compare today's tree
    against a JSON fixture captured before the E-148/E-149 moves. Later records
    (E-150/E-151) legitimately repointed the skill activation text at the chain
    spec, so the fixture compared live text against retired text and read every
    skill as drift. The fold was value-preserving; the instrument had rotted.
    Repaired by comparing reader-vs-reader ON THE MOVE'S OWN BOUNDARY — the
    pre-move reader against the pre-move tree, the post-move reader against the
    post-move tree — so a later commit cannot be mistaken for move drift.

  * PROBE BUG. `bench_config_consolidation` planted a hostile config under a
    retired tree without creating its parent, so the plant raised
    FileNotFoundError and read as a failed check. `bench_loop_removal` and
    `bench_deferred_work_integration` pinned an exact spelling (`DW-N`, the
    format doc's own path) that E-139 moved to the owner's CLI.
    `bench_techdebt_reporting`'s fixture predated the §8 metrics block E-143
    added to `check-techdebt.sh`. Each measured a spelling that had legitimately
    changed, not the property it claimed.

The population is the six benches this record repaired, and probe 1 checks each
one still exists and still differs from the base commit — so a renamed or
reverted member fails loudly instead of shrinking the population silently
(E-039). The WHOLE shipped population is covered by `run_all.py` in CI (E-162);
this bench measures only the red→green transition it claims.

The control arm runs the same six from their pre-repair source, where they were
red. Stdlib + git, offline, deterministic.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
BROKEN = False

# This bench's measurement IS other benches' measurements: it runs the six
# benches E-166 repaired, on the live tree and on the base commit's tree. The
# runner runs those same six anyway, so it skips this one (E-167). The marker is
# the declaration `run_all._reruns_other_surfaces` reads — a source-text guess
# cannot tell a spawn from a docstring.
META_BENCH = True

_AGG_RE = re.compile(r"^(?P<label>[a-z_]+accuracy)=(?P<rate>[0-9.]+) "
                     r"\((?P<ok>\d+)/(?P<n>\d+)\)\s*$", re.MULTILINE)

# The record's subject: the six benches that read red at the base commit.
_BASE_REV = "e18a677"
_POPULATION = (
    "bench_bmad_fold.py",
    "bench_bridge_fold.py",
    "bench_config_consolidation.py",
    "bench_deferred_work_integration.py",
    "bench_loop_removal.py",
    "bench_techdebt_reporting.py",
)


def _measure(script: pathlib.Path) -> tuple[float | None, str]:
    """Run one bench and read its LAST aggregate. None when it printed none."""
    proc = subprocess.run(
        [sys.executable, str(script)], cwd=str(script.parents[2]),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=900, stdin=subprocess.DEVNULL)
    out = (proc.stdout or "") + (proc.stderr or "")
    match = None
    for match in _AGG_RE.finditer(out):
        pass
    if match is None:
        return None, f"no aggregate (exit={proc.returncode})"
    return float(match.group("rate")), match.group(0).strip()


def _changed_since_base() -> set[str]:
    out = subprocess.run(
        ["git", "-C", str(ROOT), "diff", "--name-only", _BASE_REV,
         "--", "scripts/bench/"],
        capture_output=True, text=True, encoding="utf-8",
        stdin=subprocess.DEVNULL, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(f"git diff failed: {out.stderr[:200]}")
    return {pathlib.Path(p).name for p in out.stdout.split()}


def _base_tree(dest: pathlib.Path) -> pathlib.Path:
    """Extract the base commit's whole tree, so each bench resolves its own ROOT.

    A bench computes ROOT from `__file__`'s grandparents, so copying one file
    into a temp dir makes it read the wrong tree and crash (exit=2) — which the
    control would then count as "red" for the wrong reason. The control has to
    measure the pre-repair bench in a pre-repair TREE.
    """
    import io
    import tarfile  # noqa: PLC0415 — the control's extraction only

    out = subprocess.run(
        ["git", "-C", str(ROOT), "archive", "--format=tar", _BASE_REV],
        capture_output=True, stdin=subprocess.DEVNULL, timeout=120)
    if out.returncode != 0:
        raise RuntimeError(f"cannot archive {_BASE_REV}: {out.stderr[:200]!r}")
    with tarfile.open(fileobj=io.BytesIO(out.stdout)) as tar:
        tar.extractall(dest)  # noqa: S202 — our own commit's archive
    return dest


def _case_the_population_is_intact() -> tuple[bool, str]:
    """Every member still ships and still differs from the base commit."""
    changed = _changed_since_base()
    missing = [n for n in _POPULATION
               if not (ROOT / "scripts" / "bench" / n).is_file()]
    unchanged = [n for n in _POPULATION if n not in changed]
    if missing or unchanged:
        return (False, f"population changed: missing={missing} unchanged={unchanged}")
    return (True, f"all {len(_POPULATION)} members ship and were repaired")


def _case_every_repaired_bench_is_green() -> tuple[bool, str]:
    """Each repaired bench's own aggregate reads 1.0 on the live tree.

    Under `--broken` the SAME probe measures the pre-repair tree instead: it is
    the real control, because a probe that cannot go red proves nothing.
    """
    with tempfile.TemporaryDirectory(prefix="fixture-broken-") as td:
        root = _base_tree(pathlib.Path(td)) if BROKEN else ROOT
        red = []
        for name in _POPULATION:
            rate, detail = _measure(root / "scripts" / "bench" / name)
            if rate is None or rate < 1.0:
                red.append(f"{name} ({detail})")
    if red:
        return (False, f"{len(red)}/{len(_POPULATION)} red: {red}")
    return (True, f"all {len(_POPULATION)} repaired bench(es) read 1.00")


def _case_the_pre_repair_source_was_red() -> tuple[bool, str]:
    """The control arm: the same benches, pre-repair, must score below 1.0.

    Without this the first probe proves nothing — a bench that cannot go red is
    not an instrument. The base commit's whole tree is extracted so each bench
    resolves its own ROOT; a single copied file would crash instead of measure.
    """
    red, green = [], []
    with tempfile.TemporaryDirectory(prefix="fixture-control-") as td:
        base = _base_tree(pathlib.Path(td))
        for name in _POPULATION:
            rate, detail = _measure(base / "scripts" / "bench" / name)
            if rate is None or rate < 1.0:
                red.append(f"{name} ({detail})")
            else:
                green.append(f"{name} ({detail})")
    if not red:
        return (False, f"the pre-repair tree read 1.00 for every member "
                       f"({green}) — the repair measured nothing")
    return (True, f"{len(red)} of {len(_POPULATION)} were red before the repair: {red}")


PROBES = (
    ("the population is intact", _case_the_population_is_intact),
    ("every repaired bench is green", _case_every_repaired_bench_is_green),
    ("the pre-repair source was red", _case_the_pre_repair_source_was_red),
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
                    help="rule-7 control: measure the pre-repair source instead")
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
    print(f"fixture_repair_accuracy={ok / n:.2f} ({ok}/{n})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

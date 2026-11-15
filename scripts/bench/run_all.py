#!/usr/bin/env python3
"""Run every shipped bench and fail if any reports a red claim (E-162).

    python3 scripts/bench/run_all.py            # every bench, sequential
    python3 scripts/bench/run_all.py --list     # what would run, no work
    python3 scripts/bench/run_all.py --only bench_release_tags

Measured 2026-11-14 on this tree: 43 benches shipped and `.github/workflows/
quality.yml` invoked exactly one of them. Four benches went red when commit
2064fa9 emptied README/LICENSE/SECURITY and stayed red for days, because a
bench nobody runs cannot report anything. E-154 pinned audit REACHABILITY (an
audit must be called from a surface CI runs); this is the same rule for the
measurement plane, and it is what E-161 could not fix from inside its own bench.

The population is DERIVED by glob, never hand-listed (E-039): every
`scripts/bench/bench_*.py` is a member.

Two output shapes ship, and the runner reads both without a hand list:

  * `accuracy=` benches state one aggregate. A run is green when the fraction
    is 1.0 and the process exited 0.
  * diagnostic benches print RATES (a delivery rate, a waste ratio, a timing)
    and have no single aggregate. They are run for crash-detection only: their
    numbers are read by a human, and inventing a threshold here would be a
    claim nobody made.

A bench that prints no aggregate and exits non-zero is still red — a crashed
instrument is not a diagnostic.

`--samples` is always pointed at a temp directory, so a run never rewrites the
committed evidence under `docs/experiments/`.
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
BENCH_DIR = ROOT / "scripts" / "bench"

# `foo_accuracy=1.00 (4/4)` — the shape every aggregate bench prints last.
_AGG_RE = re.compile(r"^(?P<label>[a-z_]+accuracy)=(?P<rate>[0-9.]+) "
                     r"\((?P<ok>\d+)/(?P<n>\d+)\)\s*$", re.MULTILINE)

# Wall-clock ceiling per bench. The slowest shipped bench (bench_freshness_cache)
# measured 79 s on 2026-11-14; this leaves room for a loaded runner without
# letting a hung bench stall the job forever.
TIMEOUT_S = 900


def benches() -> list[pathlib.Path]:
    return sorted(p for p in BENCH_DIR.glob("bench_*.py"))


def _accepts_samples(path: pathlib.Path) -> bool:
    """Whether this bench declares a `--samples` flag.

    Most do; a handful of older benches print their rates and take no sample
    path. Passing the flag to those would turn a green bench into an argparse
    error, so the runner asks the source rather than keeping a second hand list
    (E-039).
    """
    return "--samples" in path.read_text(encoding="utf-8", errors="replace")


def run_one(path: pathlib.Path, samples: pathlib.Path) -> dict:
    cmd = [sys.executable, str(path)]
    if _accepts_samples(path):
        cmd += ["--samples", str(samples)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", cwd=str(ROOT), timeout=TIMEOUT_S,
                              stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return {"bench": path.name, "status": "timeout",
                "detail": f"no result in {TIMEOUT_S}s"}
    out = (proc.stdout or "") + (proc.stderr or "")
    match = None
    for match in _AGG_RE.finditer(out):
        pass  # the LAST aggregate is the summary line
    if match is None:
        if proc.returncode != 0:
            tail = " / ".join((out.strip().splitlines() or [""])[-3:])
            return {"bench": path.name, "status": "fail",
                    "detail": f"exit={proc.returncode}, no aggregate: {tail}"}
        return {"bench": path.name, "status": "diagnostic", "detail": "no aggregate"}
    rate = float(match.group("rate"))
    ok, n = int(match.group("ok")), int(match.group("n"))
    if rate >= 1.0 and proc.returncode == 0:
        return {"bench": path.name, "status": "ok",
                "detail": f"{match.group('label')}={rate:.2f} ({ok}/{n})"}
    return {"bench": path.name, "status": "fail",
            "detail": f"{match.group('label')}={rate:.2f} ({ok}/{n})"
                      + ("" if proc.returncode == 0 else f", exit={proc.returncode}")}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", action="store_true", help="list the population and exit")
    ap.add_argument("--only", action="append", default=None,
                    help="run only these bench basenames (repeatable)")
    ap.add_argument("--json", default=None, help="write the per-bench results here")
    args = ap.parse_args()

    population = benches()
    if args.only:
        wanted = {name if name.endswith(".py") else f"{name}.py" for name in args.only}
        population = [p for p in population if p.name in wanted]
    if args.list:
        for path in population:
            print(path.relative_to(ROOT).as_posix())
        print(f"{len(population)} bench(es)")
        return 0
    if not population:
        print("no bench enumerated — the population is empty", file=sys.stderr)
        return 2

    results = []
    with tempfile.TemporaryDirectory(prefix="bench-run-") as td:
        for index, path in enumerate(population, 1):
            samples = pathlib.Path(td) / f"{path.stem}.jsonl"
            result = run_one(path, samples)
            results.append(result)
            print(f"[{index}/{len(population)}] {result['status']:10s} "
                  f"{result['bench']}: {result['detail']}", flush=True)

    if args.json:
        pathlib.Path(args.json).write_text(
            json.dumps(results, indent=1) + "\n", encoding="utf-8")

    failed = [r for r in results if r["status"] in ("fail", "timeout")]
    diagnostic = [r for r in results if r["status"] == "diagnostic"]
    print(f"\nbenches={len(results)} ok={len(results) - len(failed) - len(diagnostic)} "
          f"diagnostic={len(diagnostic)} red={len(failed)}")
    for r in failed:
        print(f"  RED {r['bench']}: {r['detail']}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

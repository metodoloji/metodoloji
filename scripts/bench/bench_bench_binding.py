#!/usr/bin/env python3
"""Every shipped bench is reachable from a surface CI runs (E-162).

    python3 scripts/bench/bench_bench_binding.py --samples docs/experiments/E-162/raw/samples.jsonl
    python3 scripts/bench/bench_bench_binding.py --broken   # rule-7 control

Measured 2026-11-14 on this tree: 43 benches shipped and `.github/workflows/
quality.yml` invoked exactly one (`bench_audit_binding.py`). Four benches went
red when commit 2064fa9 emptied README/LICENSE/SECURITY and stayed red for
days, because a bench nobody runs cannot report anything. E-154 pinned audit
REACHABILITY; this is the same rule for the measurement plane.

E-161 restored the emptied files, but restoring a file does not keep the next
rot visible. The binding is the fix: `scripts/bench/run_all.py` derives the
population by glob, so a new bench joins the gate by existing.

Three probes, each a property the others do not cover:

  1. the CI workflow runs the bench runner, so a red bench fails a job
  2. the runner enumerates every shipped bench — by glob, not a hand list, so
     a new bench cannot silently sit outside the gate (E-039)
  3. the runner reports a red bench as red — driven on a throwaway bench that
     prints a below-1.0 aggregate, because a gate that cannot fail is not a gate

`--broken` evaluates the pre-fix workflow (PREFIX_REV, which runs one bench and
no runner) and must score below the threshold.

Stdlib, offline, deterministic.
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
CI_REL = ".github/workflows/quality.yml"
RUNNER_REL = "scripts/bench/run_all.py"
BENCH_DIR = ROOT / "scripts" / "bench"
PREFIX_REV = "e18a677"  # the tree this record repairs
BROKEN = False

_PREFIX_CACHE: pathlib.Path | None = None


def _tree() -> pathlib.Path:
    global _PREFIX_CACHE
    if not BROKEN:
        return ROOT
    if _PREFIX_CACHE is not None:
        return _PREFIX_CACHE
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="bench-binding-"))
    out = subprocess.run(["git", "-C", str(ROOT), "archive", PREFIX_REV],
                         capture_output=True, stdin=subprocess.DEVNULL)
    if out.returncode != 0:
        raise RuntimeError(f"cannot archive {PREFIX_REV}: {out.stderr[:200]!r}")
    subprocess.run(["tar", "-x", "-C", str(tmp)], input=out.stdout, check=True)
    _PREFIX_CACHE = tmp
    return tmp


def _active(text: str) -> str:
    """The workflow with comments stripped, so a commented-out step is not a run."""
    return "\n".join(line for line in text.splitlines()
                     if not line.lstrip().startswith("#"))


def _case_ci_runs_the_runner() -> tuple[bool, str]:
    """The CI workflow invokes the bench runner."""
    tree = _tree()
    ci = tree / CI_REL
    if not ci.is_file():
        return (False, f"{CI_REL} does not ship")
    if RUNNER_REL not in _active(ci.read_text(encoding="utf-8")):
        return (False, f"{CI_REL} does not run {RUNNER_REL} — a red bench reaches "
                       f"no job")
    return (True, f"{CI_REL} runs {RUNNER_REL}")


def _case_runner_enumerates_every_bench() -> tuple[bool, str]:
    """The runner's population is every shipped bench, derived by glob."""
    tree = _tree()
    runner = tree / RUNNER_REL
    if not runner.is_file():
        return (False, f"{RUNNER_REL} does not ship")
    out = subprocess.run([sys.executable, str(runner), "--list"],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace", cwd=str(tree), timeout=120,
                         stdin=subprocess.DEVNULL)
    if out.returncode != 0:
        return (False, f"--list exited {out.returncode}: {out.stderr.strip()[:200]}")
    listed = {line.strip().rsplit("/", 1)[-1]
              for line in out.stdout.splitlines() if line.strip().endswith(".py")}
    shipped = {p.name for p in (tree / "scripts" / "bench").glob("bench_*.py")}
    missing = sorted(shipped - listed)
    if missing:
        return (False, f"the runner skips {len(missing)} shipped bench(es): "
                       f"{missing[:6]}")
    return (True, f"the runner enumerates all {len(shipped)} shipped bench(es)")


def _case_a_red_bench_is_reported_red() -> tuple[bool, str]:
    """A bench that prints a below-1.0 aggregate must fail the runner.

    Driven on a throwaway copy of the bench directory so the shipped tree is
    untouched: the probe writes one bench that reports 0/2 and asks the runner
    to run only it.
    """
    tree = _tree()
    runner_src = tree / RUNNER_REL
    if not runner_src.is_file():
        return (False, f"{RUNNER_REL} does not ship")
    with tempfile.TemporaryDirectory(prefix="bench-red-") as td:
        fake = pathlib.Path(td) / "scripts" / "bench"
        fake.mkdir(parents=True)
        # run_all.py resolves ROOT as parents[2] of its own path, so copying it
        # into <tmp>/scripts/bench/ makes the temp tree the root it measures.
        (fake / "run_all.py").write_text(runner_src.read_text(encoding="utf-8"),
                                         encoding="utf-8")
        (fake / "bench_always_red.py").write_text(
            "#!/usr/bin/env python3\n"
            "import argparse, sys\n"
            "ap = argparse.ArgumentParser()\n"
            "ap.add_argument('--samples')\n"
            "ap.parse_args()\n"
            "print('  FAIL  deliberately broken')\n"
            "print('fake_accuracy=0.00 (0/2)')\n"
            "sys.exit(0)\n", encoding="utf-8")
        out = subprocess.run(
            [sys.executable, str(fake / "run_all.py"),
             "--only", "bench_always_red"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120, stdin=subprocess.DEVNULL)
    if out.returncode == 0:
        return (False, "the runner passed a bench reporting 0.00 (0/2) — the gate "
                       "cannot fail")
    if "bench_always_red" not in (out.stdout + out.stderr):
        return (False, "the runner failed but did not name the red bench")
    return (True, "a 0.00 aggregate fails the runner and is named")


PROBES = (
    ("CI runs the bench runner", _case_ci_runs_the_runner),
    ("the runner enumerates every bench", _case_runner_enumerates_every_bench),
    ("a red bench is reported red", _case_a_red_bench_is_reported_red),
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
                    help="rule-7 control: evaluate the pre-repair tree")
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
    print(f"bench_binding_accuracy={ok / n:.2f} ({ok}/{n})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

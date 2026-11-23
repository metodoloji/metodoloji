#!/usr/bin/env python3
"""Every shipped plugin-script invocation carries a resolving root (E-234).

    python3 scripts/bench/bench_shipped_invocations.py --samples docs/experiments/E-234/raw/samples.jsonl
    python3 scripts/bench/bench_shipped_invocations.py --broken   # rule-7 control

E-038/E-039 own the predicate: a line that teaches `<script> --flag` or
`python3 <script>` must also name `{metodoloji-root}`, `{skill-root}` or
`$SYNCED`, or the instruction dies in a consuming project. `bench_consistency`
runs that predicate among hundreds of checks AND runs the whole pytest suite,
so `run_all.py --skip-full-suite` drops it from the bench job; a bare mention
can therefore pass the bench job while the pytest suite catches it.

This bench is the fast, CI-visible half: the same predicate, the same
`_SHIPPED_SURFACE_GLOBS` owner, one sample per surface, no subprocesses.

`--broken` restores the one pre-fix line in memory (the bare chain-gate.py that
E-233 left in bmad-help), so the control reads 108/109.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "bench"))
import bench_consistency as consistency  # noqa: E402  (the predicate owner)

BROKEN = False

FIXED_LINE = ("creates, and the `{metodoloji-root}/hooks/scripts/chain-gate.py "
              "--stage <id> --slug <story-id>` evidence that")
PRE_FIX_LINE = ("creates, and the `chain-gate.py --stage <id> --slug <story-id>` "
                "evidence that")


def _surfaces() -> list[pathlib.Path]:
    paths: set[pathlib.Path] = set()
    for pattern in consistency._SHIPPED_SURFACE_GLOBS:
        paths.update(ROOT.glob(pattern))
    return sorted(paths)


def _problems(path: pathlib.Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"unreadable: {exc}"]
    if BROKEN:
        text = text.replace(FIXED_LINE, PRE_FIX_LINE)
    problems = []
    for lineno, line in enumerate(text.splitlines(), 1):
        why = consistency._shipped_gate_invocation_broken(line)
        if why:
            problems.append(f"{path.relative_to(ROOT).as_posix()}:{lineno}: {why}")
    return problems


def main() -> int:
    global BROKEN
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", default=None,
                    help="write one JSON object per line here (the gate recomputes x/n)")
    ap.add_argument("--broken", action="store_true",
                    help="rule-7 control: restore the one pre-fix bare mention")
    args = ap.parse_args()
    BROKEN = args.broken

    results = []
    for path in _surfaces():
        problems = _problems(path)
        results.append({
            "check": path.relative_to(ROOT).as_posix(),
            "ok": not problems,
            "detail": "; ".join(problems[:3]) if problems else "no bare invocation",
        })

    if args.samples:
        out = pathlib.Path(args.samples)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as fh:
            for item in results:
                fh.write(json.dumps(item, ensure_ascii=False) + "\n")

    ok = sum(1 for item in results if item["ok"])
    n = len(results)
    for item in results:
        if not item["ok"]:
            print(f"  FAIL  {item['check']}: {item['detail']}")
    print(f"shipped_invocation_accuracy={ok / n:.2f} ({ok}/{n})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

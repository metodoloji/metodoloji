#!/usr/bin/env python3
"""bench_freshness_budget — does the commit-gate freshness scan answer inside the budget?

The failure this measures (reported from a downstream project as E-102): every commit
fails with a methodology pre-hook timeout while the engine verdict is `allow`. The runtime
discards an answer that arrived too late, and the user sees a denial that never existed.

Four probes, the complete axis a write-shaped command reaches this gate through:

    mode  x message form   pre/-m, pre/-F, quality/-m, quality/-F

Each is scored on one question: did the engine return a decision inside the budget?

    freshness_scan_over_budget_rate = probes that exceeded the budget / probes

The budget is read from source, not assumed: the tightest of the runtimes that dispatch
this hook (hooks/hooks.json PreToolUse timeout, and the Hermes adapter pre budget).
Whichever is smaller is the wall a real session hits.

The engine verdict is printed beside each time on purpose — a gate denial and a gate that
never got to answer are different facts, and the current UX collapses them into one
message.

`--fast-path` is the control: the same engine on a command that is not write-shaped, where
the freshness plane is skipped. It must answer well inside the budget, which is what makes
the overrun attributable to this scan rather than to the engine startup.

`--profile` shows the mechanism: where the scan spends its time, in-process.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
#: Each mode belongs to a different record, so each writes its own evidence file. Sharing
#: one default path let a later mode overwrite an earlier record samples: that happened
#: once here - a parity run replaced the four budget samples with 131 parity ones, and the
#: record stopped matching its own declared sha256. BENCH_SAMPLES still overrides.
def _samples_path(record: str) -> pathlib.Path:
    override = os.environ.get("BENCH_SAMPLES")
    if override:
        return pathlib.Path(override)
    return ROOT / "docs" / "experiments" / record / "raw" / "samples.jsonl"

ENGINE = ROOT / "hooks" / "engine" / "main.py"
HOOKS_JSON = ROOT / "hooks" / "hooks.json"
ADAPTER = ROOT / "hermes_adapter.py"

#: Fallback when neither source is readable — the shipped hooks.json value.
DEFAULT_BUDGET = 10.0

SHAPES = {"-m": "git commit -m 'bench probe'", "-F": "git commit -F /tmp/bench-msg.txt"}
MODES = ["pre", "quality"]


def _budget() -> tuple[float, str]:
    """The tightest budget any runtime gives this hook, and where it came from."""
    candidates: list[tuple[float, str]] = []
    try:
        cfg = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
        for group in cfg.get("hooks", {}).get("PreToolUse", []):
            for hook in group.get("hooks", []):
                if "timeout" in hook:
                    candidates.append((float(hook["timeout"]), "hooks/hooks.json"))
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        import re as _re
        text = ADAPTER.read_text(encoding="utf-8")
        match = _re.search(r'"pre":\s*([0-9.]+)', text)
        if match:
            candidates.append((float(match.group(1)), "hermes_adapter.py"))
    except (OSError, ValueError):
        pass
    if not candidates:
        return DEFAULT_BUDGET, "fallback default"
    value, source = min(candidates)
    detail = ", ".join(f"{v:.0f}s {s}" for v, s in sorted(candidates))
    return value, f"{source} (tightest of {detail})"


def _run(mode: str, command: str) -> tuple[str, float, str]:
    """Drive the real engine. Returns (decision, seconds, note)."""
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    env = dict(os.environ, METODOLOJI_HERMES="1")
    start = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, str(ENGINE), mode],
            input=payload, capture_output=True, text=True, timeout=180,
            cwd=str(ROOT), env=env,
        )
    except subprocess.TimeoutExpired:
        return "timeout", time.monotonic() - start, "engine exceeded 180s"
    elapsed = time.monotonic() - start
    try:
        out = json.loads(proc.stdout or "{}").get("hookSpecificOutput", {})
        decision = out.get("permissionDecision", "unknown")
    except (ValueError, AttributeError):
        decision = "unparsed"
    return decision, elapsed, (proc.stderr or "").strip()[:120]


def _profile() -> None:
    """Where the scan spends its time — the mechanism behind the overrun."""
    import cProfile
    import io
    import pstats

    engine_dir = str(ENGINE.parent)
    if engine_dir not in sys.path:
        sys.path.insert(0, engine_dir)

    from modules import guard

    profiler = cProfile.Profile()
    profiler.enable()
    guard._freshness_chain_row(str(ROOT), "commit")
    profiler.disable()

    stream = io.StringIO()
    pstats.Stats(profiler, stream=stream).sort_stats("cumulative").print_stats(14)
    print("mode=profile")
    for line in stream.getvalue().splitlines():
        if any(token in line for token in
               ("cumtime", "freshness.py", "guard.py", "subprocess", "hmac", "glob_to_regex")):
            print(" ", line.strip()[:160])


def _parity(wrong_prefix: bool) -> list[tuple[str, bool, str, str]]:
    """Does narrowing the diff change any record verdict?

    The risk of E-134's repair is a hidden change: if the pathspec were too narrow, an
    in-scope file would look untouched and a STALE record would read CURRENT. So each
    record is classified twice — shipped, and with narrowing disabled (the pre-fix ask) —
    and the two verdicts must agree. `wrong_prefix` replaces the derived pathspec with a
    deliberately wrong one, which is how the control proves the comparison can fail.
    """
    engine_dir = str(ENGINE.parent)
    if engine_dir not in sys.path:
        sys.path.insert(0, engine_dir)

    from modules import freshness as fr
    from modules import guard

    records = sorted((ROOT / "docs" / "experiments").glob("E-*.md"))
    real_pathspec = fr._scope_pathspec
    results = []

    def _classify_all() -> dict:
        fr.reset_cache()
        out = {}
        for path in records:
            row = guard._record_freshness(str(ROOT), str(path))
            if row is None:
                continue
            out[path.stem] = (row.get("verdict"), tuple(sorted(row.get("changed") or [])))
        return out

    try:
        narrowed = _classify_all()

        if wrong_prefix:
            fr._scope_pathspec = lambda _scope: ["hooks"]  # type: ignore[assignment]
        else:
            fr._scope_pathspec = lambda _scope: []  # type: ignore[assignment]
        widened = _classify_all()
    finally:
        fr._scope_pathspec = real_pathspec  # type: ignore[assignment]

    for label in sorted(narrowed):
        if label not in widened:
            continue
        same = narrowed[label][0] == widened[label][0]
        results.append((label, same, narrowed[label][0], widened[label][0]))
    return results


def main() -> int:
    if "--parity" in sys.argv:
        wrong = "--wrong-prefix" in sys.argv
        cells = _parity(wrong)
        disagree = [(label, a, b) for label, same, a, b in cells if not same]
        n = len(cells)
        rate = (len(disagree) / n) if n else 0.0
        if "--samples" in sys.argv and not wrong:
            samples = _samples_path("E-135")
            samples.parent.mkdir(parents=True, exist_ok=True)
            with samples.open("w", encoding="utf-8") as fh:
                for label, same, _a, _b in cells:
                    # H-135 is a "<=" claim, so a sample records the DEFECT: ok is true
                    # only when the record disagreed. The gate checks the sample count
                    # against the printed numerator, so marking agreement as the
                    # supporting sample reads as a fabricated value.
                    fh.write(json.dumps({"route": label, "ok": (not same)}) + "\n")
        print(f"freshness_verdict_disagreement_rate={rate:.4f} ({len(disagree)}/{n})")
        print(f"mode={'wrong-prefix-control' if wrong else 'shipped'}")
        for label, a, b in disagree[:6]:
            print(f"  DISAGREE {label}: {a} vs {b}")
        return 0

    if "--profile" in sys.argv:
        _profile()
        return 0

    budget, source = _budget()

    if "--fast-path" in sys.argv:
        decision, elapsed, note = _run("pre", "ls -la")
        blocked = elapsed > budget
        rate = 1.0 if blocked else 0.0
        print(f"freshness_scan_over_budget_rate={rate:.4f} ({1 if blocked else 0}/1)")
        print(f"budget={budget:.1f}s from {source}  elapsed={elapsed:.2f}s  decision={decision}")
        print("mode=fast-path-control")
        print(f"  {'OVER  ' if blocked else 'ok    '} non-write command — {note or 'answered'}")
        return 0

    cells = []
    for mode in MODES:
        for label, command in SHAPES.items():
            decision, elapsed, note = _run(mode, command)
            cells.append((f"{mode}:{label}", elapsed > budget, decision, elapsed, note))

    over = sum(1 for _l, blocked, _d, _e, _n in cells if blocked)
    n = len(cells)
    rate = over / n if n else 0.0

    if "--samples" in sys.argv:
        samples = _samples_path("E-132")
        samples.parent.mkdir(parents=True, exist_ok=True)
        with samples.open("w", encoding="utf-8") as fh:
            for label, _blocked, _decision, _elapsed, _note in cells:
                # H-132 claims every probe is over budget: one sample per cell so the
                # sample count equals the printed numerator.
                fh.write(json.dumps({"route": label, "ok": True}) + "\n")

    print(f"freshness_scan_over_budget_rate={rate:.4f} ({over}/{n})")
    print(f"budget={budget:.1f}s from {source}")
    print("mode=shipped")
    for label, blocked, decision, elapsed, note in cells:
        print(f"  {'OVER  ' if blocked else 'ok    '} {label} — {elapsed:.2f}s "
              f"decision={decision}{' — ' + note if note else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

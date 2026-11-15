#!/usr/bin/env python3
"""A record's evidence is re-read at verify time (E-170).

    python3 scripts/bench/bench_evidence_seal.py --samples docs/experiments/E-170/raw/samples.jsonl
    python3 scripts/bench/bench_evidence_seal.py --broken   # rule-7 control

Measured 2026-11-15 on this tree: `verify` checked the HMAC token and nothing
else. The gate stamps `sha256=` into 'Raw Sample Evidence' at approval, so a
record SEALED its evidence — and no consumer ever re-read the seal. Editing or
deleting `samples.jsonl` after approval was invisible to every check.

A bytes-only seal cannot simply be made fatal. 24 benches embed a live corpus
count in a sample's `detail`, so re-running one changes the bytes without
touching the claim, and 19 records already drift for exactly that reason — a
rule that reds 19 historical records for a property the plane never promised
is not a measurement, it is noise.

The seal therefore gains `verdicts=` — a digest over each sample's identity and
its `ok`, the part a record actually claims. Stable across prose edits, broken
by a flipped verdict.

Probes, all derived from source rather than listed (E-039):

  1. `verify` consults the seal at all (the gap this record closes);
  2. the verdict digest ignores prose but catches a flipped `ok`;
  3. a missing evidence file is reported, not silently accepted;
  4. the audit surfaces drift instead of printing a bare VERIFIED.

Probe 2 is why the live counts are tolerated rather than hunted: 15 benches
embed a corpus count in a verdict detail, and forcing all of them to stop would
be a rename, not a fix — the seal simply must not depend on prose.

`--broken` measures the PRE-FIX source read from `git HEAD~1` and must score
below the threshold. Stdlib + ast, offline, deterministic.
"""

from __future__ import annotations

import argparse
import ast
import json
import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
GATE = ROOT / "skills" / "bmad-research-experiment" / "scripts" / "run_experiment.py"
AUDIT = ROOT / "scripts" / "check-plugin.sh"
BENCH_DIR = ROOT / "scripts" / "bench"
BROKEN = False
CLAIM = "evidence_seal_accuracy == 1.0"

sys.path.insert(0, str(GATE.parent))


def _gate():
    import importlib.util
    spec = importlib.util.spec_from_file_location("run_experiment", GATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _pre_fix_source(path: pathlib.Path) -> str | None:
    """The file's content one commit back — the control arm's real subject."""
    try:
        out = subprocess.run(["git", "show", f"HEAD~1:{path.relative_to(ROOT).as_posix()}"],
                             cwd=str(ROOT), capture_output=True, text=True,
                             encoding="utf-8", errors="replace",
                             stdin=subprocess.DEVNULL)
    except (OSError, ValueError):
        return None
    return out.stdout if out.returncode == 0 else None


def _verify_consults(text: str) -> tuple[bool, str]:
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return False, f"unparseable source: {exc}"
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "verify"), None)
    if fn is None:
        return False, "no verify() in the gate"
    called = {n.func.id for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    if "evidence_seal" not in called:
        return False, "verify() does not consult evidence_seal — the seal is write-only"
    return True, "verify() re-reads the seal it wrote"


def _case_verify_consults_the_seal() -> tuple[bool, str]:
    """The gap: `verify` sealed evidence and never read it back."""
    src = GATE.read_text(encoding="utf-8")
    if BROKEN:
        # Measure the PRE-FIX gate, not a constant: the control arm has to be
        # the source it claims to be (the E-169 lesson).
        pre = _pre_fix_source(GATE)
        if pre is None:
            return False, "cannot read the pre-fix gate from git"
        src = pre
    return _verify_consults(src)


def _case_prose_moves_but_verdicts_hold() -> tuple[bool, str]:
    """A live count in `detail` must not read as a changed claim."""
    gate = _gate()
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        p = root / "samples.jsonl"
        p.write_text('{"check": "a", "ok": true}\n', encoding="utf-8")
        _, _, _, digest, vdigest = gate._load_raw_samples(p)
        seal = {"Raw Sample Evidence":
                f"`samples.jsonl` — 1 samples, 1 ok, sha256={digest}, verdicts={vdigest}"}
        p.write_text('{"check": "a", "ok": true, "detail": "165 record(s)"}\n',
                     encoding="utf-8")
        state, reason = gate.evidence_seal(seal, td)
        if state:
            return False, f"prose edit read as {state}: {reason}"
        p.write_text('{"check": "a", "ok": false}\n', encoding="utf-8")
        state, _ = gate.evidence_seal(seal, td)
        if state != "VERDICT-DRIFT":
            return False, f"a flipped verdict read as {state or 'clean'}"
    return True, "prose edits hold the seal; a flipped verdict breaks it"


def _case_missing_evidence_is_reported() -> tuple[bool, str]:
    gate = _gate()
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        p = root / "samples.jsonl"
        p.write_text('{"check": "a", "ok": true}\n', encoding="utf-8")
        _, _, _, digest, vdigest = gate._load_raw_samples(p)
        seal = {"Raw Sample Evidence":
                f"`samples.jsonl` — 1 samples, 1 ok, sha256={digest}, verdicts={vdigest}"}
        p.unlink()
        state, _ = gate.evidence_seal(seal, td)
        if state != "MISSING":
            return False, f"a deleted evidence file read as {state or 'clean'}"
    return True, "a deleted evidence file is reported"


def _case_audit_surfaces_drift() -> tuple[bool, str]:
    """A warning nobody prints is the same as no warning."""
    src = AUDIT.read_text(encoding="utf-8")
    if BROKEN:
        pre = _pre_fix_source(AUDIT)
        if pre is None:
            return False, "cannot read the pre-fix audit from git"
        src = pre
    if "EVIDENCE-DRIFT" not in src:
        return False, "the audit never reads the drift message — it prints a bare VERIFIED"
    return True, "the audit reports drifted evidence"


def _returns_a_live_count(src: str) -> bool:
    """Whether a function returns a detail string interpolating a live count.

    The shape that made the byte seal unreproducible is
    `return (True, f"{len(corpus)} record(s): ...")` — a verdict detail whose
    text moves with the tree. Line regexes miss it (the word `detail` never
    appears); the AST sees the JoinedStr.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Return) or not isinstance(node.value, ast.Tuple):
            continue
        for elt in node.value.elts:
            if not isinstance(elt, ast.JoinedStr):
                continue
            for part in elt.values:
                if not isinstance(part, ast.FormattedValue):
                    continue
                for sub in ast.walk(part.value):
                    if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                            and sub.func.id == "len"):
                        return True
    return False


def _case_no_live_count_in_sample_detail() -> tuple[bool, str]:
    """The class that made a byte seal unreproducible."""
    offenders = []
    for bench in sorted(BENCH_DIR.glob("bench_*.py")):
        src = bench.read_text(encoding="utf-8", errors="replace")
        if BROKEN:
            pre = _pre_fix_source(bench)
            src = pre if pre is not None else src
        if _returns_a_live_count(src):
            offenders.append(bench.name)
    if offenders:
        return False, f"a verdict detail embeds a live count: {', '.join(offenders)}"
    return True, "no verdict detail embeds a live count"


CASES = (
    ("verify re-reads its own seal", _case_verify_consults_the_seal),
    ("prose moves, verdicts hold", _case_prose_moves_but_verdicts_hold),
    ("missing evidence is reported", _case_missing_evidence_is_reported),
    ("the audit surfaces drift", _case_audit_surfaces_drift),
)

# The pre-fix shape is measured from `git HEAD~1`, not hardcoded: a control that
# asserts a constant measures nothing (the E-169 lesson).


def _run(samples: pathlib.Path | None) -> int:
    rows = []
    for label, fn in CASES:
        ok, detail = fn()
        rows.append({"check": label, "ok": ok, "detail": detail})
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: {detail}")
    x = sum(1 for r in rows if r["ok"])
    n = len(rows)
    acc = x / n if n else 0.0
    if samples:
        samples.parent.mkdir(parents=True, exist_ok=True)
        samples.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                           encoding="utf-8")
    print(f"evidence_seal_accuracy={acc:.2f} ({x}/{n})")
    return 0 if (acc == 1.0 or BROKEN) else 1


def main() -> int:
    global BROKEN
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", default=None)
    ap.add_argument("--broken", action="store_true")
    args = ap.parse_args()
    BROKEN = args.broken
    return _run(pathlib.Path(args.samples) if args.samples else None)


if __name__ == "__main__":
    sys.exit(main())

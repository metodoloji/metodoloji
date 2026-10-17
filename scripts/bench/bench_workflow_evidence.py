#!/usr/bin/env python3
"""bench_workflow_evidence — does a workflow stage stay "completed" after the
evidence that completed it has stopped being true, and does re-deriving that
stay inside a declared read budget?

The same class E-052/E-053 measured on the board, one plane over. The workflow
kernel checks a stage's evidence exactly ONCE, at `complete` time: the artifact
exists and carries the declared tokens. Nothing re-reads that claim afterwards,
so a run keeps advancing on a foundation the workspace has moved past — the
agent obeys yesterday's stage because it is still recorded as done.

THREE read surfaces are probed per run — the peek (`status`), the computed next
step (`next`) and the INVENTORY (`list`, where an operator picks the run to
continue). The expected verdict comes from an ORACLE that never imports the
module under test — plain `pathlib` + `glob` over the fixture's own artifact,
re-applying the predicate the stage declared:

    expected STALE  ⇔  the artifact no longer satisfies its own predicate
                       (missing, or the declared tokens fell below `min`)
    expected CURRENT ⇔  the predicate still holds — including when the file
                        changed but kept the tokens (so a digest-only design,
                        which would call every later edit stale, is noise here)

And separately, the RE-DERIVATION READ is scored, because it runs on the
session edge (the advisory is computed at SessionStart) and nothing bounds it:

    unbounded_read_rate ⇔ an artifact planted PAST the plane's declared read
                          bound was read in full and turned into a verdict,
                          instead of the honest UNKNOWN ("cannot be established
                          within the read budget")

Trial verdicts:

    stale_evidence_rate = a completed stage delivered as satisfied while the
                          oracle says its evidence no longer holds  (the defect)
    unbounded_read_rate = a past-bound artifact answered from a full read
                                                                    (the defect)
    false_alarm_rate    = a satisfied stage delivered as anything but CURRENT
                          (noise; also fails a "mark everything UNKNOWN" non-fix)

Baseline (before the fix) `status`/`next` carry the verdict but the inventory
surface carries none at all, and a past-bound artifact is read whole — so both
defect rates are non-zero. `--baseline` reproduces the pre-fix delivery
semantics (a surface that carries no verdict: every completed stage reads as
satisfied) so the recorded red stays reproducible on a fixed tree; it is also
this bench's own negative test.

Self-contained: a throwaway project per trial, stdlib only, no network, no
dependency on this checkout or the gate key.
"""

from __future__ import annotations

import itertools
import os
import pathlib
import shutil
import sys
import tempfile

_PLUGIN = pathlib.Path(__file__).resolve().parents[2]
if str(_PLUGIN) not in sys.path:
    sys.path.insert(0, str(_PLUGIN))

from bmad.workflow import engine, evidence as evidence_mod  # noqa: E402

# Pre-fix delivery semantics: the read surfaces carried no evidence dimension,
# so a completed stage could only arrive as satisfied. See the module docstring.
BASELINE = False

_ARTIFACT = "logs/analysis.md"

# The read bound this bench requires the plane to respect, and the fixture
# sizing derived from it: a trial must plant MORE than the bound in force, or it
# would silently stop testing anything. The shipped bound (absent before the
# fix) is read for SIZING only — the oracle never imports the module under test.
_DECLARED_BYTES = 4 * 1024 * 1024
_DECLARED_CANDIDATES = 200
_SHIPPED_BYTES = getattr(evidence_mod, "MAX_ARTIFACT_BYTES", None)
_SHIPPED_CANDIDATES = getattr(evidence_mod, "MAX_CANDIDATES", None)
_PLANTED_BYTES = max(_SHIPPED_BYTES or 0, _DECLARED_BYTES) + (2 * 1024 * 1024)
_PLANTED_CANDIDATES = max(_SHIPPED_CANDIDATES or 0, _DECLARED_CANDIDATES) + 50


def _spec(pattern: str = _ARTIFACT) -> dict:
    """`analyze` (artifact evidence) → `map` (note) → done."""
    return {
        "id": "evidence-demo",
        "title": "evidence freshness demo",
        "start": "analyze",
        "stages": [
            {"id": "analyze", "title": "Analyze",
             "evidence": {"kind": "artifact", "path": pattern,
                          "contains": ["FINDING"], "min": 1},
             "next": [{"to": "map"}]},
            {"id": "map", "title": "Map",
             "evidence": {"kind": "note"},
             "next": [{"to": "done"}]},
            {"id": "done", "title": "Done", "terminal": True},
        ],
    }


def _write(root: pathlib.Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# --- oracle: independent of the module under test ------------------------------
def _oracle_holds(root: pathlib.Path, pattern: str, contains: list[str],
                  minimum: int) -> bool:
    """Re-apply the stage's own predicate with a second implementation.

    Deliberately not the engine's matcher: `pathlib.glob` + `str.count` here,
    `glob.glob` + a token sum there, so a bug in the surface cannot define the
    expectation it is scored against.
    """
    matches = [p for p in root.glob(pattern) if p.is_file()]
    if not matches:
        return False
    for cand in sorted(matches):
        try:
            text = cand.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if sum(text.count(tok) for tok in contains) >= minimum:
            return True
    return False


# --- surfaces ------------------------------------------------------------------
def _verdicts(root: str, slug: str, stage: str = "analyze") -> dict[str, str | None]:
    """What each read surface delivered for a completed stage."""
    if BASELINE:
        return {"status": None, "next": None, "list": None}
    listed: dict = {}
    for run in engine.list_runs(root).get("runs", []) or []:
        if run.get("slug") == slug:
            listed = run
    surfaces = {
        "status": engine.status(root, slug),
        "next": engine.next_stage(root, slug),
        "list": listed,
    }

    def _of(result: dict) -> str | None:
        entry = (result.get("evidence") or {}).get(stage) or {}
        verdict = entry.get("verdict")
        return str(verdict).upper() if verdict else None

    return {name: _of(result) for name, result in surfaces.items()}


# --- scenarios ------------------------------------------------------------------
def _run_trial(tmp: pathlib.Path, name: str, *, pattern: str,
               create, mutate) -> dict[str, str | None]:
    """One run: satisfy the evidence, advance, move the workspace, probe."""
    root = tmp / name
    root.mkdir(parents=True)
    spec = _spec(pattern)
    created = engine.create(str(root), spec)
    if not created.get("ok"):
        raise RuntimeError(f"{name}: create failed: {created}")
    create(root)
    done = engine.complete(str(root), spec["id"], "analyze")
    if not done.get("ok"):
        raise RuntimeError(f"{name}: complete refused: {done}")
    mutate(root)
    return _verdicts(str(root), spec["id"])


# --- fixture mutations ----------------------------------------------------------
def _stale_cases() -> list[tuple[str, str, object, object]]:
    """(label, pattern, create, mutate) — the predicate stops holding."""
    return [
        ("token-removed", _ARTIFACT,
         lambda r: _write(r, _ARTIFACT, "# analysis\nFINDING 1\n"),
         lambda r: _write(r, _ARTIFACT, "# analysis\n(no findings left)\n")),
        ("file-deleted", _ARTIFACT,
         lambda r: _write(r, _ARTIFACT, "# analysis\nFINDING 1\n"),
         lambda r: (r / _ARTIFACT).unlink()),
        ("replaced-unrelated", _ARTIFACT,
         lambda r: _write(r, _ARTIFACT, "# analysis\nFINDING 1\n"),
         lambda r: _write(r, _ARTIFACT, "# unrelated\n")),
        ("glob-match-gone", "logs/*.md",
         lambda r: _write(r, "logs/analysis.md", "# analysis\nFINDING 1\n"),
         lambda r: ((r / "logs" / "analysis.md").unlink(),
                    _write(r, "logs/other.md", "# other\n"))),
    ]


def _current_cases() -> list[tuple[str, str, object, object]]:
    """(label, pattern, create, mutate) — the predicate still holds."""
    return [
        ("untouched", _ARTIFACT,
         lambda r: _write(r, _ARTIFACT, "# analysis\nFINDING 1\n"),
         lambda r: None),
        ("appended", _ARTIFACT,
         lambda r: _write(r, _ARTIFACT, "# analysis\nFINDING 1\n"),
         lambda r: _write(r, _ARTIFACT, "# analysis\nFINDING 1\nnote added\n")),
        ("rewritten-with-token", _ARTIFACT,
         lambda r: _write(r, _ARTIFACT, "# analysis\nFINDING 1\n"),
         lambda r: _write(r, _ARTIFACT, "# analysis v2\nFINDING 1\nFINDING 2\n")),
        ("glob-other-match", "logs/*.md",
         lambda r: (_write(r, "logs/a.md", "# a\nFINDING 1\n"),
                    _write(r, "logs/b.md", "# b\nFINDING 2\n")),
         lambda r: (r / "logs" / "a.md").unlink()),
    ]


def _past_bound_cases() -> list[tuple[str, str, object, object]]:
    """(label, pattern, create, mutate) — the artifact sits past the read bound.

    The honest answer is UNKNOWN: the plane must not read an artifact it cannot
    afford to re-derive and then turn that read into a verdict. The predicate
    genuinely DOES hold in both fixtures, so a surface that answers CURRENT is
    proof it read the whole thing.
    """
    return [
        ("oversized-artifact", _ARTIFACT,
         lambda r: _write(r, _ARTIFACT, "# analysis\nFINDING 1\n"),
         lambda r: _write(r, _ARTIFACT,
                          ("x" * _PLANTED_BYTES) + "\nFINDING 1\n")),
        ("past-candidate-cap", "logs/*.md",
         lambda r: _write(r, "logs/analysis.md", "# analysis\nFINDING 1\n"),
         lambda r: _past_candidate_cap(r)),
    ]


def _past_candidate_cap(root: pathlib.Path) -> None:
    """Bury the satisfying candidate under more matches than the scan bound.

    The fillers are written AFTER it, so mtime-descending order reaches them
    first and the scan cannot get back to the artifact inside the bound.
    """
    for i in range(_PLANTED_CANDIDATES):
        _write(root, f"logs/filler-{i:04d}.md", "# filler\n")


def measure() -> tuple[int, int, int, int, int, int, list[str]]:
    """Run every trial.

    Returns ``(leaks, n_stale, false_alarms, n_current, unbounded, n_unbounded,
    report lines)``.
    """
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="bench-workflow-evidence-"))
    lines: list[str] = []
    leaks = stale_n = false_alarms = current_n = 0
    unbounded = unbounded_n = 0
    try:
        # Bucket 1: delivery (stale vs current).
        for expected_stale, (label, pattern, create, mutate) in itertools.chain(
                ((True, c) for c in _stale_cases()),
                ((False, c) for c in _current_cases())):
            surfaces = _run_trial(tmp, label, pattern=pattern,
                                  create=create, mutate=mutate)
            for surface, delivered in surfaces.items():
                if expected_stale:
                    stale_n += 1
                    if delivered != "STALE":
                        leaks += 1
                        lines.append(f"  LEAK   {label}/{surface}: broken "
                                     f"evidence delivered as "
                                     f"{delivered or 'no verdict'}")
                else:
                    current_n += 1
                    if delivered != "CURRENT":
                        false_alarms += 1
                        lines.append(f"  NOISE  {label}/{surface}: satisfied "
                                     f"evidence delivered as "
                                     f"{delivered or 'no verdict'}")
        # Bucket 2: the read budget.
        for label, pattern, create, mutate in _past_bound_cases():
            surfaces = _run_trial(tmp, label, pattern=pattern,
                                  create=create, mutate=mutate)
            for surface, delivered in surfaces.items():
                unbounded_n += 1
                if delivered != "UNKNOWN":
                    unbounded += 1
                    lines.append(f"  UNBOUND {label}/{surface}: past-bound "
                                 f"artifact answered as "
                                 f"{delivered or 'no verdict'}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if not stale_n or not current_n or not unbounded_n:
        raise RuntimeError("fixture produced no trials — build broken")
    return (leaks, stale_n, false_alarms, current_n, unbounded, unbounded_n, lines)


def main() -> int:
    global BASELINE
    BASELINE = "--baseline" in sys.argv[1:]
    if _SHIPPED_BYTES is not None and _SHIPPED_BYTES > _DECLARED_BYTES:
        print(f"BENCH ERROR: the shipped byte bound ({_SHIPPED_BYTES}) is wider "
              f"than this bench declares ({_DECLARED_BYTES}) — widen the "
              f"fixture, not the verdict", file=sys.stderr)
        return 1
    try:
        (leaks, stale_n, false_alarms, current_n, unbounded, unbounded_n,
         lines) = measure()
    except Exception as exc:  # a bench that cannot run must not read as a pass
        print(f"BENCH ERROR: {exc}", file=sys.stderr)
        return 1
    for line in lines:
        print(line)
    print(f"trials: stale={stale_n} current={current_n} "
          f"unbounded={unbounded_n} surfaces=status+next+list"
          + (" mode=baseline(pre-E-058 delivery)" if BASELINE else ""))
    print(f"stale_evidence_rate={leaks / stale_n:.4f} ({leaks}/{stale_n})")
    print(f"unbounded_read_rate={unbounded / unbounded_n:.4f} "
          f"({unbounded}/{unbounded_n})")
    print(f"false_alarm_rate={false_alarms / current_n:.4f} "
          f"({false_alarms}/{current_n})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

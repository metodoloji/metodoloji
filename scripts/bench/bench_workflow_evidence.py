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

The read is also scored for WHERE it reads, because a run's evidence is a claim
about ITS OWN project and nothing confines the path to it:

    escape_read_rate     ⇔ an artifact declared OUTSIDE the trial's project root
                           (`../…`, `docs/../../…`, an absolute path) was
                           answered with anything but UNKNOWN. A surface that
                           answers CURRENT read the outside file and turned it
                           into a verdict; a surface that answers STALE read it
                           and turned it into a definite negative; a surface
                           with no verdict (the baseline) delivered the stage as
                           satisfied without ever asking. All three are the same
                           defect: the plane reads a file the run does not own.
                           The outside file carries the token, so the honest
                           answer is UNKNOWN — "cannot be established", never
                           "satisfied" and never "no".
    escape_accepted_rate ⇔ the COMPLETION gate accepted a stage whose declared
                           artifact path escapes the root — the write that
                           records such a witness must be refused, and the
                           refusal must name the cause.

And the STORAGE plane is scored, because a run's own state and definition are
files too, and both are named by strings the operator supplies (a slug) or the
state carries (its `spec_file`):

    storage_escape_rate  ⇔ the run's SPEC was read from OUTSIDE the project root.
                           Two spellings: a slug that walks out of the specs dir
                           (`../../../../loaded`, resolved against the project
                           root the bench plants a spec beside), and a state
                           whose stored `spec_file` points outside. The outside
                           spec declares the SAME stage id with a DIFFERENT
                           predicate and a DIFFERENT next stage, so the leak is
                           observable on all three surfaces: the peek shows the
                           outside stage's title, the computed next step names
                           the outside-only stage, and the inventory classifies
                           the recorded completion under the outside predicate
                           (STALE, where the run's own spec says CURRENT).
    storage_control_rate ⇔ a legitimate run stopped resolving its OWN spec (a
                           false refusal — the failure mode of a containment
                           applied too eagerly). Must be 0.0 on BOTH trees: with
                           an in-root spec and a matching slug, all three
                           surfaces deliver the run's own shape.

Trial verdicts:

    stale_evidence_rate = a completed stage delivered as satisfied while the
                          oracle says its evidence no longer holds  (the defect)
    unbounded_read_rate = a past-bound artifact answered from a full read
                                                                    (the defect)
    escape_read_rate    = an out-of-root artifact answered from a read
                                                                    (the defect)
    escape_accepted_rate= a stage completed on an out-of-root artifact
                                                                    (the defect)
    storage_escape_rate = a run's spec read from outside the project root
                                                                    (the defect)
    false_alarm_rate    = a satisfied stage delivered as anything but CURRENT
                          (noise; also fails a "mark everything UNKNOWN" non-fix)

Baseline (before the fix) `status`/`next` carry the verdict but the inventory
surface carries none at all, a past-bound artifact is read whole, and an
out-of-root artifact is read and accepted — so every defect rate is non-zero.
`--baseline` reproduces the pre-fix delivery semantics (a surface that carries
no verdict: every completed stage reads as satisfied, and an out-of-root path is
accepted) so the recorded red stays reproducible on a fixed tree; it is also
this bench's own negative test. The fixed tree is measured through the real
surfaces, not through the simulation — `--baseline` only stands in for behaviour
that no longer exists to call.

Self-contained: a throwaway project per trial, stdlib only, no network, no
dependency on this checkout or the gate key.
"""

from __future__ import annotations

import itertools
import json
import os
import pathlib
import shutil
import sys
import tempfile

_PLUGIN = pathlib.Path(__file__).resolve().parents[2]
if str(_PLUGIN) not in sys.path:
    sys.path.insert(0, str(_PLUGIN))

from bmad.workflow import (engine, evidence as evidence_mod,  # noqa: E402
                           store as store_mod)

# Pre-fix delivery semantics: the read surfaces carried no evidence dimension,
# so a completed stage could only arrive as satisfied. See the module docstring.
BASELINE = False

_ARTIFACT = "logs/analysis.md"
_OUTSIDE = "outside.md"  # written BESIDE the trial's project root, never inside
_OUTSIDE_SPEC = "loaded.json"  # likewise: a SPEC file the run does not own

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


def _escape_cases() -> list[tuple[str, object]]:
    """(label, declared-path-of-root) — the artifact is genuinely OUTSIDE root.

    Every fixture names a file the run does not own, and that file carries the
    declared token: so `CURRENT` means the plane read it, `STALE` means it read
    it and drew a definite negative, and "no verdict" means it never asked. The
    honest answer is UNKNOWN in all three shapes — outside the root, nothing can
    be established. Three spellings on purpose: the traversal, the traversal
    buried in the middle (which only a normalising check catches), and the
    absolute path.
    """
    return [
        ("dotdot-sibling", lambda r: f"../{_OUTSIDE}"),
        ("dotdot-middle", lambda r: f"docs/../../{_OUTSIDE}"),
        ("absolute-path", lambda r: str(r.parent / _OUTSIDE)),
    ]


# --- the storage plane: which SPEC a run is read against -----------------------
# The outside spec declares the SAME stage ids as the owned one, with a different
# title, a different next stage and — for the stage the run completed without a
# witness — a different evidence KIND. Reading it is therefore visible on every
# read surface, and reading it must never happen:
#
#   status ⇔ the current stage's title comes from the spec        (MAP / OUTSIDE MAP)
#   next   ⇔ the computed next stage's id comes from the spec      (done / outside-done)
#   list   ⇔ the completed-but-unwitnessed stage is classified only when the
#            CURRENT spec calls it an artifact stage               (1 / 2 entries)
_OUTSIDE_TITLE = "OUTSIDE MAP"
_OUTSIDE_NEXT = "outside-done"
_OWNED_TITLE = "MAP"
_OWNED_NEXT = "done"
# analyze carries a witness; brief completed as a `note` stage (no witness), so
# the owned spec classifies exactly one entry.
_OWNED_EVIDENCE_TOTAL = 1


def _owned_spec() -> dict:
    return {
        "id": "demo",
        "title": "owned spec",
        "start": "analyze",
        "stages": [
            {"id": "analyze", "title": "Analyze",
             "evidence": {"kind": "artifact", "path": _ARTIFACT,
                          "contains": ["FINDING"], "min": 1},
             "next": [{"to": "brief"}]},
            {"id": "brief", "title": "Brief",
             "evidence": {"kind": "note"},
             "next": [{"to": "map"}]},
            {"id": "map", "title": _OWNED_TITLE,
             "evidence": {"kind": "note"},
             "next": [{"to": _OWNED_NEXT}]},
            {"id": _OWNED_NEXT, "title": "Done", "terminal": True},
        ],
    }


def _outside_spec() -> dict:
    return {
        "id": "outside",
        "title": "outside spec",
        "start": "analyze",
        "stages": [
            {"id": "analyze", "title": "Analyze",
             "evidence": {"kind": "artifact", "path": _ARTIFACT,
                          "contains": ["OUTSIDE-TOKEN"], "min": 1},
             "next": [{"to": "brief"}]},
            {"id": "brief", "title": "Brief",
             "evidence": {"kind": "artifact", "path": _ARTIFACT,
                          "contains": ["OUTSIDE-TOKEN"], "min": 1},
             "next": [{"to": "map"}]},
            {"id": "map", "title": _OUTSIDE_TITLE,
             "evidence": {"kind": "note"},
             "next": [{"to": _OUTSIDE_NEXT}]},
            {"id": _OUTSIDE_NEXT, "title": "Outside done", "terminal": True},
        ],
    }


def _outside_slug(root: pathlib.Path) -> str:
    """Walk out of the specs dir and out of the project root in one string.

    The specs dir sits at <root>/.metodoloji/workflow/specs, so four levels up is
    the directory the outside spec is planted in — the bench's own temp dir, which
    is not the run's project.
    """
    return "../../../../loaded"


def _storage_cases() -> list[tuple[str, str]]:
    """(label, kind) — how the run is made to name a spec it does not own."""
    return [("slug-traversal", "slug"), ("stored-spec-file", "state")]


def _prepare_storage_run(tmp: pathlib.Path, name: str, kind: str):
    """A completed run + an outside spec planted beside the project root.

    Returns ``(root, slug, outside_path)``. The trial isolates SPEC RESOLUTION
    from completion: the run is completed against its OWN spec first, and the
    state that the addressed slug reads is that legitimate shape — so the
    artifact, the recorded witness and the completed stage are identical on both
    trees and only the question "which spec does this run resolve to?" differs.

    Two addressings, both reachable without any hand-editing inside the project:
    a slug that walks out of the specs dir, and a state whose stored `spec_file`
    names a file outside the root (what an older tree or a hand edit leaves).
    """
    root = tmp / name
    root.mkdir(parents=True)
    outside = tmp / _OUTSIDE_SPEC
    outside.write_text(json.dumps(_outside_spec()), encoding="utf-8")
    # "state" is addressed by a slug that has no spec file of its own, so the
    # stored spec_file is the only thing left to resolve against.
    slug = "orphan" if kind == "state" else "demo"
    res = engine.create(str(root), _owned_spec(), slug=slug)
    if not res.get("ok"):
        raise RuntimeError(f"{name}: create failed: {res}")
    _write(root, _ARTIFACT, "# analysis\nFINDING 1\n")
    for stage, kwargs in (("analyze", {}), ("brief", {"note": "brief produced"})):
        done = engine.complete(str(root), slug, stage, **kwargs)
        if not done.get("ok"):
            raise RuntimeError(f"{name}: complete '{stage}' refused: {done}")
    legit = pathlib.Path(store_mod.state_path(str(root), slug))
    state = json.loads(legit.read_text(encoding="utf-8"))
    if kind == "slug":
        crafted = _outside_slug(root)
        state["slug"] = crafted
        pathlib.Path(store_mod.state_path(str(root), crafted)).write_text(
            json.dumps(state), encoding="utf-8")
        return root, crafted, outside
    state["spec_file"] = str(outside)
    legit.write_text(json.dumps(state), encoding="utf-8")
    return root, slug, outside


def _row_for(root: str, slug: str) -> dict:
    """The inventory row for a run, addressed by its raw or sanitized name.

    `list_runs` derives slugs from the FILES it finds, so a run addressed by a
    crafted slug is listed under the sanitized spelling — a lookup by the raw
    string would silently score an absent row as a difference.
    """
    rows = {r.get("slug"): r for r in engine.list_runs(root).get("runs", [])}
    return rows.get(slug) or rows.get(store_mod.safe_slug(slug)) or {}


def _delivered_outside(root: str, slug: str) -> dict[str, bool]:
    """True per surface when that surface delivered the OUTSIDE spec's shape.

    Measured, never simulated: `--baseline` restores the pre-fix spec loader (see
    :func:`_install_prefix_loader`) instead of modelling what it would have
    delivered, so the red this bucket reproduces is the red the unfixed kernel
    produced.
    """
    status = engine.status(root, slug)
    nxt = engine.next_stage(root, slug)
    row = _row_for(root, slug)
    return {
        # the peek renders the current stage the SPEC declares
        "status": ((status.get("stage") or {}).get("title") == _OUTSIDE_TITLE),
        # the computed next step names the outside-only stage
        "next": ((nxt.get("after") or {}).get("stage") == _OUTSIDE_NEXT),
        # the inventory classifies the unwitnessed completion only when the spec
        # in force calls it an artifact stage
        "list": ((row.get("evidence_health") or {}).get("total")
                 != _OWNED_EVIDENCE_TOTAL),
    }


def _run_storage_control(tmp: pathlib.Path, name: str = "storage-control") -> dict[str, bool]:
    """A legitimate run must still resolve its OWN spec (no false refusal)."""
    root = tmp / name
    root.mkdir(parents=True)
    (tmp / _OUTSIDE_SPEC).write_text(json.dumps(_outside_spec()), encoding="utf-8")
    res = engine.create(str(root), _owned_spec(), slug="demo")
    if not res.get("ok"):
        raise RuntimeError(f"{name}: create failed: {res}")
    _write(root, _ARTIFACT, "# analysis\nFINDING 1\n")
    for stage, kwargs in (("analyze", {}), ("brief", {"note": "brief produced"})):
        if not engine.complete(str(root), "demo", stage, **kwargs).get("ok"):
            raise RuntimeError(f"{name}: complete '{stage}' refused")
    status = engine.status(str(root), "demo")
    nxt = engine.next_stage(str(root), "demo")
    row = _row_for(str(root), "demo")
    # A FAILURE is any surface that stopped delivering the run's own shape.
    return {
        "status": ((status.get("stage") or {}).get("title") != _OWNED_TITLE),
        "next": ((nxt.get("after") or {}).get("stage") != _OWNED_NEXT),
        "list": ((row.get("evidence_health") or {}).get("total")
                 != _OWNED_EVIDENCE_TOTAL),
    }


def _past_candidate_cap(root: pathlib.Path) -> None:
    """Bury the satisfying candidate under more matches than the scan bound.

    The fillers are written AFTER it, so mtime-descending order reaches them
    first and the scan cannot get back to the artifact inside the bound.
    """
    for i in range(_PLANTED_CANDIDATES):
        _write(root, f"logs/filler-{i:04d}.md", "# filler\n")


def _run_escape_trial(tmp: pathlib.Path, name: str, declared) -> dict[str, str | None]:
    """Complete on an out-of-root artifact (forced), then probe the surfaces.

    `force=True` is what makes the fixture reachable on BOTH trees: the run's
    state must NAME the outside artifact for the read surfaces to be probed, and
    the fixed completion gate refuses to record that witness on its own. The
    shape is not artificial — it is exactly what a pre-fix tree, a hand-edited
    state or an older run leaves behind, which is why the read side needs its own
    containment rather than relying on the write side.
    """
    root = tmp / name
    root.mkdir(parents=True)
    (tmp / _OUTSIDE).write_text("# outside\nFINDING 1\n", encoding="utf-8")
    pattern = declared(root)
    spec = _spec(pattern)
    created = engine.create(str(root), spec)
    if not created.get("ok"):
        raise RuntimeError(f"{name}: create failed: {created}")
    done = engine.complete(str(root), spec["id"], "analyze", force=True)
    if not done.get("ok"):
        raise RuntimeError(f"{name}: forced complete refused: {done}")
    return _verdicts(str(root), spec["id"])


def _run_escape_gate_trial(tmp: pathlib.Path, name: str, declared) -> bool:
    """Does the completion gate accept a stage whose artifact escapes the root?"""
    if BASELINE:
        # Pre-fix semantics: there was no containment, so the read succeeded and
        # the stage completed. Simulated — no such code path remains to call.
        return True
    root = tmp / name
    root.mkdir(parents=True)
    (tmp / _OUTSIDE).write_text("# outside\nFINDING 1\n", encoding="utf-8")
    spec = _spec(declared(root))
    created = engine.create(str(root), spec)
    if not created.get("ok"):
        raise RuntimeError(f"{name}: create failed: {created}")
    return bool(engine.complete(str(root), spec["id"], "analyze").get("ok"))


def measure() -> dict:
    """Run every trial. Returns counts, denominators and report lines."""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="bench-workflow-evidence-"))
    lines: list[str] = []
    leaks = stale_n = false_alarms = current_n = 0
    unbounded = unbounded_n = escape = escape_n = accepted = accepted_n = 0
    storage = storage_n = control = control_n = 0
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
        # Bucket 3: the read must not leave the project root.
        for label, declared in _escape_cases():
            surfaces = _run_escape_trial(tmp, f"escape-{label}", declared)
            for surface, delivered in surfaces.items():
                escape_n += 1
                if delivered != "UNKNOWN":
                    escape += 1
                    lines.append(f"  ESCAPE {label}/{surface}: out-of-root "
                                 f"artifact answered as "
                                 f"{delivered or 'no verdict'}")
            accepted_n += 1
            if _run_escape_gate_trial(tmp, f"gate-{label}", declared):
                accepted += 1
                lines.append(f"  ESCAPE {label}/complete: the gate accepted an "
                             f"out-of-root artifact")
        # Bucket 4: the storage plane — which spec the run is read against.
        for label, kind in _storage_cases():
            root, slug, _outside = _prepare_storage_run(tmp, f"storage-{label}", kind)
            for surface, leaked in _delivered_outside(str(root), slug).items():
                storage_n += 1
                if leaked:
                    storage += 1
                    lines.append(f"  STORAGE {label}/{surface}: the run's spec was "
                                 f"read from outside the project root")
        for surface, refused in _run_storage_control(tmp).items():
            control_n += 1
            if refused:
                control += 1
                lines.append(f"  CONTROL storage-control/{surface}: a legitimate "
                             f"run stopped resolving its own spec")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if (not stale_n or not current_n or not unbounded_n or not escape_n
            or not storage_n or not control_n):
        raise RuntimeError("fixture produced no trials — build broken")
    return {"leaks": leaks, "stale_n": stale_n,
            "false_alarms": false_alarms, "current_n": current_n,
            "unbounded": unbounded, "unbounded_n": unbounded_n,
            "escape": escape, "escape_n": escape_n,
            "accepted": accepted, "accepted_n": accepted_n,
            "storage": storage, "storage_n": storage_n,
            "control": control, "control_n": control_n,
            "lines": lines}


def _install_prefix_loader() -> None:
    """Restore the pre-fix spec resolution (E-061) for `--baseline`.

    `load_spec` joined the RAW slug into the specs dir and opened the state's
    `spec_file` wherever it pointed. Both are restored verbatim, so the storage
    bucket's red is reproduced by running the old resolution — not by asserting
    what it would have returned. (The delivery and gate buckets still model
    pre-fix delivery semantics; no code path exists to call there.)
    """
    def load_spec(project_root: str, slug: str) -> dict:
        base = store_mod.paths(project_root)["specs"]
        for candidate in (os.path.join(base, f"{slug}.json"),):
            try:
                with open(candidate, encoding="utf-8") as fh:
                    return json.load(fh)
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                continue
        state = store_mod.read_state(project_root, slug)
        spec_file = state.get("spec_file")
        if spec_file:
            try:
                with open(spec_file, encoding="utf-8") as fh:
                    return json.load(fh)
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                pass
        return {}
    store_mod.load_spec = load_spec


def main() -> int:
    global BASELINE
    BASELINE = "--baseline" in sys.argv[1:]
    if BASELINE:
        _install_prefix_loader()
    if _SHIPPED_BYTES is not None and _SHIPPED_BYTES > _DECLARED_BYTES:
        print(f"BENCH ERROR: the shipped byte bound ({_SHIPPED_BYTES}) is wider "
              f"than this bench declares ({_DECLARED_BYTES}) — widen the "
              f"fixture, not the verdict", file=sys.stderr)
        return 1
    try:
        r = measure()
    except Exception as exc:  # a bench that cannot run must not read as a pass
        print(f"BENCH ERROR: {exc}", file=sys.stderr)
        return 1
    for line in r["lines"]:
        print(line)
    print(f"trials: stale={r['stale_n']} current={r['current_n']} "
          f"unbounded={r['unbounded_n']} escape={r['escape_n']} "
          f"gate={r['accepted_n']} storage={r['storage_n']} "
          f"storage_control={r['control_n']} surfaces=status+next+list"
          + (" mode=baseline(pre-fix delivery)" if BASELINE else ""))
    print(f"stale_evidence_rate={r['leaks'] / r['stale_n']:.4f} "
          f"({r['leaks']}/{r['stale_n']})")
    print(f"unbounded_read_rate={r['unbounded'] / r['unbounded_n']:.4f} "
          f"({r['unbounded']}/{r['unbounded_n']})")
    print(f"escape_read_rate={r['escape'] / r['escape_n']:.4f} "
          f"({r['escape']}/{r['escape_n']})")
    print(f"escape_accepted_rate={r['accepted'] / r['accepted_n']:.4f} "
          f"({r['accepted']}/{r['accepted_n']})")
    print(f"storage_escape_rate={r['storage'] / r['storage_n']:.4f} "
          f"({r['storage']}/{r['storage_n']})")
    print(f"storage_control_rate={r['control'] / r['control_n']:.4f} "
          f"({r['control']}/{r['control_n']})")
    print(f"false_alarm_rate={r['false_alarms'] / r['current_n']:.4f} "
          f"({r['false_alarms']}/{r['current_n']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

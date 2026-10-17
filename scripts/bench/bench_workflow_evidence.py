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

And COMMAND evidence is scored, because a stage whose proof is "this argv exited
0" is the one claim in the plane that cannot be re-derived by looking — and the
plane used to say nothing about it at all:

    command_silence_rate ⇔ a completed `command` stage whose TRACKED CODE STATE has
                           moved since the command ran was delivered without a
                           verdict (or as CURRENT). Nothing distinguishes "the
                           suite passed against this code" from "the suite
                           passed forty commits ago" unless the completion is
                           dated, so the trial changes the tracked tree (a new
                           commit in one fixture, a dirty edit of a tracked file
                           in the other) and demands the honest UNKNOWN with the
                           reason. The fixture's own project is a git work tree,
                           which is the code-state oracle the board plane already
                           dates its decisions to (E-053/E-054).
    command_hold_rate    ⇔ the paired control: a command completion whose tracked
                           code state is UNCHANGED must answer CURRENT — it fails
                           a "mark every command UNKNOWN" non-fix, which would
                           make the verdict worthless. (Baseline 3/3: the
                           unfixed plane answers nothing at all, so the control
                           fails there too, by silence.)

And the SPEC's own declared `artifact` field is scored, because it is the one
pointer the session edge repeats to the agent — "tracked in X" — and a spec can
name a file the run does not own:

    artifact_field_escape_rate   ⇔ an out-of-root `artifact` was DELIVERED as the
                                   run's tracking location: `status.artifact`
                                   resolved outside the root, or the session
                                   advisory said "tracked in <path>". The agent
                                   obeys the line, so the plane must not hand
                                   over a path it does not own.
    artifact_field_accepted_rate ⇔ `create` accepted a spec whose declared
                                   `artifact` resolves outside the root. The
                                   write that plants such a pointer must be
                                   refused, naming the cause.
    artifact_field_control_rate  ⇔ the paired control: an in-root `artifact` is
                                   still delivered verbatim by both surfaces.
                                   Must be 0.0 on BOTH trees — a containment
                                   applied too eagerly would stop naming the
                                   run's own durable file.

The fixed tree is measured through REAL code: the run is created from a spec
with no `artifact`, and the SAVED copy is then given one — precisely what an
older tree, a hand edit, or a spec authored elsewhere leaves behind, so the read
side must handle that shape whether or not the write side now refuses it.
`--baseline` MODELS the pre-fix delivery for this bucket (the recorded 1.0/1.0
was measured on the unfixed tree; no unfixed path remains to call), like the
delivery and gate buckets and unlike the storage bucket, which restores the old
loader and runs it.

And the NAME question is scored, because the CLI answers "which run did the
operator mean?" by DERIVING EVIDENCE for every run:

    slug_resolve_read_rate    ⇔ resolving a slug (or refusing to resolve an
                                ambiguous one) derived at least one evidence
                                verdict. A name question is answered by the
                                FILENAMES the store already lists; classifying
                                every run to answer it puts the whole evidence
                                plane (bounded artifact reads, one code-state
                                probe) behind a lookup, and then classifies the
                                answered run a second time when `status` runs.
    slug_resolve_control_rate ⇔ the resolution returned the WRONG answer (one
                                run must resolve to it; two or more must stay
                                ambiguous; none must stay unresolved). Must be
                                0.0 on BOTH trees: a resolver that got cheap by
                                answering "no single run" would be useless.

The read counter is a WRAPPER around the plane's one matcher
(`artifact_matches`), installed for the duration of a trial: the expectation is
arithmetic (a name question needs ZERO artifact reads), not something the
implementation defines. `--baseline` installs the pre-fix resolver verbatim, so
both trees run real code.

And a FORCED completion is scored, because `--force` exists for work the
environment cannot prove — and the plane recorded it as if it had:

    forced_claim_rate   ⇔ a completion accepted with `--force` delivered a verdict
                          about evidence that was never satisfied. Two shapes,
                          both real: an `artifact` stage whose token is missing
                          reads STALE (the plane blames the workspace and, worse,
                          blocks the next advance on its own unverified claim),
                          and a `command` stage whose argv exits 3 when rc 0 was
                          declared reads CURRENT (the plane vouches for a run
                          that never passed). The honest answer is UNKNOWN naming
                          the force: nothing was established, so nothing can be
                          re-derived.
    forced_control_rate ⇔ the paired control: a GENUINE completion still answers
                          CURRENT on every surface, and the same unsatisfied
                          evidence is still REFUSED without `--force`. Must be 0.0
                          on BOTH trees — a fix that turned every completion into
                          UNKNOWN would be worthless.

`--baseline` strips the forced marker from the recorded witness, because that is
exactly the state the unfixed tree wrote; the surfaces then classify it with real
code, so the recorded red is not a model of a delivery, it is the old record.

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
    command_silence_rate= a command completion at a moved code state delivered
                          with no verdict, or as CURRENT            (the defect)
    command_hold_rate   = a command completion at an unchanged code state NOT
                          delivered as CURRENT                      (control)
    artifact_field_escape_rate = an out-of-root declared `artifact` delivered as
                          the run's tracking location                 (the defect)
    artifact_field_accepted_rate = `create` accepting an out-of-root declared
                          `artifact`                                 (the defect)
    artifact_field_control_rate = an in-root declared `artifact` NOT delivered
                          as the run's tracking location             (control)
    slug_resolve_read_rate = a slug resolution that derived an evidence verdict
                                                                    (the defect)
    slug_resolve_control_rate = a slug resolution that answered wrongly
                                                                      (control)
    forced_claim_rate = a `--force` completion delivered as CURRENT or STALE
                                                                    (the defect)
    forced_control_rate = a genuine completion not delivered as CURRENT, or an
                          unforced refusal that was accepted               (control)
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
import subprocess
import sys
import tempfile

_PLUGIN = pathlib.Path(__file__).resolve().parents[2]
if str(_PLUGIN) not in sys.path:
    sys.path.insert(0, str(_PLUGIN))

from bmad.workflow import (advisory as advisory_mod, engine,  # noqa: E402
                           evidence as evidence_mod, store as store_mod)

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


# --- the command bucket: a result dated to the code state it was run against --
# "this argv exited 0" cannot be re-derived by looking, so the only useful honest
# answer is a comparison: is the TRACKED code state still the one the command was
# recorded against? A `dirty` move edits a tracked file without committing, so the
# two fixtures cover both halves of the fingerprint (the commit and the dirty set).

def _git(root: pathlib.Path, *args: str) -> None:
    proc = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          stdin=subprocess.DEVNULL, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: "
                           f"{(proc.stderr or proc.stdout).strip()[:160]}")


def _commit(root: pathlib.Path, message: str) -> None:
    ident = ["-c", "user.email=bench@example.invalid", "-c", "user.name=bench"]
    _git(root, *ident, "add", "-A")
    _git(root, *ident, "commit", "-q", "-m", message)


def _command_spec() -> dict:
    return {
        "id": "cmd-demo",
        "title": "command evidence demo",
        "start": "check",
        "stages": [
            {"id": "check", "title": "Run the suite",
             "evidence": {"kind": "command",
                          "argv": [sys.executable, "-c", "print('ok')"], "rc": 0},
             "next": [{"to": "ship"}]},
            {"id": "ship", "title": "Ship",
             "evidence": {"kind": "note"}, "next": [{"to": "done"}]},
            {"id": "done", "title": "Done", "terminal": True},
        ],
    }


def _command_cases() -> list[str]:
    """How the tracked code state moves after the command is recorded."""
    return ["committed-change", "dirty-edit"]


def _prepare_command_run(tmp: pathlib.Path, name: str, movement: str):
    """A git work tree whose `check` stage completed on a command, then moved."""
    root = tmp / name
    root.mkdir(parents=True)
    _write(root, "app.py", "VALUE = 1\n")
    _git(root, "init", "-q")
    _commit(root, "init")
    spec = _command_spec()
    created = engine.create(str(root), spec)
    if not created.get("ok"):
        raise RuntimeError(f"{name}: create failed: {created}")
    done = engine.complete(str(root), spec["id"], "check")
    if not done.get("ok"):
        raise RuntimeError(f"{name}: complete refused: {done}")
    _write(root, "app.py", "VALUE = 2  # the suite would run against this now\n")
    if movement == "committed-change":
        _commit(root, "move the code on")
    return root, spec["id"]


def _command_verdicts(root: str, slug: str, stage: str = "check") -> dict[str, str | None]:
    """The verdict each surface delivered for the completed command stage."""
    if BASELINE:
        # Pre-fix semantics: `verify` SKIPPED command stages entirely, so every
        # surface delivered the completion with no verdict dimension at all.
        return {name: None for name in ("status", "next", "list")}
    status = engine.status(root, slug)
    nxt = engine.next_stage(root, slug)
    row = _row_for(root, slug)

    def _of(result: dict) -> str | None:
        entry = (result.get("evidence") or {}).get(stage) or {}
        verdict = entry.get("verdict")
        return str(verdict).upper() if verdict else None

    return {"status": _of(status), "next": _of(nxt), "list": _of(row)}


def _run_command_control(tmp: pathlib.Path, name: str = "command-control"):
    """A command completion whose tracked code state did NOT move."""
    root = tmp / name
    root.mkdir(parents=True)
    _write(root, "app.py", "VALUE = 1\n")
    _git(root, "init", "-q")
    _commit(root, "init")
    spec = _command_spec()
    engine.create(str(root), spec)
    if not engine.complete(str(root), spec["id"], "check").get("ok"):
        raise RuntimeError(f"{name}: complete refused")
    return str(root), spec["id"]


# --- the declared-artifact bucket: the pointer the session edge repeats --------
# A spec's top-level `artifact` is its durable source of truth: the session
# advisory tells the agent "tracked in <it>". A pointer outside the project is
# therefore not a style question — it is a location the agent will read and
# write, named by a file the run does not own.
_DECLARED_ARTIFACT = "docs/notes.md"  # inside the run's project (the control)


def _artifact_field_cases() -> list[tuple[str, object]]:
    """(label, declared) — the spec's `artifact` names a file the run does not own."""
    return [
        ("dotdot-sibling", lambda r: f"../{_OUTSIDE}"),
        ("dotdot-middle", lambda r: f"docs/../../{_OUTSIDE}"),
        ("absolute-path", lambda r: str(r.parent / _OUTSIDE)),
    ]


def _artifact_field_spec(declared: str | None) -> dict:
    spec = _spec()
    spec["id"] = "artifact-field-demo"
    if declared is not None:
        spec["artifact"] = declared
    return spec


def _prepare_artifact_field_run(tmp: pathlib.Path, name: str, declared: str):
    """A run whose SAVED spec copy declares an out-of-root `artifact`.

    The copy is edited AFTER `create` on purpose: that is the shape an older
    tree, a hand edit or a spec authored outside this kernel leaves behind, and
    the read surfaces must handle it whether or not the write side refuses it
    today. Both trees are therefore measured through real code, no simulation.
    """
    root = tmp / name
    root.mkdir(parents=True)
    (tmp / _OUTSIDE).write_text("# outside\n", encoding="utf-8")
    spec = _artifact_field_spec(None)
    if not engine.create(str(root), spec).get("ok"):
        raise RuntimeError(f"{name}: create failed")
    _write(root, _ARTIFACT, "# analysis\nFINDING 1\n")
    if not engine.complete(str(root), spec["id"], "analyze").get("ok"):
        raise RuntimeError(f"{name}: complete refused")
    copy = pathlib.Path(store_mod.paths(str(root))["specs"]) / f"{spec['id']}.json"
    data = json.loads(copy.read_text(encoding="utf-8"))
    data["artifact"] = declared
    copy.write_text(json.dumps(data), encoding="utf-8")
    return str(root), spec["id"]


def _resolves_outside(root: str, path: str) -> bool:
    """Lexical+real answer, computed WITHOUT the module under test."""
    base = pathlib.Path(root).resolve()
    probe = pathlib.Path(path)
    target = probe if probe.is_absolute() else (pathlib.Path(root) / probe)
    try:
        target = target.resolve()
    except OSError:
        return True
    return target != base and base not in target.parents


def _artifact_field_delivered(root: str, slug: str, declared: str) -> dict[str, bool]:
    """True per surface when the pointer was handed over as the run's own."""
    if BASELINE:
        # Pre-fix delivery: both surfaces repeated the pointer verbatim, because
        # nothing asked where it resolved. Modelled — the recorded baseline (1.0)
        # was measured on the unfixed tree, and no unfixed path remains to call.
        return {"status": True, "advisory": True}
    status = engine.status(root, slug)
    field = status.get("artifact")
    line = advisory_mod.peek(root)
    return {
        # the peek hands the agent the path as `artifact`
        "status": bool(field) and _resolves_outside(root, str(field)),
        # the session edge says "tracked in <that path>"
        "advisory": f"tracked in {declared}" in line,
    }


def _run_artifact_field_gate_trial(tmp: pathlib.Path, name: str, declared: str) -> bool:
    """Does `create` accept a spec whose declared `artifact` escapes the root?"""
    if BASELINE:
        # Pre-fix: there was no gate check at all, so the spec was accepted.
        return True
    root = tmp / name
    root.mkdir(parents=True)
    spec = _artifact_field_spec(declared)
    return bool(engine.create(str(root), spec).get("ok"))


def _run_artifact_field_control(tmp: pathlib.Path, name: str = "artifact-field-control"):
    """An in-root `artifact` must still be delivered verbatim (no over-refusal)."""
    root = tmp / name
    root.mkdir(parents=True)
    spec = _artifact_field_spec(_DECLARED_ARTIFACT)
    if not engine.create(str(root), spec).get("ok"):
        raise RuntimeError(f"{name}: create refused a legitimate spec")
    _write(root, _ARTIFACT, "# analysis\nFINDING 1\n")
    if not engine.complete(str(root), spec["id"], "analyze").get("ok"):
        raise RuntimeError(f"{name}: complete refused")
    status = engine.status(str(root), spec["id"])
    line = advisory_mod.peek(str(root))
    return {
        "status": status.get("artifact") != _DECLARED_ARTIFACT,
        "advisory": f"tracked in {_DECLARED_ARTIFACT}" not in line,
    }


# --- the name question: does resolving a slug derive evidence? ------------------
# The CLI's "no --slug given" path asks WHICH run, and the only thing it needs is
# a filename. The pre-fix resolver went through the inventory, which derives a
# verdict for every run (bounded artifact reads plus a shared code-state probe) —
# and then `status` classified the answered run again.
_REAL_MATCHES = evidence_mod.artifact_matches
_READS = {"n": 0}


def _counted_matches(*args, **kwargs):
    """Count artifact matches actually performed (the plane's one choke point)."""
    _READS["n"] += 1
    return _REAL_MATCHES(*args, **kwargs)


def _legacy_resolve(project_root: str, slug: str | None = None) -> str | None:
    """The pre-fix resolver verbatim (E-068): classify the inventory, take a row."""
    if slug:
        return slug
    runs = engine.list_runs(project_root).get("runs", [])
    return runs[0]["slug"] if len(runs) == 1 else None


def _install_prefix_resolver() -> None:
    """`--baseline` runs the OLD resolution, not a model of it."""
    engine.resolve_slug = _legacy_resolve


def _resolver():
    """The resolver the CLI calls: the fixed one when it ships, else the legacy."""
    return getattr(engine, "resolve_slug", _legacy_resolve)


def _slug_resolve_cases() -> list[tuple[str, list[str]]]:
    """(label, slugs) — one run (resolves) and three (must stay ambiguous)."""
    return [("single-run", ["r1"]), ("ambiguous", ["r1", "r2", "r3"])]


def _prepare_slug_project(tmp: pathlib.Path, name: str, slugs: list[str]):
    """A project whose every run has a completed ARTIFACT stage to classify."""
    root = tmp / name
    root.mkdir(parents=True)
    _write(root, _ARTIFACT, "# analysis\nFINDING 1\n")
    spec = _spec()
    for slug in slugs:
        if not engine.create(str(root), spec, slug=slug).get("ok"):
            raise RuntimeError(f"{name}: create '{slug}' failed")
        done = engine.complete(str(root), slug, "analyze")
        if not done.get("ok"):
            raise RuntimeError(f"{name}: complete '{slug}' refused: {done}")
    return str(root)


def _slug_resolve_trial(tmp: pathlib.Path, label: str, slugs: list[str]) -> dict[str, object]:
    """Reads performed by the RESOLUTION alone, and whether it answered right."""
    root = _prepare_slug_project(tmp, f"slug-{label}", slugs)
    expected = slugs[0] if len(slugs) == 1 else None
    _READS["n"] = 0
    chosen = _resolver()(root)
    reads = _READS["n"]
    _READS["n"] = 0
    # What the CLI then does with the answer: one run's worth of classification.
    if chosen:
        engine.status(root, chosen)
    status_reads = _READS["n"]
    return {"reads": reads, "status_reads": status_reads,
            "wrong": (chosen != expected),
            "chosen": chosen, "expected": expected}


# --- forced completions: the claim nobody established (E-070) -------------------
# `--force` is the kernel's documented escape hatch for evidence the environment
# genuinely cannot produce. What it recorded, though, was a witness identical to
# a satisfied one — so the plane later either blamed the workspace (STALE, and
# blocked the next advance on it) or vouched for a command that never ran
# (CURRENT). A forced completion must say that it was forced.

def _forced_artifact_spec() -> dict:
    return {
        "id": "forced-artifact", "title": "forced artifact", "start": "a",
        "stages": [
            {"id": "a", "title": "A",
             "evidence": {"kind": "artifact", "path": _ARTIFACT,
                          "contains": ["FINDING"], "min": 1},
             "next": [{"to": "b"}]},
            {"id": "b", "title": "B", "evidence": {"kind": "note"},
             "next": [{"to": "done"}]},
            {"id": "done", "title": "Done", "terminal": True},
        ],
    }


def _forced_command_spec() -> dict:
    return {
        "id": "forced-command", "title": "forced command", "start": "c",
        "stages": [
            {"id": "c", "title": "Run the suite",
             "evidence": {"kind": "command",
                          "argv": [sys.executable, "-c",
                                   "import sys; sys.exit(3)"], "rc": 0},
             "next": [{"to": "done"}]},
            {"id": "done", "title": "Done", "terminal": True},
        ],
    }


def _prepare_forced_run(tmp: pathlib.Path, name: str, kind: str):
    """A run whose stage completed on evidence that was NOT satisfied.

    `artifact`: the file exists without the declared token. `command`: the argv
    exits 3 where rc 0 was declared — and the project is a git work tree, because
    a command witness is dated to a tracked code state and that dating is part of
    what makes the false CURRENT reachable.
    """
    root = tmp / name
    root.mkdir(parents=True)
    if kind == "command":
        _write(root, "app.py", "V = 1\n")
        _git(root, "init", "-q")
        _commit(root, "init")
        spec = _forced_command_spec()
        stage = "c"
    else:
        _write(root, _ARTIFACT, "# analysis without the token\n")
        spec = _forced_artifact_spec()
        stage = "a"
    if not engine.create(str(root), spec).get("ok"):
        raise RuntimeError(f"{name}: create failed")
    if not engine.complete(str(root), spec["id"], stage, force=True).get("ok"):
        raise RuntimeError(f"{name}: forced complete refused")
    if BASELINE:
        # The unfixed tree wrote a witness with no forced marker; strip it so the
        # surfaces classify the OLD record with real code.
        path = pathlib.Path(store_mod.state_path(str(root), spec["id"]))
        data = json.loads(path.read_text(encoding="utf-8"))
        for witness in (data.get("evidence") or {}).values():
            witness.pop("forced", None)
            witness.pop("forced_detail", None)
        path.write_text(json.dumps(data), encoding="utf-8")
    return str(root), spec["id"], stage


def _forced_verdicts(root: str, slug: str, stage: str) -> dict[str, str | None]:
    """The verdict each surface delivers for the forced completion (never None)."""
    status = engine.status(root, slug)
    nxt = engine.next_stage(root, slug)
    row = _row_for(root, slug)

    def _of(result: dict) -> str | None:
        verdict = ((result.get("evidence") or {}).get(stage) or {}).get("verdict")
        return str(verdict).upper() if verdict else None

    return {"status": _of(status), "next": _of(nxt), "list": _of(row)}


def _run_forced_control(tmp: pathlib.Path, name: str = "forced-control") -> dict[str, bool]:
    """A genuine completion still reads CURRENT, and an unforced refusal stands."""
    root = tmp / name
    root.mkdir(parents=True)
    _write(root, _ARTIFACT, "# analysis without the token\n")
    spec = _forced_artifact_spec()
    if not engine.create(str(root), spec).get("ok"):
        raise RuntimeError(f"{name}: create failed")
    # The gate must still refuse the unsatisfied evidence when nobody forces it.
    refused = engine.complete(str(root), spec["id"], "a")
    _write(root, _ARTIFACT, "# analysis\nFINDING 1\n")
    if not engine.complete(str(root), spec["id"], "a").get("ok"):
        raise RuntimeError(f"{name}: genuine complete refused")
    surfaces = _forced_verdicts(str(root), spec["id"], "a")
    return {**{k: v != "CURRENT" for k, v in surfaces.items()},
            "gate": bool(refused.get("ok"))}


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
    silence = silence_n = held = held_n = 0
    afield = afield_n = afield_accepted = afield_gate_n = 0
    afield_control = afield_control_n = 0
    slug_reads = slug_reads_n = slug_wrong = slug_wrong_n = 0
    forced = forced_n = forced_control = forced_control_n = 0
    slug_status_reads = 0
    evidence_mod.artifact_matches = _counted_matches
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
        # Bucket 5: command evidence, dated to the tracked code state.
        for label in _command_cases():
            root, slug = _prepare_command_run(tmp, f"command-{label}", label)
            for surface, delivered in _command_verdicts(str(root), slug).items():
                silence_n += 1
                if delivered != "UNKNOWN":
                    silence += 1
                    lines.append(f"  COMMAND {label}/{surface}: a command at a "
                                 f"moved code state delivered as "
                                 f"{delivered or 'no verdict'}")
        root, slug = _run_command_control(tmp)
        for surface, delivered in _command_verdicts(root, slug).items():
            held_n += 1
            if delivered != "CURRENT":
                held += 1
                lines.append(f"  CONTROL command-control/{surface}: an unchanged "
                             f"code state delivered as "
                             f"{delivered or 'no verdict'}")
        # Bucket 6: the spec's declared `artifact` field must stay in the root.
        for label, declared in _artifact_field_cases():
            name = f"afield-{label}"
            pattern = declared(tmp / name)
            root, slug = _prepare_artifact_field_run(tmp, name, pattern)
            for surface, delivered in _artifact_field_delivered(
                    root, slug, pattern).items():
                afield_n += 1
                if delivered:
                    afield += 1
                    lines.append(f"  AFIELD {label}/{surface}: an out-of-root "
                                 f"`artifact` was delivered as the run's "
                                 f"tracking location")
            gate_name = f"afield-gate-{label}"
            afield_gate_n += 1
            if _run_artifact_field_gate_trial(tmp, gate_name,
                                              declared(tmp / gate_name)):
                afield_accepted += 1
                lines.append(f"  AFIELD {label}/create: the gate accepted a spec "
                             f"whose declared `artifact` resolves outside the root")
        # Bucket 7: resolving a NAME must not derive evidence.
        for label, slugs in _slug_resolve_cases():
            trial = _slug_resolve_trial(tmp, label, slugs)
            slug_reads_n += 1
            slug_status_reads += int(trial["status_reads"])
            if int(trial["reads"]) > 0:
                slug_reads += 1
                lines.append(f"  SLUG   {label}/resolve: a name question derived "
                             f"{trial['reads']} evidence verdict(s)")
            slug_wrong_n += 1
            if trial["wrong"]:
                slug_wrong += 1
                lines.append(f"  CONTROL slug-{label}: resolved to "
                             f"{trial['chosen']!r}, expected {trial['expected']!r}")
        # Bucket 8: a forced completion must not deliver a claim it never made.
        for label, kind in (("forced-artifact", "artifact"),
                            ("forced-command", "command")):
            root, slug, stage = _prepare_forced_run(tmp, label, kind)
            for surface, delivered in _forced_verdicts(root, slug, stage).items():
                forced_n += 1
                if delivered != "UNKNOWN":
                    forced += 1
                    lines.append(f"  FORCED {label}/{surface}: a completion "
                                 f"accepted with --force delivered as "
                                 f"{delivered or 'no verdict'}")
        for surface, failed in _run_forced_control(tmp).items():
            forced_control_n += 1
            if failed:
                forced_control += 1
                lines.append(f"  CONTROL forced-control/{surface}: a genuine "
                             f"completion or the unforced refusal changed")
        for surface, missing in _run_artifact_field_control(tmp).items():
            afield_control_n += 1
            if missing:
                afield_control += 1
                lines.append(f"  CONTROL artifact-field-control/{surface}: an "
                             f"in-root `artifact` stopped being delivered")
    finally:
        evidence_mod.artifact_matches = _REAL_MATCHES
        shutil.rmtree(tmp, ignore_errors=True)
    if (not stale_n or not current_n or not unbounded_n or not escape_n
            or not storage_n or not control_n or not silence_n or not held_n
            or not afield_n or not afield_gate_n or not afield_control_n
            or not slug_reads_n or not slug_wrong_n
            or not forced_n or not forced_control_n):
        raise RuntimeError("fixture produced no trials — build broken")
    return {"leaks": leaks, "stale_n": stale_n,
            "false_alarms": false_alarms, "current_n": current_n,
            "unbounded": unbounded, "unbounded_n": unbounded_n,
            "escape": escape, "escape_n": escape_n,
            "accepted": accepted, "accepted_n": accepted_n,
            "storage": storage, "storage_n": storage_n,
            "control": control, "control_n": control_n,
            "silence": silence, "silence_n": silence_n,
            "held": held, "held_n": held_n,
            "afield": afield, "afield_n": afield_n,
            "afield_accepted": afield_accepted, "afield_gate_n": afield_gate_n,
            "afield_control": afield_control, "afield_control_n": afield_control_n,
            "slug_reads": slug_reads, "slug_reads_n": slug_reads_n,
            "slug_wrong": slug_wrong, "slug_wrong_n": slug_wrong_n,
            "forced": forced, "forced_n": forced_n,
            "forced_control": forced_control, "forced_control_n": forced_control_n,
            "slug_status_reads": slug_status_reads,
            "lines": lines}


def _git_available() -> bool:
    """The command bucket dates a completion to a git work tree, so it needs git."""
    try:
        proc = subprocess.run(["git", "--version"], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


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
        _install_prefix_resolver()
    if not _git_available():
        print("BENCH ERROR: the command bucket needs `git` (the code-state oracle)",
              file=sys.stderr)
        return 1
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
          f"storage_control={r['control_n']} command={r['silence_n']} "
          f"command_control={r['held_n']} artifact_field={r['afield_n']} "
          f"artifact_field_gate={r['afield_gate_n']} "
          f"artifact_field_control={r['afield_control_n']} "
          f"slug_resolve={r['slug_reads_n']} slug_resolve_control={r['slug_wrong_n']} "
          f"forced={r['forced_n']} forced_control={r['forced_control_n']} "
          f"surfaces=status+next+list"
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
    print(f"command_silence_rate={r['silence'] / r['silence_n']:.4f} "
          f"({r['silence']}/{r['silence_n']})")
    print(f"command_hold_rate={r['held'] / r['held_n']:.4f} "
          f"({r['held']}/{r['held_n']})")
    print(f"false_alarm_rate={r['false_alarms'] / r['current_n']:.4f} "
          f"({r['false_alarms']}/{r['current_n']})")
    print(f"artifact_field_escape_rate={r['afield'] / r['afield_n']:.4f} "
          f"({r['afield']}/{r['afield_n']})")
    print(f"artifact_field_accepted_rate="
          f"{r['afield_accepted'] / r['afield_gate_n']:.4f} "
          f"({r['afield_accepted']}/{r['afield_gate_n']})")
    print(f"artifact_field_control_rate="
          f"{r['afield_control'] / r['afield_control_n']:.4f} "
          f"({r['afield_control']}/{r['afield_control_n']})")
    print(f"slug_resolve_read_rate={r['slug_reads'] / r['slug_reads_n']:.4f} "
          f"({r['slug_reads']}/{r['slug_reads_n']})")
    print(f"slug_resolve_control_rate={r['slug_wrong'] / r['slug_wrong_n']:.4f} "
          f"({r['slug_wrong']}/{r['slug_wrong_n']})")
    print(f"forced_claim_rate={r['forced'] / r['forced_n']:.4f} "
          f"({r['forced']}/{r['forced_n']})")
    print(f"forced_control_rate={r['forced_control'] / r['forced_control_n']:.4f} "
          f"({r['forced_control']}/{r['forced_control_n']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

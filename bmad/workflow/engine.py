"""Workflow engine — deterministic stage state machine over a declared spec.

Every function returns a JSON-friendly dict. The engine is the ONLY thing that
decides whether a stage may be completed and where the run goes next; the model
supplies work and evidence, never ordering. A stage cannot be completed without
its declared evidence, and a run cannot advance past a stage whose
preconditions are unmet — those are hard refusals, not warnings.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import time

from . import evidence as evidence_mod
from . import paths as paths_mod
from . import spec as spec_mod
from . import store

MAX_HISTORY = 200


def _now() -> float:
    return time.time()


def _fail(msg: str, **extra) -> dict:
    return {"ok": False, "error": msg, **extra}


def _notify_blackboard(project_root: str, slug: str, event: str,
                       stage_id: str = "", detail: str = "",
                       bridge_prefix: str = "") -> None:
    """Fail-open notification to the project blackboard context."""
    try:
        root_dir = str(pathlib.Path(__file__).resolve().parents[2])
        if root_dir not in sys.path:
            sys.path.insert(0, root_dir)
        from hooks.engine.modules import blackboard
        blackboard.write_key(project_root, f"workflow.{slug}.status", event, type_="status")
        if stage_id:
            blackboard.write_key(project_root, f"workflow.{slug}.current", stage_id, type_="stage")
        if bridge_prefix and stage_id:
            # Write a methodology bridge key (e.g. "E-{slug}") so the
            # blackboard _METHODOLOGY_MIRROR_KEYS mechanism automatically
            # creates methodology.last_experiment, etc.
            bridge_key = f"{bridge_prefix}{slug}"
            bridge_value = (f"{stage_id}: {detail}" if detail
                           else f"{stage_id}: completed")
            blackboard.write_key(project_root, bridge_key, bridge_value,
                                 type_="state")
    except Exception:
        pass


def _history_line(stage: str, event: str, detail: str = "") -> str:
    return f"{int(_now())}|{stage}|{event}|{detail}"[:300]


def resolve_slug(project_root: str, slug: str | None = None) -> str | None:
    """Which run the operator meant: the explicit name, or the project's only one.

    A question about NAMES is answered from the names the store already lists
    (:func:`store.list_slugs`). It used to be answered through the inventory,
    which derives an evidence verdict for every run it describes — bounded
    artifact reads, a code-state probe, a spec parse — so the CLI paid for the
    whole evidence plane to learn a filename, and then `status` classified the
    answered run a second time (E-068). The store lists a run per state file,
    corrupt ones included, so the inventory's own one-row-per-file count is what
    this keeps; only the verdicts are dropped.

    None means "no single run to resolve": no runs at all, or several. That is
    the same answer the inventory produced, and the CLI's refusal is unchanged.
    """
    if slug:
        return slug
    slugs = store.list_slugs(project_root)
    return slugs[0] if len(slugs) == 1 else None


def declared_artifact(project_root: str, spec: dict) -> dict:
    """The spec's declared `artifact`, delivered only when the run owns it.

    The top-level `artifact` names the run's durable source of truth and is the
    one spec field the read surfaces hand over verbatim — `status.artifact`
    returns it and the session advisory says "tracked in <it>". That makes it a
    pointer an agent ACTS on, not documentation: E-060 stopped the plane from
    READING an outside artifact and E-062 stopped it from RESOLVING an outside
    spec, and this is the third way a spec can name a file the run does not own
    — by handing the pointer over (E-067, measured by E-066).

    A contained pointer is delivered unchanged. An outside one is not delivered
    at all: `artifact` is None and `artifact_note` names what the spec declares
    and why it was withheld, so nothing is hidden and nothing is trusted.
    """
    declared = str((spec or {}).get("artifact") or "").strip()
    if not declared:
        return {"artifact": None, "artifact_note": ""}
    root_real = paths_mod.root_of(project_root)
    if paths_mod.outside(root_real, os.path.join(str(project_root), declared)):
        return {"artifact": None,
                "artifact_note": (f"spec 'artifact' resolves outside the project "
                                  f"root: {declared} — a run's artifact lives "
                                  f"inside {root_real}")}
    return {"artifact": declared, "artifact_note": ""}


# --- create / load ------------------------------------------------------------

def create(project_root: str, spec: dict, *, slug: str | None = None,
           force: bool = False) -> dict:
    """Instantiate a run from a validated spec. Refuses to clobber by default."""
    problems = spec_mod.validate(spec)
    if problems:
        return _fail("spec is invalid", problems=problems)
    # E-067: the write that PLANTS an out-of-root pointer is refused here, the
    # same asymmetry the evidence plane keeps (gate refuses, view reports).
    outside_artifact = declared_artifact(project_root, spec)
    if outside_artifact["artifact_note"]:
        return _fail(outside_artifact["artifact_note"], artifact=outside_artifact)
    slug = (slug or spec_mod.spec_id(spec)).strip()
    if state_is_corrupt(project_root, slug):
        # E-008: read_state degrades a corrupt file to {} (the documented
        # fail-open storage contract), so `existing` alone read "no run here"
        # and create OVERWROTE the broken-but-possibly-recoverable state with
        # ok:true — without --force. The corrupt predicate gates creation the
        # same way every read path refuses; --force does not override it
        # (the refusal names repair-or-remove as the only remedy).
        return _missing_or_corrupt(project_root, slug)
    existing = store.read_state(project_root, slug)
    if existing and not force:
        return _fail(f"workflow '{slug}' already exists (use --force to reset)",
                     slug=slug)
    spec_file = store.save_spec(project_root, spec)
    state = {
        "slug": slug,
        "spec_id": spec_mod.spec_id(spec),
        "spec_file": spec_file,
        "status": "active",
        "current": spec["start"],
        "completed": [],
        "flags": {},
        "blocked": None,
        "history": [_history_line(spec["start"], "start")],
        "created_at": _now(),
    }
    store.write_state(project_root, slug, state)
    _notify_blackboard(project_root, slug, "created", spec["start"])
    return {"ok": True, "slug": slug, "state": state}


def load(project_root: str, slug: str) -> tuple[dict, dict]:
    """Return (state, spec); both {} when missing (callers check for emptiness)."""
    state = store.read_state(project_root, slug)
    if not state:
        return {}, {}
    spec = store.load_spec(project_root, slug)
    return state, spec


def state_is_corrupt(project_root: str, slug: str) -> bool:
    """True when the run has a state file but it fails to parse (E-005).

    store.read_state() degrades a corrupt file to {} by design (fail-open);
    this predicate recovers the distinction so callers can tell a missing run
    from one whose state cannot be read.
    """
    import json as _json
    import os as _os
    # E-008: probe THE storage path (slug sanitized), never a raw-slug join —
    # "my run" lives at my_run.state.json, and a raw probe would miss it,
    # report the corrupt run as missing, and lose its event trail.
    path = store.state_path(project_root, slug)
    if not _os.path.exists(path):
        return False
    try:
        with open(path, encoding="utf-8") as fh:
            _json.load(fh)
    except (OSError, _json.JSONDecodeError, UnicodeDecodeError):
        # UnicodeDecodeError (E-009): a BINARY state file is unreadable JSON
        # too — without it the detector itself raised and the CLI tracebacks
        # instead of naming the corrupt file.
        return True
    return False


def _missing_or_corrupt(project_root: str, slug: str) -> dict:
    """The honest refusal for an unreadable run (E-005).

    A corrupt state file used to masquerade as "no workflow 'slug'" — the
    operator's next move (re-create with --force) would silently overwrite
    the broken-but-possibly-recoverable file and its event trail.
    """
    if state_is_corrupt(project_root, slug):
        # E-008: name the file storage actually wrote ("my run" ->
        # my_run.state.json), not a raw-slug path that does not exist.
        fname = store.state_path(project_root, slug).replace("\\", "/").rsplit("/", 1)[-1]
        rel = f".metodoloji/workflow/{fname}"
        return {"ok": False,
                "error": (f"workflow '{slug}' state file is corrupt (unreadable JSON): "
                          f"{rel} — repair or remove it; do NOT re-create over it")}
    return _fail(f"no workflow '{slug}'")


# --- inspection ---------------------------------------------------------------

def status(project_root: str, slug: str) -> dict:
    state, spec = load(project_root, slug)
    if not state:
        return _missing_or_corrupt(project_root, slug)
    current = state.get("current")
    stage = spec_mod.stage_by_id(spec, current) if (spec and current) else None
    health = evidence_mod.verify(project_root, state, spec)
    artifact = declared_artifact(project_root, spec)
    return {
        "ok": True,
        "slug": slug,
        "status": state.get("status"),
        "current": current,
        "completed": state.get("completed", []),
        "flags": state.get("flags", {}),
        "blocked": state.get("blocked"),
        "stage": stage,
        # E-067: the declared durable pointer, confined like everything else this
        # plane hands over — an outside one arrives as None plus the reason.
        "artifact": artifact["artifact"],
        "artifact_note": artifact["artifact_note"],
        "total_stages": len(spec.get("stages", [])) if spec else 0,
        # E-056: a recorded completion is a claim about the workspace, so the
        # two read surfaces carry whether it still holds — derived here, never
        # stored. `evidence` is keyed by stage id; `stale_evidence` names the
        # completed stages whose artifact no longer satisfies its predicate.
        "evidence": health["stages"],
        "evidence_health": health["tally"],
        "stale_evidence": health["stale"],
    }


def next_stage(project_root: str, slug: str) -> dict:
    """What to do now, and (deterministically) where the run goes after it."""
    state, spec = load(project_root, slug)
    if not state:
        return _missing_or_corrupt(project_root, slug)
    current = state.get("current")
    stage = spec_mod.stage_by_id(spec, current) if current else None
    decision = spec_mod.compute_next(
        spec, current=current or "",
        completed=set(state.get("completed", [])),
        ctx=state.get("flags", {}),
    ) if (spec and current) else {"decision": "blocked", "reason": "no current stage"}
    # E-056: `next` is the surface an agent drives a run from, so it must not
    # present the computed step as if the completed foundation still held.
    health = evidence_mod.verify(project_root, state, spec)
    return {"ok": True, "slug": slug, "status": state.get("status"),
            "current": current, "stage": stage, "after": decision,
            "evidence": health["stages"],
            "evidence_health": health["tally"],
            "stale_evidence": health["stale"]}


def list_runs(project_root: str) -> dict:
    runs = []
    probe = evidence_mod.CodeStateProbe(project_root)
    for slug in store.list_slugs(project_root):
        if state_is_corrupt(project_root, slug):
            # A corrupt state file used to appear as a ghost row of nulls,
            # indistinguishable from a real run (E-005). Name it.
            runs.append({"slug": slug, "corrupt": True,
                         "error": "state file unreadable — repair or remove it, do not re-create over it"})
            continue
        state = store.read_state(project_root, slug)
        # E-058: the inventory is a READ SURFACE — an operator picks the run to
        # continue from it, so a run whose foundation moved must not be listed
        # as merely "completed". The verdict is derived on a bounded read
        # (evidence.verify -> artifact_matches(bounded=True)), which is what
        # makes classifying every run here affordable.
        # E-064: `command` witnesses are dated to the tracked code state, and the
        # inventory classifies EVERY run — so the pass shares ONE probe instead
        # of running the oracle once per row (the probe is lazy: a project whose
        # completions are all artifacts pays nothing).
        health = evidence_mod.verify(project_root, state,
                                     store.load_spec(project_root, slug),
                                     code_state_now=probe)
        runs.append({
            "slug": slug,
            "status": state.get("status"),
            "current": state.get("current"),
            "completed": len(state.get("completed", [])),
            "updated_at": state.get("updated_at"),
            "evidence": health["stages"],
            "evidence_health": health["tally"],
            "stale_evidence": health["stale"],
        })
    return {"ok": True, "runs": runs}


def history(project_root: str, slug: str, limit: int = 50) -> dict:
    state = store.read_state(project_root, slug)
    if not state:
        return _missing_or_corrupt(project_root, slug)
    return {"ok": True, "slug": slug, "history": state.get("history", [])[-limit:]}


# --- mutation -----------------------------------------------------------------

def flag(project_root: str, slug: str, key: str, value: str) -> dict:
    """Set a branch flag a conditional edge reads (e.g. regressed=true)."""
    state, _ = load(project_root, slug)
    if not state:
        return _missing_or_corrupt(project_root, slug)
    flags = dict(state.get("flags", {}))
    flags[key] = str(value)
    state["flags"] = flags
    store.write_state(project_root, slug, state)
    return {"ok": True, "slug": slug, "flags": flags}


def complete(project_root: str, slug: str, stage_id: str, *,
             note: str = "", force: bool = False) -> dict:
    """Validate the current stage's evidence, record it, and advance."""
    state, spec = load(project_root, slug)
    if not state:
        return _missing_or_corrupt(project_root, slug)
    if not spec:
        return _fail(f"spec for '{slug}' is missing — cannot advance")
    if state.get("status") != "active":
        return _fail(f"workflow '{slug}' is {state.get('status')}; resume/force first")

    current = state.get("current")
    if stage_id != current and not force:
        return _fail(f"stage '{stage_id}' is not the current stage '{current}' "
                     f"(ordering is computed, not chosen)")

    stage = spec_mod.stage_by_id(spec, stage_id)
    if stage is None:
        return _fail(f"unknown stage '{stage_id}'")

    completed = list(state.get("completed", []))
    unmet = [r for r in (stage.get("requires") or []) if r not in completed]
    if unmet and not force:
        return _fail(f"stage '{stage_id}' requires {unmet} first", unmet=unmet)

    # E-056: an advance must not rest on evidence the workspace has moved past.
    # Derived on every attempt (never stored). UNKNOWN is deliberately NOT a
    # refusal — a legacy run with no witness stays advanceable — while a
    # completed stage whose predicate no longer holds (STALE) is, and --force
    # remains the kernel's documented escape hatch.
    health = evidence_mod.verify(project_root, state, spec)
    foundation = evidence_mod.foundation_of(state, stage)
    broken = [s for s in health["stale"] if s in foundation]
    if broken and not force:
        return _fail(
            f"stage '{stage_id}' rests on evidence that no longer holds: "
            f"{broken} — re-complete it or pass --force",
            stale_evidence=broken)

    ok, detail = _check_evidence(project_root, stage, spec, note)
    if not ok and not force:
        return _fail(f"evidence for '{stage_id}' is not satisfied: {detail}",
                     evidence=detail)

    bridge_prefix = str(stage.get("bridge", "")).strip() or ""

    if stage_id not in completed:
        completed.append(stage_id)
    state["completed"] = completed

    # Date the completion to the predicate that satisfied it (E-056). Stored,
    # because the spec can be edited afterwards; the VERDICT is still derived
    # from the artifact on every read.
    # E-070: a completion accepted with `--force` satisfied NOTHING, so the
    # witness must say so. Recorded without that marker it was indistinguishable
    # from a real one, and the plane then either blamed the workspace for the
    # kernel's own claim (STALE, which also blocked the next advance on it) or
    # vouched for a command that never passed (CURRENT).
    witness = evidence_mod.witness_for(stage, project_root=project_root)
    if witness:
        if not ok:
            witness["forced"] = True
            witness["forced_detail"] = detail
        state.setdefault("evidence", {})[stage_id] = witness

    # Write bridge key for the completed stage (not the target) so the
    # blackboard methodology relay sees methodology.last_* keys.
    if bridge_prefix:
        _notify_blackboard(project_root, slug, "completed", stage_id, detail,
                           bridge_prefix)

    decision = spec_mod.compute_next(spec, current=stage_id,
                                     completed=set(completed),
                                     ctx=state.get("flags", {}))
    history = list(state.get("history", []))
    history.append(_history_line(stage_id, "complete", detail))

    # A taken conditional edge consumes its flag(s) so the default edge wins
    # next time — that is what makes a branch able to fire once without looping.
    if decision.get("decision") == "advance":
        flags = dict(state.get("flags", {}))
        for key in decision.get("cleared", []):
            flags[key] = ""
        state["flags"] = flags
        target = decision["stage"]
        target_stage = spec_mod.stage_by_id(spec, target)
        history.append(_history_line(target, "enter"))
        if (target_stage is not None and target_stage.get("terminal")
                and not target_stage.get("evidence")):
            # A terminal stage with no evidence is a pure end marker: entering it
            # finishes the run instead of demanding a no-op completion.
            if target not in completed:
                completed.append(target)
            state["completed"] = completed
            state["current"] = None
            state["status"] = "completed"
            history.append(_history_line(target, "done"))
            _notify_blackboard(project_root, slug, "completed", target, detail)
        else:
            state["current"] = target
            state["status"] = "active"
            _notify_blackboard(project_root, slug, "advanced", target, detail)
    elif decision.get("decision") == "end":
        state["current"] = None
        state["status"] = "completed"
        history.append(_history_line(stage_id, "done"))
        _notify_blackboard(project_root, slug, "completed", stage_id, detail)
    else:  # blocked transition — the spec has no matching edge
        state["status"] = "blocked"
        state["blocked"] = {"stage": stage_id, "reason": decision.get("reason", ""),
                            "ts": _now()}
        _notify_blackboard(project_root, slug, "blocked", stage_id,
                           decision.get("reason", ""))

    state["history"] = history[-MAX_HISTORY:]
    store.write_state(project_root, slug, state)
    return {"ok": True, "slug": slug, "completed": completed,
            "next": decision, "status": state["status"],
            "current": state.get("current"),
            "evidence_health": evidence_mod.verify(project_root, state, spec)["tally"]}


def block(project_root: str, slug: str, reason: str) -> dict:
    state, _ = load(project_root, slug)
    if not state:
        return _missing_or_corrupt(project_root, slug)
    state["status"] = "blocked"
    state["blocked"] = {"stage": state.get("current"), "reason": reason, "ts": _now()}
    state.setdefault("history", []).append(
        _history_line(state.get("current") or "-", "block", reason))
    store.write_state(project_root, slug, state)
    _notify_blackboard(project_root, slug, "blocked", state.get("current") or "", reason)
    return {"ok": True, "slug": slug, "blocked": state["blocked"]}


def resume(project_root: str, slug: str) -> dict:
    state, _ = load(project_root, slug)
    if not state:
        return _missing_or_corrupt(project_root, slug)
    state["status"] = "active"
    state["blocked"] = None
    state.setdefault("history", []).append(
        _history_line(state.get("current") or "-", "resume"))
    store.write_state(project_root, slug, state)
    _notify_blackboard(project_root, slug, "resumed", state.get("current") or "")
    return {"ok": True, "slug": slug, "status": "active",
            "current": state.get("current")}


# --- evidence -----------------------------------------------------------------

def _check_evidence(project_root: str, stage: dict, spec: dict,
                    note: str) -> tuple[bool, str]:
    """True only when the stage's declared evidence actually exists/ran."""
    ev = stage.get("evidence") or {}
    kind = ev.get("kind")
    if kind == "artifact":
        # E-056: ONE matcher, shared with the re-derivation the read surfaces
        # run — a second copy would be a second opinion on the same predicate.
        # The check and the witness are the same code, so a completion cannot
        # be recorded under a predicate the plane would later score differently.
        found, found_detail = evidence_mod.artifact_matches(
            project_root, str(ev.get("path", "")),
            [str(t) for t in (ev.get("contains") or [])], ev.get("min", 1),
            bounded=False)  # E-058: accepting a stage reads its artifact once
        # None = cannot establish (unreadable artifact): fail-explicit, the
        # same refusal the missing-file case gets — never a silent pass.
        return (found is True), found_detail
    if kind == "command":
        argv = [str(a) for a in (ev.get("argv") or [])]
        if not argv:
            return False, "command evidence has no argv"
        want = int(ev.get("rc", 0))
        delay = float(ev.get("retry_delay", 0.3))
        # stdin=DEVNULL: under pytest capture on Windows the inherited std
        # handles can be invalid and the child dies with WinError 6 ("The
        # handle is invalid"); a fresh stdin sidesteps it, and the retry covers
        # the residual race. Without this a green test can be read as a failed
        # stage — a false refusal, which is worse than a slow one.
        proc = None
        for attempt in range(3):
            try:
                proc = subprocess.run(argv, cwd=project_root, capture_output=True,
                                      text=True, encoding="utf-8", errors="replace",
                                      timeout=int(ev.get("timeout", 600)),
                                      stdin=subprocess.DEVNULL)
                break
            except subprocess.TimeoutExpired as exc:
                return False, f"command timed out: {exc}"
            except OSError as exc:
                if attempt == 2:
                    return False, f"command failed to run: {exc}"
                time.sleep(delay * (attempt + 1))
        if proc is None:  # unreachable, but never advance on a missing result
            return False, "command did not run"
        if proc.returncode != want:
            tail = (proc.stdout or proc.stderr or "")[-200:].strip()
            return False, f"command exit {proc.returncode} != {want}: {tail}"
        return True, f"command exit {proc.returncode}"
    if kind == "note":
        if not note.strip():
            return False, "note evidence requires --note"
        return True, f"note: {note.strip()[:120]}"
    if kind is None:
        return False, "stage declares no evidence"
    return False, f"unknown evidence kind {kind!r}"

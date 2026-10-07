"""Workflow engine — deterministic stage state machine over a declared spec.

Every function returns a JSON-friendly dict. The engine is the ONLY thing that
decides whether a stage may be completed and where the run goes next; the model
supplies work and evidence, never ordering. A stage cannot be completed without
its declared evidence, and a run cannot advance past a stage whose
preconditions are unmet — those are hard refusals, not warnings.
"""

from __future__ import annotations

import glob
import os
import pathlib
import subprocess
import sys
import time

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


# --- create / load ------------------------------------------------------------

def create(project_root: str, spec: dict, *, slug: str | None = None,
           force: bool = False) -> dict:
    """Instantiate a run from a validated spec. Refuses to clobber by default."""
    problems = spec_mod.validate(spec)
    if problems:
        return _fail("spec is invalid", problems=problems)
    slug = (slug or spec_mod.spec_id(spec)).strip()
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


# --- inspection ---------------------------------------------------------------

def status(project_root: str, slug: str) -> dict:
    state, spec = load(project_root, slug)
    if not state:
        return _fail(f"no workflow '{slug}'")
    current = state.get("current")
    stage = spec_mod.stage_by_id(spec, current) if (spec and current) else None
    return {
        "ok": True,
        "slug": slug,
        "status": state.get("status"),
        "current": current,
        "completed": state.get("completed", []),
        "flags": state.get("flags", {}),
        "blocked": state.get("blocked"),
        "stage": stage,
        "artifact": spec.get("artifact") if spec else None,
        "total_stages": len(spec.get("stages", [])) if spec else 0,
    }


def next_stage(project_root: str, slug: str) -> dict:
    """What to do now, and (deterministically) where the run goes after it."""
    state, spec = load(project_root, slug)
    if not state:
        return _fail(f"no workflow '{slug}'")
    current = state.get("current")
    stage = spec_mod.stage_by_id(spec, current) if current else None
    decision = spec_mod.compute_next(
        spec, current=current or "",
        completed=set(state.get("completed", [])),
        ctx=state.get("flags", {}),
    ) if (spec and current) else {"decision": "blocked", "reason": "no current stage"}
    return {"ok": True, "slug": slug, "status": state.get("status"),
            "current": current, "stage": stage, "after": decision}


def list_runs(project_root: str) -> dict:
    runs = []
    for slug in store.list_slugs(project_root):
        state = store.read_state(project_root, slug)
        runs.append({
            "slug": slug,
            "status": state.get("status"),
            "current": state.get("current"),
            "completed": len(state.get("completed", [])),
            "updated_at": state.get("updated_at"),
        })
    return {"ok": True, "runs": runs}


def history(project_root: str, slug: str, limit: int = 50) -> dict:
    state = store.read_state(project_root, slug)
    if not state:
        return _fail(f"no workflow '{slug}'")
    return {"ok": True, "slug": slug, "history": state.get("history", [])[-limit:]}


# --- mutation -----------------------------------------------------------------

def flag(project_root: str, slug: str, key: str, value: str) -> dict:
    """Set a branch flag a conditional edge reads (e.g. regressed=true)."""
    state, _ = load(project_root, slug)
    if not state:
        return _fail(f"no workflow '{slug}'")
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
        return _fail(f"no workflow '{slug}'")
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

    ok, detail = _check_evidence(project_root, stage, spec, note)
    if not ok and not force:
        return _fail(f"evidence for '{stage_id}' is not satisfied: {detail}",
                     evidence=detail)

    bridge_prefix = str(stage.get("bridge", "")).strip() or ""

    if stage_id not in completed:
        completed.append(stage_id)
    state["completed"] = completed

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
            "current": state.get("current")}


def block(project_root: str, slug: str, reason: str) -> dict:
    state, _ = load(project_root, slug)
    if not state:
        return _fail(f"no workflow '{slug}'")
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
        return _fail(f"no workflow '{slug}'")
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
        raw_path = str(ev.get("path", ""))
        full_pattern = os.path.join(project_root, raw_path)
        if any(ch in raw_path for ch in ("*", "?", "[")):
            matches = [p for p in glob.glob(full_pattern, recursive=True) if os.path.isfile(p)]
            if not matches:
                return False, f"artifact not found: {raw_path}"
            candidate_paths = sorted(matches, key=lambda p: (os.path.getmtime(p), p), reverse=True)
        else:
            if not os.path.isfile(full_pattern):
                return False, f"artifact not found: {raw_path}"
            candidate_paths = [full_pattern]

        contains = [str(t) for t in (ev.get("contains") or [])]
        minimum = int(ev.get("min", 1))
        if not contains:
            return True, f"artifact {raw_path} ok"

        for cand in candidate_paths:
            try:
                text = pathlib.Path(cand).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            hits = sum(text.count(tok) for tok in contains)
            if hits >= minimum:
                return True, f"artifact {os.path.basename(cand)} ok"

        return False, (f"artifact {raw_path} has no candidate matching "
                       f"{contains} (need >= {minimum})")
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

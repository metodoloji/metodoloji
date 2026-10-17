"""Workflow advisory — the read-only one-liner the session edge injects.

The kernel already knows where a run stands; this module turns that computed
position into one bounded sentence, so a resumed session continues the declared
workflow instead of re-deriving the plan from chat. The advisory is read-only by
construction — it never writes state, never advances a stage and never consumes
a flag — and fail-open: any problem yields "" and the session opens with the
fixed sentence alone.

Why the position, not just the name: `next` is *computed* from the spec and the
recorded flags, so the sentence can state where the run goes after the current
stage without the model choosing it. That is the whole point of the kernel, so
the place the agent looks first (session start) shows it.
"""

from __future__ import annotations

from . import engine as engine_mod
from . import evidence as evidence_mod
from . import spec as spec_mod
from . import store

# Bounded by design: one session-edge line, never a run inventory dump.
MAX_RUNS = 2
MAX_BROKEN_FOUNDATION = 2  # E-056: bounded, like everything else on this line
_TITLE_LEN = 70


def _clip(text: str, limit: int = _TITLE_LEN) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _evidence_hint(stage: dict) -> str:
    """What has to exist before this stage may complete, in a few words."""
    ev = (stage or {}).get("evidence") or {}
    kind = ev.get("kind")
    if kind == "artifact":
        tokens = ", ".join(str(t) for t in (ev.get("contains") or []))
        base = f"artifact {ev.get('path')}"
        return f"{base} needs {tokens}" if tokens else base
    if kind == "command":
        argv = " ".join(str(a) for a in (ev.get("argv") or [])[:3])
        return f"command `{argv}` (rc={ev.get('rc', 0)})"
    if kind == "note":
        return "a --note statement"
    return "none (end marker)"


def _describe(slug: str, state: dict, spec: dict, project_root: str) -> str:
    current = state.get("current")
    status = state.get("status")
    completed = list(state.get("completed", []))
    stage = spec_mod.stage_by_id(spec, current) if (spec and current) else None
    total = len(spec.get("stages", [])) if spec else 0

    parts = [f"WORKFLOW '{slug}' is {status}"]
    if status == "blocked":
        reason = (state.get("blocked") or {}).get("reason") or "unspecified"
        parts.append(f"stopped at '{current}': {_clip(reason)} — "
                     f"`workflow.py resume` to lift it")
    elif stage:
        parts.append(f"stage {len(completed) + 1}/{total} '{current}' "
                     f"({_clip(stage.get('title', ''))})")
        parts.append(f"completes with {_evidence_hint(stage)}")
        decision = spec_mod.compute_next(
            spec, current=current, completed=set(completed),
            ctx=state.get("flags", {}))
        if decision.get("decision") == "advance":
            parts.append(f"then the kernel computes '{decision['stage']}'")
        elif decision.get("decision") == "end":
            parts.append("then the run ends")
        else:
            parts.append("no transition matches the current state")
    elif current:
        parts.append(f"at unknown stage '{current}' (spec missing?)")
    else:
        parts.append("with no current stage")

    # E-056: the session edge is where stale context gets obeyed, so a run whose
    # completed stage no longer carries its evidence must not read as a clean
    # position. Report-only (like the board plane at `freshness = warn`): the
    # kernel refuses the advance at `complete`, and this line says why before
    # the agent plans its next move.
    if project_root:
        try:
            broken = evidence_mod.verify(project_root, state, spec)["stale"]
        except Exception:
            broken = []
        if broken:
            shown = ", ".join(f"'{s}'" for s in broken[:MAX_BROKEN_FOUNDATION])
            extra = (f" (+{len(broken) - MAX_BROKEN_FOUNDATION} more)"
                     if len(broken) > MAX_BROKEN_FOUNDATION else "")
            parts.append(f"FOUNDATION MOVED: completed {shown}{extra} no longer "
                         f"carries its evidence — re-complete it, do not trust "
                         f"the position above")

    # E-067: this line is context an agent obeys, so the declared pointer is
    # delivered only when the run owns it — otherwise the sentence says what the
    # spec declares and that it is outside, which is the honest half.
    field = engine_mod.declared_artifact(project_root, spec)
    if field["artifact"]:
        parts.append(f"tracked in {field['artifact']}")
    elif field["artifact_note"]:
        parts.append(f"{field['artifact_note']} — fix the spec")
    return "; ".join(parts)


def peek(project_root: str, limit: int = MAX_RUNS) -> str:
    """A bounded sentence about unfinished runs, or "" (fail-open, read-only)."""
    try:
        runs: list[tuple[str, dict]] = []
        for slug in store.list_slugs(project_root):
            state = store.read_state(project_root, slug)
            if not state or state.get("status") == "completed":
                continue
            runs.append((slug, state))
        lines = [_describe(slug, state, store.load_spec(project_root, slug),
                           project_root)
                 for slug, state in runs[:max(1, limit)]]
    except Exception:
        return ""
    skipped = len(runs) - len(lines)
    if not lines:
        return ""
    tail = f" (+{skipped} more run(s))" if skipped else ""
    return (" ".join(lines) + tail +
            " — the order is computed, not chosen: drive it with "
            "`workflow.py status|next|complete`.")

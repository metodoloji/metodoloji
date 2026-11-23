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


def _counted_list(items, limit: int, *, noun: str) -> str:
    """The repo's bounded-list join, reached lazily to keep `peek` fail-open.

    This module is imported by `hooks.workflow`, which the session edge reaches
    from its own lazy import. A module-scope `hooks.engine.modules.utils`
    import would drag the whole engine package (audit/guard/stop/config, whose
    config validates itself) into that path, so a broken engine would cost the
    whole workflow line instead of just its remainder marker. `orient._named`
    makes the same choice for the same reason: fall back to a local marker
    rather than go silent.
    """
    try:
        from hooks.engine.modules.utils import counted_list  # noqa: PLC0415
        return counted_list(items, limit, noun=noun)
    except Exception:
        pass
    shown = ", ".join(str(item) for item in items[:limit])
    rest = len(items) - limit
    if rest <= 0:
        return shown
    return f"{shown}, … (+{rest} more {noun}{'' if rest == 1 else 's'})"


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
        # E-208: `argv[:3]` printed `chain-gate.py --stage` for every builtin
        # stage — a dangling flag, no slug, no project root, and identical for
        # six different stages. The hint exists to tell the agent what to run;
        # a command elided in the middle is a different command. argv is
        # authored by the spec, so it prints whole.
        argv = " ".join(str(a) for a in (ev.get("argv") or []))
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
            # E-215: the broken-foundation list printed at most two stages and
            # then a bare `(+N more)`, so a run with five stale stages read as
            # three and the reader could not tell withdrawn stages from withdrawn
            # runs. counted_list names the unit (stage) and the true count.
            shown = _counted_list(
                [f"'{s}'" for s in broken],
                MAX_BROKEN_FOUNDATION, noun="stage")
            parts.append(f"FOUNDATION MOVED: completed {shown} no longer "
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
    # E-215: `run(s)` is the only place in the tree that still uses the
    # parenthesised plural; the withheld count names its unit instead.
    if skipped == 1:
        tail = " (+1 more run)"
    elif skipped > 1:
        tail = f" (+{skipped} more runs)"
    else:
        tail = ""
    return (" ".join(lines) + tail +
            " — the order is computed, not chosen: drive it with "
            "`workflow.py status|next|complete`.")

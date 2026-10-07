"""Mirror — the project's single chain-heartbeat entry point.

Every methodology/tool-chain stage ends in one of two board effects:

- **heartbeat**: the stage's run key (`E-001`, `IR-2026-09-17`, `story.1-1-x`,
  `QR-001`, …) is written. `write_key` automatically stamps the
  `methodology.last_*` relay position from the key prefix, so session_start
  and doctor surface the progress with no further calls.
- **hand-off**: an optional signal into `handoff.<downstream>` so the next
  run opens knowing what to pick up first. Posting is idempotent: a waiting
  signal with the same `<from-key>:` prefix is never duplicated — re-running
  a close-out (or a script) cannot flood the channel.

Producers (gate script, record generators, skills via the `mirror` CLI)
call exactly one function instead of hand-assembling `write` + `handoff`
pairs that get skipped or half-executed. Fail-open throughout: the mirror
never raises, never changes the caller's verdict, and silently degrades to
a no-op when disabled (`METODOLOJI_NO_BLACKBOARD=1`) or rootless.
"""

from __future__ import annotations

import json
import os
import pathlib
import time

# Run-key prefix → owning stage (diagnostic only; unknown prefixes mirror
# fine and attribute to "custom" — the protocol is extensible).
_STAGE_BY_PREFIX = (
    ("E-", "experiment"),
    ("IR-", "readiness"),
    ("SP-", "sprint"),
    ("S-", "story"),
    ("story.", "story"),
    ("QR-", "quality"),
    ("PR-", "readiness-review"),
    ("prd.", "prd"),
    ("ux.", "ux"),
    ("architecture.", "architecture"),
    ("spec.", "spec"),
    ("epics.", "epics"),
    ("brief.", "brief"),
    ("forge.", "forge"),
    ("brainstorm.", "brainstorm"),
    ("prfaq.", "prfaq"),
    ("research.market.", "market-research"),
    ("research.domain.", "domain-research"),
    ("research.tech.", "technical-research"),
    ("research.", "research"),
    ("cis.design.", "cis-design-thinking"),
    ("cis.innovation.", "cis-innovation-strategy"),
    ("cis.solving.", "cis-problem-solving"),
    ("cis.story.", "cis-storytelling"),
    ("retro.", "retrospective"),
    ("change.", "correct-course"),
    ("quickdev.", "quick-dev"),
    ("devauto.", "dev-auto"),
    ("agentdev.", "agent-dev"),
    ("test.atdd.", "test-atdd"),
    ("test.automate.", "test-automate"),
    ("test.ci.", "test-ci"),
    ("test.framework.", "test-framework"),
    ("test.nfr.", "test-nfr"),
    ("test.design.", "test-design"),
    ("test.review.", "test-review"),
    ("test.trace.", "test-trace"),
    ("test.e2e.", "test-e2e"),
    ("test.", "testing"),
    ("teach.", "teaching"),
    # GDS vertical (game) — diagnostic stage attribution only
    ("gds.quickdev.", "gds-quick-dev"),
    ("gds.agentdev.", "gds-agent-game-dev"),
    ("gds.solodev.", "gds-agent-game-solo-dev"),
    ("gds.research.", "gds-research"),
    ("gds.brainstorm.", "gds-brainstorm"),
    ("gds.brief.", "gds-brief"),
    ("gds.narrative.", "gds-narrative"),
    ("gds.gdd.", "gds-gdd"),
    ("gds.ux.", "gds-ux"),
    ("gds.prd.", "gds-prd"),
    ("gds.architecture.", "gds-architecture"),
    ("gds.ir.", "gds-readiness"),
    ("gds.sp.", "gds-sprint"),
    ("gds.story.", "gds-story"),
    ("gds.dev.", "gds-dev"),
    ("gds.review.", "gds-review"),
    ("gds.test.design.", "gds-test-design"),
    ("gds.test.framework.", "gds-test-framework"),
    ("gds.test.automate.", "gds-test-automate"),
    ("gds.test.review.", "gds-test-review"),
    ("gds.test.e2e.", "gds-test-e2e"),
    ("gds.test.perf.", "gds-test-perf"),
    ("gds.playtest.", "gds-playtest"),
    ("gds.retro.", "gds-retro"),
    ("gds.change.", "gds-change"),
    ("gds.status.", "gds-status"),
    ("gds.investigate.", "gds-investigate"),
    ("gds.", "gds"),
    # WDS vertical (web design) — numbered phases
    ("wds.setup.", "wds-setup"),
    ("wds.signoff.", "wds-signoff"),
    ("wds.brief.", "wds-brief"),
    ("wds.trigger.", "wds-trigger"),
    ("wds.scenario.", "wds-scenario"),
    ("wds.ux.", "wds-ux"),
    ("wds.dev.", "wds-dev"),
    ("wds.asset.", "wds-asset"),
    ("wds.designsystem.", "wds-design-system"),
    ("wds.evolve.", "wds-evolve"),
    ("wds.", "wds"),
)


def stage_of(run_key: str) -> str:
    """Stage name for a run key (`E-001` → `experiment`, …; `custom` fallback)."""
    key = str(run_key or "")
    for prefix, stage in _STAGE_BY_PREFIX:
        if key.startswith(prefix):
            return stage
    return "custom"


# Minimum seconds between two identical relay notes (cross-process, see
# pending_note below).
_RELAY_THROTTLE_S = 60


def _disabled() -> bool:
    return os.environ.get("METODOLOJI_NO_BLACKBOARD", "").strip() == "1"


def mirror(project_root: str | None, run_key: str, value: str, *,
           to: str | None = None, note: str = "",
           type_: str = "state", sender: str | None = None) -> dict:
    """Write the run-key heartbeat and optionally hand off downstream.

    - `run_key` (e.g. `E-001`, `story.1-1-x`) is written with `value`; the
      `methodology.last_*` relay position follows automatically.
    - `to` (e.g. `bmad-sprint-planning`) posts one hand-off signal carrying
      `note`, unless an identical `<run_key>:` signal already waits (dedup).
      `sender` stamps the posting stage's identity when the run-key namespace
      is shared (create-story and dev-story both write `story.`) — without it
      the diagnostic attributes the baton to the prefix's default owner.
    - Returns an ack `{"ok", "key", "stage", "handed_off", "dedup", "root"}`.
      Never raises: any board failure degrades to `{"ok": False, …}`.
    """
    run_key = str(run_key or "").strip()[:200]
    if not run_key:
        return {"ok": False, "error": "empty run key"}
    if not project_root:
        return {"ok": False, "error": "no project root (mirror skipped)"}
    if _disabled():
        return {"ok": True, "key": run_key, "stage": stage_of(run_key),
                "handed_off": False, "dedup": False, "skipped": True,
                "root": os.path.abspath(project_root)}
    try:
        from . import blackboard as bb
    except Exception as exc:
        return {"ok": False, "error": f"board unavailable ({exc})"}
    try:
        bb.write_key(project_root, run_key, value, type_=type_)
        handed_off, dedup = False, False
        downstream = str(to or "").strip()
        if downstream:
            waiting = bb.pending_handoffs(project_root, downstream)
            if any(str(a.get("text", "")).startswith(f"{run_key}:")
                   for a in waiting):
                dedup = True  # identical baton already waits — don't flood
            else:
                ack = bb.post_handoff(project_root, downstream, run_key,
                                      note=note or "", sender=sender)
                handed_off = bool(ack.get("ok"))
        return {"ok": True, "key": run_key, "stage": stage_of(run_key),
                "handed_off": handed_off, "dedup": dedup,
                "root": bb.board_paths(project_root)["root"]}
    except Exception as exc:
        return {"ok": False, "error": f"mirror skipped ({exc})"}


def pending_note(project_root: str | None, to: str = "", *, limit: int = 3) -> str | None:
    """One bounded sentence for the freshest unclaimed downstream batons.

    The engine-side carrier of the close-out relay (2026-09-23 mailjs
    session: a dev-story close-out posted the code-review baton and the
    very next turn forgot it — the skill text names the mirror call, but
    between close-outs nothing re-announces the waiting signal). Hook
    layer: guard/pre calls this on gated writes and ships the note out
    through PostToolUse (throttled in audit.py); stop/session_start keep
    their own count-based announcements at the session edges. Returns
    None on an empty relay — the caller injects nothing. Read-only,
    fail-open, announce-only: never consumes, never blocks.
    """
    if not project_root or _disabled():
        return None
    try:
        from . import blackboard as bb
        if to:
            hits = [h for h in bb.recent_handoffs(project_root, limit=limit + 4)
                    if h["skill"] == str(to).strip()]
        else:
            hits = bb.recent_handoffs(project_root, limit=limit + 4)
        if not hits:
            return None
        hits = hits[:limit]
        parts = [f"{h['skill']} ← {h['from']} ({h['age_s'] // 60}m ago)"
                 if h.get("from") else f"{h['skill']} ({h['age_s'] // 60}m ago)"
                 for h in hits]
        s = ", ".join(parts)
        tail = " — peek `handoffs --skill <skill>`, consume the channel, then proceed"
        if to:
            note = (f"PROACTIVE relay: a hand-off addressed to you ({to}) is still "
                    f"unclaimed: {s}{tail}.")
        else:
            note = (f"PROACTIVE relay: {len(hits)} unclaimed downstream hand-off(s): "
                    f"{s}{tail}.")
        # Cross-process throttle: the hook engine is cold-started per call, so
        # an in-process throttle never fires — guard/pre and audit would both
        # re-announce the same unchanged baton every turn. The state lives next
        # to the board it mirrors; a changed table re-announces instantly.
        # Best-effort: an unwritable state file degrades to always-announce.
        #
        # Throttle key is the TABLE STATE, not the rendered text (E-048): the
        # note embeds "(Nm ago)" stamps that roll forward every minute, so a
        # note-text comparison re-announced an unchanged relay at every minute
        # boundary. The digest strips the age stamps from the announced note —
        # the table's identity, not its clock — and hashes the rest: identical
        # tables stay silent for the window regardless of age drift, and any
        # real change (baton added/consumed, different set) still re-announces
        # instantly. Backward-compatible read: a state file written by the old
        # note-keyed code carries "note", not "digest" — treated as foreign,
        # so the first call after the upgrade announces once and re-keys the
        # state.
        import hashlib
        import re as _re
        digest = hashlib.sha256(
            _re.sub(r"\(\d+m ago\)", "", note).encode("utf-8")).hexdigest()[:16]
        try:
            state_path = pathlib.Path(bb.board_paths(project_root)["dir"]) / "relay-note.state"
            try:
                st = json.loads(state_path.read_text(encoding="utf-8"))
            except Exception:
                st = {}
            now = time.time()
            if st.get("digest") == digest and (now - float(st.get("ts", 0.0) or 0.0)) < _RELAY_THROTTLE_S:
                return None
            state_path.write_text(json.dumps({"digest": digest, "ts": now}), encoding="utf-8")
        except OSError:
            pass
        return note
    except Exception:
        return None

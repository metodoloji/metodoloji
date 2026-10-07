"""Stop logic for Stop hook.

Report-only (Faz 4): stop() NEVER blocks the session close. It appends a
session_stop marker, then returns allow with a one-line report of anything
left open (in-progress stories, code writes this session without an approved
experiment). Enforcement lives at write time (guard) and commit time
(quality/deploy gates) — closing the loop must not wedge it.
"""

import json
import pathlib
import re
import sys
import time

from .utils import repo_root


# Marker the audit log carries per session start; the report only counts
# lines after the newest marker, so yesterday's leftovers never pollute
# today's report.
_SESSION_MARKER_TYPE = "session_start"

# Cap on session lines read per stop: a bounded tail covers any realistic
# session; unbounded growth would make stop O(history).
_SESSION_TAIL_LINES = 20000

# Sprint-status candidates moved to modules/state.py (single source): the
# session-start inject, orient.py and this scan now share one parser — three
# answers to "where are we" must not disagree.
from .state import _SPRINT_STATUS_CANDIDATES  # noqa: F401  (re-exported for tests)


def _latest_session_marker(root: str) -> dict | None:
    """Newest session_start marker record in the audit log (None = none).

    Scans from the TAIL: the newest marker is almost always near the end, so
    a bounded tail read replaces the full-file parse. Falls back to the full
    file only when the tail holds no marker. Returns the whole record — the
    report key needs `session_id`, which the timestamp-only view threw away.
    """
    from .config import log_file
    log_path = pathlib.Path(root).absolute() / log_file()
    try:
        with open(log_path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 65536))
            tail = f.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(tail):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("type") == _SESSION_MARKER_TYPE:
            return rec
    # No marker in tail — full scan (old logs predate the tail window).
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    found = None
    newest = -1.0
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("type") == _SESSION_MARKER_TYPE:
            try:
                stamp = float(rec.get("timestamp", 0) or 0)
            except (TypeError, ValueError):
                stamp = 0.0
            if stamp >= newest:
                newest = stamp
                found = rec
    return found


def _session_report_key(marker: dict | None) -> str:
    """Stable identity of the current session for report dedupe ('' = unknown).

    `session_id` when the marker carries one; the timestamp otherwise (older
    markers, and test seeds, write no id). Empty string disables dedupe — a
    session we cannot identify must not have its report suppressed by a
    previous one.
    """
    if not isinstance(marker, dict):
        return ""
    session_id = str(marker.get("session_id") or "").strip()
    if session_id:
        return session_id
    try:
        stamp = float(marker.get("timestamp", 0) or 0)
    except (TypeError, ValueError):
        return ""
    return f"ts:{stamp:.6f}" if stamp else ""


def _latest_session_start(root: str) -> float:
    """Newest session_start marker timestamp in the audit log (0.0 = none)."""
    marker = _latest_session_marker(root)
    try:
        return float((marker or {}).get("timestamp", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _report_already_emitted(root: str, session_key: str) -> bool:
    """True when this session already got its Stop report.

    Stop returns a reason → additionalContext → the model continues its turn →
    Stop fires again. The `stop_hook_active` fast exit covers the documented
    re-fire, but a session that simply ends more than one turn (or a duplicate
    Stop registration — "Ran 2 stop hooks") re-enters with no such flag and
    reported the SAME findings three times in the graph-engineering-arge
    session (2026-10-01), each time costing a wrap-up turn. The report is a
    wrap-up nudge, not a per-turn watchdog: emit it once per session.
    """
    if not session_key:
        return False
    from .config import log_file
    log_path = pathlib.Path(root).absolute() / log_file()
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    for line in lines[-_SESSION_TAIL_LINES:]:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if (isinstance(rec, dict) and rec.get("type") == "stop_report"
                and str(rec.get("session_id") or "") == session_key):
            return True
    return False


def _record_stop_report(root: str, session_key: str) -> None:
    """Stamp the once-per-session report marker (fail-open)."""
    if not session_key:
        return
    from .config import log_file
    log_path = pathlib.Path(root).absolute() / log_file()
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "stop_report",
                                "hook_event": "Stop",
                                "session_id": session_key,
                                "timestamp": time.time()},
                               ensure_ascii=False) + "\n")
    except OSError:
        pass


def _in_progress_stories(root: str) -> list[str]:
    """Story keys left in-progress in sprint-status.yaml (stale-safe).

    Parsing lives in modules/state.py (single source with the session-start
    inject and orient.py); the stale-safety stays here because it needs the
    session marker, which is this module's own bookkeeping.

    A sprint-status older than this session's start is a brownfield leftover
    and is ignored; with no session marker the file is read as-is (legacy).
    """
    from .state import sprint_summary
    summary = sprint_summary(root)
    if not summary.get("found"):
        return []
    candidate = summary.get("path")
    try:
        newest = pathlib.Path(candidate).stat().st_mtime if candidate else 0.0
    except OSError:
        newest = 0.0
    session_start = _latest_session_start(root)
    if session_start and newest < session_start:
        return []  # stale leftover from a previous session
    return sorted(k for k, s in (summary.get("stories") or {}).items()
                  if s == "in-progress")


def _session_code_writes(root: str) -> list[str]:
    """Code-target paths this session wrote (audit trail after the marker).

    Bounded tail read, no approval check here — guard already enforced that
    at write time. Free-zone paths (scratch/, .metodoloji/, docs/*.md, …)
    are skipped: they need no approval, so reporting them as "code written"
    is a false positive that keeps the Stop report (and the Stop re-fire
    loop) fed on sessions that wrote no real code — e.g. an init-only
    session whose sole write is `.metodoloji/initialized`. Paths are
    reported verbatim (max 5) as a wrap-up nudge.
    """
    from .config import log_file
    from .utils import is_code_target, is_free, rel_to_root
    from .bash_targets import extract_bash_targets
    log_path = pathlib.Path(root).absolute() / log_file()
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    lines = lines[-_SESSION_TAIL_LINES:]

    # Touches before the newest session_start marker belong to an older session.
    offset = 0
    for i, line in enumerate(lines):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("type") == _SESSION_MARKER_TYPE:
            offset = i + 1

    touched: set[str] = set()
    for line in lines[offset:]:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict) or "tool" not in rec:
            continue  # session_start / session_stop markers carry no tool input
        tool = str(rec.get("tool", ""))
        tool_input = rec.get("input", {})
        if not isinstance(tool_input, dict):
            continue
        if tool in ("file_editor", "notebook_editor"):
            path = tool_input.get("path", "") or tool_input.get("file_path", "")
            if path and "$" not in str(path):
                rel = rel_to_root(root, str(path))
                if rel and not is_free(rel) and is_code_target(rel):
                    touched.add(rel)
        elif tool == "terminal":
            command = tool_input.get("command", "") or tool_input.get("cmd", "")
            if command and "$" not in str(command):
                for target in extract_bash_targets(str(command)):
                    rel = rel_to_root(root, str(target))
                    if rel and not is_free(rel) and is_code_target(rel):
                        touched.add(rel)
    return sorted(touched)


# --- GRP plan visibility (E-066) --------------------------------------------
# Report-only: the session close names how much of the operation's declared
# Implementation Plan is on disk. The plan layer is GATE-FREE on this path by
# design (stop never blocks and must not pay an HMAC verify), so an undecided
# record's progress is reported as progress — approval stays the guard's job
# at write time. Both caps keep a large session's report a nudge, not a
# directory listing.
_PLAN_PROGRESS_FILES = 20      # touched files inspected for a covering plan
_PLAN_PROGRESS_RECORDS = 3     # covering records named in the note


def _plan_progress_note(root: str, writes: list[str]) -> str:
    """One-line GRP progress note, or '' when no plan covers this session.

    Fail-open at every step: a missing engine module, an unreadable record or
    a missing records dir yields '' — the report simply carries one note less.
    """
    if not writes:
        return ""
    try:
        from . import plan as plan_mod
    except Exception:
        return ""
    recs_dir = pathlib.Path(root) / "docs" / "experiments"
    if not recs_dir.is_dir():
        return ""
    seen: list[str] = []
    for rel in writes[:_PLAN_PROGRESS_FILES]:
        try:
            rec = plan_mod.active_plan_for(rel, str(recs_dir))
        except Exception:
            continue
        if rec and rec not in seen:
            seen.append(rec)
        if len(seen) >= _PLAN_PROGRESS_RECORDS:
            break
    parts: list[str] = []
    for rec in seen:
        try:
            # Shared formatter (E-068): the SAME open_plan_line the SessionStart
            # context and the orient/board digest print, so the three surfaces
            # can never disagree — and both name the record and its counts.
            line = plan_mod.open_plan_line(rec, root)
        except Exception:
            line = None
        if line:
            parts.append(line)
    if not parts:
        return ""
    return ("plan progress (GRP): " + "; ".join(parts)
            + " — extend an operation's plan with "
              "run_experiment.py --amend-plan.")


def record_session_start(root: str) -> None:
    """Append a session_start marker (called by the audit hook on SessionStart).

    Carries a timestamp so the report can tell stale sprint-status leftovers
    from this session's stories, and so the touched-set starts after this line.
    """
    from .config import log_file
    import uuid

    # Generate unique session_id (timestamp + uuid for collision avoidance)
    session_id = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"

    log_path = pathlib.Path(root).absolute() / log_file()
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": _SESSION_MARKER_TYPE,
                                "hook_event": "SessionStart",
                                "session_id": session_id,
                                "timestamp": time.time()},
                               ensure_ascii=False) + "\n")
    except OSError:
        pass


def _record_session_stop_marker(root: str) -> None:
    """Append a session_stop marker to the audit log (explicit session-end marker)."""
    from .config import log_file
    log_path = pathlib.Path(root).absolute() / log_file()
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            event = {"type": "session_stop",
                     "hook_event": "Stop",
                     "timestamp": time.time()}
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError:
        pass


def stop(json_in: dict) -> dict:
    """Stop hook: always allow; report what is left open.

    stop_hook_active re-fire (Claude re-invokes Stop after feedback) closes
    immediately with NO reason and NO side effects: any feedback string here
    (reason → additionalContext) makes Claude continue the turn, which fires
    Stop again, which reports again — an infinite loop. The guard message in
    that loop says it explicitly: check stop_hook_active and return success
    while it is true. No deny path exists — a hook that can never block can
    never wedge the session.
    """
    # Loop-safe fast exit FIRST, before any I/O: a re-fired Stop must not
    # append another session_stop marker nor return feedback.
    try:
        if isinstance(json_in, dict) and json_in.get("stop_hook_active"):
            return {"decision": "allow"}
    except Exception:
        pass

    root = repo_root(json_in)

    # Explicit session-end marker (fire-and-forget, fail-open).
    _record_session_stop_marker(root)

    notes: list[str] = []
    try:
        stories = _in_progress_stories(root)
    except Exception:
        stories = []
    if stories:
        shown = ", ".join(stories[:5])
        if len(stories) > 5:
            shown += f", … (+{len(stories) - 5} more)"
        notes.append(f"in-progress stories: {shown}.")
    try:
        writes = _session_code_writes(root)
    except Exception:
        writes = []
    if writes:
        shown = ", ".join(writes[:5])
        if len(writes) > 5:
            shown += f", … (+{len(writes) - 5} more)"
        # "this session" ≠ "this turn": the set spans the whole session, and a
        # write made hours ago is still listed. Say so, and say the report is
        # informational — the guard already validated each write at write time,
        # so an agent reads this as a wrap-up nudge instead of something to fix
        # (the graph-engineering-arge session misread it as "code written this
        # turn" and denied writing any code).
        notes.append(f"code written earlier this session: {shown} "
                     f"(informational — guard already validated each write at "
                     f"write time; nothing to fix here).")

    # GRP plan progress (E-066, informational): how much of the operation's
    # declared Implementation Plan is on disk. Report-only — the guard owns
    # approval at write time; this note exists so a multi-file operation is
    # visible at the session close instead of only inside the record.
    try:
        plan_note = _plan_progress_note(root, writes)
    except Exception:
        plan_note = ""
    if plan_note:
        notes.append(plan_note)

    # PROACTIVE relay nudges (report-only): waiting hand-off signals
    # addressed to METHODOLOGY_CHAIN stages mean an upstream run finished
    # its work but nobody picked up the baton — surface them as a nudge,
    # never a block (stop cannot deny; the addressed skill completes the
    # shake via its peek + consume). Receivers filter by construction:
    # only methodology stages are peeked here, so tool-relay (bmad-ux …)
    # and bmad-help signals stay out of the stop report, and a session's
    # own stop-channel alerts (kind "stop") are not hand-offs at all.
    # The full waiting table lives in chain-health / doctor --json.
    try:
        from .blackboard import METHODOLOGY_CHAIN, pending_handoffs
        routed: dict = {}
        for skill in METHODOLOGY_CHAIN:
            signals = pending_handoffs(root, skill)
            if signals:
                routed[skill] = len(signals)
        if routed:
            waiting_s = ", ".join(f"{s} ({n})" for s, n in routed.items())
            notes.append(
                f"PROACTIVE — hand-off waiting: {waiting_s}: "
                f"{sum(routed.values())} unclaimed signal(s) from completed "
                f"upstream runs — the designed post-close state, not a "
                f"failure; a run finished its work but nobody picked "
                f"up the baton — peek `handoffs --skill <self>`, then consume "
                f"the channel (doctor --json lists every waiting hop; correct "
                f"a note with handoff-replace, never consume-then-repost).")
    except Exception:
        pass

    # Subscription-plane delivery surface (BLACKBOARD.md §5: "stop → surfaced
    # at stop"). Report-only like every stop note — nothing is consumed here:
    # the producer/watcher clears the channel with `consume --channel stop`,
    # so an unconsumed routed alert keeps surfacing until its consumer takes
    # it (deliver-once belongs to the consumer, not the announcer).
    try:
        from .blackboard import pending_alerts
        stop_alerts = pending_alerts(root, "stop")
        if stop_alerts:
            first = str(stop_alerts[0].get("text", ""))[:160]
            more = f" (+{len(stop_alerts) - 1} more)" if len(stop_alerts) > 1 else ""
            notes.append(f"routed alert(s) on the stop channel: {first}{more} — "
                         f"clear with `blackboard.py consume --channel stop`.")
    except Exception:
        pass

    # Workflow progress (informational): active or blocked workflow runs from advisory
    try:
        plugin_root = str(pathlib.Path(__file__).resolve().parents[3])
        if plugin_root not in sys.path:
            sys.path.insert(0, plugin_root)
        from bmad.workflow import advisory
        wf_peek = advisory.peek(root)
        if wf_peek:
            notes.append(f"active workflow: {wf_peek}.")
    except Exception:
        pass

    if notes:
        session_key = _session_report_key(_latest_session_marker(root))
        try:
            if _report_already_emitted(root, session_key):
                return {"decision": "allow"}  # already nudged this session
        except Exception:
            pass
        _record_stop_report(root, session_key)
        return {"decision": "allow", "reason": "Stop report — " + " ".join(notes)}
    return {"decision": "allow"}

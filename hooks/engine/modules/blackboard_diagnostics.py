"""Read-only board diagnostics and explicit lifecycle alert posting."""

from __future__ import annotations

# --- doctor (one-glance diagnostic) ---------------------------------------------------
_DOCTOR_CAP_WARN_PCT = 90  # warn when a plane is this close to its cap

def _doctor_age(ts: float) -> str:
    """Human age of a timestamp ('2s', '5m', '3h', '6d')."""
    from . import blackboard as _board_api
    time = _board_api.time
    age = int(max(0, time.time() - (ts or 0)))
    if age < 60:
        return f"{age}s"
    if age < 3600:
        return f"{age // 60}m"
    if age < 86400:
        return f"{age // 3600}h"
    return f"{age // 86400}d"

def _doctor_drift(paths: dict, board: dict) -> dict:
    """Snapshot vs event-log replay comparison (tool-stamped keys excluded:
    last_tool.* are event-sourced like everything else; excluded here because
    they are high-churn skill-side touch stamps, not meaningful state for
    drift)."""
    from . import blackboard as _board_api
    _replay_events = _board_api._replay_events
    replay = _replay_events(paths)

    def norm(b: dict) -> dict:
        keys = {k: v for k, v in b["keys"].items() if v.get("type") != "tool"}
        return {"keys": keys, "tags": b["tags"], "canvases": b["canvases"],
                "consumed": b.get("consumed", {}),
                "open_handoffs": b.get("open_handoffs", {}),
                "links": b["links"], "subscriptions": b["subscriptions"],
                "alerts": b["alerts"], "hot": b["hot"],
                "hot_canvas": b["hot_canvas"]}

    in_sync = norm(replay) == norm(board)
    return {"in_sync": in_sync, "status": "ok" if in_sync else "warn"}

def doctor(project_root: str, *, post: bool = False) -> dict:
    """One-glance diagnostic for the whole board: gate, snapshot, event log,
    state counts, cap usage, focus, chain health, FRESHNESS, integrity
    (snapshot/event drift, tmp residue) and watch paths. Verdict is HEALTHY iff
    no warnings.

    NEW: Supports --rotate flag to archive old event logs (MEDIUM #1)
    NEW: Includes record ID uniqueness check (MEDIUM #2 / ISSUE #61)
    NEW: Detects stale sessions (SessionStart without Stop) (MEDIUM #5 / ISSUE #64)
    NEW (E-053): reports the derived freshness of every waiting claim, and — when
    `post=True` — PRODUCES the lifecycle alerts the board's taxonomy declares
    (`witness_stale`, `cascade_invalidation`, `stale_session`). The default stays
    read-only: a diagnostic must not mutate the thing it measures.
    """
    from . import blackboard as _board_api
    MAX_ALERTS = _board_api.MAX_ALERTS
    MAX_CANVASES = _board_api.MAX_CANVASES
    MAX_CELLS = _board_api.MAX_CELLS
    MAX_CONTRIBUTIONS = _board_api.MAX_CONTRIBUTIONS
    MAX_EVENTS = _board_api.MAX_EVENTS
    MAX_KEYS = _board_api.MAX_KEYS
    MAX_LINKS = _board_api.MAX_LINKS
    MAX_SUBSCRIPTIONS = _board_api.MAX_SUBSCRIPTIONS
    MAX_TAGS = _board_api.MAX_TAGS
    _replay_events = _board_api._replay_events
    _doctor_age = _board_api._doctor_age
    _doctor_drift = _board_api._doctor_drift
    _post_lifecycle_alerts = _board_api._post_lifecycle_alerts
    board_paths = _board_api.board_paths
    chain_health = _board_api.chain_health
    fr = _board_api.fr
    json = _board_api.json
    os = _board_api.os
    pathlib = _board_api.pathlib
    pending_handoff_freshness = _board_api.pending_handoff_freshness
    re = _board_api.re
    read_board = _board_api.read_board
    time = _board_api.time
    waiting_hop_rows = _board_api.waiting_hop_rows
    stale_session_hours: int | None = None
    paths = board_paths(project_root)
    warnings = []
    board = read_board(project_root)

    # gate
    gate_known, gate_on = True, True
    try:
        from .config import blackboard_enabled
        gate_on = bool(blackboard_enabled())
    except Exception:
        gate_known = False

    # event log
    lines = garbage = 0
    try:
        with open(paths["events"], encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                lines += 1
                try:
                    json.loads(line)
                except ValueError:
                    garbage += 1
        events_exist = lines > 0
    except OSError:
        events_exist = False
    if garbage:
        warnings.append(f"event log carries {garbage} unparseable line(s) "
                        "(tolerated, but investigate the writer)")
    # Rotation hint (MEDIUM #1 / ISSUE #60 surface): _truncate_events_if_needed
    # silently keeps the newest MAX_EVENTS lines at every mutation; the
    # operator-facing surface for "the log hit its bound and history was
    # dropped" is this warning, pointing at `rotate` (archive + fresh log).
    if events_exist and lines >= MAX_EVENTS:
        from .utils import plugin_script_cmd as _plugin_cmd
        warnings.append(f"event log at bound ({lines}/{MAX_EVENTS} lines) — "
                        "oldest events were already dropped by the truncation "
                        f"guard; archive history with "
                        f"`{_plugin_cmd('bmad/scripts/blackboard.py', 'rotate')}`")

    # caps
    cells_per_canvas = {name: len(cv.get("cells", {}))
                        for name, cv in board["canvases"].items()}
    caps = {
        "keys": (len(board["keys"]), MAX_KEYS),
        "tags": (len(board["tags"]), MAX_TAGS),
        "contributions": (len(board["contributions"]), MAX_CONTRIBUTIONS),
        "canvases": (len(board["canvases"]), MAX_CANVASES),
        "cells": (sum(cells_per_canvas.values()), MAX_CELLS * MAX_CANVASES),
        "links": (len(board["links"]), MAX_LINKS),
        "subscriptions": (len(board["subscriptions"]), MAX_SUBSCRIPTIONS),
        "alerts": (len(board["alerts"]), MAX_ALERTS),
    }
    cap_warnings = []
    for name, (used, cap) in caps.items():
        if used * 100 >= _DOCTOR_CAP_WARN_PCT * cap:
            cap_warnings.append(f"{name} {used}/{cap} (≥{_DOCTOR_CAP_WARN_PCT}% — "
                                "oldest will expire soon)")
    warnings.extend(cap_warnings)

    # NEW: Check if snapshot rebuild from events works (corruption recovery)
    replay_for_check = _replay_events(paths)
    skipped = replay_for_check.get("_metadata", {}).get("replay_skipped_lines", 0)
    if skipped > 0:
        warnings.append(f"event log replay skipped {skipped} line(s) during rebuild "
                        "(corruption detected, but auto-recovered by skipping bad lines)")

    # NEW: Check for duplicate record IDs (MEDIUM #2 / ISSUE #61)
    # Record IDs live in filenames (E-001.md, IR-002.md, …) scattered across
    # the canonical layout (check-methodology.sh CHECK 2):
    #   E → docs/experiments;  IR/SP/S/PR → docs/development;
    #   S → docs/development/stories;  QR → docs/quality.
    # The old per-type table pointed at directories that don't exist
    # (docs/stories, docs/quality-records, …), so this scan silently never
    # ran. Scanning the real layout catches a same-ID copy under the wrong
    # directory (an archive subdir, a second artifacts tree, the legacy
    # docs/stories location) — the duplicate shapes that actually occur.
    root = os.path.abspath(project_root or os.getcwd())
    seen_ids: dict[str, str] = {}  # record ID → first directory it was seen in
    scan_dirs = [
        "docs/experiments", "docs/development", "docs/development/stories",
        "docs/quality",
    ]

    for rec_dir in scan_dirs:
        rec_path = pathlib.Path(root) / rec_dir
        if not rec_path.exists():
            continue

        for record_file in rec_path.glob("*.md"):
            match = re.match(r"^([A-Z]+-\d+)\.md$", record_file.name)
            if not match:
                continue
            record_id = match.group(1)

            if record_id in seen_ids:
                warnings.append(
                    f"Duplicate record ID '{record_id}' detected in {rec_dir} "
                    f"(first seen in {seen_ids[record_id]}; record IDs must be "
                    f"globally unique per type)"
                )
            else:
                seen_ids[record_id] = rec_dir

    # NEW: Detect stale sessions (MEDIUM #5 / ISSUE #64)
    # Look for SessionStart without corresponding Stop in recent history
    try:
        last_session_start = None
        last_session_stop = None
        last_session_id = ""
        session_count = 0
        current_session_id = ""

        with open(paths["events"], "r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue

                entry_type = entry.get("type", "")
                if entry_type == "session_marker":
                    if entry.get("hook_event") == "SessionStart":
                        last_session_start = entry.get("ts", 0.0)
                        last_session_id = entry.get("session_id", "")
                        current_session_id = last_session_id
                        session_count += 1

                elif entry_type == "session_stop":
                    last_session_stop = entry.get("ts", 0.0)

        # Check if there's an unclosed session
        if last_session_start and (last_session_stop is None or last_session_start > last_session_stop):
            session_age = time.time() - last_session_start
            stale_threshold = 3600  # 1 hour

            if session_age > stale_threshold:
                hours = int(session_age / 3600)
                stale_session_hours = hours
                warnings.append(
                    f"Stale session detected: SessionStart {hours}h+ ago without Stop "
                    f"(hung session? agent crash?)"
                )

        # NEW: Check multi-session isolation (MEDIUM #6 / ISSUE #65)
        # If more than one session, track session boundaries
        if session_count > 1:
            warnings.append(
                f"Multi-session log detected: {session_count} sessions tracked "
                f"(use session_id for isolation). Current: {current_session_id[:12]}..."
            )
    except Exception:
        pass  # Can't check session history, skip

    # integrity: snapshot presence + drift
    snapshot_exists = os.path.exists(paths["snapshot"])
    drift = _doctor_drift(paths, board)
    if not drift["in_sync"]:
        warnings.append("snapshot drifts from the event log — delete "
                        "blackboard.json to force a rebuild")

    # tmp residue (crash leftovers, snapshot dir + event-log dir)
    tmp_files: list[str] = []
    try:
        for d in (paths["dir"], os.path.dirname(paths["events"])):
            if os.path.isdir(d):
                tmp_files.extend(os.path.join(d, f) for f in os.listdir(d)
                                 if f.endswith(".tmp"))
    except OSError:
        pass
    if tmp_files:
        warnings.append(f"{len(tmp_files)} leftover .tmp file(s) under "
                        f"{paths['dir']} — safe to delete")

    # chain (delivery + methodology relays + every sub-chain).
    # A waiting baton is the designed post-close state, NOT a defect: a close
    # that hands off downstream always leaves one unclaimed signal, so counting
    # it into the verdict made every healthy close read NEEDS ATTENTION (real
    # opencode session, 2026-09-30). It is surfaced three ways instead: the
    # informational warnings list (read-only report), checks.chain (status:
    # "signal" with its own verdict), and the skill close-step text that names
    # unclaimed signals explicitly. Waiting-signal warnings therefore do NOT
    # poison the verdict — only genuine health findings do. Stale (> TTL) stays
    # a health warning: a >24h baton means a crashed/hung skill.
    chain = chain_health(project_root)
    chain_warnings: list = []  # signal-state warnings — informational, not health
    if chain["total_waiting"] > 0:
        waiting_hops = [f"{h['from']}->{h['to']}" for h in waiting_hop_rows(chain)]
        chain_warnings.append(f"{chain['total_waiting']} unclaimed hand-off signal(s) "
                              f"({', '.join(waiting_hops)}) -- PROACTIVE, see chain-health")

    # Stale handoff escalation: folded-snapshot ages (TD-014 — the snapshot
    # is the live truth skills can claim; a log-only age stages a
    # divergence, not staleness). Oldest-first so operators triage the
    # longest-waiting baton first. The diagnosis is split by DERIVED verdict
    # (E-053): a >24h baton whose claim still holds is a crashed/hung skill,
    # while one whose scope changed is obsolete WORK — the engine used to call
    # both "hung skill" and so mis-prescribed the remedy for the second.
    stale = chain.get("stale", [])
    if stale:
        stale_sorted = sorted(stale, key=lambda s: -s.get("age_seconds", 0))
        oldest = stale_sorted[0]
        obsolete = [s for s in stale
                    if str(s.get("verdict", "")).upper() == fr.STALE]
        if obsolete:
            warnings.append(
                f"{len(stale)} STALE hand-off signal(s) (> 24h old) — "
                f"{len(obsolete)} name decisions the code has already moved "
                f"past (obsolete work, not a hung skill); oldest: "
                f"{oldest['from']}->{oldest['to']} — retire them "
                f"(`handoff --retire <key> --reason \"…\"`) or re-post, then "
                f"consume the channel")
        else:
            warnings.append(f"{len(stale)} STALE hand-off signal(s) "
                            f"(> 24h old, claims still current) -- skill "
                            f"crashed/hung? oldest: {oldest['from']}->"
                            f"{oldest['to']} -- claim via handoffs --skill "
                            f"<downstream>, then consume")

    # Focus liveness: a hot key with a finished bridge `status` is the stale
    # hand-off this engine used to hand bmad-help (2026-09-21 real session:
    # status="complete" while hot="ux.mailjs" and discovery in flight).
    # write_key now corrects exactly this on the hot write, so the warning
    # catches boards written before that (or by a hand-edited event log).
    # An unset status is no claim at all — never warn on it.
    bridge_entry = board["keys"].get("status")
    bridge_status = str(bridge_entry.get("value", "")).strip() \
        if isinstance(bridge_entry, dict) else ""
    focus_stale = bool(board.get("hot")) and bridge_status.lower() in (
        "complete", "done")
    if focus_stale:
        warnings.append(
            f"focus says '{bridge_status}' but '{board['hot']}' is hot — the "
            f"live run never marked itself in-progress (write `--key status "
            f"--value in-progress`); readers route on status"
        )

    # Blocked-run tag (blackboard full-integration P1): `blocked` is the
    # stalls-the-run marker from the tag vocabulary (GUIDE §14). A board
    # carrying it names its hot run so the operator triages the stall
    # instead of re-deriving state. Informational severity stays with the
    # verdict logic (any warning → NEEDS ATTENTION).
    if "blocked" in (board.get("tags") or []):
        _hot_name = board.get("hot") or "(no hot focus)"
        warnings.append(
            f"board tagged 'blocked' — run '{_hot_name}' is stalled; "
            f"clear with `untag --tag blocked` once it moves"
        )

    # Graph orphans (blackboard full-integration, informational only): a link
    # endpoint with no key or canvas on the board. A forward edge legitimately
    # precedes its downstream run key (the run lands at the next close-out),
    # so — like waiting batons (SIGNAL) — this never touches the verdict; it
    # names the endpoints so the operator can tell a pending downstream apart
    # from a mis-slugged key (e.g. a literal `<slug>` pasted from a template).
    # Bounded to keep doctor bounded.
    _nodes = set(board["keys"])
    _nodes.update(board["canvases"])
    _nodes.update(f"canvas:{n}" for n in board["canvases"])
    _orphans: list = []
    for _l in board["links"]:
        for _end in (_l.get("a"), _l.get("b")):
            if _end and _end not in _nodes and _end not in _orphans:
                _orphans.append(_end)

    # watch paths (informational: missing prefixes may be created later)
    watch = [{"canvas": name, "path": w, "exists": os.path.exists(w)}
             for name, cv in board["canvases"].items()
             for w in cv.get("watch", [])]

    # methodology relay progress (informational: which E→IR→SP→S→QR→PR
    # stages stamped their run key — empty = relay not started on this board)
    methodology = {}
    for stage, key in (("E", "methodology.last_experiment"),
                       ("IR", "methodology.last_ir"),
                       ("SP", "methodology.last_sp"),
                       ("S", "methodology.last_story"),
                       ("QR", "methodology.last_qr"),
                       ("PR", "methodology.last_pr")):
        entry = board["keys"].get(key)
        if entry and isinstance(entry, dict) and str(entry.get("value", "")).strip():
            methodology[stage] = str(entry.get("value"))[:80]

    # Freshness plane (E-053): the DERIVED verdict of every waiting claim, plus
    # the claims explicitly withdrawn. Informational by itself — a signal whose
    # scope moved is report-only noise until it also ages out (the health case
    # above). This is also the reader that turns the declared-but-dead alert
    # taxonomy into produced data: doctor --post emits the lifecycle kinds.
    freshness_report = {}
    unclear = chain.get("unclear", [])
    retired_count = len(board.get("retired") or {})
    try:
        freshness_report = pending_handoff_freshness(project_root)
    except Exception:
        freshness_report = {}
    fresh_tally = fr.tally(unclear)
    # `[freshness] unknown_policy = "silent"` keeps "unverifiable" out of the
    # warnings (a legacy corpus has nothing else to report) while the tally
    # keeps counting it — the fact is never lost, only not repeated.
    report_unknown = fr.unknown_policy() != "silent"
    if fresh_tally[fr.STALE] or (fresh_tally[fr.UNKNOWN] and report_unknown):
        counts = f"{fresh_tally[fr.STALE]} stale"
        tail = ""
        if report_unknown:
            counts += f", {fresh_tally[fr.UNKNOWN]} unknown"
            tail = " or cannot be anchored"
        chain_warnings.append(
            f"{len(unclear)} waiting signal(s) name decisions the code has "
            f"moved past{tail} ({counts}) — verify, then retire or re-post "
            f"(checks.freshness.inject)")
    stale_verdicts = [s for s in stale
                      if str(s.get("verdict", "")).upper() == fr.STALE]

    checks = {
        "gate": {"known": gate_known, "on": gate_on,
                 "status": "ok" if gate_known else "info"},
        "snapshot": {"exists": snapshot_exists,
                     "version": board.get("version"),
                     "age": _doctor_age(board.get("updated", 0.0)),
                     "status": "ok"},
        "events": {"exists": events_exist, "lines": lines, "garbage": garbage,
                   "status": "ok" if not garbage else "warn"},
        "caps": {"usage": {k: {"used": u, "cap": c}
                           for k, (u, c) in caps.items()},
                 "status": "ok" if not cap_warnings else "warn"},
        "focus": {"hot": board.get("hot"), "hot_canvas": board.get("hot_canvas"),
                  "bridge_status": bridge_status,
                  "status": "warn" if focus_stale else "ok"},
        "drift": drift,
        "residue": {"tmp_files": tmp_files,
                    "status": "ok" if not tmp_files else "warn"},
        "chain": {"total_waiting": chain["total_waiting"],
                  "waiting_hops": waiting_hop_rows(chain),
                  "stale": chain.get("stale", []),
                  # "signal" = waiting batons exist (designed post-close state,
                  # announce-only); "warn" = STALE ones exist (crashed skill).
                  "verdict": ("HEALTHY" if not chain_warnings and not stale
                              else "NEEDS ATTENTION" if stale else "SIGNAL"),
                  "status": ("ok" if not chain_warnings and not stale
                             else "warn" if stale else "signal")},
        "watch": {"paths": watch, "status": "info" if watch else "ok"},
        "graph": {"edges": len(board["links"]), "orphans": _orphans[:10],
                  "status": "info"},
        "methodology": {"progress": methodology,
                        "status": "info" if methodology else "ok"},
        "freshness": {"mode": fr.mode(),
                      "witnesses": len(board.get("witness") or {}),
                      "retired": retired_count,
                      "report": freshness_report,
                      "unclear": unclear[:10],
                      "tally": fresh_tally,
                      # A >24h signal that is ALSO stale by code state is a
                      # health finding (its remedy is retire/re-post, not
                      # "restart the skill"); otherwise freshness is info.
                      "status": ("warn" if stale_verdicts
                                 else "info" if unclear else "ok")},
    }
    report = {"ok": True, "root": paths["root"],
              "verdict": "HEALTHY" if not warnings else "NEEDS ATTENTION",
              "checks": checks,
              "warnings": warnings,
              # Signal-state findings (waiting batons): report-only, never part
              # of the health verdict. The close-step contract surfaces these
              # alongside the verdict instead of reading NEEDS ATTENTION as
              # "the close failed".
              "signal_warnings": chain_warnings}
    if post:
        report["posted"] = _post_lifecycle_alerts(project_root, unclear,
                                                  stale_session_hours)
    return report

def _post_lifecycle_alerts(project_root: str, unclear: list,
                           stale_session_hours: int | None) -> int:
    """Produce the lifecycle alerts doctor detected (bounded, deduped, fail-open).

    Split out so the default diagnostic stays read-only: only an explicit
    `doctor --post` writes. Every kind emitted here already has a consumer —
    the session-start inject and `alerts --channel session` — which is what
    makes the board's declared taxonomy a working plane instead of dead names
    (E-053, F-06). Bounded to 5 signals so a flooded board cannot evict real
    batons from the shared 32-slot alert pool.
    """
    from . import blackboard as _board_api
    AlertKind = _board_api.AlertKind
    fr = _board_api.fr
    post_alert = _board_api.post_alert
    posted = 0
    try:
        for sig in (unclear or [])[:5]:
            if str(sig.get("verdict", "")).upper() != fr.STALE:
                continue
            kind = (AlertKind.CASCADE_INVALIDATION.value
                    if "superseded by" in str(sig.get("reason", ""))
                    else AlertKind.WITNESS_STALE.value)
            post_alert(
                project_root, "session", kind,
                f"{sig.get('run_key') or sig.get('from')}: {sig.get('reason')} "
                f"(waiting for {sig.get('to')}, "
                f"{int(sig.get('age_seconds') or 0) // 3600}h) — retire or re-post",
                sender="doctor")
            posted += 1
        if stale_session_hours:
            post_alert(
                project_root, "session", AlertKind.STALE_SESSION.value,
                f"stale session: SessionStart {stale_session_hours}h+ ago "
                f"without Stop (hung session? agent crash?)", sender="doctor")
            posted += 1
    except Exception:
        pass
    return posted

"""Handoff and signal delivery for the blackboard.

The public delegates remain in ``blackboard`` and bind its current
helpers on each call, retaining compatibility for consumers and focused
test seams.
"""

from __future__ import annotations

def post_handoff(project_root: str, to_skill: str, from_key: str, note: str = "",
                 *, sender: str | None = None) -> dict:
    """Upstream→downstream hand-off signal: route an alert into the
    `handoff.<to_skill>` channel (kind `handoff`, text `<from_key>: <note>`).
    The signal waits there until the downstream skill consumes it — that
    consumption completes the handshake.

    `sender` is optional but authoritative: pass it whenever the run key's
    namespace is shared by two stages (`story.` is written by BOTH
    create-story and dev-story), so the diagnostic attributes the baton to the
    stage that actually posted it instead of guessing from the prefix."""
    from . import blackboard as _board_api
    MAX_CHANNEL_LEN = _board_api.MAX_CHANNEL_LEN
    post_alert = _board_api.post_alert
    to_skill = str(to_skill).strip()[:MAX_CHANNEL_LEN - len("handoff.")]  # channel budget
    if not to_skill:
        return {"ok": False, "error": "empty --to"}
    from_key = str(from_key).strip()[:200]
    if not from_key:
        return {"ok": False, "error": "empty --from-key"}
    return post_alert(project_root, f"handoff.{to_skill}", "handoff",
                      f"{from_key}: {str(note).strip()}", sender=sender)

def replace_handoff(project_root: str, to_skill: str, from_key: str, note: str = "",
                    *, sender: str | None = None) -> dict:
    """Correct the note on a WAITING hand-off signal without dropping it.

    A hand-off is a bridge, not a message: consuming it to "fix a typo in the
    note" clears the waiting signal, and downstream readers that peeked in the
    gap saw a relay that no longer exists (real opencode session, 2026-09-30:
    a close-out note with a drafting typo was corrected by consume → repost,
    which silently dropped the signal for everyone reading between the two
    calls). This replaces the alert text in place — the signal keeps its
    original ts (so staleness/age stays truthful), stays in its channel, and
    never disappears. The replaced alert id is stable (same id, new text), so
    snapshot folds and dedup stay consistent.

    No waiting signal from `from_key` → posts a fresh one (first-write
    semantics, identical to post_handoff), so a correction to an already-
    consumed handshake cannot resurrect the consumed bridge. Returns the same
    ack shape as post_handoff plus `"replaced": True|False`.
    """
    from . import blackboard as _board_api
    MAX_CHANNEL_LEN = _board_api.MAX_CHANNEL_LEN
    _SENDER_RE = _board_api._SENDER_RE
    _append_event = _board_api._append_event
    _apply_event = _board_api._apply_event
    _mutate = _board_api._mutate
    board_paths = _board_api.board_paths
    post_alert = _board_api.post_alert
    read_board = _board_api.read_board
    time = _board_api.time
    to_skill = str(to_skill).strip()[:MAX_CHANNEL_LEN - len("handoff.")]  # channel budget
    if not to_skill:
        return {"ok": False, "error": "empty --to"}
    from_key = str(from_key).strip()[:200]
    if not from_key:
        return {"ok": False, "error": "empty --from-key"}
    channel = f"handoff.{to_skill}"
    new_text = f"{from_key}: {str(note).strip()}"
    waiting = [a for a in read_board(project_root)["alerts"]
               if a.get("channel") == channel and a.get("kind") == "handoff"
               and str(a.get("text", "")).startswith(f"{from_key}:")]
    if not waiting:
        # No waiting signal from this key: a correction is a first write.
        # (A consumed handshake stays consumed — never resurrect it.)
        ack = post_alert(project_root, channel, "handoff", new_text, sender=sender)
        ack["replaced"] = False
        return ack

    def mut(board):
        replaced = 0
        for a in board["alerts"]:
            if (a.get("channel") == channel and a.get("kind") == "handoff"
                    and str(a.get("text", "")).startswith(f"{from_key}:") and a.get("id")):
                ev = {"event": "alert-edit", "id": a["id"], "channel": channel,
                      "kind": "handoff", "text": new_text, "ts": time.time()}
                if sender and _SENDER_RE.match(str(sender).strip()):
                    ev["sender"] = str(sender).strip()
                _append_event(board_paths(project_root), ev)
                _apply_event(board, ev)
                replaced += 1
        return board, {"ok": replaced > 0, "replaced": True, "count": replaced,
                       "channel": channel,
                       "root": board_paths(project_root)["root"]}

    return _mutate(project_root, mut)

# --- freshness annotation of waiting signals -------------------------------------
# ONE scan, ONE derivation. pending_handoffs, pending_handoff_channels,
# pending_handoff_freshness, recent_handoffs, chain_health, doctor and the
# session-start inject all read this, because a second parser is a second
# opinion about whether a claim still holds (the project's own doctrine).
def _routed_skills() -> set:
    """Every known receiving skill (delivery + methodology + sub-chains)."""
    from . import blackboard as _board_api
    CHAIN = _board_api.CHAIN
    CHAIN_ADJACENT = _board_api.CHAIN_ADJACENT
    CHAIN_TERMINAL = _board_api.CHAIN_TERMINAL
    METHODOLOGY_CHAIN = _board_api.METHODOLOGY_CHAIN
    sub_chain_skills = _board_api.sub_chain_skills
    return (set(CHAIN) | set(METHODOLOGY_CHAIN) | sub_chain_skills()
            | CHAIN_ADJACENT
            | {s for hop in CHAIN_TERMINAL for s in hop})

def _annotate_alert(project_root: str, alert: dict, board: dict) -> dict:
    """Derived freshness for one waiting signal — read-only, never raises.

    A stored witness (written with the signal) wins; otherwise the claim is
    dated by the commit that last touched the record its run key names. A
    retirement (of the signal, its channel, or its run key) overrides both.
    """
    from . import blackboard as _board_api
    fr = _board_api.fr
    time = _board_api.time
    aid = str(alert.get("id", ""))
    channel = str(alert.get("channel", ""))
    key = fr.key_of(alert.get("text", ""))
    markers = board.get("retired") or {}
    retired = (aid in markers or channel in markers or bool(key and key in markers))
    stored = (board.get("witness") or {}).get(aid) if aid else None
    try:
        ann = fr.annotate(project_root, key, stored=stored, retired=retired)
    except Exception:
        ann = {"verdict": fr.UNKNOWN, "reason": "freshness derivation failed",
               "age_s": None, "changed": [], "changed_count": 0,
               "run_key": key, "anchor": "none", "superseded_by": None}
    if ann.get("age_s") is None:
        try:
            ann["age_s"] = max(0.0, time.time() - float(alert.get("ts", 0.0) or 0.0))
        except (TypeError, ValueError):
            ann["age_s"] = None
    if retired and ann.get("verdict") != fr.RETIRED:
        marker = markers.get(aid) or markers.get(channel) or markers.get(key) or {}
        ann = {**ann, "verdict": fr.RETIRED,
               "reason": f"retired — {marker.get('reason') or 'no longer applies'}"}
    return ann

def _waiting_signals(project_root: str, *, board: dict | None = None,
                     include_retired: bool = False) -> list:
    """Every waiting hand-off signal, annotated with its derived freshness.

    Shaped for every reader: the alert's own fields (ts/text/id/channel/sender)
    plus `skill`, `verdict`, `reason`, `age_s`, `changed_count`, `anchor`,
    `run_key`. Retired signals are dropped unless explicitly asked for — a
    withdrawn claim is not pending work.
    """
    from . import blackboard as _board_api
    _annotate_alert = _board_api._annotate_alert
    fr = _board_api.fr
    read_board = _board_api.read_board
    board = board if board is not None else read_board(project_root)
    out = []
    # One batch: N signals on one board cost ONE head/diff/status read instead
    # of N, and the cache dies with the scan so the next reader sees git as it
    # is then (a wall-clock TTL here was measured hiding a commit — see
    # freshness._BATCH).
    with fr.batch():
        for alert in board.get("alerts", []):
            if not isinstance(alert, dict) or alert.get("kind") != "handoff":
                continue
            channel = str(alert.get("channel", ""))
            if not channel.startswith("handoff."):
                continue
            ann = _annotate_alert(project_root, alert, board)
            if ann["verdict"] == fr.RETIRED and not include_retired:
                continue
            out.append({**alert, "skill": channel[len("handoff."):],
                        "verdict": ann["verdict"], "reason": ann["reason"],
                        "age_s": ann["age_s"],
                        "changed_count": ann["changed_count"],
                        "anchor": ann["anchor"], "run_key": ann["run_key"]})
    return out

def pending_handoffs(project_root: str, skill: str, *,
                     only_current: bool = False) -> list:
    """Un-consumed hand-off signals waiting for `skill` (read-only peek).

    Each entry carries the DERIVED freshness verdict (`CURRENT` / `STALE` /
    `UNKNOWN`) and the reason behind it, so a reader can say "this no longer
    applies" instead of repeating the past. `only_current=True` drops the
    claims the code state has moved past.
    """
    from . import blackboard as _board_api
    MAX_CHANNEL_LEN = _board_api.MAX_CHANNEL_LEN
    _waiting_signals = _board_api._waiting_signals
    fr = _board_api.fr
    skill = str(skill).strip()[:MAX_CHANNEL_LEN - len("handoff.")]  # mirror post_handoff's budget
    return [sig for sig in _waiting_signals(project_root)
            if sig["skill"] == skill
            and (not only_current or sig["verdict"] == fr.CURRENT)]

def pending_handoff_channels(project_root: str) -> dict:
    """Skills with waiting hand-off signals: {skill: count} (engine peek —
    session_start announces, never consumes; the skill completes the shake).

    Routed skills only: the delivery relay (CHAIN) together with its DECLARED
    terminal hops (CHAIN_TERMINAL — both ends, so the gate is routable without
    naming it here), the methodology relay (METHODOLOGY_CHAIN), every sub-chain
    member (SUB_CHAINS — discovery, alt_dev, …), the chain-adjacent surfaces
    (CHAIN_ADJACENT — help, infrastructure, the loop orchestrator's automation
    layer, the WDS backends), so a waiting signal addressed to any known skill
    surfaces instead of being silently dropped. Off-relay channels stay out of
    the session-start nudge — the skill-side CLI lists every channel: run the
    plugin's ``bmad/scripts/blackboard.py handoffs`` (without `--skill`) from
    the plugin root.

    Counts the SAME scan `pending_handoff_freshness` breaks down by verdict, so
    the count and the breakdown can never disagree. Retired signals are not
    waiting work and are excluded from both.
    """
    from . import blackboard as _board_api
    _routed_skills = _board_api._routed_skills
    _waiting_signals = _board_api._waiting_signals
    relay = _routed_skills()
    counts: dict[str, int] = {}
    for sig in _waiting_signals(project_root):
        if sig["skill"] in relay:
            counts[sig["skill"]] = counts.get(sig["skill"], 0) + 1
    return counts

def pending_handoff_freshness(project_root: str) -> dict:
    """Per-routed-channel freshness breakdown of the waiting signals.

    `{skill: {total, current, stale, unknown, oldest_age_s, stale_keys,
    unknown_keys}}` — the data the session-start inject and doctor render. The
    read that gives the delivery plane a third answer (E-052/E-053): a baton
    the code moved past is reported as such instead of as live work.
    """
    from . import blackboard as _board_api
    _routed_skills = _board_api._routed_skills
    _waiting_signals = _board_api._waiting_signals
    fr = _board_api.fr
    relay = _routed_skills()
    out: dict = {}
    for sig in _waiting_signals(project_root):
        skill = sig["skill"]
        if skill not in relay:
            continue
        entry = out.setdefault(skill, {"total": 0, "current": 0, "stale": 0,
                                       "unknown": 0, "oldest_age_s": None,
                                       "stale_keys": [], "unknown_keys": [],
                                       "freshness": []})
        entry["total"] += 1
        verdict = str(sig["verdict"]).lower()
        entry[verdict if verdict in ("current", "stale", "unknown") else "unknown"] += 1
        age = sig.get("age_s")
        if isinstance(age, (int, float)):
            if entry["oldest_age_s"] is None or age > entry["oldest_age_s"]:
                entry["oldest_age_s"] = float(age)
        key = sig.get("run_key")
        if key and sig["verdict"] in (fr.STALE, fr.UNKNOWN):
            bucket = ("stale_keys" if sig["verdict"] == fr.STALE
                      else "unknown_keys")
            if key not in entry[bucket]:
                entry[bucket].append(key)
            entry["freshness"].append(
                {"key": key, "verdict": sig["verdict"],
                 "label": fr.label(sig["verdict"], age),
                 "age_s": age, "reason": str(sig.get("reason", ""))})
    return out

def recent_handoffs(project_root: str, limit: int = 3, *,
                    only_current: bool = False,
                    with_freshness: bool = False) -> list:
    """Most recent un-consumed hand-off signals, relay-routed only (read-only).

    The engine-side carrier for the close-out relay: skills post their
    downstream baton via `mirror --to` and a later session can only see it if
    some surface re-announces it — stop/session_start announce counts at the
    edges, but mid-session every turn was silent (the 2026-09-23 mailjs
    session forgot dev-story's baton to code-review one turn after the
    close-out). Hook layer: guard/pre peeks this list and re-injects it via
    PostToolUse warnings (throttled in audit.py); orient.py folds it into the
    digest. Shaped for injection: newest first, at most `limit`, each entry
    `{skill, from, note, age_s}`. Fail-open caller side; off-relay channels
    stay out (same routing rule as pending_handoff_channels).
    """
    from . import blackboard as _board_api
    _routed_skills = _board_api._routed_skills
    _waiting_signals = _board_api._waiting_signals
    fr = _board_api.fr
    time = _board_api.time
    limit = max(1, min(int(limit), 10))
    relay = _routed_skills()
    now = time.time()
    hits = []
    for sig in _waiting_signals(project_root):
        skill = sig["skill"]
        if skill not in relay:
            continue
        if only_current and sig["verdict"] != fr.CURRENT:
            continue
        text = str(sig.get("text", ""))
        age = sig.get("age_s")
        if not isinstance(age, (int, float)):
            age = max(0.0, now - float(sig.get("ts", 0.0) or 0.0))
        hit = {
            "skill": skill,
            "from": text.split(":", 1)[0] if ":" in text else "",
            "note": text.split(":", 1)[1].strip() if ":" in text else text,
            "age_s": age,
        }
        if with_freshness:
            # The verdict rides the note too (the inject reads `note`), so a
            # stale baton cannot be re-announced as fresh work by a surface
            # that only renders the table.
            hit["verdict"] = sig["verdict"]
            hit["reason"] = sig["reason"]
        hits.append(hit)
    # Freshest baton first. Sort on the raw float — same-second batons keep
    # their post order stable (an int-rounded key would tie them and flip the
    # order); the shape rounds age_s for display.
    hits.sort(key=lambda h: h["age_s"])
    for h in hits:
        h["age_s"] = int(h["age_s"])
    return hits[:limit]

# --- chain health (hand-off diagnostics) ----------------------------------------------
# The canonical delivery relay: each hop is one skill handing off to the next.
# TOOL CHAIN: PRD → UX → Architecture → Spec → Epics → Story → Dev (7 skills,
# 6 stage-to-stage hops) — the ordered hop list chain_health reports.
CHAIN = [
    "bmad-prd", "bmad-ux", "bmad-architecture", "bmad-spec",
    "bmad-create-epics-and-stories", "bmad-create-story", "bmad-dev-story"
]

# TERMINAL HOPS: receivers that consume a relay signal and hand nothing on.
# They ARE hops (a signal waits there and is consumed there), so they must be
# declared — otherwise the terminal baton reports as off-chain 'extra' noise
# and every consumer has to bolt the receiver on by hand. Declaring them here
# (rather than appending to CHAIN) keeps the ordered relay's stage list exact
# while chain_health, the session-start router and doctor pick them up as
# first-class hops. Such chain rows carry `terminal: true`.
CHAIN_TERMINAL = [
    ("bmad-dev-story", "bmad-code-review"),
]

# METHODOLOGY CHAIN: Experiment → IR → Sprint Planning → Story → Quality Record →
# Production Readiness. Not part of the ordered hop relay — stages receive
# hand-offs by prefix attribution (table below) and surface under
# chain_health's 'extra' when they hold waiting signals. stop.py consults
# this set for chain-completion checks (single source of truth).
METHODOLOGY_CHAIN = [
    "bmad-research-experiment",                 # E (Experiment)
    "bmad-check-implementation-readiness",      # IR (Implementation Readiness)
    "bmad-sprint-planning",                     # SP (Sprint Planning)
    "bmad-create-story",                        # S (Story)
    "bmad-quality-record",                      # QR (Quality Record)
    "bmad-production-readiness",                # PR (Production Readiness)
]

# SUB-CHAINS — the phase-based modular relays.
#
# The two canonical ordered relays (CHAIN, METHODOLOGY_CHAIN) are not the whole
# hand-off contract: ideation, the alternative development branches, the QA
# feeders and the governance/retro loops each relay work between stages too.
# Every hop listed here is part of the contract, so its waiting signals MUST
# surface in chain_health/doctor and must never be reported as off-chain
# 'extra' noise (that bucket is reserved for genuinely unknown receivers).
#
# Adding a phase = adding one entry here; chain_health, doctor and the
# session-start/stop relays all follow automatically.
SUB_CHAINS: dict[str, list[tuple[str, str]]] = {
    # Discovery — raw idea to a brief/PRD input (ideation + research fan-in).
    "discovery": [
        ("bmad-forge-idea", "bmad-product-brief"),
        ("bmad-brainstorming", "bmad-product-brief"),
        ("bmad-prfaq", "bmad-prd"),
        ("bmad-product-brief", "bmad-prd"),
        ("bmad-market-research", "bmad-product-brief"),
        ("bmad-domain-research", "bmad-product-brief"),
        ("bmad-technical-research", "bmad-product-brief"),
        # CIS creative/strategy facilitators — brainstorming's siblings: they
        # shape the raw problem, so a session that produced a direction worth
        # building on feeds the brief.
        ("bmad-cis-design-thinking", "bmad-product-brief"),
        ("bmad-cis-innovation-strategy", "bmad-product-brief"),
        ("bmad-cis-problem-solving", "bmad-product-brief"),
        ("bmad-cis-storytelling", "bmad-product-brief"),
    ],
    # Alt-dev — shortcut (quick-dev) and unattended (dev-auto) implementations
    # fan in on the same formal review gate the delivery relay terminates at.
    "alt_dev": [
        ("bmad-quick-dev", "bmad-code-review"),
        ("bmad-dev-auto", "bmad-code-review"),
        # The dev persona implements too (its BRIDGE carries S→QR→PR), so it is
        # the third branch converging on the same formal review gate.
        ("bmad-agent-dev", "bmad-code-review"),
    ],
    # Testing — the TEA toolbox feeds the quality record's mechanical checks
    # (every one of these skills is a registered QR feeder: it adds evidence to
    # an existing QR, it never opens a record of its own), plus TEA Academy
    # routing the learner into the workflow chain it teaches.
    "testing": [
        ("bmad-testarch-atdd", "bmad-quality-record"),
        ("bmad-testarch-automate", "bmad-quality-record"),
        ("bmad-testarch-ci", "bmad-quality-record"),
        ("bmad-testarch-framework", "bmad-quality-record"),
        ("bmad-testarch-nfr", "bmad-quality-record"),
        ("bmad-testarch-test-design", "bmad-quality-record"),
        ("bmad-testarch-test-review", "bmad-quality-record"),
        ("bmad-testarch-trace", "bmad-quality-record"),
        ("bmad-qa-generate-e2e-tests", "bmad-quality-record"),
        # Academy close-out recommends "start with Framework setup": the
        # learner lands in the chain it just studied.
        ("bmad-teach-me-testing", "bmad-testarch-framework"),
    ],
    # Governance — the hops that CLOSE the methodology loop: a retrospective's
    # lessons become the next hypothesis, and a course correction lands on the
    # planning surface that owns the backlog.
    "governance": [
        ("bmad-retrospective", "bmad-research-experiment"),
        ("bmad-correct-course", "bmad-sprint-planning"),
    ],
    # Game Dev Suite (GDS) — the game vertical mirrors the canonical relay
    # shape (ideation fan-in → design docs → readiness → planning → dev →
    # review) with game-native artifacts, and re-uses the same patterns: QA
    # tools feed the playtest record, retro/correct-course re-enter planning,
    # the dev branches converge on the review gate.
    "gds": [
        # ideation & research fan-in on the brief (the discovery pattern)
        ("gds-domain-research", "gds-create-game-brief"),
        ("gds-brainstorm-game", "gds-create-game-brief"),
        ("gds-create-game-brief", "gds-gdd"),
        ("gds-create-narrative", "gds-gdd"),
        # design gates — GDD is primary; a formal PRD is the optional
        # alternative gate; both converge on architecture via UX.
        ("gds-gdd", "gds-ux"),
        ("gds-ux", "gds-game-architecture"),
        ("gds-prd", "gds-game-architecture"),
        # readiness → planning → dev (the IR twin and the planning relay)
        ("gds-game-architecture", "gds-check-implementation-readiness"),
        ("gds-check-implementation-readiness", "gds-sprint-planning"),
        ("gds-sprint-planning", "gds-create-story"),
        ("gds-create-story", "gds-dev-story"),
        ("gds-dev-story", "gds-code-review"),
        # QA feeders — their BRIDGE (already in the team TOMLs) adds evidence
        # to the QR record the review session owns, never a record of their
        # own: the bmad TEA feeder pattern, game-flavored. gds-playtest-plan is
        # the seventh: it plans the playtest and its findings land in the same
        # QR (its BRIDGE says so explicitly), so it signals the review too.
        ("gds-playtest-plan", "gds-code-review"),
        ("gds-test-design", "gds-code-review"),
        ("gds-test-framework", "gds-code-review"),
        ("gds-test-automate", "gds-code-review"),
        ("gds-test-review", "gds-code-review"),
        ("gds-e2e-scaffold", "gds-code-review"),
        ("gds-performance-test", "gds-code-review"),
        # governance — retro/correct-course re-enter planning (the loop,
        # game-flavored; the review gate itself is terminal like its bmad twin)
        ("gds-retrospective", "gds-sprint-planning"),
        ("gds-correct-course", "gds-sprint-planning"),
        # dev-branch variants converge on the same review gate
        ("gds-quick-dev", "gds-code-review"),
        ("gds-agent-game-dev", "gds-code-review"),
        ("gds-agent-game-solo-dev", "gds-code-review"),
        # reporter — live status next to the file-backed plan
        ("gds-sprint-status", "gds-sprint-planning"),
        # a forensic investigation's defect feeds course correction
        ("gds-investigate", "gds-correct-course"),
    ],
    # Web Design Suite (WDS) — Freya's design pipeline: setup seeds the
    # alignment sign-off, the brief flows through trigger mapping and
    # scenarios into UX design, then the numbered build phases run in order;
    # brownfield evolution re-enters at the brief (the WDS loop).
    "wds": [
        ("wds-0-project-setup", "wds-0-alignment-signoff"),
        ("wds-0-alignment-signoff", "wds-1-project-brief"),
        ("wds-1-project-brief", "wds-2-trigger-mapping"),
        ("wds-2-trigger-mapping", "wds-3-scenarios"),
        ("wds-3-scenarios", "wds-4-ux-design"),
        ("wds-4-ux-design", "wds-5-agentic-development"),
        ("wds-5-agentic-development", "wds-6-asset-generation"),
        ("wds-6-asset-generation", "wds-7-design-system"),
        # brownfield re-entry: evolution cycles inform the next brief
        ("wds-8-product-evolution", "wds-1-project-brief"),
    ],
}

# Skills that appear in no hop but are known chain-adjacent surfaces (the help
# router, the infrastructure tools, the loop orchestrator's automation layer,
# the WDS session backends). They post and relay nothing, so the hop relays
# cannot carry them — but they must NOT surface as off-chain 'extra' noise
# when a signal is (mis)addressed to them, and the lint still checks them.
CHAIN_ADJACENT = {
    "bmad-help",           # routes the user; never produces
    "bmad-loop-setup", "bmad-loop-sweep", "bmad-loop-resolve",
    "bmad-bmb-setup", "bmad-module-builder", "bmad-workflow-builder",
    "bmad-customize", "bmad-checkpoint-preview", "bmad-eval-runner",
    "memory", "sync",
    # Vertical tooling — the GDS/WDS personas that only advise (architect,
    # designer, tech-writer, freya/mimir/saga) and the documentation tools;
    # the producing GDS personas (game-dev, solo-dev) are hop members above, as
    # is the playtest-plan feeder (gds.playtest.<slug> → gds-code-review).
    "gds-agent-game-architect", "gds-agent-game-designer",
    "gds-agent-tech-writer", "gds-document-project",
    "gds-generate-project-context",
    "wds-agent-freya-ux", "wds-agent-mimir-builder", "wds-agent-saga-analyst",
}

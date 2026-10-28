"""Handoff-chain health analysis for the blackboard.

The public delegates remain in ``blackboard`` and bind its current
helpers on each call, retaining compatibility for consumers and focused
test seams.
"""

from __future__ import annotations

import re

def sub_chain_skills() -> set:
    """Every skill named by any sub-chain hop (senders and receivers)."""
    from . import blackboard as _board_api
    SUB_CHAINS = _board_api.SUB_CHAINS
    return {s for hops in SUB_CHAINS.values() for hop in hops for s in hop}

# Run-key namespace prefix → the skill that owns the hand-off (sender attribution).
_KEY_PREFIX_TO_SKILL = [
    # METHODOLOGY CHAIN prefixes
    ("E-", "bmad-research-experiment"),
    ("IR-", "bmad-check-implementation-readiness"),
    ("SP-", "bmad-sprint-planning"),
    ("S-", "bmad-create-story"),
    ("QR-", "bmad-quality-record"),
    ("PR-", "bmad-production-readiness"),
    # DISCOVERY CHAIN prefixes
    ("forge.", "bmad-forge-idea"),
    ("brainstorm.", "bmad-brainstorming"),
    ("brief.", "bmad-product-brief"),
    ("prfaq.", "bmad-prfaq"),
    ("research.market.", "bmad-market-research"),
    ("research.domain.", "bmad-domain-research"),
    ("research.tech.", "bmad-technical-research"),
    ("cis.design.", "bmad-cis-design-thinking"),
    ("cis.innovation.", "bmad-cis-innovation-strategy"),
    ("cis.solving.", "bmad-cis-problem-solving"),
    ("cis.story.", "bmad-cis-storytelling"),
    ("retro.", "bmad-retrospective"),
    ("change.", "bmad-correct-course"),
    # ALT DEV BRANCH prefixes (alternative implementations of the dev stage)
    ("quickdev.", "bmad-quick-dev"),
    ("devauto.", "bmad-dev-auto"),
    ("agentdev.", "bmad-agent-dev"),
    # TESTING CHAIN prefixes (TEA toolbox + academy — specific prefixes first)
    ("test.atdd.", "bmad-testarch-atdd"),
    ("test.automate.", "bmad-testarch-automate"),
    ("test.ci.", "bmad-testarch-ci"),
    ("test.framework.", "bmad-testarch-framework"),
    ("test.nfr.", "bmad-testarch-nfr"),
    ("test.design.", "bmad-testarch-test-design"),
    ("test.review.", "bmad-testarch-test-review"),
    ("test.trace.", "bmad-testarch-trace"),
    ("test.e2e.", "bmad-qa-generate-e2e-tests"),
    ("teach.", "bmad-teach-me-testing"),
    # GDS prefixes (game vertical — specific before generic)
    ("gds.quickdev.", "gds-quick-dev"),
    ("gds.agentdev.", "gds-agent-game-dev"),
    ("gds.solodev.", "gds-agent-game-solo-dev"),
    ("gds.research.", "gds-domain-research"),
    ("gds.brainstorm.", "gds-brainstorm-game"),
    ("gds.brief.", "gds-create-game-brief"),
    ("gds.narrative.", "gds-create-narrative"),
    ("gds.gdd.", "gds-gdd"),
    ("gds.ux.", "gds-ux"),
    ("gds.prd.", "gds-prd"),
    ("gds.architecture.", "gds-game-architecture"),
    ("gds.ir.", "gds-check-implementation-readiness"),
    ("gds.sp.", "gds-sprint-planning"),
    ("gds.story.", "gds-create-story"),
    ("gds.dev.", "gds-dev-story"),
    ("gds.review.", "gds-code-review"),
    ("gds.test.design.", "gds-test-design"),
    ("gds.test.framework.", "gds-test-framework"),
    ("gds.test.automate.", "gds-test-automate"),
    ("gds.test.review.", "gds-test-review"),
    ("gds.test.e2e.", "gds-e2e-scaffold"),
    ("gds.test.perf.", "gds-performance-test"),
    ("gds.playtest.", "gds-playtest-plan"),
    ("gds.retro.", "gds-retrospective"),
    ("gds.change.", "gds-correct-course"),
    ("gds.status.", "gds-sprint-status"),
    ("gds.investigate.", "gds-investigate"),
    ("gds.", "gds-generic"),
    # WDS prefixes (web design vertical — numbered phases before generic)
    ("wds.setup.", "wds-0-project-setup"),
    ("wds.signoff.", "wds-0-alignment-signoff"),
    ("wds.brief.", "wds-1-project-brief"),
    ("wds.trigger.", "wds-2-trigger-mapping"),
    ("wds.scenario.", "wds-3-scenarios"),
    ("wds.ux.", "wds-4-ux-design"),
    ("wds.dev.", "wds-5-agentic-development"),
    ("wds.asset.", "wds-6-asset-generation"),
    ("wds.designsystem.", "wds-7-design-system"),
    ("wds.evolve.", "wds-8-product-evolution"),
    ("wds.", "wds-generic"),
    ("prd.", "bmad-prd"),
    ("ux.", "bmad-ux"),
    ("architecture.", "bmad-architecture"),
    ("spec.", "bmad-spec"),
    ("epics.", "bmad-create-epics-and-stories"),
    ("story.", "bmad-create-story"),
]

def _skill_for_run_key(key: str) -> str | None:
    """Owning skill for a run-key namespace — the producer side of the same
    table the hand-off diagnostic attributes with (watch-plane stamps and
    canvas owner links must name stages the way chain_health names senders).
    None for keys outside every known namespace (bridge keys, ad-hoc notes)."""
    from . import blackboard as _board_api
    _KEY_PREFIX_TO_SKILL = _board_api._KEY_PREFIX_TO_SKILL
    key = str(key or "").strip()
    for prefix, skill in _KEY_PREFIX_TO_SKILL:
        if key.startswith(prefix):
            return skill
    return None

# A skill token accepted as an explicit signal sender (same shape as the
# channel budget allows). Anything else is ignored — attribution falls back to
# the prefix rather than trusting a free-form string.
_SENDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")

# A run key that names a record (`E-048`, `QR-001`): only these can supersede or
# be superseded, so only these pay the cascade scan on the write path.
_RECORD_KEY_RE = re.compile(r"^[A-Z]+-\d+$")

def _signal_sender(text: str, explicit: str | None = None) -> str:
    """Attribute a hand-off signal to a sender skill.

    An explicit sender (recorded by the poster) wins over the from-key prefix:
    run-key namespaces are shared (`story.` is written by BOTH create-story and
    dev-story), so the prefix alone cannot name the stage that posted. Without
    one, the from-key prefix decides (signal text is '<from-key>: <note>');
    keys outside every known namespace (e.g. a QR record key) attribute to
    'unknown' — they still surface under chain_health's 'extra', just unnamed."""
    from . import blackboard as _board_api
    _KEY_PREFIX_TO_SKILL = _board_api._KEY_PREFIX_TO_SKILL
    _SENDER_RE = _board_api._SENDER_RE
    if explicit:
        candidate = str(explicit).strip()
        if _SENDER_RE.match(candidate):
            return candidate
    key = text.split(":", 1)[0].strip()
    for prefix, skill in _KEY_PREFIX_TO_SKILL:
        if key.startswith(prefix):
            return skill
    return "unknown"

def chain_health(project_root: str, *, now: float | None = None) -> dict:
    """Per-hop hand-off diagnostics for the delivery relay AND the methodology
    relay: how many signals are waiting (downstream has not picked up) and how
    many were consumed (handshake completed) on every hop — waiting
    sender-attributed from the folded snapshot (poster sender persisted),
    consumed folded into that same board.

    - ``chain``: the ordered delivery relay (CHAIN, 7 skills → 6 hops) plus
      its declared terminal hops (CHAIN_TERMINAL — the review gate that
      consumes the last stage and signals nothing onward; such rows carry
      ``terminal: true``).
    - ``methodology_chain``: the ordered methodology relay (METHODOLOGY_CHAIN,
      6 stages → 5 hops).
    - ``sub_chains``: the phase-based modular relays (SUB_CHAINS), keyed by
      phase name — 'discovery', 'alt_dev', …
    - ``extra``: signals outside every declared relay (unknown receivers only).
      The protocol is extensible, the diagnostic follows.
    - ``stale``: waiting signals older than HANDOFF_SIGNAL_TTL_SECONDS —
      the escalation path stop/session_start surface. Pass ``now`` in tests
      for deterministic age computation.

    One source, one contract (TD-014): ``waiting``, ``stale`` AND ``consumed``
    all fold from the same snapshot skills read via ``pending_handoffs`` /
    ``consume`` — the diagnostic can only see a board the readers can see.
    Snapshot evictions (stale-cleanup, alert caps) and event-log rotation can
    therefore never stage a divergence between the two views.
    """
    from . import blackboard as _board_api
    CHAIN = _board_api.CHAIN
    CHAIN_ADJACENT = _board_api.CHAIN_ADJACENT
    CHAIN_TERMINAL = _board_api.CHAIN_TERMINAL
    HANDOFF_SIGNAL_TTL_SECONDS = _board_api.HANDOFF_SIGNAL_TTL_SECONDS
    METHODOLOGY_CHAIN = _board_api.METHODOLOGY_CHAIN
    SUB_CHAINS = _board_api.SUB_CHAINS
    _signal_sender = _board_api._signal_sender
    _waiting_signals = _board_api._waiting_signals
    fr = _board_api.fr
    read_board = _board_api.read_board
    sub_chain_skills = _board_api.sub_chain_skills
    time = _board_api.time
    ref = now if now is not None else time.time()
    board = read_board(project_root)
    consumed: dict = {}  # (sender, receiver) -> count, from the folded board
    for _sender, _receivers in (board.get("consumed") or {}).items():
        if not isinstance(_receivers, dict):
            continue
        for _receiver, _count in _receivers.items():
            try:
                _n = int(_count)
            except (TypeError, ValueError):
                continue
            if _n:
                consumed[(_sender, _receiver)] = (
                    consumed.get((_sender, _receiver), 0) + _n)
    # Waiting + stale + consumed ALL read the folded board (single source):
    # the same live truth pending_handoffs/consume see. Replay-only
    # diagnostics are gone, so the diagnostic can only see a board the
    # readers can see — evicted or log-rotated history cannot stage a
    # divergence between the two views.
    waiting: dict = {}   # (sender, receiver) -> count
    stale: list = []     # waiting signals past TTL (age-based escalation)
    verdicts: dict = {}  # (sender, receiver) -> {STALE: n, UNKNOWN: n}
    unclear: list = []   # waiting signals the CODE STATE moved past (E-053)
    for sig in _waiting_signals(project_root, board=board):
        receiver = sig["skill"]
        sender = _signal_sender(str(sig.get("text", "")), explicit=sig.get("sender"))
        key = (sender, receiver)
        waiting[key] = waiting.get(key, 0) + 1
        verdict = str(sig.get("verdict") or fr.CURRENT).upper()
        if verdict in (fr.STALE, fr.UNKNOWN):
            verdicts.setdefault(key, {})
            verdicts[key][verdict] = verdicts[key].get(verdict, 0) + 1
            unclear.append({"from": sender, "to": receiver,
                            "text": str(sig.get("text", "")),
                            "verdict": verdict,
                            "reason": str(sig.get("reason", "")),
                            "run_key": str(sig.get("run_key", "")),
                            "age_seconds": int(sig.get("age_s") or 0)})
        try:
            ts = float(sig.get("ts", 0.0) or 0.0)
        except (TypeError, ValueError):
            ts = 0.0
        if ref - ts > HANDOFF_SIGNAL_TTL_SECONDS:
            stale.append({"from": sender, "to": receiver,
                          "text": str(sig.get("text", "")),
                          "age_seconds": int(ref - ts), "verdict": verdict})

    def hop_rows(sender_skill: str, receiver_skill: str) -> dict:
        w = sum(v for (s, r), v in waiting.items() if s == sender_skill and r == receiver_skill)
        c = sum(v for (s, r), v in consumed.items() if s == sender_skill and r == receiver_skill)
        v = verdicts.get((sender_skill, receiver_skill), {})
        if w:
            status = "waiting"
        elif w + c:
            status = "clear"
        else:
            status = "idle"
        # Age and CODE STATE are different questions: `stale` counts the clock,
        # `stale_claims`/`unknown_claims` count the claims the code moved past
        # (and those the anchor cannot settle). A hop can be perfectly fresh by
        # age and already false by state — that was the whole defect (E-052).
        return {"from": sender_skill, "to": receiver_skill,
                "waiting": w, "consumed": c, "status": status,
                "stale_claims": v.get(fr.STALE, 0),
                "unknown_claims": v.get(fr.UNKNOWN, 0)}

    chain = [hop_rows(a, b) for a, b in zip(CHAIN, CHAIN[1:])]
    for a, b in CHAIN_TERMINAL:
        chain.append({**hop_rows(a, b), "terminal": True})
    methodology_chain = [hop_rows(a, b) for a, b in zip(METHODOLOGY_CHAIN, METHODOLOGY_CHAIN[1:])]
    sub_chains = {name: [hop_rows(a, b) for a, b in hops]
                  for name, hops in SUB_CHAINS.items()}
    # NOTE: bmad-create-story is the bridge (present in both ordered relays);
    # its fan-out to quality-record is a METHODOLOGY_CHAIN hop (S->QR). Every
    # declared relay feeds 'seen', so only genuinely unknown receivers land in
    # 'extra'.
    seen = ({(h["from"], h["to"]) for h in chain} |
            {(h["from"], h["to"]) for h in methodology_chain} |
            {(h["from"], h["to"]) for hops in sub_chains.values() for h in hops})
    # Chain-adjacent surfaces (help, infrastructure, the loop orchestrator's
    # automation layer, the WDS backends) declare no hop, but a signal
    # addressed to a KNOWN skill is still first-class diagnostic data — only
    # genuinely unknown receivers land in 'extra'. They report as a single
    # self-hop so their waiting count stays visible without inventing a relay.
    extra: list = []
    for r in sorted(CHAIN_ADJACENT - sub_chain_skills()):
        w = sum(v for (s, rc), v in waiting.items() if rc == r)
        c = sum(v for (s, rc), v in consumed.items() if rc == r)
        if w or c:
            extra.append({"from": "chain-adjacent", "to": r, "waiting": w,
                          "consumed": c, "status": "waiting" if w else "clear"})
    for (s, r) in sorted(set(list(waiting) + list(consumed))):
        if (s, r) in seen:
            continue
        if r in CHAIN_ADJACENT:
            continue  # already reported as a chain-adjacent row above
        w, c = waiting.get((s, r), 0), consumed.get((s, r), 0)
        extra.append({"from": s, "to": r, "waiting": w, "consumed": c,
                      "status": "waiting" if w else "clear"})
    total_waiting = sum(waiting.values())
    return {"ok": True, "chain": chain, "methodology_chain": methodology_chain,
            "sub_chains": sub_chains,
            "extra": extra, "total_waiting": total_waiting, "stale": stale,
            # Freshness dimension (E-053): derived from the same scan, keyed by
            # claim, never stored. `stale` above is the clock; `unclear` is the
            # code state. `retired` counts the claims explicitly withdrawn.
            "unclear": unclear,
            "freshness": fr.tally(unclear),
            "retired": len(board.get("retired") or {}),
            "freshness_mode": fr.mode()}

def waiting_hop_rows(health: dict) -> list:
    """Every hop holding a waiting signal, across ALL declared relays
    (delivery, methodology, every sub-chain) plus the unknown 'extra' bucket.
    One place, so a new sub-chain is picked up by every consumer."""
    rows = [h for h in health.get("chain", []) if h.get("waiting")]
    rows += [h for h in health.get("methodology_chain", []) if h.get("waiting")]
    for hops in (health.get("sub_chains") or {}).values():
        rows += [h for h in hops if h.get("waiting")]
    rows += [h for h in health.get("extra", []) if h.get("waiting")]
    return rows

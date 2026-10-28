#!/usr/bin/env python3
"""check-handoff.py — static handoff wiring lint (skill <-> chain contract).

Every chain member's SKILL.md must:
  - peek its own channel:  "handoffs --skill <self>"
    (relay origins / entry points are exempt — nothing routes to them; see
    PEEK_EXEMPT, which names each one's reason)
  - post the next hop:     "handoff --to <next>" or the one-call equivalent
    "mirror --to <next>" (the central heartbeat: run-key write + hand-off)
    (terminal skills bmad-code-review / bmad-production-readiness are exempt)
The bridge skill bmad-create-story fans out (dev-story + quality-record),
so it must post BOTH.

Chain members = the delivery relay, the methodology relay, and every
phase-based sub-chain (see SUB_CHAINS in hooks/engine/modules/blackboard.py).
Deprecated shims (FORWARDERS) are checked too: each must be marked DEPRECATED
and forward to a canonical skill that is itself a chain member.

Two structural invariants ride on top of the per-skill checks:
  - every declared hop RECEIVER is registered AND claimable — a receiver may
    not be exempted as "nothing hands off to me", and may not be missing from
    the registry (a baton nobody can claim rots until it goes stale)
  - a run-key namespace written by MORE THAN ONE stage must be stamped
    (`--sender <self>`) by every stage but its owner, or chain_health's
    prefix-based attribution silently credits the wrong skill

Usage:  python scripts/check-handoff.py [--negtest] [--runtime --project-root R]
        --runtime inspects the live board at R (event log + snapshot):
        are methodology run keys (E-/IR-/SP-) stamped and are handoff
        signals flowing, or is the wiring present but never exercised
        ("wiring OK, board silent" — the static-board symptom)? Exit 0 when
        the board shows a live relay, 1 when silent, alongside the static
        wiring result.
Output: [OK] / [WARNING] / [ERROR] lines; exit 0 clean, 1 problems.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
SKILLS = PLUGIN / "skills"

# skill -> expected --to targets; [] = terminal (no post required)
EXPECTED_POSTS = {
    # DISCOVERY CHAIN (sub-chain for ideation, research and brief formulation)
    "bmad-brainstorming": ["bmad-product-brief"],
    "bmad-forge-idea": ["bmad-product-brief"],
    "bmad-product-brief": ["bmad-prd"],
    "bmad-prfaq": ["bmad-prd"],
    "bmad-market-research": ["bmad-product-brief"],
    "bmad-domain-research": ["bmad-product-brief"],
    "bmad-technical-research": ["bmad-product-brief"],
    "bmad-cis-design-thinking": ["bmad-product-brief"],
    "bmad-cis-innovation-strategy": ["bmad-product-brief"],
    "bmad-cis-problem-solving": ["bmad-product-brief"],
    "bmad-cis-storytelling": ["bmad-product-brief"],
    # The inline technique amplifier: it reads the invoking run's board and
    # leaves exactly one `contribute` trace, then hands the enhancements back
    # to the caller's close-out. It owns no channel and posts no hop.
    "bmad-advanced-elicitation": [],
    # ALT-DEV SUB-CHAIN (alternative implementations of the dev stage — the
    # shortcut path and the unattended loop converge on the same formal
    # review gate the delivery relay terminates at)
    "bmad-quick-dev": ["bmad-code-review"],
    "bmad-dev-auto": ["bmad-code-review"],
    "bmad-agent-dev": ["bmad-code-review"],  # dev persona implements too
    # TESTING SUB-CHAIN (TEA toolbox feeders — each adds evidence to an
    # existing QR record's mechanical checks instead of opening its own
    # record, so all of them signal the quality record they feed)
    "bmad-testarch-atdd": ["bmad-quality-record"],
    "bmad-testarch-automate": ["bmad-quality-record"],
    "bmad-testarch-ci": ["bmad-quality-record"],
    "bmad-testarch-framework": ["bmad-quality-record"],
    "bmad-testarch-nfr": ["bmad-quality-record"],
    "bmad-testarch-test-design": ["bmad-quality-record"],
    "bmad-testarch-test-review": ["bmad-quality-record"],
    "bmad-testarch-trace": ["bmad-quality-record"],
    "bmad-qa-generate-e2e-tests": ["bmad-quality-record"],
    # TEA Academy close-out recommends "start with Framework setup": the
    # learner lands in the chain the academy teaches.
    "bmad-teach-me-testing": ["bmad-testarch-framework"],
    # GOVERNANCE SUB-CHAIN (the hops that close the methodology loop)
    "bmad-retrospective": ["bmad-research-experiment"],
    "bmad-correct-course": ["bmad-sprint-planning"],
    # INFRASTRUCTURE & SYSTEM — the builder/tooling layer and the WDS session
    # backends. Their products are not chain artifacts (installed modules,
    # built skills, TOML overrides, progress files), so they relay nothing.
    "bmad-loop-setup": [],
    "bmad-loop-sweep": [],
    "bmad-loop-resolve": [],
    "bmad-bmb-setup": [],
    "bmad-module-builder": [],
    "bmad-workflow-builder": [],
    "bmad-customize": [],
    "bmad-checkpoint-preview": [],
    "bmad-eval-runner": [],
    "memory": [],
    "sync": [],
    # GDS SUB-CHAIN (game vertical — mirrors the canonical relay shape with
    # game-native artifacts; QA tools feed the playtest record, retro and
    # correct-course re-enter planning, dev variants converge on the review)
    "gds-domain-research": ["gds-create-game-brief"],
    "gds-brainstorm-game": ["gds-create-game-brief"],
    "gds-create-game-brief": ["gds-gdd"],
    "gds-create-narrative": ["gds-gdd"],
    "gds-gdd": ["gds-ux"],
    "gds-ux": ["gds-game-architecture"],
    "gds-prd": ["gds-game-architecture"],
    "gds-game-architecture": ["gds-check-implementation-readiness"],
    "gds-check-implementation-readiness": ["gds-sprint-planning"],
    "gds-sprint-planning": ["gds-create-story"],
    "gds-create-story": ["gds-dev-story"],
    "gds-dev-story": ["gds-code-review"],
    "gds-test-design": ["gds-code-review"],
    "gds-test-framework": ["gds-code-review"],
    "gds-test-automate": ["gds-code-review"],
    "gds-test-review": ["gds-code-review"],
    "gds-e2e-scaffold": ["gds-code-review"],
    "gds-performance-test": ["gds-code-review"],
    "gds-code-review": [],  # terminal — retro re-enters planning on demand
    "gds-retrospective": ["gds-sprint-planning"],
    "gds-correct-course": ["gds-sprint-planning"],
    "gds-quick-dev": ["gds-code-review"],
    "gds-agent-game-dev": ["gds-code-review"],
    "gds-agent-game-solo-dev": ["gds-code-review"],
    "gds-sprint-status": ["gds-sprint-planning"],
    "gds-investigate": ["gds-correct-course"],
    # WDS SUB-CHAIN (web design vertical — Freya's design pipeline; brownfield
    # evolution re-enters at the brief)
    "wds-0-project-setup": ["wds-0-alignment-signoff"],
    "wds-0-alignment-signoff": ["wds-1-project-brief"],
    "wds-1-project-brief": ["wds-2-trigger-mapping"],
    "wds-2-trigger-mapping": ["wds-3-scenarios"],
    "wds-3-scenarios": ["wds-4-ux-design"],
    "wds-4-ux-design": ["wds-5-agentic-development"],
    "wds-5-agentic-development": ["wds-6-asset-generation"],
    "wds-6-asset-generation": ["wds-7-design-system"],
    # Terminal phase: it consumes the asset-generation baton and hands nothing
    # on (the pipeline is done until an evolution cycle re-enters the brief).
    # Registered so the lint checks its peek — an unregistered terminal receiver
    # would leave a baton nobody can claim.
    "wds-7-design-system": [],
    "wds-8-product-evolution": ["wds-1-project-brief"],
    # The playtest plan's BRIDGE feeds the QR record the review session owns
    # ("feed the findings into the QR, do not open a separate record") — the
    # seventh GDS QA feeder, not an advisory surface.
    "gds-playtest-plan": ["gds-code-review"],
    # GDS/WDS advisor personas — route the user into workflows, never produce.
    # (They adopt an identity and dispatch a menu, so they carry a READ side:
    # see ROUTERS below.)
    "gds-agent-game-architect": [],
    "gds-agent-game-designer": [],
    "gds-agent-tech-writer": [],
    "wds-agent-freya-ux": [],
    "wds-agent-mimir-builder": [],
    "wds-agent-saga-analyst": [],
    # Vertical documentation tools — docs, no hop.
    "gds-document-project": [],
    "gds-generate-project-context": [],
    # PERSONA / ORCHESTRATION SURFACE — [] = no hop to post. A persona adopts
    # an identity, grounds in the board and dispatches the user into a
    # workflow; it produces no methodology record of its own, so it never
    # signals downstream (see ROUTERS below for the read-side requirement).
    "bmad-agent-analyst": [],
    "bmad-agent-architect": [],
    "bmad-agent-builder": [],
    "bmad-agent-pm": [],
    "bmad-agent-tech-writer": [],
    "bmad-agent-ux-designer": [],
    "bmad-tea": [],
    "bmad-party-mode": [],
    "bmad-sprint-status": [],
    # The help surface recommends the next skill; it never produces, so its
    # waiting signals must still surface (pending_handoff_channels special-
    # cases its result-reporting channel).
    "bmad-help": [],
    "bmad-document-project": [],
    "bmad-generate-project-context": [],
    "bmad-index-docs": [],
    "bmad-shard-doc": [],
    "bmad-editorial-review-prose": [],
    "bmad-editorial-review-structure": [],
    "bmad-review-adversarial-general": [],
    "bmad-review-edge-case-hunter": [],
    "bmad-cis-agent-brainstorming-coach": [],
    "bmad-cis-agent-creative-problem-solver": [],
    "bmad-cis-agent-design-thinking-coach": [],
    "bmad-cis-agent-innovation-strategist": [],
    "bmad-cis-agent-presentation-master": [],
    "bmad-cis-agent-storyteller": [],
    # TOOL & DELIVERY CHAIN
    "bmad-prd": ["bmad-ux"],
    "bmad-ux": ["bmad-architecture"],
    "bmad-architecture": ["bmad-spec"],
    "bmad-spec": ["bmad-create-epics-and-stories"],
    "bmad-create-epics-and-stories": ["bmad-create-story"],
    "bmad-create-story": ["bmad-dev-story", "bmad-quality-record"],  # bridge fan-out
    "bmad-dev-story": ["bmad-code-review"],
    "bmad-code-review": [],  # terminal
    # METHODOLOGY CHAIN
    "bmad-research-experiment": ["bmad-check-implementation-readiness"],  # origin
    "bmad-check-implementation-readiness": ["bmad-sprint-planning"],
    "bmad-sprint-planning": ["bmad-create-story"],
    "bmad-quality-record": ["bmad-production-readiness"],
    "bmad-production-readiness": [],  # terminal
}
# Skills with nothing upstream to peek, mapped to why:
#  - entry point: invoked directly with user intent (an alternative to routing
#    through the chain), so no upstream is expected to hand off to it.
#  - feeder: a toolbox skill invoked on demand that ADDS to an existing record
#    and never receives relay work — it only signals the record it fed.
#  - router: a persona/orchestration surface that produces nothing and posts
#    nothing. Nothing routes to it, and it must not be tempted to consume a
#    channel it cannot complete — instead it READS the board.
#  - contributor: an inline technique amplifier invoked INSIDE another skill's
#    session. It reads the caller's board context and leaves one `contribute`
#    trace; the caller's close-out folds the result in.
#  - reporter: a status surface that reads the board and recommends the next
#    step (bmad-sprint-status) — it must cross-check the file it summarizes
#    against the live relay instead of trusting the file alone.
#  - inline: invoked INSIDE another run as a subagent/tool; it returns findings
#    to the caller and posts no hop. The blind reviewers must NOT read the
#    caller's board context either — the information asymmetry is by design.
#  - tool: a builder/standalone tool whose product is not a chain artifact
#    (bmad-agent-builder emits agent skills, bmad-document-project emits docs).
PEEK_EXEMPT = {
    # NOTE: bmad-research-experiment is deliberately NOT exempt. It opens the
    # methodology relay, but 'governance' sends a retrospective's lessons back
    # to it — so its activation half is real: it must claim that baton, or the
    # loop's return edge waits forever.
    "bmad-quick-dev": "entry point",
    "bmad-dev-auto": "entry point",
    "bmad-agent-dev": "entry point",
    "bmad-teach-me-testing": "entry point",
    "bmad-cis-design-thinking": "entry point",
    "bmad-cis-innovation-strategy": "entry point",
    "bmad-cis-problem-solving": "entry point",
    "bmad-cis-storytelling": "entry point",
    "bmad-testarch-atdd": "feeder",
    "bmad-testarch-automate": "feeder",
    "bmad-testarch-ci": "feeder",
    "bmad-testarch-nfr": "feeder",
    "bmad-testarch-test-design": "feeder",
    "bmad-testarch-test-review": "feeder",
    "bmad-testarch-trace": "feeder",
    "bmad-qa-generate-e2e-tests": "feeder",
    "bmad-agent-analyst": "router",
    "bmad-agent-architect": "router",
    "bmad-agent-pm": "router",
    "bmad-agent-tech-writer": "router",
    "bmad-agent-ux-designer": "router",
    "bmad-agent-builder": "tool",
    "bmad-advanced-elicitation": "contributor",
    "bmad-cis-agent-brainstorming-coach": "router",
    "bmad-cis-agent-creative-problem-solver": "router",
    "bmad-cis-agent-design-thinking-coach": "router",
    "bmad-cis-agent-innovation-strategist": "router",
    "bmad-cis-agent-presentation-master": "router",
    "bmad-cis-agent-storyteller": "router",
    "bmad-tea": "router",
    # Discovery entry points: ideation and research start from user intent, and
    # the registry declares no hop into them — nothing can tell them a baton
    # waits, so no peek is promised (their CIS siblings are classified the same
    # way). The lint enforces the alternative: a peek must be claimable.
    "bmad-brainstorming": "entry point",
    "bmad-forge-idea": "entry point",
    "bmad-prfaq": "entry point",
    "bmad-market-research": "entry point",
    "bmad-domain-research": "entry point",
    "bmad-technical-research": "entry point",
    "bmad-sprint-status": "reporter",
    "bmad-party-mode": "contributor",
    # GDS/WDS advisor personas & documentation tools: produce advice/docs,
    # never hops (the producing GDS personas are hop members with their own
    # close-out signal — wired in the BRIDGE layer like bmad-agent-dev).
    "gds-agent-game-architect": "router",
    "gds-agent-game-designer": "router",
    "gds-agent-tech-writer": "router",
    "gds-document-project": "tool",
    "gds-generate-project-context": "tool",
    # These three ship the SAME persona template as the WDS personas (identity
    # + menu + Step 8 dispatch into a workflow), so they route, not tool.
    "wds-agent-freya-ux": "router",
    "wds-agent-mimir-builder": "router",
    "wds-agent-saga-analyst": "router",
    "bmad-document-project": "tool",
    "bmad-generate-project-context": "tool",
    "bmad-index-docs": "tool",
    "bmad-shard-doc": "tool",
    "bmad-editorial-review-prose": "inline",
    "bmad-editorial-review-structure": "inline",
    "bmad-review-adversarial-general": "inline",
    "bmad-review-edge-case-hunter": "inline",
    "bmad-retrospective": "entry point",
    "bmad-correct-course": "entry point",
    # Infrastructure: installers/builders whose product is an installed module
    # or a built skill (it inherits the methodology; the installer does not
    # relay), customization authoring, the loop orchestrator's automation
    # surfaces and the WDS session backends.
    "bmad-loop-setup": "tool",
    "bmad-bmb-setup": "tool",
    "bmad-module-builder": "tool",
    "bmad-workflow-builder": "tool",
    "bmad-customize": "tool",
    "memory": "tool",
    "sync": "tool",
    # bmad-loop-sweep runs INSIDE the orchestrator's session (automation-only,
    # never human-invoked) and returns a machine-readable partition — findings
    # to the caller, no hop, no board I/O: the orchestrator owns that state.
    "bmad-loop-sweep": "inline",
    # Human-invoked on intent, each owns its interactive session:
    "bmad-loop-resolve": "entry point",
    "bmad-checkpoint-preview": "entry point",
    # Registered, not rewired: the eval runner's contributor contract lives in
    # its own SKILL.md (board read at activation, one close-out contribute,
    # all other writes forbidden) — the registry just makes it visible.
    "bmad-eval-runner": "contributor",
    "bmad-help": "router",
    "gds-quick-dev": "entry point",
    "gds-agent-game-dev": "entry point",
    "gds-agent-game-solo-dev": "entry point",
    "gds-playtest-plan": "entry point",
    "gds-retrospective": "entry point",   # nothing hands off to retro
    "gds-sprint-status": "reporter",
    "gds-investigate": "entry point",
    "gds-domain-research": "entry point",
    "gds-brainstorm-game": "entry point",
    "gds-create-narrative": "entry point",
    "gds-prd": "entry point",
    "gds-playtest-plan": "feeder",
    "gds-test-design": "feeder",
    "gds-test-framework": "feeder",
    "gds-test-automate": "feeder",
    "gds-test-review": "feeder",
    "gds-e2e-scaffold": "feeder",
    "gds-performance-test": "feeder",
    # No upstream hands these off — direct entries into the vertical:
    "wds-0-project-setup": "entry point",
    "wds-8-product-evolution": "entry point",
}

# Reasons that assert "nothing hands off to me". If the declared hop table
# (EXPECTED_POSTS values) says otherwise, the registry is lying: the baton
# would wait for a stage that never looks at its channel. Checked mechanically.
NO_UPSTREAM_REASONS = ("entry point", "feeder", "origin")

# Run-key namespaces written by MORE THAN ONE stage. `story.` is opened by
# create-story and continued by dev-story, so a close-out posted under it
# cannot be attributed from the prefix alone — the poster must stamp
# `--sender <self>`. Index 0 owns the namespace (it may omit the stamp); every
# later stage must carry it.
SHARED_NAMESPACES = {
    "story.": ["bmad-create-story", "bmad-dev-story"],
}

# Blackboard full-integration (graph + change radar, GUIDE §14): the producing
# and relay skills below carry one graph edge per close-out
# (`link --a <run-key> --b <next-run-key> --relation <rel>` — SKILL.md or its
# steps/ tree) and one upstream subscription at activation
# (`subscribe --watcher <self>` — SKILL.md side). WARNING-level, not ERROR: a
# missing edge degrades traceability but never breaks the chain the way a
# missing handoff does — and the GDS/WDS verticals migrate later, so failing
# the whole lint on them would be wrong. Extend these dicts when they migrate.
#
# The declared value is the FULL edge — (source prefix, target prefix,
# relation) — because the first cut checked only the source half
# (`"link --a prd." in blob`), so a close-out could point the graph at the
# WRONG downstream, or ship a relation outside the documented
# informs|constrains|validates vocabulary, and the lint still printed [OK]
# (E-044; the same class E-043 closed for the radar).
EXPECTED_LINKS = {
    "bmad-prd": (("prd.", "ux.", "informs"),),
    "bmad-ux": (("ux.", "architecture.", "informs"),),
    "bmad-architecture": (("architecture.", "spec.", "constrains"),),
    "bmad-spec": (("spec.", "epics.", "informs"),),
    "bmad-create-epics-and-stories": (("spec.", "epics.", "informs"),),
    "bmad-create-story": (("epics.", "story.", "informs"),),
    "bmad-check-implementation-readiness": (("E-", "IR-", "informs"),),
    "bmad-sprint-planning": (("IR-", "SP-", "informs"),),
    "bmad-quality-record": (("story.", "QR-", "validates"),),
    "bmad-production-readiness": (("QR-", "PR-", "validates"),),
    # WDS pipeline (linear phases — links only, no subscriptions: the 16-slot
    # global subscription cap is reserved for the bmad change radar, and a
    # strict pipeline already carries its radar in the per-phase baton).
    # Terminal wds-7-design-system writes no run key, so it carries no edge
    # (same rule as the bmad-code-review terminal).
    "wds-0-project-setup": (("wds.setup.", "wds.signoff.", "informs"),),
    "wds-0-alignment-signoff": (("wds.signoff.", "wds.brief.", "informs"),),
    "wds-1-project-brief": (("wds.brief.", "wds.trigger.", "informs"),),
    "wds-2-trigger-mapping": (("wds.trigger.", "wds.scenario.", "informs"),),
    "wds-3-scenarios": (("wds.scenario.", "wds.ux.", "informs"),),
    "wds-4-ux-design": (("wds.ux.", "wds.dev.", "informs"),),
    "wds-5-agentic-development": (("wds.dev.", "wds.asset.", "informs"),),
    "wds-8-product-evolution": (("wds.evolve.", "wds.brief.", "informs"),),
    # GDS game vertical (close-outs live in custom/<skill>.toml [workflow]
    # on_complete — the lint blob covers the bridge TOMLs, so TOML edges
    # count. Feeders, dev variants and governance post no graph edge
    # (bmad-side symmetry: a feeder adds evidence, it does not open a run).
    # Terminal gds-dev-story writes no review key (gds-code-review carries no
    # mirror), so it carries no edge — same rule as the bmad/wds terminals.
    "gds-domain-research": (("gds.research.", "gds.brief.", "informs"),),
    "gds-brainstorm-game": (("gds.brainstorm.", "gds.brief.", "informs"),),
    "gds-create-game-brief": (("gds.brief.", "gds.gdd.", "informs"),),
    "gds-create-narrative": (("gds.narrative.", "gds.gdd.", "informs"),),
    "gds-gdd": (("gds.gdd.", "gds.ux.", "informs"),),
    "gds-ux": (("gds.ux.", "gds.architecture.", "informs"),),
    "gds-prd": (("gds.prd.", "gds.architecture.", "informs"),),
    "gds-game-architecture": (("gds.architecture.", "gds.ir.", "informs"),),
    "gds-check-implementation-readiness": (("gds.ir.", "gds.sp.", "informs"),),
    "gds-sprint-planning": (("gds.sp.", "gds.story.", "informs"),),
    "gds-create-story": (("gds.story.", "gds.dev.", "informs"),),
}
# The change radar (GUIDE/KILAVUZ §14): every subscribing skill watches a fixed
# set of upstream patterns on ONE channel. The value is the FULL contract —
# the (pattern, channel) pairs each skill must carry — because the first cut
# checked only the watcher NAME (`subscribe --watcher <self>`), which let a
# skill ship the WRONG pattern under the right watcher without the lint
# noticing: bmad-spec shipped without its documented `prd.*` watch while the
# runbook claimed it, and nothing could see the drift. This table is the one
# declared truth: the skills must carry it exactly (checked below), and the two
# runbooks must enumerate it exactly (bench_consistency
# `_check_change_radar_contract_matches`, which re-derives the radar from the
# skills independently so the table cannot quietly agree with itself).
EXPECTED_SUBSCRIPTIONS = {
    "bmad-architecture": (("prd.*", "session"), ("ux.*", "session")),
    "bmad-spec": (("architecture.*", "session"), ("prd.*", "session")),
    "bmad-create-epics-and-stories": (("spec.*", "session"),),
    "bmad-create-story": (("epics.*", "session"), ("SP-*", "session")),
    "bmad-dev-story": (("story.*", "session"),),
    "bmad-check-implementation-readiness": (("E-*", "session"),
                                            ("prd.*", "session")),
    "bmad-sprint-planning": (("IR-*", "session"),),
    "bmad-quality-record": (("story.*", "stop"),),
    "bmad-production-readiness": (("QR-*", "stop"),),
    "bmad-research-experiment": (("retro.*", "session"),),
}

# The exact CLI triple a SKILL.md ships for the radar. Anchored end to end so a
# half-written or reordered line is not silently counted as present.
_SUBSCRIBE_RE = re.compile(
    r'subscribe --watcher (\S+) --pattern "([^"]+)" --channel (\S+)')


def actual_subscriptions(text: str) -> set:
    """Every (watcher, pattern, channel) a SKILL.md ships, as the radar sees
    it — the read side the contract is checked against."""
    return set(_SUBSCRIBE_RE.findall(text))


# The exact graph-edge CLI a surface hands the relay (GUIDE §14). Anchored end
# to end — including `--relation` — so a bare `link --a … --b …` without the
# relation the runbook requires is not silently counted as a traced relay.
_EDGE_RE = re.compile(r"link --a (\S+) --b (\S+) --relation (\S+)")


def actual_edges(blob: str) -> set:
    """Every (source, target, relation) graph edge a surface ships, as the lint
    sees it — the read side the declared relay graph is checked against."""
    return set(_EDGE_RE.findall(blob))


def _edge_satisfied(declared: tuple, actual: set) -> bool:
    """Is a declared (src-prefix, dst-prefix, relation) met by some shipped edge?

    Prefix, not equality: the shipped node is a placeholder (`prd.<slug>`), so
    the contract pins the namespace and the relation, not the placeholder text.
    """
    src, dst, rel = declared
    return any(a.startswith(src) and b.startswith(dst) and r == rel
               for a, b, r in actual)


def _edge_declared(edge: tuple, declared) -> bool:
    a, b, r = edge
    return any(a.startswith(src) and b.startswith(dst) and r == rel
               for src, dst, rel in declared)


# Reasons whose READ side is mandatory, not decorative. A router is the
# session's front door and must ground in the chain (relay position + waiting
# batons) before greeting; a contributor must ground in the INVOKING run's
# context before it can enhance it. Checked mechanically — the reason string
# alone is a promise.
BOARD_GROUNDED_REASONS = ("router", "reporter")
# Guest contributors (`bmad-advanced-elicitation`, `bmad-eval-runner`) are called
# *inside* another skill's session and need only the invoking run's focus, not a
# whole orientation: they ground with ONE bounded read — `read --context`, the
# compact summary the board module calls its bounded-context contract.
BOARD_GROUNDED_GUESTS = ("contributor",)
# The grounding is ONE call: orient.py wraps the board context (focus +
# liveness, waiting batons with age, chain progress, record inventory) that the
# old pair of raw reads fetched separately. A board-grounded skill that HAS an
# activation body must therefore run the digest there — the reason string alone
# stays a promise until that call exists (test_grounded_reason_alone_is_not_
# enough proves the red path).
BOARD_GROUNDED_MARKER = "orient.py"
# Legacy route, kept for exactly one shape: a surface with no activation section
# at all (its grounding lives in a reference it loads on demand —
# bmad-advanced-elicitation). It may still spell the raw read; nothing else may.
BOARD_GROUNDED_LEGACY = ("read --context",)

# The activation section, located case-insensitively. `bmad-eval-runner` spelled
# it `## On activation` until 2026-09-23, which made a case-sensitive
# `"## On Activation" in text` test return False — silently exempting the skill
# from every activation rule (config dump, board grounding). One extractor, used
# by every check below.
_ACTIVATION_RE = re.compile(r"^#{2,3}[ \t]+on[ \t]+activation\b",
                            re.MULTILINE | re.IGNORECASE)
# The old two-call board read: the context summary plus the unqualified baton
# listing. The digest replaced both (docs/research/B-001); a targeted
# `handoffs --skill <self>` peek is not this, and a front door may still keep the
# bare listing as its fallback for when the digest could not run.
RAW_BOARD_READ = "read --context"
RAW_HANDOFFS_RE = re.compile(r"blackboard\.py handoffs(?! --skill)")
# A config read that asks for a whole module (or every layer) instead of naming
# keys. `(?!-)` keeps long flags like `--module-yaml` out of it.
MODULE_DUMP_RE = re.compile(r"resolve_config\.py[^\n`]*--module(?!-)")


def activation_body(text: str) -> str:
    """The activation section's text ("" when the skill has none)."""
    match = _ACTIVATION_RE.search(text)
    if not match:
        return ""
    return text[match.end():].split("\n## ", 1)[0]

# Run openers: the skills that START a chain run — every stage opener of the
# E→IR→SP→S→QR→PR chain, the dev alternates and the review gate, the discovery
# and planning entry points that feed it, and the governance openers that
# re-enter it. They must open from the orientation digest (one small read-only
# call that replaces the separate root/config/board/baton/record reads) instead
# of a module dump — see check_digest_first() and docs/research/B-001.
#
# NOT in this set, deliberately: feeders, contributors, tools and mid-run
# personas. They inherit the invoking run's orientation, and the digest (2.8 KB
# here) costs more body budget than it returns when a run only needs config —
# their activation must stay dump-free rather than digest-first
# (check_no_full_dump()), which is the half that actually truncates.
DIGEST_FIRST = (
    # E → IR → SP → S → dev → review → QR → PR, plus the governance loop
    "bmad-research-experiment", "bmad-check-implementation-readiness",
    "bmad-sprint-planning", "bmad-create-story", "bmad-dev-story",
    "bmad-quick-dev", "bmad-dev-auto", "bmad-agent-dev", "bmad-code-review",
    "bmad-quality-record", "bmad-production-readiness", "bmad-retrospective",
    "bmad-correct-course",
    # planning chain + the spec/spine/epic openers it feeds
    "bmad-prd", "bmad-ux", "bmad-spec", "bmad-architecture",
    "bmad-create-epics-and-stories",
    # discovery entry points (they open a session and produce a brief)
    "bmad-brainstorming", "bmad-forge-idea", "bmad-prfaq",
    "bmad-product-brief", "bmad-domain-research", "bmad-technical-research",
    "bmad-market-research", "bmad-cis-design-thinking",
    "bmad-cis-innovation-strategy", "bmad-cis-problem-solving",
    "bmad-cis-storytelling",
    # GDS sub-chain (game vertical — the same chain shape, its own module
    # config: the digest resolves `gds` paths the same way it resolves `bmm`'s)
    "gds-domain-research", "gds-brainstorm-game", "gds-create-game-brief",
    "gds-create-narrative", "gds-gdd", "gds-prd", "gds-ux",
    "gds-game-architecture", "gds-create-epics-and-stories",
    "gds-check-implementation-readiness", "gds-sprint-planning",
    "gds-create-story", "gds-dev-story", "gds-quick-dev", "gds-code-review",
    "gds-retrospective", "gds-correct-course",
    # WDS sub-chain (web-design vertical — nine phase openers; their SKILL.md is
    # a shell, so the run's step 1 is mirrored in the phase's workflow.md)
    "wds-0-project-setup", "wds-0-alignment-signoff", "wds-1-project-brief",
    "wds-2-trigger-mapping", "wds-3-scenarios", "wds-4-ux-design",
    "wds-5-agentic-development", "wds-6-asset-generation",
    "wds-7-design-system", "wds-8-product-evolution",
    # FRONT DOORS (personas/routers, plus the contributor and reporter surfaces
    # whose read side is a promise): for these the digest IS the grounding, so
    # the older two-call board read (`read --context` + `handoffs`) is now the
    # fallback, not the contract.
    "bmad-agent-analyst", "bmad-agent-architect", "bmad-agent-pm",
    "bmad-agent-tech-writer", "bmad-agent-ux-designer", "bmad-agent-builder",
    "bmad-tea", "bmad-party-mode", "bmad-sprint-status",
    # A human-decision entry point: it opens a review session, so it needs the
    # roots, the config, the relay position and the waiting batons — one digest
    # call where the activation used to make three reads.
    "bmad-checkpoint-preview",
    "bmad-cis-agent-brainstorming-coach",
    "bmad-cis-agent-creative-problem-solver",
    "bmad-cis-agent-design-thinking-coach",
    "bmad-cis-agent-innovation-strategist", "bmad-cis-agent-presentation-master",
    "bmad-cis-agent-storyteller",
    "gds-agent-game-architect", "gds-agent-game-designer", "gds-agent-tech-writer",
    "gds-agent-game-dev", "gds-agent-game-solo-dev", "gds-sprint-status",
    "wds-agent-freya-ux", "wds-agent-saga-analyst",
)

# Deprecated shims kept for backward compatibility: they hold no chain logic of
# their own, they forward to the canonical skill that does.
FORWARDERS = {
    "bmad-create-prd": "bmad-prd",
    "bmad-validate-prd": "bmad-prd",
    "bmad-create-architecture": "bmad-architecture",
}


def check() -> int:
    problems = 0
    for skill, wants in EXPECTED_POSTS.items():
        md = SKILLS / skill / "SKILL.md"
        if not md.is_file():
            print(f"[ERROR] {skill}: SKILL.md missing")
            problems += 1
            continue
        text = md.read_text(encoding="utf-8", errors="replace")
        # step/reference files carry the close-out handoff for some skills, and
        # the BRIDGE layer carries it for others (bmad-agent-dev's close-out is
        # a team principle) — read the whole skill tree plus the bridge TOMLs,
        # so a post cannot hide outside the lint's reach. Steps sit at the skill
        # root for some skills (quick-dev, dev-auto) and under steps/ for others
        # (research), so the skill-tree scan must be recursive.
        blob = text
        for f in sorted((SKILLS / skill).rglob("*.md")):
            if f.name == "SKILL.md" or "__pycache__" in f.parts:
                continue
            try:
                blob += "\n" + f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
        for bridge in (PLUGIN / "custom" / f"{skill}.toml",
                       PLUGIN / "custom" / f"{skill}.user.toml"):
            if bridge.is_file():
                try:
                    blob += "\n" + bridge.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    pass
        if skill not in PEEK_EXEMPT:
            if f"handoffs --skill {skill}" not in text:
                print(f"[ERROR] {skill}: missing peek 'handoffs --skill {skill}'")
                problems += 1
            else:
                print(f"[OK]   {skill}: peeks handoffs --skill {skill}")
        else:
            reason = PEEK_EXEMPT[skill]
            print(f"[OK]   {skill}: {reason} (no peek required)")
            if reason in BOARD_GROUNDED_REASONS or reason in BOARD_GROUNDED_GUESTS:
                if BOARD_GROUNDED_MARKER in text:
                    # One call: the digest IS the grounding.
                    print(f"[OK]   {skill}: {reason} grounds in the board "
                          f"(read-only, via '{BOARD_GROUNDED_MARKER}')")
                elif reason in BOARD_GROUNDED_GUESTS and RAW_BOARD_READ in text:
                    # A guest inherits the invoking run's focus with one bounded
                    # read (the board's bounded-context contract).
                    print(f"[OK]   {skill}: {reason} inherits the invoking run's "
                          f"context with one bounded read ('{RAW_BOARD_READ}')")
                elif activation_body(text):
                    # It has an activation and no digest — the read side is a
                    # promise it never keeps.
                    print(f"[ERROR] {skill}: a {reason} must ground in the "
                          f"board via one call — run '{BOARD_GROUNDED_MARKER}' "
                          f"(orient.py), or a single '{RAW_BOARD_READ}' for a "
                          f"guest, in its activation")
                    problems += 1
                else:
                    # No activation section at all (its grounding lives in a
                    # reference it loads on demand): the legacy raw read stands.
                    marker = next((m for m in BOARD_GROUNDED_LEGACY if m in text),
                                  None)
                    if marker is None:
                        print(f"[ERROR] {skill}: a {reason} must ground in the "
                              f"board (missing '{BOARD_GROUNDED_MARKER}' and "
                              f"legacy '{BOARD_GROUNDED_LEGACY[0]}')")
                        problems += 1
                    else:
                        print(f"[OK]   {skill}: {reason} grounds in the board "
                              f"(no activation section — via '{marker}')")
        posted = set(re.findall(r"handoff --to ([\w-]+)", blob))
        posted |= set(re.findall(r"mirror --key \S+ --value .*?--to ([\w-]+)", blob))
        for want in wants:
            if want not in posted:
                print(f"[ERROR] {skill}: missing post 'handoff --to {want}' "
                      f"(found: {sorted(posted) or 'none'})")
                problems += 1
            else:
                print(f"[OK]   {skill}: posts handoff --to {want}")
        if not wants and posted:
            print(f"[WARNING] {skill}: terminal skill posts {sorted(posted)} "
                  f"(harmless, informational)")
        if skill in EXPECTED_LINKS:
            # The edge may live in a steps/ file (IR, epics), so scan the blob.
            # The contract is the FULL edge — source, target AND relation: a
            # source-only check let a wrong downstream (or an undocumented
            # relation) pass as [OK] (E-044).
            want = EXPECTED_LINKS[skill]
            got = actual_edges(blob)
            missing = [e for e in want if not _edge_satisfied(e, got)]
            undeclared = sorted(e for e in got if not _edge_declared(e, want))
            if missing:
                print(f"[WARNING] {skill}: close-out graph edge missing "
                      f"{[f'{s}* -> {d}* ({r})' for s, d, r in missing]} — trace "
                      f"the relay with 'link --a <run-key> --b <next-run-key> "
                      f"--relation <informs|constrains|validates>'")
            if undeclared:
                print(f"[WARNING] {skill}: undeclared graph edge {undeclared} — "
                      f"update EXPECTED_LINKS or fix the link")
            if not (missing or undeclared):
                print(f"[OK]   {skill}: close-out traces the relay on the graph")
        if skill in EXPECTED_SUBSCRIPTIONS:
            # Subscriptions are activation-side (SKILL.md), never steps/. The
            # contract is the FULL (pattern, channel) set, not the watcher name:
            # a wrong pattern is drift, not a pass (the bmad-spec gap).
            want = {(skill, p, c) for p, c in EXPECTED_SUBSCRIPTIONS[skill]}
            got = actual_subscriptions(text)
            rogue = sorted(s for s in got if s[0] != skill)
            missing = sorted(want - got)
            extra = sorted(s for s in got if s[0] == skill and s not in want)
            if rogue:
                print(f"[WARNING] {skill}: subscribes under a foreign watcher "
                      f"{rogue} — the change radar must watch as '{skill}'")
            if missing:
                print(f"[WARNING] {skill}: change-radar subscription missing "
                      f"{[(p, c) for _, p, c in missing]} — run "
                      f"'subscribe --watcher {skill} --pattern \"<p>\" "
                      f"--channel <c>'")
            if extra:
                print(f"[WARNING] {skill}: change-radar subscription not in the "
                      f"declared contract {[(p, c) for _, p, c in extra]} — "
                      f"update EXPECTED_SUBSCRIPTIONS or drop the watch")
            if not (rogue or missing or extra):
                pats = ", ".join(sorted(p for p, _ in EXPECTED_SUBSCRIPTIONS[skill]))
                print(f"[OK]   {skill}: watches its upstreams ({pats})")

    problems += check_forwarders()
    problems += check_receivers()
    problems += check_sender_attribution()
    problems += check_digest_first()
    problems += check_targeted_config_reads()
    problems += check_no_module_dump_in_skills()
    problems += check_bounded_board_reads()

    if problems:
        print(f"[ERROR] handoff wiring: {problems} problem(s)")
        return 1
    print(f"[OK] handoff wiring: all {len(EXPECTED_POSTS)} chain skills and "
          f"{len(FORWARDERS)} shim(s) match the chain contract")
    return 0


def check_digest_first() -> int:
    """The chain's run openers must START from the digest, not from dumps.

    A real session (2026-09-21, see docs/research/B-001) opened three skills
    with `resolve_config.py --module bmm` and then re-ran the dropped output
    repeatedly, because a full merged dump does not survive the transport —
    it arrives truncated, so the agent runs it again. `orient.py` answers the
    same questions in ~20 lines. This checks the first half for the openers —
    that the digest is the activation's first command. The dump half lives in
    check_targeted_config_reads(), which covers *every* activation.
    """
    problems = 0
    for skill in DIGEST_FIRST:
        md = SKILLS / skill / "SKILL.md"
        if not md.is_file():
            print(f"[ERROR] {skill}: SKILL.md missing")
            problems += 1
            continue
        text = md.read_text(encoding="utf-8", errors="replace")
        body = activation_body(text)
        if "orient.py" not in body:
            print(f"[ERROR] {skill}: activation does not run the orientation "
                  f"digest (orient.py) — run-openers start from the digest")
            problems += 1
        else:
            print(f"[OK]   {skill}: activation starts from the orientation digest")
    return problems


def check_targeted_config_reads() -> int:
    """No activation may read the config with a *dump* — module-scoped or not.

    Measured (2026-09-22/23, this plugin): `resolve_config.py --module bmm` is
    449 B / 12 lines and the same script with neither flag prints every key of
    every layer — 240 lines / 13.7 KB — far past the transport cut that made a
    real session re-run it (docs/research/B-001); `--key core.project_name` is
    55 B. The module dump did not truncate, but it hands a surface keys it never
    reads and teaches "run the dump, read what lands" — the habit behind the
    loop. So every activation names the keys it reads, and a skill that needs
    three keys prints three keys. The extractor is case-tolerant on purpose:
    bmad-eval-runner's `## On activation` was invisible to the old check.
    """
    problems = 0
    for md in sorted(SKILLS.glob("*/SKILL.md")):
        text = md.read_text(encoding="utf-8", errors="replace")
        body = activation_body(text)
        for ln in body.splitlines():
            if "resolve_config.py" not in ln or "--key" in ln:
                continue
            shape = ("module dump (`--module`)" if MODULE_DUMP_RE.search(ln)
                     else "unbounded dump (neither `--key` nor `--module`)")
            print(f"[ERROR] {md.parent.name}: activation reads config with a "
                  f"{shape} — name the keys with --key (docs/research/B-001)")
            problems += 1
    if not problems:
        print("[OK]   no activation reads config with a dump — every one names "
              "its keys (--key)")
    return problems


def check_no_module_dump_in_skills() -> int:
    """The module dump is banned across the shipped skill surface, not just
    activations.

    A step file, phase workflow manifest or scaffold template carries the same
    instruction into a run mid-flight, and a template writes it into every skill
    generated afterwards. 25 such lines sat outside the activation bodies until
    2026-09-23 (TEA workflow YAMLs, the WDS phase steps, the WDS XML manifest,
    the builder templates). One rule for the package: read config targeted.
    """
    problems = 0
    for path in sorted(SKILLS.rglob("*")):
        if (not path.is_file() or "__pycache__" in path.parts
                or path.suffix not in (".md", ".yaml", ".yml", ".xml")):
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for num, ln in enumerate(lines, 1):
            if not MODULE_DUMP_RE.search(ln):
                continue
            print(f"[ERROR] {path.relative_to(PLUGIN)}:{num}: module dump "
                  f"(`resolve_config.py --module`) — read targeted (--key)")
            problems += 1
    if not problems:
        print("[OK]   no skill file reads config with a module dump")
    return problems


def check_bounded_board_reads() -> int:
    """An activation grounds in the board with ONE call, never the raw pair.

    The digest is that call for every board-grounded surface. What no activation
    may spell is the two-call board read the digest replaced — `read --context`
    plus an unqualified `handoffs` listing (docs/research/B-001) — and a
    digest-first surface may not spell `read --context` at all, because that is
    precisely the read its step 1 already carries. Two deliberate exceptions,
    neither of them the pair: a guest contributor that only needs the invoking
    run's focus keeps the single bounded `read --context` (the board module),
    and a front door may keep the bare baton listing as its fallback for when
    the digest could not run (the relay position then degrades to "unknown"
    rather than costing a second read).
    """
    problems = 0
    for md in sorted(SKILLS.glob("*/SKILL.md")):
        skill = md.parent.name
        text = md.read_text(encoding="utf-8", errors="replace")
        body = activation_body(text)
        if not body:
            continue
        has_context = RAW_BOARD_READ in body
        has_bare_listing = bool(RAW_HANDOFFS_RE.search(body))
        if has_context and has_bare_listing:
            print(f"[ERROR] {skill}: activation spells the raw two-call board "
                  f"read (`read --context` + bare `handoffs`) — the digest "
                  f"replaced that pair")
            problems += 1
        elif has_context and skill in DIGEST_FIRST:
            print(f"[ERROR] {skill}: opens from the digest and still spells "
                  f"`read --context` — its step 1 already carries that read")
            problems += 1
    if not problems:
        print("[OK]   board reads are bounded: no activation spells the raw pair, "
              "and no digest-first surface re-reads the context")
    return problems


# --- freshness contract (E-053 / P7) ------------------------------------------
# The witness producer must exist, every verdict consumer must come through the
# single derivation, and no surface may read the RAW alert plane (whose entries
# carry no verdict). All three sets are DERIVED from the tree by AST — the list
# is never hand-maintained, so a new surface is checked the moment it lands
# (E-049's doctrine), and deleting a surface's freshness read turns this red.
VERDICT_VOCAB = ("CURRENT", "STALE", "UNKNOWN", "RETIRED")
_WITNESS_EVENTS = ('"event": "witness"', '"event": "retire"')
_RAW_PLANE = '"alerts"'
# These are the only raw-plane roles outside blackboard.py: doctor compares the
# stored alert payload with event-log replay as an integrity check, the handoff
# editor preserves a signal's stable identity while replacing it in place, and
# _waiting_signals is the single producer that annotates raw handoffs with the
# freshness verdict. None of them delivers a raw alert as actionable work.
_RAW_PLANE_MAINTENANCE = {
    ("blackboard_diagnostics.py", "doctor"),
    ("blackboard_handoff.py", "replace_handoff"),
    ("blackboard_handoff.py", "_waiting_signals"),
}


def _freshness_scan_paths() -> list:
    """Every shipped module that may carry a verdict surface."""
    engine = PLUGIN / "hooks" / "engine"
    return (sorted((engine / "modules").glob("*.py"))
            + [engine / "main.py"]
            + [PLUGIN / "bmad" / "scripts" / "blackboard.py",
               PLUGIN / "skills" / "bmad-research-experiment" / "scripts" / "run_experiment.py"])


def _module_functions(source: str) -> list:
    """(module, name, body-lines, line) for every function in the source.

    Nested functions are listed under their own name too: a helper that builds
    a claim shape is as much a surface as the function that calls it.
    """
    import ast
    lines = source.splitlines()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, "end_lineno", None) or node.lineno
            out.append((node.name, lines[node.lineno - 1:end], node.lineno))
    return out


def _uses_freshness(source: str) -> bool:
    """Does this module obtain its verdicts from the plane?

    Accepts the module alias (`fr.STALE`, `bb.fr.mode()`) or a direct import —
    the point is that the verdicts come FROM freshness, not that a particular
    spelling was used.
    """
    return bool(re.search(r"\b(?:fr|freshness)\.\w+", source)) or bool(
        re.search(r"^\s*(?:from\s+\.?\s*import\s+freshness"
                  r"|import\s+freshness\b)", source, re.MULTILINE))


def _mentions_verdict(source: str) -> list:
    """Names of the functions whose CODE touches a verdict (AST, not prose)."""
    import ast
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for inner in ast.walk(node):
            hit = False
            if isinstance(inner, ast.Constant) and inner.value == "verdict":
                hit = True
            elif isinstance(inner, ast.Attribute) and inner.attr == "verdict":
                hit = True
            if hit:
                out.append(node.name)
                break
    return out


def _code_constants(source: str) -> list:
    """(line, value) for every verdict literal in CODE (docstrings excluded)."""
    import ast
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    docstrings = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                              ast.AsyncFunctionDef)) and body):
            first = body[0]
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                docstrings.add(id(first.value))
    out = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and node.value in VERDICT_VOCAB and id(node) not in docstrings):
            out.append((node.lineno, node.value))
    return out


def check_freshness_contract() -> int:
    """The freshness plane's producer/consumer contract, derived from the tree.

    Four mechanical questions:
      1. Is there a WRITER? (a `witness`/`retire` event producer)
      2. Is the verdict VOCABULARY defined once? A literal `"STALE"` outside
         freshness.py is a second definition waiting to drift.
      3. Does every module that speaks about a verdict obtain it FROM the
         plane? A surface that builds one of its own is the "second opinion"
         the doctrine forbids.
      4. Does any surface read the RAW alert plane? Entries there carry no
         verdict at all, so a reader of it can only deliver live work — the
         exact defect E-052 measured.

    Everything is derived, never listed: a surface added tomorrow is checked
    on the next run, and removing a freshness read turns this red.
    """
    problems = 0
    sources: dict = {}
    for path in _freshness_scan_paths():
        if not path.is_file():
            continue
        try:
            sources[path] = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

    # 1. A writer must exist (a read plane with no producer is a dead plane).
    producers = []
    for path, source in sources.items():
        for name, body, line in _module_functions(source):
            text = "\n".join(body)
            if any(ev in text for ev in _WITNESS_EVENTS):
                producers.append(f"{path.name}:{name}")
    if not producers:
        print("[ERROR] freshness: no witness/retire producer in the tree — the "
              "plane stores nothing, so every verdict would read UNKNOWN")
        problems += 1

    consumers = []
    for path, source in sources.items():
        if path.name == "freshness.py":
            continue

        # 2. One vocabulary: the verdict literals live in freshness.py alone.
        for line, value in _code_constants(source):
            print(f"[ERROR] freshness: {path.name}:{line} spells the verdict "
                  f"literal {value!r} — use freshness.{value} (the plane's own "
                  f"constant) so the vocabulary has ONE definition")
            problems += 1

        # 3. A module that speaks about a verdict must obtain it from the plane.
        #    "Speaks" is read from the AST — a docstring that names the concept
        #    is prose, not a surface (utils.py mentions it exactly that way).
        speaks = _mentions_verdict(source)
        if speaks and not _uses_freshness(source):
            print(f"[ERROR] freshness: {path.name} carries a verdict "
                  f"({', '.join(speaks[:3])}) without obtaining it from the "
                  f"freshness module — a surface that derives its own verdict "
                  f"is a second source of truth")
            problems += 1
        elif speaks:
            consumers.extend(f"{path.name}:{name}" for name in speaks)

        # 4. No surface may read the raw alert plane instead of the annotated one.
        for name, body, line in _module_functions(source):
            text = "\n".join(body)
            if (path.name != "blackboard.py" and _RAW_PLANE in text
                    and "read_board" in text
                    and (path.name, name) not in _RAW_PLANE_MAINTENANCE):
                print(f"[ERROR] freshness: {path.name}:{name} (line {line}) reads "
                      f"the raw alert plane — those entries carry no verdict; "
                      f"go through pending_handoffs/pending_handoff_channels/"
                      f"recent_handoffs (the annotated accessors)")
                problems += 1

    if not problems:
        print(f"[OK]   freshness contract: {len(producers)} producer(s), "
              f"{len(consumers)} verdict consumer(s), no unannotated alert consumer")
    return problems


def check_receivers() -> int:
    """Every declared hop receiver must be registered AND able to claim its
    baton.

    Two failure modes this closes: a receiver nobody hands off to being marked
    an 'entry point' (the stage would never look at its channel, so the baton
    waits until it goes stale), and a receiver missing from EXPECTED_POSTS
    altogether (the lint never checked its peek — the same silence, one level
    down).
    """
    receivers = {t for wants in EXPECTED_POSTS.values() for t in wants}
    problems = 0
    for skill in sorted(receivers):
        if skill not in EXPECTED_POSTS:
            senders = [s for s, w in EXPECTED_POSTS.items() if skill in w]
            print(f"[ERROR] {skill}: receives from {senders} but is not a "
                  f"registered chain member")
            problems += 1
        reason = PEEK_EXEMPT.get(skill)
        if reason in NO_UPSTREAM_REASONS:
            senders = [s for s, w in EXPECTED_POSTS.items() if skill in w]
            print(f"[ERROR] {skill}: declared '{reason}' but {senders} hand "
                  f"off to it (the baton could never be claimed)")
            problems += 1
    if not problems:
        print(f"[OK]   hop receivers: all {len(receivers)} registered and claimable")
    return problems


def check_sender_attribution() -> int:
    """A shared run-key namespace must be disambiguated at the source.

    chain_health attributes a signal by its from-key prefix; when two stages
    write the same namespace the prefix credits the wrong one. Every stage but
    the namespace owner must stamp `--sender <self>` on its close-out.
    """
    problems = 0
    for prefix, stages in SHARED_NAMESPACES.items():
        for skill in stages[1:]:
            md = SKILLS / skill / "SKILL.md"
            if not md.is_file():
                continue
            blob = md.read_text(encoding="utf-8", errors="replace")
            for f in sorted((SKILLS / skill).rglob("*.md")):
                if f.name != "SKILL.md" and "__pycache__" not in f.parts:
                    blob += "\n" + f.read_text(encoding="utf-8", errors="replace")
            bridge = PLUGIN / "custom" / f"{skill}.toml"
            if bridge.is_file():
                blob += "\n" + bridge.read_text(encoding="utf-8", errors="replace")
            if f"--sender {skill}" not in blob:
                print(f"[ERROR] {skill}: writes the shared '{prefix}' namespace "
                      f"without '--sender {skill}' — the diagnostic would "
                      f"credit {stages[0]}")
                problems += 1
            else:
                print(f"[OK]   {skill}: stamps --sender for the shared '{prefix}' namespace")
    return problems


def check_forwarders() -> int:
    """Deprecated shims must be marked DEPRECATED, name the canonical skill
    they forward to, and that skill must itself be a chain member — otherwise
    the shim is a dead end that silently drops the baton."""
    problems = 0
    for shim, canonical in FORWARDERS.items():
        md = SKILLS / shim / "SKILL.md"
        if not md.is_file():
            print(f"[ERROR] {shim}: shim SKILL.md missing")
            problems += 1
            continue
        text = md.read_text(encoding="utf-8", errors="replace")
        if "DEPRECATED" not in text:
            print(f"[ERROR] {shim}: not marked DEPRECATED")
            problems += 1
        elif canonical not in text:
            print(f"[ERROR] {shim}: never names its canonical skill {canonical}")
            problems += 1
        elif canonical not in EXPECTED_POSTS:
            print(f"[ERROR] {shim}: forwards to {canonical}, not a chain member")
            problems += 1
        else:
            print(f"[OK]   {shim}: deprecated shim -> {canonical}")
    return problems


def negtest_freshness() -> int:
    """Break the contract the freshness check derives and require it to go red.

    The violation planted is the measured defect itself: a surface reading the
    RAW alert plane, whose entries carry no verdict, so it can only deliver a
    claim as live work. Restored byte-exact in `finally`.
    """
    victim = PLUGIN / "hooks" / "engine" / "modules" / "stop.py"
    orig = victim.read_bytes()
    try:
        text = orig.decode("utf-8")
        planted = text.replace(
            "from .blackboard import pending_alerts",
            "from .blackboard import read_board, pending_alerts", 1)
        planted = planted.replace(
            "        stop_alerts = pending_alerts(root, \"stop\")",
            "        stop_alerts = [a for a in read_board(root)[\"alerts\"] "
            "if a.get('channel') == 'stop']", 1)
        assert planted != text, "negtest premise gone: stop.py stop-channel peek changed"
        victim.write_text(planted, encoding="utf-8")
        rc = check_freshness_contract()
        if rc == 0:
            print("  [ERROR] raw-plane read NOT caught by the freshness contract")
            return 1
        print("  [OK] raw-plane read caught, problems>0")
    finally:
        victim.write_bytes(orig)
    return 0


def negtest() -> int:
    skill = SKILLS / "bmad-ux" / "SKILL.md"
    orig = skill.read_text(encoding="utf-8")
    # Break the bmad-ux post in whichever command form it currently uses
    # (handoff --to / mirror --to ... --to).
    broken = orig.replace("--to bmad-architecture", "--to bmad-NOWHERE", 1)
    assert broken != orig, "negtest premise gone: bmad-ux posts nowhere"
    try:
        skill.write_text(broken, encoding="utf-8")
        # stdin=DEVNULL: the negtest re-runs this script; the probe must not
        # inherit a stdio handle the host cannot duplicate (WinError 6).
        r = subprocess.run([sys.executable, str(Path(__file__).resolve())],
                           capture_output=True, text=True, encoding="utf-8",
                           timeout=60, cwd=str(PLUGIN),
                           stdin=subprocess.DEVNULL)
        if r.returncode == 1 and "bmad-ux" in r.stdout:
            print("  [OK] wiring break caught, exit=1")
        else:
            print(f"  [ERROR] wiring break expected, rc={r.returncode} "
                  f"out=...{r.stdout[-400:]!r}")
            return 1
    finally:
        skill.write_text(orig, encoding="utf-8")
    rc = negtest_freshness()
    if rc != 0:
        return rc
    print("[OK] check-handoff negtest passed")
    return 0


def runtime_check(project_root: str) -> int:
    """Live-board check: is the relay actually flowing at project_root?

    Reads the event log (authoritative) for methodology run-key writes
    (E-/IR-/SP-/S-/QR-/PR- prefixes) and handoff alert/consume events.
    Exit 0 = live relay (run keys + at least one handoff handshake observed);
    exit 1 = silent board (wiring may be fine, but nothing ever flowed —
    investigate gate mirror / skill close-out / project-root mismatch).
    Never crashes: missing log = silent.
    """
    import json
    root = Path(project_root).resolve()
    log = root / ".metodoloji" / "logs" / "blackboard-events.log"
    run_keys: set = set()
    posted = 0
    consumed = 0
    try:
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("event") == "write":
                key = str(ev.get("key", ""))
                if re.match(r"^(E-|IR-|SP-|S-|QR-|PR-)", key):
                    run_keys.add(key)
            elif ev.get("event") == "alert" and ev.get("kind") == "handoff":
                posted += 1
            elif ev.get("event") == "consume" and str(
                    ev.get("channel", "")).startswith("handoff."):
                consumed += 1
    except OSError:
        print(f"[WARNING] runtime: no event log at {log} — board never written "
              f"(gate mirror / skill close-out never ran here, or wrong --project-root)")
        return 1
    print(f"[INFO] runtime: {len(run_keys)} methodology run key(s), "
          f"{posted} handoff signal(s) posted, {consumed} channel(s) consumed @ {root}")
    if not run_keys:
        print("[WARNING] runtime: board silent — no E-/IR-/SP-/S-/QR-/PR- run key "
              "ever written. If gates ran APPROVED here, the board mirror is "
              "not reaching this root (check gate --project-root / skill cwd).")
        return 1
    if posted == 0:
        print("[WARNING] runtime: run keys exist but no handoff ever posted — "
              "the relay starts but never hands off (check close-out handoff).")
        return 1
    print("[OK] runtime: live relay observed on this board")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="handoff wiring lint (+ live-board check)")
    ap.add_argument("--negtest", action="store_true")
    ap.add_argument("--freshness-only", action="store_true",
                    help="run ONLY the witness producer/consumer contract "
                         "(E-053); check-plugin.sh §6h calls this")
    ap.add_argument("--runtime", action="store_true",
                    help="also inspect the live board event log")
    ap.add_argument("--project-root", default=".",
                    help="project root for --runtime (default: cwd)")
    ns = ap.parse_args()
    if ns.negtest:
        sys.exit(negtest())
    if ns.freshness_only:
        sys.exit(1 if check_freshness_contract() else 0)
    rc = check()
    if ns.runtime:
        rc = runtime_check(ns.project_root) or rc
    sys.exit(rc)

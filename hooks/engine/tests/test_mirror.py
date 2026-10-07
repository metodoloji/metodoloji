"""Mirror tests — the central chain-heartbeat entry point.

Covers: stage attribution, heartbeat write (+ methodology.last_* relay),
hand-off posting, idempotent re-post (no duplicate waiting signal),
fail-open paths (empty key, missing root, disabled env, broken board).
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules import blackboard as bb  # noqa: E402
from modules import mirror as mir  # noqa: E402


@pytest.fixture()
def root():
    with tempfile.TemporaryDirectory() as td:
        yield td


def test_stage_of_known_prefixes():
    assert mir.stage_of("E-001") == "experiment"
    assert mir.stage_of("IR-2026-01") == "readiness"
    assert mir.stage_of("SP-2026-01") == "sprint"
    assert mir.stage_of("story.1-1-x") == "story"
    assert mir.stage_of("QR-001") == "quality"
    assert mir.stage_of("PR-001") == "readiness-review"
    assert mir.stage_of("prd.acme") == "prd"
    assert mir.stage_of("whatever") == "custom"
    assert mir.stage_of("") == "custom"


def test_stage_of_sub_chain_prefixes():
    assert mir.stage_of("forge.my-idea") == "forge"
    assert mir.stage_of("brainstorm.topic") == "brainstorm"
    assert mir.stage_of("brief.acme") == "brief"
    assert mir.stage_of("prfaq.acme") == "prfaq"
    assert mir.stage_of("research.market.tam") == "market-research"
    assert mir.stage_of("research.domain.vertical") == "domain-research"
    assert mir.stage_of("research.tech.stack") == "technical-research"
    assert mir.stage_of("quickdev.3-2-digest") == "quick-dev"
    assert mir.stage_of("devauto.gh-47-fix-auth") == "dev-auto"
    assert mir.stage_of("agentdev.3-2-digest") == "agent-dev"
    assert mir.stage_of("cis.design.2026-09-17-onboarding") == "cis-design-thinking"
    assert mir.stage_of("cis.innovation.x") == "cis-innovation-strategy"
    assert mir.stage_of("cis.solving.x") == "cis-problem-solving"
    assert mir.stage_of("cis.story.x") == "cis-storytelling"
    assert mir.stage_of("test.atdd.3-2-digest") == "test-atdd"
    assert mir.stage_of("test.automate.x") == "test-automate"
    assert mir.stage_of("test.ci.acme") == "test-ci"
    assert mir.stage_of("test.framework.acme") == "test-framework"
    assert mir.stage_of("test.nfr.api") == "test-nfr"
    assert mir.stage_of("test.design.epic-1") == "test-design"
    assert mir.stage_of("test.review.svc") == "test-review"
    assert mir.stage_of("test.trace.R-1") == "test-trace"
    assert mir.stage_of("test.e2e.checkout") == "test-e2e"
    # specific prefixes win over the generic test. fallback
    assert mir.stage_of("test.unknown-namespace") == "testing"
    assert mir.stage_of("teach.ada") == "teaching"
    assert mir.stage_of("retro.epic-1") == "retrospective"
    assert mir.stage_of("change.2026-09-17") == "correct-course"
    # GDS vertical (game) — specific before generic
    assert mir.stage_of("gds.gdd.acme") == "gds-gdd"
    assert mir.stage_of("gds.quickdev.slug") == "gds-quick-dev"
    assert mir.stage_of("gds.test.review.R-1") == "gds-test-review"
    assert mir.stage_of("gds.retro.4") == "gds-retro"
    assert mir.stage_of("gds.anything") == "gds"
    # WDS vertical (web design) — numbered phases before generic
    assert mir.stage_of("wds.brief.acme") == "wds-brief"
    assert mir.stage_of("wds.ux.checkout") == "wds-ux"
    assert mir.stage_of("wds.evolve.c2") == "wds-evolve"
    assert mir.stage_of("wds.anything") == "wds"


def test_mirror_vertical_hops_never_stamp_methodology_relay(root):
    """GDS/WDS run keys mirror fine (fail-open, attributed) but are diagnostic
    only: the verticals run beside the E→IR→SP→S→QR→PR relay, so no gds./wds.
    heartbeat may write a methodology.last_* position."""
    ack = mir.mirror(root, "gds.dev.3-1-boss-ai", "dev complete",
                     to="gds-code-review", note="fold findings into the QR")
    assert ack["ok"] is True and ack["stage"] == "gds-dev"
    board = bb.read_board(root)
    stamped = [k for k in board["keys"] if k.startswith("methodology.last_")]
    assert stamped == [], f"vertical heartbeat stamped a relay position: {stamped}"


def test_mirror_governance_hops_close_the_loop(root):
    """Retrospective -> experiment is the hop that closes E→IR→SP→S→QR→PR back
    onto E, and correct-course -> sprint-planning re-enters at the surface that
    owns the backlog. Both are side loops, not stages of the relay, so neither
    stamps a methodology position."""
    ack = mir.mirror(root, "retro.4", "retro complete — 3 action items",
                     to="bmad-research-experiment",
                     note="the lesson that needs an experiment")
    assert ack["ok"] is True and ack["stage"] == "retrospective"
    assert ack["handed_off"] is True and ack["dedup"] is False
    assert bb.pending_handoffs(root, "bmad-research-experiment")[0]["text"].startswith(
        "retro.4")
    assert not [k for k in bb.read_board(root)["keys"]
                if k.startswith("methodology.last_")]
    # the loop-closing channel is the SAME one the origin posts to, so a stale
    # retro baton and a fresh experiment origin cannot be told apart by channel
    assert bb.pending_handoff_channels(root) == {"bmad-research-experiment": 1}
    ack2 = mir.mirror(root, "change.2026-09-17", "Sprint change proposal: Major scope",
                      to="bmad-sprint-planning", note="re-cut the backlog")
    assert ack2["stage"] == "correct-course" and ack2["handed_off"] is True
    assert bb.pending_handoff_channels(root) == {"bmad-research-experiment": 1,
                                                "bmad-sprint-planning": 1}


def test_mirror_tea_feeder_signals_the_quality_record(root):
    """A TEA feeder adds evidence to an existing QR: it binds a run key and
    signals the quality record, without stamping an E→IR→SP→S→QR→PR position
    (it is a feeder, not a stage of that relay)."""
    ack = mir.mirror(root, "test.design.epic-1", "test design complete",
                     to="bmad-quality-record", note="fold into the QR")
    assert ack["ok"] is True and ack["stage"] == "test-design"
    assert ack["handed_off"] is True and ack["dedup"] is False
    pending = bb.pending_handoffs(root, "bmad-quality-record")
    assert pending[0]["text"].startswith("test.design.epic-1")
    assert not [k for k in bb.read_board(root)["keys"]
                if k.startswith("methodology.last_")]
    # repeat — idempotent, the QR channel is not flooded
    ack2 = mir.mirror(root, "test.design.epic-1", "test design complete",
                      to="bmad-quality-record", note="fold into the QR")
    assert ack2["dedup"] is True
    assert len(bb.pending_handoffs(root, "bmad-quality-record")) == 1


def test_mirror_alt_dev_branches_converge_on_the_review_gate(root):
    """quick-dev / dev-auto are alternative implementations of the dev stage:
    they bind a run key and converge on the same formal review gate. They are
    NOT methodology stages, so no E→IR→SP→S→QR→PR position is stamped."""
    ack = mir.mirror(root, "quickdev.3-2-digest", "quick-dev done",
                     to="bmad-code-review", note="review it")
    assert ack["ok"] is True and ack["stage"] == "quick-dev"
    assert ack["handed_off"] is True and ack["dedup"] is False
    pending = bb.pending_handoffs(root, "bmad-code-review")
    assert pending[0]["text"].startswith("quickdev.3-2-digest")
    assert "methodology.last_story" not in bb.read_board(root)["keys"]
    # repeat — idempotent, the channel is not flooded
    ack2 = mir.mirror(root, "quickdev.3-2-digest", "quick-dev done",
                      to="bmad-code-review", note="review it")
    assert ack2["dedup"] is True and ack2["handed_off"] is False
    assert len(bb.pending_handoffs(root, "bmad-code-review")) == 1


def test_mirror_carries_the_sender_for_a_shared_run_key(root):
    """The delivery close-out writes `story.` — the namespace create-story
    opened. Mirror passes the posting stage through, so the terminal hop is
    attributed to the stage that actually finished (dev-story), not to the
    prefix's owner."""
    ack = mir.mirror(root, "story.1-1-x", "dev complete — story in review",
                     to="bmad-code-review", note="review it",
                     sender="bmad-dev-story")
    assert ack["ok"] is True and ack["handed_off"] is True
    h = bb.chain_health(root)
    terminal = [r for r in h["chain"] if r.get("terminal")]
    assert [(r["from"], r["to"]) for r in terminal] == [
        ("bmad-dev-story", "bmad-code-review")]
    assert terminal[0]["waiting"] == 1
    assert h["extra"] == []  # a declared hop, not off-chain noise
    # dedup still keys off the run key, so re-running the close-out is safe
    ack2 = mir.mirror(root, "story.1-1-x", "dev complete — story in review",
                      to="bmad-code-review", note="review it",
                      sender="bmad-dev-story")
    assert ack2["dedup"] is True


def test_mirror_heartbeat_only(root):
    ack = mir.mirror(root, "E-021", "APPROVED: m=1.0")
    assert ack["ok"] is True and ack["key"] == "E-021"
    assert ack["stage"] == "experiment" and ack["handed_off"] is False
    assert ack["root"] == os.path.abspath(root)
    board = bb.read_board(root)
    assert board["keys"]["E-021"]["value"].startswith("APPROVED")
    assert board["keys"]["methodology.last_experiment"]["value"].startswith("E-021")


def test_mirror_with_handoff_posts_once(root):
    ack = mir.mirror(root, "E-022", "APPROVED", to="bmad-check-implementation-readiness",
                     note="see record")
    assert ack["handed_off"] is True and ack["dedup"] is False
    assert len(bb.pending_handoffs(root, "bmad-check-implementation-readiness")) == 1
    # repeat — idempotent, no duplicate waiting signal
    ack2 = mir.mirror(root, "E-022", "APPROVED", to="bmad-check-implementation-readiness",
                      note="see record")
    assert ack2["handed_off"] is False and ack2["dedup"] is True
    assert len(bb.pending_handoffs(root, "bmad-check-implementation-readiness")) == 1


def test_mirror_after_consume_posts_again(root):
    mir.mirror(root, "SP-1", "done", to="bmad-create-story", note="queue")
    bb.consume_alerts(root, "handoff.bmad-create-story")  # handshake complete
    ack = mir.mirror(root, "SP-1", "done", to="bmad-create-story", note="queue")
    assert ack["handed_off"] is True  # new baton, not a duplicate


def test_mirror_fan_out_posts_both_channels(root):
    # The create-story bridge: one heartbeat, two downstream signals.
    a = mir.mirror(root, "story.1-1-x", "ready", to="bmad-dev-story", note="dev")
    b = mir.mirror(root, "story.1-1-x", "ready", to="bmad-quality-record",
                   note="qr")
    assert a["handed_off"] is True and b["handed_off"] is True
    assert len(bb.pending_handoffs(root, "bmad-dev-story")) == 1
    assert len(bb.pending_handoffs(root, "bmad-quality-record")) == 1


def test_mirror_empty_key_rejected(root):
    assert mir.mirror(root, "  ", "v")["ok"] is False


def test_mirror_missing_root_is_fail_open():
    assert mir.mirror(None, "E-023", "v")["ok"] is False  # never raises


def test_mirror_disabled_env_skips(root, monkeypatch):
    monkeypatch.setenv("METODOLOJI_NO_BLACKBOARD", "1")
    ack = mir.mirror(root, "E-024", "APPROVED", to="bmad-check-implementation-readiness")
    assert ack["ok"] is True and ack.get("skipped") is True
    assert "E-024" not in bb.read_board(root)["keys"]


def test_mirror_cli_roundtrip(tmp_path):
    import json
    import subprocess
    cli = (Path(__file__).resolve().parent.parent.parent.parent
           / "bmad" / "scripts" / "blackboard.py")
    r = subprocess.run(
        [sys.executable, str(cli), "mirror", "--key", "IR-9",
         "--value", "readiness: READY", "--to", "bmad-sprint-planning",
         "--note", "verdict", "--project-root", str(tmp_path)],
        capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    ack = json.loads(r.stdout)
    assert ack["ok"] is True and ack["handed_off"] is True
    assert r.returncode == 0
    # and the relay position followed automatically
    board = bb.read_board(str(tmp_path))
    assert board["keys"]["methodology.last_ir"]["value"].startswith("IR-9")

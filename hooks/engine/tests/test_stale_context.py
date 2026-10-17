"""Read-path tests for the freshness plane (E-053, P2/P3).

The claims that matter here are about the DELIVERY surfaces: a signal whose
scope moved must be delivered as something other than live work, the session
inject must stop repeating it in the present tense, retirement must be
replay-stable, and a re-measured record must cascade onto the claims that cite
it.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

_HOOKS = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HOOKS))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "bmad" / "scripts"))

from modules import audit as audit_mod  # noqa: E402
from modules import blackboard as bb  # noqa: E402
from modules import freshness as fr  # noqa: E402
from modules import mirror as mir  # noqa: E402

RECEIVER_STALE = "bmad-check-implementation-readiness"
RECEIVER_CURRENT = "bmad-architecture"


@pytest.fixture(autouse=True)
def _clear_cache():
    fr.reset_cache()
    yield
    fr.reset_cache()


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=30, stdin=subprocess.DEVNULL)


def _commit(root, message):
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=t@example.invalid", "-c", "user.name=t",
         "commit", "-q", "-m", message)


def _record(root, key, scope, extra=""):
    path = root / "docs" / "experiments" / f"{key}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"## Experiment: {key} — fixture\n"
                    f"- **Status:** completed\n"
                    f"- **Code Scope:** {scope}\n"
                    f"- **Decision:** APPROVED — fixture\n{extra}",
                    encoding="utf-8")
    return path


@pytest.fixture()
def repo(tmp_path):
    """git repo: `src/**` claim (moves) + `lib/**` claim (does not)."""
    (tmp_path / "src").mkdir()
    (tmp_path / "lib").mkdir()
    (tmp_path / "src" / "a.py").write_text("# a\n", encoding="utf-8")
    (tmp_path / "lib" / "b.py").write_text("# b\n", encoding="utf-8")
    _record(tmp_path, "E-900", "src/**")
    _record(tmp_path, "E-901", "lib/**")
    _git(tmp_path, "init", "-q")
    _commit(tmp_path, "baseline")
    bb.post_handoff(str(tmp_path), RECEIVER_STALE, "E-900", "stale probe")
    bb.post_handoff(str(tmp_path), RECEIVER_CURRENT, "E-901", "current probe")
    (tmp_path / "src" / "a.py").write_text("# a v2\n", encoding="utf-8")
    _commit(tmp_path, "engine moves on")
    fr.reset_cache()
    return tmp_path


def _inject(root) -> str:
    return audit_mod.session_start({"cwd": str(root)})["additionalContext"]


# --- the peek surface -----------------------------------------------------------
def test_waiting_signal_carries_the_derived_verdict(repo):
    stale = bb.pending_handoffs(str(repo), RECEIVER_STALE)
    current = bb.pending_handoffs(str(repo), RECEIVER_CURRENT)
    assert stale[0]["verdict"] == fr.STALE
    assert stale[0]["run_key"] == "E-900" and stale[0]["changed_count"] == 1
    assert stale[0]["anchor"] == "record"  # dated by the decision, not the post
    assert "scope changed" in stale[0]["reason"]
    assert current[0]["verdict"] == fr.CURRENT


def test_only_current_drops_claims_the_code_moved_past(repo):
    assert bb.pending_handoffs(str(repo), RECEIVER_STALE, only_current=True) == []
    assert len(bb.pending_handoffs(str(repo), RECEIVER_CURRENT,
                                   only_current=True)) == 1


def test_counts_and_breakdown_come_from_one_scan(repo):
    counts = bb.pending_handoff_channels(str(repo))
    report = bb.pending_handoff_freshness(str(repo))
    assert counts[RECEIVER_STALE] == 1
    assert report[RECEIVER_STALE]["stale"] == 1
    assert report[RECEIVER_STALE]["stale_keys"] == ["E-900"]
    assert report[RECEIVER_STALE]["freshness"][0]["label"].startswith("STALE (")
    assert report[RECEIVER_CURRENT]["current"] == 1
    # totals are the same scan, so they can never disagree
    assert {k: v["total"] for k, v in report.items()} == counts


def test_chain_health_reports_claims_apart_from_the_clock(repo):
    health = bb.chain_health(str(repo))
    hops = [h for h in bb.waiting_hop_rows(health)]
    assert hops and hops[0]["to"] == RECEIVER_STALE
    assert hops[0]["stale_claims"] == 1    # code moved past the claim
    assert health["stale"] == []           # nothing aged out yet
    assert health["freshness"][fr.STALE] == 1
    assert health["unclear"][0]["run_key"] == "E-900"
    assert health["freshness_mode"] in ("warn", "deny")


def test_recent_handoffs_can_carry_the_verdict(repo):
    hits = bb.recent_handoffs(str(repo), with_freshness=True)
    verdicts = {h["skill"]: h["verdict"] for h in hits}
    assert verdicts[RECEIVER_STALE] == fr.STALE
    assert verdicts[RECEIVER_CURRENT] == fr.CURRENT
    # the default shape is unchanged (pinned by other suites)
    assert all(set(h) == {"skill", "from", "note", "age_s"}
               for h in bb.recent_handoffs(str(repo)))


def test_relay_note_marks_a_stale_baton(repo):
    note = mir.pending_note(str(repo))
    assert note and "STALE" in note and "no longer valid" in note.lower()


# --- the inject -----------------------------------------------------------------
def test_inject_says_stale_instead_of_repeating_the_past(repo):
    ctx = _inject(repo)
    assert "NO LONGER VALID WORK" in ctx
    assert "E-900 (STALE (" in ctx
    assert "retire" in ctx
    # the stale channel must NOT be announced as work nobody picked up
    stale_part = ctx.split(RECEIVER_STALE, 1)[1].split(";")[0]
    assert "nobody picked up the baton" not in stale_part


def test_inject_keeps_the_current_wording_for_a_current_channel(repo):
    ctx = _inject(repo)
    current_part = ctx.split(RECEIVER_CURRENT, 1)[1]
    assert "nobody picked up the baton" in current_part
    assert "NO LONGER VALID WORK" not in current_part


def test_inject_labels_the_relay_position_when_it_moved(tmp_path):
    """A relay position written BEFORE the scope moved must carry its verdict:
    the chain-progress sentence used to repeat the decision in the present
    tense (F-03: the most durable surface was the least checked)."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("# a\n", encoding="utf-8")
    _record(tmp_path, "E-900", "src/**")
    _git(tmp_path, "init", "-q")
    _commit(tmp_path, "baseline")
    mir.mirror(str(tmp_path), "E-900", "APPROVED — engine changed",
               sender="bmad-research-experiment")
    (tmp_path / "src" / "a.py").write_text("# a v2\n", encoding="utf-8")
    _commit(tmp_path, "engine moves on")
    fr.reset_cache()
    ctx = _inject(tmp_path)
    assert "Chain progress — E: " in ctx and "[STALE (" in ctx


# --- retirement (P3) ------------------------------------------------------------
def test_retire_a_run_key_keeps_the_ts_and_stops_delivery(repo):
    before = bb.pending_handoffs(str(repo), RECEIVER_STALE)[0]["ts"]
    ack = bb.retire(str(repo), "E-900", reason="decision superseded by E-953")
    assert ack["ok"] is True and ack["signals"] == 1
    pending = bb.pending_handoffs(str(repo), RECEIVER_STALE)
    assert pending == []                                   # not delivered as work
    assert bb.retired_map(str(repo))["E-900"]["reason"].startswith("decision superseded")
    # the signal itself still exists with its ORIGINAL ts (age stays honest)
    kept = [a for a in bb.read_board(str(repo))["alerts"]
            if a.get("channel") == f"handoff.{RECEIVER_STALE}"]
    assert kept and kept[0]["ts"] == before


def test_retire_by_channel_and_by_alert_id(repo):
    bb.retire(str(repo), f"handoff.{RECEIVER_CURRENT}", reason="handled out of band")
    assert bb.pending_handoffs(str(repo), RECEIVER_CURRENT) == []
    assert bb.pending_handoff_channels(str(repo)).get(RECEIVER_CURRENT) is None
    # by alert id: the id is the precise target
    aid = bb.read_board(str(repo))["alerts"][0]["id"]
    bb.retire(str(repo), aid, reason="by id")
    assert bb.pending_handoffs(str(repo), RECEIVER_STALE) == []


def test_retirement_survives_a_snapshot_rebuild(repo):
    bb.retire(str(repo), "E-900", reason="obsolete")
    paths = bb.board_paths(str(repo))
    pathlib.Path(paths["snapshot"]).unlink()          # lose the snapshot
    fr.reset_cache()
    assert bb.pending_handoffs(str(repo), RECEIVER_STALE) == []
    assert "E-900" in bb.retired_map(str(repo))       # folded from the event log
    assert bb.doctor(str(repo))["checks"]["drift"]["in_sync"] is True


def test_retiring_produces_a_retired_alert_once(repo):
    bb.retire(str(repo), "E-900", reason="obsolete")
    bb.retire(str(repo), "E-900", reason="obsolete")       # identical → deduped
    alerts = bb.pending_alerts(str(repo), "session")
    retired = [a for a in alerts if a.get("kind") == "retired"]
    assert len(retired) == 1 and "E-900" in retired[0]["text"]


def test_cli_handoff_retire_and_witness(repo, capsys):
    import blackboard as cli
    rc = cli.main(["handoff-retire", "--retire", "E-901",
                   "--reason", "no longer applies", "--project-root", str(repo)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["target"] == "E-901"
    assert bb.pending_handoffs(str(repo), RECEIVER_CURRENT) == []
    rc = cli.main(["witness", "--target", "E-900", "--project-root", str(repo)])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["anchor"]["anchor"] == "record"       # dated by the decision
    assert payload["anchor"]["verdict"] == fr.STALE
    assert payload["witness"]["commit"]                  # derived anchor resolved


# --- cascade (the declared-but-dead plane, wired) --------------------------------
def test_re_measured_by_cascades_onto_the_old_claim(repo):
    """Writing the record that re-measures E-900 retires E-900's claims."""
    _record(repo, "E-902", "src/**")
    (repo / "docs" / "experiments" / "E-900.md").write_text(
        (repo / "docs" / "experiments" / "E-900.md").read_text(encoding="utf-8")
        + "- **Re-Measured-By:** E-902 (16.10.2026)\n", encoding="utf-8")
    fr.reset_cache()
    ack = bb.write_key(str(repo), "E-902", "APPROVED — re-measured", type_="state")
    assert ack["ok"] is True
    assert "E-900" in bb.retired_map(str(repo))
    assert bb.pending_handoffs(str(repo), RECEIVER_STALE) == []
    kinds = [a.get("kind") for a in bb.pending_alerts(str(repo), "session")]
    assert "cascade_invalidation" in kinds


def test_superseded_claim_is_stale_even_without_retirement(tmp_path):
    """The cascade reason is derived too: a claim citing a superseded record is
    STALE with `superseded by …`, not merely old."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("# a\n", encoding="utf-8")
    _record(tmp_path, "E-910", "src/**")
    _git(tmp_path, "init", "-q")
    _commit(tmp_path, "baseline")
    bb.post_handoff(str(tmp_path), RECEIVER_STALE, "E-910", "probe")
    _record(tmp_path, "E-911", "src/**")
    (tmp_path / "docs" / "experiments" / "E-910.md").write_text(
        (tmp_path / "docs" / "experiments" / "E-910.md").read_text(encoding="utf-8")
        + "- **Re-Measured-By:** E-911 (16.10.2026)\n", encoding="utf-8")
    fr.reset_cache()
    ann = fr.annotate(str(tmp_path), "E-910")
    assert ann["verdict"] == fr.STALE and "superseded by E-911" in ann["reason"]


# --- doctor ---------------------------------------------------------------------
def test_doctor_reports_the_freshness_plane(repo):
    report = bb.doctor(str(repo))
    fresh = report["checks"]["freshness"]
    assert fresh["mode"] in ("warn", "deny")
    assert fresh["witnesses"] == 2                 # one per posted claim
    assert fresh["retired"] == 0
    assert fresh["tally"][fr.STALE] == 1
    assert any("moved past" in w for w in report["signal_warnings"])


def test_doctor_post_produces_the_lifecycle_alerts(repo):
    before = len(bb.pending_alerts(str(repo), "session"))
    report = bb.doctor(str(repo), post=True)
    assert report["posted"] >= 1
    kinds = [a.get("kind") for a in bb.pending_alerts(str(repo), "session")]
    assert "witness_stale" in kinds
    assert len(bb.pending_alerts(str(repo), "session")) > before
    # read-only by default
    quiet = bb.doctor(str(repo))
    assert "posted" not in quiet


def test_doctor_splits_the_two_stale_readings(repo):
    """A >24h baton whose claim still holds is a hung skill; one whose scope
    moved is obsolete work — the engine used to prescribe "restart" for both.

    The age is backdated in BOTH the event log and the snapshot (TD-014: the
    snapshot is the live truth, so a log-only backdate stages a drift instead
    of an age) — and the snapshot is kept, because a rebuild would simply
    TTL-prune the backdated signals before the diagnosis could see them.
    """
    import time as _time
    paths = bb.board_paths(str(repo))
    old = _time.time() - (bb.HANDOFF_SIGNAL_TTL_SECONDS + 7200)
    with open(paths["events"], encoding="utf-8") as f:
        lines = f.read().splitlines()
    rewritten = []
    for line in lines:
        try:
            ev = json.loads(line)
        except ValueError:
            rewritten.append(line)
            continue
        if ev.get("event") == "alert" and ev.get("kind") == "handoff":
            ev["ts"] = old
        rewritten.append(json.dumps(ev))
    with open(paths["events"], "w", encoding="utf-8") as f:
        f.write("\n".join(rewritten) + "\n")
    with open(paths["snapshot"], encoding="utf-8") as f:
        snap = json.load(f)
    for alert in snap.get("alerts", []):
        if alert.get("kind") == "handoff":
            alert["ts"] = old
    with open(paths["snapshot"], "w", encoding="utf-8") as f:
        json.dump(snap, f)
    fr.reset_cache()
    report = bb.doctor(str(repo))
    joined = " ".join(report["warnings"])
    assert "obsolete work, not a hung skill" in joined
    assert report["checks"]["freshness"]["status"] == "warn"


# --- the write/commit surfaces (P2/P4/P6 leftovers) ------------------------------
def test_quality_gate_reports_the_freshness_row(repo, monkeypatch):
    """The commit is the last moment \"does this approval still apply?\" can be
    asked before the work leaves the tree — the row is REPORT-only at the
    shipped modes, so the decision stays `allow`."""
    from modules import guard as guard_mod
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    res = guard_mod.quality({"tool_name": "terminal",
                             "tool_input": {"command": "git commit -m 'x'"}})
    warnings = res.get("methodology_warnings") or []
    assert any("FRESHNESS:" in w for w in warnings), res
    assert res.get("decision") in (None, "allow") or "FRESHNESS" in res.get("reason", "")


def test_quality_gate_blocks_when_the_operator_asked_for_deny(repo, monkeypatch):
    """`[hooks] freshness = deny` turns the same row into a block."""
    from modules import guard as guard_mod
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    monkeypatch.setattr(fr, "blocks", lambda verdict: str(verdict).upper() == fr.STALE)
    stale = guard_mod._freshness_chain_row(str(repo), "git commit blocked")
    # The fixture records carry no Witness line, so the row is UNKNOWN-only and
    # nothing blocks; with a STALE verdict it would deny. Pin both halves.
    assert stale["decision"] == "allow"
    monkeypatch.setattr(guard_mod, "_record_freshness",
                        lambda root, rec: {"verdict": fr.STALE, "reason": "moved",
                                           "changed": ["src/a.py"], "age_s": 1.0,
                                           "commit": "deadbeef", "record": "E-900.md"})
    blocked = guard_mod._freshness_chain_row(str(repo), "git commit blocked")
    assert blocked["decision"] == "deny" and "freshness denies" in blocked["reason"]


def test_stop_report_labels_a_stale_baton_and_lists_retirements(repo, monkeypatch):
    """P2/P3 on the stop surface: a baton the code moved past is not announced
    as live work, and a withdrawal made in this session is named."""
    from modules import stop as stop_mod
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    bb.retire(str(repo), "E-901", reason="withdrawn for the test")
    fr.reset_cache()
    out = stop_mod.stop({"cwd": str(repo), "hook_event_name": "Stop"})
    reason = out.get("reason", "")
    assert "may no longer apply" in reason and "E-900" in reason
    assert "retired this session" in reason and "E-901" in reason


def test_lifecycle_alert_kinds_are_written_through_the_enum():
    """TD-016 / F-06: the declared taxonomy is a working plane, not names.

    Every kind the enum ANNOTATES as written has a producer that goes through
    the enum itself, so a rename is a rename at the writer — the exact failure
    the audit measured (a taxonomy no code referenced).
    """
    import pathlib
    import re
    source = pathlib.Path(bb.__file__).read_text(encoding="utf-8")
    annotated = re.findall(r"^\s{4}([A-Z_]+) = \"[a-z_]+\"\s+# written by", source, re.M)
    assert annotated, "the taxonomy lost its producer annotations"
    for name in annotated:
        assert f"AlertKind.{name}.value" in source, f"AlertKind.{name} has no producer"

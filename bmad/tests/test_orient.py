"""Tests for bmad/scripts/orient.py — the one-call orientation digest.

The digest exists because a real session (2026-09-21, /root/mailjs) re-derived
state all run long: full `resolve_config.py` dumps arrived truncated so it
re-ran them, the activation body arrived truncated so it re-catted SKILL.md,
and it searched the filesystem for {metodoloji-root} that the SessionStart line
had already printed. One read-only call must now answer all of it — and must
answer it correctly, which is where these tests earn their keep: a shared
record-ID regex counted every SP-026.md as an IR record too (IR=52, QR=85).
"""

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
SCRIPT = PLUGIN / "bmad" / "scripts" / "orient.py"

_spec = importlib.util.spec_from_file_location("orient", SCRIPT)
orient = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(orient)

sys.path.insert(0, str(PLUGIN / "hooks" / "engine"))
from modules import blackboard as bb  # noqa: E402


def _run(args):
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          cwd=str(PLUGIN), stdin=subprocess.DEVNULL)


def test_catalog_output_locations_match_the_skill_defaults():
    """The catalog must not point completion detection at the wrong folder.

    bmad-prd/ux/architecture write to `{project-root}/docs/design/…` (their own
    customize.toml defaults), but the catalog said `planning_artifacts` — which
    resolves to docs/planning. A real session had its PRD in docs/design/prds
    and epics in docs/planning, so a router trusting the catalog would look for
    the PRD in the wrong tree.
    """
    by_skill = {e["skill"]: e for e in orient.catalog_digest()["skills"]}
    assert by_skill["bmad-prd"]["output_location"] == "{project-root}/docs/design/prds"
    assert by_skill["bmad-ux"]["output_location"] == \
        "{project-root}/docs/design/ux-designs"
    assert by_skill["bmad-architecture"]["output_location"] == \
        "{project-root}/docs/design/architecture"


def test_catalog_digest_reports_real_ambiguity():
    digest = orient.catalog_digest()
    assert digest["available"] is True
    assert digest["skill_count"] > 50 and len(digest["modules"]) > 3
    ambiguous = digest["ambiguous_menu_codes"]
    # The codes the real session mis-routed on: CU is bmad-ux and gds-ux, CS is
    # create-story and the WDS conceptual-sketching row. SP must no longer list
    # the spec skill: bmad-spec collided with bmad-sprint-planning, and SP is
    # this methodology's own Sprint-Plan record prefix — the worst possible
    # code for a Spec skill to carry.
    owners = " ".join(ambiguous.get("SP", []))
    assert "bmad-spec" not in owners
    assert "bmad-sprint-planning" in owners
    assert "CU" in ambiguous and "CS" in ambiguous
    assert not [owners for code, owners in ambiguous.items()
                if "bmad-spec" in owners]  # SPEC is unique
    assert any(e["menu_code"] == "SPEC" and e["skill"] == "bmad-spec"
               for e in digest["skills"])
    assert digest["required"]  # help routes on the required gates


def test_record_inventory_counts_each_kind_once(tmp_path):
    dev = tmp_path / "docs" / "development"
    dev.mkdir(parents=True)
    # A shared `^(E|IR|…)-N.md$` pattern matched SP-026.md for IR, SP, QR and
    # PR at once — the first digest reported IR=52/QR=85 on this repo.
    (dev / "SP-026.md").write_text("x", encoding="utf-8")
    (dev / "IR-007.md").write_text("x", encoding="utf-8")
    (dev / "QR-009.md").write_text("x", encoding="utf-8")
    inv = orient.record_inventory(tmp_path)
    assert inv["IR"]["count"] == 1 and inv["IR"]["newest"] == "IR-007.md"
    assert inv["SP"]["count"] == 1
    assert inv["QR"]["count"] == 1
    assert "S" not in inv and "E" not in inv  # nothing invented


def test_artifact_dirs_collapse_inherited_paths(tmp_path):
    (tmp_path / "docs" / "planning").mkdir(parents=True)
    (tmp_path / "docs" / "planning" / "epics.md").write_text("x", encoding="utf-8")
    config = {"modules": {"bmm": {"planning_artifacts": "{project-root}/docs/planning"},
                          "gds": {"planning_artifacts": "{project-root}/docs/planning"}}}
    out = orient.artifact_dirs(tmp_path, str(tmp_path / "docs"), config)
    # Every module inherits core's paths; the folder is printed once.
    assert out["bmm.planning_artifacts"]["exists"] is True
    assert out["bmm.planning_artifacts"]["entries"] == ["epics.md"]
    assert out["gds.planning_artifacts"]["alias_of"] == "bmm.planning_artifacts"


def test_build_on_the_plugin_repo_is_self_consistent():
    digest = orient.build(PLUGIN)
    assert digest["plugin_root"] == str(PLUGIN)
    assert digest["project_root"] == str(PLUGIN)
    # Run-opening skills read `{date}` off the digest — it must be a real ISO
    # date, so a run never needs a second config read just for today.
    import datetime as _dt
    assert digest["today"] == _dt.date.today().isoformat()
    assert digest["skeleton"]["installed"] is True  # dogfood marker (see §6d)
    assert digest["core"]["project_name"]
    assert digest["board"]["available"] is True
    assert set(digest["module_codes"]) >= {"bmm", "tea"}


def test_build_names_the_plugin_default_project_name(tmp_path):
    """An unconfigured project must hear that its identity is a plugin default.

    Run folders are named from {project_name} (prd-{project_name}-{date}), so a
    project that never set its own name silently names artifacts after the
    plugin's neutral base value. The graph-engineering-arge session (2026-10-01)
    spent user turns working out which identity governed its record paths — and
    the shipped base layer held that project's name, which is how the leak
    reached every other install (check-plugin.sh §0b).
    """
    digest = orient.build(tmp_path)
    assert digest["core"]["project_name"] == "unconfigured"
    assert any("project_name is unset" in step for step in digest["next_steps"])


def test_build_is_quiet_about_project_name_once_configured(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "config.toml").write_text(
        '[core]\nproject_name = "workpanel"\n', encoding="utf-8")
    digest = orient.build(tmp_path)
    assert digest["core"]["project_name"] == "workpanel"
    assert not any("project_name is unset" in step for step in digest["next_steps"])


def test_build_is_read_only_and_names_the_next_step(tmp_path):
    bb.write_key(str(tmp_path), "status", "complete", type_="state")
    bb.write_key(str(tmp_path), "ux.mailjs", "discovery started")
    bb.set_hot(str(tmp_path), "ux.mailjs")  # hot without a write: the stale path
    events = Path(bb.board_paths(str(tmp_path))["events"]).read_bytes()

    digest = orient.build(tmp_path)
    assert Path(bb.board_paths(str(tmp_path))["events"]).read_bytes() == events
    # Stale focus is named as a fact, not silently routed on.
    assert digest["board"]["status_stale"] is True
    assert any("in-progress" in step for step in digest["next_steps"])
    # Skeleton absent → the hint is an executable command.
    assert any("skeleton.py --install" in step for step in digest["next_steps"])


def _write_sprint(tmp_path, body):
    native = tmp_path / "docs" / "development" / "native"
    native.mkdir(parents=True, exist_ok=True)
    (native / "sprint-status.yaml").write_text(body, encoding="utf-8")


def test_build_surfaces_epic_lag_as_next_step(tmp_path):
    """The mailjs lag: every story done, epic still in-progress, retro never
    ran. The digest must name the roll-up and the retrospective."""
    _write_sprint(tmp_path,
                  "development_status:\n"
                  "  epic-1: in-progress\n"
                  "  1-1-a: done\n"
                  "  1-2-b: done\n"
                  "  1-3-c: done\n")
    digest = orient.build(tmp_path)
    steps = " \n".join(digest["next_steps"])
    assert "roll the epic up to done" in steps
    assert "bmad-retrospective" in steps


def test_build_surfaces_review_story_as_next_step(tmp_path):
    """A story sitting in 'review' is an unfinished relay: the digest must
    point at bmad-code-review (the only thing that moves it to done)."""
    _write_sprint(tmp_path,
                  "development_status:\n"
                  "  epic-2: in-progress\n"
                  "  2-1-c: review\n"
                  "  2-2-d: done\n")
    digest = orient.build(tmp_path)
    steps = " \n".join(digest["next_steps"])
    assert "2-1-c" in steps and "bmad-code-review" in steps


def test_build_surfaces_next_ready_story(tmp_path):
    """With no live review/epic signal, the next ready story is the route."""
    _write_sprint(tmp_path,
                  "development_status:\n"
                  "  epic-3: in-progress\n"
                  "  3-1-first: ready-for-dev\n"
                  "  3-2-second: backlog\n")
    digest = orient.build(tmp_path)
    steps = " \n".join(digest["next_steps"])
    assert "3-1-first" in steps and "bmad-dev-story" in steps
    assert digest["sprint"]["found"] is True
    # The text render carries the bounded sprint line too.
    r = _run(["--project-root", str(tmp_path)])
    assert "sprint:" in r.stdout


def test_build_record_inventory_matches_the_engine_parser(tmp_path):
    """Delegation contract: orient's inventory IS state.py's inventory.

    Two parsers of the same tree is how "the digest said X, the session line
    said Y" starts — they must never diverge.
    """
    dev = tmp_path / "docs" / "development"
    dev.mkdir(parents=True)
    (dev / "IR-001.md").write_text("x", encoding="utf-8")
    (dev / "SP-007.md").write_text("x", encoding="utf-8")
    sys.path.insert(0, str(PLUGIN / "hooks" / "engine"))
    from modules import state as engine_state
    assert orient.record_inventory(tmp_path) == engine_state.record_inventory(tmp_path)


def test_handoff_digest_is_read_only_and_names_the_peek(tmp_path):
    bb.post_handoff(str(tmp_path), "bmad-ux", "prd.mailjs", "PRD final — wizard")
    before = bb.pending_handoffs(str(tmp_path), "bmad-ux")
    digest = orient.handoff_digest(tmp_path)
    assert digest["total"] == 1
    assert digest["waiting"]["bmad-ux"]["preview"].startswith("prd.mailjs:")
    assert digest["waiting"]["bmad-ux"]["peek"] == "blackboard.py handoffs --skill bmad-ux"
    # read-only: the baton is still waiting (orient never consumes it)
    assert len(bb.pending_handoffs(str(tmp_path), "bmad-ux")) == len(before) == 1


def test_cli_text_and_json(tmp_path):
    r = _run(["--project-root", str(tmp_path)])
    assert r.returncode == 0, r.stderr
    assert "{metodoloji-root}" in r.stdout
    assert "NOT installed" in r.stdout  # skeleton hint for a fresh project
    assert len(r.stdout.splitlines()) <= 30  # a digest, not a dump

    r = _run(["--project-root", str(tmp_path), "--json"])
    payload = json.loads(r.stdout)
    assert payload["project_root"] == str(tmp_path.resolve())
    assert payload["catalog"]["available"] is True
    assert "next_steps" in payload


# --- MCP discovery + steering (2026-09-24 request) ----------------------------


def test_build_includes_mcp_inventory_and_steers_when_servers_exist(tmp_path, monkeypatch):
    """Configured servers surface as a bounded steering next-step, never a dump."""
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {
            "exa": {"command": "npx", "args": ["-y", "exa-mcp"]},
            "sleepy": {"command": "x", "disabled": True},
        }}), encoding="utf-8")
    monkeypatch.setattr(orient.pathlib.Path, "home", lambda: tmp_path)
    digest = orient.build(tmp_path)
    mcp = digest["mcp"]
    assert mcp["count"] == 2
    assert mcp["steering_line"].startswith("mcp: 1 active server(s) — exa")
    steering = [s for s in digest["next_steps"] if s.startswith("mcp servers reachable")]
    assert steering and "`exa`" in steering[0] and "prefer them" in steering[0]
    assert not any(s.startswith("no MCP servers") for s in digest["next_steps"])


def test_build_offers_mcp_setup_only_when_none_configured(tmp_path, monkeypatch):
    """The none-case names what was checked and offers softly — never mandatory."""
    monkeypatch.setattr(orient.pathlib.Path, "home", lambda: tmp_path)
    digest = orient.build(tmp_path)
    assert digest["mcp"]["count"] == 0
    assert digest["mcp"]["sources_checked"]  # the absence is provable
    offer = [s for s in digest["next_steps"] if s.startswith("no MCP servers")]
    assert offer and "offer to add" in offer[0] and "otherwise proceed tool-free" in offer[0]
    # And the text panel shows the same case (in-process: the subprocess would
    # read the REAL user config, which may legitimately carry servers).
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        orient._print_text(digest)
    assert "mcp             : none configured" in buf.getvalue()


def test_build_all_disabled_servers_get_honest_enable_step(tmp_path, monkeypatch):
    """Configured-but-all-disabled is its own case: "none configured" would be
    false and push the user into re-adding what already exists."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(orient.pathlib.Path, "home", lambda: home)
    (tmp_path / ".mcp.json").write_text(json.dumps(
        {"mcpServers": {"sleepy": {"command": "x", "disabled": True}}}),
        encoding="utf-8")
    digest = orient.build(tmp_path)
    assert digest["mcp"]["count"] == 1
    assert digest["mcp"]["steering_line"] == ""  # nothing active → no steering line
    steps = [s for s in digest["next_steps"] if "disabled" in s]
    assert steps and steps[0].startswith(
        "1 MCP server(s) configured but all disabled")
    assert not any(s.startswith("no MCP servers") for s in digest["next_steps"])
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        orient._print_text(digest)
    assert "all disabled" in buf.getvalue()


def test_install_digest_quiet_when_no_second_copy(tmp_path, monkeypatch):
    """Empty HOME: no installs found, no duplicate — digest stays quiet."""
    monkeypatch.setattr(orient.pathlib.Path, "home", lambda: tmp_path)
    d = orient.install_digest()
    assert d["available"] is True
    assert d["duplicate"] is False
    assert d["found"] == [] and d["shadows"] == []
    assert d["canonical"] == str(orient._PLUGIN_ROOT)


def test_install_digest_flags_shadow_copy(tmp_path, monkeypatch):
    """A second install beside the checkout is a shadow: flagged, canonical kept."""
    home = tmp_path / "home"
    inst = home / ".openhands" / "plugins" / "installed" / "metodoloji"
    inst.mkdir(parents=True)
    monkeypatch.setattr(orient.pathlib.Path, "home", lambda: home)
    d = orient.install_digest()
    assert d["duplicate"] is True
    assert str(inst) in d["shadows"]
    assert d["canonical"] == str(orient._PLUGIN_ROOT)


def test_build_carries_docs_and_installs_keys(tmp_path, monkeypatch):
    """build() always carries the docs + installs sections (fail-open values)."""
    monkeypatch.setattr(orient.pathlib.Path, "home", lambda: tmp_path)
    arge = tmp_path / "docs" / "arge"
    arge.mkdir(parents=True)
    (arge / "README.md").write_text("# arge", encoding="utf-8")
    digest = orient.build(tmp_path)
    assert digest["docs"]["available"] is True
    assert any(s["name"] == "arge" for s in digest["docs"]["subdirs"])
    assert digest["installs"]["duplicate"] is False
    assert digest["installs"]["canonical"] == str(orient._PLUGIN_ROOT)


def test_record_inventory_counts_slugged_records(tmp_path):
    """Parity case: title-derived names (`E-056-gate-heading-guard.md`) count.

    The bare pattern reported the plugin's own five E records as "none yet".
    """
    exp = tmp_path / "docs" / "experiments"
    exp.mkdir(parents=True)
    (exp / "E-056-gate-heading-guard.md").write_text("x", encoding="utf-8")
    (exp / "_template.md").write_text("x", encoding="utf-8")
    inv = orient.record_inventory(tmp_path)
    assert inv["E"]["count"] == 1
    assert inv["E"]["newest"] == "E-056-gate-heading-guard.md"


def test_build_flags_hot_run_with_no_record_file(tmp_path):
    """Reset-session shape: board hot E-001, docs/experiments/ without it.

    The digest must name the dangling claim with its cure instead of
    reporting alerts=0 while a new session believes the run is live.
    """
    exp = tmp_path / "docs" / "experiments"
    exp.mkdir(parents=True)
    (exp / "_template.md").write_text("x", encoding="utf-8")
    bb.write_key(str(tmp_path), "E-001", "in-progress: H-001 x", type_="state")
    bb.write_key(str(tmp_path), "status", "in-progress", type_="state")
    bb.set_hot(str(tmp_path), "E-001")
    digest = orient.build(tmp_path)
    assert digest["board"]["hot_dangling"] is True
    assert any("hot --clear" in step for step in digest["next_steps"])
    r = _run(["--project-root", str(tmp_path)])
    assert "DANGLING" in r.stdout


def test_build_hot_run_with_record_file_is_not_dangling(tmp_path):
    """Control: hot E-001 grounded by E-001.md (or a slugged E-001-*.md)."""
    exp = tmp_path / "docs" / "experiments"
    exp.mkdir(parents=True)
    (exp / "E-001-logger-migration.md").write_text("x", encoding="utf-8")
    bb.set_hot(str(tmp_path), "E-001")
    digest = orient.build(tmp_path)
    assert digest["board"]["hot_dangling"] is False
    assert not any("hot --clear" in step for step in digest["next_steps"])


# --- Stale gate verdicts (2026-09-30 graph-engineering-arge session) ----------
# The board said `IR-2026-09-30: readiness: NOT READY — report: …` because
# docs/design/prds/ did not exist yet; the PRD landed ~30 minutes later and the
# next session's digest still presented the verdict as live fact. bmad-help read
# the readiness report and reasoned the caveat by hand. These tests pin the
# mechanical replacement for that reasoning: a negative verdict older than the
# artifacts it judged is flagged, with the superseding path and the re-run named.

_NEG_IR = ("readiness: NOT READY — report: "
           "docs/planning/implementation-readiness-report-2026-09-30.md")


def _write_ir_verdict(tmp_path, value=_NEG_IR):
    """Put the IR run key on the board (write_key stamps methodology.last_ir)."""
    bb.write_key(str(tmp_path), "IR-2026-09-30", value, type_="state")


def _touch(path, offset):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    stamp = time.time() + offset
    os.utime(path, (stamp, stamp))


def test_build_flags_negative_verdict_outgrown_by_its_inputs(tmp_path):
    """The real shape: PRD written after the IR verdict → STALE VERDICT, re-run."""
    _write_ir_verdict(tmp_path)
    prd = tmp_path / "docs" / "design" / "prds" / "prd-x-2026-09-30" / "prd.md"
    _touch(prd, 3600)  # newer than the run key that judged its absence

    digest = orient.build(tmp_path)
    stale = digest["board"]["verdict_stale"]
    assert len(stale) == 1 and stale[0]["stage"] == "IR"
    assert stale[0]["key"] == "IR-2026-09-30"
    assert stale[0]["verdict"] == "NOT READY"
    assert stale[0]["newer_path"].endswith("prd.md")
    assert stale[0]["rerun"] == "bmad-check-implementation-readiness"
    step = [s for s in digest["next_steps"] if s.startswith("IR verdict stale")]
    assert step and "bmad-check-implementation-readiness" in step[0]

    from io import StringIO
    import contextlib
    buf = StringIO()
    with contextlib.redirect_stdout(buf):
        orient._print_text(digest)
    out = buf.getvalue()
    assert "← STALE VERDICT (IR)" in out
    assert "stale verdict: IR-2026-09-30 = NOT READY" in out


def test_negative_verdict_with_no_newer_inputs_is_not_flagged(tmp_path):
    """Control: an input older than the verdict is not evidence of staleness."""
    prd = tmp_path / "docs" / "design" / "prds" / "prd-x" / "prd.md"
    _touch(prd, -3600)
    _write_ir_verdict(tmp_path)  # judged AFTER the PRD existed
    digest = orient.build(tmp_path)
    assert digest["board"]["verdict_stale"] == []
    assert not any("verdict stale" in s for s in digest["next_steps"])


def test_positive_verdict_is_never_flagged_as_stale(tmp_path):
    """A READY verdict is superseded by a re-run, not by later inputs."""
    _write_ir_verdict(tmp_path, value="readiness: READY — report: "
                                     "docs/planning/implementation-readiness-report-2026-09-30.md")
    _touch(tmp_path / "docs" / "design" / "prds" / "prd-x" / "prd.md", 3600)
    digest = orient.build(tmp_path)
    assert digest["board"]["verdict_stale"] == []


def test_verdict_report_is_never_its_own_newer_input(tmp_path):
    """The gate writes its report, then its run key — a fresh report is not a change.

    Without the exclusion, an input-driven verdict would flag itself stale on the
    very next digest whenever the report file's mtime ledgered past the key.
    """
    _write_ir_verdict(tmp_path)
    report = (tmp_path / "docs" / "planning" /
              "implementation-readiness-report-2026-09-30.md")
    _touch(report, 3600)
    digest = orient.build(tmp_path)
    assert digest["board"]["verdict_stale"] == []


def test_chain_line_names_the_run_key_and_clips_long_values(tmp_path):
    """The run key must survive, and a long value must be marked, not chopped.

    compact_context clips at 80 chars with no ellipsis, so the real digest showed
    `…report: docs/planning/implementation-readi` — a mangled path a router was
    asked to name. The text panel now clips with `…`; --json keeps the full value.
    """
    _write_ir_verdict(tmp_path)
    _touch(tmp_path / "docs" / "design" / "prds" / "prd-x" / "prd.md", 3600)
    digest = orient.build(tmp_path)
    full = digest["board"]["chain_values"]["IR"]
    assert full.startswith("IR-2026-09-30: readiness: NOT READY")
    assert "implementation-readiness-report-2026-09-30.md" in full  # untruncated

    from io import StringIO
    import contextlib
    buf = StringIO()
    with contextlib.redirect_stdout(buf):
        orient._print_text(digest)
    chain = [l for l in buf.getvalue().splitlines() if l.strip().startswith("chain:")]
    assert chain and "IR-2026-09-30: readiness: NOT READY" in chain[0]
    assert "…" in chain[0]  # the cut is marked
    assert "implementation-readi\n" not in buf.getvalue()

    r = _run(["--project-root", str(tmp_path), "--json"])
    payload = json.loads(r.stdout)
    assert payload["board"]["chain_keys"]["IR"] == "IR-2026-09-30"
    assert payload["board"]["verdict_stale"][0]["stage"] == "IR"


# --- SP's two concrete forms: heartbeat vs gate record (fikir 2026-10-01) ----
def test_sp_heartbeat_without_record_is_named_as_a_refresh(tmp_path):
    """The fikir session read 'SP yok' as 'sprint planning skipped', then
    mis-read the catalog's required column as a hard CS prerequisite. The
    digest now names the split: an SP heartbeat on the board with no SP-*.md
    gate record is a sprint REFRESH, not a skipped stage."""
    import contextlib
    import io
    (tmp_path / "docs" / "development").mkdir(parents=True)
    (tmp_path / "docs" / "development" / "IR-001.md").write_text("# x")
    bb.write_key(str(tmp_path), "SP-2026-10-01", "sprint status: yaml — 3 epics")
    digest = orient.build(tmp_path)
    assert digest["records"].get("SP") is None          # no gate record file
    assert digest["board"]["chain_keys"].get("SP") == "SP-2026-10-01"  # heartbeat live
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        orient._print_text(digest)
    out = buf.getvalue()
    assert "an existing sprint was refreshed" in out
    assert "not a skipped stage" not in out  # phrasing lives in the cursor, not the panel
    assert "no sprint-status file" not in out  # the old misleading line is gone


def test_no_sp_at_all_names_the_phase_gate_semantics(tmp_path):
    """Neither form present: the panel pre-answers the 'is CS blocked?' question
    instead of leaving the router to re-derive it from the catalog columns."""
    import contextlib
    import io
    (tmp_path / "docs" / "development").mkdir(parents=True)
    (tmp_path / "docs" / "development" / "IR-001.md").write_text("# x")
    digest = orient.build(tmp_path)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        orient._print_text(digest)
    out = buf.getvalue()
    assert "bmad-sprint-planning has not run yet" in out
    assert "PHASE gate" in out and "not a create-story prerequisite" in out


# --- Open Implementation Plans in the digest (E-068) -------------------------

def _plan_rec(root, eid="E-100", planned="src/a.py, src/b.py"):
    recs = root / "docs" / "experiments"
    recs.mkdir(parents=True, exist_ok=True)
    (recs / f"{eid}.md").write_text(
        f"## Experiment: {eid} — plan\n- **Status:** planned\n"
        "- **Code Scope:** none\n\n## Implementation Plan (GRP)\n\n"
        f"- **Planned Files:** {planned}\n- **Amendments:** none\n",
        encoding="utf-8")


def test_build_and_board_surface_open_plans(tmp_path):
    """The board peek and build() carry the same open-plan line the session
    edge injects — one shared formatter, read-only."""
    _plan_rec(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x", encoding="utf-8")
    bb.write_key(str(tmp_path), "status", "in-progress", type_="state")

    digest = orient.build(tmp_path)
    assert digest["plan"]["open"][0]["name"] == "E-100.md"
    assert digest["plan"]["line"] == \
        "E-100.md 1/2 planned files on disk (1 pending)"
    board = digest["board"]
    assert board["plan_progress"][0]["line"] == digest["plan"]["line"]
    assert "chain_progress" in board  # pre-existing peek stays

    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        orient._print_text(digest)
    assert "plan: E-100.md 1/2 planned files on disk (1 pending)" in buf.getvalue()


def test_board_plan_peek_is_read_only_and_silent_on_completion(tmp_path):
    """A completed plan adds no row, and the peek writes NOTHING to the board."""
    _plan_rec(tmp_path, planned="src/c.py")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "c.py").write_text("x", encoding="utf-8")
    bb.write_key(str(tmp_path), "status", "in-progress", type_="state")
    events = Path(bb.board_paths(str(tmp_path))["events"]).read_bytes()

    digest = orient.build(tmp_path)
    assert digest["board"]["plan_progress"] == []
    assert digest["plan"]["open"] == []
    assert Path(bb.board_paths(str(tmp_path))["events"]).read_bytes() == events


def test_sp_record_present_adds_no_split_line(tmp_path):
    """A real gate record satisfies the chain: no split explanation needed."""
    import contextlib
    import io
    (tmp_path / "docs" / "development").mkdir(parents=True)
    (tmp_path / "docs" / "development" / "SP-001.md").write_text(
        "# Sprint: SP-001\n\n- **Status:** planned\n- **Date:** 2026-10-01\n")
    digest = orient.build(tmp_path)
    assert digest["records"].get("SP", {}).get("count") == 1
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        orient._print_text(digest)
    assert "was refreshed" not in buf.getvalue()
    assert "has not run yet" not in buf.getvalue()

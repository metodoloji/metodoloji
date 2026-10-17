"""Tests for the workflow kernel: spec validation, deterministic transitions,
evidence-gated completion, and the CLI — the contract the SEO scenario rides."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from bmad.workflow import (advisory, engine, evidence as evidence_mod,  # noqa: E402
                           spec as spec_mod, store as store_mod)
from bmad.scripts import workflow as wf_cli  # noqa: E402

BUILTIN = _ROOT / "bmad" / "workflow" / "builtin" / "seo-visibility.json"


def _spec() -> dict:
    return {
        "id": "demo",
        "title": "demo flow",
        "start": "a",
        "stages": [
            {"id": "a", "title": "A",
             "evidence": {"kind": "artifact", "path": "log.md", "contains": ["A1"]},
             "next": [{"to": "b"}]},
            {"id": "b", "title": "B",
             "requires": ["a"],
             "evidence": {"kind": "note"},
             "next": [{"to": "c", "when": {"key": "redo", "equals": "true"}},
                      {"to": "done"}]},
            {"id": "c", "title": "C",
             "evidence": {"kind": "note"},
             "next": [{"to": "done"}]},
            {"id": "done", "title": "Done", "terminal": True},
        ],
    }


# --- spec ---------------------------------------------------------------------

def test_validate_accepts_a_well_formed_spec():
    assert spec_mod.validate(_spec()) == []


def test_builtin_seo_spec_is_valid():
    data = spec_mod.load(BUILTIN)
    assert data["id"] == "seo-visibility"
    assert data["start"] == "analyze"
    assert spec_mod.validate(data) == []


@pytest.mark.parametrize("mutate, needle", [
    (lambda s: s.update(start="nope"), "start"),
    (lambda s: s["stages"].append({"id": "a", "title": "dup"}), "duplicate"),
    (lambda s: s["stages"][0]["next"].append({"to": "ghost"}), "ghost"),
    (lambda s: s["stages"][0].pop("evidence"), "no 'evidence'"),
    (lambda s: s["stages"][0].update(next=[]), "no 'next'"),
    # bridge validation
    (lambda s: s["stages"][0].update(bridge=123), "bridge must be a string"),
    (lambda s: s["stages"][0].update(bridge="E*"), "bridge must be a prefix"),
    (lambda s: s["stages"][0].update(bridge=""), "bridge must be a prefix"),
    # min validation
    (lambda s: s["stages"][0]["evidence"].update(min=-1), ">= 1"),
    (lambda s: s["stages"][0]["evidence"].update(min="not-a-number"), "must be an integer"),
])
def test_validate_rejects_broken_specs(mutate, needle):
    s = _spec()
    mutate(s)
    problems = " | ".join(spec_mod.validate(s))
    assert needle in problems


def test_validate_rejects_unreachable_stage():
    s = _spec()
    s["stages"].append({"id": "island", "title": "X",
                        "evidence": {"kind": "note"}, "next": [{"to": "done"}]})
    assert any("unreachable" in p for p in spec_mod.validate(s))


def test_compute_next_advances_and_ends():
    s = _spec()
    assert spec_mod.compute_next(s, current="a", completed={"a"}, ctx={}) == {
        "decision": "advance", "stage": "b", "cleared": []}
    # 'b' with no redo flag falls through to the default edge (done).
    assert spec_mod.compute_next(s, current="b", completed={"a", "b"}, ctx={}) == {
        "decision": "advance", "stage": "done", "cleared": []}
    assert spec_mod.compute_next(s, current="done", completed=set(), ctx={}) == {
        "decision": "end"}


def test_compute_next_takes_conditional_edge_and_consumes_flag():
    s = _spec()
    out = spec_mod.compute_next(s, current="b", completed={"a", "b"},
                                ctx={"redo": "true"})
    assert out["decision"] == "advance"
    assert out["stage"] == "c"
    assert out["cleared"] == ["redo"]


# --- engine -------------------------------------------------------------------

def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_create_and_status(tmp_path):
    res = engine.create(str(tmp_path), _spec())
    assert res["ok"] and res["state"]["current"] == "a"
    st = engine.status(str(tmp_path), "demo")
    assert st["status"] == "active" and st["current"] == "a"


def test_create_refuses_clobber_without_force(tmp_path):
    engine.create(str(tmp_path), _spec())
    assert engine.create(str(tmp_path), _spec())["ok"] is False
    assert engine.create(str(tmp_path), _spec(), force=True)["ok"] is True


def test_complete_refuses_without_evidence(tmp_path):
    engine.create(str(tmp_path), _spec())
    res = engine.complete(str(tmp_path), "demo", "a")
    assert res["ok"] is False and "evidence" in res["error"]
    assert engine.status(str(tmp_path), "demo")["current"] == "a"


def test_complete_advances_only_after_evidence(tmp_path):
    engine.create(str(tmp_path), _spec())
    _write(tmp_path / "log.md", "A1 first issue\n")
    res = engine.complete(str(tmp_path), "demo", "a")
    assert res["ok"] and res["current"] == "b"


def test_complete_rejects_out_of_order_stage(tmp_path):
    engine.create(str(tmp_path), _spec())
    res = engine.complete(str(tmp_path), "demo", "c", note="skip")
    assert res["ok"] is False and "not the current stage" in res["error"]


def test_note_evidence_requires_note(tmp_path):
    engine.create(str(tmp_path), _spec())
    _write(tmp_path / "log.md", "A1\n")
    engine.complete(str(tmp_path), "demo", "a")
    assert engine.complete(str(tmp_path), "demo", "b")["ok"] is False
    assert engine.complete(str(tmp_path), "demo", "b", note="done b")["ok"] is True


def test_branch_loops_back_then_flag_is_consumed(tmp_path):
    engine.create(str(tmp_path), _spec())
    _write(tmp_path / "log.md", "A1\n")
    engine.complete(str(tmp_path), "demo", "a")
    engine.complete(str(tmp_path), "demo", "b", note="b")
    # b -> done (default) since redo is unset. Reset and drive the branch instead.
    engine.create(str(tmp_path), _spec(), force=True)
    _write(tmp_path / "log.md", "A1\n")
    engine.complete(str(tmp_path), "demo", "a")
    engine.flag(str(tmp_path), "demo", "redo", "true")
    res = engine.complete(str(tmp_path), "demo", "b", note="b")
    assert res["current"] == "c"
    # The flag was consumed, so 'c' proceeds to done normally.
    assert engine.complete(str(tmp_path), "demo", "c", note="c")["status"] == "completed"


def test_full_run_to_completion(tmp_path):
    engine.create(str(tmp_path), _spec())
    _write(tmp_path / "log.md", "A1\n")
    engine.complete(str(tmp_path), "demo", "a")
    engine.complete(str(tmp_path), "demo", "b", note="b")
    engine.complete(str(tmp_path), "demo", "c", note="c")
    st = engine.status(str(tmp_path), "demo")
    assert st["status"] == "completed" and st["current"] is None


def test_command_evidence_runs_and_checks_rc(tmp_path):
    s = _spec()
    s["stages"][0]["evidence"] = {"kind": "command",
                                  "argv": [sys.executable, "-c", "raise SystemExit(0)"]}
    engine.create(str(tmp_path), s)
    assert engine.complete(str(tmp_path), "demo", "a")["ok"] is True


def test_command_evidence_fails_on_rc(tmp_path):
    s = _spec()
    s["stages"][0]["evidence"] = {"kind": "command",
                                  "argv": [sys.executable, "-c", "raise SystemExit(3)"]}
    engine.create(str(tmp_path), s)
    res = engine.complete(str(tmp_path), "demo", "a")
    assert res["ok"] is False and "exit 3" in res["error"]


def test_block_resume_and_list(tmp_path):
    engine.create(str(tmp_path), _spec())
    assert engine.block(str(tmp_path), "demo", "waiting on GSC access")["ok"]
    assert engine.status(str(tmp_path), "demo")["status"] == "blocked"
    assert engine.resume(str(tmp_path), "demo")["status"] == "active"
    assert engine.list_runs(str(tmp_path))["runs"][0]["slug"] == "demo"


# --- CLI ----------------------------------------------------------------------

def test_cli_create_builtin_and_drive(tmp_path, capsys):
    root = str(tmp_path)
    assert wf_cli.main(["--project-root", root, "create", "--builtin", "seo-visibility"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out["state"]["current"] == "analyze"

    _write(tmp_path / "sorunlar.md", "- ISSUE-001 meta eksik\n")
    assert wf_cli.main(["--project-root", root, "complete", "--slug", "seo-visibility",
                        "--stage", "analyze"]) == 0
    capsys.readouterr()

    # next stage is computed, not chosen: 'map'.
    assert wf_cli.main(["--project-root", root, "next", "--slug", "seo-visibility"]) == 0
    nxt = json.loads(capsys.readouterr().out)
    assert nxt["current"] == "map"


def test_cli_refusal_exits_one(tmp_path, capsys):
    root = str(tmp_path)
    wf_cli.main(["--project-root", root, "create", "--builtin", "seo-visibility"])
    capsys.readouterr()
    # evidence missing -> refusal, exit 1
    assert wf_cli.main(["--project-root", root, "complete", "--slug", "seo-visibility",
                        "--stage", "analyze"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False


def test_cli_list_returns_runs(tmp_path, capsys):
    root = str(tmp_path)
    assert wf_cli.main(["--project-root", root, "create", "--builtin", "seo-visibility"]) == 0
    capsys.readouterr()
    assert wf_cli.main(["--project-root", root, "list"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"]
    assert any(r["slug"] == "seo-visibility" for r in out["runs"])


def test_cli_resolve_slug_requires_explicit_when_no_runs(tmp_path, capsys):
    root = str(tmp_path)
    # No runs exist, no --slug given
    assert wf_cli.main(["--project-root", root, "status"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and "no --slug" in out["error"]


def test_cli_resolve_slug_requires_explicit_when_ambiguous(tmp_path, capsys):
    root = str(tmp_path)
    s1 = _spec()
    s2 = dict(_spec(), id="other", start="a")
    engine.create(root, s1)
    engine.create(root, s2)
    # Two runs exist, no --slug given -> ambiguous
    assert wf_cli.main(["--project-root", root, "status"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and "no --slug" in out["error"]


def test_cli_validate_spec(tmp_path, capsys):
    spec_path = tmp_path / "test_spec.json"
    spec_path.write_text(json.dumps(_spec()), encoding="utf-8")
    assert wf_cli.main(["--project-root", str(tmp_path), "validate",
                        "--spec", str(spec_path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out["id"] == "demo"


def test_cli_validate_rejects_broken_spec(tmp_path, capsys):
    spec_path = tmp_path / "bad.json"
    spec_path.write_text(json.dumps({"id": "x"}), encoding="utf-8")
    assert wf_cli.main(["--project-root", str(tmp_path), "validate",
                        "--spec", str(spec_path)]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False


def test_cli_history_command(tmp_path, capsys):
    root = str(tmp_path)
    wf_cli.main(["--project-root", root, "create", "--builtin", "seo-visibility"])
    capsys.readouterr()
    assert wf_cli.main(["--project-root", root, "history",
                        "--slug", "seo-visibility"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"]
    assert len(out["history"]) > 0


# --- evidence edge cases ----------------------------------------------------


def test_evidence_artifact_without_contains_passes_on_file(tmp_path):
    spec = {
        "id": "no-contains",
        "title": "No contains",
        "start": "a",
        "stages": [
            {"id": "a", "title": "A",
             "evidence": {"kind": "artifact", "path": "data.txt"},
             "next": [{"to": "done"}]},
            {"id": "done", "title": "Done", "terminal": True},
        ],
    }
    root = str(tmp_path)
    engine.create(root, spec)
    # Without 'contains', just the file existing is enough
    (tmp_path / "data.txt").write_text("anything\n", encoding="utf-8")
    res = engine.complete(root, "no-contains", "a")
    assert res["ok"]
    assert res["status"] == "completed"


def test_evidence_unknown_kind_fails_gracefully(tmp_path):
    spec = {
        "id": "bad-kind",
        "title": "Bad kind",
        "start": "a",
        "stages": [
            {"id": "a", "title": "A",
             "evidence": {"kind": "telepathic"},
             "next": [{"to": "done"}]},
            {"id": "done", "title": "Done", "terminal": True},
        ],
    }
    # spec validation should reject unknown evidence kind
    problems = spec_mod.validate(spec)
    assert any("must be one of" in p for p in problems)


# --- advisory (session edge) --------------------------------------------------

def test_advisory_is_silent_without_runs(tmp_path):
    assert advisory.peek(str(tmp_path)) == ""


def test_advisory_states_position_evidence_and_computed_next(tmp_path):
    engine.create(str(tmp_path), _spec())
    line = advisory.peek(str(tmp_path))
    assert "WORKFLOW 'demo' is active" in line
    assert "stage 1/4 'a'" in line
    assert "artifact log.md needs A1" in line
    # The next stage is *computed*, which is the property the line exists to show.
    assert "then the kernel computes 'b'" in line
    assert "workflow.py" in line


def test_advisory_reports_blocked_reason(tmp_path):
    engine.create(str(tmp_path), _spec())
    engine.block(str(tmp_path), "demo", "waiting on GSC access")
    line = advisory.peek(str(tmp_path))
    assert "is blocked" in line and "waiting on GSC access" in line
    assert "resume" in line


def test_advisory_goes_quiet_once_the_run_completes(tmp_path):
    engine.create(str(tmp_path), _spec())
    _write(tmp_path / "log.md", "A1\n")
    engine.complete(str(tmp_path), "demo", "a")
    engine.complete(str(tmp_path), "demo", "b", note="b")
    engine.complete(str(tmp_path), "demo", "c", note="c")
    assert advisory.peek(str(tmp_path)) == ""


def test_advisory_is_read_only(tmp_path):
    engine.create(str(tmp_path), _spec())
    state_file = next((tmp_path / ".metodoloji/workflow").glob("*.state.json"))
    before_bytes = state_file.read_bytes()
    events = (tmp_path / ".metodoloji/workflow/events.jsonl").read_text(encoding="utf-8")
    for _ in range(3):
        advisory.peek(str(tmp_path))
    assert state_file.read_bytes() == before_bytes
    assert (tmp_path / ".metodoloji/workflow/events.jsonl").read_text(
        encoding="utf-8") == events


def test_advisory_survives_a_corrupt_state_file(tmp_path):
    engine.create(str(tmp_path), _spec())
    state_file = next((tmp_path / ".metodoloji/workflow").glob("*.state.json"))
    state_file.write_text("{not json", encoding="utf-8")
    assert advisory.peek(str(tmp_path)) == ""  # fail-open, never raises


def test_builtin_methodology_chain_spec_is_valid():
    chain_path = _ROOT / "bmad" / "workflow" / "builtin" / "methodology-chain.json"
    data = spec_mod.load(chain_path)
    assert data["id"] == "methodology-chain"
    assert data["start"] == "experiment"
    assert spec_mod.validate(data) == []


def test_engine_complete_writes_bridge_key_to_blackboard(tmp_path):
    """When a stage carries 'bridge', complete() writes a bridge key to the
    blackboard so the methodology relay sees methodology.last_* keys."""
    spec = {
        "id": "bridge-test",
        "title": "Bridge test",
        "start": "alpha",
        "stages": [
            {"id": "alpha", "title": "Alpha",
             "evidence": {"kind": "artifact", "path": "log.md", "contains": ["OK"]},
             "bridge": "E-",
             "next": [{"to": "beta"}]},
            {"id": "beta", "title": "Beta",
             "requires": ["alpha"],
             "evidence": {"kind": "artifact", "path": "log.md", "contains": ["OK"]},
             "bridge": "IR-",
             "next": [{"to": "done"}]},
            {"id": "done", "title": "Done", "terminal": True},
        ],
    }
    root = str(tmp_path)
    engine.create(root, spec)
    from hooks.engine.modules import blackboard

    # Complete alpha
    (tmp_path / "log.md").write_text("OK\n", encoding="utf-8")
    engine.complete(root, "bridge-test", "alpha")

    board = blackboard.read_board(root)
    # Bridge key written
    bridge_key = next((k for k in board["keys"] if k.startswith("E-")), None)
    assert bridge_key is not None, "bridge key E-* was not written"
    assert bridge_key == "E-bridge-test"
    assert "alpha" in board["keys"][bridge_key]["value"]

    # Mirror: methodology.last_experiment populated
    me = board["keys"].get("methodology.last_experiment")
    assert me is not None, "methodology.last_experiment not set"
    assert "E-bridge-test" in me["value"]

    # Complete beta
    engine.complete(root, "bridge-test", "beta")
    board = blackboard.read_board(root)

    ir_key = next((k for k in board["keys"] if k.startswith("IR-")), None)
    assert ir_key is not None, "bridge key IR-* was not written"
    mi = board["keys"].get("methodology.last_ir")
    assert mi is not None, "methodology.last_ir not set"


def test_engine_stage_without_bridge_does_not_write_bridge_key(tmp_path):
    """Stages without 'bridge' should not write methodology bridge keys."""
    root = str(tmp_path)
    engine.create(root, _spec())
    (tmp_path / "log.md").write_text("A1\n", encoding="utf-8")
    engine.complete(root, "demo", "a")
    from hooks.engine.modules import blackboard
    board = blackboard.read_board(root)
    bridge_keys = [k for k in board["keys"] if "-" in k and k.count("-") == 1
                   and len(k) <= 10 and board["keys"][k].get("type") == "state"]
    bridge_keys = [k for k in bridge_keys
                   if any(k.startswith(p) for p in ("E-", "IR-", "SP-", "S-", "QR-", "PR-"))]
    assert len(bridge_keys) == 0, f"unexpected bridge keys: {bridge_keys}"


def test_evidence_glob_artifact_resolution(tmp_path):
    spec = {
        "id": "glob-test",
        "title": "Glob artifact test",
        "start": "step1",
        "stages": [
            {
                "id": "step1",
                "title": "Step 1",
                "evidence": {
                    "kind": "artifact",
                    "path": "docs/experiments/E-*.md",
                    "contains": ["approved"],
                    "min": 1,
                },
                "next": [{"to": "done"}],
            },
            {"id": "done", "title": "Done", "terminal": True},
        ],
    }
    engine.create(str(tmp_path), spec)
    exp_dir = tmp_path / "docs" / "experiments"
    exp_dir.mkdir(parents=True, exist_ok=True)

    # When no file matches glob: fails
    res_fail = engine.complete(str(tmp_path), "glob-test", "step1")
    assert not res_fail["ok"]
    assert "artifact not found" in res_fail["error"]

    # Create older file without matching content
    f1 = exp_dir / "E-001.md"
    f1.write_text("Status: rejected\n", encoding="utf-8")

    # Create newer file with matching content
    f2 = exp_dir / "E-002.md"
    f2.write_text("Status: approved\nGATE-OK-12345\n", encoding="utf-8")

    res_ok = engine.complete(str(tmp_path), "glob-test", "step1")
    assert res_ok["ok"]
    assert res_ok["status"] == "completed"


def test_recursive_glob_resolves_nested_artifact(tmp_path):
    spec = {
        "id": "rec-glob",
        "title": "Recursive glob test",
        "start": "s1",
        "stages": [
            {"id": "s1", "title": "S1",
             "evidence": {"kind": "artifact", "path": "docs/**/QR-*.md",
                          "contains": ["approve"], "min": 1},
             "next": [{"to": "done"}]},
            {"id": "done", "title": "Done", "terminal": True},
        ],
    }
    engine.create(str(tmp_path), spec)
    nested = tmp_path / "docs" / "development" / "qr"
    nested.mkdir(parents=True, exist_ok=True)
    (nested / "QR-001.md").write_text("approve: OK\n", encoding="utf-8")
    res = engine.complete(str(tmp_path), "rec-glob", "s1")
    assert res["ok"], f"recursive glob failed: {res.get('error')}"
    assert res["status"] == "completed"


def test_blackboard_integration_on_workflow_lifecycle(tmp_path):
    res = engine.create(str(tmp_path), _spec())
    assert res["ok"]
    from hooks.engine.modules import blackboard
    board = blackboard.read_board(str(tmp_path))
    assert board["keys"].get("workflow.demo.status", {}).get("value") == "created"
    assert board["keys"].get("workflow.demo.current", {}).get("value") == "a"

    _write(tmp_path / "log.md", "A1\n")
    engine.complete(str(tmp_path), "demo", "a")
    board = blackboard.read_board(str(tmp_path))
    assert board["keys"].get("workflow.demo.status", {}).get("value") == "advanced"
    assert board["keys"].get("workflow.demo.current", {}).get("value") == "b"

    engine.block(str(tmp_path), "demo", "blocked on auth")
    board = blackboard.read_board(str(tmp_path))
    assert board["keys"].get("workflow.demo.status", {}).get("value") == "blocked"

    engine.resume(str(tmp_path), "demo")
    board = blackboard.read_board(str(tmp_path))
    assert board["keys"].get("workflow.demo.status", {}).get("value") == "resumed"


# --- store durability (fail-open / atomic / event-sourced) -----------------


def test_store_paths_returns_valid_layout(tmp_path):
    p = store_mod.paths(str(tmp_path))
    assert ".metodoloji" in p["base"] and "workflow" in p["base"]
    assert "specs" in p["specs"]
    assert "events.jsonl" in p["events"]
    assert "workflow.lock" in p["lock"]
    for key in ("base", "specs", "events", "lock"):
        assert os.path.isabs(p[key])


def test_store_read_state_returns_empty_for_missing(tmp_path):
    assert store_mod.read_state(str(tmp_path), "nope") == {}


def test_store_read_state_returns_empty_for_corrupt(tmp_path):
    p = store_mod.paths(str(tmp_path))
    state_f = os.path.join(p["base"], "corrupt.state.json")
    os.makedirs(p["base"], exist_ok=True)
    Path(state_f).write_text("{not json", encoding="utf-8")
    assert store_mod.read_state(str(tmp_path), "corrupt") == {}


# --- corrupt-state honesty (E-005) -------------------------------------------
# read_state's fail-open {} is the storage contract; the ENGINE layer must not
# let that {} masquerade as "no such workflow" — an operator re-creating over
# a corrupt file would silently destroy a possibly-recoverable run.

def test_state_is_corrupt_distinguishes_corrupt_from_missing(tmp_path):
    root = str(tmp_path)
    assert engine.state_is_corrupt(root, "ghost") is False  # missing file
    store_mod.write_state(root, "healthy", {"slug": "healthy", "status": "active"})
    assert engine.state_is_corrupt(root, "healthy") is False
    p = store_mod.paths(root)
    os.makedirs(p["base"], exist_ok=True)
    Path(os.path.join(p["base"], "broken.state.json")).write_text(
        "{{{not json", encoding="utf-8")
    assert engine.state_is_corrupt(root, "broken") is True


def test_list_runs_marks_corrupt_instead_of_ghost_row(tmp_path):
    root = str(tmp_path)
    engine.create(root, _spec(), slug="demo")
    p = store_mod.paths(root)
    os.makedirs(p["base"], exist_ok=True)
    Path(os.path.join(p["base"], "broken.state.json")).write_text(
        "{{{corrupt", encoding="utf-8")
    runs = {r["slug"]: r for r in engine.list_runs(root)["runs"]}
    assert runs["demo"]["status"] == "active"  # healthy rows unchanged
    assert runs["broken"].get("corrupt") is True
    assert "error" in runs["broken"]


def test_status_on_corrupt_run_names_the_file_not_no_workflow(tmp_path):
    root = str(tmp_path)
    engine.create(root, _spec(), slug="demo")
    p = store_mod.paths(root)
    os.makedirs(p["base"], exist_ok=True)
    Path(os.path.join(p["base"], "demo.state.json")).write_text(
        "{{{corrupt", encoding="utf-8")
    out = engine.status(root, "demo")
    assert out["ok"] is False
    assert "corrupt" in out["error"]
    assert "demo.state.json" in out["error"]
    assert "no workflow" not in out["error"]


def test_missing_run_still_says_no_workflow(tmp_path):
    out = engine.status(str(tmp_path), "ghost")
    assert out["ok"] is False
    assert "no workflow 'ghost'" in out["error"]


# --- corrupt-state honesty: sanitizer agreement + create gate (E-008) --------
# state_is_corrupt probed the RAW slug while store sanitizes it ("my run" ->
# my_run.state.json), so seven commands reported "no workflow" for a run that
# list() honestly marked corrupt; create() never consulted the predicate and
# clobbered the corrupt file with ok:true — WITHOUT --force (the exact silent
# data loss E-005's refusal text warns about).

def _seed_corrupt(root: str, slug: str) -> Path:
    """Write a corrupt state file at the SANITIZED path storage actually uses."""
    path = Path(store_mod.state_path(root, slug))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{{{corrupt", encoding="utf-8")
    return path


def test_state_is_corrupt_agrees_with_storage_path_for_sanitized_slug(tmp_path):
    root = str(tmp_path)
    _seed_corrupt(root, "my run")
    assert store_mod.state_path(root, "my run").endswith("my_run.state.json")
    assert engine.state_is_corrupt(root, "my run") is True


def test_every_read_on_corrupt_special_slug_names_the_file(tmp_path):
    root = str(tmp_path)
    _seed_corrupt(root, "my run")
    calls = (
        engine.status(root, "my run"),
        engine.next_stage(root, "my run"),
        engine.history(root, "my run"),
        engine.flag(root, "my run", "k", "v"),
        engine.block(root, "my run", "why"),
        engine.resume(root, "my run"),
        engine.complete(root, "my run", "a"),
    )
    for out in calls:
        assert out["ok"] is False, out
        assert "corrupt" in out["error"], out
        assert "my_run.state.json" in out["error"], out  # sanitized name
        assert "no workflow" not in out["error"], out
    # list agrees (it iterates file stems, which are already sanitized)
    runs = {r["slug"]: r for r in engine.list_runs(root)["runs"]}
    assert runs["my_run"].get("corrupt") is True


def test_create_refuses_to_clobber_corrupt_state(tmp_path):
    root = str(tmp_path)
    seed = _seed_corrupt(root, "alpha")
    for force in (False, True):
        out = engine.create(root, _spec(), slug="alpha", force=force)
        assert out["ok"] is False, out
        assert "corrupt" in out["error"], out
        assert "alpha.state.json" in out["error"], out
        # the broken-but-possibly-recoverable bytes stay untouched
        assert seed.read_text(encoding="utf-8") == "{{{corrupt"


def test_cli_status_on_corrupt_special_slug_exits_one(tmp_path, capsys):
    root = str(tmp_path)
    _seed_corrupt(root, "my run")
    rc = wf_cli.main(["--project-root", root, "status", "--slug", "my run"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert "corrupt" in out["error"]
    assert "no workflow" not in out["error"]


def test_store_write_state_creates_files(tmp_path):
    root = str(tmp_path)
    slug = "demo"
    state = {"slug": slug, "status": "active", "current": "a"}
    store_mod.write_state(root, slug, state)

    p = store_mod.paths(root)
    state_f = os.path.join(p["base"], f"{slug}.state.json")
    assert os.path.isfile(state_f)
    assert os.path.isfile(p["events"])

    loaded = json.loads(Path(state_f).read_text(encoding="utf-8"))
    assert loaded["slug"] == slug
    assert "updated_at" in loaded


def test_store_write_state_appends_event(tmp_path):
    root = str(tmp_path)
    store_mod.write_state(root, "alpha", {"slug": "alpha", "status": "active"})
    store_mod.write_state(root, "beta", {"slug": "beta", "status": "active"})
    evs = store_mod.read_events(root)
    assert len(evs) == 2
    slugs = {e["slug"] for e in evs}
    assert slugs == {"alpha", "beta"}


def test_store_list_slugs_after_multiple_creates(tmp_path):
    root = str(tmp_path)
    engine.create(root, _spec())
    engine.create(root, _spec(), slug="second", force=True)
    slugs = store_mod.list_slugs(root)
    assert len(slugs) == 2
    assert "demo" in slugs


def test_store_list_slugs_returns_empty_when_missing(tmp_path):
    assert store_mod.list_slugs(str(tmp_path)) == []


def test_store_save_spec_writes_and_returns_valid_path(tmp_path):
    root = str(tmp_path)
    spec = _spec()
    dest = store_mod.save_spec(root, spec)
    assert os.path.isfile(dest)
    loaded = json.loads(Path(dest).read_text(encoding="utf-8"))
    assert loaded["id"] == spec["id"]


def test_store_load_spec_fallback_from_state_file(tmp_path):
    """When spec dir has no file matching the slug, load_spec falls back to
    state['spec_file'] (which uses the spec's own id, not the run slug)."""
    root = str(tmp_path)
    # Use a slug that differs from the spec id so the spec-dir lookup misses.
    engine.create(root, _spec(), slug="my-run")
    # specs/my-run.json does not exist (it is specs/demo.json — the spec id).
    p = store_mod.paths(root)
    assert not os.path.isfile(os.path.join(p["specs"], "my-run.json"))
    loaded = store_mod.load_spec(root, "my-run")
    assert loaded.get("id") == "demo"


def test_store_load_spec_returns_empty_when_all_sources_gone(tmp_path):
    root = str(tmp_path)
    engine.create(root, _spec())
    p = store_mod.paths(root)
    for f in os.listdir(p["specs"]):
        os.remove(os.path.join(p["specs"], f))
    state_f = os.path.join(p["base"], "demo.state.json")
    Path(state_f).write_text(json.dumps({"slug": "demo"}), encoding="utf-8")
    assert store_mod.load_spec(str(tmp_path), "demo") == {}


def test_store_read_events_respects_limit(tmp_path):
    root = str(tmp_path)
    for i in range(5):
        store_mod.write_state(root, f"run-{i}", {"slug": f"run-{i}"})
    assert len(store_mod.read_events(root)) == 5
    assert len(store_mod.read_events(root, limit=2)) == 2


def test_store_state_survives_special_slug_chars(tmp_path):
    root = str(tmp_path)
    store_mod.write_state(root, "my.workflow_v2", {"slug": "my.workflow_v2", "status": "active"})
    loaded = store_mod.read_state(root, "my.workflow_v2")
    assert loaded.get("status") == "active"


# --- engine edge cases ------------------------------------------------------


def test_engine_flag_persists_and_surfaces_in_status(tmp_path):
    root = str(tmp_path)
    engine.create(root, _spec())
    engine.flag(root, "demo", "regressed", "true")
    st = engine.status(root, "demo")
    assert st["flags"].get("regressed") == "true"


def test_engine_status_returns_artifact_and_stage_count(tmp_path):
    root = str(tmp_path)
    engine.create(root, _spec())
    st = engine.status(root, "demo")
    assert st["total_stages"] == 4
    assert st["stage"] is not None
    assert st["stage"]["id"] == "a"


def test_engine_history_after_several_transitions(tmp_path):
    root = str(tmp_path)
    engine.create(root, _spec())
    _write(tmp_path / "log.md", "A1\n")
    engine.complete(root, "demo", "a")
    h = engine.history(root, "demo", limit=10)
    assert len(h["history"]) > 0
    assert any("|a|complete|" in line for line in h["history"])


def test_engine_history_rejects_missing_slug(tmp_path):
    res = engine.history(str(tmp_path), "nope")
    assert res["ok"] is False


def test_engine_force_complete_skips_evidence(tmp_path):
    root = str(tmp_path)
    engine.create(root, _spec())
    # No evidence file exists, but force=True should still advance
    res = engine.complete(root, "demo", "a", force=True)
    assert res["ok"]
    assert res["current"] == "b"


def test_engine_force_overrides_out_of_order(tmp_path):
    root = str(tmp_path)
    engine.create(root, _spec())
    # 'c' is not current, but force=True lets it advance
    res = engine.complete(root, "demo", "c", note="forced", force=True)
    assert res["ok"]


def test_engine_evidence_min_gt_one(tmp_path):
    spec = {
        "id": "min-test",
        "title": "Min > 1",
        "start": "a",
        "stages": [
            {"id": "a", "title": "A",
             "evidence": {"kind": "artifact", "path": "data.md",
                          "contains": ["TOKEN"], "min": 2},
             "next": [{"to": "done"}]},
            {"id": "done", "title": "Done", "terminal": True},
        ],
    }
    root = str(tmp_path)
    engine.create(root, spec)
    data_f = tmp_path / "data.md"

    # One hit — not enough
    data_f.write_text("TOKEN once\n", encoding="utf-8")
    res = engine.complete(root, "min-test", "a")
    assert res["ok"] is False

    # Two hits — passes
    data_f.write_text("TOKEN first\nTOKEN second\n", encoding="utf-8")
    res = engine.complete(root, "min-test", "a")
    assert res["ok"]
    assert res["status"] == "completed"


def test_engine_complete_blocks_run_then_resume(tmp_path):
    """block() after a failure, then resume(), should allow completion."""
    root = str(tmp_path)
    engine.create(root, _spec())
    engine.block(root, "demo", "waiting on external")
    res = engine.complete(root, "demo", "a", note="try")
    assert res["ok"] is False
    assert "blocked" in res["error"]

    engine.resume(root, "demo")
    _write(tmp_path / "log.md", "A1\n")
    res = engine.complete(root, "demo", "a")
    assert res["ok"]


def test_engine_next_stage_on_completed_run(tmp_path):
    root = str(tmp_path)
    engine.create(root, _spec())
    _write(tmp_path / "log.md", "A1\n")
    engine.complete(root, "demo", "a")
    engine.complete(root, "demo", "b", note="b")
    engine.complete(root, "demo", "c", note="c")
    n = engine.next_stage(root, "demo")
    # Completed runs have no current stage
    assert "blocked" in n.get("after", {}).get("decision", "") or n.get("current") is None


def test_engine_next_stage_missing_run(tmp_path):
    res = engine.next_stage(str(tmp_path), "nope")
    assert res["ok"] is False


def test_engine_load_returns_empty_tuple_for_missing(tmp_path):
    state, spec = engine.load(str(tmp_path), "nope")
    assert state == {}
    assert spec == {}


# --- spec edge cases --------------------------------------------------------


@pytest.mark.parametrize("when, ctx, expected", [
    ({"key": "x", "equals": "1"}, {"x": "1"}, True),
    ({"key": "x", "equals": "1"}, {"x": "2"}, False),
    ({"key": "x", "equals": "1"}, {}, False),
    ({"all": [{"key": "a", "equals": "1"}, {"key": "b", "equals": "2"}]},
     {"a": "1", "b": "2"}, True),
    ({"all": [{"key": "a", "equals": "1"}, {"key": "b", "equals": "2"}]},
     {"a": "1", "b": "3"}, False),
    ({"any": [{"key": "a", "equals": "1"}, {"key": "b", "equals": "2"}]},
     {"a": "1"}, True),
    ({"any": [{"key": "a", "equals": "1"}, {"key": "b", "equals": "2"}]},
     {"a": "3", "b": "4"}, False),
    ({}, {"x": "1"}, True),
    (None, {"x": "1"}, True),
])
def test_evaluate_when_parametrized(when, ctx, expected):
    assert spec_mod.evaluate_when(when, ctx) == expected


def test_condition_keys_extracts_from_nested(tmp_path):
    when = {"all": [{"key": "a", "equals": "1"},
                    {"any": [{"key": "b", "equals": "2"},
                             {"key": "c", "equals": "3"}]}]}
    keys = spec_mod.condition_keys(when)
    assert "a" in keys
    assert "b" in keys
    assert "c" in keys


def test_compute_next_with_end_destination():
    spec = {
        "id": "end-edge",
        "title": "End edge",
        "start": "a",
        "stages": [
            {"id": "a", "title": "A",
             "evidence": {"kind": "note"},
             "next": [{"to": "end"}]},
        ],
    }
    assert spec_mod.validate(spec) == []
    assert spec_mod.compute_next(spec, current="a", completed={"a"}, ctx={}) == {
        "decision": "end"}


def test_compute_next_blocked_when_no_edge_matches():
    spec = {
        "id": "blocked-path",
        "title": "Blocked",
        "start": "a",
        "stages": [
            {"id": "a", "title": "A",
             "evidence": {"kind": "note"},
             "next": [{"to": "b", "when": {"key": "go", "equals": "true"}}]},
            {"id": "b", "title": "B",
             "evidence": {"kind": "note"},
             "next": [{"to": "done"}]},
            {"id": "done", "title": "Done", "terminal": True},
        ],
    }
    assert spec_mod.validate(spec) == []
    # With flag unset, no edge matches — blocked
    decision = spec_mod.compute_next(spec, current="a", completed=set(), ctx={})
    assert decision["decision"] == "blocked"


def test_advisory_multi_run_peek(tmp_path):
    root = str(tmp_path)
    engine.create(root, _spec())
    engine.create(root, _spec(), slug="other", force=True)
    line = advisory.peek(root)
    assert "WORKFLOW" in line
    assert "active" in line


# --- additional edge cases --------------------------------------------------


def test_spec_validates_artifact_min_is_strictly_positive():
    """Spec with min=0 should be rejected (at least 1 match required)."""
    s = _spec()
    s["stages"][0]["evidence"]["min"] = 0
    problems = spec_mod.validate(s)
    assert any(">= 1" in p for p in problems), f"expected min>=1 rejection, got {problems}"


def test_spec_validates_min_non_integer():
    s = _spec()
    s["stages"][0]["evidence"]["min"] = "high"
    problems = spec_mod.validate(s)
    assert any("integer" in p for p in problems)


def test_command_evidence_rejected_without_argv():
    """Spec with command evidence but no argv should be rejected."""
    s = _spec()
    s["stages"][0]["evidence"] = {"kind": "command"}
    problems = spec_mod.validate(s)
    assert any("argv" in p for p in problems)


def test_engine_load_loads_valid_spec(tmp_path):
    root = str(tmp_path)
    engine.create(root, _spec())
    state, spec = engine.load(root, "demo")
    assert state.get("slug") == "demo"
    assert spec.get("id") == "demo"
    assert "stages" in spec


def test_engine_load_returns_empty_for_missing(tmp_path):
    state, spec = engine.load(str(tmp_path), "nope")
    assert state == {}
    assert spec == {}


def test_blocked_transition_writes_bridge_for_completed_stage(tmp_path):
    """A stage that completes but hits a blocked transition should still
    write its bridge key — the stage was completed, it just can't advance."""
    spec = {
        "id": "block-bridge",
        "title": "Block bridge test",
        "start": "a",
        "stages": [
            {"id": "a", "title": "A",
             "evidence": {"kind": "artifact", "path": "log.md", "contains": ["OK"]},
             "bridge": "E-",
             "next": [{"to": "b", "when": {"key": "flag", "equals": "true"}}]},
            {"id": "b", "title": "B",
             "evidence": {"kind": "note"},
             "next": [{"to": "done"}]},
            {"id": "done", "title": "Done", "terminal": True},
        ],
    }
    root = str(tmp_path)
    engine.create(root, spec)
    (tmp_path / "log.md").write_text("OK\n", encoding="utf-8")
    engine.complete(root, "block-bridge", "a")
    from hooks.engine.modules import blackboard
    board = blackboard.read_board(root)
    # Stage 'a' was completed (evidence passed) even though blocked at transition
    bridge_key = next((k for k in board["keys"] if k.startswith("E-")), None)
    assert bridge_key is not None, "bridge should be written on successful complete"
    assert board["keys"]["workflow.block-bridge.status"]["value"] == "blocked"


# --- undecodable state (E-009) ------------------------------------------------
# UnicodeDecodeError is a ValueError but NOT a JSONDecodeError: a BINARY state
# file escaped read_state's / state_is_corrupt's except tuples and tracebacks
# on five CLI commands instead of reporting the file as corrupt.

_BINARY = b"\xff\xfe\x00 binary \x80 junk"


def _seed_binary(path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(_BINARY)
    return p


def test_read_state_binary_file_is_corrupt_not_crash(tmp_path):
    root = str(tmp_path)
    _seed_binary(store_mod.state_path(root, "bin"))
    assert store_mod.read_state(root, "bin") == {}
    assert engine.state_is_corrupt(root, "bin") is True


def test_status_on_binary_state_names_the_file(tmp_path):
    root = str(tmp_path)
    _seed_binary(store_mod.state_path(root, "bin"))
    out = engine.status(root, "bin")
    assert out["ok"] is False
    assert "corrupt" in out["error"]
    assert "no workflow" not in out["error"]


def test_cli_list_marks_binary_state_corrupt(tmp_path, capsys):
    root = str(tmp_path)
    _seed_binary(store_mod.state_path(root, "bin"))
    rc = wf_cli.main(["--project-root", root, "list"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    runs = {r["slug"]: r for r in out["runs"]}
    assert runs["bin"].get("corrupt") is True


def test_load_spec_binary_file_falls_back_to_empty(tmp_path):
    root = str(tmp_path)
    specs = Path(store_mod.paths(root)["specs"])
    specs.mkdir(parents=True, exist_ok=True)
    (specs / "x.json").write_bytes(_BINARY)
    assert store_mod.load_spec(root, "x") == {}


def test_read_events_binary_log_returns_empty(tmp_path):
    root = str(tmp_path)
    _seed_binary(store_mod.paths(root)["events"])
    assert store_mod.read_events(root) == []



# --- evidence freshness (E-056) -----------------------------------------------
# A completion is a claim about the workspace. The kernel used to check the
# claim once, at `complete`, and then deliver the stage as done forever; E-055
# measured that at stale_evidence_rate 1.0. These tests pin the derived verdict,
# the three-valued vocabulary (UNKNOWN never degrades to CURRENT), the predicate
# sensitivity (an edit that keeps the tokens stays CURRENT), and the refusal.

def _evidence_spec(pattern: str = "log.md") -> dict:
    return {
        "id": "ev",
        "title": "evidence demo",
        "start": "a",
        "stages": [
            {"id": "a", "title": "A",
             "evidence": {"kind": "artifact", "path": pattern,
                          "contains": ["A1"], "min": 1},
             "next": [{"to": "b"}]},
            {"id": "b", "title": "B", "requires": ["a"],
             "evidence": {"kind": "note"},
             "next": [{"to": "done"}]},
            {"id": "done", "title": "Done", "terminal": True},
        ],
    }


def _complete_a(tmp_path, spec: dict | None = None) -> tuple[str, dict]:
    spec = spec or _evidence_spec()
    root = str(tmp_path)
    engine.create(root, spec)
    _write(tmp_path / "log.md", "A1\n")
    assert engine.complete(root, spec["id"], "a")["ok"]
    return root, spec


def test_status_is_current_while_the_artifact_still_carries_evidence(tmp_path):
    root, spec = _complete_a(tmp_path)
    st = engine.status(root, spec["id"])
    assert st["evidence"]["a"]["verdict"] == "CURRENT"
    assert st["stale_evidence"] == []
    assert st["evidence_health"]["total"] == 1


def test_removing_the_declared_token_makes_the_completion_stale(tmp_path):
    root, spec = _complete_a(tmp_path)
    _write(tmp_path / "log.md", "nothing here\n")
    st = engine.status(root, spec["id"])
    assert st["evidence"]["a"]["verdict"] == "STALE"
    assert st["stale_evidence"] == ["a"]
    assert "no candidate matching" in st["evidence"]["a"]["reason"]


def test_deleted_artifact_is_stale_not_current(tmp_path):
    root, spec = _complete_a(tmp_path)
    (tmp_path / "log.md").unlink()
    st = engine.status(root, spec["id"])
    assert st["evidence"]["a"]["verdict"] == "STALE"


def test_evidence_is_predicate_sensitive_not_digest_sensitive(tmp_path):
    """An edit that keeps the declared tokens stays CURRENT.

    This is the paired control that rejects a "the file changed ⇒ stale"
    design, which would stale a stage every time a later stage appends to the
    artifact the run tracks.
    """
    root, spec = _complete_a(tmp_path)
    _write(tmp_path / "log.md", "A1\nmore findings, still A1\n")
    assert engine.status(root, spec["id"])["evidence"]["a"]["verdict"] == "CURRENT"


def test_status_and_next_agree_because_one_derivation_feeds_both(tmp_path):
    root, spec = _complete_a(tmp_path)
    (tmp_path / "log.md").unlink()
    from_status = engine.status(root, spec["id"])["evidence"]["a"]["verdict"]
    from_next = engine.next_stage(root, spec["id"])["evidence"]["a"]["verdict"]
    assert from_status == from_next == "STALE"


def test_glob_evidence_stays_current_while_another_match_has_the_token(tmp_path):
    spec = _evidence_spec(pattern="logs/*.md")
    root = str(tmp_path)
    engine.create(root, spec)
    _write(tmp_path / "logs" / "a.md", "A1\n")
    _write(tmp_path / "logs" / "b.md", "A1\n")
    assert engine.complete(root, spec["id"], "a")["ok"]
    (tmp_path / "logs" / "a.md").unlink()
    assert engine.status(root, spec["id"])["evidence"]["a"]["verdict"] == "CURRENT"


def test_glob_evidence_with_no_satisfying_match_is_stale(tmp_path):
    spec = _evidence_spec(pattern="logs/*.md")
    root = str(tmp_path)
    engine.create(root, spec)
    _write(tmp_path / "logs" / "a.md", "A1\n")
    assert engine.complete(root, spec["id"], "a")["ok"]
    _write(tmp_path / "logs" / "a.md", "gone\n")
    assert engine.status(root, spec["id"])["stale_evidence"] == ["a"]


def test_legacy_completion_without_witness_reads_unknown_never_current(tmp_path):
    root, spec = _complete_a(tmp_path)
    path = Path(store_mod.state_path(root, spec["id"]))
    state = json.loads(path.read_text(encoding="utf-8"))
    state.pop("evidence", None)  # a run recorded before E-056
    path.write_text(json.dumps(state), encoding="utf-8")
    st = engine.status(root, spec["id"])
    assert st["evidence"]["a"]["verdict"] == "UNKNOWN"
    assert st["stale_evidence"] == []
    # UNKNOWN is not a refusal: a legacy run stays advanceable.
    assert engine.complete(root, spec["id"], "b", note="ok")["ok"] is True


def test_complete_refuses_to_advance_on_a_stale_foundation(tmp_path):
    root, spec = _complete_a(tmp_path)
    (tmp_path / "log.md").unlink()
    refused = engine.complete(root, spec["id"], "b", note="done")
    assert refused["ok"] is False
    assert refused["stale_evidence"] == ["a"]
    assert "no longer holds" in refused["error"]
    assert "--force" in refused["error"]


def test_force_still_overrides_the_stale_foundation_refusal(tmp_path):
    root, spec = _complete_a(tmp_path)
    (tmp_path / "log.md").unlink()
    assert engine.complete(root, spec["id"], "b", note="done", force=True)["ok"] is True


def test_requires_less_spec_treats_the_completed_chain_as_the_foundation(tmp_path):
    spec = _evidence_spec()
    del spec["stages"][1]["requires"]
    root, spec = _complete_a(tmp_path, spec)
    (tmp_path / "log.md").unlink()
    assert engine.complete(root, spec["id"], "b", note="done")["ok"] is False


def test_unreadable_artifact_reads_unknown_not_stale(tmp_path, monkeypatch):
    _write(tmp_path / "log.md", "A1\n")

    def _boom(self, *args, **kwargs):
        raise OSError("locked")

    monkeypatch.setattr(Path, "read_text", _boom)
    verdict, detail = evidence_mod.classify(
        str(tmp_path), {"kind": "artifact", "path": "log.md",
                        "contains": ["A1"], "min": 1})
    assert verdict == "UNKNOWN"
    assert "cannot be read" in detail["reason"]


def test_witness_records_artifact_predicates_and_dated_commands(tmp_path):
    # A `note` is a statement, not a claim: no witness at all (rule 5).
    assert evidence_mod.witness_for({"evidence": {"kind": "note"}}) is None
    # A `command` IS dated now (E-064) — E-063 measured that leaving it
    # unwitnessed delivered a green suite as satisfied forever.
    root, _spec = _git_project(tmp_path)
    cmd = evidence_mod.witness_for(
        {"evidence": {"kind": "command", "argv": ["pytest"], "rc": 0}},
        project_root=root)
    assert cmd["kind"] == "command" and cmd["argv"] == ["pytest"] and cmd["rc"] == 0
    assert cmd["code_state"] and "." in cmd["code_state"]
    w = evidence_mod.witness_for({"evidence": {"kind": "artifact", "path": "log.md",
                                               "contains": ["A1"], "min": 2}})
    assert w["path"] == "log.md" and w["min"] == 2 and w["contains"] == ["A1"]
    # The predicate witness carries no code state: it is re-derived from the file.
    assert "code_state" not in w


def test_foundation_is_the_declared_requires_or_the_completed_chain():
    assert evidence_mod.foundation_of({"completed": ["a", "z"]},
                                      {"requires": ["a"]}) == ["a"]
    assert evidence_mod.foundation_of({"completed": ["a", "z"]}, {}) == ["a", "z"]


def test_advisory_flags_a_moved_foundation(tmp_path):
    root, spec = _complete_a(tmp_path)
    (tmp_path / "log.md").unlink()
    line = advisory.peek(root)
    assert "FOUNDATION MOVED" in line
    assert "'a'" in line


def test_cli_status_and_next_carry_the_evidence_verdict(tmp_path, capsys):
    root, spec = _complete_a(tmp_path)
    (tmp_path / "log.md").unlink()
    assert wf_cli.main(["--project-root", root, "status",
                        "--slug", spec["id"]]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["stale_evidence"] == ["a"]
    assert wf_cli.main(["--project-root", root, "next", "--slug", spec["id"]]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["evidence"]["a"]["verdict"] == "STALE"


# --- command evidence, dated to a code state (E-064) --------------------------
# A command's result cannot be re-derived by looking, so the plane compares the
# TRACKED code state the command was recorded against with the one in force now.
# Unchanged answers CURRENT; moved answers UNKNOWN naming both states; an oracle
# that cannot answer at all answers UNKNOWN with the reason. NEVER STALE — a look
# cannot falsify "this argv exited 0" — so the dating never blocks an advance.

def _git(project_root, *args):
    return subprocess.run(["git", *args], cwd=project_root, capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          stdin=subprocess.DEVNULL, timeout=60)


def _git_project(tmp_path):
    """A project root that is a git work tree with one commit."""
    root = tmp_path / "proj"
    root.mkdir(parents=True, exist_ok=True)
    _write(root / "app.py", "VALUE = 1\n")
    if _git(root, "init", "-q").returncode != 0:
        pytest.skip("git unavailable on this host")
    ident = ["-c", "user.email=t@example.invalid", "-c", "user.name=t"]
    _git(root, *ident, "add", "-A")
    if _git(root, *ident, "commit", "-q", "-m", "init").returncode != 0:
        pytest.skip("git cannot commit on this host")
    return str(root), _command_spec()


def _command_spec() -> dict:
    return {
        "id": "cmd-demo", "title": "command demo", "start": "check",
        "stages": [
            {"id": "check", "title": "Run the suite",
             "evidence": {"kind": "command",
                          "argv": [sys.executable, "-c", "print('ok')"], "rc": 0},
             "next": [{"to": "ship"}]},
            {"id": "ship", "title": "Ship",
             "evidence": {"kind": "note"}, "next": [{"to": "done"}]},
            {"id": "done", "title": "Done", "terminal": True},
        ],
    }


def _completed_command_run(tmp_path):
    root, spec = _git_project(tmp_path)
    assert engine.create(root, spec)["ok"]
    assert engine.complete(root, spec["id"], "check")["ok"] is True
    return root, spec


def test_an_unchanged_code_state_answers_current(tmp_path):
    root, spec = _completed_command_run(tmp_path)
    st = engine.status(root, spec["id"])
    assert st["evidence"]["check"]["verdict"] == "CURRENT"
    assert "tracked code state" in st["evidence"]["check"]["reason"]
    assert st["evidence_health"]["total"] == 1


def test_a_dirty_edit_of_a_tracked_file_moves_the_code_state(tmp_path):
    """HEAD stands; the working tree is part of the fingerprint."""
    root, spec = _completed_command_run(tmp_path)
    _write(Path(root) / "app.py", "VALUE = 2\n")
    st = engine.status(root, spec["id"])
    assert st["evidence"]["check"]["verdict"] == "UNKNOWN"
    assert "moved since this command was recorded" in st["evidence"]["check"]["reason"]
    # A command's outcome is never FALSIFIED by a look, so it is never STALE and
    # it never blocks the advance (the kernel's own refusal list is unaffected).
    assert st["stale_evidence"] == []
    assert engine.complete(root, spec["id"], "ship", note="shipped")["ok"] is True


def test_a_new_commit_moves_the_code_state(tmp_path):
    root, spec = _completed_command_run(tmp_path)
    ident = ["-c", "user.email=t@example.invalid", "-c", "user.name=t"]
    _git(root, *ident, "add", "-A")
    assert _git(root, *ident, "commit", "-q", "-m", "moved").returncode == 0
    listed = {r["slug"]: r for r in engine.list_runs(root)["runs"]}[spec["id"]]
    assert listed["evidence"]["check"]["verdict"] == "UNKNOWN"
    assert "moved since this command was recorded" in listed["evidence"]["check"]["reason"]


def test_a_command_witness_without_a_code_state_reads_unknown(tmp_path):
    legacy = {"kind": "command", "argv": ["pytest"], "rc": 0}
    verdict, detail = evidence_mod.classify(str(tmp_path), legacy)
    assert verdict == "UNKNOWN"
    assert "before completions were dated" in detail["reason"]
    undatable = {**legacy, "code_state": None}
    verdict, detail = evidence_mod.classify(str(tmp_path), undatable)
    assert verdict == "UNKNOWN"
    assert "no code state could be established" in detail["reason"]
    assert verdict != "STALE"


def test_a_code_state_that_cannot_be_read_answers_unknown_not_current(tmp_path):
    """No git work tree (or no commit): the oracle fails open to UNKNOWN."""
    assert evidence_mod.code_state(str(tmp_path)) is None
    verdict, detail = evidence_mod.classify(
        str(tmp_path), {"kind": "command", "argv": ["pytest"], "rc": 0,
                        "code_state": "deadbeef.0123456789ab"})
    assert verdict == "UNKNOWN"
    assert "cannot be established here" in detail["reason"]


def test_an_inventory_read_probes_the_code_state_once(tmp_path, monkeypatch):
    """E-058's doctrine applied to the new probe: one bounded call per read pass."""
    root, first = _git_project(tmp_path)
    assert engine.create(root, first)["ok"]
    assert engine.complete(root, first["id"], "check")["ok"] is True
    second = {**_command_spec(), "id": "cmd-second"}
    assert engine.create(root, second)["ok"]
    assert engine.complete(root, "cmd-second", "check")["ok"] is True
    calls = {"n": 0}
    real = evidence_mod.code_state

    def counted(project_root):
        calls["n"] += 1
        return real(project_root)

    monkeypatch.setattr(evidence_mod, "code_state", counted)
    runs = engine.list_runs(root)["runs"]
    assert len(runs) == 2
    assert all(r["evidence_health"]["total"] >= 1 for r in runs)
    assert calls["n"] == 1, "the inventory must share ONE probe across its runs"


# --- evidence read budget + inventory surface (E-058) -------------------------
# The re-derivation read runs on the session edge, so it is bounded: at most
# MAX_CANDIDATES glob matches are scanned and no candidate past
# MAX_ARTIFACT_BYTES is read. A match inside the budget stays exact; anything
# skipped turns the answer into UNKNOWN — never a false STALE. The COMPLETION
# path stays unbounded (it reads the artifact the operator just produced).

def _bound_spec(pattern: str = "log.md") -> dict:
    return {
        "id": "bound",
        "title": "bound demo",
        "start": "a",
        "stages": [
            {"id": "a", "title": "A",
             "evidence": {"kind": "artifact", "path": pattern,
                          "contains": ["A1"], "min": 1},
             "next": [{"to": "done"}]},
            {"id": "done", "title": "Done", "terminal": True},
        ],
    }


def _touch(path: Path, when: float) -> None:
    os.utime(path, (when, when))


def test_completion_stays_unbounded_while_the_verdict_is_bounded(tmp_path, monkeypatch):
    """A big artifact can still complete; re-deriving it answers UNKNOWN."""
    monkeypatch.setattr(evidence_mod, "MAX_ARTIFACT_BYTES", 8)
    spec = _bound_spec()
    root = str(tmp_path)
    engine.create(root, spec)
    _write(tmp_path / "log.md", "A1 and more text well past the bound\n")
    assert engine.complete(root, spec["id"], "a")["ok"] is True
    st = engine.status(root, spec["id"])
    assert st["evidence"]["a"]["verdict"] == "UNKNOWN"
    assert "read budget" in st["evidence"]["a"]["reason"]
    # UNKNOWN is not a refusal, and the stage is not named as stale.
    assert st["stale_evidence"] == []


def test_a_candidate_past_the_byte_budget_never_produces_a_false_stale(tmp_path, monkeypatch):
    """One oversized candidate plus one clean miss must read UNKNOWN, not STALE."""
    monkeypatch.setattr(evidence_mod, "MAX_ARTIFACT_BYTES", 8)
    spec = _bound_spec(pattern="logs/*.md")
    root = str(tmp_path)
    engine.create(root, spec)
    # E-071: the witness must be a GENUINE one — `--force` now records that it
    # was forced, which would answer UNKNOWN before the budget is ever consulted
    # and stop exercising the read path this test is about.
    _write(tmp_path / "logs" / "small.md", "A1\n")
    assert engine.complete(root, spec["id"], "a")["ok"] is True
    # The satisfying candidate loses the token, and another one is oversized.
    _write(tmp_path / "logs" / "small.md", "nothing\n")
    _write(tmp_path / "logs" / "big.md", "x" * 64 + "\n")
    st = engine.status(root, spec["id"])
    assert st["evidence"]["a"]["verdict"] == "UNKNOWN"
    assert "byte read budget" in st["evidence"]["a"]["reason"]


def test_the_scan_budget_stops_the_walk_and_reports_it(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence_mod, "MAX_CANDIDATES", 1)
    spec = _bound_spec(pattern="logs/*.md")
    root = str(tmp_path)
    engine.create(root, spec)
    _write(tmp_path / "logs" / "old.md", "A1\n")
    _write(tmp_path / "logs" / "new.md", "no token\n")
    _touch(tmp_path / "logs" / "old.md", 1_000_000_000)
    _touch(tmp_path / "logs" / "new.md", 2_000_000_000)  # newest-first reaches it
    assert engine.complete(root, spec["id"], "a")["ok"] is True
    st = engine.status(root, spec["id"])
    assert st["evidence"]["a"]["verdict"] == "UNKNOWN"
    assert "scan budget" in st["evidence"]["a"]["reason"]


def test_a_match_inside_the_budget_stays_exact(tmp_path, monkeypatch):
    """A partial scan that FOUND the evidence answers CURRENT, not UNKNOWN."""
    monkeypatch.setattr(evidence_mod, "MAX_CANDIDATES", 2)
    spec = _bound_spec(pattern="logs/*.md")
    root = str(tmp_path)
    engine.create(root, spec)
    _write(tmp_path / "logs" / "old.md", "A1\n")
    _write(tmp_path / "logs" / "middle.md", "A1\n")
    _write(tmp_path / "logs" / "new.md", "no token\n")
    _touch(tmp_path / "logs" / "old.md", 1_000_000_000)
    _touch(tmp_path / "logs" / "middle.md", 2_000_000_000)
    _touch(tmp_path / "logs" / "new.md", 3_000_000_000)
    assert engine.complete(root, spec["id"], "a")["ok"] is True
    assert engine.status(root, spec["id"])["evidence"]["a"]["verdict"] == "CURRENT"


def test_the_inventory_surface_carries_the_verdict(tmp_path):
    root, spec = _complete_a(tmp_path)
    listed = {r["slug"]: r for r in engine.list_runs(root)["runs"]}[spec["id"]]
    assert listed["evidence"]["a"]["verdict"] == "CURRENT"
    assert listed["evidence_health"]["total"] == 1
    assert listed["stale_evidence"] == []


def test_the_inventory_surface_names_a_broken_foundation(tmp_path):
    root, spec = _complete_a(tmp_path)
    (tmp_path / "log.md").unlink()
    listed = {r["slug"]: r for r in engine.list_runs(root)["runs"]}[spec["id"]]
    assert listed["stale_evidence"] == ["a"]
    assert listed["evidence"]["a"]["verdict"] == "STALE"


def test_the_inventory_surface_survives_a_run_whose_spec_is_gone(tmp_path):
    root, spec = _complete_a(tmp_path)
    store_mod.paths(root)
    spec_file = Path(store_mod.paths(root)["specs"]) / f"{spec['id']}.json"
    spec_file.unlink()
    listed = {r["slug"]: r for r in engine.list_runs(root)["runs"]}[spec["id"]]
    # The witness — not the spec — is the claim, so it is still classified.
    assert listed["evidence"]["a"]["verdict"] == "CURRENT"


# --- the read never leaves the project root (E-060) ---------------------------
# A run's evidence is a claim about ITS OWN project, so an artifact path that
# resolves outside the project root is never read. The completion gate refuses
# it (a gate refuses, and names the cause); every read surface answers UNKNOWN
# instead — never CURRENT, which would mean the outside file was read and
# turned into a verdict, and never STALE, which would mean it was read and
# turned into a definite negative. Containment lives in the one matcher
# (evidence.artifact_matches), so the witness and the re-derivation cannot
# disagree about it.

def _escape_spec(declared: str, slug: str = "escape") -> dict:
    return {
        "id": slug,
        "title": "escape demo",
        "start": "a",
        "stages": [
            {"id": "a", "title": "A",
             "evidence": {"kind": "artifact", "path": declared,
                          "contains": ["A1"], "min": 1},
             "next": [{"to": "done"}]},
            {"id": "done", "title": "Done", "terminal": True},
        ],
    }


def _plant_outside(tmp_path):
    """The artifact the run must NOT read: a sibling of the project root."""
    outside = tmp_path.parent / f"{tmp_path.name}-outside.md"
    _write(outside, "A1 — this file belongs to nobody's run\n")
    return outside


def test_a_declared_path_leaving_the_root_is_refused_at_completion(tmp_path):
    root = str(tmp_path)
    outside = _plant_outside(tmp_path)
    spellings = [f"../{outside.name}", str(outside),
                 f"docs/../../{outside.name}"]
    for i, declared in enumerate(spellings):
        spec = _escape_spec(declared, slug=f"escape-{i}")
        engine.create(root, spec)
        res = engine.complete(root, spec["id"], "a")
        assert res["ok"] is False
        assert "outside the project root" in res["error"]
        # Refused BEFORE the witness is written: nothing claims the outside file.
        assert store_mod.read_state(root, spec["id"]).get("evidence", {}) == {}


def test_an_out_of_root_witness_reads_unknown_not_current_or_stale(tmp_path):
    """A stored witness naming an outside file (an older tree, a hand edit)."""
    root = str(tmp_path)
    outside = _plant_outside(tmp_path)
    spec = _escape_spec(f"../{outside.name}")
    engine.create(root, spec)
    assert engine.complete(root, spec["id"], "a", force=True)["ok"] is True
    st = engine.status(root, spec["id"])
    assert st["evidence"]["a"]["verdict"] == "UNKNOWN"
    assert "outside the project root" in st["evidence"]["a"]["reason"]
    # UNKNOWN is not staleness: the plane cannot establish the claim, so it does
    # not name the stage as broken and does not block an advance.
    assert st["stale_evidence"] == []


def test_the_inventory_and_the_advisory_report_the_same_unknown(tmp_path):
    root = str(tmp_path)
    outside = _plant_outside(tmp_path)
    spec = _escape_spec(f"../{outside.name}")
    engine.create(root, spec)
    assert engine.complete(root, spec["id"], "a", force=True)["ok"] is True
    listed = {r["slug"]: r for r in engine.list_runs(root)["runs"]}[spec["id"]]
    assert listed["evidence"]["a"]["verdict"] == "UNKNOWN"
    assert listed["stale_evidence"] == []
    assert "FOUNDATION MOVED" not in advisory.peek(root)


def _link_dir(link: Path, target: Path) -> bool:
    """Point ``link`` at ``target`` however this host allows, else False."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        pass
    if os.name == "nt":
        # A junction needs no privilege on Windows and `realpath` resolves it
        # like any other link, so the containment check is exercised on the
        # platform most likely to lack the symlink privilege.
        try:
            proc = subprocess.run(["cmd", "/c", "mklink", "/J",
                                   str(link), str(target)],
                                  capture_output=True, text=True,
                                  stdin=subprocess.DEVNULL)
            return proc.returncode == 0 and link.exists()
        except OSError:
            return False
    return False


def test_a_linked_candidate_outside_the_root_is_not_read(tmp_path):
    """The DECLARED path is inside the root; the glob reaches outside it."""
    root = tmp_path / "proj"
    root.mkdir()
    away = tmp_path / "away"
    _write(away / "x.md", "A1\n")
    if not _link_dir(root / "logs", away):
        pytest.skip("no symlink/junction support on this host")
    spec = _escape_spec("logs/*.md")
    engine.create(str(root), spec)
    res = engine.complete(str(root), spec["id"], "a")
    assert res["ok"] is False
    assert "outside the project root" in res["error"]
    # And the re-derivation agrees, from the same matcher.
    assert engine.complete(str(root), spec["id"], "a", force=True)["ok"] is True
    st = engine.status(str(root), spec["id"])
    assert st["evidence"]["a"]["verdict"] == "UNKNOWN"
    assert "outside the project root" in st["evidence"]["a"]["reason"]


def test_containment_does_not_disturb_in_root_resolution(tmp_path):
    """A nested in-root glob still completes and still reads CURRENT."""
    root = tmp_path / "proj"
    root.mkdir()
    _write(root / "deep" / "nested" / "log.md", "A1\n")
    spec = _escape_spec("deep/**/*.md")
    engine.create(str(root), spec)
    assert engine.complete(str(root), spec["id"], "a")["ok"] is True
    assert engine.status(str(root), spec["id"])["evidence"]["a"]["verdict"] == "CURRENT"


# --- the storage plane resolves the run's own spec (E-062) --------------------
# A slug and a stored `spec_file` are STRINGS until the kernel makes them a
# filename, and both were turned into paths raw: `load_spec` joined the slug
# without the sanitizer `state_path` uses (E-008's seam, one filename over), and
# it opened the state's `spec_file` wherever it pointed. The spec decides which
# stages exist and where the run goes, so reading an outside one silently
# replaces the run's definition on every surface. Both now go through the same
# sanitizer and the same containment helper the evidence plane calls.

def _storage_spec(map_title: str, map_next: str, *, brief_kind: str = "note") -> dict:
    """A run whose current stage's title and next stage come from the spec."""
    return {
        "id": "demo", "title": "storage demo", "start": "analyze",
        "stages": [
            {"id": "analyze", "title": "Analyze",
             "evidence": {"kind": "artifact", "path": "logs/analysis.md",
                          "contains": ["FINDING"], "min": 1},
             "next": [{"to": "brief"}]},
            {"id": "brief", "title": "Brief",
             "evidence": {"kind": brief_kind, "path": "logs/analysis.md",
                          "contains": ["OUTSIDE-TOKEN"], "min": 1},
             "next": [{"to": "map"}]},
            {"id": "map", "title": map_title,
             "evidence": {"kind": "note"}, "next": [{"to": map_next}]},
            {"id": map_next, "title": "End", "terminal": True},
        ],
    }


def _plant_outside_spec(tmp_path) -> Path:
    """A spec beside the project root — four levels up from the specs dir."""
    outside = tmp_path / "loaded.json"
    _write(outside, json.dumps(_storage_spec("OUTSIDE MAP", "outside-done",
                                             brief_kind="artifact")))
    return outside


def test_the_spec_path_is_built_from_the_sanitized_slug(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    _plant_outside_spec(tmp_path)
    assert store_mod.load_spec(str(root), "../../../../loaded") == {}
    # The sanitized spelling, planted INSIDE the specs dir, is what is read.
    specs = Path(store_mod.paths(str(root))["specs"])
    _write(specs / f"{store_mod.safe_slug('../../../../loaded')}.json",
           json.dumps({"id": "inside", "title": "inside spec"}))
    assert store_mod.load_spec(str(root), "../../../../loaded")["id"] == "inside"


def test_a_crafted_slug_reads_the_runs_own_spec(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    _plant_outside_spec(tmp_path)
    crafted = "../../../../loaded"
    engine.create(str(root), _storage_spec("MAP", "done"), slug=crafted)
    _write(root / "logs" / "analysis.md", "FINDING 1\n")
    assert engine.complete(str(root), crafted, "analyze")["ok"] is True
    assert engine.complete(str(root), crafted, "brief", note="brief produced")["ok"] is True
    st = engine.status(str(root), crafted)
    assert st["total_stages"] == len(_storage_spec("MAP", "done")["stages"])
    assert st["stage"]["title"] == "MAP"  # not "OUTSIDE MAP"
    assert engine.next_stage(str(root), crafted)["after"]["stage"] == "done"
    # The inventory addresses a run by its sanitized filename, so the traversal
    # was never reachable there — pinned so a later change cannot lose it.
    row = {r["slug"]: r for r in engine.list_runs(str(root))["runs"]}[
        store_mod.safe_slug(crafted)]
    assert row["evidence_health"]["total"] == 1  # the unwitnessed `brief` is a note stage


def test_a_stored_spec_file_outside_the_root_is_not_read(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    outside = _plant_outside_spec(tmp_path)
    # A slug with no spec file of its own: the stored spec_file is the only
    # candidate left, which is where an older tree or a hand edit lands.
    engine.create(str(root), _storage_spec("MAP", "done"), slug="orphan")
    path = Path(store_mod.state_path(str(root), "orphan"))
    state = json.loads(path.read_text(encoding="utf-8"))
    state["spec_file"] = str(outside)
    path.write_text(json.dumps(state), encoding="utf-8")
    assert store_mod.load_spec(str(root), "orphan") == {}
    st = engine.status(str(root), "orphan")
    assert st["total_stages"] == 0 and st["stage"] is None
    assert engine.next_stage(str(root), "orphan")["after"]["decision"] == "blocked"


def test_an_in_root_spec_file_is_still_read(tmp_path):
    """The containment must not refuse the path the kernel itself wrote."""
    root = tmp_path / "proj"
    root.mkdir()
    engine.create(str(root), _storage_spec("MAP", "done"), slug="orphan")
    assert store_mod.load_spec(str(root), "orphan")["id"] == "demo"


def test_containment_is_one_implementation_shared_by_both_planes():
    from bmad.workflow import paths as paths_mod
    assert evidence_mod.root_of is paths_mod.root_of
    assert paths_mod.outside(paths_mod.root_of(str(_ROOT)), str(_ROOT / "README.md")) is False
    assert paths_mod.outside(paths_mod.root_of(str(_ROOT / "bmad")), str(_ROOT)) is True


# --- the declared `artifact` field (E-067) -------------------------------------
# The spec's top-level pointer is never opened — it is REPEATED to the agent
# (`status.artifact`, "tracked in <it>" at the session edge), which is why it
# escaped both containment planes until now.

def _artifact_field_spec(declared: str | None = None, slug: str = "artifact-demo") -> dict:
    spec = _spec()
    spec["id"] = slug
    if declared is not None:
        spec["artifact"] = declared
    return spec


def _completed_artifact_field_run(root: str, declared: str | None = None) -> dict:
    spec = _artifact_field_spec(declared)
    assert engine.create(root, spec)["ok"] is True
    _write(Path(root) / "log.md", "A1\n")
    assert engine.complete(root, spec["id"], "a")["ok"] is True
    return spec


def test_an_out_of_root_declared_artifact_is_not_delivered(tmp_path):
    """A hand-edited / older-tree spec copy still declares it; the read refuses."""
    root = str(tmp_path)
    spec = _completed_artifact_field_run(root)
    copy = Path(store_mod.paths(root)["specs"]) / f"{spec['id']}.json"
    data = json.loads(copy.read_text(encoding="utf-8"))
    data["artifact"] = "../outside-notes.md"
    copy.write_text(json.dumps(data), encoding="utf-8")
    st = engine.status(root, spec["id"])
    assert st["artifact"] is None
    assert "../outside-notes.md" in st["artifact_note"]
    assert "outside the project root" in st["artifact_note"]
    line = advisory.peek(root)
    assert "tracked in ../outside-notes.md" not in line
    assert "outside the project root" in line


def test_an_in_root_declared_artifact_is_delivered_unchanged(tmp_path):
    root = str(tmp_path)
    spec = _completed_artifact_field_run(root, "docs/notes.md")
    st = engine.status(root, spec["id"])
    assert st["artifact"] == "docs/notes.md"
    assert st["artifact_note"] == ""
    assert "tracked in docs/notes.md" in advisory.peek(root)


def test_no_declared_artifact_reports_none_rather_than_a_guess(tmp_path):
    root = str(tmp_path)
    spec = _completed_artifact_field_run(root)
    st = engine.status(root, spec["id"])
    assert st["artifact"] is None and st["artifact_note"] == ""
    assert "tracked in" not in advisory.peek(root)


# --- resolving a slug: a name question, not an evidence one (E-068) -----------

def _three_artifact_runs(root: str) -> None:
    """Three runs, each with a completed artifact stage anything would classify."""
    _write(Path(root) / "log.md", "A1\n")
    for slug in ("r1", "r2", "r3"):
        assert engine.create(root, _spec(), slug=slug)["ok"] is True
        assert engine.complete(root, slug, "a")["ok"] is True


def _count_matches(monkeypatch) -> dict:
    """Count every artifact match the plane performs (its one choke point)."""
    seen = {"n": 0}
    real = evidence_mod.artifact_matches

    def counted(*args, **kwargs):
        seen["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(evidence_mod, "artifact_matches", counted)
    return seen


def test_resolve_slug_answers_from_names_without_classifying(tmp_path, monkeypatch):
    root = str(tmp_path)
    _three_artifact_runs(root)
    seen = _count_matches(monkeypatch)
    assert engine.resolve_slug(root) is None  # three runs: still ambiguous
    assert seen["n"] == 0
    assert engine.resolve_slug(root, "r1") == "r1"
    assert seen["n"] == 0


def test_resolve_slug_names_the_single_run_and_stays_ambiguous_otherwise(tmp_path):
    root = str(tmp_path)
    assert engine.resolve_slug(root) is None  # nothing to resolve
    assert engine.create(root, _spec(), slug="only")["ok"] is True
    assert engine.resolve_slug(root) == "only"  # one name: that name
    assert engine.create(root, _spec(), slug="second")["ok"] is True
    assert engine.resolve_slug(root) is None


def test_resolve_slug_counts_a_corrupt_run_as_a_name(tmp_path):
    """The inventory listed one row per state file — corrupt ones included."""
    root = str(tmp_path)
    _write(Path(store_mod.state_path(root, "broken")), "{not json")
    assert engine.resolve_slug(root) == "broken"
    assert engine.create(root, _spec(), slug="good")["ok"] is True
    assert engine.resolve_slug(root) is None  # two names, one of them unreadable
    assert "corrupt" in engine.status(root, "broken")["error"]


def test_the_cli_classifies_only_the_answered_run(tmp_path, monkeypatch, capsys):
    root = str(tmp_path)
    _write(Path(root) / "log.md", "A1\n")
    assert engine.create(root, _spec(), slug="only")["ok"] is True
    assert engine.complete(root, "only", "a")["ok"] is True
    seen = _count_matches(monkeypatch)
    assert wf_cli.main(["--project-root", root, "status"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out["slug"] == "only"
    # One derivation for one answer: the resolver adds none.
    assert seen["n"] == 1


# --- a forced completion records that it was forced (E-071) --------------------

def test_a_forced_artifact_completion_reads_unknown_and_does_not_block(tmp_path):
    """`--force` satisfied nothing, so it must not read as a workspace fault."""
    root = str(tmp_path)
    spec = _artifact_field_spec()
    assert engine.create(root, spec)["ok"] is True
    _write(Path(root) / "log.md", "# no token here\n")
    assert engine.complete(root, spec["id"], "a")["ok"] is False
    assert engine.complete(root, spec["id"], "a", force=True)["ok"] is True
    st = engine.status(root, spec["id"])
    assert st["evidence"]["a"]["verdict"] == "UNKNOWN"
    assert "--force" in st["evidence"]["a"]["reason"]
    # Not a workspace fault: nothing lands in stale_evidence, so the run is not
    # locked into forcing every later stage too.
    assert st["stale_evidence"] == []
    assert engine.next_stage(root, spec["id"])["evidence"]["a"]["verdict"] == "UNKNOWN"
    assert engine.complete(root, spec["id"], "b", note="proceeded")["ok"] is True


def test_a_forced_command_completion_reads_unknown_not_current(tmp_path):
    """A command that exited 3 against rc 0 was never a passed suite."""
    root = tmp_path / "proj"
    root.mkdir()
    _write(root / "app.py", "VALUE = 1\n")
    if _git(str(root), "init", "-q").returncode != 0:
        pytest.skip("git unavailable on this host")
    ident = ["-c", "user.email=t@example.invalid", "-c", "user.name=t"]
    _git(str(root), *ident, "add", "-A")
    if _git(str(root), *ident, "commit", "-q", "-m", "init").returncode != 0:
        pytest.skip("git cannot commit on this host")
    spec = {
        "id": "cmd-forced", "title": "t", "start": "c",
        "stages": [{"id": "c", "title": "Run",
                     "evidence": {"kind": "command",
                                  "argv": [sys.executable, "-c",
                                           "import sys; sys.exit(3)"], "rc": 0},
                     "next": [{"to": "done"}]},
                    {"id": "done", "title": "Done", "terminal": True}],
    }
    assert engine.create(str(root), spec)["ok"] is True
    assert engine.complete(str(root), "cmd-forced", "c", force=True)["ok"] is True
    entry = engine.status(str(root), "cmd-forced")["evidence"]["c"]
    assert entry["verdict"] == "UNKNOWN"
    assert entry["verdict"] != "CURRENT"
    assert "--force" in entry["reason"]


def test_a_genuine_completion_is_still_current_without_a_forced_marker(tmp_path):
    root = str(tmp_path)
    spec = _artifact_field_spec()
    engine.create(root, spec)
    _write(Path(root) / "log.md", "A1\n")
    assert engine.complete(root, spec["id"], "a")["ok"] is True
    st = engine.status(root, spec["id"])
    assert st["evidence"]["a"]["verdict"] == "CURRENT"
    state = json.loads(Path(store_mod.state_path(root, spec["id"])).read_text(encoding="utf-8"))
    assert "forced" not in (state.get("evidence") or {})["a"]


def test_unsatisfied_evidence_is_still_refused_without_force(tmp_path):
    root = str(tmp_path)
    spec = _artifact_field_spec()
    engine.create(root, spec)
    _write(Path(root) / "log.md", "# no token here\n")
    got = engine.complete(root, spec["id"], "a")
    assert got["ok"] is False and "not satisfied" in got["error"]
    assert json.loads(Path(store_mod.state_path(root, spec["id"])).read_text(encoding="utf-8"))["completed"] == []


def test_create_refuses_a_declared_artifact_outside_the_root(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    outside = tmp_path / "outside-notes.md"
    _write(outside, "# not this run's\n")
    for declared in ("../outside-notes.md", "docs/../../outside-notes.md",
                     str(outside)):
        got = engine.create(str(root), _artifact_field_spec(declared),
                            slug=f"bad-{len(declared)}", force=True)
        assert got["ok"] is False
        assert "outside the project root" in got["error"]
    # Nothing was planted: the refusal happens before the spec copy is written.
    assert not list((root / ".metodoloji" / "workflow" / "specs").glob("*.json"))
    # ... and an in-root pointer is still accepted (no over-refusal).
    assert engine.create(str(root), _artifact_field_spec("docs/notes.md"))["ok"] is True

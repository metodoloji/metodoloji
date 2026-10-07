"""Tests for the workflow kernel: spec validation, deterministic transitions,
evidence-gated completion, and the CLI — the contract the SEO scenario rides."""

import json
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from bmad.workflow import advisory, engine, spec as spec_mod, store as store_mod  # noqa: E402
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


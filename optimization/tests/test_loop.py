from __future__ import annotations

import dataclasses
import importlib.util
import json
import pathlib
import sys
import types

import pytest

from optimization import methods, preflight
from optimization.bridges import deploy, hermit, skillopt
from optimization.cli import main
from optimization.loop import Optimizer, StateError, VersionStore
from optimization.proposers import RuleProposer
from optimization.rollout import ScriptedRunner
from optimization.spec import load_spec

FILLER = "Follow the house style and cite the source. " * 8
TOKENS = ("ALPHA", "BETA")


def _project(tmp_path: pathlib.Path, **overrides) -> pathlib.Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "SKILL.md").write_text(
        "# Skill\n\n" + FILLER + "\n", encoding="utf-8")
    rows = []
    for i in range(6):
        rows.append({"id": f"t{i}", "input": f"train prompt {i}",
                     "requires": list(TOKENS)})
    for i in range(6):
        rows.append({"id": f"v{i}", "input": f"val prompt {i}",
                     "requires": list(TOKENS)})
    for i in range(6):
        rows.append({"id": f"h{i}", "input": f"heldout prompt {i}",
                     "requires": list(TOKENS)})
    (tmp_path / "cases.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    settings = {
        "epochs": 2, "batch_size": 6, "val_size": 6, "heldout_size": 6,
        "min_cases": 6, "textual_lr": 0.6, "max_edit_chars": 400,
        "max_tokens": 2000,
    }
    settings.update(overrides)
    (tmp_path / "optimization.toml").write_text(
        "[run]\n"
        f"name = 'e2e'\nepochs = {settings['epochs']}\n"
        f"batch_size = {settings['batch_size']}\n"
        f"val_size = {settings['val_size']}\n"
        f"heldout_size = {settings['heldout_size']}\n"
        f"min_cases = {settings['min_cases']}\n"
        "[artifact]\nkind = 'skill'\npath = 'SKILL.md'\n"
        f"max_tokens = {settings['max_tokens']}\n"
        "[cases]\npath = 'cases.jsonl'\n"
        "[gate]\n"
        f"textual_lr = {settings['textual_lr']}\n"
        f"max_edit_chars = {settings['max_edit_chars']}\n",
        encoding="utf-8",
    )
    return tmp_path / "optimization.toml"


def _optimizer(spec_path: pathlib.Path) -> Optimizer:
    return Optimizer(load_spec(spec_path), ScriptedRunner(), RuleProposer())


def _trained(tmp_path: pathlib.Path) -> Optimizer:
    optimizer = _optimizer(_project(tmp_path))
    optimizer.run()
    return optimizer


def test_loop_accepts_the_improving_edit_and_versions_it(tmp_path):
    report = _trained(tmp_path).report()
    assert report.accepted == 1
    assert report.current_version == "v0001"
    assert report.val["rate"] == 1.0
    assert report.heldout and report.heldout["accepted"] is True


def test_version_chain_verifies_and_tampering_is_detected(tmp_path):
    store = _trained(tmp_path).store
    ok, detail = store.verify()
    assert ok and "chain intact" in detail
    target = store.version_dir("v0001") / "artifact.md"
    target.write_text(target.read_text(encoding="utf-8") + "\ntampered\n",
                      encoding="utf-8")
    ok, detail = store.verify()
    assert not ok and "digest mismatch" in detail


def test_commit_is_idempotent_for_a_replayed_epoch(tmp_path):
    optimizer = _trained(tmp_path)
    store = optimizer.store
    text = store.artifact("v0001")
    again = store.commit(text, epoch=1, parent="v0000", edit=None, gate=None,
                         tokens=1)
    assert again == "v0001"
    assert len(store.entries()) == 2


def test_resume_does_not_fork_history(tmp_path):
    spec_path = _project(tmp_path)
    first = _optimizer(spec_path).run(epochs=1)
    second = _optimizer(spec_path).run()
    assert second.epochs_done == 2
    assert len(second.versions) == len(first.versions)


def test_heldout_split_is_never_read_by_the_loop(tmp_path):
    optimizer = _trained(tmp_path)
    baseline = optimizer.state["baseline_val"]
    val_ids = {case.id for case in optimizer.splits.val}
    heldout_ids = {case.id for case in optimizer.splits.heldout}
    assert set(baseline) == val_ids
    assert not (heldout_ids & set(baseline))


def test_leakage_refuses_to_start(tmp_path):
    (tmp_path / "SKILL.md").write_text("# Skill\n", encoding="utf-8")
    rows = [
        {"id": "a", "input": "same prompt", "split": "train", "requires": []},
        {"id": "b", "input": "Same prompt", "split": "val", "requires": []},
        {"id": "c", "input": "third prompt", "split": "heldout", "requires": []},
    ]
    (tmp_path / "cases.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    (tmp_path / "optimization.toml").write_text(
        "[run]\nname='leak'\nval_size=1\nheldout_size=1\n"
        "[artifact]\npath='SKILL.md'\n[cases]\npath='cases.jsonl'\n",
        encoding="utf-8")
    with pytest.raises(StateError, match="leakage"):
        _optimizer(tmp_path / "optimization.toml")


def test_oversized_artifact_is_refused_before_training(tmp_path):
    spec_path = _project(tmp_path, max_tokens=50)
    with pytest.raises(ValueError, match="max_tokens"):
        _optimizer(spec_path).prepare()


def test_metrics_log_records_the_gate_decision(tmp_path):
    optimizer = _trained(tmp_path)
    lines = [json.loads(line) for line in
             optimizer.metrics_path.read_text(encoding="utf-8").splitlines()]
    accepted = [row for row in lines if row["accepted"]]
    assert accepted and accepted[0]["gate"]["p_value"] < 0.05


# --- run records ------------------------------------------------------------

def test_run_records_follow_the_documented_shape(tmp_path):
    optimizer = _trained(tmp_path)
    work = optimizer.spec.work_dir
    steps = [json.loads(line) for line in
             (work / "steps.jsonl").read_text(encoding="utf-8").splitlines()]
    assert steps and {"accepted", "delta", "version"} <= set(steps[0])
    summary = json.loads((work / "summary.json").read_text(encoding="utf-8"))
    assert summary["stop_reason"] in {"completed", "stopped"}
    assert summary["steps"] == len(steps)
    report = (work / "output" / "report.md").read_text(encoding="utf-8")
    assert "Run report" in report and "Held-out promotion" in report
    assert (work / "checkout" / "ADAPTER.md").is_file()


def test_learning_material_has_no_labels(tmp_path):
    optimizer = _trained(tmp_path)
    directory = optimizer.spec.work_dir / "learning_material" / "step_0001"
    tasks = sorted((directory / "tasks").glob("*.json"))
    assert tasks, "step 1 wrote no learning material"
    assert set(json.loads(tasks[0].read_text(encoding="utf-8"))) == {"id", "prompt"}
    feedback = json.loads((directory / "feedback.json").read_text(encoding="utf-8"))
    assert feedback["items"] and "ok" in feedback["items"][0]


# --- methods ----------------------------------------------------------------

def test_method_registry_lists_the_documented_methods():
    names = methods.method_names()
    assert names == [
        "agent", "continual_harness", "gepa", "harness2",
        "meta_harness_adapter", "mwm", "prompt_only", "reharness", "skillopt",
    ]
    with pytest.raises(methods.MethodError, match="not supported"):
        methods.resolve("ace")


def test_validation_needs_match_the_method_table():
    assert methods.resolve("skillopt").needs_validation is True
    assert methods.resolve("gepa").needs_validation is True
    assert methods.resolve("prompt_only").needs_validation is False
    assert methods.resolve("skillopt").template == "prompt-only@1.0"


def test_skillopt_parameter_rules_are_enforced():
    assert methods.validate_params("skillopt", methods.SKILLOPT_DEFAULTS) == []
    assert methods.validate_params("skillopt", {"gate_metric": "vibes"})
    assert methods.validate_params("skillopt", {"learning_rate": 1,
                                                "min_learning_rate": 4})
    assert methods.validate_params("skillopt", {"unknown": 1})


# --- hermit export ----------------------------------------------------------

def test_hermit_export_matches_the_documented_layout(tmp_path):
    optimizer = _trained(tmp_path)
    out = tmp_path / "hermit"
    written = hermit.export(optimizer.spec, out, store=optimizer.store,
                            report=optimizer.report().as_dict())
    assert hermit.validate(out) == []
    assert written["adapter"].name == "adapter"
    assert (out / "adapter" / "ADAPTER.md").read_text(encoding="utf-8") == \
        optimizer.store.artifact("v0001")
    config = (out / "run.yaml").read_text(encoding="utf-8")
    for section in ("data:", "template:", "evolver:", "agent:", "judge:",
                    "trainer:"):
        assert section in config
    # Hermit's own schema: `task_info` is top level, and `data` has exactly its documented keys.
    assert "\ntask_info: \"task-info\"" in config
    assert "\n  task_info:" not in config
    # Both settings the real `hermit check` needs: a host without bubblewrap still starts, and the acting
    # pipeline can see the endpoint variables it is told to read.
    assert 'enforce: "best_effort"' in config
    assert "\n  env: null" in config
    for split in ("train.txt", "val.txt", "test.txt"):
        assert (out / split).is_file()
    rows = [json.loads(line) for line in
            (out / "tasks.jsonl").read_text(encoding="utf-8").splitlines()]
    assert all(set(row) == {"id", "prompt"} for row in rows)
    assert (out / "task-info" / "README.md").is_file()
    manifest = (out / "adapter" / ".hermit" / "adapter.yaml").read_text(
        encoding="utf-8")
    assert 'contract: "2"' in manifest
    assert "max_bytes:" in manifest


def test_exported_judge_scores_a_task(tmp_path):
    optimizer = _trained(tmp_path)
    out = tmp_path / "hermit"
    hermit.export(optimizer.spec, out, store=optimizer.store,
                  report=optimizer.report().as_dict())

    task_module = types.ModuleType("hermit.task")

    @dataclasses.dataclass
    class RunOutput:
        output: str = ""

    @dataclasses.dataclass
    class JudgeResult:
        score: float
        feedback: str = ""

    task_module.RunOutput = RunOutput
    task_module.JudgeResult = JudgeResult
    package = types.ModuleType("hermit")
    package.task = task_module
    saved = {key: sys.modules.get(key) for key in ("hermit", "hermit.task")}
    sys.modules["hermit"] = package
    sys.modules["hermit.task"] = task_module
    try:
        spec = importlib.util.spec_from_file_location("exported_judge",
                                                      out / "judge.py")
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        good = module.score({"id": "t0"}, RunOutput(output="ALPHA and BETA"))
        bad = module.score({"id": "t0"}, RunOutput(output="nothing"))
    finally:
        for key, value in saved.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value
    assert good.score == 1.0
    assert bad.score == 0.0
    assert "ALPHA" in bad.feedback


def test_hermit_export_rejects_an_unmeasured_tree(tmp_path):
    spec = load_spec(_project(tmp_path))
    with pytest.raises(ValueError, match="no versions"):
        hermit.export(spec, tmp_path / "empty")


# --- skillopt export --------------------------------------------------------

def test_skillopt_export_carries_documented_defaults(tmp_path):
    optimizer = _trained(tmp_path)
    out = tmp_path / "skillopt"
    skillopt.export(optimizer.spec, out, store=optimizer.store,
                    report=optimizer.report().as_dict())
    assert skillopt.validate(out) == []
    assert (out / "best_skill.md").read_text(encoding="utf-8") == \
        (out / "adapter" / "ADAPTER.md").read_text(encoding="utf-8")
    config = (out / "run.yaml").read_text(encoding="utf-8")
    assert "name: \"skillopt\"" in config
    for key in ("minibatch_size: 8", "merge_batch_size: 8",
                "analyst_workers: 16", "gate_metric: \"hard\"",
                "use_meta_skill: true", "promote_final: true"):
        assert key in config
    assert (out / "val.txt").is_file()
    manifest = json.loads((out / "skillopt.json").read_text(encoding="utf-8"))
    assert manifest["evolver"] == "skillopt" and manifest["needs_validation"]


def test_export_enforces_the_token_budget(tmp_path):
    optimizer = _trained(tmp_path)
    tight = load_spec(_project(tmp_path / "tight", max_tokens=60))
    with pytest.raises(ValueError, match="exceeds max_tokens"):
        skillopt.export(tight, tmp_path / "tight-out", store=optimizer.store)


def test_ingest_tolerates_an_unknown_schema(tmp_path):
    result = tmp_path / "result.json"
    result.write_text(json.dumps({"metrics": {"accuracy": 0.87, "extra": 1}}),
                      encoding="utf-8")
    data = hermit.ingest(result)
    assert data["source"] == "hermit" and data["accuracy"] == 0.87


# --- preflight --------------------------------------------------------------

def test_preflight_passes_a_sound_project(tmp_path):
    report = preflight.check(load_spec(_project(tmp_path)))
    assert report.ok, report.problems
    assert report.facts["splits"] == {"train": 6, "val": 6, "heldout": 6}


def test_preflight_flags_a_validation_split_below_the_gate(tmp_path):
    spec = load_spec(_project(tmp_path, val_size=2, min_cases=6))
    report = preflight.check(spec)
    assert not report.ok
    assert any("validation split" in problem for problem in report.problems)


# --- deployment -------------------------------------------------------------

def test_deploy_writes_a_host_workspace(tmp_path):
    optimizer = _trained(tmp_path)
    out = tmp_path / "hermit"
    hermit.export(optimizer.spec, out, store=optimizer.store,
                  report=optimizer.report().as_dict())
    workspace = tmp_path / "ws"
    marker = deploy.deploy(out / "adapter", workspace, "codex")
    assert (workspace / "AGENTS.md").is_file()
    assert (workspace / "hermit" / marker["adapter_id"] / "ADAPTER.md").is_file()
    assert deploy.validate(workspace, "codex") == []
    deploy.deploy(out / "adapter", workspace, "codex")
    text = (workspace / "AGENTS.md").read_text(encoding="utf-8")
    assert text.count(f"hermit:begin {marker['adapter_id']}") == 1


def test_deploy_refuses_an_adapter_inside_the_workspace(tmp_path):
    optimizer = _trained(tmp_path)
    out = tmp_path / "hermit"
    hermit.export(optimizer.spec, out, store=optimizer.store,
                  report=optimizer.report().as_dict())
    with pytest.raises(deploy.DeployError, match="separate from the adapter"):
        deploy.deploy(out / "adapter", out, "codex")


def test_deploy_refuses_a_foreign_nonempty_workspace(tmp_path):
    optimizer = _trained(tmp_path)
    out = tmp_path / "hermit"
    hermit.export(optimizer.spec, out, store=optimizer.store,
                  report=optimizer.report().as_dict())
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "notes.txt").write_text("mine", encoding="utf-8")
    with pytest.raises(deploy.DeployError, match="not empty"):
        deploy.deploy(out / "adapter", workspace, "codex")


# --- CLI --------------------------------------------------------------------

def test_cli_split_check_train_and_verify(tmp_path, capsys):
    spec_path = _project(tmp_path)
    assert main(["split", "--spec", str(spec_path), "--json"]) == 0
    capsys.readouterr()
    assert main(["check", "--spec", str(spec_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert main(["train", "--spec", str(spec_path), "--json"]) == 0
    capsys.readouterr()
    assert main(["verify", "--spec", str(spec_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_cli_lists_methods_and_reports_spec_errors(tmp_path, capsys):
    assert main(["methods", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert {row["name"] for row in rows} == set(methods.method_names())
    assert main(["verify", "--spec", str(tmp_path / "missing.toml")]) == 2
    assert "error:" in capsys.readouterr().err

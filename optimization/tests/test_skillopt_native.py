from __future__ import annotations

import json
import pathlib

import pytest

from optimization import protected, skillopt_native
from optimization.bridges import skillopt
from optimization.cli import main
from optimization.edits import Edit, apply_edit
from optimization.loop import Optimizer
from optimization.proposers import RuleProposer
from optimization.rollout import ScriptedRunner
from optimization.spec import GatePolicy, load_spec

FILLER = "Follow the house style and cite the source. " * 8
TOKENS = ("ALPHA", "BETA")

PROTECTED_DOC = """# Skill

Follow the house style.

<!-- SLOW_UPDATE_START -->
Longitudinal notes.
<!-- SLOW_UPDATE_END -->

<!-- APPENDIX_START -->
Appendix body.
<!-- APPENDIX_END -->
"""


def _project(tmp_path: pathlib.Path) -> pathlib.Path:
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
    (tmp_path / "optimization.toml").write_text(
        "[run]\n"
        "name = 'native'\n"
        "epochs = 2\n"
        "batch_size = 6\n"
        "val_size = 6\n"
        "heldout_size = 6\n"
        "min_cases = 6\n"
        "[artifact]\nkind = 'skill'\npath = 'SKILL.md'\nmax_tokens = 2000\n"
        "[cases]\npath = 'cases.jsonl'\n"
        "[gate]\ntextual_lr = 0.6\nmax_edit_chars = 400\nmax_hunks = 3\n",
        encoding="utf-8",
    )
    return tmp_path / "optimization.toml"


def _trained(tmp_path: pathlib.Path) -> Optimizer:
    optimizer = Optimizer(load_spec(_project(tmp_path)), ScriptedRunner(),
                          RuleProposer())
    optimizer.run()
    return optimizer


def _export(tmp_path: pathlib.Path):
    optimizer = _trained(tmp_path)
    out = tmp_path / "native"
    skillopt.export(optimizer.spec, out, store=optimizer.store,
                    report=optimizer.report().as_dict(), lane="native")
    return out, optimizer


def test_native_config_is_the_six_documented_sections(tmp_path):
    out, _ = _export(tmp_path)
    data = skillopt_native.read_config(out / "skillopt.yaml")
    assert list(data) == list(skillopt_native.SECTION_ORDER)
    assert skillopt_native.validate_config(data, require_runnable=True) == []
    assert skillopt.validate(out) == []


def test_native_config_maps_the_documented_knobs(tmp_path):
    out, optimizer = _export(tmp_path)
    data = skillopt_native.read_config(out / "skillopt.yaml")
    spec = optimizer.spec
    sizes = optimizer.splits.size()
    assert data["train"]["num_epochs"] == spec.epochs
    assert data["train"]["batch_size"] == spec.batch_size
    assert data["train"]["seed"] == spec.seed
    assert data["optimizer"]["learning_rate"] == max(1, spec.gate.max_hunks)
    assert (data["optimizer"]["min_learning_rate"]
            <= data["optimizer"]["learning_rate"])
    assert (data["optimizer"]["slow_update_gate_with_selection"]
            is spec.gate.require_heldout)
    assert data["optimizer"]["slow_update_samples"] == sizes["val"]
    assert data["evaluation"]["sel_env_num"] == sizes["val"]
    assert data["evaluation"]["test_env_num"] == sizes["heldout"]
    assert data["env"]["split_ratio"] == (
        f"{sizes['train']}:{sizes['val']}:{sizes['heldout']}")
    assert data["env"]["split_seed"] == spec.seed


def test_native_export_ships_the_initial_and_accepted_skill(tmp_path):
    out, optimizer = _export(tmp_path)
    rows = optimizer.store.entries()
    initial = out / "skills" / "skill_v0000.md"
    assert initial.read_text(encoding="utf-8") == optimizer.store.artifact(
        str(rows[0]["version"]))
    best = (out / "best_skill.md").read_text(encoding="utf-8")
    assert best == optimizer.store.artifact(str(rows[-1]["version"]))
    manifest = json.loads((out / "skillopt.json").read_text(encoding="utf-8"))
    assert manifest["lane"] == "native"
    assert manifest["initial_skill"] == "skills/skill_v0000.md"
    assert manifest["artifact_sha256"] == __import__("hashlib").sha256(
        best.encode("utf-8")).hexdigest()


def test_native_export_hands_off_the_splits(tmp_path):
    out, optimizer = _export(tmp_path)
    sizes = optimizer.splits.size()
    for name, key in (("train", "train"), ("valid_seen", "val"),
                      ("valid_unseen", "heldout")):
        rows = [json.loads(line) for line in
                (out / "data" / "splits" / f"{name}.jsonl")
                .read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(rows) == sizes[key]
        assert set(rows[0]) == {"id", "split", "question", "requires", "expected"}
        assert rows[0]["split"] == name


def test_native_export_validates_runnable_invariants(tmp_path):
    out, _ = _export(tmp_path)
    readme = (out / "README.md").read_text(encoding="utf-8")
    assert "--split valid_unseen" in readme and "--split valid_seen" in readme
    (out / "data" / "splits" / "valid_unseen.jsonl").unlink()
    assert any("valid_unseen" in problem for problem in skillopt.validate(out))


def test_native_export_detects_a_tampered_skill(tmp_path):
    out, _ = _export(tmp_path)
    skill = out / "best_skill.md"
    skill.write_text(skill.read_text(encoding="utf-8") + "\nHand edit.\n",
                     encoding="utf-8")
    problems = skillopt.validate(out)
    assert any("sha256" in problem for problem in problems)


def test_native_config_refuses_unknown_key_section_and_enum():
    assert skillopt_native.validate_config(
        {"train": {"num_epochs": 2, "epochz": 2}})
    assert skillopt_native.validate_config({"optimiser": {"learning_rate": 4}})
    problems = skillopt_native.validate_config(
        {"optimizer": {"learning_rate": 4, "lr_scheduler": "vibes"}})
    assert any("lr_scheduler" in problem for problem in problems)
    assert skillopt_native.validate_config(
        {"env": {"split_mode": "folder"}})
    assert skillopt_native.validate_config(
        {"optimizer": {"learning_rate": 1, "min_learning_rate": 4}})


def test_native_config_requires_its_run_fields():
    problems = skillopt_native.validate_config({"env": {}},
                                               require_runnable=True)
    assert any("[env].name" in problem for problem in problems)
    assert any("[env].skill_init" in problem for problem in problems)
    assert any("[env].data_path" in problem for problem in problems)


def test_native_render_roundtrips(tmp_path):
    out, _ = _export(tmp_path)
    text = (out / "skillopt.yaml").read_text(encoding="utf-8")
    assert skillopt_native.render(skillopt_native.parse(text)) == text


def test_protected_regions_are_located_and_checked():
    found = protected.regions(PROTECTED_DOC)
    assert [region.name for region in found] == ["SLOW_UPDATE", "APPENDIX"]
    assert found[0].body.strip() == "Longitudinal notes."
    assert found[1].body.strip() == "Appendix body."
    assert protected.bodies(PROTECTED_DOC)["APPENDIX"] == ["\nAppendix body.\n"]
    assert protected.validate(PROTECTED_DOC) == []


def test_unbalanced_markers_are_refused():
    broken = "<!-- APPENDIX_START -->\nbody only\n"
    assert protected.validate(broken)
    with pytest.raises(protected.ProtectedRegionError, match="unclosed"):
        protected.regions(broken)


def test_edit_inside_a_protected_region_is_refused():
    policy = GatePolicy(max_edit_chars=10 ** 6, textual_lr=1.0, max_hunks=99)
    with pytest.raises(protected.ProtectedRegionError, match="APPENDIX"):
        apply_edit(PROTECTED_DOC, Edit("replace", "Appendix body.", "Changed."),
                   policy)


def test_edit_that_removes_a_marker_is_refused():
    policy = GatePolicy(max_edit_chars=10 ** 6, textual_lr=1.0, max_hunks=99)
    with pytest.raises(protected.ProtectedRegionError):
        apply_edit(PROTECTED_DOC, Edit("delete", "<!-- APPENDIX_END -->"),
                   policy)


def test_edit_outside_a_protected_region_is_allowed():
    policy = GatePolicy(max_edit_chars=10 ** 6, textual_lr=1.0, max_hunks=99)
    result = apply_edit(
        PROTECTED_DOC,
        Edit("add", "<!-- APPENDIX_END -->", "\nClosing note.\n"),
        policy,
    )
    assert "Closing note." in result
    assert protected.bodies(result) == protected.bodies(PROTECTED_DOC)


def test_cli_lane_is_explicit(tmp_path, capsys):
    optimizer = _trained(tmp_path)
    out = tmp_path / "cli-native"
    assert main(["export", "--spec", str(optimizer.spec.spec_path),
                 "--target", "skillopt", "--lane", "native",
                 "--out", str(out)]) == 0
    assert (out / "skillopt.yaml").is_file()
    capsys.readouterr()
    assert main(["export", "--spec", str(optimizer.spec.spec_path),
                 "--target", "hermit", "--lane", "native",
                 "--out", str(tmp_path / "bad")]) == 2
    assert "only available" in capsys.readouterr().err

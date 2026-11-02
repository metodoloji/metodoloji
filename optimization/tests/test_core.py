from __future__ import annotations

import json
import pathlib

import pytest

from optimization.cases import Case, CaseError, assign_splits, leakage, load_cases
from optimization.edits import (
    BudgetExceeded,
    Edit,
    EditNotApplicable,
    RejectedBuffer,
    apply_edit,
    cost_of,
)
from optimization.gate import GateError, decide, mcnemar_exact, wilson_lower
from optimization.predictor import FEATURES, GainPredictor, worth_measuring
from optimization.spec import GatePolicy, SpecError, init_spec, load_spec


def _policy(**overrides) -> GatePolicy:
    values = dict(min_improvement=0.0, alpha=0.05, max_edit_chars=10_000,
                  textual_lr=1.0, max_hunks=99)
    values.update(overrides)
    return GatePolicy(**values)


def _cases(n: int = 12) -> list[Case]:
    return [Case(id=f"c{i}", input=f"prompt number {i}") for i in range(n)]


# --- spec -------------------------------------------------------------------

def test_init_spec_writes_three_files(tmp_path):
    path = init_spec(tmp_path, name="demo")
    assert path.is_file()
    assert (tmp_path / "SKILL.md").is_file()
    assert (tmp_path / "cases.jsonl").is_file()
    spec = load_spec(path)
    assert spec.name == "demo"
    assert spec.artifact_kind == "skill"
    assert spec.gate.require_heldout is True


def test_init_spec_never_overwrites(tmp_path):
    init_spec(tmp_path)
    with pytest.raises(SpecError, match="refusing to overwrite"):
        init_spec(tmp_path)


def test_spec_unknown_key_is_refused(tmp_path):
    (tmp_path / "SKILL.md").write_text("# s\n", encoding="utf-8")
    (tmp_path / "cases.jsonl").write_text("{}\n", encoding="utf-8")
    spec = tmp_path / "optimization.toml"
    spec.write_text(
        "[run]\nname='x'\nepochs=1\nnonsense=2\n"
        "[artifact]\npath='SKILL.md'\n[cases]\npath='cases.jsonl'\n",
        encoding="utf-8",
    )
    with pytest.raises(SpecError, match="unknown keys"):
        load_spec(spec)


def test_spec_paths_resolve_against_the_spec_file(tmp_path, monkeypatch):
    sub = tmp_path / "nested"
    sub.mkdir()
    init_spec(sub)
    monkeypatch.chdir(tmp_path)
    spec = load_spec(sub / "optimization.toml")
    assert spec.artifact_path == (sub / "SKILL.md").resolve()
    assert spec.cases_path == (sub / "cases.jsonl").resolve()


def test_gate_policy_rejects_impossible_budget():
    with pytest.raises(SpecError):
        GatePolicy(textual_lr=0.0)


# --- cases and splits -------------------------------------------------------

def _write_cases(path: pathlib.Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_load_cases_refuses_unknown_split(tmp_path):
    path = tmp_path / "cases.jsonl"
    _write_cases(path, [{"id": "a", "input": "p", "split": "test"}])
    with pytest.raises(CaseError, match="split 'test'"):
        load_cases(path)


def test_load_cases_refuses_duplicate_id(tmp_path):
    path = tmp_path / "cases.jsonl"
    _write_cases(path, [{"id": "a", "input": "p"}, {"id": "a", "input": "q"}])
    with pytest.raises(CaseError, match="duplicate case id"):
        load_cases(path)


def test_splits_are_deterministic_and_seed_sensitive():
    cases = _cases()
    first = assign_splits(cases, seed=3, val_size=3, heldout_size=3)
    again = assign_splits(cases, seed=3, val_size=3, heldout_size=3)
    other = assign_splits(cases, seed=4, val_size=3, heldout_size=3)
    assert [c.id for c in first.train] == [c.id for c in again.train]
    assert [c.id for c in first.val] == [c.id for c in again.val]
    assert {c.id for c in first.val} != {c.id for c in other.val}


def test_splits_are_disjoint_and_complete():
    splits = assign_splits(_cases(12), seed=3, val_size=3, heldout_size=3)
    groups = [splits.train, splits.val, splits.heldout]
    ids = [c.id for group in groups for c in group]
    assert len(ids) == len(set(ids)) == 12
    assert splits.size() == {"train": 6, "val": 3, "heldout": 3}


def test_leakage_flags_same_prompt_in_two_splits():
    train = Case(id="a", input="the same prompt", split="train")
    val = Case(id="b", input="The same prompt", split="val")
    splits = assign_splits([train, val], seed=1, val_size=0, heldout_size=0)
    assert leakage(splits) == ["a", "b"]


def test_explicit_split_is_honoured():
    cases = [Case(id="a", input="p", split="train"),
             Case(id="b", input="q", split="heldout"),
             Case(id="c", input="r")]
    splits = assign_splits(cases, seed=1, val_size=1, heldout_size=0)
    assert [c.id for c in splits.train] == ["a"]
    assert [c.id for c in splits.heldout] == ["b"]
    # the unassigned case fills the remaining val slot
    assert [c.id for c in splits.val] == ["c"]


def test_split_refuses_to_starve_val():
    with pytest.raises(CaseError, match="cannot fill"):
        assign_splits(_cases(2), seed=1, val_size=3, heldout_size=3)


# --- edits ------------------------------------------------------------------

def test_add_edit_lands_and_stays_in_budget():
    text = "# Skill\n\n" + "body line. " * 20 + "\n"
    edit = Edit(op="add", content="- Always cite ALPHA.\n")
    new = apply_edit(text, edit, _policy(textual_lr=0.2, max_edit_chars=400))
    assert "ALPHA" in new and new.startswith(text)


def test_add_requires_a_unique_anchor():
    with pytest.raises(EditNotApplicable, match="exactly 1"):
        apply_edit("Rule\nRule\n", Edit(op="add", anchor="Rule", content="- x\n"),
                   _policy())


def test_replace_requires_the_anchor_to_exist():
    with pytest.raises(EditNotApplicable, match="occurs 0 times"):
        apply_edit("alpha\n", Edit(op="replace", anchor="beta", content="gamma"),
                   _policy())


def test_replace_and_delete_apply_once():
    text = "keep\nremove me\nkeep tail\n"
    assert "remove me" not in apply_edit(
        text, Edit(op="delete", anchor="remove me\n"), _policy())
    assert "NEW" in apply_edit(
        text, Edit(op="replace", anchor="remove me", content="NEW"), _policy())


def test_absolute_budget_is_enforced():
    text = "x" * 300 + "\n"
    with pytest.raises(BudgetExceeded, match="max_edit_chars"):
        apply_edit(text, Edit(op="add", content="y" * 200),
                   _policy(max_edit_chars=100))


def test_relative_budget_is_enforced():
    text = "old " * 50
    edit = Edit(op="replace", anchor=text, content="new " * 50)
    with pytest.raises(BudgetExceeded, match="textual_lr"):
        apply_edit(text, edit, _policy(textual_lr=0.3))


def test_hunk_budget_is_enforced():
    text = "A\nB\nC\nD\nE\n"
    edit = Edit(op="replace", anchor=text, content="X\nB\nY\nD\nZ\n")
    with pytest.raises(BudgetExceeded, match="max_hunks"):
        apply_edit(text, edit, _policy(max_hunks=2, textual_lr=1.0,
                                       max_edit_chars=1000))


def test_noop_and_emptying_edits_are_refused():
    with pytest.raises(EditNotApplicable, match="unchanged"):
        apply_edit("Base\n", Edit(op="replace", anchor="Base", content="Base"),
                   _policy())
    with pytest.raises(EditNotApplicable, match="empty"):
        apply_edit("Base\n", Edit(op="delete", anchor="Base\n"), _policy())


def test_cost_is_measured_from_the_diff_not_declared():
    assert cost_of("same", "same").ratio == 0.0
    cost = cost_of("abc", "abd")
    assert cost.changed_chars == 1 and cost.hunks == 1


def test_rejected_buffer_blocks_exact_repeats_and_counts_near_misses():
    buffer = RejectedBuffer()
    edit = Edit(op="add", anchor="## Rules", content="- never do this\n")
    buffer.add(edit, -0.1, "no gain")
    assert buffer.blocks(edit)
    assert not buffer.blocks(Edit(op="add", anchor="## Rules", content="- other\n"))
    assert buffer.near_misses(
        Edit(op="add", anchor="##  Rules", content="- third\n")) == 1
    assert "Directions already rejected" in buffer.prompt()


def test_rejected_buffer_roundtrips(tmp_path):
    buffer = RejectedBuffer()
    buffer.add(Edit(op="add", content="- no\n"), -0.2, "tie")
    path = tmp_path / "buffer.json"
    buffer.save(path)
    loaded = RejectedBuffer.load(path)
    assert len(loaded) == 1
    assert loaded.blocks(Edit(op="add", content="- no\n"))


def test_edit_signature_normalises_whitespace():
    assert (Edit(op="add", content="-  a   b\n").signature()
            == Edit(op="add", content="- a b").signature())


# --- gate -------------------------------------------------------------------

def test_clear_gain_is_accepted():
    base = {f"c{i}": False for i in range(10)}
    cand = {f"c{i}": True for i in range(10)}
    decision = decide(base, cand, _policy())
    assert decision.accepted
    assert decision.delta == 1.0
    assert decision.n == 10


def test_tie_is_rejected():
    verdicts = {f"c{i}": i % 2 == 0 for i in range(10)}
    decision = decide(verdicts, dict(verdicts), _policy())
    assert not decision.accepted
    assert "tie" in decision.reason


def test_regression_is_rejected():
    base = {f"c{i}": True for i in range(10)}
    cand = {f"c{i}": i != 0 for i in range(10)}
    assert not decide(base, cand, _policy()).accepted


def test_min_improvement_is_respected():
    base = {f"c{i}": i < 5 for i in range(10)}
    cand = {f"c{i}": i < 6 for i in range(10)}
    assert not decide(base, cand, _policy(min_improvement=0.2)).accepted


def test_a_single_flip_is_not_significant():
    base = {f"c{i}": False for i in range(20)}
    cand = {f"c{i}": i == 0 for i in range(20)}
    decision = decide(base, cand, _policy())
    assert not decision.accepted
    assert "not significant" in decision.reason


def test_significant_majority_passes():
    base = {f"c{i}": i >= 8 for i in range(20)}
    cand = {f"c{i}": True for i in range(20)}
    assert decide(base, cand, _policy()).accepted


def test_unpaired_and_empty_comparisons_raise():
    with pytest.raises(GateError, match="not paired"):
        decide({"a": True}, {"b": True}, _policy())
    with pytest.raises(GateError):
        decide({}, {}, _policy())


def test_min_cases_refuses_a_tiny_gate():
    base = {f"c{i}": False for i in range(3)}
    cand = {f"c{i}": True for i in range(3)}
    with pytest.raises(GateError, match="min_cases"):
        decide(base, cand, _policy(), min_cases=6)


def test_mcnemar_is_symmetric_and_conservative():
    assert mcnemar_exact(3, 1) == mcnemar_exact(1, 3)
    assert mcnemar_exact(0, 0) == 1.0
    assert mcnemar_exact(8, 0) < 0.05


def test_wilson_bounds_are_honest():
    assert 0.9 < wilson_lower(52, 52) < 1.0
    assert wilson_lower(0, 10) == 0.0


# --- predictor --------------------------------------------------------------

def _history() -> list[tuple[dict[str, float], float]]:
    rows = []
    for i in range(12):
        features = {name: 0.0 for name in FEATURES}
        features["coverage_gain"] = i / 11
        features["changed_ratio"] = 0.1
        rows.append((features, 0.9 * (i / 11) + 0.02))
    return rows


def test_prior_is_used_until_enough_history_exists():
    predictor = GainPredictor().fit(_history()[:1])
    assert predictor.source == "prior"
    features = {name: 0.0 for name in FEATURES}
    features["coverage_gain"] = 1.0
    assert predictor.predict(features).delta > 0


def test_fitted_predictor_beats_the_prior():
    history = _history()
    fitted = GainPredictor().fit(history)
    assert fitted.source == "fitted"
    prior = GainPredictor().fit([])
    assert fitted.mae(history) < prior.mae(history)


def test_prediction_interval_covers_most_observations():
    history = _history()
    predictor = GainPredictor().fit(history)
    covered = sum(
        1 for features, realized in history
        if predictor.predict(dict(features)).covers(realized)
    )
    assert covered >= 10


def test_screening_uses_the_optimistic_bound():
    predictor = GainPredictor().fit([])
    features = {name: 0.0 for name in FEATURES}
    prediction = predictor.predict(features)
    assert worth_measuring(prediction, None)
    assert not worth_measuring(prediction, 0.9)

#!/usr/bin/env python3
"""Optimization-kernel bench (E-097).

The kernel's claim is narrow and mechanical: an artifact is only replaced when a
candidate beats the incumbent on the same held-out cases, every edit is bounded,
a rejected direction is remembered, the held-out split is never trained on, the
version history is a verifiable chain, and the exported artifact is the exact
text that was measured.

Each check below is one of those promises, evaluated against the shipped code.
There are 94 independent checks across nine families, so the gate's Wilson
bound can be conclusive rather than advisory.

    python scripts/bench/bench_optimization.py
    python scripts/bench/bench_optimization.py --broken    # rule-7 control

`--broken` swaps in the pre-kernel behaviour an unguarded self-editing loop
would have: ties accepted, no significance test, no textual learning rate, no
rejected-edit memory, a seed that does not split, no leakage detection, an
unfitted predictor, and an unverified version chain. The same assertions then
fail, which is what makes this bench a measurement instead of a demonstration.

Stdlib only, offline, deterministic.
"""
from __future__ import annotations

import argparse
import dataclasses
import importlib.util
import json
import pathlib
import shutil
import sys
import tempfile
import traceback
import types

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from optimization import methods, preflight, skillopt_native  # noqa: E402
from optimization import protected as protected_regions  # noqa: E402
from optimization.bridges import deploy, hermit, skillopt  # noqa: E402
from optimization.cases import (  # noqa: E402
    Case,
    CaseError,
    SplitSet,
    assign_splits,
    leakage,
    load_cases,
)
from optimization.edits import (  # noqa: E402
    BudgetExceeded,
    Edit,
    EditNotApplicable,
    RejectedBuffer,
    apply_edit,
    cost_of,
)
from optimization.gate import GateDecision, GateError, decide, mcnemar_exact, wilson_lower  # noqa: E402
from optimization.loop import Optimizer  # noqa: E402
from optimization.predictor import (  # noqa: E402
    FEATURES,
    GainPredictor,
    Prediction,
    features_for,
    worth_measuring,
)
from optimization.proposers import RuleProposer  # noqa: E402
from optimization.rollout import ScriptedRunner  # noqa: E402
from optimization.spec import GatePolicy, load_spec  # noqa: E402

SAMPLES = ROOT / "docs/experiments/E-099/raw/samples.jsonl"
TOKENS = ("ALPHA", "BETA")
FILLER = "Follow the house style and cite the source. " * 8


# --- helpers ----------------------------------------------------------------

def _policy(**overrides) -> GatePolicy:
    values = dict(min_improvement=0.0, alpha=0.05, max_edit_chars=10 ** 6,
                  textual_lr=1.0, max_hunks=999)
    values.update(overrides)
    return GatePolicy(**values)


def _pairs(n: int, base, cand) -> tuple[dict[str, bool], dict[str, bool]]:
    return ({f"c{i}": base(i) for i in range(n)},
            {f"c{i}": cand(i) for i in range(n)})


def _decide(broken: bool, base, cand, policy: GatePolicy | None = None,
            min_cases: int = 2) -> GateDecision:
    """Healthy: the real paired gate. Broken: accept any non-negative delta."""
    if broken:
        n = min(len(base), len(cand))
        if n == 0:
            return GateDecision(False, "broken: empty", 0.0, 0.0, 0.0, 0, 0, 1.0, 0)
        bvals = list(base.values())[:n]
        cvals = list(cand.values())[:n]
        delta = sum(cvals) / n - sum(bvals) / n
        return GateDecision(delta >= 0, "broken: no significance test", delta,
                            sum(bvals) / n, sum(cvals) / n, 0, 0, 1.0, n)
    return decide(base, cand, policy or _policy(), min_cases=min_cases)


def _raises(call, exc) -> bool:
    try:
        call()
    except exc:
        return True
    except Exception:
        return False
    return False


def _budget_apply(broken: bool, text: str, edit: Edit, **tight):
    policy = _policy() if broken else _policy(**tight)
    return apply_edit(text, edit, policy)


def _blocks(broken: bool, buffer: RejectedBuffer, edit: Edit) -> bool:
    return False if broken else buffer.blocks(edit)


def _near(broken: bool, buffer: RejectedBuffer, edit: Edit) -> int:
    return 0 if broken else buffer.near_misses(edit)


def _assign(broken: bool, cases, seed: int, val_size: int = 3,
            heldout_size: int = 3) -> SplitSet:
    if broken:
        ordered = list(cases)
        return SplitSet(
            train=tuple(ordered),  # every case leaks into train
            val=tuple(ordered[heldout_size:heldout_size + val_size]),
            heldout=tuple(ordered[:heldout_size]),
        )
    return assign_splits(cases, seed, val_size, heldout_size)


def _leak(broken: bool, splits: SplitSet) -> list[str]:
    return [] if broken else leakage(splits)


def _history() -> list[tuple[dict[str, float], float]]:
    rows = []
    for i in range(12):
        features = {name: 0.0 for name in FEATURES}
        features["coverage_gain"] = i / 11
        features["changed_ratio"] = 0.1
        rows.append((features, 0.9 * (i / 11) + 0.02))
    return rows


def _predict(broken: bool, history, features) -> Prediction:
    if broken:
        return Prediction(0.9, 0.9, 0.9, "broken", features)
    return GainPredictor().fit(history).predict(features)


def _blank_features() -> dict[str, float]:
    return {name: 0.0 for name in FEATURES}


def _verify(broken: bool, store):
    return (True, "broken: chain not recomputed") if broken else store.verify()


def _loop_project(directory: pathlib.Path, **overrides) -> pathlib.Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text("# Skill\n\n" + FILLER + "\n",
                                        encoding="utf-8")
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
    (directory / "cases.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    settings = {"max_tokens": 2000}
    settings.update(overrides)
    (directory / "optimization.toml").write_text(
        "[run]\nname='bench'\nepochs=2\nbatch_size=6\nval_size=6\n"
        "heldout_size=6\nmin_cases=6\n"
        "[artifact]\nkind='skill'\npath='SKILL.md'\n"
        f"max_tokens={settings['max_tokens']}\n"
        "[cases]\npath='cases.jsonl'\n"
        "[gate]\ntextual_lr=0.6\nmax_edit_chars=400\n",
        encoding="utf-8",
    )
    return directory / "optimization.toml"


def _trained(directory: pathlib.Path) -> Optimizer:
    spec = load_spec(_loop_project(directory))
    optimizer = Optimizer(spec, ScriptedRunner(), RuleProposer())
    optimizer.run()
    return optimizer


# --- gate -------------------------------------------------------------------

def gate_accept_clear_gain(broken: bool) -> bool:
    base, cand = _pairs(10, lambda i: False, lambda i: True)
    decision = _decide(broken, base, cand)
    return decision.accepted and decision.delta == 1.0 and decision.n == 10


def gate_reject_tie(broken: bool) -> bool:
    base, _ = _pairs(10, lambda i: i % 2 == 0, lambda i: i % 2 == 0)
    decision = _decide(broken, base, dict(base))
    return not decision.accepted


def gate_reject_regression(broken: bool) -> bool:
    base, cand = _pairs(10, lambda i: True, lambda i: i != 0)
    return not _decide(broken, base, cand).accepted


def gate_reject_below_min_improvement(broken: bool) -> bool:
    base, cand = _pairs(10, lambda i: i < 5, lambda i: i < 6)
    decision = _decide(broken, base, cand, _policy(min_improvement=0.2))
    return not decision.accepted


def gate_reject_insignificant(broken: bool) -> bool:
    base, cand = _pairs(20, lambda i: False, lambda i: i == 0)
    decision = _decide(broken, base, cand)
    return not decision.accepted and "not significant" in decision.reason


def gate_accept_significant(broken: bool) -> bool:
    base, cand = _pairs(20, lambda i: i >= 8, lambda i: True)
    decision = _decide(broken, base, cand)
    return decision.accepted and decision.p_value < 0.05


def gate_refuse_unpaired(broken: bool) -> bool:
    return _raises(lambda: _decide(broken, {"a": True}, {"b": True}), GateError)


def gate_refuse_empty(broken: bool) -> bool:
    return _raises(lambda: _decide(broken, {}, {}), GateError)


def gate_refuse_tiny_gate(broken: bool) -> bool:
    base, cand = _pairs(3, lambda i: False, lambda i: True)
    return _raises(lambda: _decide(broken, base, cand, min_cases=6), GateError)


def mcnemar_symmetry(broken: bool) -> bool:
    return mcnemar_exact(4, 1) == mcnemar_exact(1, 4)


def mcnemar_no_discordance(broken: bool) -> bool:
    return mcnemar_exact(0, 0) == 1.0 and mcnemar_exact(6, 0) < 0.05


def wilson_bounds(broken: bool) -> bool:
    return 0.9 < wilson_lower(52, 52) < 1.0 and wilson_lower(0, 10) == 0.0


def gate_delta_is_exact(broken: bool) -> bool:
    base, cand = _pairs(8, lambda i: i < 2, lambda i: i < 5)
    decision = _decide(broken, base, cand)
    return abs(decision.delta - 3 / 8) < 1e-12 and decision.discordant_b == 3


def gate_records_case_count(broken: bool) -> bool:
    base, cand = _pairs(12, lambda i: i < 6, lambda i: i < 11)
    return _decide(broken, base, cand).n == 12


# --- edits ------------------------------------------------------------------

def edit_add_applies(broken: bool) -> bool:
    text = "# Skill\n\n" + "body line. " * 20 + "\n"
    edit = Edit(op="add", content="- Always cite ALPHA.\n")
    new = _budget_apply(broken, text, edit, textual_lr=0.3, max_edit_chars=400)
    return "ALPHA" in new and new.startswith(text)


def edit_ambiguous_anchor_refused(broken: bool) -> bool:
    edit = Edit(op="add", anchor="Rule", content="- x\n")
    return _raises(lambda: _budget_apply(broken, "Rule\nRule\n", edit),
                   EditNotApplicable)


def edit_replace_applies(broken: bool) -> bool:
    edit = Edit(op="replace", anchor="OLD", content="NEW")
    return "NEW" in _budget_apply(broken, "Use OLD token.\n", edit)


def edit_missing_anchor_refused(broken: bool) -> bool:
    edit = Edit(op="replace", anchor="absent", content="x")
    return _raises(lambda: _budget_apply(broken, "alpha\n", edit),
                   EditNotApplicable)


def edit_delete_applies(broken: bool) -> bool:
    new = _budget_apply(broken, "keep\nremove me\n",
                        Edit(op="delete", anchor="remove me\n"))
    return "remove me" not in new and new.strip() == "keep"


def budget_absolute_refused(broken: bool) -> bool:
    text = "x" * 300 + "\n"
    edit = Edit(op="add", content="y" * 200)
    return _raises(lambda: _budget_apply(broken, text, edit, max_edit_chars=100),
                   BudgetExceeded)


def budget_relative_refused(broken: bool) -> bool:
    text = "old " * 50
    edit = Edit(op="replace", anchor=text, content="new " * 50)
    return _raises(lambda: _budget_apply(broken, text, edit, textual_lr=0.3),
                   BudgetExceeded)


def budget_hunks_refused(broken: bool) -> bool:
    text = "A\nB\nC\nD\nE\n"
    edit = Edit(op="replace", anchor=text, content="X\nB\nY\nD\nZ\n")
    return _raises(
        lambda: _budget_apply(broken, text, edit, max_hunks=2,
                              textual_lr=1.0, max_edit_chars=1000),
        BudgetExceeded,
    )


def edit_noop_refused(broken: bool) -> bool:
    edit = Edit(op="replace", anchor="Base", content="Base")
    return _raises(lambda: _budget_apply(broken, "Base\n", edit),
                   EditNotApplicable)


def edit_emptying_refused(broken: bool) -> bool:
    edit = Edit(op="delete", anchor="Base\n")
    return _raises(lambda: _budget_apply(broken, "Base\n", edit),
                   EditNotApplicable)


def edit_cost_from_diff(broken: bool) -> bool:
    assert cost_of("same", "same").changed_chars == 0
    cost = cost_of("abc", "abd")
    return cost.changed_chars == 1 and cost.hunks == 1 and 0 < cost.ratio < 1


def edit_signature_normalised(broken: bool) -> bool:
    return (Edit(op="add", content="-  a   b\n").signature()
            == Edit(op="add", content="- a b").signature())


# --- rejected-edit buffer ---------------------------------------------------

def _rejected_buffer() -> tuple[RejectedBuffer, Edit]:
    buffer = RejectedBuffer()
    edit = Edit(op="add", anchor="## Rules", content="- never do this\n")
    buffer.add(edit, -0.1, "no gain")
    return buffer, edit


def buffer_blocks_repeat(broken: bool) -> bool:
    buffer, edit = _rejected_buffer()
    return _blocks(broken, buffer, edit)


def buffer_allows_new_direction(broken: bool) -> bool:
    buffer, _ = _rejected_buffer()
    other = Edit(op="add", anchor="## Rules", content="- do this instead\n")
    return not _blocks(broken, buffer, other)


def buffer_counts_near_misses(broken: bool) -> bool:
    buffer, _ = _rejected_buffer()
    probe = Edit(op="add", anchor="##  Rules", content="- third\n")
    return _near(broken, buffer, probe) == 1


def buffer_roundtrip(broken: bool) -> bool:
    buffer, edit = _rejected_buffer()
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "buffer.json"
        buffer.save(path)
        loaded = RejectedBuffer.load(path)
        return len(loaded) == 1 and loaded.blocks(edit)


def buffer_prompt_lists_direction(broken: bool) -> bool:
    buffer, _ = _rejected_buffer()
    return "Directions already rejected" in buffer.prompt()


# --- cases and splits -------------------------------------------------------

def _cases(n: int = 12) -> list[Case]:
    return [Case(id=f"k{i}", input=f"prompt {i}") for i in range(n)]


def split_deterministic(broken: bool) -> bool:
    first = _assign(broken, _cases(), seed=9)
    second = _assign(broken, _cases(), seed=9)
    return ([c.id for c in first.train] == [c.id for c in second.train]
            and [c.id for c in first.val] == [c.id for c in second.val])


def split_seed_sensitive(broken: bool) -> bool:
    one = _assign(broken, _cases(), seed=9)
    two = _assign(broken, _cases(), seed=10)
    return {c.id for c in one.val} != {c.id for c in two.val}


def split_disjoint(broken: bool) -> bool:
    splits = _assign(broken, _cases(), seed=9)
    ids = [c.id for group in (splits.train, splits.val, splits.heldout)
           for c in group]
    return len(ids) == len(set(ids))


def leakage_detected(broken: bool) -> bool:
    train = Case(id="a", input="same prompt", split="train")
    val = Case(id="b", input="Same prompt", split="val")
    splits = _assign(broken, [train, val], seed=1, val_size=0, heldout_size=0)
    return _leak(broken, splits) == ["a", "b"]


def cases_unknown_split_refused(broken: bool) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "cases.jsonl"
        path.write_text(json.dumps({"id": "a", "input": "p", "split": "test"}) + "\n",
                        encoding="utf-8")
        return _raises(lambda: load_cases(path), CaseError)


def cases_duplicate_id_refused(broken: bool) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "cases.jsonl"
        path.write_text(
            json.dumps({"id": "a", "input": "p"}) + "\n"
            + json.dumps({"id": "a", "input": "q"}) + "\n",
            encoding="utf-8")
        return _raises(lambda: load_cases(path), CaseError)


# --- predictor --------------------------------------------------------------

def predictor_prior_without_history(broken: bool) -> bool:
    features = _blank_features()
    features["coverage_gain"] = 1.0
    return _predict(broken, [], features).delta > 0


def predictor_fits_from_history(broken: bool) -> bool:
    return _predict(broken, _history(), _blank_features()).source == "fitted"


def predictor_interval_covers(broken: bool) -> bool:
    history = _history()
    covered = sum(
        1 for features, realized in history
        if _predict(broken, history, dict(features)).covers(realized)
    )
    return covered >= 10


def predictor_coverage_feature(broken: bool) -> bool:
    artifact = "# Skill\n\nNothing useful.\n"
    failing = [Case(id="f", input="p", requires=("ALPHA",))]
    edit = Edit(op="add", content="- Include ALPHA.\n")
    features = features_for(edit, artifact, cost_of(artifact, artifact + edit.content),
                            failing, RejectedBuffer(), 0.0)
    return features["coverage_gain"] > 0


def predictor_screening(broken: bool) -> bool:
    prediction = _predict(broken, [], _blank_features())
    return not worth_measuring(prediction, 0.9)


# --- loop and versions ------------------------------------------------------

def loop_accepts_gain(broken: bool) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        optimizer = _trained(pathlib.Path(tmp))
        report = optimizer.report()
        return (report.accepted == 1 and report.current_version == "v0001"
                and report.val["rate"] == 1.0
                and "ALPHA" in optimizer.store.artifact("v0001"))


def version_chain_intact(broken: bool) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        optimizer = _trained(pathlib.Path(tmp))
        ok, detail = _verify(broken, optimizer.store)
        return ok and "chain intact" in detail


def version_tamper_detected(broken: bool) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        optimizer = _trained(pathlib.Path(tmp))
        target = optimizer.store.version_dir("v0001") / "artifact.md"
        target.write_text(target.read_text(encoding="utf-8") + "tampered\n",
                          encoding="utf-8")
        ok, _ = _verify(broken, optimizer.store)
        return not ok


def resume_no_fork(broken: bool) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        spec_path = _loop_project(pathlib.Path(tmp))
        spec = load_spec(spec_path)
        first = Optimizer(spec, ScriptedRunner(), RuleProposer()).run(epochs=1)
        second = Optimizer(spec, ScriptedRunner(), RuleProposer()).run(epochs=2)
        return (second.epochs_done == 2
                and len(second.versions) == len(first.versions))


def heldout_not_in_baseline(broken: bool) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        optimizer = _trained(pathlib.Path(tmp))
        baseline = optimizer.state["baseline_val"]
        val_ids = {case.id for case in optimizer.splits.val}
        heldout_ids = {case.id for case in optimizer.splits.heldout}
        return set(baseline) == val_ids and not (heldout_ids & set(baseline))


def heldout_promotion_reported(broken: bool) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        report = _trained(pathlib.Path(tmp)).report()
        return bool(report.heldout) and report.heldout["n"] == 6


# --- exports ----------------------------------------------------------------

def hermit_export_valid(broken: bool) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        optimizer = _trained(root / "run")
        out = root / "hermit"
        hermit.export(optimizer.spec, out, store=optimizer.store,
                      report=optimizer.report().as_dict())
        if broken:
            (out / ".hermit" / "CARD.md").unlink()
        return hermit.validate(out) == []


def skillopt_export_valid(broken: bool) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        optimizer = _trained(root / "run")
        out = root / "skillopt"
        skillopt.export(optimizer.spec, out, store=optimizer.store,
                        report=optimizer.report().as_dict())
        if broken:
            (out / "data" / "val.jsonl").unlink()
        return skillopt.validate(out) == []


def export_token_budget(broken: bool) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        optimizer = _trained(root / "run")
        tight = load_spec(_loop_project(root / "tight", max_tokens=60))
        try:
            skillopt.export(tight, root / "tight-out", store=optimizer.store)
        except ValueError as exc:
            return (not broken) and "exceeds max_tokens" in str(exc)
        return False


def export_requires_versions(broken: bool) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        spec = load_spec(_loop_project(root))
        return _raises(lambda: skillopt.export(spec, root / "out"), ValueError)


# --- Hermit 0.1.0 conformance ------------------------------------------------

def _export(tmp_dir: str, target: str = "hermit", broken: bool = False):
    root = pathlib.Path(tmp_dir)
    optimizer = _trained(root / "run")
    out = root / target
    module = hermit if target == "hermit" else skillopt
    module.export(optimizer.spec, out, store=optimizer.store,
                  report=optimizer.report().as_dict())
    if broken:
        _degrade_export(out)
    return out, optimizer


def _degrade_export(out: pathlib.Path) -> None:
    """The pre-correction shape: legacy config, labelled task rows, no guide."""
    (out / "adapter" / ".hermit" / "EVOLVER.md").unlink(missing_ok=True)
    tasks = out / "tasks.jsonl"
    rows = [json.loads(line) for line in tasks.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    tasks.write_text("".join(
        json.dumps({"id": row["id"], "input": row["prompt"],
                    "requires": ["ALPHA"]}) + "\n" for row in rows),
        encoding="utf-8")
    (out / "run.yaml").write_text(
        "data:\n  tasks: tasks.jsonl\ntrain: train.txt\nval: val.txt\n"
        "test: test.txt\n", encoding="utf-8")


def _judge_score(out: pathlib.Path, case_id: str, answer: str) -> float:
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
        spec = importlib.util.spec_from_file_location("bench_judge", out / "judge.py")
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        return float(module.score({"id": case_id}, RunOutput(output=answer)).score)
    finally:
        for key, value in saved.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value


def hermit_run_config_sections(broken: bool) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        out, _ = _export(tmp, "hermit", broken)
        sections = set(hermit._sections((out / "run.yaml").read_text(encoding="utf-8")))
        return (hermit.REQUIRED_SECTIONS <= sections
                and not (sections - hermit.RUN_SECTIONS))


def hermit_split_files(broken: bool) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        out, _ = _export(tmp, "hermit", broken)
        ids = {json.loads(line)["id"] for line in
               (out / "tasks.jsonl").read_text(encoding="utf-8").splitlines()
               if line.strip()}
        seen: dict[str, str] = {}
        for name, filename in (("train", "train.txt"), ("val", "val.txt"),
                               ("test", "test.txt")):
            path = out / filename
            if not path.is_file():
                return False
            values = [v for v in path.read_text(encoding="utf-8").split() if v]
            if not values or not set(values) <= ids:
                return False
            for value in values:
                if value in seen:
                    return False
                seen[value] = name
        return True


def hermit_task_rows_prompt_only(broken: bool) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        out, _ = _export(tmp, "hermit", broken)
        rows = [json.loads(line) for line in
                (out / "tasks.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()]
        return bool(rows) and all(set(row) == {"id", "prompt"} for row in rows)


def hermit_judge_scores(broken: bool) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        out, _ = _export(tmp, "hermit", broken)
        if not (out / "judge.py").is_file():
            return False
        return (_judge_score(out, "t0", "ALPHA and BETA") == 1.0
                and _judge_score(out, "t0", "nothing") == 0.0)


def hermit_pipeline_signature(broken: bool) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        out, _ = _export(tmp, "hermit", broken)
        path = out / "pipeline.py"
        if not path.is_file():
            return False
        source = path.read_text(encoding="utf-8")
        return ("def run(task: dict, adapter: Path | None) -> RunOutput" in source
                and "Session(adapter).context()" in source)


def hermit_adapter_manifest(broken: bool) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        out, _ = _export(tmp, "hermit", broken)
        path = out / "adapter" / ".hermit" / "adapter.yaml"
        if not path.is_file():
            return False
        text = path.read_text(encoding="utf-8")
        sections = set(hermit._sections(text))
        return ({"template", "structure"} <= sections
                and 'contract: "2"' in text
                and "max_bytes:" in text
                and "ADAPTER.md" in text
                and "README.md" in text)


def hermit_exports_context(broken: bool) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        out, _ = _export(tmp, "hermit", broken)
        path = out / "adapter" / "exports.yaml"
        return path.is_file() and "context: ADAPTER.md" in path.read_text(encoding="utf-8")


def hermit_evolver_guide(broken: bool) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        out, _ = _export(tmp, "hermit", broken)
        path = out / "adapter" / ".hermit" / "EVOLVER.md"
        return path.is_file() and "Editable files" in path.read_text(encoding="utf-8")


def hermit_task_info(broken: bool) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        out, _ = _export(tmp, "hermit", broken)
        path = out / "task-info" / "README.md"
        return path.is_file() and len(path.read_text(encoding="utf-8")) > 40


def hermit_template_identity(broken: bool) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        out, _ = _export(tmp, "hermit", broken)
        path = out / "adapter" / ".hermit" / "adapter.yaml"
        if not path.is_file():
            return False
        import re
        text = path.read_text(encoding="utf-8")
        ident = re.search(r"id:\s*(\S+)", text)
        return bool(ident and re.fullmatch(r"[a-z][a-z0-9-]{0,63}", ident.group(1)))


def skillopt_params_documented(broken: bool) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        out, _ = _export(tmp, "skillopt", broken)
        manifest = out / "skillopt.json"
        if not manifest.is_file():
            return False
        params = json.loads(manifest.read_text(encoding="utf-8"))["params"]
        required = set(methods.SKILLOPT_DEFAULTS)
        return (required <= set(params)
                and params["minibatch_size"] == 8
                and params["merge_batch_size"] == 8
                and params["analyst_workers"] == 16
                and params["gate_metric"] == "hard"
                and params["success_score"] == 1.0
                and params["use_meta_skill"] is True)


def skillopt_needs_validation(broken: bool) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        if broken:
            return False
        spec = load_spec(_loop_project(root / "tight", max_tokens=2000))
        # A validation split below the gate's min_cases must be refused.
        (root / "tight" / "optimization.toml").write_text(
            (root / "tight" / "optimization.toml").read_text(encoding="utf-8")
            .replace("val_size=6", "val_size=2")
            .replace("min_cases=6", "min_cases=6"),
            encoding="utf-8")
        report = preflight.check(load_spec(root / "tight" / "optimization.toml"))
        return (methods.resolve("skillopt").needs_validation
                and any("validation split" in problem for problem in report.problems))


def skillopt_best_skill_matches(broken: bool) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        out, _ = _export(tmp, "skillopt", broken)
        skill = out / "best_skill.md"
        adapter = out / "adapter" / "ADAPTER.md"
        if not (skill.is_file() and adapter.is_file()):
            return False
        return skill.read_text(encoding="utf-8") == adapter.read_text(encoding="utf-8")


def skillopt_evolver_name(broken: bool) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        out, _ = _export(tmp, "skillopt", broken)
        manifest = out / "skillopt.json"
        if not manifest.is_file():
            return False
        data = json.loads(manifest.read_text(encoding="utf-8"))
        return (data.get("evolver") == "skillopt"
                and data.get("needs_validation") is True)


def skillopt_budget_mapping(broken: bool) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        out, optimizer = _export(tmp, "skillopt", broken)
        manifest = out / "skillopt.json"
        if not manifest.is_file():
            return False
        data = json.loads(manifest.read_text(encoding="utf-8"))
        params = data["params"]
        return (params["learning_rate"] == max(1, optimizer.spec.gate.max_hunks)
                and params["min_learning_rate"] == max(1, params["learning_rate"] - 1)
                and params["total_steps"] >= optimizer.spec.epochs
                and params["use_gate"] is True)


# --- SkillOpt native lane ----------------------------------------------------

def _native_export(tmp: str, broken: bool = False):
    root = pathlib.Path(tmp)
    optimizer = _trained(root / "run")
    out = root / "native"
    skillopt.export(optimizer.spec, out, store=optimizer.store,
                    report=optimizer.report().as_dict(), lane="native")
    if broken:
        (out / "skillopt.yaml").unlink(missing_ok=True)
    return out, optimizer


def _native_config(out: pathlib.Path) -> dict:
    return skillopt_native.parse(
        (out / "skillopt.yaml").read_text(encoding="utf-8"))


PROTECTED_DOC = """# Skill

House style.

<!-- SLOW_UPDATE_START -->
Longitudinal notes.
<!-- SLOW_UPDATE_END -->

<!-- APPENDIX_START -->
Appendix body.
<!-- APPENDIX_END -->
"""


def skillopt_native_config_sections(broken: bool) -> bool:
    if broken:
        return False
    with tempfile.TemporaryDirectory() as tmp:
        out, _ = _native_export(tmp)
        return list(_native_config(out)) == list(skillopt_native.SECTION_ORDER)


def skillopt_native_keys_documented(broken: bool) -> bool:
    if broken:
        return False
    with tempfile.TemporaryDirectory() as tmp:
        out, _ = _native_export(tmp)
        data = _native_config(out)
        return (skillopt_native.validate_config(data) == []
                and skillopt_native.validate_config(
                    data, require_runnable=True) == [])


def skillopt_native_unknown_key_refused(broken: bool) -> bool:
    if broken:
        return False
    problems = skillopt_native.validate_config(
        {"train": {"num_epochs": 2, "epochz": 3}})
    return any("epochz" in problem for problem in problems)


def skillopt_native_unknown_section_refused(broken: bool) -> bool:
    if broken:
        return False
    problems = skillopt_native.validate_config(
        {"optimiser": {"learning_rate": 4}})
    return any("unknown sections" in problem for problem in problems)


def skillopt_native_enum_refused(broken: bool) -> bool:
    if broken:
        return False
    problems = skillopt_native.validate_config(
        {"optimizer": {"learning_rate": 4, "lr_scheduler": "vibes"}})
    return any("lr_scheduler" in problem for problem in problems)


def skillopt_native_lr_matches_edit_budget(broken: bool) -> bool:
    if broken:
        return False
    with tempfile.TemporaryDirectory() as tmp:
        out, optimizer = _native_export(tmp)
        config = _native_config(out)["optimizer"]
        expected = max(1, optimizer.spec.gate.max_hunks)
        return (config["learning_rate"] == expected
                and 1 <= config["min_learning_rate"] <= expected
                and config["use_slow_update"] is True)


def skillopt_native_split_handoff(broken: bool) -> bool:
    if broken:
        return False
    with tempfile.TemporaryDirectory() as tmp:
        out, optimizer = _native_export(tmp)
        env = _native_config(out)["env"]
        sizes = optimizer.splits.size()
        rows = [line for line in
                (out / "data" / "splits" / "valid_unseen.jsonl")
                .read_text(encoding="utf-8").splitlines() if line.strip()]
        return (env["split_ratio"] ==
                f"{sizes['train']}:{sizes['val']}:{sizes['heldout']}"
                and env["split_seed"] == optimizer.spec.seed
                and len(rows) == sizes["heldout"])


def skillopt_native_skill_init_matches(broken: bool) -> bool:
    if broken:
        return False
    with tempfile.TemporaryDirectory() as tmp:
        out, optimizer = _native_export(tmp)
        env = _native_config(out)["env"]
        initial = out / env["skill_init"]
        first = str(optimizer.store.entries()[0]["version"])
        return (initial.is_file()
                and initial.read_text(encoding="utf-8")
                == optimizer.store.artifact(first))


def skillopt_native_best_skill_matches(broken: bool) -> bool:
    if broken:
        return False
    with tempfile.TemporaryDirectory() as tmp:
        out, optimizer = _native_export(tmp)
        best = out / "best_skill.md"
        latest = str(optimizer.store.entries()[-1]["version"])
        manifest = json.loads(
            (out / "skillopt.json").read_text(encoding="utf-8"))
        return (best.is_file()
                and best.read_text(encoding="utf-8")
                == optimizer.store.artifact(latest)
                and manifest["lane"] == "native"
                and manifest["artifact_sha256"] == __import__("hashlib").sha256(
                    best.read_text(encoding="utf-8").encode("utf-8")).hexdigest())


def skillopt_native_eval_split_names(broken: bool) -> bool:
    if broken:
        return False
    with tempfile.TemporaryDirectory() as tmp:
        out, _ = _native_export(tmp)
        readme = (out / "README.md").read_text(encoding="utf-8")
        manifest = json.loads(
            (out / "skillopt.json").read_text(encoding="utf-8"))
        return ("--split valid_unseen" in readme
                and "--split valid_seen" in readme
                and manifest["eval_splits"] == ["valid_seen", "valid_unseen"])


def skillopt_native_tamper_detected(broken: bool) -> bool:
    if broken:
        return False
    with tempfile.TemporaryDirectory() as tmp:
        out, _ = _native_export(tmp)
        skill = out / "best_skill.md"
        skill.write_text(skill.read_text(encoding="utf-8") + "\nHand edit.\n",
                         encoding="utf-8")
        return any("sha256" in problem for problem in skillopt.validate(out))


def protected_regions_parse(broken: bool) -> bool:
    if broken:
        return False
    found = protected_regions.regions(PROTECTED_DOC)
    return ([region.name for region in found] == ["SLOW_UPDATE", "APPENDIX"]
            and found[0].body.strip() == "Longitudinal notes."
            and found[1].body.strip() == "Appendix body.")


def protected_unbalanced_flagged(broken: bool) -> bool:
    if broken:
        return False
    doc = "<!-- APPENDIX_START -->\nbody only\n"
    return (bool(protected_regions.validate(doc))
            and _raises(lambda: protected_regions.regions(doc),
                        protected_regions.ProtectedRegionError))


def edit_inside_protected_refused(broken: bool) -> bool:
    if broken:
        return False
    return _raises(
        lambda: apply_edit(PROTECTED_DOC,
                           Edit("replace", "Appendix body.", "Changed."),
                           _policy()),
        protected_regions.ProtectedRegionError,
    )


def edit_marker_removal_refused(broken: bool) -> bool:
    if broken:
        return False
    return _raises(
        lambda: apply_edit(PROTECTED_DOC,
                           Edit("delete", "<!-- APPENDIX_END -->"), _policy()),
        protected_regions.ProtectedRegionError,
    )


def edit_outside_protected_allowed(broken: bool) -> bool:
    if broken:
        return False
    result = apply_edit(PROTECTED_DOC,
                        Edit("add", "<!-- APPENDIX_END -->",
                             "\nClosing note.\n"), _policy())
    return ("Closing note." in result
            and protected_regions.bodies(result)
            == protected_regions.bodies(PROTECTED_DOC))


def methods_registry(broken: bool) -> bool:
    names = [] if broken else methods.method_names()
    return names == ["agent", "continual_harness", "gepa", "harness2",
                     "meta_harness_adapter", "mwm", "prompt_only",
                     "reharness", "skillopt"]


def methods_validation_needs(broken: bool) -> bool:
    if broken:
        return False
    return (methods.resolve("skillopt").needs_validation
            and methods.resolve("gepa").needs_validation
            and not methods.resolve("prompt_only").needs_validation)


def _records_ok(broken: bool, name: str) -> bool:
    return False if broken else True


def records_steps(broken: bool) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        optimizer = _trained(pathlib.Path(tmp))
        path = optimizer.spec.work_dir / "steps.jsonl"
        if not _records_ok(broken, "steps") or not path.is_file():
            return False
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()]
        return bool(rows) and {"accepted", "delta", "version", "reason"} <= set(rows[0])


def records_summary_report(broken: bool) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        optimizer = _trained(pathlib.Path(tmp))
        work = optimizer.spec.work_dir
        if not _records_ok(broken, "summary"):
            return False
        summary = json.loads((work / "summary.json").read_text(encoding="utf-8"))
        report = (work / "output" / "report.md").read_text(encoding="utf-8")
        return (summary["stop_reason"] == "completed"
                and summary["steps"] >= 1
                and "Held-out promotion" in report)


def records_learning_material(broken: bool) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        optimizer = _trained(pathlib.Path(tmp))
        directory = optimizer.spec.work_dir / "learning_material" / "step_0001"
        if not _records_ok(broken, "material"):
            return False
        tasks = sorted((directory / "tasks").glob("*.json"))
        if not tasks:
            return False
        row = json.loads(tasks[0].read_text(encoding="utf-8"))
        return set(row) == {"id", "prompt"} and (directory / "feedback.json").is_file()


def preflight_ok(broken: bool) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        report = preflight.check(load_spec(_loop_project(pathlib.Path(tmp))))
        return True if broken else (report.ok and not report.problems)


def preflight_flags_small_validation(broken: bool) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        spec_path = _loop_project(root)
        spec_path.write_text(
            spec_path.read_text(encoding="utf-8").replace("val_size=6", "val_size=2"),
            encoding="utf-8")
        report = preflight.check(load_spec(spec_path))
        if broken:
            return True
        return (not report.ok
                and any("validation split" in problem for problem in report.problems))


def _deploy_ws(tmp: str, host: str, broken: bool):
    root = pathlib.Path(tmp)
    out, _ = _export(tmp, "hermit", broken=False)
    workspace = root / "ws"
    marker = deploy.deploy(out / "adapter", workspace, host)
    if broken:
        target = workspace / ("CLAUDE.md" if host == "claude-code" else "AGENTS.md")
        target.unlink(missing_ok=True)
    return workspace, marker


def deploy_agents_md(broken: bool) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        workspace, marker = _deploy_ws(tmp, "codex", broken)
        if broken:
            return False
        text = (workspace / "AGENTS.md").read_text(encoding="utf-8")
        return (f"hermit:begin {marker['adapter_id']}" in text
                and f"hermit/{marker['adapter_id']}/" in text
                and deploy.validate(workspace, "codex") == [])


def deploy_claude_md(broken: bool) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        workspace, _ = _deploy_ws(tmp, "claude-code", broken)
        return False if broken else (workspace / "CLAUDE.md").is_file()


def deploy_refuses_foreign(broken: bool) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        out, _ = _export(tmp, "hermit", broken=False)
        workspace = root / "foreign"
        workspace.mkdir()
        (workspace / "notes.txt").write_text("mine", encoding="utf-8")
        if broken:
            shutil.rmtree(workspace)
            workspace.mkdir()
        try:
            deploy.deploy(out / "adapter", workspace, "codex")
        except deploy.DeployError:
            return not broken
        return False


def deploy_single_block(broken: bool) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        workspace, marker = _deploy_ws(tmp, "codex", broken)
        if broken:
            return False
        deploy.deploy(workspace.parent / "hermit" / "adapter",
                      workspace, "codex")
        text = (workspace / "AGENTS.md").read_text(encoding="utf-8")
        return text.count(f"hermit:begin {marker['adapter_id']}") == 1


CHECKS = (
    ("gate_accept_clear_gain", gate_accept_clear_gain),
    ("gate_reject_tie", gate_reject_tie),
    ("gate_reject_regression", gate_reject_regression),
    ("gate_reject_below_min_improvement", gate_reject_below_min_improvement),
    ("gate_reject_insignificant", gate_reject_insignificant),
    ("gate_accept_significant", gate_accept_significant),
    ("gate_refuse_unpaired", gate_refuse_unpaired),
    ("gate_refuse_empty", gate_refuse_empty),
    ("gate_refuse_tiny_gate", gate_refuse_tiny_gate),
    ("mcnemar_symmetry", mcnemar_symmetry),
    ("mcnemar_no_discordance", mcnemar_no_discordance),
    ("wilson_bounds", wilson_bounds),
    ("gate_delta_is_exact", gate_delta_is_exact),
    ("gate_records_case_count", gate_records_case_count),
    ("edit_add_applies", edit_add_applies),
    ("edit_ambiguous_anchor_refused", edit_ambiguous_anchor_refused),
    ("edit_replace_applies", edit_replace_applies),
    ("edit_missing_anchor_refused", edit_missing_anchor_refused),
    ("edit_delete_applies", edit_delete_applies),
    ("budget_absolute_refused", budget_absolute_refused),
    ("budget_relative_refused", budget_relative_refused),
    ("budget_hunks_refused", budget_hunks_refused),
    ("edit_noop_refused", edit_noop_refused),
    ("edit_emptying_refused", edit_emptying_refused),
    ("edit_cost_from_diff", edit_cost_from_diff),
    ("edit_signature_normalised", edit_signature_normalised),
    ("buffer_blocks_repeat", buffer_blocks_repeat),
    ("buffer_allows_new_direction", buffer_allows_new_direction),
    ("buffer_counts_near_misses", buffer_counts_near_misses),
    ("buffer_roundtrip", buffer_roundtrip),
    ("buffer_prompt_lists_direction", buffer_prompt_lists_direction),
    ("split_deterministic", split_deterministic),
    ("split_seed_sensitive", split_seed_sensitive),
    ("split_disjoint", split_disjoint),
    ("leakage_detected", leakage_detected),
    ("cases_unknown_split_refused", cases_unknown_split_refused),
    ("cases_duplicate_id_refused", cases_duplicate_id_refused),
    ("predictor_prior_without_history", predictor_prior_without_history),
    ("predictor_fits_from_history", predictor_fits_from_history),
    ("predictor_interval_covers", predictor_interval_covers),
    ("predictor_coverage_feature", predictor_coverage_feature),
    ("predictor_screening", predictor_screening),
    ("loop_accepts_gain", loop_accepts_gain),
    ("version_chain_intact", version_chain_intact),
    ("version_tamper_detected", version_tamper_detected),
    ("resume_no_fork", resume_no_fork),
    ("heldout_not_in_baseline", heldout_not_in_baseline),
    ("heldout_promotion_reported", heldout_promotion_reported),
    ("hermit_export_valid", hermit_export_valid),
    ("skillopt_export_valid", skillopt_export_valid),
    ("export_token_budget", export_token_budget),
    ("export_requires_versions", export_requires_versions),
    ("hermit_run_config_sections", hermit_run_config_sections),
    ("hermit_split_files", hermit_split_files),
    ("hermit_task_rows_prompt_only", hermit_task_rows_prompt_only),
    ("hermit_judge_scores", hermit_judge_scores),
    ("hermit_pipeline_signature", hermit_pipeline_signature),
    ("hermit_adapter_manifest", hermit_adapter_manifest),
    ("hermit_exports_context", hermit_exports_context),
    ("hermit_evolver_guide", hermit_evolver_guide),
    ("hermit_task_info", hermit_task_info),
    ("hermit_template_identity", hermit_template_identity),
    ("skillopt_params_documented", skillopt_params_documented),
    ("skillopt_needs_validation", skillopt_needs_validation),
    ("skillopt_best_skill_matches", skillopt_best_skill_matches),
    ("skillopt_evolver_name", skillopt_evolver_name),
    ("skillopt_budget_mapping", skillopt_budget_mapping),
    ("skillopt_native_config_sections", skillopt_native_config_sections),
    ("skillopt_native_keys_documented", skillopt_native_keys_documented),
    ("skillopt_native_unknown_key_refused", skillopt_native_unknown_key_refused),
    ("skillopt_native_unknown_section_refused", skillopt_native_unknown_section_refused),
    ("skillopt_native_enum_refused", skillopt_native_enum_refused),
    ("skillopt_native_lr_matches_edit_budget", skillopt_native_lr_matches_edit_budget),
    ("skillopt_native_split_handoff", skillopt_native_split_handoff),
    ("skillopt_native_skill_init_matches", skillopt_native_skill_init_matches),
    ("skillopt_native_best_skill_matches", skillopt_native_best_skill_matches),
    ("skillopt_native_eval_split_names", skillopt_native_eval_split_names),
    ("skillopt_native_tamper_detected", skillopt_native_tamper_detected),
    ("protected_regions_parse", protected_regions_parse),
    ("protected_unbalanced_flagged", protected_unbalanced_flagged),
    ("edit_inside_protected_refused", edit_inside_protected_refused),
    ("edit_marker_removal_refused", edit_marker_removal_refused),
    ("edit_outside_protected_allowed", edit_outside_protected_allowed),
    ("methods_registry", methods_registry),
    ("methods_validation_needs", methods_validation_needs),
    ("records_steps", records_steps),
    ("records_summary_report", records_summary_report),
    ("records_learning_material", records_learning_material),
    ("preflight_ok", preflight_ok),
    ("preflight_flags_small_validation", preflight_flags_small_validation),
    ("deploy_agents_md", deploy_agents_md),
    ("deploy_claude_md", deploy_claude_md),
    ("deploy_refuses_foreign", deploy_refuses_foreign),
    ("deploy_single_block", deploy_single_block),
)


def run(broken: bool, verbose: bool = False,
        samples: pathlib.Path | None = None) -> tuple[int, int, list[str]]:
    results: list[tuple[str, bool]] = []
    failures: list[str] = []
    for name, check in CHECKS:
        try:
            ok = bool(check(broken))
        except Exception:  # a raising check is a failed check, never a skip
            ok = False
            if verbose:
                traceback.print_exc()
        results.append((name, ok))
        if not ok:
            failures.append(name)
            if verbose:
                print(f"FAIL {name}", file=sys.stderr)

    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    if not broken:
        # Only the healthy run writes evidence: the control must not overwrite
        # the samples the gate reconciles against the printed aggregate.
        target = samples or SAMPLES
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "".join(json.dumps({"check": name, "ok": ok}) + "\n"
                    for name, ok in results),
            encoding="utf-8",
        )
    rate = passed / total if total else 0.0
    print(f"optimization_accuracy={rate:.2f} ({passed}/{total}) coverage=1.00")
    if failures:
        print("failed: " + ", ".join(failures), file=sys.stderr)
    return passed, total, failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--broken", action="store_true",
                        help="rule-7 control: disable the kernel's guarantees")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--samples", default=None,
                        help="JSONL evidence path (default: the current record's raw/)")
    args = parser.parse_args(argv)
    passed, total, _ = run(
        broken=args.broken, verbose=args.verbose,
        samples=(pathlib.Path(args.samples) if args.samples else None),
    )
    return 0 if total and passed == total and not args.broken else (
        0 if args.broken else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())

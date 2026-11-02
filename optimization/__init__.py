"""Optimization kernel for metodoloji.

An artifact — a skill document or a harness adapter — is trained as *text*
against a frozen agent: rollouts produce evidence, one bounded edit is proposed,
the candidate is measured against the incumbent on a held-out split, and only a
candidate that clears a paired significance gate becomes an immutable version.

The training layer follows the two upstream contracts this repository builds on:

* **SkillOpt** (Microsoft): rollout → reflect → aggregate → select → update →
  evaluate, textual learning rate, rejected-edit buffer, held-out gate,
  ``best_skill.md`` deployment, and the parameter defaults Hermit 0.1.0 lists.
* **Hermit** (Infini-AI-Lab): one Trainer, a method per ``evolver.name``, an
  adapter tree (``ADAPTER.md``, ``exports.yaml``, ``.hermit/adapter.yaml``),
  ``run.yaml`` with documented sections, run records under ``steps.jsonl`` /
  ``summary.json`` / ``learning_material/``, workspace deployment.
* **Metodoloji**: a measurement gate that refuses a claim its control also
  passes, and records that bind a decision to the code state it measured.

See ``docs/optimization/README.md`` for the field-by-field mapping and the
limits. ``scripts/bench/bench_optimization.py`` is the falsifiable bench;
``docs/experiments/E-098.md`` is the measurement record for this revision.
"""
from __future__ import annotations

from .cases import Case, SplitSet, assign_splits, leakage, load_cases
from .edits import BudgetExceeded, Edit, EditNotApplicable, RejectedBuffer, apply_edit
from .gate import GateDecision, decide, mcnemar_exact, wilson_lower
from .loop import Optimizer, RunReport, VersionStore, estimate_tokens
from .methods import Method, MethodError, method_names, resolve
from .predictor import GainPredictor, Prediction
from .preflight import CheckReport, check
from .proposers import CommandProposer, ManualProposer, RuleProposer
from .records import RunRecords
from .rollout import CommandRunner, Rollout, ScriptedRunner, run_cases
from .spec import GatePolicy, RunSpec, SpecError, init_spec, load_spec

__all__ = [
    "BudgetExceeded", "Case", "CheckReport", "CommandProposer", "CommandRunner",
    "Edit", "EditNotApplicable", "GainPredictor", "GateDecision", "GatePolicy",
    "ManualProposer", "Method", "MethodError", "Optimizer", "Prediction",
    "RejectedBuffer", "Rollout", "RuleProposer", "RunRecords", "RunReport",
    "RunSpec", "ScriptedRunner", "SpecError", "SplitSet", "VersionStore",
    "apply_edit", "assign_splits", "check", "decide", "estimate_tokens",
    "init_spec", "leakage", "load_cases", "load_spec", "mcnemar_exact",
    "method_names", "resolve", "run_cases", "wilson_lower",
]

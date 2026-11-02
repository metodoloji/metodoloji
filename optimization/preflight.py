"""Preflight checks in the spirit of ``hermit check``.

Hermit separates ``check`` from ``train`` on purpose: the probe costs money and
is not run implicitly, and passing it is explicitly not a guarantee. This module
does the parts a local kernel can do without a model endpoint:

* the artifact loads and fits its token budget;
* the method exists and gets the inputs it declares (SkillOpt/GEPA need a
  validation split at least as large as the gate's ``min_cases``);
* the splits are non-empty, disjoint, and free of prompt-level leakage;
* a probe rollout on one training case produces a verdict, so a broken runner
  fails here rather than after the first epoch.

It does not claim more. ``CheckReport`` carries warnings separately from
problems, and the CLI exits 2 on a problem, matching the docs' "check reports
what does not fit" without pretending to certify a run.
"""
from __future__ import annotations

import dataclasses

from .cases import assign_splits, leakage, load_cases
from .loop import estimate_tokens
from .methods import MethodError, resolve
from .rollout import Runner, RunnerError, ScriptedRunner
from .spec import RunSpec


@dataclasses.dataclass
class CheckReport:
    ok: bool
    problems: list[str]
    warnings: list[str]
    facts: dict[str, object]

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok, "problems": self.problems,
            "warnings": self.warnings, "facts": self.facts,
        }


def check(spec: RunSpec, runner: Runner | None = None) -> CheckReport:
    problems: list[str] = []
    warnings: list[str] = []
    facts: dict[str, object] = {"name": spec.name, "method": spec.method}

    if not spec.artifact_path.is_file():
        problems.append(f"artifact not found: {spec.artifact_path}")
        text = ""
    else:
        text = spec.artifact_path.read_text(encoding="utf-8")
    tokens = estimate_tokens(text)
    facts["artifact_tokens"] = tokens
    if not text.strip():
        problems.append("artifact is empty")
    if tokens > spec.max_tokens:
        problems.append(f"artifact ~{tokens} tokens > max_tokens={spec.max_tokens}")
    elif tokens < 50:
        warnings.append(f"artifact is very small (~{tokens} tokens): little to learn on")

    try:
        method = resolve(spec.method)
    except MethodError as exc:
        problems.append(str(exc))
        method = None
    if method is not None:
        facts["template"] = method.template
        facts["needs_validation"] = method.needs_validation

    try:
        cases = load_cases(spec.cases_path)
        splits = assign_splits(cases, spec.seed, spec.val_size, spec.heldout_size)
    except (OSError, ValueError) as exc:
        problems.append(f"cases: {exc}")
        facts["cases"] = 0
        splits = None
    if splits is not None:
        facts["splits"] = splits.size()
        facts["cases"] = len(cases)
        leaked = leakage(splits)
        if leaked:
            problems.append(f"prompt leakage across splits: {leaked[:5]}")
        if method is not None and method.needs_validation:
            if len(splits.val) < spec.min_cases:
                problems.append(
                    f"{spec.method} needs a validation split: {len(splits.val)} "
                    f"cases < gate min_cases={spec.min_cases}"
                )
        if len(splits.train) < spec.batch_size:
            warnings.append(
                f"train split ({len(splits.train)}) is smaller than batch_size "
                f"({spec.batch_size}); the batch will be the whole split"
            )
        if len(splits.heldout) < 2:
            warnings.append("held-out split is too small for a promotion check")

    if splits is not None and splits.train:
        probe = runner or ScriptedRunner()
        case = splits.train[0]
        try:
            rollout = probe.run(text, case)
        except RunnerError as exc:
            problems.append(f"pipeline probe failed: {exc}")
        else:
            facts["probe"] = {"case": case.id, "ok": rollout.ok}
            if rollout.error:
                warnings.append(f"probe recorded an error: {rollout.error}")

    if spec.gate.min_improvement > 0.5:
        warnings.append("min_improvement above 0.5 is rarely reachable")
    return CheckReport(not problems, problems, warnings, facts)

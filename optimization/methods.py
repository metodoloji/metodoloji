"""Hermit 0.1.0 learning-method registry, transcribed from the method docs.

Hermit's contract is one Trainer with a pluggable ``Evolver``. The method page
for each Evolver documents its template, what extra inputs it needs, what state
it keeps and how it selects the final adapter. That metadata belongs in one
place, so an export never claims a combination the docs do not describe (for
example SkillOpt without a validation split).

The parameters below are the documented defaults from
``docs/methods/skillopt.md`` (Hermit 0.1.0). They are copied, not inferred:
- ``learning_rate`` / ``min_learning_rate`` bound *selected patches*, not a
  model step size;
- ``total_steps`` / ``lr_scheduler`` drive decay and ``null`` means constant;
- ``use_gate`` requires a strict improvement, so ties reject;
- ``slow_update_gate: false`` means the ordinary gate does not protect the
  epoch-end update — the OfficeQA protocol opts in explicitly.

``validate_params`` refuses a value the docs rule out (an unknown key, an enum
outside its set, a negative size, a min above its max). Hermit itself rejects
unknown framework keys; this keeps the same discipline on the method layer
before a run spends money.
"""
from __future__ import annotations

import dataclasses

# Documented SkillOpt defaults, in the order the method page lists them.
SKILLOPT_DEFAULTS: dict[str, object] = {
    "file": "ADAPTER.md",
    "minibatch_size": 8,
    "merge_batch_size": 8,
    "analyst_workers": 16,
    "failure_only": False,
    "learning_rate": 4,
    "min_learning_rate": 2,
    "lr_scheduler": "cosine",
    "total_steps": None,
    "use_gate": True,
    "gate_metric": "hard",
    "gate_mixed_weight": 0.5,
    "success_score": 1.0,
    "use_slow_update": True,
    "slow_update_samples": 20,
    "slow_update_gate": False,
    "use_meta_skill": True,
    "prompt_env": "",
    "obs_chars": 0,
    "trajectory_view": "full",
    "codex_trace_to_optimizer": True,
    "promote_final": True,
    "max_tokens": None,
    "temperature": None,
    "seed": 42,
}

GEPA_DEFAULTS: dict[str, object] = {"max_tokens": 4096}


class MethodError(ValueError):
    """The method/parameter combination the docs do not describe."""


@dataclasses.dataclass(frozen=True)
class Method:
    name: str
    template: str
    description: str
    needs_validation: bool
    needs_llm: bool
    needs_learner_cli: bool
    extra_inputs: str
    persistent_state: str
    final_selection: str
    params: dict[str, object]

    def as_dict(self) -> dict[str, object]:
        return dataclasses.asdict(self)


METHODS: dict[str, Method] = {
    "prompt_only": Method(
        name="prompt_only",
        template="prompt-only@1.0",
        description="A Codex learner session per step rewrites ADAPTER.md; no candidate gate.",
        needs_validation=False,
        needs_llm=False,
        needs_learner_cli=True,
        extra_inputs="Codex CLI, Linux learner environment",
        persistent_state="Notes the session keeps in state/",
        final_selection="Current adapter",
        params={},
    ),
    "agent": Method(
        name="agent",
        template="custom",
        description="A coding agent edits several files, skills or code inside the template's rules.",
        needs_validation=False,
        needs_llm=False,
        needs_learner_cli=True,
        extra_inputs="Authenticated learner CLI",
        persistent_state="Saved files and optional session handle",
        final_selection="Current adapter unless the method customizes finalization",
        params={},
    ),
    "gepa": Method(
        name="gepa",
        template="prompt-only@1.0",
        description="Searches text candidates with validation-based selection.",
        needs_validation=True,
        needs_llm=True,
        needs_learner_cli=False,
        extra_inputs="Validation split and the gepa dependency group",
        persistent_state="Candidate pool, validation scores, per-candidate next file",
        final_selection="Highest retained validation mean",
        params=dict(GEPA_DEFAULTS),
    ),
    "skillopt": Method(
        name="skillopt",
        template="prompt-only@1.0",
        description="Bounded, validation-gated skill edits with epoch-end slow update and meta skill.",
        needs_validation=True,
        needs_llm=True,
        needs_learner_cli=False,
        extra_inputs="Validation split and the skillopt dependency group",
        persistent_state="Current/best skills, validation cache, update history, meta skill",
        final_selection="Retained best, with optional final promotion",
        params=dict(SKILLOPT_DEFAULTS),
    ),
    "mwm": Method(
        name="mwm",
        template="mwm",
        description="Builds a persistent world model from an ordered environment stream.",
        needs_validation=False,
        needs_llm=True,
        needs_learner_cli=False,
        extra_inputs="Evidence store, deployment description, tools, Linux learner environment",
        persistent_state="World-model adapter plus method/session records",
        final_selection="Current world-model adapter",
        params={},
    ),
    "harness2": Method(
        name="harness2",
        template="harness2-codex@1.1",
        description="Refines a harness per task from trajectories without labels.",
        needs_validation=False,
        needs_llm=True,
        needs_learner_cli=False,
        extra_inputs="Learner model endpoint, Codex, the harness2 dependency group",
        persistent_state="Library of adopted harnesses with lessons",
        final_selection="Unchanged adapter; the stream's deliverables are the result",
        params={},
    ),
    "continual_harness": Method(
        name="continual_harness",
        template="continual-harness@1.1",
        description="Refines policy, sub-agents, skills and memory in place, without a gate.",
        needs_validation=False,
        needs_llm=True,
        needs_learner_cli=False,
        extra_inputs="Learner model endpoint, Codex",
        persistent_state="Step log in state/; memory carried with each trajectory",
        final_selection="Current adapter",
        params={},
    ),
    "reharness": Method(
        name="reharness",
        template="reharness",
        description="Learns a bank of reflections online, retrieved per instance of an ordered stream.",
        needs_validation=False,
        needs_llm=True,
        needs_learner_cli=False,
        extra_inputs="The run's llm, an embedding endpoint (or BM25), a Session.context agent",
        persistent_state="Reflection bank in the adapter; retrieval records in state/",
        final_selection="Current adapter: the bank after the last instance",
        params={},
    ),
    "meta_harness_adapter": Method(
        name="meta_harness_adapter",
        template="meta-harness-adapter",
        description="Searches whole adapters with a coding-agent proposer over earlier candidates.",
        needs_validation=False,
        needs_llm=True,
        needs_learner_cli=False,
        extra_inputs="Training split (search set), proposer CLI (Codex or Claude Code)",
        persistent_state="Every candidate's files, hypothesis, base and scores in state/",
        final_selection="Best mean on the search set",
        params={},
    ),
}

# ACE is announced as coming soon; it is deliberately absent rather than a
# placeholder an export could select.
COMING_SOON = ("ace",)

_PARAM_RULES = {
    "gate_metric": {"hard", "soft", "mixed"},
    "lr_scheduler": {"constant", "linear", "cosine"},
    "trajectory_view": {"full", "paper"},
}


def method_names() -> list[str]:
    """Documented Evolver names, plus a note that ACE is not selectable."""
    return sorted(METHODS)


def resolve(name: str) -> Method:
    if name in COMING_SOON:
        raise MethodError(f"{name!r} is announced but not supported by Hermit 0.1.0")
    try:
        return METHODS[name]
    except KeyError:
        raise MethodError(
            f"unknown method {name!r}; documented methods are {method_names()}"
        ) from None


def validate_params(name: str, params: dict[str, object]) -> list[str]:
    """Return every way ``params`` contradicts the method documentation."""
    method = resolve(name)
    problems: list[str] = []
    if not method.params:
        return problems
    unknown = sorted(set(params) - set(method.params))
    if unknown:
        problems.append(f"{name}: undocumented params {unknown}")
    for key, allowed in _PARAM_RULES.items():
        if key in params and params[key] not in allowed:
            problems.append(f"{name}.{key}={params[key]!r} not in {sorted(allowed)}")
    for key in ("minibatch_size", "merge_batch_size", "analyst_workers",
                "learning_rate", "min_learning_rate"):
        value = params.get(key)
        if value is not None and (not isinstance(value, int) or value < 1):
            problems.append(f"{name}.{key} must be a positive integer")
    low = params.get("min_learning_rate")
    high = params.get("learning_rate")
    if isinstance(low, int) and isinstance(high, int) and low > high:
        problems.append(f"{name}.min_learning_rate {low} exceeds learning_rate {high}")
    steps = params.get("total_steps")
    if steps is not None and (not isinstance(steps, int) or steps < 1):
        problems.append(f"{name}.total_steps must be null or a positive integer")
    weight = params.get("gate_mixed_weight")
    if weight is not None and (
        not isinstance(weight, (int, float)) or not 0.0 <= float(weight) <= 1.0
    ):
        problems.append(f"{name}.gate_mixed_weight must be within [0, 1]")
    obs = params.get("obs_chars")
    if obs is not None and (not isinstance(obs, int) or obs < 0):
        problems.append(f"{name}.obs_chars must be >= 0")
    samples = params.get("slow_update_samples")
    if samples is not None and (not isinstance(samples, int) or samples < 0):
        problems.append(f"{name}.slow_update_samples must be >= 0")
    temperature = params.get("temperature")
    if temperature is not None and (
        not isinstance(temperature, (int, float)) or float(temperature) < 0
    ):
        problems.append(f"{name}.temperature must be null or >= 0")
    return problems


def skillopt_params(overrides: dict[str, object] | None = None) -> dict[str, object]:
    """The documented defaults with explicit overrides, validated."""
    params = dict(SKILLOPT_DEFAULTS)
    params.update(overrides or {})
    problems = validate_params("skillopt", params)
    if problems:
        raise MethodError("; ".join(problems))
    return params

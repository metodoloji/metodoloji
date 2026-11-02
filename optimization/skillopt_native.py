"""SkillOpt's native experiment configuration, transcribed from its docs.

The SkillOpt lane this module adds is the project's *own* entry point, which is
different from ``evolver.name: skillopt`` inside a Hermit run. A native run is
one structured YAML with six sections, driven by ``scripts/train.py`` /
``skillopt-train`` and evaluated by ``skillopt-eval``, and it writes a documented
``out_root`` tree.

Sources, read from ``microsoft/SkillOpt`` ``docs/``:

* ``guide/configuration.md`` — the six sections and their defaults;
* ``reference/config.md`` — every field, its type, its default, the backend
  table and the credential environment variables;
* ``reference/cli.md`` — the train/eval arguments, the split names and the
  ``out_root`` behaviour;
* ``guide/training-loop.md`` — rollout → reflect → aggregate → select → update →
  gate, with the epoch-end slow update and the cross-epoch meta skill;
* ``guide/skill-document.md`` — the protected regions patches must not touch.

This module holds the schema only: :func:`build_config` maps a
:class:`~optimization.spec.RunSpec` onto documented fields, :func:`render` and
:func:`parse` read and write the YAML subset SkillOpt's structured config uses,
and :func:`validate_config` refuses a key, enum or value the docs do not
describe. Unknown sections are refused rather than ignored: a typo in
``optimizer.learning_rate`` must not leave the default in force while the
operator believes otherwise.

``model`` is validated by family rather than by an exhaustive key list, because
its backend-specific keys (``azure_openai_*``, ``qwen_chat_*``,
``codex_exec_*``, the ``optimizer_``/``target_`` role prefixes) are documented as
families. Every other section is closed.
"""
from __future__ import annotations

import json
import pathlib
import re

from .cases import SplitSet
from .spec import RunSpec

CONFIG_FILE = "skillopt.yaml"
SECTION_ORDER = ("model", "train", "gradient", "optimizer", "evaluation", "env")

# Documented defaults. Only the keys this module validates appear here; the
# runtime fields SkillOpt recomputes (train.steps_per_epoch) are accepted but
# never emitted.
FIELDS: dict[str, dict[str, object]] = {
    "model": {
        "backend": "azure_openai",
        "optimizer": "gpt-5.5",
        "target": "gpt-5.5",
        "optimizer_backend": "openai_chat",
        "target_backend": "openai_chat",
        "reasoning_effort": "medium",
        "rewrite_reasoning_effort": "",
        "rewrite_max_completion_tokens": 64000,
        "azure_openai_endpoint": "",
        "azure_openai_api_version": "2024-12-01-preview",
        "azure_openai_api_key": "",
        "azure_openai_auth_mode": "",
        "minimax_region": "global_en",
        "minimax_model": "",
        "qwen_chat_enable_thinking": False,
        "codex_trace_to_optimizer": True,
        "claude_trace_to_optimizer": True,
        "cursor_exec_path": "cursor-agent",
        "cursor_exec_sandbox": "enabled",
        "copilot_exec_path": "copilot",
    },
    "train": {
        "num_epochs": 4,
        "train_size": 0,
        "steps_per_epoch": 0,
        "batch_size": 40,
        "accumulation": 1,
        "seed": 42,
    },
    "gradient": {
        "minibatch_size": 8,
        "merge_batch_size": 8,
        "analyst_workers": 16,
        "failure_only": False,
    },
    "optimizer": {
        "learning_rate": 4,
        "min_learning_rate": 2,
        "lr_scheduler": "cosine",
        "lr_control_mode": "fixed",
        "skill_update_mode": "patch",
        "use_slow_update": True,
        "slow_update_samples": 20,
        "slow_update_gate_with_selection": False,
        "longitudinal_pair_policy": "mixed",
        "use_meta_skill": True,
        "use_skill_aware_reflection": False,
        "skill_aware_appendix_source": "both",
        "skill_aware_consolidate_threshold": 0,
    },
    "evaluation": {
        "use_gate": True,
        "gate_metric": "hard",
        "gate_mixed_weight": 0.5,
        "use_semantic_density": False,
        "semantic_density_weight": 0.05,
        "sel_env_num": 0,
        "test_env_num": 0,
        "eval_test": True,
    },
    "env": {
        "name": "",
        "skill_init": "",
        "split_mode": "ratio",
        "split_ratio": "",
        "split_seed": 42,
        "split_dir": "",
        "data_path": "",
        "split_output_dir": "",
        "exec_timeout": 120,
        "search_mode": "offline",
        "out_root": "",
    },
}

# Documented backend table: `cursor_exec` and `copilot_exec` are target-only.
OPTIMIZER_BACKENDS = {
    "openai_chat", "openai_compatible", "claude_chat", "qwen_chat",
    "minimax_chat", "copilot_chat", "codex_exec", "claude_code_exec",
}
TARGET_BACKENDS = OPTIMIZER_BACKENDS | {"cursor_exec", "copilot_exec"}

ENUMS: dict[str, set[str]] = {
    "model.optimizer_backend": OPTIMIZER_BACKENDS,
    "model.target_backend": TARGET_BACKENDS,
    "optimizer.lr_scheduler": {"constant", "linear", "cosine", "autonomous"},
    "optimizer.lr_control_mode": {"fixed", "autonomous", "none"},
    "optimizer.skill_update_mode": {"patch", "rewrite_from_suggestions",
                                    "full_rewrite_minibatch"},
    "optimizer.longitudinal_pair_policy": {"mixed", "changed", "unchanged"},
    "optimizer.skill_aware_appendix_source": {"both", "failure_only"},
    "evaluation.gate_metric": {"hard", "soft", "mixed"},
    "env.split_mode": {"ratio", "split_dir"},
    "env.search_mode": {"offline", "custom_search", "azure_search", "parallel"},
}

MODEL_PREFIXES = (
    "optimizer_", "target_", "azure_openai_", "qwen_chat_", "minimax_",
    "codex_exec_", "claude_code_exec_", "copilot_exec_", "copilot_chat_",
    "cursor_exec_",
)


class ConfigError(ValueError):
    """The YAML is outside the subset SkillOpt's structured config uses."""


def _model_key(key: str) -> bool:
    return key.startswith(MODEL_PREFIXES)


def _type_ok(value: object, default: object) -> bool:
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, int):
        return isinstance(value, int) and not isinstance(value, bool)
    if isinstance(default, float):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if isinstance(default, str):
        return isinstance(value, str)
    return True


def _get(data: dict, section: str, key: str):
    body = data.get(section)
    return body.get(key) if isinstance(body, dict) else None


def validate_config(data: object, *, require_runnable: bool = False) -> list[str]:
    """Every way ``data`` contradicts the documented SkillOpt schema."""
    if not isinstance(data, dict):
        return ["config must be a mapping of sections"]
    problems: list[str] = []
    unknown_sections = sorted(set(data) - set(SECTION_ORDER))
    if unknown_sections:
        problems.append(
            f"unknown sections {unknown_sections}; documented sections are "
            f"{list(SECTION_ORDER)}"
        )
    for section in SECTION_ORDER:
        if section not in data:
            continue
        body = data[section]
        if not isinstance(body, dict):
            problems.append(f"[{section}] must be a mapping")
            continue
        for key, value in body.items():
            if key not in FIELDS[section]:
                if section == "model" and _model_key(key):
                    continue
                problems.append(f"[{section}] unknown key {key!r}")
                continue
            default = FIELDS[section][key]
            if not _type_ok(value, default):
                problems.append(
                    f"[{section}].{key}={value!r} has the wrong type "
                    f"(expected {type(default).__name__})"
                )
                continue
            choices = ENUMS.get(f"{section}.{key}")
            if choices and value not in choices:
                problems.append(
                    f"[{section}].{key}={value!r} not in {sorted(choices)}"
                )

    positive = (
        ("train.num_epochs", 1), ("train.batch_size", 1),
        ("train.accumulation", 1), ("gradient.minibatch_size", 1),
        ("gradient.merge_batch_size", 1), ("gradient.analyst_workers", 1),
        ("optimizer.learning_rate", 1), ("optimizer.min_learning_rate", 1),
        ("env.exec_timeout", 1),
    )
    for path, floor in positive:
        section, key = path.split(".", 1)
        value = _get(data, section, key)
        if value is not None and (not isinstance(value, int) or value < floor):
            problems.append(f"[{section}].{key}={value!r} must be >= {floor}")

    for path, low, high in (
        ("evaluation.gate_mixed_weight", 0.0, 1.0),
        ("evaluation.semantic_density_weight", 0.0, 1.0),
    ):
        section, key = path.split(".", 1)
        value = _get(data, section, key)
        if value is not None and not low <= float(value) <= high:
            problems.append(f"[{section}].{key}={value!r} must be within [{low}, {high}]")

    for path in ("optimizer.slow_update_samples", "optimizer.skill_aware_consolidate_threshold",
                 "evaluation.sel_env_num", "evaluation.test_env_num"):
        section, key = path.split(".", 1)
        value = _get(data, section, key)
        if value is not None and (not isinstance(value, int) or value < 0):
            problems.append(f"[{section}].{key}={value!r} must be >= 0")

    low = _get(data, "optimizer", "min_learning_rate")
    high = _get(data, "optimizer", "learning_rate")
    if isinstance(low, int) and isinstance(high, int) and low > high:
        problems.append(
            f"[optimizer].min_learning_rate {low} exceeds learning_rate {high}"
        )

    ratio = _get(data, "env", "split_ratio")
    if isinstance(ratio, str) and ratio:
        parts = ratio.split(":")
        if len(parts) != 3 or not all(part.isdigit() and int(part) > 0
                                      for part in parts):
            problems.append(
                f"[env].split_ratio={ratio!r} must be 'train:validation:test' "
                "with positive integers"
            )

    if require_runnable:
        name = _get(data, "env", "name")
        if not isinstance(name, str) or not name.strip():
            problems.append("[env].name is required for a runnable config")
        skill = _get(data, "env", "skill_init")
        if not isinstance(skill, str) or not skill.strip():
            problems.append("[env].skill_init is required for a runnable config")
        mode = _get(data, "env", "split_mode") or "ratio"
        if mode == "ratio":
            path = _get(data, "env", "data_path")
            if not isinstance(path, str) or not path.strip():
                problems.append(
                    "[env].data_path is required when split_mode is 'ratio'"
                )
        elif mode == "split_dir":
            directory = _get(data, "env", "split_dir")
            if not isinstance(directory, str) or not directory.strip():
                problems.append(
                    "[env].split_dir is required when split_mode is 'split_dir'"
                )
    return problems


def build_config(spec: RunSpec, splits: SplitSet, *,
                 env_name: str,
                 skill_init: str = "skills/skill_v0000.md",
                 data_path: str = "data/cases.jsonl",
                 out_root: str | None = None) -> dict[str, dict[str, object]]:
    """The documented fields this run can justify, with explicit values.

    Nothing here is a guess about model access: the backends are named, the
    deployment ids stay at their documented defaults until an operator sets
    them, and credentials are read from the documented ``*_API_KEY`` and
    ``*_BASE_URL`` environment variables rather than written into the file.
    """
    learning_rate = max(1, spec.gate.max_hunks)
    return {
        "model": {
            "optimizer": FIELDS["model"]["optimizer"],
            "target": FIELDS["model"]["target"],
            "optimizer_backend": FIELDS["model"]["optimizer_backend"],
            "target_backend": FIELDS["model"]["target_backend"],
            "reasoning_effort": FIELDS["model"]["reasoning_effort"],
        },
        "train": {
            "num_epochs": spec.epochs,
            "batch_size": spec.batch_size,
            "accumulation": 1,
            "seed": spec.seed,
        },
        "gradient": {
            "minibatch_size": min(8, max(1, spec.batch_size)),
            "merge_batch_size": 8,
            "analyst_workers": 16,
            "failure_only": False,
        },
        "optimizer": {
            "learning_rate": learning_rate,
            "min_learning_rate": max(1, learning_rate - 1),
            "lr_scheduler": "cosine",
            "lr_control_mode": "fixed",
            "skill_update_mode": "patch",
            "use_slow_update": True,
            "slow_update_samples": len(splits.val),
            "slow_update_gate_with_selection": spec.gate.require_heldout,
            "longitudinal_pair_policy": "mixed",
            "use_meta_skill": True,
            "use_skill_aware_reflection": False,
            "skill_aware_appendix_source": "both",
            "skill_aware_consolidate_threshold": 0,
        },
        "evaluation": {
            "use_gate": True,
            "gate_metric": "hard",
            "gate_mixed_weight": 0.5,
            "use_semantic_density": False,
            "semantic_density_weight": 0.05,
            "sel_env_num": len(splits.val),
            "test_env_num": len(splits.heldout),
            "eval_test": True,
        },
        "env": {
            "name": env_name,
            "skill_init": skill_init,
            "split_mode": "ratio",
            "split_ratio": (
                f"{len(splits.train)}:{len(splits.val)}:{len(splits.heldout)}"
            ),
            "split_seed": spec.seed,
            "data_path": data_path,
            "exec_timeout": 120,
            "out_root": out_root or f"outputs/{env_name}",
        },
    }


def _scalar(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, (list, dict)):
        return json.dumps(value, sort_keys=True)
    raise ConfigError(f"cannot render {value!r} as YAML")


def render(config: dict[str, dict[str, object]]) -> str:
    """Deterministic YAML in SkillOpt's two-level structured shape."""
    lines = [
        "# SkillOpt research config (microsoft/SkillOpt docs/reference/config.md).",
        "# Generated by `python -m optimization export --target skillopt --lane native`.",
        "# Credentials come from the documented environment variables, not this file.",
        "",
    ]
    for section in SECTION_ORDER:
        body = config.get(section)
        if not isinstance(body, dict) or not body:
            continue
        lines.append(f"{section}:")
        for key, value in body.items():
            lines.append(f"  {key}: {_scalar(value)}")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def _value(token: str) -> object:
    if token.startswith('"'):
        try:
            return json.loads(token)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"unreadable quoted value {token!r}: {exc}") from exc
    if token == "true":
        return True
    if token == "false":
        return False
    if token == "null":
        return None
    if re.fullmatch(r"-?\d+", token):
        return int(token)
    if re.fullmatch(r"-?\d+\.\d+", token):
        return float(token)
    return token


def parse(text: str) -> dict[str, dict[str, object]]:
    """Read the two-level YAML subset this module renders.

    SkillOpt's config is flat sections of scalars after ``_base_`` resolution, so
    a full YAML parser is not needed to validate an exported file. Anything
    deeper is refused instead of misread.
    """
    data: dict[str, dict[str, object]] = {}
    current: str | None = None
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0] in " \t":
            if current is None:
                raise ConfigError(f"line {lineno}: indented value outside a section")
            key, sep, value = line.strip().partition(":")
            if not sep or not key.strip():
                raise ConfigError(f"line {lineno}: expected 'key: value'")
            if not value.strip():
                raise ConfigError(
                    f"line {lineno}: nested mappings are outside the supported subset"
                )
            data[current][key.strip()] = _value(value.strip())
            continue
        if not line.endswith(":"):
            raise ConfigError(f"line {lineno}: expected a 'section:' header")
        current = line[:-1].strip()
        if not current:
            raise ConfigError(f"line {lineno}: empty section name")
        if current in data:
            raise ConfigError(f"line {lineno}: duplicate section {current!r}")
        data[current] = {}
    return data


def read_config(path: str | pathlib.Path) -> dict[str, dict[str, object]]:
    return parse(pathlib.Path(path).read_text(encoding="utf-8"))

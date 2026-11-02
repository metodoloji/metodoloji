"""The run specification: one TOML file that fully describes an optimization run.

The spec is deliberately small and closed. Unknown keys are an error rather
than a silently ignored typo — a misspelled ``min_improvements`` must not leave
the gate on its default while the operator believes it is tightened.

This file is the neutral middle of the three vocabularies:

* SkillOpt calls the same knobs ``epochs``, ``batch_size``, ``lr`` (a textual
  learning rate) and a held-out validation gate.
* Hermit calls the output an *adapter* with a spec, versions and a trainer.
* Metodoloji calls the result an experiment that must be measured before code.

``method`` only selects the export/proposer flavour; the kernel loop is shared.
"""
from __future__ import annotations

import dataclasses
import pathlib
import tomllib
from typing import Any

ARTIFACT_KINDS = ("skill", "adapter")
METHODS = ("skillopt", "hermit", "gepa", "manual")
_TOP_SECTIONS = {"run", "artifact", "cases", "gate"}
_KNOWN: dict[str, set[str]] = {
    "run": {
        "name", "method", "seed", "epochs", "batch_size", "val_size",
        "heldout_size", "min_cases", "slow_every", "screen_below",
    },
    "artifact": {"kind", "path", "max_tokens"},
    "cases": {"path"},
    "gate": {
        "min_improvement", "alpha", "max_edit_chars", "textual_lr",
        "max_hunks", "require_heldout",
    },
}


class SpecError(ValueError):
    """The spec cannot describe a run; refuse rather than default."""


@dataclasses.dataclass(frozen=True)
class GatePolicy:
    """How much a candidate must prove before it may become the artifact."""

    min_improvement: float = 0.0
    alpha: float = 0.05
    max_edit_chars: int = 400
    textual_lr: float = 0.20
    max_hunks: int = 3
    require_heldout: bool = True

    def __post_init__(self) -> None:
        if not 0.0 <= self.min_improvement <= 1.0:
            raise SpecError("gate.min_improvement must be within [0, 1]")
        if not 0.0 < self.alpha < 1.0:
            raise SpecError("gate.alpha must be within (0, 1)")
        if self.max_edit_chars < 1:
            raise SpecError("gate.max_edit_chars must be >= 1")
        if not 0.0 < self.textual_lr <= 1.0:
            raise SpecError("gate.textual_lr must be within (0, 1]")
        if self.max_hunks < 1:
            raise SpecError("gate.max_hunks must be >= 1")


@dataclasses.dataclass(frozen=True)
class RunSpec:
    name: str
    method: str
    seed: int
    epochs: int
    batch_size: int
    val_size: int
    heldout_size: int
    min_cases: int
    slow_every: int
    screen_below: float | None
    artifact_kind: str
    artifact_path: pathlib.Path
    max_tokens: int
    cases_path: pathlib.Path
    gate: GatePolicy
    root: pathlib.Path
    spec_path: pathlib.Path | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise SpecError("run.name is required")
        if self.method not in METHODS:
            raise SpecError(f"run.method must be one of {METHODS}, got {self.method!r}")
        if self.epochs < 1:
            raise SpecError("run.epochs must be >= 1")
        if min(self.batch_size, self.val_size) < 1:
            raise SpecError("run.batch_size and run.val_size must be >= 1")
        if self.heldout_size < 1:
            raise SpecError("run.heldout_size must be >= 1")
        if self.min_cases < 2:
            raise SpecError("run.min_cases must be >= 2 (a gate over one case is theatre)")
        if self.slow_every < 1:
            raise SpecError("run.slow_every must be >= 1")
        if self.screen_below is not None and not 0.0 <= self.screen_below <= 1.0:
            raise SpecError("run.screen_below must be within [0, 1]")
        if self.artifact_kind not in ARTIFACT_KINDS:
            raise SpecError(
                f"artifact.kind must be one of {ARTIFACT_KINDS}, got {self.artifact_kind!r}"
            )
        if self.max_tokens < 50:
            raise SpecError("artifact.max_tokens must be >= 50")

    @property
    def work_dir(self) -> pathlib.Path:
        """Where versions, state and reports live (beside the artifact)."""
        return self.artifact_path.parent / ".optimization" / self.name

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "method": self.method,
            "seed": self.seed,
            "epochs": self.epochs,
            "batch_size": self.batch_size,
            "val_size": self.val_size,
            "heldout_size": self.heldout_size,
            "min_cases": self.min_cases,
            "slow_every": self.slow_every,
            "screen_below": self.screen_below,
            "artifact_kind": self.artifact_kind,
            "artifact_path": self.artifact_path.as_posix(),
            "max_tokens": self.max_tokens,
            "cases_path": self.cases_path.as_posix(),
            "gate": dataclasses.asdict(self.gate),
        }


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise SpecError(f"[{name}] must be a table")
    unknown = set(value) - _KNOWN[name]
    if unknown:
        raise SpecError(f"[{name}] has unknown keys: {sorted(unknown)}")
    return value


def load_spec(path: str | pathlib.Path) -> RunSpec:
    path = pathlib.Path(path)
    if not path.is_file():
        raise SpecError(f"spec not found: {path}")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise SpecError(f"{path}: unparsable TOML: {exc}") from exc
    unknown = set(data) - _TOP_SECTIONS
    if unknown:
        raise SpecError(f"unknown sections: {sorted(unknown)}")

    run = _section(data, "run")
    artifact = _section(data, "artifact")
    cases = _section(data, "cases")
    gate_raw = _section(data, "gate")
    if "name" not in run:
        raise SpecError("[run] requires name")
    if "path" not in artifact or "path" not in cases:
        raise SpecError("[artifact] and [cases] both require a path")

    root = path.resolve().parent
    # Paths are relative to the spec file, never to the shell's cwd: a spec run
    # from another directory must measure the same files.
    artifact_path = (root / str(artifact["path"])).resolve()
    cases_path = (root / str(cases["path"])).resolve()

    return RunSpec(
        name=str(run["name"]),
        method=str(run.get("method", "skillopt")),
        seed=int(run.get("seed", 7)),
        epochs=int(run.get("epochs", 3)),
        batch_size=int(run.get("batch_size", 8)),
        val_size=int(run.get("val_size", 12)),
        heldout_size=int(run.get("heldout_size", 12)),
        min_cases=int(run.get("min_cases", 6)),
        slow_every=int(run.get("slow_every", 2)),
        screen_below=(None if run.get("screen_below") is None
                      else float(run["screen_below"])),
        artifact_kind=str(artifact.get("kind", "skill")),
        artifact_path=artifact_path,
        max_tokens=int(artifact.get("max_tokens", 2000)),
        cases_path=cases_path,
        gate=GatePolicy(
            min_improvement=float(gate_raw.get("min_improvement", 0.0)),
            alpha=float(gate_raw.get("alpha", 0.05)),
            max_edit_chars=int(gate_raw.get("max_edit_chars", 400)),
            textual_lr=float(gate_raw.get("textual_lr", 0.20)),
            max_hunks=int(gate_raw.get("max_hunks", 3)),
            require_heldout=bool(gate_raw.get("require_heldout", True)),
        ),
        root=root,
        spec_path=path,
    )


SPEC_TEMPLATE = """\
# Optimization spec — see docs/optimization/README.md
# Paths are relative to this file.

[run]
name = "{name}"
method = "{method}"          # skillopt | hermit | gepa | manual
seed = 7
epochs = 3
batch_size = 8
val_size = 12                # held-out validation cases per gate
heldout_size = 12            # touched only by `optimize eval`
min_cases = 6                # refuse to certify a gate over fewer pairs
slow_every = 2               # epoch-wise consolidation
# screen_below = 0.01        # skip a rollout when the predictor expects less gain

[artifact]
kind = "{kind}"              # skill | adapter
path = "{artifact}"
max_tokens = 2000            # deployed artifact budget (SkillOpt: 300-2000)

[cases]
path = "cases.jsonl"

[gate]
min_improvement = 0.0        # observed accuracy delta must clear this
alpha = 0.05                 # McNemar significance level
max_edit_chars = 400         # textual learning-rate budget, absolute
textual_lr = 0.20            # fraction of the document one edit may rewrite
max_hunks = 3                # bounded edits: at most this many changed regions
require_heldout = true       # promotion needs a second, unseen split
"""


def init_spec(directory: str | pathlib.Path, name: str = "example",
              method: str = "skillopt", kind: str = "skill") -> pathlib.Path:
    """`optimize init` — write a spec plus the artifact and case skeletons."""
    directory = pathlib.Path(directory)
    artifact_name = "SKILL.md" if kind == "skill" else "ADAPTER.md"
    spec_path = directory / "optimization.toml"
    if spec_path.exists():
        raise SpecError(f"refusing to overwrite {spec_path}")
    artifact_path = directory / artifact_name
    cases_path = directory / "cases.jsonl"
    if not artifact_path.exists():
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(
            "# Skill under optimization\n\n"
            "Start from the current hand-written version. The optimizer only\n"
            "accepts edits that survive a held-out validation gate.\n",
            encoding="utf-8",
        )
    if not cases_path.exists():
        cases_path.parent.mkdir(parents=True, exist_ok=True)
        cases_path.write_text(
            '{"id": "train-1", "input": "What does the task ask for?", '
            '"requires": ["expected token"], "split": "train"}\n',
            encoding="utf-8",
        )
    spec_path.write_text(
        SPEC_TEMPLATE.format(
            name=name, method=method, kind=kind,
            artifact=artifact_name,
        ),
        encoding="utf-8",
    )
    return spec_path

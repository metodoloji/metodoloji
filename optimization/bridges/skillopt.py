"""SkillOpt export, in the shape Hermit 0.1.0 documents.

SkillOpt inside Hermit is not a separate CLI: it is ``evolver.name: skillopt``
in the run config, it requires ``data.val``, and its defaults come from
``docs/methods/skillopt.md``. This module therefore emits the Hermit tree (from
:mod:`optimization.bridges.hermit`) with the SkillOpt parameters layered over
the documented defaults, plus the two artefacts a direct SkillOpt deployment
needs:

* ``best_skill.md`` — the exact accepted adapter text, token-budget enforced;
* ``skillopt.json`` — a machine-readable record of the parameter mapping and the
  run's gate metrics, so a reader does not have to parse YAML to see what was
  claimed.

Three mapping decisions are recorded in ``README.md`` rather than hidden:

1. ``learning_rate`` bounds *selected patches*, not characters. Our textual
   budgets (``max_edit_chars``/``textual_lr``/``max_hunks``) are enforced by the
   metodoloji loop; the Hermit template's ``format.max_bytes`` carries the hard
   file cap. ``learning_rate`` is derived from ``max_hunks`` so a step cannot
   select more patches than our loop would allow.
2. Hermit's gate is strict improvement; our ``min_improvement`` has no Hermit
   parameter. ``gate_metric: hard`` and ``use_gate: true`` are emitted, and the
   mapping records that a stricter margin is a metodoloji-only setting.
3. ``slow_update_gate`` follows ``require_heldout``: gating the epoch-end update
   is the paper's protocol and the safe choice when a held-out split exists.
"""
from __future__ import annotations

import hashlib
import json
import math
import pathlib

from .. import protected as protected_regions
from .. import skillopt_native as native
from .._io import atomic_write_text, write_json
from ..cases import Case, assign_splits, load_cases
from ..loop import VersionStore, estimate_tokens
from ..methods import MethodError, resolve, skillopt_params
from ..spec import RunSpec
from . import hermit, skillopt_env

PARAM_MAPPING = {
    "learning_rate": "gate.max_hunks (bounded patches per step)",
    "min_learning_rate": "max(1, gate.max_hunks - 1)",
    "total_steps": "epochs x ceil(train / batch_size)",
    "use_gate": "always true (strict improvement; ties reject)",
    "gate_metric": "hard (success at score >= success_score)",
    "slow_update_gate": "gate.require_heldout",
    "max_tokens": "null (SkillOpt per-call limit, not the artifact budget)",
}


def _derived_params(spec: RunSpec) -> dict[str, object]:
    cases = load_cases(spec.cases_path)
    splits = assign_splits(cases, spec.seed, spec.val_size, spec.heldout_size)
    steps_per_epoch = max(1, math.ceil(len(splits.train) / max(1, spec.batch_size)))
    learning_rate = max(1, spec.gate.max_hunks)
    return {
        "learning_rate": learning_rate,
        "min_learning_rate": max(1, learning_rate - 1),
        "total_steps": spec.epochs * steps_per_epoch,
        "use_gate": True,
        "gate_metric": "hard",
        "slow_update_gate": spec.gate.require_heldout,
        "seed": spec.seed,
    }


def export(spec: RunSpec, out_dir: str | pathlib.Path,
           store: VersionStore | None = None,
           report: dict[str, object] | None = None,
           *, lane: str = "hermit") -> dict[str, pathlib.Path]:
    """Write one of SkillOpt's two documented deployment shapes.

    ``lane="hermit"`` is SkillOpt as a Hermit Evolver: the tree Hermit's
    Trainer consumes with ``evolver.name: skillopt``. ``lane="native"`` is
    SkillOpt's own experiment bundle: a six-section ``skillopt.yaml``, the
    initial and accepted skill documents, the split hand-off and the protected
    regions its trainer owns.
    """
    if lane == "native":
        return export_native(spec, out_dir, store=store, report=report)
    if lane != "hermit":
        raise ValueError(
            f"unknown SkillOpt lane {lane!r}; expected 'hermit' or 'native'"
        )
    out = pathlib.Path(out_dir)
    store = store or VersionStore(spec.work_dir / "versions")
    rows = store.entries()
    if not rows:
        raise ValueError("no versions to export — run `python -m optimization train` first")
    artifact_text = store.artifact(str(rows[-1]["version"]))
    tokens = estimate_tokens(artifact_text)
    if tokens > spec.max_tokens:
        raise ValueError(
            f"artifact ~{tokens} tokens exceeds max_tokens={spec.max_tokens}; "
            "SkillOpt deploys a compact skill, not a transcript"
        )
    cases = load_cases(spec.cases_path)
    splits = assign_splits(cases, spec.seed, spec.val_size, spec.heldout_size)
    if not splits.val:
        raise ValueError(
            "SkillOpt declares a validation-data need; data.val is empty"
        )

    params = skillopt_params(_derived_params(spec))
    method = resolve("skillopt")

    written: dict[str, pathlib.Path] = {}
    adapter = hermit.write_adapter(spec, artifact_text, out, report)
    written["adapter"] = adapter
    written.update(hermit.write_taskset(spec, out))
    written["judge"] = hermit.write_judge(spec, out)
    written["pipeline"] = hermit.write_pipeline(spec, out)

    config = out / "run.yaml"
    atomic_write_text(
        config,
        hermit.render_run_yaml(spec, evolver="skillopt", params=params),
    )
    written["config"] = config

    skill = out / "best_skill.md"
    atomic_write_text(skill, artifact_text)
    written["artifact"] = skill

    metrics_path = spec.work_dir / "metrics.jsonl"
    rejected = []
    if metrics_path.is_file():
        for line in metrics_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if not row.get("accepted") and row.get("edit"):
                rejected.append({
                    "edit": row["edit"], "delta": row.get("delta", 0.0),
                    "reason": row.get("reason", "rejected"),
                })
    buffer_path = out / "rejected_edits.jsonl"
    atomic_write_text(buffer_path,
                      "".join(json.dumps(r, sort_keys=True) + "\n" for r in rejected))
    written["rejected_edits"] = buffer_path

    manifest = out / "skillopt.json"
    write_json(manifest, {
        "artifact": "best_skill.md",
        "artifact_sha256": __import__("hashlib").sha256(
            artifact_text.encode("utf-8")).hexdigest(),
        "artifact_tokens": tokens,
        "evolver": "skillopt",
        "template": method.template,
        "template_directory": "adapter",
        "needs_validation": method.needs_validation,
        "params": params,
        "mapping": PARAM_MAPPING,
        "splits": splits.size(),
        "heldout": (report or {}).get("heldout"),
    })
    written["manifest"] = manifest

    readme = out / "README.md"
    atomic_write_text(readme, (
        f"# SkillOpt export — {spec.name}\n\n"
        f"- Artifact: `best_skill.md` (~{tokens} tokens), byte-identical to "
        "`adapter/ADAPTER.md`\n"
        f"- Evolver: `skillopt` on template `{method.template}`\n"
        f"- Splits: train {len(splits.train)} / val {len(splits.val)} / "
        f"test {len(splits.heldout)}\n\n"
        "```bash\n"
        "hermit check --config run.yaml\n"
        "hermit train --config run.yaml\n"
        "hermit eval  --config run.yaml --adapter final\n"
        "```\n\n"
        "## Parameter mapping\n\n"
        "| Hermit/SkillOpt | Source |\n|---|---|\n"
        + "\n".join(f"| `{key}` | {value} |" for key, value in PARAM_MAPPING.items())
        + "\n\n`textual_lr` and `max_edit_chars` are metodoloji's textual "
        "learning-rate budgets; Hermit has no equivalent parameter, so they stay "
        "enforced by this kernel and are not restated in `evolver.params`. "
        "`params.max_tokens` is SkillOpt's per-call model limit and is left null; "
        "the artifact budget is enforced here and by `format.max_bytes`.\n\n"
        "`rejected_edits.jsonl` is negative feedback for the next round, not a "
        "training set. The test split was never read by the loop.\n"
    ))
    written["readme"] = readme
    return written


NATIVE_MAPPING = {
    "train.num_epochs": "run.epochs",
    "train.batch_size": "run.batch_size",
    "train.seed": "run.seed",
    "gradient.minibatch_size": "min(8, run.batch_size)",
    "optimizer.learning_rate": "gate.max_hunks (maximum patches per step)",
    "optimizer.min_learning_rate": "max(1, learning_rate - 1)",
    "optimizer.slow_update_samples": "len(val)",
    "optimizer.slow_update_gate_with_selection": "gate.require_heldout",
    "evaluation.sel_env_num": "len(val)",
    "evaluation.test_env_num": "len(heldout)",
    "env.split_ratio": "len(train):len(val):len(heldout)",
    "env.split_seed": "run.seed",
    "env.skill_init": "skills/skill_v0000.md (the version the run started from)",
}

NATIVE_UNMAPPED = {
    "gate.max_edit_chars": (
        "SkillOpt bounds patches, not characters; the kernel enforces the "
        "absolute budget before a candidate is measured"
    ),
    "gate.textual_lr": (
        "No SkillOpt field takes a document-fraction budget; it stays enforced "
        "here and is not restated in skillopt.yaml"
    ),
    "gate.min_improvement": (
        "SkillOpt gates strict improvement; a larger margin is metodoloji-only"
    ),
    "gate.alpha": (
        "SkillOpt does not expose a significance level; this kernel's McNemar "
        "gate keeps alpha at the run level"
    ),
}


def _native_row(case: Case, split: str) -> dict[str, object]:
    return {
        "id": case.id,
        "split": split,
        "question": case.input,
        "requires": list(case.requires),
        "expected": case.expected,
    }


def _data_readme(spec: RunSpec) -> str:
    return (
        f"# Data hand-off — {spec.name}\n\n"
        "`cases.jsonl` is the evaluation dataset, not optimizer input: the "
        "learner never sees these rows. Each row is\n\n"
        "```json\n"
        '{"id": "...", "split": "train|valid_seen|valid_unseen", '
        '"question": "...", "requires": ["token"], "expected": "..."}\n'
        "```\n\n"
        "`data/splits/` carries the same rows per split, named after the "
        "`--split` values `skillopt-eval` documents (`train`, `valid_seen`, "
        "`valid_unseen`). `env.split_mode` stays `ratio` because a `split_dir` "
        "must be materialized by the benchmark adapter, whose file contract is "
        "the adapter's, not this exporter's.\n"
    )


def _native_readme(spec: RunSpec, tokens: int, env_name: str) -> str:
    return (
        f"# SkillOpt native export — {spec.name}\n\n"
        f"- Artifact: `best_skill.md` (~{tokens} tokens), byte-identical to the "
        "accepted version this repository measured\n"
        "- Initial skill: `skills/skill_v0000.md` (`env.skill_init`)\n"
        "- Config: `skillopt.yaml` — the six documented sections only\n"
        f"- Environment: `env/{skillopt_env.package_name(env_name)}/` — the "
        "adapter that makes `env.name` resolvable, registered by "
        f"`{skillopt_env.RUNNER}`\n\n"
        "```bash\n"
        "python run_skillopt.py train --config skillopt.yaml\n"
        "python run_skillopt.py eval --config skillopt.yaml "
        "--skill best_skill.md --split valid_unseen\n"
        "python run_skillopt.py eval --config skillopt.yaml "
        "--skill best_skill.md --split valid_seen\n"
        "```\n\n"
        "The runner registers the bundle's adapter in SkillOpt's own "
        "`_ENV_REGISTRY` and hands the rest of the command line to "
        "`scripts.train.main()` / `scripts.eval_only.main()` unchanged; no file "
        "of the installed package is edited. `skillopt-train --config "
        "skillopt.yaml` directly still refuses `env.name`, because nothing has "
        "registered the adapter in that process. Overrides use `--cfg-options "
        "section.key=value`, as the CLI reference documents.\n\n"
        "## The environment adapter\n\n"
        "`env/<name>/dataloader.py` reads `data/cases.jsonl` (or a split "
        "directory), `rollout.py` asks the configured target backend through "
        "`skillopt.model.chat_target` and scores each answer with the same rule "
        "as this run's `judge.py` (every required token present; exact match "
        "when a reference exists), and `adapter.py` wires both into the four "
        "`EnvAdapter` methods. Each rollout writes "
        "`predictions/<id>/conversation.json`, which the inherited `reflect()` "
        "reads.\n\n"
        "Point it at an OpenAI-compatible server without editing the file:\n\n"
        "```bash\n"
        "export OPENAI_COMPATIBLE_BASE_URL=http://127.0.0.1:8000/v1\n"
        "export OPENAI_COMPATIBLE_API_KEY=...\n"
        "export OPENAI_COMPATIBLE_MODEL=...\n"
        "python run_skillopt.py train --config skillopt.yaml \\\n"
        "    --cfg-options model.optimizer_backend=openai_compatible \\\n"
        "                  model.target_backend=openai_compatible \\\n"
        "                  model.optimizer=... model.target=...\n"
        "```\n\n"
        "`model.optimizer` and `model.target` are the two deployment ids, not "
        "free parameters: the file carries SkillOpt's documented defaults "
        "(`gpt-5.5`) and a different deployment must name its own model on both "
        "roles, or the first call fails even though the backend is "
        "`openai_compatible`.\n\n"
        "## What carries over, and what does not\n\n"
        "| SkillOpt field | Source |\n|---|---|\n"
        + "\n".join(f"| `{key}` | {value} |" for key, value in NATIVE_MAPPING.items())
        + "\n\n"
        "The kernel's textual budgets have no SkillOpt field, so they stay "
        "enforced here:\n\n"
        + "\n".join(f"- `{key}` — {value}" for key, value in NATIVE_UNMAPPED.items())
        + "\n\n## Protected regions\n\n"
        "`<!-- SLOW_UPDATE_START -->` … `<!-- SLOW_UPDATE_END -->` and\n"
        "`<!-- APPENDIX_START -->` … `<!-- APPENDIX_END -->` are written by\n"
        "SkillOpt's slow update and appendix consolidation. A patch that edits\n"
        "inside one, deletes a marker or invents a region is refused here before\n"
        "it is measured; `skillopt.json` records which regions the artifact has.\n\n"
        "## What this is not\n\n"
        "The adapter is a faithful, small harness for *these* cases: one target "
        "call, token scoring, a persisted conversation. It is not one of "
        "SkillOpt's published benchmarks, and its numbers are not comparable to "
        "the paper's. Scoring is deliberately the same rule as this run's "
        "`judge.py`, so a native run and the metodoloji gate agree on what a "
        "passing answer is.\n"
    )


def export_native(spec: RunSpec, out_dir: str | pathlib.Path,
                  store: VersionStore | None = None,
                  report: dict[str, object] | None = None) -> dict[str, pathlib.Path]:
    """SkillOpt's own bundle: config, skills, data hand-off, manifest."""
    out = pathlib.Path(out_dir)
    store = store or VersionStore(spec.work_dir / "versions")
    rows = store.entries()
    if not rows:
        raise ValueError("no versions to export — run `python -m optimization train` first")
    artifact_text = store.artifact(str(rows[-1]["version"]))
    tokens = estimate_tokens(artifact_text)
    if tokens > spec.max_tokens:
        raise ValueError(
            f"artifact ~{tokens} tokens exceeds max_tokens={spec.max_tokens}; "
            "SkillOpt deploys a compact skill, not a transcript"
        )
    initial_text = store.artifact(str(rows[0]["version"]))
    cases = load_cases(spec.cases_path)
    splits = assign_splits(cases, spec.seed, spec.val_size, spec.heldout_size)
    if not splits.val:
        raise ValueError("SkillOpt declares a validation-data need; data.val is empty")

    env_name = hermit.slug(spec.name)
    config = native.build_config(spec, splits, env_name=env_name)
    problems = native.validate_config(config, require_runnable=True)
    if problems:
        raise ValueError("native SkillOpt config invalid: " + "; ".join(problems))

    written: dict[str, pathlib.Path] = {}
    config_path = out / native.CONFIG_FILE
    atomic_write_text(config_path, native.render(config))
    written["config"] = config_path

    skill = out / "best_skill.md"
    atomic_write_text(skill, artifact_text)
    written["artifact"] = skill
    initial = out / "skills" / "skill_v0000.md"
    atomic_write_text(initial, initial_text)
    written["initial_skill"] = initial

    groups = (("train", splits.train), ("valid_seen", splits.val),
              ("valid_unseen", splits.heldout))
    combined = []
    for name, group in groups:
        payload = "".join(
            json.dumps(_native_row(case, name), sort_keys=True) + "\n"
            for case in group
        )
        path = out / "data" / "splits" / f"{name}.jsonl"
        atomic_write_text(path, payload)
        written[f"split_{name}"] = path
        combined.append(payload)
    data = out / "data" / "cases.jsonl"
    atomic_write_text(data, "".join(combined))
    atomic_write_text(out / "data" / "README.md", _data_readme(spec))
    written["data"] = data

    # The environment adapter, so `skillopt-train` can resolve `env.name` without an installed benchmark package.
    written.update(skillopt_env.write(spec, out, env_name))
    written["env_adapter"] = out / "env" / skillopt_env.package_name(env_name) / "adapter.py"
    written["runner"] = out / skillopt_env.RUNNER

    manifest = out / "skillopt.json"
    write_json(manifest, {
        "lane": "native",
        "upstream": "microsoft/SkillOpt",
        "config": native.CONFIG_FILE,
        "artifact": "best_skill.md",
        "artifact_sha256": hashlib.sha256(artifact_text.encode("utf-8")).hexdigest(),
        "artifact_tokens": tokens,
        "max_tokens": spec.max_tokens,
        "initial_skill": "skills/skill_v0000.md",
        "splits": {"train": len(splits.train), "valid_seen": len(splits.val),
                   "valid_unseen": len(splits.heldout)},
        "eval_splits": ["valid_seen", "valid_unseen"],
        "protected_regions": [region.name for region in
                             protected_regions.regions(artifact_text)],
        "env_adapter": {
            "env_name": env_name,
            "package": f"env/{skillopt_env.package_name(env_name)}",
            "adapter": f"{skillopt_env.class_prefix(env_name)}Adapter",
            "runner": skillopt_env.RUNNER,
        },
        "mapping": NATIVE_MAPPING,
        "unmapped": NATIVE_UNMAPPED,
        "heldout": (report or {}).get("heldout"),
    })
    written["manifest"] = manifest

    readme = out / "README.md"
    atomic_write_text(readme, _native_readme(spec, tokens, env_name))
    written["readme"] = readme
    return written


def _validate_native(out: pathlib.Path) -> list[str]:
    problems: list[str] = []
    try:
        data = native.read_config(out / native.CONFIG_FILE)
    except (OSError, ValueError) as exc:
        return [f"{native.CONFIG_FILE}: {exc}"]
    problems += [f"{native.CONFIG_FILE}: {problem}" for problem in
                 native.validate_config(data, require_runnable=True)]
    env = data.get("env") if isinstance(data.get("env"), dict) else {}
    for key in ("skill_init", "data_path"):
        value = env.get(key)
        if isinstance(value, str) and value and not (out / value).is_file():
            problems.append(f"env.{key} points at a missing file: {value}")

    skill = out / "best_skill.md"
    if not skill.is_file():
        problems.append("missing best_skill.md")
    else:
        text = skill.read_text(encoding="utf-8")
        problems += [f"best_skill.md: {problem}" for problem in
                     protected_regions.validate(text)]
        manifest = out / "skillopt.json"
        if not manifest.is_file():
            problems.append("missing skillopt.json")
        else:
            record = json.loads(manifest.read_text(encoding="utf-8"))
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if record.get("artifact_sha256") != digest:
                problems.append(
                    "best_skill.md sha256 differs from the recorded measurement"
                )
            if record.get("lane") != "native":
                problems.append("skillopt.json does not declare the native lane")
            if record.get("artifact") != "best_skill.md":
                problems.append("skillopt.json does not name best_skill.md")

    # The environment adapter: the bundle is only runnable if `env.name` resolves and the registered class exists.
    env_name = env.get("name") if isinstance(env.get("name"), str) else ""
    package = skillopt_env.package_name(env_name)
    module = out / skillopt_env.PACKAGE / package
    for name in ("__init__.py", "adapter.py", "dataloader.py", "rollout.py"):
        if not (module / name).is_file():
            problems.append(f"missing env/{package}/{name} (the adapter `env.name` needs)")
    runner = out / skillopt_env.RUNNER
    if not runner.is_file():
        problems.append(f"missing {skillopt_env.RUNNER} (registers the adapter for skillopt-train)")
    else:
        text = runner.read_text(encoding="utf-8")
        if env_name and f'ENV_NAME = "{env_name}"' not in text:
            problems.append(f"{skillopt_env.RUNNER} registers a different env.name than {env_name!r}")
    adapter = module / "adapter.py"
    if adapter.is_file() and "EnvAdapter" not in adapter.read_text(encoding="utf-8"):
        problems.append("env adapter does not subclass EnvAdapter")

    if not (out / "skills" / "skill_v0000.md").is_file():
        problems.append("missing skills/skill_v0000.md (env.skill_init target)")
    for name in ("train", "valid_seen", "valid_unseen"):
        if not (out / "data" / "splits" / f"{name}.jsonl").is_file():
            problems.append(f"missing data/splits/{name}.jsonl")

    readme = out / "README.md"
    if not readme.is_file():
        problems.append("missing README.md")
    else:
        text = readme.read_text(encoding="utf-8")
        for split in ("valid_seen", "valid_unseen"):
            if f"--split {split}" not in text:
                problems.append(f"README.md does not show `--split {split}`")
    return problems


def validate(out_dir: str | pathlib.Path) -> list[str]:
    out = pathlib.Path(out_dir)
    if (out / native.CONFIG_FILE).is_file():
        return _validate_native(out)
    problems = hermit.validate(out)
    config = out / "run.yaml"
    if config.is_file():
        text = config.read_text(encoding="utf-8")
        sections = hermit._sections(text)
        if 'name: "skillopt"' not in text and "name: skillopt" not in text:
            problems.append("evolver.name is not skillopt")
        lines = sections.get("evolver", [])
        params_keys = set()
        in_params = False
        for line in lines:
            if line.strip() == "params:":
                in_params = True
                continue
            if in_params:
                if line.startswith("    ") and not line.startswith("     "):
                    match = __import__("re").match(r"\s*([A-Za-z_][\w-]*):", line)
                    if match:
                        params_keys.add(match.group(1))
                elif not line.startswith("    "):
                    in_params = False
        try:
            required = set(resolve("skillopt").params)
        except MethodError as exc:
            problems.append(str(exc))
            required = set()
        missing = sorted(required - params_keys)
        if missing:
            problems.append(f"evolver.params is missing documented keys {missing}")
        if "val:" not in "\n".join(sections.get("data", [])):
            problems.append("data.val is missing (SkillOpt declares validation_data)")
    skill = out / "best_skill.md"
    adapter = out / "adapter" / "ADAPTER.md"
    if not skill.is_file():
        problems.append("missing best_skill.md")
    elif adapter.is_file() and skill.read_text(encoding="utf-8") != adapter.read_text(encoding="utf-8"):
        problems.append("best_skill.md differs from the measured adapter/ADAPTER.md")
    manifest = out / "skillopt.json"
    if not manifest.is_file():
        problems.append("missing skillopt.json")
    else:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if data.get("evolver") != "skillopt":
            problems.append("skillopt.json does not name the skillopt evolver")
        if not data.get("needs_validation"):
            problems.append("skillopt.json does not declare its validation need")
    return problems


def ingest(path: str | pathlib.Path) -> dict[str, object]:
    """Read a SkillOpt/Hermit result JSON without assuming its full schema."""
    from .._io import read_json

    data = read_json(pathlib.Path(path))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    metrics = data.get("metrics") if isinstance(data.get("metrics"), dict) else data
    result: dict[str, object] = {"source": "skillopt", "path": str(path)}
    for key in ("accuracy", "val_accuracy", "best_score", "baseline", "final"):
        if key in metrics:
            result[key] = metrics[key]
    result["keys"] = sorted(metrics)[:20]
    return result

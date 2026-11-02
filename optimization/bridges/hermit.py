"""Hermit 0.1.0 export: the documented adapter tree and run configuration.

The layout below is taken from Hermit's own docs and examples, not guessed:

    <out>/
      run.yaml                  template, evolver, agent, judge, data, trainer
      tasks.jsonl               {"id","prompt"} rows only — labels live in judge.py
      train.txt val.txt test.txt  one task id per line
      task-info/README.md       learner-facing task description
      judge.py                  score(task, RunOutput) -> JudgeResult
      pipeline.py               run(task, adapter) -> RunOutput
      adapter/
        ADAPTER.md              the acting context (the measured artifact)
        exports.yaml            {"context": "ADAPTER.md"}
        README.md               fixed
        .hermit/adapter.yaml    template identity + structure (contract "2")
        .hermit/EVOLVER.md      learner guidance, never exported
        .hermit/CARD.md         provenance and recorded evaluation

Two documented rules drive the shape:

* **Labels never travel in task rows.** ``tasks.jsonl`` carries ``id`` and
  ``prompt`` only; the reference label lives in ``judge.py``, exactly as
  ``docs/_examples/first_model`` does. Task rows are learning material.
* **The template's ``structure`` decides what a learner may touch.** The
  export lists ``ADAPTER.md`` as learnable with a byte cap and fixes
  ``README.md``; every file outside ``.hermit/`` is covered by one or the other,
  which the specification check requires.

The exporter stays inside the documented schema: unknown top-level framework
keys are rejected by Hermit, so ``validate`` refuses any key this module does not
know rather than shipping a config that fails at startup.
"""
from __future__ import annotations

import json
import pathlib
import re

from .._io import atomic_write_text
from ..cases import assign_splits, load_cases
from ..loop import VersionStore, estimate_tokens
from ..methods import MethodError, resolve
from ..spec import RunSpec

# Documented run-config sections; anything else is a schema mistake.
RUN_SECTIONS = {
    "data", "template", "start_from", "evolver", "agent", "judge", "llm",
    "sandbox", "trainer", "eval", "model", "backends", "task_info",
    "evolver_access", "pipeline",
}
REQUIRED_SECTIONS = {"data", "template", "evolver", "agent", "judge", "trainer"}
DATA_KEYS = {"tasks", "train", "val", "test", "step_size", "shuffle",
             "shuffle_seed"}
TEMPLATE_SECTIONS = {"template", "structure", "check", "evolver_tools",
                     "plugin-portable"}
SPLIT_FILES = {"train": "train.txt", "val": "val.txt", "test": "test.txt"}


def slug(name: str) -> str:
    """A valid template id: ``[a-z][a-z0-9-]{0,63}``."""
    value = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
    value = re.sub(r"-+", "-", value)
    if not value or not value[0].isalpha():
        value = f"t-{value or 'adapter'}"
    value = value[:64].rstrip("-")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", value):
        raise ValueError(f"cannot derive a valid template id from {name!r}")
    return value


def run_name(name: str) -> str:
    """Hemrit's run-name rule: alphanumeric start, [\\w.-], <= 81 chars."""
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-")[:81]
    if not value or not value[0].isalnum():
        value = f"run-{value or 'adapter'}"
    return value[:81]


def _scalar(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    text = str(value)
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _mapping(values: dict[str, object], indent: int) -> list[str]:
    pad = " " * indent
    return [f"{pad}{key}: {_scalar(value)}" for key, value in values.items()]


def write_taskset(spec: RunSpec, out: pathlib.Path) -> dict[str, pathlib.Path]:
    """tasks.jsonl plus the three documented split files and task-info."""
    cases = load_cases(spec.cases_path)
    splits = assign_splits(cases, spec.seed, spec.val_size, spec.heldout_size)
    tasks = out / "tasks.jsonl"
    written: dict[str, pathlib.Path] = {"tasks": tasks}
    rows = [{"id": case.id, "prompt": case.input}
            for case in splits.all_cases()]
    atomic_write_text(tasks, "".join(json.dumps(r, sort_keys=True) + "\n"
                                     for r in rows))
    for name, group in (("train", splits.train), ("val", splits.val),
                        ("test", splits.heldout)):
        path = out / SPLIT_FILES[name]
        atomic_write_text(path, "".join(f"{case.id}\n" for case in group))
        written[name] = path
    info = out / "task-info" / "README.md"
    atomic_write_text(info, (
        f"# Task: {spec.name}\n\n"
        "Answer each prompt in one turn. The acting agent receives the adapter "
        "context as system instructions.\n\n"
        f"- Training tasks: {len(splits.train)}\n"
        f"- Validation tasks: {len(splits.val)} (used by the validation gate)\n"
        f"- Test tasks: {len(splits.heldout)}\n\n"
        "`tasks.jsonl` carries only `id` and `prompt`. The reference labels are "
        "held by `judge.py`, outside the learner-visible task rows.\n\n"
        "The judge checks that an answer contains every required token and, "
        "when a reference answer exists, matches it exactly.\n"
    ))
    written["task_info"] = info
    return written


def write_judge(spec: RunSpec, out: pathlib.Path) -> pathlib.Path:
    cases = load_cases(spec.cases_path)
    labels = {case.id: {"requires": list(case.requires), "expected": case.expected}
              for case in cases}
    source = f'''"""Scoring module for the Hermit export of {spec.name!r}.

Reference labels live here, not in tasks.jsonl: task rows are learning
material. `score` returns a JudgeResult with a hard success flag and
human-readable feedback, which is what the Hermit SkillOpt integration turns
into `score >= success_score`.
"""
import json

from hermit.task import JudgeResult, RunOutput

LABELS = {labels!r}


def score(task: dict, output: RunOutput) -> JudgeResult:
    label = LABELS[task["id"]]
    text = output.output if isinstance(output.output, str) else json.dumps(
        output.output, sort_keys=True)
    required = label.get("requires") or []
    missing = [token for token in required if token not in text]
    expected = label.get("expected")
    exact = expected is None or text.strip() == expected.strip()
    ok = not missing and exact
    if ok:
        feedback = "Correct."
    elif missing:
        feedback = "The answer must contain: " + ", ".join(missing) + "."
    else:
        feedback = "The answer must match the reference exactly."
    return JudgeResult(score=float(ok), feedback=feedback)
'''
    path = out / "judge.py"
    atomic_write_text(path, source)
    return path


def write_pipeline(spec: RunSpec, out: pathlib.Path) -> pathlib.Path:
    source = f'''"""Acting pipeline for the Hermit export of {spec.name!r}.

The agent reads the adapter context through `Session(adapter).context()`, calls
a chat endpoint, and returns a `RunOutput`. The acting model is separate from
the learner's `llm` section, as Hermit documents.
"""
import os
from pathlib import Path

from hermit import Session
from hermit.agent.model.llm import ChatClient
from hermit.task import RunOutput


def run(task: dict, adapter: Path | None) -> RunOutput:
    client = ChatClient(
        os.environ.get("ACTOR_BASE_URL") or os.environ["MODEL_BASE_URL"],
        os.environ.get("ACTOR_MODEL") or os.environ["MODEL_NAME"],
        api_key_env=("ACTOR_API_KEY" if os.environ.get("ACTOR_API_KEY")
                     else "MODEL_API_KEY"),
    )
    context = Session(adapter).context() if adapter is not None else ""
    messages = [
        {{"role": "system", "content": "Complete the user's request.\\n" + context}},
        {{"role": "user", "content": task["prompt"]}},
    ]
    reply, usage = client.chat(messages, max_tokens=1024, temperature=0.0)
    answer = reply.get("content")
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("Actor returned no text; inspect endpoint and model settings")
    return RunOutput(
        output=answer,
        messages=messages + [{{"role": "assistant", "content": answer}}],
        tokens=usage.input_tokens + usage.output_tokens,
        cost_usd=usage.cost_usd,
    )
'''
    path = out / "pipeline.py"
    atomic_write_text(path, source)
    return path


def render_adapter_yaml(spec: RunSpec, template_id: str) -> str:
    max_bytes = spec.max_tokens * 4
    return "\n".join([
        "# Template metadata (Hermit adapter metadata contract 2).",
        "template:",
        f"  id: {template_id}",
        '  version: "1.0"',
        f"  name: {_scalar(spec.name)}",
        '  contract: "2"',
        "",
        "structure:",
        "  learnable:",
        "    - exports.yaml",
        "    - path: ADAPTER.md",
        f"      format: {{text: true, max_bytes: {max_bytes}}}",
        "  fixed:",
        "    - README.md",
    ]) + "\n"


def render_run_yaml(spec: RunSpec, *, evolver: str,
                    params: dict[str, object] | None = None,
                    template: str = "./adapter") -> str:
    method = resolve(evolver)
    lines = [
        "# Generated by `python -m optimization export --target hermit`.",
        "# Hermit 0.1.0 run configuration; paths are relative to this file.",
        "data:",
        '  tasks: "tasks.jsonl"',
        '  train: "train.txt"',
        '  val: "val.txt"',
        '  test: "test.txt"',
        f"  step_size: {spec.batch_size}",
        "  shuffle: false",
        f"  shuffle_seed: {spec.seed}",
        "",
        'task_info: "task-info"',
        "",
        f"template: {_scalar(template)}",
        "",
        "evolver:",
        f"  name: {_scalar(evolver)}",
    ]
    if params:
        lines.append("  params:")
        lines.extend(_mapping(params, 4))
    lines += [
        "",
        "agent:",
        '  name: "pipeline.py:run"',
        "  concurrency: 1",
        "",
        "judge:",
        '  path: "judge.py"',
        '  name: "score"',
        "",
    ]
    if method.needs_llm:
        lines += [
            "llm:",
            '  base_url: "https://your-endpoint.example/v1"',
            '  model: "your-model-id"',
            '  api_key_env: "MODEL_API_KEY"',
            "",
        ]
    lines += [
        "sandbox:",
        "  enabled: true",
        # Hermit's default is `required`: it refuses to run without bubblewrap, which is Linux-only. A generated
        # config has to start on the host that runs it, so it records an unconfined run instead of refusing.
        '  enforce: "best_effort"',
        '  network: "isolated"',
        "  allow: []",
        "  read: []",
        # null passes the environment on, which is Hermit's own default and what the acting pipeline needs to reach
        # its endpoint (MODEL_BASE_URL / MODEL_API_KEY). An empty list would hand the worker no credentials at all.
        "  env: null",
        "",
        "trainer:",
        f"  run_name: {_scalar(run_name(spec.name))}",
        '  output_dir: "runs"',
        f"  tree_dir: {_scalar('runs/trees/' + slug(spec.name))}",
        f"  epochs: {spec.epochs}",
        "  budget:",
        "    tokens: 200000",
        "    hours: 2",
        "  call_timeout_min: 15",
        '  resume_mode: "auto"',
        "",
        "eval:",
        "  repeats: 1",
    ]
    return "\n".join(lines) + "\n"


def write_adapter(spec: RunSpec, artifact_text: str, out: pathlib.Path,
                  report: dict[str, object] | None) -> pathlib.Path:
    adapter = out / "adapter"
    atomic_write_text(adapter / "ADAPTER.md", artifact_text)
    template_id = slug(spec.name)
    atomic_write_text(adapter / ".hermit" / "adapter.yaml",
                      render_adapter_yaml(spec, template_id))
    atomic_write_text(adapter / ".hermit" / "EVOLVER.md", (
        "# Editable files\n\n"
        "You may edit `ADAPTER.md` and, within its limits, the export "
        "declarations in `exports.yaml` (for example the context entry).\n"
        f"Keep every edited file UTF-8 and at most {spec.max_tokens * 4} bytes; "
        "the specification check enforces the template's `structure`.\n"
        "`README.md` is fixed. Do not edit anything under `.hermit/`.\n"
    ))
    atomic_write_text(adapter / "exports.yaml", 'context: ADAPTER.md\n')
    atomic_write_text(adapter / "README.md", (
        f"# {spec.name} harness adapter\n\n"
        "Generated by the metodoloji optimizer from a measured version tree.\n\n"
        "- `ADAPTER.md` — the acting context; byte-identical to the accepted version\n"
        "- `exports.yaml` — what the acting agent receives (the context file)\n"
        "- `.hermit/adapter.yaml` — template identity and the editable structure\n"
        "- `.hermit/CARD.md` — provenance and recorded evaluation\n\n"
        "Run `hermit check --config ../run.yaml` before training, then\n"
        "`hermit train --config ../run.yaml` and\n"
        "`hermit eval --config ../run.yaml --adapter final`.\n"
    ))
    atomic_write_text(adapter / ".hermit" / "CARD.md",
                      render_card(spec, report, artifact_text))
    return adapter


def render_card(spec: RunSpec, report: dict[str, object] | None,
                artifact_text: str) -> str:
    report = report or {}
    val = report.get("val") or {}
    heldout = report.get("heldout") or {}
    return "\n".join([
        "# Adapter card",
        "",
        f"- **Run:** {spec.name}",
        f"- **Method:** {spec.method}",
        f"- **Artifact:** {spec.artifact_kind}",
        f"- **Seed:** {spec.seed}",
        f"- **Artifact sha256:** {__import__('hashlib').sha256(artifact_text.encode()).hexdigest()}",
        f"- **Artifact tokens (est.):** {estimate_tokens(artifact_text)}",
        f"- **Validation rate:** {val.get('rate', 'unmeasured')} (n={val.get('n', 0)})",
        f"- **Held-out promotion:** {heldout.get('accepted', 'not run')} — "
        f"{heldout.get('reason', '')}".rstrip(" —"),
        f"- **Prediction MAE:** {report.get('prediction_mae', 'not measured')}",
        f"- **Gate:** min_improvement={spec.gate.min_improvement}, "
        f"alpha={spec.gate.alpha}, budget={spec.gate.max_edit_chars} chars / "
        f"{spec.gate.textual_lr:.0%}",
        "",
        "The card records observed numbers, not intentions. A candidate that the",
        "gate rejected is not described here as a change.",
    ]) + "\n"


def export(spec: RunSpec, out_dir: str | pathlib.Path,
           store: VersionStore | None = None,
           report: dict[str, object] | None = None,
           *, template_id: str | None = None) -> dict[str, pathlib.Path]:
    out = pathlib.Path(out_dir)
    store = store or VersionStore(spec.work_dir / "versions")
    rows = store.entries()
    if not rows:
        raise ValueError("no versions to export — run `python -m optimization train` first")
    artifact_text = store.artifact(str(rows[-1]["version"]))
    tokens = estimate_tokens(artifact_text)
    if tokens > spec.max_tokens:
        raise ValueError(f"artifact ~{tokens} tokens exceeds max_tokens={spec.max_tokens}")
    method = resolve(spec.method)

    written: dict[str, pathlib.Path] = {}
    adapter = write_adapter(spec, artifact_text, out, report)
    written["adapter"] = adapter
    written.update(write_taskset(spec, out))
    written["judge"] = write_judge(spec, out)
    written["pipeline"] = write_pipeline(spec, out)
    params = dict(method.params)
    config = out / "run.yaml"
    atomic_write_text(config, render_run_yaml(spec, evolver=spec.method,
                                              params=params))
    written["config"] = config
    readme = out / "README.md"
    atomic_write_text(readme, (
        f"# Hermit export — {spec.name}\n\n"
        "```bash\n"
        "hermit check --config run.yaml\n"
        "hermit train --config run.yaml\n"
        "hermit eval  --config run.yaml --adapter final\n"
        "hermit deploy --adapter adapter --workspace ./workspace --host codex\n"
        "```\n\n"
        "The run config follows the documented Hermit 0.1.0 fields; confirm them\n"
        "against `hermit --version` if the installed release differs. The adapter\n"
        "is the version the validation gate accepted, so the recorded rate\n"
        "describes these exact bytes.\n"
    ))
    written["readme"] = readme
    return written


# --- validation -------------------------------------------------------------

def _sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if not raw.startswith((" ", "\t")):
            match = re.match(r"([A-Za-z_][\w-]*):", raw)
            if match:
                current = match.group(1)
                sections[current] = []
            else:
                current = None
        elif current is not None:
            sections[current].append(raw)
    return sections


def _child_keys(lines: list[str], indent: int) -> list[str]:
    keys = []
    prefix = " " * indent
    for line in lines:
        if not line.startswith(prefix) or line.startswith(prefix + " "):
            continue
        match = re.match(r"\s*([A-Za-z_][\w-]*):", line)
        if match:
            keys.append(match.group(1))
    return keys


def validate(out_dir: str | pathlib.Path) -> list[str]:
    """Check the export against Hermit's documented contract."""
    out = pathlib.Path(out_dir)
    problems: list[str] = []
    config = out / "run.yaml"
    if not config.is_file():
        return ["missing run.yaml"]
    text = config.read_text(encoding="utf-8")
    sections = _sections(text)
    unknown = sorted(set(sections) - RUN_SECTIONS)
    if unknown:
        problems.append(f"run.yaml has unknown top-level sections {unknown}")
    missing = sorted(REQUIRED_SECTIONS - set(sections))
    if missing:
        problems.append(f"run.yaml is missing sections {missing}")
    data_keys = _child_keys(sections.get("data", []), 2)
    unknown_data = sorted(set(data_keys) - DATA_KEYS)
    if unknown_data:
        problems.append(f"data has unknown keys {unknown_data}")
    for key in ("tasks", "train", "val", "test", "step_size"):
        if key not in data_keys:
            problems.append(f"data.{key} is missing")
    if "task_info" not in sections:
        problems.append("task_info is missing (it is a top-level key, not data.task_info)")

    # tasks.jsonl: id + prompt only, unique ids, and the splits must resolve.
    tasks_path = out / "tasks.jsonl"
    ids: list[str] = []
    if not tasks_path.is_file():
        problems.append("missing tasks.jsonl")
    else:
        for lineno, line in enumerate(tasks_path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                problems.append(f"tasks.jsonl:{lineno} is not JSON")
                continue
            if set(row) != {"id", "prompt"}:
                problems.append(
                    f"tasks.jsonl:{lineno} keys {sorted(row)} — task rows must carry "
                    "only id and prompt (labels belong to judge.py)"
                )
            ids.append(str(row.get("id")))
        if len(ids) != len(set(ids)):
            problems.append("tasks.jsonl has duplicate ids")
    split_ids: dict[str, list[str]] = {}
    for name, filename in SPLIT_FILES.items():
        path = out / filename
        if not path.is_file():
            problems.append(f"missing {filename}")
            continue
        values = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
                  if line.strip()]
        split_ids[name] = values
        if not values:
            problems.append(f"{filename} is empty")
        unknown_ids = sorted(set(values) - set(ids))
        if unknown_ids:
            problems.append(f"{filename} names ids not in tasks.jsonl: {unknown_ids[:5]}")
    if all(name in split_ids for name in ("train", "val", "test")):
        seen: dict[str, str] = {}
        for name, values in split_ids.items():
            for value in values:
                if value in seen:
                    problems.append(f"{value!r} is in both {seen[value]} and {name}")
                seen[value] = name

    # Adapter metadata: identity, structure, exports, guide.
    adapter = out / "adapter"
    if not (adapter / "ADAPTER.md").is_file():
        problems.append("missing adapter/ADAPTER.md")
    elif not (adapter / "ADAPTER.md").read_text(encoding="utf-8").strip():
        problems.append("adapter/ADAPTER.md is empty")
    manifest = adapter / ".hermit" / "adapter.yaml"
    if not manifest.is_file():
        problems.append("missing adapter/.hermit/adapter.yaml")
    else:
        meta = _sections(manifest.read_text(encoding="utf-8"))
        unknown_meta = sorted(set(meta) - TEMPLATE_SECTIONS)
        if unknown_meta:
            problems.append(f"adapter.yaml has unknown sections {unknown_meta}")
        template_lines = meta.get("template", [])
        template_keys = _child_keys(template_lines, 2)
        for key in ("id", "version", "contract"):
            if key not in template_keys:
                problems.append(f"adapter.yaml template.{key} is missing")
        identity = "\n".join(template_lines)
        match = re.search(r"id:\s*(\S+)", identity)
        if not match or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", match.group(1)):
            problems.append("adapter.yaml template.id is not a valid slug")
        structure = "\n".join(meta.get("structure", []))
        if "learnable:" not in structure or "fixed:" not in structure:
            problems.append("adapter.yaml structure must list learnable and fixed")
        if "ADAPTER.md" not in structure:
            problems.append("adapter.yaml structure does not make ADAPTER.md learnable")
        if "max_bytes:" not in structure:
            problems.append("adapter.yaml structure gives ADAPTER.md no size limit")
        if "exports.yaml" not in structure:
            problems.append("adapter.yaml structure does not cover exports.yaml")
        if "README.md" not in structure:
            problems.append("adapter.yaml structure does not cover README.md")
    exports = adapter / "exports.yaml"
    if not exports.is_file():
        problems.append("missing adapter/exports.yaml")
    elif "context: ADAPTER.md" not in exports.read_text(encoding="utf-8"):
        problems.append("exports.yaml must declare context: ADAPTER.md")
    if not (adapter / ".hermit" / "EVOLVER.md").is_file():
        problems.append("missing adapter/.hermit/EVOLVER.md")

    judge = out / "judge.py"
    if not judge.is_file():
        problems.append("missing judge.py")
    else:
        source = judge.read_text(encoding="utf-8")
        if "def score(" not in source:
            problems.append("judge.py defines no score()")
        if "JudgeResult" not in source:
            problems.append("judge.py does not return JudgeResult")
    pipeline = out / "pipeline.py"
    if not pipeline.is_file():
        problems.append("missing pipeline.py")
    elif "def run(" not in pipeline.read_text(encoding="utf-8"):
        problems.append("pipeline.py defines no run()")
    if not (out / "task-info" / "README.md").is_file():
        problems.append("missing task-info/README.md")

    evolver = None
    match = re.search(r"name:\s*\"?([a-z0-9_]+)\"?", "\n".join(sections.get("evolver", [])))
    if match:
        evolver = match.group(1)
        try:
            method = resolve(evolver)
        except MethodError as exc:
            problems.append(str(exc))
        else:
            if method.needs_llm and "llm" not in sections:
                problems.append(f"{evolver} needs an llm section")
    else:
        problems.append("evolver.name is missing")
    return problems


def ingest(path: str | pathlib.Path) -> dict[str, object]:
    """Read a Hermit result JSON without assuming its full schema."""
    from .._io import read_json

    data = read_json(pathlib.Path(path))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    metrics = data.get("metrics") if isinstance(data.get("metrics"), dict) else data
    result: dict[str, object] = {"source": "hermit", "path": str(path)}
    for key in ("accuracy", "score", "val_accuracy", "baseline", "final"):
        if key in metrics:
            result[key] = metrics[key]
    result["keys"] = sorted(metrics)[:20]
    return result

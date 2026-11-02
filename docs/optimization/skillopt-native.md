# SkillOpt native lane

SkillOpt has two deployment shapes, and they are not interchangeable:

| Lane | What it is | Command |
|---|---|---|
| `hermit` (default) | SkillOpt as one Hermit Evolver: `evolver.name: skillopt` inside a Hermit `run.yaml`, with Hermit's eighteen documented wrapper parameters and `data.val` | `python -m optimization export --target skillopt --out DIR` |
| `native` | SkillOpt's own experiment: one six-section `skillopt.yaml`, driven by `skillopt-train` / `scripts/train.py` and evaluated by `skillopt-eval` | `python -m optimization export --target skillopt --lane native --out DIR` |

Both ship the exact artifact text the validation gate accepted. The native lane
is the one that answers "can this run under SkillOpt itself", which the Hermit
lane does not.

## The configuration contract

`skillopt.yaml` is structured YAML with six sections, resolved by SkillOpt from
`configs/_base_/default.yaml` and overridden with `--cfg-options
section.key=value`. The exporter emits all six explicitly and refuses any key
its documentation does not list:

| Section | Fields the exporter sets | Why |
|---|---|---|
| `model` | `optimizer`, `target`, `optimizer_backend`, `target_backend`, `reasoning_effort` | Roles and client paths; credentials stay in the documented environment variables |
| `train` | `num_epochs`, `batch_size`, `accumulation`, `seed` | The kernel's epochs, minibatch and seed |
| `gradient` | `minibatch_size`, `merge_batch_size`, `analyst_workers`, `failure_only` | Reflection minibatch and merge defaults |
| `optimizer` | `learning_rate`, `min_learning_rate`, `lr_scheduler`, `lr_control_mode`, `skill_update_mode`, `use_slow_update`, `slow_update_samples`, `slow_update_gate_with_selection`, `longitudinal_pair_policy`, `use_meta_skill`, `use_skill_aware_reflection`, `skill_aware_appendix_source`, `skill_aware_consolidate_threshold` | Bounded patch count, epoch-end slow update, cross-epoch meta skill |
| `evaluation` | `use_gate`, `gate_metric`, `gate_mixed_weight`, `use_semantic_density`, `sel_env_num`, `test_env_num`, `eval_test` | Strict-improvement gate over the selection and test sizes |
| `env` | `name`, `skill_init`, `split_mode`, `split_ratio`, `split_seed`, `data_path`, `exec_timeout`, `out_root` | The benchmark, the initial skill and where the run writes |

`optimizer.learning_rate` is SkillOpt's *maximum patches per step*, so it is
derived from `gate.max_hunks`, not from a character count. The kernel's textual
budgets (`gate.max_edit_chars`, `gate.textual_lr`), its `min_improvement` margin
and its McNemar `alpha` have no SkillOpt field; they remain enforced by this
kernel and are recorded as unmapped in `skillopt.json` rather than approximated
in the config.

## What the bundle contains

```
<out>/
  skillopt.yaml              six documented sections, validated before writing
  best_skill.md              the accepted artifact, byte-identical to the version tree
  skills/skill_v0000.md      env.skill_init — the version the run started from
  data/cases.jsonl           evaluation rows: id, split, question, requires, expected
  data/splits/               train.jsonl, valid_seen.jsonl, valid_unseen.jsonl
  data/README.md             the row schema and the "not optimizer input" rule
  skillopt.json              provenance, hashes, splits, mapping, unmapped knobs
  README.md                  the exact train/eval commands
```

`skillopt.json` carries `artifact_sha256`, so `skillopt.validate(out)` detects a
skill file edited after measurement. `env.split_mode` stays `ratio`: a
`split_dir` must be materialized by the benchmark adapter, whose file contract
belongs to that adapter, not to this exporter. The split files above are a
hand-off named after the `--split` values `skillopt-eval` documents.

## Protected regions

SkillOpt's trainer owns two regions of the skill document:

```markdown
<!-- SLOW_UPDATE_START --> … <!-- SLOW_UPDATE_END -->
<!-- APPENDIX_START -->    … <!-- APPENDIX_END -->
```

The epoch-end slow update fills the first and the skill-aware appendix the
second. `optimization/protected.py` locates them and `apply_edit` refuses any
candidate that:

- changes a region body (the trainer would overwrite it),
- deletes or moves a marker (the region would stop being managed),
- adds a new region (it would hide text from every later patch).

An unbalanced marker pair is refused at export time, because SkillOpt's patch
filler cannot place an update in a document whose regions do not close.

## Commands

```bash
python -m optimization export --spec optimization.toml --target skillopt \
    --lane native --out export/skillopt

cd export/skillopt
skillopt-train --config skillopt.yaml --out_root outputs/<env-name> \
    --cfg-options env.skill_init=skills/skill_v0000.md
skillopt-eval --config skillopt.yaml --skill best_skill.md --split valid_unseen
skillopt-eval --config skillopt.yaml --skill best_skill.md --split valid_seen
```

`env.name` is only resolvable inside the bundle's own runner, which registers
the generated adapter in SkillOpt's registry first:

```bash
python run_skillopt.py train --config skillopt.yaml
python run_skillopt.py eval --config skillopt.yaml --skill best_skill.md \
    --split valid_unseen
```

A self-hosted or OpenAI-compatible deployment sets the model on both roles —
backend *and* id — because `skillopt.yaml` carries SkillOpt's documented
defaults (`gpt-5.5`), which most servers do not have:

```bash
export OPENAI_COMPATIBLE_BASE_URL=http://127.0.0.1:8000/v1
export OPENAI_COMPATIBLE_API_KEY=...
export OPENAI_COMPATIBLE_MODEL=...
python run_skillopt.py train --config skillopt.yaml \
    --cfg-options model.optimizer_backend=openai_compatible \
                  model.target_backend=openai_compatible \
                  model.optimizer=<id> model.target=<id>
```

A source checkout runs the same code as `python scripts/train.py` and
`python scripts/eval_only.py`. `skillopt-eval` without `--out_root` writes its
own timestamped directory and does not modify the training run.

## Limits

The export is schema-valid and self-consistent, checked offline against the
documented fields, enums and run invariants. It is not a benchmark
reproduction: the generated adapter runs *these* cases with one target call
and this run's judge rule, so its numbers are not comparable to the paper's.
`model` is
validated by documented family (`azure_openai_*`, `qwen_chat_*`,
`codex_exec_*`, role prefixes) rather than by an exhaustive key list, because
SkillOpt's backend-specific surface changes between releases.

# Optimization infrastructure

**Türkçe özet:** Bu altyapı, bir ajanın *dışarıdaki* öğrenilebilir parçasını —
bir `SKILL.md` dosyasını veya bir harness adapter'ını — modeli ve harness'i
dondurarak eğitir. Her turda: rollout → tek ve sınırlı bir düzenleme → aynı
validation case'leri üzerinde eşleştirilmiş kapı (McNemar) → kabul edilirse
değiştirilemez bir sürüm. Hermit'in adapter/sürüm ağacı sözleşmesi ile
SkillOpt'un metinsel öğrenme oranı, reddedilen-düzenleme tamponu ve held-out
kapısı aynı çekirdekte birleşir. Aşağıdaki İngilizce bölümler sözleşmenin
tamamını, üst kaynaklarla eşlemesini ve sınırlarını verir.

---

## What this is

> **SkillOpt native lane:** the six-section `skillopt.yaml` contract, the
> protected-region rule and the exact train/eval commands are documented in
> [`skillopt-native.md`](skillopt-native.md).

An optimizer for artifacts that a frozen agent consumes as text. Two artifact
kinds are supported because the two upstream projects made the same move under
different names:

| Upstream | Their noun | What it means here |
|---|---|---|
| [SkillOpt](https://github.com/microsoft/SkillOpt) (Microsoft Research) | skill | `artifact.kind = "skill"`, deployed as `best_skill.md` |
| [Hermit](https://github.com/Infini-AI-Lab/hermit) (Infini-AI-Lab) | harness adapter | `artifact.kind = "adapter"`, deployed as `ADAPTER.md` |

The model and the host harness are never modified. What changes is an external
document plus its version history, and every change is admitted by a
measurement rather than by the author's confidence.

## What the upstreams establish, and what this kernel adopts

The design was written after reading both projects' published method and
documentation, not from a summary of them. The adopted points are:

**From SkillOpt** ([paper](https://arxiv.org/pdf/2605.23904),
[project page](https://microsoft.github.io/SkillOpt/)):

- **Training discipline over self-revision.** Their loop is rollout →
  reflect → aggregate → select → update → evaluate, with epochs, batch size
  and a learning rate. An unbounded "rewrite the skill" step is explicitly the
  failure mode they fix. Adopted as `epochs`, `batch_size`, and one edit per
  step in `loop.py`.
- **A textual learning rate.** Bounded add / delete / replace edits, not a
  rewrite. Adopted as `max_edit_chars`, `textual_lr` (fraction of the document
  one edit may touch) and `max_hunks` in `edits.py`, measured on the diff.
- **A held-out validation gate.** A candidate is accepted only when it strictly
  improves a held-out score. Adopted in `gate.py`, strengthened to a paired
  exact McNemar test so a 1-of-20 flip cannot pass as learning.
- **Rejected edits as negative feedback.** Their buffer keeps the optimizer from
  repeating harmful directions. Adopted as `RejectedBuffer`, persisted in run
  state and rendered into the proposer prompt.
- **A compact deployed artifact.** `best_skill.md` is typically 300–2,000
  tokens. Adopted as `artifact.max_tokens`, enforced at training *and* export
  (`ArtifactTooLarge`), because a skill that grows without bound is how textual
  optimization silently degrades.
- **Epoch-wise slow / meta update.** Their ablations attribute stability to it.
  Adopted as the per-epoch incumbent re-measurement plus `slow_every`; the gate
  is always relative to the current version, never to the original skill.

**From Hermit** ([repo](https://github.com/Infini-AI-Lab/hermit),
[site](https://infini-ai-lab.github.io/hermit/)):

- **One Trainer, many methods.** Their Trainer runs a batch through the acting
  agent, scores it with a judge, hands trajectories to an Evolver, checks the
  proposed adapter and commits the next version. Adopted as `Optimizer` with
  injected `Runner` and `Proposer` seams; `method` only selects the export
  flavour.
- **Adapters as files in a git tree.** Every version is inspectable, rollback
  is real, and the final one ships. Adopted as an append-only `versions/vNNNN/`
  tree with a hash-chained manifest; `verify()` recomputes the chain and every
  artifact digest.
- **Specification checks and checked commits.** Adopted as `prepare()` (the base
  artifact must fit the budget), `commit()` (immutable, idempotent per epoch),
  and `validate()` on each export.
- **Deployment is an export, not a copy-paste.** Their `deploy`/`convert` write
  a workspace or native plugin. Adopted as `export` to Hermit's run layout or
  SkillOpt's artifact/config layout, always shipping the exact text that was
  measured.
- **Method plurality.** GEPA, SkillOpt, MetaWorld Model, Harness², Continual
  Harness, ReHarness and Meta-Harness Adapter all share one interface.
  Adopted as `Proposer` — a rule proposer, an external command proposer, a
  manual queue — behind the same loop, so methods are comparable.

**What was deliberately not copied:** neither project's model/provider layer.
This kernel makes no network calls and has no API key, so it runs offline and
inside CI. A real model is attached by `CommandRunner` (per-case verdicts) and
`CommandProposer` (edit JSON), which keeps provider choice and cost outside the
kernel.

## The loop, precisely

```text
prepare()                       v0000 from the artifact on disk, budget checked
for each epoch:
    evaluate incumbent on val   (same cases every epoch)
    roll out a train minibatch  (deterministic from seed + epoch)
    propose ONE bounded edit    (sees failures + closed directions only)
    apply under budget          (chars, ratio, hunks; re-checked on the diff)
    predict the gain            (optional screening; never accepts)
    evaluate candidate on val   (identical cases)
    paired gate                 (delta >= min_improvement, b > c, McNemar p < alpha)
    accept -> commit vNNNN      (immutable, hash-chained, idempotent per epoch)
    reject -> remember          (RejectedBuffer, with the measured delta)
    checkpoint                  (atomic state.json; replay cannot fork history)
evaluate_heldout()              (base vs current on a split the loop never read)
carry_forward()                 (promoted version written back to the artifact path)
export                          (Hermit or SkillOpt layout, byte-identical artifact)
```

Why these choices, in one line each:

- **The incumbent is the baseline, not the original.** Otherwise a candidate
  worse than the version it replaces can be accepted forever.
- **One edit per step.** Multi-edit steps make an accept/reject verdict
  unattributable, and the buffer cannot learn which direction lost.
- **Ties are rejected.** The most common no-op edit is also the easiest to
  mistake for progress.
- **The gate is paired.** Same cases both sides; the evidence is the discordant
  pairs, so a two-sample test would be the wrong instrument.
- **A split must be able to reach alpha.** The paired test is exact, so at
  `alpha = 0.05` even a flawless edit needs six discordant pairs. `check`
  refuses a spec whose validation or held-out split is smaller: such a run
  cannot promote anything, however good the edit is (E-105).
- **Validation is never trained on, held-out is never optimized against.**
  `leakage()` refuses identical prompts across splits, because a duplicated
  prompt is the same case under two names.
- **A checkpoint marks an epoch, not a step.** The batch is a function of seed
  and epoch, and `commit` is idempotent per (epoch, artifact), so a crash
  replays deterministically instead of forking the tree.

## Contract

### Spec (`optimization.toml`)

Closed schema: an unknown key is an error, never a silent default. Paths are
relative to the spec file, not to the shell's working directory.

```toml
[run]
name = "officeqa-skill"
method = "skillopt"      # skillopt | hermit | gepa | manual
seed = 7
epochs = 3
batch_size = 8
val_size = 12
heldout_size = 12
min_cases = 6            # refuse to certify a gate over fewer pairs
slow_every = 2
# screen_below = 0.01    # skip rollouts the predictor expects to be worthless

[artifact]
kind = "skill"           # skill | adapter
path = "SKILL.md"
max_tokens = 2000

[cases]
path = "cases.jsonl"

[gate]
min_improvement = 0.0
alpha = 0.05
max_edit_chars = 400
textual_lr = 0.20
max_hunks = 3
require_heldout = true
```

### Cases (`cases.jsonl`)

```json
{"id": "q-001", "input": "...", "requires": ["42"], "hint": "treasury", "split": "train"}
```

`requires` is what a passing answer must contain; `expected` is an alternative
exact-match contract; `split` may pin a case to `train`/`val`/`heldout`.
Unpinned cases are assigned by a stable hash of `seed:id`.

### Artifact

Any UTF-8 markdown document. Its size is checked in estimated tokens
(`len/4`) against `artifact.max_tokens` before training and at export.

## Hermit 0.1.0 conformance (exact)

This section is transcribed from the 0.1.0 docs —
[configuration](https://infini-ai-lab.github.io/hermit/docs), the field
reference, `framework/adapter-manifest`, `guides/runs` and
`tutorials/deploy-an-adapter` — and is what `bridges/hermit.py` emits.

### Adapter tree

```text
out/adapter/
├── ADAPTER.md                 acting context (the measured artifact)
├── exports.yaml               context: ADAPTER.md
├── README.md                  fixed
└── .hermit/
    ├── adapter.yaml           template + structure (metadata contract "2")
    ├── EVOLVER.md             learner guidance (never exported)
    └── CARD.md                provenance and recorded evaluation
```

`.hermit/adapter.yaml` has exactly the sections Hermit reads:

```yaml
template:
  id: officeqa-skill        # [a-z][a-z0-9-]{0,63}
  version: "1.0"
  name: "OfficeQA skill"
  contract: "2"
structure:
  learnable:
    - exports.yaml
    - path: ADAPTER.md
      format: {text: true, max_bytes: 8000}
  fixed:
    - README.md
```

Every file outside `.hermit/` must be learnable or fixed; the exporter covers
`ADAPTER.md`, `exports.yaml` and `README.md` and nothing else. `max_bytes` is
the artifact's token budget × 4.

### Run configuration

`run.yaml` uses the documented sections only; Hermit rejects unknown framework
keys, so `validate()` refuses any it does not know.

```yaml
data:
  tasks: "tasks.jsonl"      # {"id", "prompt"} only
  train: "train.txt"        # one task id per line
  val: "val.txt"            # required by SkillOpt/GEPA
  test: "test.txt"
  step_size: 8
  shuffle: false
  shuffle_seed: 7
task_info: "task-info"      # top level: `data.task_info` is an unknown key to Hermit
template: "./adapter"
evolver:
  name: "skillopt"
  params: {…documented defaults…}
agent:
  name: "pipeline.py:run"
  concurrency: 1
judge:
  path: "judge.py"
  name: "score"
llm:                        # only for methods that need the learner model
  base_url: "https://your-endpoint.example/v1"
  model: "your-model-id"
  api_key_env: "MODEL_API_KEY"
sandbox:
  enabled: true
  enforce: "best_effort"    # `required` refuses to run without bubblewrap (Linux only)
  network: "isolated"
  allow: []
  read: []
  env: null                 # pass the environment through, as Hermit's own default does
trainer:
  run_name: "officeqa-skill"
  output_dir: "runs"
  tree_dir: "runs/trees/officeqa-skill"
  epochs: 3
  budget: {tokens: 200000, hours: 2}
  call_timeout_min: 15
  resume_mode: "auto"
eval:
  repeats: 1
```

**Labels never travel in task rows.** `tasks.jsonl` is `{"id", "prompt"}` and
the reference label lives in `judge.py` (`score(task, RunOutput) ->
JudgeResult`), exactly as Hermit's own `first_model` example does. Task rows
become learning material.

`pipeline.py` exports `run(task, adapter) -> RunOutput`, reads the adapter via
`Session(adapter).context()`, and calls `ChatClient` with `ACTOR_*` or
`MODEL_*` environment variables. This is the documented function-agent form
(`agent.name: pipeline.py:run`).

### Run records

The loop writes Hermit's record shape so the same inspection commands apply:

| Path | Content |
|---|---|
| `steps.jsonl` | one line per step: accepted, delta, version, reason, gate |
| `learning_material/step_NNNN/tasks/*.json` | the exact tasks the proposer saw (`id`, `prompt`) |
| `learning_material/step_NNNN/feedback.json` | per-task verdict and feedback |
| `output/report.md` | per-step table and how to read it |
| `summary.json` | stop reason, steps, accepted/rejected, current/final version |
| `checkout/ADAPTER.md` | the current accepted adapter |

### Deployment

`python -m optimization deploy --adapter out/adapter --out ws --host codex`
writes `AGENTS.md` (Codex, Pi) or `CLAUDE.md` (Claude Code) with a managed
block naming `hermit/<id>/`, copies the adapter there, and records the host in
`.hermit-deploy.json`. Redeploying replaces the block rather than appending a
second one; a non-empty workspace that this tool did not generate is refused.
Codex's `project_doc_max_bytes` (default 32768) is enforced with
`--context-limit`. Native plugin emission stays `hermit convert`'s job — it
consumes the adapter directory this export produces.

### SkillOpt parameters

Hermit's SkillOpt page documents these defaults; the export carries all of
them, and `validate()` refuses a missing one:

| Parameter | Default | Here |
|---|---|---|
| `file` | `ADAPTER.md` | the measured artifact |
| `minibatch_size` / `merge_batch_size` | 8 / 8 | documentation default |
| `analyst_workers` | 16 | documentation default |
| `learning_rate` / `min_learning_rate` | 4 / 2 | from `gate.max_hunks` and one less |
| `lr_scheduler` | `cosine` | documentation default |
| `total_steps` | null | `epochs × ceil(train / batch_size)` |
| `use_gate` | true | always true; ties reject |
| `gate_metric` | `hard` | `score >= success_score` |
| `slow_update_gate` | false | follows `require_heldout` |
| `promote_final` | true | documentation default |
| `seed` | 42 | the run seed |

`textual_lr` and `max_edit_chars` are this kernel's budgets and have no Hermit
parameter; they stay enforced here and are not restated as if Hermit honoured
them. `params.max_tokens` is SkillOpt's per-call model limit, not the artifact
budget, so it is left null.

### Methods

`python -m optimization methods` lists the nine Evolvers the docs describe —
`prompt_only`, `agent`, `gepa`, `skillopt`, `mwm`, `harness2`,
`continual_harness`, `reharness`, `meta_harness_adapter` — with their template,
validation need, persistent state and final selection. ACE is announced as
coming soon and is refused rather than offered.

## Quick start

```bash
python -m optimization init workspaces/demo --name demo
# edit workspaces/demo/cases.jsonl
python -m optimization check  --spec workspaces/demo/optimization.toml
python -m optimization train  --spec workspaces/demo/optimization.toml
python -m optimization report --spec workspaces/demo/optimization.toml
python -m optimization export --spec workspaces/demo/optimization.toml \
    --target hermit --out out/hermit
python -m optimization deploy --adapter out/hermit/adapter --out ws --host codex
```

Attach a real agent (one JSON verdict per line on stdout, prompt in
`OPTIMIZE_CASE_INPUT`, artifact path in `OPTIMIZE_ARTIFACT`):

```bash
python -m optimization train --spec ... --runner 'command:python my_agent.py'
```

Attach a real proposer (one JSON request on stdin, one edit or `null` on
stdout):

```bash
python -m optimization train --spec ... --proposer 'command:python my_proposer.py'
```

Check an export with the upstream tools themselves (optional, installs a
pinned Hermit checkout and a venv; see [live-setup.md](live-setup.md)):

```bash
bash scripts/setup-upstream.sh
bash scripts/verify-upstream.sh
```

## Files written by a run

```text
<artifact dir>/.optimization/<run name>/
├── versions/
│   ├── MANIFEST.jsonl        one line per accepted version, hash-chained
│   └── v0000/
│       ├── artifact.md       exact text
│       └── meta.json         parent, epoch, gate decision, digests
├── state.json                epochs done, current version, history, buffer
├── metrics.jsonl             one line per step: edit, prediction, decision
└── report.json               summary, held-out promotion, predictor MAE
```

`v0000` is the base. Versions are never rewritten; a rollback is "point at an
older directory", and `verify` proves the chain was not edited.

## Falsifiability and evidence

- `scripts/bench/bench_optimization.py` — 78 independent checks over the
  kernel's promises and the Hermit 0.1.0 export contract, printable rate and
  coverage for the methodology gate.
- `scripts/bench/bench_optimization.py --broken` — the rule-7 control. It swaps
  in the unguarded behaviour (ties accepted, no significance test, no budget, no
  buffer, seed-insensitive split, no leakage check, unfitted predictor,
  unverified chain) and must fall below the claim. A bench that passes in both
  modes measures nothing.
- `docs/experiments/E-097.md` — the record for the kernel (52 checks).
- `docs/experiments/E-098.md` — the record for the documentation-conformance
  revision (78 checks), including the measured control.

## Guarantees

| Guarantee | Where | Pinned by |
|---|---|---|
| A rejected candidate never becomes the artifact | `gate.decide`, `loop.run` | gate checks, e2e loop |
| A tie is not learning | `gate.decide` | `gate_reject_tie` |
| No single edit can rewrite the document | `edits.apply_edit` | three budget checks |
| A rejected direction cannot be re-proposed unchanged | `RejectedBuffer` | buffer checks |
| Validation cases are never in the held-out split | `cases.assign_splits`, `leakage` | `heldout_not_in_baseline` |
| History cannot fork on resume | `VersionStore.commit` | `resume_no_fork` |
| A version cannot be edited after the fact | `VersionStore.verify` | `version_tamper_detected` |
| The exported artifact is the measured one | both bridges | export checks |
| An oversized artifact cannot ship | `estimate_tokens`, both bridges | `export_token_budget` |

## Limits (read these before trusting a run)

- **The scripted runner is not a benchmark.** It scores token coverage. It exists
  so the kernel is testable offline and deterministically; real claims need
  `CommandRunner` and real cases.
- **The predictor is a screening aid, not evidence.** With fewer than 11 rows it
  is a hand-set prior and says `source = "prior"`. Its interval is residual-based,
  not calibrated per-case, and it never accepts an edit.
- **McNemar is exact but the sample can be small.** `min_cases` refuses a tiny
  gate; the gate's own `Uncertainty` lesson applies: at small n, a rate is a
  bound, not a fact.
- **The exporter is validated structurally, not against a live Hermit.** Section
  names, split semantics, label boundaries, adapter metadata and SkillOpt's
  parameters are checked offline; whether a given Hermit patch release accepts
  every value is only settled by `hermit check` in that environment. A drifted
  schema fails `validate()` rather than silently misconfiguring a run.
- **`convert` is not reimplemented.** Native plugin emission is Hermit's command
  and consumes the adapter directory this export writes; faking a plugin format
  would be more likely to mislead than to help.
- **One edit per epoch means the loop is sample-hungry.** That is the price of
  attributable decisions; batch size buys rollout coverage, not acceptance speed.
- **No network, no provider layer.** Attaching a model means writing the two
  adapters (`CommandRunner`, `CommandProposer`); the kernel will not manage keys,
  retries or cost for you.
- **The held-out check is a report, not a merge.** `require_heldout` marks a run
  unpromoted (exit 3); it does not roll the artifact back for you.
- **Leakage detection is prompt-level.** Two cases with different prompts but the
  same underlying task are not detected. Case authorship is the real defence.

## Relation to the metodoloji gate

The optimizer is a measurement instrument, not a bypass. A code change to the
kernel still needs an `E` record whose measurement the gate performs; E-097 is
that record for this module. The kernel's own gate (`gate.py`) is about
*artifacts*; the methodology gate (`run_experiment.py`) is about *code*. They
share the same discipline — a claim needs a measurement, a control, and a
record — and neither substitutes for the other.

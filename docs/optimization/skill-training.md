# Training a skill

`skills/bmad-research-experiment` is the first subject: a workflow document
whose rules are already testable, trained by the kernel in this repository
rather than edited by hand. This page is the recipe and the honest reading of
its numbers.

## What is trained, and what judges it

Three files, no new machinery:

| File | Role |
|---|---|
| `optimization.toml` | the spec: artifact `SKILL.md`, seed, epochs, split sizes, gate budget |
| `evals/training-cases.jsonl` | one case per line: `id`, `input`, `requires`, `hint`, `checks`, plus a `good` and a `bad` answer for the bench |
| `evals/skill_runner.py` | `CommandRunner`: asks a real model to answer the case *holding the candidate SKILL.md as its system prompt*, then grades the answer with `checks` |
| `evals/skill_proposer.py` | `CommandProposer`: turns the failing cases into one bounded edit, JSON in / JSON out |

The judge is deterministic substring logic, not a second model's opinion: every
`checks` entry passes when at least one of its `must_any` tokens is present and
none of its `must_not_any` tokens is. `must_any` is an OR inside one check, so
a rule with two required parts needs two checks — that is why the cases read
repetitively.

Keeping the judge in `skill_runner.py` is what makes the bench possible:
`scripts/bench/bench_skill_training.py` imports the *same* function and judges
each case twice, once on the compliant answer and once on the answer the skill
exists to prevent. A judge that accepts both cannot measure a candidate, and
the bench catches that before a training run spends a token.

## The loop

```bash
export SKILL_EVAL_BASE_URL=https://api.reflection.ai/openai/v1
export SKILL_EVAL_API_KEY=...          # never written to a file
export SKILL_EVAL_MODEL=Beam-1.0-501B-A23B

python -m optimization check \
  --spec skills/bmad-research-experiment/optimization.toml \
  --runner "command:python skills/bmad-research-experiment/evals/skill_runner.py --artifact {artifact} --case {case_id}"

python -m optimization train \
  --spec skills/bmad-research-experiment/optimization.toml \
  --runner "command:python .../skill_runner.py --artifact {artifact} --case {case_id}" \
  --proposer "command:python .../skill_proposer.py" \
  --epochs 4

python -m optimization eval --spec skills/bmad-research-experiment/optimization.toml
```

`check` spends exactly one model call (a probe rollout) and is the cheap way to
find a broken runner. `train` writes versions under
`skills/bmad-research-experiment/.optimization/<run-name>/versions/`: `v0000` is
the document on disk, and every later version is one accepted edit with its
gate decision in `meta.json`.

The kernel enforces the budget on the proposer's *output*: an edit may change at
most `gate.max_edit_chars` characters, at most `gate.textual_lr` of the
document, across at most `gate.max_hunks` regions. An anchor that is not unique
is refused before a validation run is spent.

## Why the split matters here

14 cases, `val_size = 4`, `heldout_size = 4` leaves 6 for training. A candidate
is accepted only when it beats the **incumbent** on the same four validation
cases (paired, McNemar, `alpha = 0.05`), and `eval` compares `v0000` with the
last version on the four held-out cases the loop never read. With six training
cases, a run reaches "all training cases pass" quickly — after that the
proposer has no failures to explain, returns `null`, and further epochs change
nothing. That is saturation, not convergence: the artifact is as good as this
case set can measure.

## What the first real run produced

The eleven-epoch run over the project's own skill (`Beam-1.0-501B-A23B`, 25→28
cases, three run attempts after two harness fixes) promoted **nothing**, and
the record of why is more useful than a promotion:

| epoch | outcome |
|---|---|
| 1, 4, 7–9 | `proposer returned no edit` — the batch held no failing case |
| 2 | `inapplicable: add anchor occurs 0 times` — the model paraphrased the anchor |
| 3 | `tie (b=1 c=1)` |
| 5–6 | `delta -0.143` — a **correct refusal** failed because it quoted `Decision: APPROVED` |
| 10–11 | `+0.286 (b=3 c=1)`, `p=0.625` — real gains, not significant |

Three findings came out of it, each fixed in the harness rather than the gate:

1. **An empty completion is not a skill failure.** The model returned
   `finish_reason: stop` with `content: None` on one prompt; the runner now
   retries with "respond only with the text you would send the user", which
   turns that into a 1.9k-character answer instead of a false rejection.
2. **A forbidden token must be a shape only a violator writes.** `decision:
   approved` failed a refusal that quoted the rule; the checks now forbid the
   record's own `- **Decision:** APPROVED` line.
3. **The proposer must be handed valid anchors.** Given the document's heading
   list, it stopped inventing anchors it could not copy.

The document's one measured gap — it never states the gate key lifecycle
(`--init-secret`, `~/.bmad/gate-key`, `--check-secret`, `--import-key`,
`~/.bmad/gate-keys/`, `BMAD_GATE_KEY`) — was sampled with eight cases, six in
validation. The best edit flipped three of them and broke one, which the
paired gate refused at `p=0.625`: with seven validation cases an edit must
carry about six discordant pairs to reach p<0.05. That is the gate doing its
job on a small sample, not a bug — and it means this skill's gap is still open.
Fixing it by hand would be exactly the edit the methodology forbids; it needs
more validation cases or an edit that covers the whole lifecycle.

## Limits

- **The judge is vocabulary, not understanding.** A candidate can pass a case by
  containing the right words in the wrong argument. Every case here is a
  refusal rule ("do not approve without a measurement"), which is exactly the
  kind of thing a substring can check — a skill whose value is judgement would
  need a different instrument.
- **Sample size.** 14 cases cannot certify a 0.90 claim on their own; the gate
  records the Wilson bound and a small sample stays advisory.
- **Paid, non-deterministic training.** Each epoch is real model calls, so the
  procedure reproduces but the tokens do not. `--broken` is the only
  deterministic check of the harness.
- **The bench measures the judge, not the run.** It proves the instrument
  separates a compliant answer from a forbidden one; it cannot prove a
  promotion was earned.

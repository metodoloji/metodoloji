# bmad-research-experiment — Plan (bmad-builder format)

- **Skill name:** bmad-research-experiment
- **Type:** workflow skill (single workflow, single use case)
- **Module:** bmm (software-development)
- **Purpose:** Fully executes the **Theory→Hypothesis→Experiment→Measurement→Approval** flow at the
  heart of the scientific methodology. The output cannot be "APPROVED" unless the approval gate passes.
- **Install location:** `{metodoloji-root}/skills/bmad-research-experiment/`
- **Outputs:** `docs/experiments/<deney-id>.md` + `docs/experiments/<deney-id>/raw/` (raw data)
- **Gate tools:** REFUSE to write the approval mechanism without testing it in ANY way (without an
  assert-based `__main__` verification) — an untested gate cannot protect the methodology.

## Components

| File | Purpose |
|-------|------|
| `SKILL.md` | Activation + 6-step flow (Theory→Hypothesis→Experiment→Measurement→Approval→Result) + honesty rules |
| `customize.toml` | Surface: `persistent_facts` manifesto loading, `activation_steps_append` (manifesto + Stage-6 verification) |
| `experiment-log.md` | Mandatory experiment record template (format from the manifesto) |
| `scripts/run_experiment.py` | Measurement runner: mechanical approval gate against the hypothesis threshold (assert-based) |

## Experiment record template (required fields)

- `deney-id`, `date`, `status`
- `theory`, `hypothesis` (H-id, falsifiable claim, **threshold value**)
- `measurement metrics` (metric name + threshold), `experiment design`
- `raw results` (numbers — as-is)
- `decision` (APPROVED / REJECTED + rationale), `next step`

## Approval gate logic (mechanical)

Comparison of the measured value against the hypothesis threshold:

- `PASS`: the measurement passed the threshold → decision `APPROVED`
- `FAIL`: the measurement did not meet the threshold → decision `REJECTED`, the rationale is recorded
- If the gate command output is not `PASS`, the skill CANNOT mark the output as `APPROVED`.
- **Fraud protection:** the gate command uses the hypothesis threshold; changing the hypothesis afterwards
  and saying "accepted" is invalid (the `hypothesis` field in the record does not change — a new
  hypothesis → a new experiment).
- **Record-bound gate:** `--verify` cross-validates the `Hypothesis` claim in the record against the
  claim in `Gate Evidence`. A record that changes the threshold after approval and keeps the token
  returns `FORGED`, even if the token matches.
- **Sample/CI (rule 4, mechanical):** the gate parses the `(x/y)` denominator in the `--run` output as
  the sample `n` and computes the 95% Wilson lower bound; if it is below the threshold it writes an
  `Uncertainty` row to the record (`sample too small`), if sufficient `none`, if no denominator
  `n unknown`. Advisory, not a rejection; the token does not include `n` (existing tokens remain valid).
  For `--measured`, use `--n` or the `Sample n` field.

## Honesty rules (quoted from section 2)

- A rejected hypothesis is not deleted/hidden; it is recorded and reported.
- Raw data is stored as-is.
- Uncertainty is acknowledged.
- "We didn't try it, but made it look like we did" is forbidden.
- A negative result is still a result.

## Blackboard integration (Methodology chain origin)

- **Start:** `write --key E-{id} --value "in-progress" --type state --hot`, `write --key status --value in-progress`, `write --key scope --value "docs/experiments"`
- **On APPROVED:** `mirror --key E-{id} --value "APPROVED..." --to bmad-check-implementation-readiness --from-key E-{id}` (gate does this automatically)
- **On REJECTED:** `write --key E-{id} --value "REJECTED..."`
- **Close:** `write --key status --value complete`, `hot --clear`, `doctor --json`

## Verification (bmad-customize Step 6)

- `python3 {metodoloji-root}/hooks/engine/resolve_customization.py --skill {skill-root} --key workflow` — use your harness-native shell tool with the command as given (no extra wrapper params)
- Gate script: `python3 scripts/run_experiment.py` is tested with its own `__main__` verification
  (PASS and FAIL paths, assert-based).

## Closing condition

- Skill files written, the resolver verified the override, the gate script test passed.
- The user received a summary.

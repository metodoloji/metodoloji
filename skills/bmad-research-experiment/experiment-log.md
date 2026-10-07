# Experiment Record Template — docs/experiments/<experiment-id>.md

This template is mandatory. Every field is filled; an empty field means the experiment
did not pass that stage. The `Raw results`, `Uncertainty`, `Metric`, `Decision`, `Gate Evidence`, `Next Step` and
`status` fields are written by the approval gate — they are not filled manually.

```
## Experiment: <experiment-id> — <short title>
- **Date:** <YYYY-MM-DD>
- **Status:** planned | in-progress | completed | REJECTED
- **Theory:** <which theory/framework it comes from — "I was curious" is not enough>
- **Hypothesis:** H-NNN: "metric >= threshold"   <!-- e.g. H-001: "accuracy >= 0.90" — falsifiable claim; threshold is a unitless number -->
- **Measurement Metrics:** <metric name + threshold, e.g. "latency <= 100" (use a unitless numeric threshold)>
- **Experiment Design:** <inputs, procedure, control variables, repeatability>
- **Sample n:** <optional — sample size; if absent, the gate writes an "n unknown" warning>
- **Raw Results:** <numbers/outputs — as-is; raw files: docs/experiments/<experiment-id>/raw/>
- **Uncertainty:** <gate writes: sample too small | none | n unknown — not filled manually>
- **Metric:** <gate writes: compliant | NON-COMPLIANT | n/a>
- **Decision:** <gate writes: APPROVED | REJECTED — rationale>
- **Gate Evidence:** <GATE-OK-... token — written by the gate script>
- **Next Step:** Move to Code | Return to Theory | Additional experiment
```

## Notes

- `decision` is based on the output (PASS/FAIL) of `{skill-root}/scripts/run_experiment.py`.
  `APPROVED` cannot be written unless the gate passes.
- A `REJECTED` decision is not deleted or hidden; it is reported as-is.
- A new hypothesis → a new experiment id (`H-002`, `E-002`, ...). The same experiment record is not
  rewritten to change the hypothesis — that is fraud.

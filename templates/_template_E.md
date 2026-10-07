# Experiment Record Template (Mode A — Quantitative / Empirical, MECHANICAL GATE)

Copy this file for a new experiment: `docs/experiments/E-NNN.md`

This template is for **Mode A (quantitative/empirical)** — the only legitimate path
to code production. For documentary modes: Mode B (qualitative) and Mode D (contextual)
→ `docs/research/_template.md`; Mode C (design) → `docs/design/_template.md`.
Manifesto: `{metodoloji-root}/docs/bmad/research-methodology.md` (plugin-canonical — read from the plugin installation root, not the project).

English field labels are **mandatory** — the gate (`run_experiment.py`) parses these
labels. Do **not** hand-write the `Decision`, `Gate Evidence`, `Next Step`, `Status`
lines; the gate writes them.

```markdown
## Experiment: E-NNN — <short title>
- **Date:** <DD.MM.YYYY>
- **Status:** planned
- **Theory:** <which theory/framework this comes from — "I was curious" is not enough>
- **Hypothesis:** H-NNN: "metric >= threshold"   <!-- e.g. H-001: "accuracy >= 0.90" -->
- **Measurement Metrics:** <metric name + threshold, unitless numeric>  <!-- e.g. accuracy >= 0.90 -->
- **Experiment Design:** <inputs, procedure, control variables, reproducibility>
- **Sample Size n:** <sample size — the gate parses the denominator (x/y) from the measurement output; this field is informational>
- **Code Scope:** <glob patterns of files this approval opens, comma/space separated; e.g. src/** , lib/engine/*.py>
  <!-- "none" = experiment that produces no code. Writes to files outside scope are blocked by the guard. -->
- **Raw Results:** <measurement — the gate writes this>
- **Uncertainty:** <gate writes: small sample | none | n unknown>
- **Metric:** <gate writes: consistent | MISMATCH — from the measured metric of the --run output>
- **Decision:** <gate writes: APPROVED | REJECTED — reason>
- **Gate Evidence:** <gate writes: GATE-OK-...>
- **Next Step:** <gate writes: Proceed to Code | Return to Theory>

## Implementation Plan (GRP)

<!-- E-065: the operation's declared working set. The guard checks write targets
     against the plan FIRST (under a VERIFIED record); plan-external files join via
     --amend-plan (mechanically chained, HMAC-signed, no re-measurement) while the
     record is still undecided. The final --run token binds to this plan's hash. -->

- **Planned Files:** <explicit paths and/or bounded globs this operation will write; never a bare **>
- **Planned Tests:** <test files this operation must create or update and keep passing>
- **Known Gaps:** <advisory only — structural risks the guard cannot mechanically check; never gates>
- **Amendments:** none                <!-- gate CLI maintains: --amend-plan appends chained entries -->
```

## Set up the gate key (once per machine)

```bash
python3 {metodoloji-root}/skills/bmad-research-experiment/scripts/run_experiment.py --init-secret
# Key is written to ~/.bmad/gate-key (OUTSIDE the repo). Check:
python3 {metodoloji-root}/skills/bmad-research-experiment/scripts/run_experiment.py --check-secret
```

## Run the gate

```bash
# The gate runs the measurement itself: value + metric name + denominator (x/y)
# are parsed from the output. No operator-declared numbers (--measured removed —
# reality is mechanical).
# The measurement script MUST live in a protected area (e.g. scripts/bench/).
# Scripts in free zones (scratch/, tmp/, temp/) are REJECTED by the gate.
python3 {metodoloji-root}/skills/bmad-research-experiment/scripts/run_experiment.py \
  --record docs/experiments/E-NNN.md --run "python scripts/bench/bench_xxx.py"
# Dry-run: preview the decision WITHOUT writing (format/record check — never use
# --run alone for "checking", it would decide the record by mistake)
python3 {metodoloji-root}/skills/bmad-research-experiment/scripts/run_experiment.py \
  --record docs/experiments/E-NNN.md --run "python scripts/bench/bench_xxx.py" --dry-run
```

## Verify before code

```bash
python3 {metodoloji-root}/skills/bmad-research-experiment/scripts/run_experiment.py \
  --verify --record docs/experiments/E-NNN.md
```

**Cross-machine records:** the gate keys are machine-local, but --verify consults your
whole trust ring — this machine's own key plus peer keys imported with `--import-key`
(into `~/.bmad/gate-keys/`), so a record APPROVED on another machine of yours verifies
here without ceremony once that machine's key is in the ring. Outside the ring it
reports FORGED by design — never hand-edit gate-written fields or tokens. Key-free
remedy: re-run the measurement under a new E record with your local key and add a plain
`- **Re-Measured-By:** E-NNN (date)` line at the end of the old record;
`check-plugin.sh` §3 then reports it as a CROSS-MACHINE warning instead of an error.

Keep raw data files under `docs/experiments/E-NNN/raw/`.

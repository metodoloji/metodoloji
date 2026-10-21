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
- **Lineage:** <none | E-NNN[, E-NNN ...] — what this carries from the prior experiments>  <!-- prior-experiment carry-forward; the gate REFUSES a draft without this line (E-022) -->
  <!-- 'none' = a genuinely new line of inquiry — the judgment is made explicitly, never skipped. Otherwise
       cite every prior experiment this one builds on / supersedes (especially a REJECTED record whose Code
       Scope it re-treads: P9 — a rejection is a lesson the next record carries), and say what is inherited.
       Every cited id must resolve to docs/experiments/E-NNN.md; the gate refuses a dangling or self-citing claim. -->
- **Control Command:** <a broken implementation that MUST fail the same claim, e.g. `node scripts/bench/bench_x.mjs --broken`>
  <!-- Rule 7, MECHANICALLY ENFORCED. The gate runs this itself. If the control also clears the
       threshold, the bench cannot separate working code from broken code: no token, no Decision,
       the record stays undecided (exit 3) so the bench or the hypothesis can be fixed and re-run.
       Omitting it is allowed for legacy records but writes `Falsifiability: UNVERIFIED` into the
       record and says so on stderr — an unfalsifiable bench is a hypothesis that was never tested. -->
- **Falsifiability:** <gate writes: verified — control measured X | REFUSED … | UNVERIFIED — no control declared>
- **Raw Results:** <measurement — the gate writes this>
- **Uncertainty:** <gate writes: small sample | none | n unknown>
- **Metric:** <gate writes: consistent | MISMATCH — from the measured metric of the --run output>
- **Decision:** <gate writes: APPROVED | REJECTED — reason>
- **Gate Evidence:** <gate writes: GATE-OK-...>
- **Witness:** <gate writes: commit=<sha|none> tree=<digest|none> scope=<digest|none> — the
  code state this decision was measured against; the commit is bound into the token, so the
  line cannot be re-dated after approval (that is FORGED, not a repair)>
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

## Falsifiability — declare a control, or the result means nothing

```bash
# Rule 7 is enforced, not requested. The gate runs the control itself and
# refuses to decide when the control clears the same threshold.
python3 {metodoloji-root}/skills/bmad-research-experiment/scripts/run_experiment.py \
  --record docs/experiments/E-NNN.md \
  --run "node scripts/bench/bench_x.mjs" \
  --control "node scripts/bench/bench_x.mjs --broken"
```

A bench whose broken mode also passes is not measuring anything: it cannot tell
working code from broken code, so its APPROVED token would certify a number and
nothing else. When that happens the gate writes `Falsifiability: REFUSED …` and
the reason, exits 3, and leaves the record undecided — fix the bench or the
hypothesis and re-run the same record.

Omitting `--control` is still permitted so that older records keep verifying, but
the run writes `Falsifiability: UNVERIFIED — no control command declared` into
the record and prints the same on stderr. That line is the honest state of a
bench nobody has tried to break.

## Verify before code

```bash
python3 {metodoloji-root}/skills/bmad-research-experiment/scripts/run_experiment.py \
  --verify --record docs/experiments/E-NNN.md
```

**Cross-machine records:** the gate keys are machine-local, but --verify consults your
whole trust ring — this machine's own key plus peer keys imported with `--import-key`
(into `~/.bmad/gate-keys/`), so a record APPROVED on another machine of yours verifies
here without ceremony once that machine's key is in the ring. Outside the ring it
reports UNVERIFIABLE — the token is well-formed but re-derives under no key here, which
is NOT the same verdict as FORGED (that stamp is reserved for structural impossibilities:
a missing evidence block, a claim that contradicts the Hypothesis, a doctored plan hash
or a Witness line added after approval). Never hand-edit gate-written fields or tokens.
Key-free remedy: re-run the measurement under a new E record with your local key and add
a plain `- **Re-Measured-By:** E-NNN (date)` line at the end of the old record;
`check-plugin.sh` §3 then reports it as a CROSS-MACHINE warning instead of an error, and
signals citing the old record are cascade-invalidated rather than obeyed.

**Freshness (`Witness:`):** an APPROVED record is a claim about the code state it was
measured against, so the gate dates it to that commit. `guard` and the session-start
inject derive three verdicts from it — CURRENT / STALE / UNKNOWN — and a claim whose
scope moved is reported as `this no longer applies` instead of being obeyed. Non-code
scopes (`none`) have nothing to stale. Re-measure (a new record + Re-Measured-By on the
old one) rather than editing the line.

Keep raw data files under `docs/experiments/E-NNN/raw/`.

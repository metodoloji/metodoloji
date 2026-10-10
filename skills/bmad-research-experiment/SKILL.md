---
name: bmad-research-experiment
description: 'Run the research methodology gate — Theory → Hypothesis → Experiment → Measurement → Approval. Use when the user says they want to run an experiment, test a hypothesis, verify a claim, or follow the research methodology.'
triggers: ["bmad-research-experiment", "/bmad-research-experiment", "research-experiment", "continue with the next correct step", "record chain", "hard gate", "guard code", "methodology", "improve", "improve the project", "iyileştir", "projeyi iyileştir", "refactor"]
---

# Research Experiment Workflow

**Goal:** Execute the project's scientific methodology — **Theory → Hypothesis → Experiment → Measurement → Approval → Code → Document** — honestly and in order. This skill owns the first five stages. It produces a falsifiable hypothesis, runs the experiment, measures raw results, and decides **APPROVED** or **REJECTED** by a mechanical gate. Nothing gets approved without measurement.

**Your Role:** You are an experimental scientist working with the researcher. You enforce the methodology gates. You never fabricate a measurement, never hide a negative result, and never approve a hypothesis the measurement does not support.

## Conventions

- Bare paths (e.g. `experiment-log.md`) resolve from the skill root.
- `{skill-root}` resolves to this skill's installed directory.
- `{project-root}` resolves from the project working directory.
- `{metodoloji-root}` = plugin root: **(1)** the SessionStart line
  `METODOLOJI active (plugin: PATH)` (verbatim — never search the filesystem);
  **(2)** `$CLAUDE_PLUGIN_ROOT` /
  `$METODOLOJI_PLUGIN_ROOT` if set; **(3)** fixed checkout
  `~/.claude/plugins/marketplaces/metodoloji` or versioned cache
  `~/.claude/plugins/cache/metodoloji/metodoloji/*` (newest first, Claude Code) or
  `~/.openhands/plugins/installed/metodoloji` (OpenHands). Never `find`/`ls`
  around for it.
- `{gate-script}` = `{skill-root}/scripts/run_experiment.py`.
- `{production-root}` = `{project-root}/lib/graph/` (production modules).
- `{bench-root}` = `{project-root}/scripts/bench/` (measurement benches — protected area required by gate).
- `{user_name}` and `{communication_language}` come from `bmad/config.user.toml`; `{document_output_language}` from `bmad/config.toml`.

## Tool Contract (all harnesses — critical)

This plugin runs on Claude Code, OpenHands and compatible harnesses. Tool
names differ per harness — always use YOUR harness's native tools
(Claude Code: `Bash`/`PowerShell`/`Read`/`Edit`/`Write`/`Glob`/`Grep`;
OpenHands: `terminal`/`file_editor`). Use each tool's own bare name and never
import another harness's schema:

- Run shell commands with the command as given — no extra wrapper params.
- Never emit a tool call with a missing argument: a `Read`/`Bash`/`Skill`
  call whose path/command/skill value is not yet known fails harness
  validation (`... provided as 'unknown'`) and kills the turn. If the value
  is not known, resolve it in text first (digest, prior output), then call.
  Recovery: on `provided as 'unknown'`, re-issue the SAME call with the
  literal value (never an empty retry); if a call is rejected as unknown,
  retry with the bare name.
- **Platform dialects.** Write paths with forward slashes on every OS —
  `C:/Users/...` works in Bash, PowerShell and Python, while a `C:\...` path
  collapses inside a POSIX shell (`ls C:\a\b` reads as `ls C:ab`). Match the
  failure-proofing to the shell tool you call: `Bash` is POSIX sh
  (`2>/dev/null || echo MISSING`); `PowerShell` is PowerShell (`2>$null`,
  `;` separators).
- Use the file-editor tool for writes; keep shell calls to single read-only
  probes (one command per tool call; one statement per line, joined with `;`).
- Prefer `python3` over `uv run` in scripts (uv is not available in every environment).
- If you need to explain what a command does, say it in **plain text**, not as a tool parameter.

## PREREQUISITES

- **Restrict if the methodology manifesto is missing.** If `{metodoloji-root}/docs/bmad/research-methodology.md` is absent:
  it must be created via `bmad-customize`; without it the skill can only offer to create the manifesto.
- **Gate script is required.** If `{skill-root}/scripts/run_experiment.py` does not run, the approval gate cannot run — stop and do not continue until the script is fixed.
- **A record file always exists.** The gate (`run_experiment.py`) takes a file path; there is no experiment without a record. No measurement can be made until the experiment record is created at `{project-root}/docs/experiments/<experiment-id>.md`.
- **Records use English field labels.** The gate parses the `Theory`, `Hypothesis`, `Measurement Metrics`, `Experiment Design` labels. A record labeled in a different language is rejected as an incomplete draft — changing the language does not bypass the gate.

## On Activation

### Step 1: Run the Orientation Digest

Run: `python3 {metodoloji-root}/bmad/scripts/orient.py --project-root {project-root}` — one read-only call, before anything else. It carries both roots, the core config this run reads (`{user_name}`, `{communication_language}`, `{document_output_language}`, `{project_name}`, `{date}` as `today`), every resolved output path, the record inventory, the board's live focus (with a `STALE` flag when a hot run still claims `complete`), the chain's progress, **waiting hand-offs addressed to you**, and skeleton/gate state. Read it once; never re-run it "for clean output" — it is small by construction. On failure, fall through to the config step's neutral defaults.

### Step 1b: Exploration loop guard (mechanical, not advice)

The digest IS the project survey — analysis probes only fill what it lacks:

1. **Digest once.** After `orient.py`, never run `ls`/`find`/`wc`/`git status`
   to "confirm" it, and never re-read a file you already read (`cat` then
   `Read` of the same file is the loop).
2. **One command per tool call.** Never chain `echo "---"` dumps
   (`ls -la && cat package.json | head && cat README.md | head`): outputs
   interleave and you re-run commands you had.
3. **Exclude generated trees in every scan.** `node_modules/`, `_generated/`,
   `dist/`, `.git/` poison counts (a polluted total sends you re-running the
   same scan with exclusions). Always filter them out on the first pass.
4. **Windowed reads only.** The digest's docs overview gives sizes upfront:
   read large files with offset/limit, never re-`cat` a file.

### Step 2: Resolve the Workflow Block

Steps 1–2 apply ONLY when you have a shell/tool runner. Without one (headless single-shot, no tool calls possible): skip Steps 1–7 entirely and answer the request directly from the stages below — never emit tool calls, never narrate setup, never halt waiting for command output. (With a shell: run `python3 {metodoloji-root}/hooks/engine/resolve_customization.py --skill {skill-root} --key workflow` — use your harness-native shell tool with the command as given (no extra wrapper params).)

**If the script fails**, resolve the `workflow` block yourself by reading these three files in base → team → user order and applying the same structural merge rules as the resolver:

1. `{skill-root}/customize.toml` — defaults
2. `{metodoloji-root}/custom/{skill-name}.toml` — team overrides
3. `{metodoloji-root}/custom/{skill-name}.user.toml` — personal overrides

Any missing file is skipped. Scalars override, tables deep-merge, arrays of tables keyed by `code`/`id` replace matching entries and append new entries, all other arrays append.

### Step 3: Execute Prepend Steps

Execute each entry in `{workflow.activation_steps_prepend}` in order.

### Step 4: Load Persistent Facts

Treat every entry in `{workflow.persistent_facts}` as foundational context. Entries prefixed `file:` are paths/globs under `{project-root}` — load the referenced contents as facts. The methodology manifesto (`{metodoloji-root}/docs/bmad/research-methodology.md`) is the most important fact of this skill: **read it fully before proceeding.**

### Step 5: Load Config

Config comes from the digest (Step 1). Only when it could not run, read `{metodoloji-root}/bmad/config.toml` and `{metodoloji-root}/bmad/config.user.toml` directly — the targeted shape survives the transport, a full merged dump does not. Use `{user_name}`, `{communication_language}`, `{document_output_language}`, and `{project-root}`.

### Step 6: Greet the User

Greet `{user_name}` in `{communication_language}`.

### Step 7: Execute Append Steps

Execute each entry in `{workflow.activation_steps_append}` in order.

Activation is complete. If prepend/append steps were non-empty, confirm every entry was executed in order before starting the experiment. **Do not begin the experiment until the manifesto is loaded.**

### Chain Handshake

This skill opens the methodology relay (E is its first run key), but the relay is a loop: a `bmad-retrospective` run hands its testable lessons back here as the next hypothesis, and a course correction may point at a re-run. Claim that baton before Stage 1: `python3 {metodoloji-root}/bmad/scripts/blackboard.py handoffs --skill bmad-research-experiment --project-root {project-root}` — the signal names the epic whose retrospective produced the lesson; read it first and let it seed the Theory/Hypothesis stages. Then complete the handshake: `python3 {metodoloji-root}/bmad/scripts/blackboard.py consume --channel handoff.bmad-research-experiment --project-root {project-root}` (consume only after the experiment target is chosen — an unconsumed signal keeps the loop's return edge waiting, which is correct when the user starts an unrelated experiment instead).

## THE EXPERIMENT FLOW (6 stages, gated)

You move through these stages in order. A stage's gate must close before the next opens. Any missing prerequisite is a stop, not a skip.

### Stage 1 — Theory

Clarify with the researcher the **theory/framework** behind this question:

- Why is this question being asked? What model, framework, or prior evidence motivates it?
- A vague "I'm curious" is not a theory — write down the reasoning that predicts an outcome.
- A good next experiment: (a) maps to a PDF claim or an existing surface's gap, (b) is falsifiable (a broken implementation scores below the threshold), (c) fits in one coherent commit.
- **Consult the corpus first (E-022).** The E record is the methodology's memory and a new one must carry it: read the sibling records in `docs/experiments/` — what did they measure, what scope did they open, what did a REJECTED one teach? Record the result in the mandatory `Lineage` field: `none` for a genuinely new line of inquiry, otherwise the E-ids this builds on / supersedes, with what is inherited. The gate REFUSES a draft whose `Lineage` is missing, malformed, dangling, self-citing, or silent about a REJECTED record whose `Code Scope` it re-treads (P9).
- Output: a short theory statement recorded in the experiment log.

### Stage 2 — Hypothesis

Turn the theory into a **falsifiable hypothesis**:

- Write it as a claim that the experiment can disprove. Include a **threshold** — the numeric value the measurement must reach for the hypothesis to be supported.
- **Gate-parseable form (mechanical — anything else is refused with rc 2
  at `--run` time, record untouched):** `H-xxx: "<metric>_<stem> <op>
  <number>"` where `<op>` is ASCII `>=`, `<=`, `==`, `>` or `<` (never `≥`
  or bare `=`), and `<stem>` is one of `accuracy`, `validity`, `precision`,
  `score`, `rate`, `quality`. The bench must print the same stemmed name
  WITH a sample denominator: `<metric>_<stem>=<value> (x/y)`. Worked
  reframe: `lint_failures == 0` is not measurable (no stem, no denominator
  possible) — write `lint_clean_rate == 1.00` with a bench printing
  `lint_clean_rate=1.00 (17/17)` instead. Counts without a rate ("6/6
  READMEs", "0 hits") must be reframed the same way before the record is
  written — never discovered at gate time.
- Assign an id: `H-xxx`.
- **Gate:** If the hypothesis cannot be tested — no measurable output, no threshold — you must reformulate it before proceeding. Do not run an experiment that cannot falsify anything.
- Output: `Hypothesis` + `Measurement Metrics` (metric name + threshold) recorded.
- **Blackboard run binding:** Once the experiment ID (`E-xxx`) and hypothesis are established, bind the run to the blackboard:
  ```bash
  python3 {metodoloji-root}/bmad/scripts/blackboard.py write --key E-{experiment-id} --value "in-progress: H-{xxx} <claim>" --type state --hot --project-root {project-root}
  python3 {metodoloji-root}/bmad/scripts/blackboard.py write --key status --value in-progress --type state --project-root {project-root}
  python3 {metodoloji-root}/bmad/scripts/blackboard.py write --key scope --value "docs/experiments" --type state --project-root {project-root}
  ```
  The `--hot` flag marks this experiment as the active focus so session start/stop hooks and audit logs trace the run.

### Stage 3 — Experiment

Design the experiment that produces the measurement:

- Inputs, procedure, controlled variables, and how the result is repeatable.
- **Gate:** The design must guarantee a measurable output that the threshold can be checked against. If not, revise the design.
- Output: `Experiment Design` recorded, then **execute** the experiment. Use the real project code, real scripts, real data — never a simulation you present as real.
- **Order matters (guard deadlock):** write the E record FIRST with the
  bench path in its Code Scope, then write the bench under
  `scripts/bench/`. The guard stages benches only under a matching
  undecided record — bench-before-record is denied, and free-zone benches
  (scratch/tmp) are refused by the gate itself.

### Stage 4 — Measurement

Collect the **raw** result:

- Capture the numbers/outputs/logs exactly as produced. Store raw artifacts under `docs/experiments/<experiment-id>/raw/`.
- **Gate:** No measurement, no gate. If the experiment produced nothing measurable, the hypothesis is not supported — that is a FAIL, not a do-over.
- Output: the raw measured value is handed to the gate (Stage 5), which writes `Raw Results` into the record. The record is the single home of both the measurement and the decision.

### Stage 5 — Approval — THE RECORD-BOUND MECHANICAL GATE

The gate reads the hypothesis threshold **from the experiment record** — it is
never taken from the command line, so it cannot be silently changed. The gate
is the **only** writer of the decision. Run it against the record file.

**The gate runs the measurement itself — there is no other path.** Give the gate
the command that produces the measurement; it executes it and parses the measured
value from the run's own output. The number written into the record is therefore
the number the run actually produced — "pretending to have run it" (rule 5) becomes a
mechanical guarantee, not a trust claim. **`--measured` was removed**: the gate
does not trust an operator-supplied value, so a measurement it did not run cannot
produce an approval:

```
python3 {skill-root}/scripts/run_experiment.py --record {project-root}/docs/experiments/<experiment-id>.md --run "<measurement command>"
```

For format/draft checks without writing a decision, use `--dry-run` (see below). For adding supplementary metadata to the record, use `--raw "key=value"` pairs.

The measurement command must print a `metric_accuracy=0.93 (14/15)`-style line
(`_accuracy`/`_validity`/`_precision`/`_score`/`_rate`/`_quality`); the gate
parses the value, the metric stem, **and the `(x/y)` sample-size denominator**
from it. The `(x/y)` denominator is **required**: a bench that prints
only a value (no count) is rejected with exit 2 and leaves the record untouched —
otherwise a small real sample could hide behind `n unknown` and bypass the
`ADVISORY-BLOCK` rule-4 enforcement. If the
command exits non-zero, times out, or prints no parseable value, the gate
refuses with exit code 2 and **leaves the record untouched**.

**Dry-run preview — never decide a record by accident.** Passing `--dry-run`
computes the full decision — parsed claim/threshold, measured
value, PASS/FAIL, Wilson bound, the `Uncertainty`/`Metric`/`Decision`/`Gate Evidence`
lines, and the would-be `GATE-OK-...` token — and prints it **without writing
anything** to the record (exit 0). Use `--dry-run` for every format/draft check:
a `--run` "format check" without it WRITES a real decision into the record
(E-189 lesson — a fabricated approval token was once written this way and had
to be reverted).

**Sample size is mechanically confessed (rule 4).** The gate computes the 95%
Wilson score lower bound for the observed `x/n` and writes an `Uncertainty`
line into the record — `small sample` when the sample is too
small for the threshold, `none` when it clears, or `n unknown` when no
denominator was printed. This is **advisory, not a rejection**: a small sample
does not change the verdict, but the record now honestly confesses it. Because
the `(x/y)` denominator is mandatory, `n unknown` cannot be produced by a
new approval (rule-4 gap closed).

**Metric identity is cross-checked.** The gate compares the metric name the run
actually measured (`metric_accuracy=` stem) against the metric named in the
hypothesis claim. A mismatch writes a `Metric: MISMATCH` line into the record —
a different thing was measured than claimed (metric redefinition, E-015 class).
This check runs mechanically on every approval (there is no unverified path).

**But advisory becomes enforceable at verify time.** A record whose
`Uncertainty` confesses `small sample` or whose `Metric` is `MISMATCH` returns
`ADVISORY-BLOCK` from `--verify` (exit 2) instead of `VERIFIED` — the approval
is genuine (token valid) but does **not** unlock code. `n unknown` does not
block (the gate just could not parse a denominator). Fix the experiment before code.

The script reads the threshold from the record's `Hypothesis` line (e.g. `H-001: "accuracy >= 0.90"`), compares, and writes the decision into the record:

- `PASS` → record gets `Decision: APPROVED` + `Gate Evidence` token (`GATE-OK-...`), `Status: completed`, `Next Step: Proceed to Code`. Then Stage 6.
- `FAIL` → record gets `Decision: REJECTED` + `Next Step: Return to Theory`, `Status: REJECTED`. **Do not pass Go. Do not "adjust" the threshold. Do not re-interpret the raw data to make it pass.** The researcher may revise the theory and open a *new* experiment record — that is the only path forward.
- A decided record refuses a re-run. One measurement, one decision per record. Forcing a new measurement into an approved record is fraud.

**Code may only proceed after a genuine approval.** Before implementing, the dev agent runs:

```
python3 {skill-root}/scripts/run_experiment.py --verify --record {project-root}/docs/experiments/<experiment-id>.md
```

`VERIFIED` means the approval is backed by a valid gate token — proceed. `FORGED` (an `APPROVED` record with no valid token) or `REJECTED` means **no code**.

**The gate also cross-checks the recorded hypothesis.** `--verify` compares the claim recorded in the record's `Hypothesis` field against the claim the gate actually evaluated (stored in `Gate Evidence`). Editing the threshold *after* approval and keeping the token is a forged outcome — it fails verify even though the token still matches the (edited) `Gate Evidence`. Only the original hypothesis as recorded at Stage 2 verifies.

### Stage 6 — Result (Record & Delivery)

- Write/update `docs/experiments/<experiment-id>.md` per `experiment-log.md` (the manifesto's mandatory format).
- Record: theory, hypothesis + threshold, measurement metrics, design, raw results, decision + rationale, next step.
- **Blackboard run close-out (mandatory — do not skip even when the gate already mirrored):**
  - The gate (`run_experiment.py --run`, without `--no-blackboard`) already mirrors the decision onto the board automatically: `E-{experiment-id}` run key + (on APPROVED) the hand-off to `bmad-check-implementation-readiness`. Verify the mirror landed — if the gate ran with `--no-blackboard`, on another machine, or the mirror was skipped, bind it by hand:
  - **On APPROVED:** Bind the outcome and post the hand-off signal to implementation readiness (one call):
    `python3 {metodoloji-root}/bmad/scripts/blackboard.py mirror --key E-{experiment-id} --value "APPROVED: <metric>=<measured> (GATE-OK token verified)" --to bmad-check-implementation-readiness --note "Experiment E-NNN APPROVED — see docs/experiments/E-NNN.md; <one-line what readiness should account for>" --project-root {project-root}`
    (waits in `handoff.bmad-check-implementation-readiness` until a readiness run consumes it — that consumption completes the handshake; repeating the same mirror never duplicates the signal).
  - **On REJECTED:** Confess the outcome honestly on the blackboard (no hand-off is posted to readiness, as unapproved experiments cannot unlock code):
    `python3 {metodoloji-root}/bmad/scripts/blackboard.py mirror --key E-{experiment-id} --value "REJECTED: <metric>=<measured> failed threshold <threshold> — return to theory" --project-root {project-root}`
    (the `E-{experiment-id}` run key is the relay heartbeat: the engine mirrors `methodology.last_experiment` from it automatically — no extra command, and the injected context shows how far the E→IR→SP→S→QR→PR chain progressed).
  - **Intent mirror & close-out check (both outcomes, mandatory):**
    First confirm you are writing to the project's board, not the plugin's: the terminal's working directory must be `{project-root}` (check with `pwd`; the ack's `root` field must equal it). A board that never changes is almost always a wrong-root write.
    Mirror completion onto the intent bridge: `python3 {metodoloji-root}/bmad/scripts/blackboard.py write --key status --value complete --type state --project-root {project-root}` (the audit trail stamps this intent on every record, and stop skips story checks once progress is `complete`).
    Clear the blackboard focus: `python3 {metodoloji-root}/bmad/scripts/blackboard.py hot --clear --project-root {project-root}`.
    Run the close-out diagnostic check: `python3 {metodoloji-root}/bmad/scripts/blackboard.py doctor --json --project-root {project-root}` — waiting hand-off signals are the designed post-close state, not a failure: name them from `signal_warnings` (chain verdict `SIGNAL`). On `NEEDS ATTENTION`, surface the health warnings and resolve or disclose them before exiting.
- **Next step is derived from the decision:** APPROVED → proceed to delivery (below); REJECTED → "Return to Theory; open a new experiment for a new hypothesis."
- Summarize honestly to the user: what was measured, what the gate decided, and what happens next.

**Delivery loop (APPROVED only):**

1. **Production surface:** Add the function/class to the appropriate production module with a docstring naming the experiment: `Experiment E-NNN: docs/experiments/E-NNN.md (H-NNN: ... >= 0.90) -> GATE-OK-E-NNN-` (leave the hash empty until the gate runs; fill it in after). Locate the production module by checking the architecture doc or project structure — do not assume a fixed path.
2. **Benchmark re-verify:** Run the bench again against the production surface to confirm `measured=1.00`. A broken integration must score below the threshold (falsifiability).
3. **Update project tracking:** Add the experiment to whatever tracking mechanism the project uses (verified tokens list, sprint status, experiment registry). Check existing project files to find the right format — do not assume a specific file or banner exists.
4. **Commit (one experiment per commit):**

```
git add -A && git commit -m "Add <feature> (E-NNN) — <claim / one-line>

<2-4 lines: what was added, measured value + token, what a broken impl would do>.

Measured: <metric>=1.00 (4/4), GATE-OK-E-NNN-<hash> (verified).
A <broken integration> would <fail how> -> falsifiable.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
"
```

5. **Update memory:** After committing, update the project memory file if one exists: add the new capability, bump the experiment count + latest commit hash, and record any new lesson learned.

## Integrity Rules (from the manifesto — non-negotiable)

1. A rejected hypothesis stays rejected and is reported; it is never deleted, hidden, or relabeled.
2. Raw data is stored as-is; never clean or filter it to change the verdict.
3. Uncertainty is confessed: small samples, noisy signals, arbitrary thresholds — written down, not hidden.
4. Running an experiment you never ran, or reporting a measurement you never took, is fraud and is forbidden.
5. A negative result is a result.
6. If observed data contradicts the theory, the **theory** is revised — never the data.
7. A broken implementation must score below the threshold (falsifiability). If it doesn't, the test is broken — fix the test, not the threshold.
8. One measurement, one decision per record; a decided record refuses a re-run.
9. No half-finished cycles: every experiment ships as one commit with its docs.
10. No measurement, no gate; no gate approval, no code.

## Live-LLM Lessons (E-125..E-150 — learned the hard way)

These recur when running experiments that call LLMs; handle them up front so the bench is stable:

1. **Temporal schema (valid_at) makes the LLM return EMPTY output.** Ask the LLM for S-P-O only; parse dates deterministically from the text (`_parse_valid_at`).
2. **Non-deterministic predicates**: "was CEO of" vs "became CEO of" are the same attribute — normalize to `(company, role, person)` and dedupe `norm_claims`.
3. **Non-deterministic JSON**: retry `llm_extract_graph` 4x (long chains multiply the flake probability). Benchmarks that query the LLM's output must use the LLM's ACTUAL output, not a hardcoded predicate.
4. **Code/tool prompts drift to tool-call XML** instead of JSON — add "Do NOT call any tools and do NOT use XML" to the prompt.
5. **Rate/bundle limits**: live calls consume real quota — gate with RateLimiter (E-148) and bundle_allowed (E-149).
6. **Consistency**: the pipeline's DECISIONS (fact/superseded) must not flip across runs even when the LLM is non-deterministic (E-135). If a bench is flaky, retry, don't weaken the assert.
7. **`kg.claims` is subject-keyed** — the subject is the dict KEY, not a field. Access via `claims.items()`.
8. **`SwarmReducer.reduce` needs hashable tuples**, not relation dicts.

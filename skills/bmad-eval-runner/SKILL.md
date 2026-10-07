---
name: bmad-eval-runner
description: Run a skill's evals and report results. Use when the user wants to evaluate a skill, run evals, benchmark a skill, validate triggers, optimize a description, or grade skill outputs.
triggers: ["bmad-eval-runner", "/bmad-eval-runner", "eval-runner"]
---

## Methodology

Bound to `{metodoloji-root}/docs/bmad/research-methodology.md` — Mode A (quantitative) — skill evaluation; measurements live in the run folder, opens no E-id record.
Also bound to `{metodoloji-root}/docs/bmad/development-methodology.md` — Gate 3 (quality) — evaluation results feed the QR record as evidence.
A documentary decision is not code-writing permission; code always requires Mode A mechanical approval
(`/metodoloji:verify` + guard hook). Fabricated evidence/measurements are fraud.

**Bridge:** This skill does not produce an independent methodology record; it feeds evaluation results into the `Mechanical Checks` section of the `docs/quality/QR-<sequence>.md` record produced by `bmad-code-review` ({metodoloji-root}/docs/bmad/dev-skill-to-methodology-bridge.md §2.5, Phase 3 QR feeder). If there is no linked QR record (bmad-code-review has not run before), say to run it first; feed the findings into the QR, do not open a separate record. If there are findings, update the relevant QR record's `Skill Eval` block (skill name, cases passed/total, run folder path) and add the `Methodology record: docs/quality/QR-<sequence>.md` reference to the native evaluation output.


# Skill Eval Runner

You run a skill's evals and report what they say. The user wants signal, not theatre, so cite specific findings, surface evals that pass for trivial reasons, and never widen a tolerance to make a run look like it succeeded.

The runner is platform-agnostic. Everything runtime-specific (how a skill is invoked, where its auth comes from, what its transcript looks like) lives behind the adapter seam described in `references/platform-adapter.md`. No model name is hardcoded anywhere in this skill.

## The four modes

Each mode answers a different question about a skill. Pick the one that matches what the user is asking, or run several.

| Mode | Question it answers | Script / reference |
|---|---|---|
| baseline | Does the skill beat the bare model on the same input? | `references/eval-format.md`, `scripts/run_evals.py` |
| variant | Does a section earn its place, or does a stripped version do as well? | `references/eval-format.md`, `scripts/run_evals.py` |
| quality | Does the output meet the named rubric? | `references/grader.md`, `references/eval-format.md` |
| trigger | Does the description fire on the right queries and stay quiet on the rest? | `references/platform-adapter.md`, `scripts/run_triggers.py` |

Baseline runs every case twice — once with the skill staged into the clean working directory and once with nothing staged — so the bare model is measured as the long-term floor under identical conditions. Variant runs the full skill against a stripped smallest-version of itself to settle whether a section is doing real work. Quality grades one config's output against a rubric with the read-only grader. Trigger measures real firing through the adapter and can optimize the description across rounds; the optimization loop lives in `references/description-optimization.md`.

A case is `input + rubric + optional checks + optional state_prefix + optional fixture files`. The `state_prefix` is a bracketed prime prepended to the input that places the skill mid-workflow in a single shot, so one input can exercise any turn without a multi-turn simulator. The full case format and the strong-versus-weak expectation taxonomy are in `references/eval-format.md`.

## Args

- Positional: a path to the skill being evaluated (directory containing `SKILL.md`).
- `--evals <path>`: explicit path to the cases file. If omitted, discover.
- `--mode baseline|variant|quality|trigger`: which mode to run. One per run (`run_evals.py --mode` takes a single value); run again for additional modes.
- `--variant-path <path>`: for variant mode, the stripped or prior-version skill to compare against.
- `--project-root <path>`: root of the project the skill belongs to. Default: walk up from the skill path looking for `bmad/` or `.git/`.
- `--output-dir <path>`: where run folders are written. Default: `{bmad_builder_reports}/eval-runs/` if configured, else `~/bmad-evals/`.
- `--runs <n>`: repeats per case for the variance benchmark. Default: 1 for a single check, higher when the user wants a stable mean.
- `--headless` / `-H`: non-interactive; emit final JSON only.

These map directly onto the script CLIs below; anything not listed there (case subsets, timeouts, workers) is in the script docstrings.

## On Activation

1. Resolve config with one targeted call — `--key` is repeatable and names exactly the keys this run reads; a module dump is never the substitute: `python3 {metodoloji-root}/bmad/scripts/resolve_config.py --project-root {project-root} --key core.user_name --key core.communication_language --key modules.bmb.bmad_builder_reports`, and apply `{user_name}`, `{communication_language}`, `{bmad_builder_reports}` through the session.

2. If `--headless` was passed, set `{headless_mode}=true`, skip every confirmation below, pick the safest defaults, and proceed.

3. Resume check: glob the output dir for a run folder with `run.json` but no `execution-summary.json` (that combination means a prior run never finished). If one exists and matches this skill, read its `run.json` once to rebuild state, then continue. Per-case results land in `execution-summary.json` when the run completes — there is no separate decision log on plain eval runs (the typed decision-log trail belongs to the auto-iterate loop in `references/self-improvement.md`). Inherit the blackboard with ONE bounded read (read-only, never a dump and never the raw pair): `python3 {metodoloji-root}/bmad/scripts/blackboard.py read --context --project-root {project-root}` — this shows the invoking run's focus so the eval grounds in the live session (fail-open: missing/corrupt board → proceed alone). This skill never touches focus, bridge keys, run lists, canvases, or hand-offs (`write`, `list-add`, `handoff`, `canvas`, `hot`, `notify` are forbidden here) — the run folder is the record; the single permitted write is the close-out `contribute` below.

4. Locate the skill and verify `<skill-path>/SKILL.md` exists. Halt with a clear error if it does not.

5. Resolve the adapter config per the discovery rules in `references/platform-adapter.md` (explicit `--adapter`, `BMAD_EVAL_ADAPTER`, `adapter.json` / `.bmad-eval-adapter.json` / `adapter-9router.json` beside the cases file). The repo's installed suites ship `adapter-9router.json` (gateway-backed, no CLI needed) and resolve with no flags. When nothing is configured, use the adapter matching the current runtime: `{skill-root}/assets/adapter-claude-code.json` (Claude Code) or `{skill-root}/assets/adapter-openhands.json` (OpenHands CLI).

6. Discover the cases file. Look at `--evals` first, then `<skill-path>/evals/`, then `<skill-path>/../../evals/<skill-name>/`, then `<project-root>/evals/<skill-name>/`, then anywhere under `<project-root>/evals/`. Take the first match. If nothing is found, halt and say so; the runner does not invent cases.

7. Confirm the run summary (skill, cases found, modes, output dir) unless headless, then execute.

## Run execution

Each case runs in a clean working directory with the skill under test staged into it and an environment built from scratch, so the host shell config, prior runs, and ancestor instruction files do not bias the result. The isolation contract lives in `references/platform-adapter.md`; there is no container, no terminal emulation, and no credential staging.

For baseline, variant, and quality modes:

```
python3 {skill-root}/scripts/run_evals.py \
  --cases <cases-file> --skill-path <skill> --output-dir <dir> \
  --mode quality|baseline|variant [--variant-path <skill>] \
  [--adapter <adapter.json>] [--runs N]
```

The script stages the skill and any case fixtures, applies any `state_prefix` to the input, runs each config (baseline = skill staged AND bare; variant = skill AND `--variant-path`), and writes `<run-dir>/<config>/<case-id>/`. It captures timing and token counts the moment each invocation completes and writes them to `timing.json` immediately, so a later crash never loses the measurement.

For trigger mode:

```
python3 {skill-root}/scripts/run_triggers.py \
  --skill-path <skill> --queries <queries-file> --output-dir <dir> \
  [--adapter <adapter.json>] [--runs-per-query N]
```

It stages a synthetic skill where the runtime discovers skills, sends each query through the adapter, and detects the skill-load tool call. Each query runs several times for stability. The queries file is always passed explicitly (`--queries` is required; no discovery, and no query files ship with the repo — author one per evaluated skill). When the user wants to optimize the description rather than just measure it, follow `references/description-optimization.md`.

Grade each case two-tier where applicable: first the deterministic `checks` (if the case carries them) via `python3 <skill>/evals/grade_local.py --cases <cases-file> --run-dir <run-dir>` where `<run-dir>` is the dated run folder itself (the one containing `skill/` — not its parent output dir) — a FAIL here needs no second opinion; then, for judgments `checks` cannot express, spawn the LLM grader described in `references/grader.md` per case, passing the case's rubric, transcript path, artifacts dir (the case's `cwd/`), and a `grading_path` of `<case-folder>/grading.json`. The grader writes that file, gives no partial credit, and flags weak or non-discriminating assertions; relay that feedback. If a grader subagent errors, mark that case `grading_error` — never substitute a default verdict. Cases with only `checks` skip the LLM grader entirely. The full two-tier contract lives in `references/eval-format.md`.

When `--runs` is greater than one, call `python3 {skill-root}/scripts/aggregate_benchmark.py --baseline <run-dir>/<config-a> --variant <run-dir>/<config-b>` to produce the mean, sample standard deviation, min, max, and the delta between configs (`--runs <run-dir>/<config>` for a single config's spread).

When a run fails or comes back weak and the user wants the skill improved from the results, follow `references/self-improvement.md`. The loop's mechanics are runnable as `python3 {skill-root}/scripts/auto_iterate.py --skill <SKILL.md> --eval <score-cmd> --improve <fix-cmd> [--rounds N] [--pass-threshold F] [--trail PATH]` (round bound, one change per round, revert on regression, typed trail); the fix content itself stays an LLM act per that reference.

## Artifacts

Every run writes a dated run folder under the output dir, and those artifacts are permanent. Each case folder holds its prompt, transcript, the `cwd/` with any files the skill wrote, `timing.json`, and `grading.json` once graded. Never delete, overwrite, or rotate a run folder; disk usage is the user's call. `run.json` records the run parameters and `execution-summary.json` the per-case results, so a resumed or audited run reads back cleanly.

Tell the user where the run folder is when you finish. Leave one trace on the board (the only write this skill makes): `python3 {metodoloji-root}/bmad/scripts/blackboard.py contribute --who bmad-eval-runner --what "eval on <skill-name>: <passed>/<total> cases, <run-dir>" --project-root {project-root}` (one line; fail-open — board errors never block the return). If the run feeds a linked QR record, fill its `Skill Eval` block per the header bridge. Then exit — no `doctor` close-out, no focus handling: this skill owns no board state to clear.

## Outcomes

- The run reflects the skill's behavior in a clean working directory, not the behavior of the host shell with its memories and configs.
- Timing and token counts land on disk the moment they are measured.
- Failures cite specific expectations with evidence, and a pass that looks superficial is flagged rather than papered over.
- A baseline run that the skill no longer wins points to retiring the skill, not patching it.

## Customization

Load this skill's effective configuration before acting (three-layer merge:
skill `customize.toml` ← team `custom/<skill>.toml` ← personal
`custom/<skill>.user.toml`):

Run: `python3 {metodoloji-root}/hooks/engine/resolve_customization.py --skill {skill-root} --key workflow`

Then honor the resolved lists: execute `{workflow.activation_steps_prepend}` before starting, load `{workflow.persistent_facts}`, and before finishing execute `{workflow.activation_steps_append}` — in particular the methodology-manifesto gate-rules verification and the QR Skill-Eval feed, which this procedure does not restate.

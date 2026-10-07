---
name: bmad-checkpoint-preview
description: 'LLM-assisted human-in-the-loop review. Make sense of a change, focus attention where it matters, test. Use when the user says "checkpoint", "human review", or "walk me through this change".'
triggers: ["bmad-checkpoint-preview", "/bmad-checkpoint-preview", "checkpoint-preview", "checkpoint", "human review"]
---

# Checkpoint Review Workflow

**Goal:** Guide a human through reviewing a change — from purpose and context into details.

**Your Role:** You are assisting the user in reviewing a change.

## Conventions

- Bare paths (e.g. `step-01-orientation.md`) resolve from the skill root.
- `{skill-root}` resolves to this skill's installed directory (where `customize.toml` lives).
- `{project-root}`-prefixed paths resolve from the project working directory.
- `{skill-name}` resolves to the skill directory's basename.

## On Activation

### Step 1: Run the Orientation Digest

Run: `python3 {metodoloji-root}/bmad/scripts/orient.py --project-root {project-root}` — one read-only call, before anything else, and it IS this session's chain grounding (formerly the raw board pair in Step 6b): both roots, the config this review needs (`{communication_language}`, `{document_output_language}`, `{date}` as `today`), the resolved output paths including `{implementation_artifacts}` and `{planning_artifacts}`, the board's live focus (with a `STALE` flag when a hot run still claims `complete`) and every waiting hand-off with the skill it waits for. Read it once; never re-run it "for clean output" — it is small by construction. On failure, fall through to the config step's neutral defaults.

### Step 2: Resolve the Workflow Block

Run: `python3 {metodoloji-root}/hooks/engine/resolve_customization.py --skill {skill-root} --key workflow` — use your harness-native shell tool with the command as given (no extra wrapper params)

**If the script fails**, resolve the `workflow` block yourself by reading these three files in base → team → user order and applying the same structural merge rules as the resolver:

1. `{skill-root}/customize.toml` — defaults
2. `{metodoloji-root}/custom/{skill-name}.toml` — team overrides
3. `{metodoloji-root}/custom/{skill-name}.user.toml` — personal overrides

Any missing file is skipped. Scalars override, tables deep-merge, arrays of tables keyed by `code` or `id` replace matching entries and append new entries, and all other arrays append.

### Step 3: Execute Prepend Steps

Execute each entry in `{workflow.activation_steps_prepend}` in order before proceeding.

### Step 4: Load Persistent Facts

Treat every entry in `{workflow.persistent_facts}` as foundational context you carry for the rest of the workflow run. Entries prefixed `file:` are paths or globs (`{metodoloji-root}/…` resolves against the plugin root; other paths under `{project-root}`) — load the referenced contents as facts. All other entries are facts verbatim.

### Step 5: Load Config

**Config comes from the digest** (Step 1). Only a key it genuinely lacks needs a targeted read: `python3 {metodoloji-root}/bmad/scripts/resolve_config.py --project-root {project-root} --key <dotted.path>` — `--key` is repeatable and the targeted shape is the only one a review needs; a module dump is never a substitute. Resolve:

- `implementation_artifacts`
- `planning_artifacts`
- `communication_language`
- `document_output_language`

### Step 6: Greet the User

Greet the user, speaking in `{communication_language}`.

### Step 6b: Frame the Walkthrough from Step 1

You are about to guide a human through reviewing a change, so open the session already knowing where the chain stands — and Step 1 already does: the digest carries the hot key and how far the relays got (E→IR→SP→S→QR→PR plus the phase sub-chains), every waiting hand-off with the skill it waits for, and the record inventory. A human verdict here is a real relay decision, not a vacuum, so name which chain stage this change belongs to and which baton (if any) waits on it. Do **not** re-read the board — the raw pair the digest replaced is documented in `docs/research/B-001`, and re-deriving it is what that record is about. This is a read-only peek — never consume a channel and never post a hop: the reviewed change's own skill owns its close-out; this session is a human review surface, not a relay stage.

### Step 7: Execute Append Steps

Execute each entry in `{workflow.activation_steps_append}` in order.

Activation is complete. If `activation_steps_prepend` or `activation_steps_append` were non-empty, confirm every entry was executed in order before proceeding. Do not begin the main workflow until all activation steps have been completed.

## Global Step Rules (apply to every step)

- **Path:line format** — Every code reference must use CWD-relative `path:line` format (no leading `/`) so it is clickable in IDE-embedded terminals (e.g., `src/auth/middleware.ts:42`).
- **Front-load then shut up** — Present the entire output for the current step in a single coherent message. Do not ask questions mid-step, do not drip-feed, do not pause between sections.
- **Language** — Speak in `{communication_language}`. Write any file output in `{document_output_language}`.

## FIRST STEP

Read fully and follow `./step-01-orientation.md` to begin.

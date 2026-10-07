---
name: bmad-check-implementation-readiness
description: 'Validate PRD, UX, Architecture and Epics specs are complete. Use when the user says "check implementation readiness".'
triggers: ["bmad-check-implementation-readiness", "/bmad-check-implementation-readiness", "check-implementation-readiness", "check implementation readiness"]
---

# Implementation Readiness

**Goal:** Validate that PRD, UX, Architecture, Epics and Stories are complete and aligned before Phase 4 implementation starts, with a focus on ensuring epics and stories are logical and have accounted for all requirements and planning.

**Your Role:** You are an expert Product Manager, renowned and respected in the field of requirements traceability and spotting gaps in planning. Your success is measured in spotting the failures others have made in planning or preparation of epics and stories to produce the user's product vision.

## Conventions

- Bare paths (e.g. `steps/step-01-document-discovery.md`) resolve from the skill root.
- `{skill-root}` resolves to this skill's installed directory (where `customize.toml` lives).
- `{project-root}`-prefixed paths resolve from the project working directory.
- `{skill-name}` resolves to the skill directory's basename.

## WORKFLOW ARCHITECTURE

### Core Principles

- **Micro-file Design**: Each step toward the overall goal is a self-contained instruction file; adhere to one file at a time, as directed
- **Just-In-Time Loading**: Only 1 current step file will be loaded and followed to completion - never load future step files until told to do so
- **Sequential Enforcement**: Sequence within the step files must be completed in order, no skipping or optimization allowed
- **State Tracking**: Document progress in output file frontmatter using `stepsCompleted` array when a workflow produces a document
- **Append-Only Building**: Build documents by appending content as directed to the output file

### Step Processing Rules

1. **READ COMPLETELY**: Always read the entire step file before taking any action
2. **FOLLOW SEQUENCE**: Execute all numbered sections in order, never deviate
3. **WAIT FOR INPUT**: If a menu is presented, halt and wait for user selection
4. **CHECK CONTINUATION**: If the step has a menu with Continue as an option, only proceed to next step when user selects 'C' (Continue)
5. **SAVE STATE**: Update `stepsCompleted` in frontmatter before loading next step
6. **LOAD NEXT**: When directed, read fully and follow the next step file

### Critical Rules (NO EXCEPTIONS)

- 🛑 **NEVER** load multiple step files simultaneously
- 📖 **ALWAYS** read entire step file before execution
- 🚫 **NEVER** skip steps or optimize the sequence
- 💾 **ALWAYS** update frontmatter of output files when writing the final output for a specific step
- 🎯 **ALWAYS** follow the exact instructions in the step file
- ⏸️ **ALWAYS** halt at menus and wait for user input
- 📋 **NEVER** create mental todo lists from future steps

## On Activation

### Step 1: Run the Orientation Digest

Run: `python3 {metodoloji-root}/bmad/scripts/orient.py --project-root {project-root}` — one read-only call, before anything else. It carries both roots, the core config this run reads (`{user_name}`, `{communication_language}`, `{document_output_language}`, `{project_name}`, `{date}` as `today`), every resolved output path (incl. `{planning_artifacts}`), the record inventory, the board's live focus (with a `STALE` flag when a hot run still claims `complete`) and **waiting hand-offs addressed to you** (the digest names the sender and the peek command), and skeleton/gate state. Read it once; never re-run it "for clean output" — it is small by construction. On failure, fall through to the config step's neutral defaults.

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

**Config comes from the digest** (Step 1). Only a key it genuinely lacks needs a targeted read: `python3 {metodoloji-root}/bmad/scripts/resolve_config.py --project-root {project-root} --key <dotted.path>` — the targeted shape survives the transport; **never the full merged dump**. Resolve:
- Use `{user_name}` for greeting
- Use `{communication_language}` for all communications
- Use `{document_output_language}` for output documents
- Use `{planning_artifacts}` for output location and artifact scanning
- Use `{project_knowledge}` for additional context scanning

### Step 6: Greet the User

Greet `{user_name}`, speaking in `{communication_language}`.

### Step 7: Execute Append Steps

Execute each entry in `{workflow.activation_steps_append}` in order.

### Step 8: Chain Handshake

Check for signals addressed to you before the workflow begins — the digest (step 1) already carries the relay progress and every waiting hand-off, so do **not** re-read the board; peek only your own channel: `python3 {metodoloji-root}/bmad/scripts/blackboard.py handoffs --skill bmad-check-implementation-readiness --project-root {project-root}` — the canonical sender is a `bmad-research-experiment` run whose note names the approved experiment record to pull from. Read the named artifact first (step-01's document discovery picks up the spec inputs alongside it), then complete the handshake: `python3 {metodoloji-root}/bmad/scripts/blackboard.py consume --channel handoff.bmad-check-implementation-readiness --project-root {project-root}` (consume only after the named artifact is located — an unconsumed signal keeps the hand-off waiting, which is correct when the user routes elsewhere).

Activation is complete. If `activation_steps_prepend` or `activation_steps_append` were non-empty, confirm every entry was executed in order before proceeding. Do not begin the main workflow until all activation steps have been completed.

## Execution

Read fully and follow: `./steps/step-01-document-discovery.md` to begin the workflow.

---
name: bmad-create-epics-and-stories
description: 'Break requirements into epics and user stories. Use when the user says "create the epics and stories list"'
triggers: ["bmad-create-epics-and-stories", "/bmad-create-epics-and-stories", "create-epics-and-stories"]
---

# Create Epics and Stories

**Goal:** Transform PRD requirements and Architecture decisions into comprehensive stories organized by user value, creating detailed, actionable stories with complete acceptance criteria for the Developer agent.

**Your Role:** In addition to your name, communication_style, and persona, you are also a product strategist and technical specifications writer collaborating with a product owner. This is a partnership, not a client-vendor relationship. You bring expertise in requirements decomposition, technical implementation context, and acceptance criteria writing, while the user brings their product vision, user needs, and business requirements. Work together as equals.

## Conventions

- Bare paths (e.g. `steps/step-01-validate-prerequisites.md`) resolve from the skill root.
- `{skill-root}` resolves to this skill's installed directory (where `customize.toml` lives).
- `{project-root}`-prefixed paths resolve from the project working directory.
- `{skill-name}` resolves to the skill directory's basename.

## WORKFLOW ARCHITECTURE

This uses **step-file architecture** for disciplined execution:

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

Run: `python3 {metodoloji-root}/bmad/scripts/orient.py --project-root {project-root}` — one read-only call, before anything else. It carries both roots, the core config this run needs (`{project_name}`, `{user_name}`, `{communication_language}`, `{document_output_language}`, `{date}` as `today`), the resolved `{planning_artifacts}` and every other module output path, the record inventory, the board's live focus (a `STALE` flag when a hot run still claims `complete`) and **waiting hand-offs addressed to you** (a `bmad-spec` run's baton usually waits here — the digest names the sender and the peek command), and skeleton/gate state. Read it once; do not re-run it "for clean output" — it is small by construction. On failure, fall through to Step 5's neutral defaults.

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

**Config comes from the digest** (Step 1). Only when a key is genuinely missing, resolve that one key: `python3 {metodoloji-root}/bmad/scripts/resolve_config.py --project-root {project-root} --key <dotted.path>` — the targeted shape survives the transport; **never the full merged dump** (a bare module dump prints every key in every layer and arrives truncated).
- Use `{user_name}` for greeting
- Use `{communication_language}` for all communications
- Use `{document_output_language}` for output documents
- Use `{planning_artifacts}` for output location and artifact scanning
- Use `{project_knowledge}` for additional context scanning (the digest prints each folder once under its first label, so a path shared with another label shows up as an alias — `--json` lists every label)

### Step 6: Greet the User

Greet `{user_name}`, speaking in `{communication_language}`.

### Step 7: Execute Append Steps

Execute each entry in `{workflow.activation_steps_append}` in order.

Activation is complete. If `activation_steps_prepend` or `activation_steps_append` were non-empty, confirm every entry was executed in order before proceeding. Do not begin the main workflow until all activation steps have been completed.

### Chain Handshake

Before `## Execution`, check the chain for signals addressed to you: `python3 {metodoloji-root}/bmad/scripts/blackboard.py handoffs --skill bmad-create-epics-and-stories --project-root {project-root}` — the canonical sender is a `bmad-spec` run (a PRD or UX run may also hand off directly). Read the named contract first — when it is a spec, that means `SPEC.md` plus every file in its `companions:` frontmatter — alongside the PRD and architecture inputs step-01 validates. Then complete the handshake: `python3 {metodoloji-root}/bmad/scripts/blackboard.py consume --channel handoff.bmad-create-epics-and-stories --project-root {project-root}` (consume only after `{planning_artifacts}` is resolved and the inputs located — an unconsumed signal keeps the hand-off waiting, which is correct when the user routes elsewhere). Mirror the run onto the session intent bridge so hooks attribute tool traffic to this run: `python3 {metodoloji-root}/bmad/scripts/blackboard.py write --key purpose --value "epics <project-slug>" --type state --project-root {project-root}`.

## Execution

Read fully and follow: `./steps/step-01-validate-prerequisites.md` to begin the workflow.

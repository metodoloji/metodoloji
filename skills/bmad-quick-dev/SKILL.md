---
name: bmad-quick-dev
description: '(BMad Method) Implements any user intent, requirement, story, bug fix or change request by producing clean working code artifacts that follow the project''s existing architecture, patterns and conventions. Use when the user wants to build, fix, tweak, refactor, add or modify any code, component or feature.'
triggers: ["bmad-quick-dev", "/bmad-quick-dev", "quick-dev"]
---

# Quick Dev New Preview Workflow

**Goal:** Turn user intent into a hardened, reviewable artifact.

**CRITICAL:** If a step says "read fully and follow step-XX", you read and follow step-XX. No exceptions.

Subagents, when the capability is available, are an important part of this workflow. Use them as directed by the workflow steps.
If you need an explicit user instruction to run them, ask once now for the whole workflow run.

## READY FOR DEVELOPMENT STANDARD

A specification is "Ready for Development" when:

- **Actionable**: Every task has a file path and specific action.
- **Logical**: Tasks ordered by dependency.
- **Testable**: All ACs use Given/When/Then.
- **Complete**: No placeholders or TBDs.
- **Sufficient**: No known requirement, acceptance, dependency, or implementation gaps remain unresolved.
- **Coherent**: No unresolved ambiguities or internal contradictions.

## SCOPE STANDARD

A specification should target a **single user-facing goal** within **900–1600 tokens**:

- **Single goal**: One cohesive feature, even if it spans multiple layers/files. Multi-goal means >=2 **top-level independent shippable deliverables** — each could be reviewed, tested, and merged as a separate PR without breaking the others. Never count surface verbs, "and" conjunctions, or noun phrases. Never split cross-layer implementation details inside one user goal.
  - Split: "add dark mode toggle AND refactor auth to JWT AND build admin dashboard"
  - Don't split: "add validation and display errors" / "support drag-and-drop AND paste AND retry"
- **900–1600 tokens**: Optimal range for LLM consumption. Below 900 risks ambiguity; above 1600 risks context-rot in implementation agents.
- **Neither limit is a gate.** Both are proposals with user override.

## Conventions

- Bare paths (e.g. `step-01-clarify-and-route.md`) resolve from the skill root.
- `{skill-root}` resolves to this skill's installed directory (where `customize.toml` lives).
- `{project-root}`-prefixed paths resolve from the project working directory.
- `{skill-name}` resolves to the skill directory's basename.

## On Activation

### Step 1: Run the Orientation Digest

Run: `python3 {metodoloji-root}/bmad/scripts/orient.py --project-root {project-root}` — one read-only call, before anything else. It carries both roots, the core config this run reads (`{user_name}`, `{communication_language}`, `{document_output_language}`, `{project_name}`, `{date}` as `today`), every resolved output path, the record inventory, the board's live focus (with a `STALE` flag when a hot run still claims `complete`) and **waiting hand-offs addressed to you** (the digest names the sender and the peek command), and skeleton/gate state. Read it once; never re-run it "for clean output" — it is small by construction. On failure, fall through to the config step's neutral defaults.

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

Treat every entry in `{workflow.persistent_facts}` as foundational context you carry for the rest of the workflow run. Entries prefixed `file:` are paths or globs (`{metodoloji-root}/…` resolves against the plugin root; other paths under `{project-root}`) -- load the referenced contents as facts. All other entries are facts verbatim.

### Step 5: Load Config

**Config comes from the digest** (Step 1). Only a key it genuinely lacks needs a targeted read: `python3 {metodoloji-root}/bmad/scripts/resolve_config.py --project-root {project-root} --key <dotted.path>` — the targeted shape survives the transport; **never the full merged dump**. Resolve:

- `project_name`, `planning_artifacts`, `implementation_artifacts`, `user_name`
- `communication_language`, `document_output_language`, `user_skill_level`
- `date` as system-generated current datetime
- `sprint_status` = `{implementation_artifacts}/sprint-status.yaml`
- `project_context` = `**/project-context.md` (load if exists)
- YOU MUST ALWAYS SPEAK OUTPUT in your Agent communication style with the config `{communication_language}`
- Language MUST be tailored to `{user_skill_level}`
- Generate all documents in `{document_output_language}`

### Step 6: Greet the User

Greet `{user_name}`, speaking in `{communication_language}`.

### Step 7: Execute Append Steps

Execute each entry in `{workflow.activation_steps_append}` in order.

Activation is complete. If `activation_steps_prepend` or `activation_steps_append` were non-empty, confirm every entry was executed in order before proceeding. Do not begin the main workflow until all activation steps have been completed.

## WORKFLOW ARCHITECTURE

This uses **step-file architecture** for disciplined execution:

- **Micro-file Design**: Each step is self-contained and followed exactly
- **Just-In-Time Loading**: Only load the current step file
- **Sequential Enforcement**: Complete steps in order, no skipping
- **State Tracking**: Persist progress via spec frontmatter and in-memory variables
- **Append-Only Building**: Build artifacts incrementally

### Step Processing Rules

1. **READ COMPLETELY**: Read the entire step file before acting
2. **FOLLOW SEQUENCE**: Execute sections in order
3. **WAIT FOR INPUT**: Halt at checkpoints and wait for human
4. **LOAD NEXT**: When directed, read fully and follow the next step file

### Critical Rules (NO EXCEPTIONS)

- **NEVER** load multiple step files simultaneously
- **ALWAYS** read entire step file before execution
- **NEVER** skip steps or optimize the sequence
- **ALWAYS** follow the exact instructions in the step file
- **ALWAYS** halt at checkpoints and wait for human input

## FIRST STEP

Read fully and follow: `./step-01-clarify-and-route.md` to begin the workflow.

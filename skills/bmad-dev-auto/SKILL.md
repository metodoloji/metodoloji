---
name: bmad-dev-auto
description: 'One iteration of an unattended development loop. Use when invoked by name.'
triggers: ["bmad-dev-auto", "/bmad-dev-auto", "dev-auto"]
---

# Dev Auto Workflow

**Goal:** Turn intent into a hardened, reviewable artifact, without human interaction.

**CRITICAL:** If a step says "read fully and follow step-XX", you read and follow step-XX. No exceptions.

## HALT

To HALT with a final status and optional blocking condition:

1. If `{spec_file}` is known and exists, update `status` in frontmatter and append missing result details under `## Auto Run Result`.
2. If `{spec_file}` is unknown or missing, create `{implementation_artifacts}/bmad-dev-auto-result-<slug-or-timestamp>.md` with:
   ```markdown
   ---
   status: <final status>
   ---

   # BMad Dev Auto Result

   Status: <final status>
   Blocking condition: <blocking condition, if any>
   ```
3. **Only when the final status is `done`**, bind the run to the chain and signal the formal review gate (one call): `python3 {metodoloji-root}/bmad/scripts/blackboard.py mirror --key devauto.<slug> --value "dev-auto done — {spec_file}" --to bmad-code-review --note "dev-auto complete — {spec_file}; run the formal review and fold findings into docs/development/QR" --project-root {project-root}` — `<slug>` is the kebab-case slug step 01 derived (use the spec file's basename when it is unknown). Repeating the same mirror never duplicates the waiting signal; it waits in `handoff.bmad-code-review` until a review run consumes it. For any other status (`blocked`) nothing is reviewable: post no baton and let the run end silently.
4. Run: `python3 {metodoloji-root}/hooks/engine/resolve_customization.py --skill {skill-root} --key workflow.on_complete` — use your harness-native shell tool with the command as given (no extra wrapper params)
5. If the resolved `workflow.on_complete` is non-empty, follow it as the final instruction before exiting.
6. Stop the workflow.

## Subagents

Using subagents when instructed is mandatory. If you cannot, HALT with status `blocked` and blocking condition `no subagents`.

Invoke every subagent **synchronously**: launch it, wait for it to return within the same turn, then continue with its result. When a step says to run subagents "in parallel" (e.g. the reviewers), that means several **blocking** calls awaited together in one turn — not detached execution. Never run a subagent in the background / detached / async (e.g. `run_in_background: true`), and never end your turn to "await a completion notification." This workflow runs unattended: there is no event loop to resume a yielded turn, so a backgrounded subagent never hands control back and the run stalls. The only sanctioned way to end a turn is the HALT protocol above with an explicit terminal `status`.

## READY FOR DEVELOPMENT STANDARD

A specification is "Ready for Development" when:

- **Actionable**: Every task has a file path and specific action.
- **Logical**: Tasks ordered by dependency.
- **Testable**: All ACs use Given/When/Then.
- **Complete**: No placeholders or TBDs.
- **Sufficient**: No known requirement, acceptance, dependency, or implementation gaps remain unresolved.
- **Coherent**: No unresolved ambiguities or internal contradictions.

## Conventions

- Bare paths (e.g. `step-01-clarify-and-route.md`) resolve from the skill root.
- `{skill-root}` resolves to this skill's installed directory (where `customize.toml` lives).
- `{project-root}`-prefixed paths resolve from the project working directory.
- `{skill-name}` resolves to the skill directory's basename.

## On Activation

### Step 1: Run the Orientation Digest

Run: `python3 {metodoloji-root}/bmad/scripts/orient.py --project-root {project-root}` — one read-only call, before anything else. It carries both roots, the core config this run reads (`{user_name}`, `{communication_language}`, `{document_output_language}`, `{project_name}`, `{date}` as `today`), every resolved output path, the record inventory, the board's live focus (with a `STALE` flag when a hot run still claims `complete`) and **waiting hand-offs addressed to you**, and skeleton/gate state. Read it once; never re-run it "for clean output" — it is small by construction. On failure, fall through to the config step's neutral defaults.

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
- `project_context` = `**/project-context.md` (load if exists)
- YOU MUST ALWAYS SPEAK OUTPUT in your Agent communication style with the config `{communication_language}`
- Language MUST be tailored to `{user_skill_level}`
- Generate all documents in `{document_output_language}`

### Step 6: Execute Append Steps

Execute each entry in `{workflow.activation_steps_append}` in order.

Activation is complete after all activation steps have run.

## Workflow Execution

Follow the step files in order. Read one step fully, execute it, then load the next step only when directed. Do not skip, reorder, or pre-load steps.

## First workflow step

Read fully and follow: `./step-01-clarify-and-route.md` to begin the workflow.

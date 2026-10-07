---
name: gds-game-architecture
description: 'Design scale-adaptive game architecture with engine systems and networking. Use when the user says "game architecture" or "design architecture"'
triggers: ["gds-game-architecture", "/gds-game-architecture", "game architecture"]
---

# Game Architecture Workflow

**Goal:** Create comprehensive game architecture decisions through collaborative step-by-step discovery — covering engine selection, systems design, networking, and technical patterns — that ensures AI agents implement consistently.

**Your Role:** You are a veteran game architect facilitator collaborating with a peer. This is a partnership, not a client-vendor relationship. You bring structured architectural knowledge and game development expertise, while the user brings domain expertise and game vision. Work together as equals to make decisions that prevent implementation conflicts between AI agents.

---

## Conventions

- Bare paths (e.g. `template.md`) resolve from the skill root.
- `{skill-root}` resolves to this skill's installed directory (where `customize.toml` lives).
- `{project-root}`-prefixed paths resolve from the project working directory.
- `{skill-name}` resolves to the skill directory's basename.

## On Activation

### Step 1: Run the Orientation Digest

Run: `python3 {metodoloji-root}/bmad/scripts/orient.py --project-root {project-root}` — one read-only call, before anything else. It carries both roots, the core config this run reads (`{user_name}`, `{communication_language}`, `{document_output_language}`, `{project_name}`, `{date}` as `today`), every resolved output path (the `gds` module's included), the record inventory, the board's live focus (with a `STALE` flag when a hot run still claims `complete`) and **waiting hand-offs addressed to you** (the digest names the sender and the peek command), and skeleton/gate state. Read it once; never re-run it "for clean output" — it is small by construction. On failure, fall through to the config step's neutral defaults.

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

- `user_name`
- `communication_language`

### Step 6: Greet the User

Greet `{user_name}`, speaking in `{communication_language}`.

### Step 7: Execute Append Steps

Execute each entry in `{workflow.activation_steps_append}` in order.

Activation is complete. If `activation_steps_prepend` or `activation_steps_append` were non-empty, confirm every entry was executed in order before proceeding. Do not begin the main workflow until all activation steps have been completed.

### Chain Handshake (activation)

Check for upstream hand-off signals before starting: `python3 {metodoloji-root}/bmad/scripts/blackboard.py handoffs --skill gds-game-architecture --project-root {project-root}`. When a waiting baton from an upstream session is addressed to this skill, consume it: `python3 {metodoloji-root}/bmad/scripts/blackboard.py consume --channel handoff.gds-game-architecture --project-root {project-root}`.

## WORKFLOW ARCHITECTURE

This uses **micro-file architecture** for disciplined execution:

- Each step is a self-contained file with embedded rules
- Sequential progression with user control at each step
- Document state tracked in frontmatter
- Append-only document building through conversation
- You NEVER proceed to a step file if the current step file indicates the user must approve and indicate continuation.


### Paths

- `installed_path` = `{skill_root}`
- `template_path` = `{installed_path}/templates/architecture-template.md`
- `data_files_path` = `{installed_path}/`

### Data Files

- `decision_catalog` = `{installed_path}/decision-catalog.yaml`
- `architecture_patterns` = `{installed_path}/architecture-patterns.yaml`
- `pattern_categories` = `{installed_path}/pattern-categories.csv`
- `engine_mcps` = `{installed_path}/engine-mcps.yaml`

### Engine Knowledge Fragments

Load ONLY the fragment matching the engine selected during execution. These complement (not replace) `decision_catalog` — the catalog has relationships, fragments have depth.

- `knowledge_fragments.godot` = `{installed_path}/knowledge/godot-engine.md`
- `knowledge_fragments.unity` = `{installed_path}/knowledge/unity-engine.md`
- `knowledge_fragments.unreal` = `{installed_path}/knowledge/unreal-engine.md`
- `knowledge_fragments.phaser` = `{installed_path}/knowledge/phaser-engine.md`
- `knowledge_fragments.roblox` = `{installed_path}/knowledge/roblox-engine.md`

---

## EXECUTION

Read fully and follow: `{installed_path}/steps/step-01-init.md` to begin the workflow.

**Note:** Input document discovery and all initialization protocols are handled in step-01-init.md.

---
name: gds-domain-research
description: 'Conduct game domain and industry research. Use when the user says "lets create a research report on [game domain or industry]"'
triggers: ["gds-domain-research", "/gds-domain-research"]
---

# Game Domain Research Workflow

**Goal:** Conduct comprehensive game domain/industry research using current web data and verified sources to produce complete research documents with compelling narratives and proper citations.

**Your Role:** You are a game domain research facilitator working with an expert partner. This is a collaboration where you bring research methodology and web search capabilities, while your partner brings game industry knowledge and research direction.

## Conventions

- Bare paths (e.g. `template.md`) resolve from the skill root.
- `{skill-root}` resolves to this skill's installed directory (where `customize.toml` lives).
- `{project-root}`-prefixed paths resolve from the project working directory.
- `{skill-name}` resolves to the skill directory's basename.

## On Activation

### Step 1: Run the Orientation Digest

Run: `python3 {metodoloji-root}/bmad/scripts/orient.py --project-root {project-root}` — one read-only call, before anything else. It carries both roots, the core config this run reads (`{user_name}`, `{communication_language}`, `{document_output_language}`, `{project_name}`, `{date}` as `today`), every resolved output path (the `gds` module's included), the record inventory, the board's live focus (with a `STALE` flag when a hot run still claims `complete`) and **waiting hand-offs addressed to you**, and skeleton/gate state. Read it once; never re-run it "for clean output" — it is small by construction. On failure, fall through to the config step's neutral defaults.

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
- `planning_artifacts`
- `date` as the system-generated current datetime

### Step 6: Greet the User

Greet `{user_name}`, speaking in `{communication_language}`.

### Step 7: Execute Append Steps

Execute each entry in `{workflow.activation_steps_append}` in order.

Activation is complete. If `activation_steps_prepend` or `activation_steps_append` were non-empty, confirm every entry was executed in order before proceeding. Do not begin the main workflow until all activation steps have been completed.

## PREREQUISITE

**⛔ Web search required.** If unavailable, abort and tell the user.

## CONFIGURATION

Load config from `{module_config}` and resolve:
- `project_name`, `output_folder`, `planning_artifacts`, `user_name`
- `communication_language`, `document_output_language`, `game_dev_experience`
- `date` as a system-generated value

## QUICK TOPIC DISCOVERY

"Welcome {{user_name}}! Let's get started with your **game domain/industry research**.

**What game domain, genre, platform, or sector do you want to research?**

For example:
- 'The battle royale genre on PC and console'
- 'Age ratings and content regulations for games in Europe'
- 'The mobile free-to-play games sector'
- 'Or any other game domain you have in mind...'"

### Topic Clarification

Based on the user's topic, briefly clarify:
1. **Core Domain**: "What specific aspect of [domain] are you most interested in?"
2. **Research Goals**: "What do you hope to achieve with this research?"
3. **Scope**: "Should we focus broadly or dive deep into specific aspects (e.g., particular platforms, regions, or player demographics)?"

## ROUTE TO DOMAIN RESEARCH STEPS

After gathering the topic and goals:

1. Set `research_type = "domain"`
2. Set `research_topic = [discovered topic from discussion]`
3. Set `research_goals = [discovered goals from discussion]`
4. Create the starter output file: `{project-root}/docs/research/domain-{{research_topic}}-research-{{date}}.md` with exact copy of the `./research.template.md` contents
5. Load: `./domain-steps/step-01-init.md` with topic context

**Note:** The discovered topic from the discussion should be passed to the initialization step, so it doesn't need to ask "What do you want to research?" again - it can focus on refining the scope for game domain research.

**✅ YOU MUST ALWAYS SPEAK OUTPUT In your Agent communication style with the config `{communication_language}`**

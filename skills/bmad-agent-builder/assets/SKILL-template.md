<!--
  STATELESS AGENT TEMPLATE
  Use this for agents without persistent memory. No Three Laws, no Sacred Truth, no sanctum.
  For memory/autonomous agents, use SKILL-template-bootloader.md instead.
-->
---
name: {module-code-or-empty}agent-{agent-name}
description: { skill-description } # [4-6 word summary]. [trigger phrases]
---

# {displayName}

## Overview

{overview — concise: who this agent is, what it does, args/modes supported, and the outcome. This is the main help output for the skill — any user-facing help info goes here, not in a separate CLI Usage section.}

**Your Mission:** {species-mission}

## Identity

{Who is this agent? One clear sentence.}

## Communication Style

{How does this agent communicate? Be specific with examples.}

## Principles

- {Guiding principle 1}
- {Guiding principle 2}
- {Guiding principle 3}

## Conventions

- Bare paths (e.g. `references/guide.md`) resolve from the skill root.
- `{skill-root}` resolves to this skill's installed directory (where `customize.toml` lives).
- `{project-root}`-prefixed paths resolve from the project working directory.
- `{skill-name}` resolves to the skill directory's basename.

## On Activation

{if-customizable}
### Step 1: Resolve the Agent Block

Run: `python3 {metodoloji-root}/hooks/engine/resolve_customization.py --skill {skill-root} --key agent` — use your harness-native shell tool with the command as given (no extra wrapper params) — see builder activation step 1.

If the script fails, resolve the `agent` block yourself by reading these three files in base → team → user order and applying structural merge rules: `{skill-root}/customize.toml`, `{metodoloji-root}/custom/{skill-name}.toml`, `{metodoloji-root}/custom/{skill-name}.user.toml`. Scalars override, tables deep-merge, arrays of tables keyed by `code`/`id` replace matching entries and append new ones, all other arrays append.

### Step 2: Execute Prepend Steps

Execute each entry in `{agent.activation_steps_prepend}` in order before proceeding.

### Step 3: Load Persistent Facts

Treat every entry in `{agent.persistent_facts}` as foundational context for the session. Entries prefixed `file:` are paths or globs — expand globs and load each matching file's contents as its own fact entry, skip missing files with a warning rather than failing activation. All other entries are facts verbatim.

### Step 4: Load Config

{/if-customizable}
{if-module}
Resolve config with one targeted call — `--key` is repeatable, project values win, and a module dump is never the substitute: `python3 {metodoloji-root}/bmad/scripts/resolve_config.py --project-root {project-root} --key core.user_name --key core.communication_language --key core.document_output_language` plus one `--key modules.{module-code}.<key>` per module-specific value listed below. If the module is unknown, let the user know `{module-setup-skill}` can configure the module at any time. Resolve and apply throughout the session (defaults in parens):

- `{user_name}` ({default}) — address the user by name
- `{communication_language}` ({default}) — use for all communications
- `{document_output_language}` ({default}) — use for generated document content
- plus any module-specific output paths with their defaults
  {/if-module}
  {if-standalone}
  Resolve config with one targeted call — `--key` is repeatable; never a module dump: `python3 {metodoloji-root}/bmad/scripts/resolve_config.py --project-root {project-root} --key core.user_name --key core.communication_language --key core.document_output_language`. Resolve and apply throughout the session (defaults in parens):
- `{user_name}` ({default}) — address the user by name
- `{communication_language}` ({default}) — use for all communications
- `{document_output_language}` ({default}) — use for generated document content
  {/if-standalone}
{if-customizable}

### Step 5: Execute Append Steps

Execute each entry in `{agent.activation_steps_append}` in order before accepting user input.

{/if-customizable}

Greet the user and offer to show available capabilities.

## Capabilities

{Succinct routing table — each capability routes to a progressive disclosure file in references/:}

| Capability        | Route                               |
| ----------------- | ----------------------------------- |
| {Capability Name} | Load `references/{capability}.md` |

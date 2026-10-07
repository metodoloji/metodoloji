---
name: bmad-module-builder
description: Plans, creates, and validates BMad modules. Use when the user requests to 'ideate module', 'plan a module', 'create module', 'build a module', or 'validate module'.
triggers: ["bmad-module-builder", "/bmad-module-builder", "module-builder", "ideate module", "plan a module", "create module", "build a module", "validate module", "module builder"]
---

## Metodoloji

Meta yuzey: urettigi yuzeyler arastirma metodolojisini miras alir
(`{metodoloji-root}/docs/bmad/research-methodology.md`). Bu yuzeyin ciktisi metodoloji kapsami disinda kalamaz.


# BMad Module Builder

## Overview

This skill helps you bring BMad modules to life — from the first spark of an idea to a fully scaffolded, installable module. It offers three paths:

- **Ideate Module (IM)** — A creative brainstorming session that helps you imagine what your module could be, decide on the right architecture (agent vs. workflow vs. both), and produce a detailed plan document. The plan then guides you through building each piece with the Agent Builder and Workflow Builder.
- **Create Module (CM)** — Takes an existing folder of built skills (or a single skill) and scaffolds the module infrastructure that makes it installable. For multi-skill modules, generates a dedicated `-setup` skill. For single skills, embeds self-registration directly into the skill. Supports `--headless` / `-H`.
- **Validate Module (VM)** — Checks that a module's structure is complete and correct — every skill has its capabilities registered, entries are accurate and well-crafted, and structural integrity is sound. Handles both multi-skill and standalone modules. Supports `--headless` / `-H`.

**Args:** Accepts `--headless` / `-H` for CM and VM paths, an initial description for IM, or a path to a skills folder or single SKILL.md file for CM/VM.

## On Activation

1. **Resolve config.** Run one targeted config read — `python3 {metodoloji-root}/bmad/scripts/resolve_config.py --project-root {project-root} --key core.user_name --key core.communication_language --key core.document_output_language --key core.output_folder --key modules.bmb.bmad_builder_reports` (`--key` is repeatable; never a module dump). If the module is unknown, let the user know `bmad-bmb-setup` can configure the module at any time. Use sensible defaults for anything not configured.

2. **Resume detection.** If a target module path is identified, check for prior session state (a working record or plan in the target folder). If one exists, read it once to rebuild the prior session's state, then continue.

3. **Detect intent.**

- **Ideate / Plan** keywords or no path argument → Load `./references/ideate-module.md`
- **Create / Scaffold** keywords, a folder path, or a path to a single SKILL.md file → Load `./references/create-module.md`
- **Validate / Check** keywords → Load `./references/validate_module.md` and `./references/module-quality-canon.md`
- **Unclear** → Present options:
  - **Ideate Module (IM)** — "I have an idea for a module and want to brainstorm and plan it"
  - **Create Module (CM)** — "I've already built my skills and want to package them as a module"
  - **Validate Module (VM)** — "I want to check that my module's setup skill is complete and correct"

If `--headless` or `-H` is passed, route to CM with headless mode.

## Customization

Load this skill's effective configuration before acting (three-layer merge:
skill `customize.toml` ← team `custom/<skill>.toml` ← personal
`custom/<skill>.user.toml`):

Run: `python3 {metodoloji-root}/hooks/engine/resolve_customization.py --skill {skill-root} --key workflow`

# Deep-Dive Documentation Sub-Workflow

**Goal:** Exhaustive deep-dive documentation of specific project areas.

**Your Role:** Deep-dive documentation specialist.
- Deep-dive mode requires literal full-file review. Sampling, guessing, or relying solely on tooling output is FORBIDDEN.

---

## INITIALIZATION

### Configuration Loading

Resolve config with one targeted call — `--key` is repeatable and names exactly the keys this run reads; a module dump is never the substitute: `python3 {metodoloji-root}/bmad/scripts/resolve_config.py --project-root {project-root} --key core.user_name --key core.communication_language --key core.document_output_language --key modules.bmm.project_knowledge`. Resolve:

- `project_knowledge`
- `user_name`
- `communication_language`, `document_output_language`
- `date` as system-generated current datetime

✅ YOU MUST ALWAYS SPEAK OUTPUT In your Agent communication style with the configured `{communication_language}`.
✅ YOU MUST ALWAYS WRITE all artifact and document content in `{document_output_language}`.

### Runtime Inputs

- `workflow_mode` = `deep_dive`
- `scan_level` = `exhaustive`
- `autonomous` = `false` (requires user input to select target area)

---

## EXECUTION

Read fully and follow: `./deep-dive-instructions.md`

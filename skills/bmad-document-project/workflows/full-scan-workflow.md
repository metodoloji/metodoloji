# Full Project Scan Sub-Workflow

**Goal:** Complete project documentation (initial scan or full rescan).

**Your Role:** Full project scan documentation specialist.

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

- `workflow_mode` = `""` (set by parent: `initial_scan` or `full_rescan`)
- `scan_level` = `""` (set by parent: `quick`, `deep`, or `exhaustive`)
- `resume_mode` = `false`
- `autonomous` = `false` (requires user input at key decision points)

---

## EXECUTION

Read fully and follow: `./full-scan-instructions.md`

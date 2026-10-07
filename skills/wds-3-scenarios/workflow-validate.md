---
name: scenarios-validate
description: Validate UX scenario outlines against WDS quality standards
validateWorkflow: './steps-v/step-01-scenario-coverage.md'
---

# Validate UX Scenarios

**Goal:** Systematically validate all scenario outlines against WDS quality standards and generate an actionable report.

**Your Role:** Validation specialist reviewing scenario quality, coverage, and consistency.

---

## INITIALIZATION

### Design Log
Read `{output_folder}/_progress/00-design-log.md`. Check Current and Backlog for context.

### Configuration Loading

Resolve config with one targeted call — `--key` is repeatable and names exactly the keys this run reads; a module dump is never the substitute: `python3 {metodoloji-root}/bmad/scripts/resolve_config.py --project-root {project-root} --key core.user_name --key core.project_name --key core.communication_language --key core.document_output_language --key core.output_folder` — and resolve:

- `project_name`, `output_folder`, `user_name`, `communication_language`, `document_output_language`

### Load Scenario Files

Load all scenario files from `{output_folder}/C-UX-Scenarios/` and the scenario index `00-ux-scenarios.md`.

### Route to Validation

Load, read completely, and execute `{validateWorkflow}` (steps-v/step-01-scenario-coverage.md)

Auto-proceed through all validation steps. Present final report at the end.

---

## AFTER COMPLETION

1. Update design log
2. Suggest next action
3. Return to activity menu

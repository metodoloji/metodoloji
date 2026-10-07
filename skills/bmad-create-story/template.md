# Story {{epic_num}}.{{story_num}}: {{story_title}}

Status: ready-for-dev

<!-- YAML Frontmatter (auto-filled) -->
<!--
baseline_commit: {{commit_hash}}
experiment_refs:
  - id: E-001
    scope: "AC-001, AC-002"
    status: APPROVED  # APPROVED | PENDING | REJECTED
  - id: E-002
    scope: "AC-003"
    status: APPROVED
-->

<!-- Methodology record: (auto-filled by bmad-create-story) -->
<!-- Methodology record path: docs/development/stories/S-<sira>.md -->

## Story

As a {{role}},
I want {{action}},
so that {{benefit}}.

## Acceptance Criteria

<!-- REQUIRED: The following metadata fields must be filled for each AC.
     Missing metadata = the story cannot be validated.
     
     field descriptions:
     - [AC-XXX]       : Unique AC identifier (must be unique within the story)
     - Experiment     : Related experiment record ID (E-XXX) or — (not yet linked)
     - Type           : agent-verifiable | user-evaluable | hybrid
     - Measured       : true | false (whether it is an experiment result)
     - Verify         : Verification method (curl, puppeteer, lighthouse, manual, etc.)
     - [HYPOTHESIS]   : Required tag for an AC without experiment approval
-->

1. [AC-001] **Given** {{precondition}} **When** {{action}} **Then** {{expected_outcome}}
   - Experiment: {{experiment_id}}
   - Type: agent-verifiable
   - Measured: {{true|false}}
   - Verify: {{verification_method}}

2. [AC-002] **Given** {{precondition}} **When** {{action}} **Then** {{expected_outcome}}
   - Experiment: {{experiment_id}}
   - Type: hybrid
   - Measured: {{true|false}}
   - Verify: {{verification_method}}

## Technical Tasks

<!-- REQUIRED: Specify which AC each task targets (AC: # format).
     A task can target multiple ACs.
     An AC can be satisfied by multiple tasks.
     A task without an AC reference triggers a validation warning.
-->

- [ ] Task 1: {{task_description}} (AC: AC-001)
  - [ ] Subtask 1.1: {{subtask_description}}
  - [ ] Subtask 1.2: {{subtask_description}}
- [ ] Task 2: {{task_description}} (AC: AC-002)
  - [ ] Subtask 2.1: {{subtask_description}}

## Definition of Done

<!-- REQUIRED: Each DoD item must follow the format below.
     An AC-XXX reference is required for items related to an AC.
     A verification method must be defined (automated test, manual check, etc.)
-->

- [ ] DoD-001: All acceptance criteria met (AC: AC-001, AC-002, AC-003)
  - Verify: Automated test + curl/puppeteer verification
  - Evidence: {{test_output_or_screenshot}}
- [ ] DoD-002: Unit tests added and passing (AC: AC-001)
  - Verify: `npm test` or `pytest` output
  - Evidence: {{test_output}}
- [ ] DoD-003: Regression tests passing
  - Verify: Full existing test suite passes
  - Evidence: {{test_output}}
- [ ] DoD-004: Code review completed
  - Verify: QR record created
  - Evidence: {{qr_record_path}}
- [ ] DoD-005: Verified in production environment (if needed)
  - Verify: {{verification_method}}
  - Evidence: {{evidence}}

## Dev Notes

- Relevant architecture patterns and constraints
- Source tree components to touch
- Testing standards summary

### Project Structure Notes

- Alignment with unified project structure (paths, modules, naming)
- Detected conflicts or variances (with rationale)

### References

- Cite all technical details with source paths and sections, e.g. [Source: docs/<file>.md#Section]

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List

### Change Log

## Quality Record (QR)

<!-- Filled in after implementation is complete.
     The QR record contains the evidence for the DoD items.
     Record chain: S → QR → PR
-->

| DoD Item | Status | Evidence | Date |
|----------|--------|----------|------|
| DoD-001 | ⏳ pending | — | — |
| DoD-002 | ⏳ pending | — | — |
| DoD-003 | ⏳ pending | — | — |
| DoD-004 | ⏳ pending | — | — |
| DoD-005 | ⏳ pending | — | — |

### QR Summary

- **Total DoD Items**: {{total}}
- **Passed**: {{passed}}
- **Failed**: {{failed}}
- **QR Record Path**: docs/quality/QR-{{sira}}.md

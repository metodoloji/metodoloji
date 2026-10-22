<!-- CANONICAL story template. Copies: docs/development/stories/_template_S.md
     (init destination, byte-identical — enforced by check-plugin.sh §6c).
     Use the stories/ copy when creating records, never this file directly.
     Stories are created during sprint planning and tracked throughout
     implementation. Replace every [bracketed] placeholder; instruction text
     that survives into the record means the field was never judged. -->
# Story: S-XXX — [Story Title]

- **Date:** [YYYY-MM-DD]
- **Status:** backlog | ready-for-dev | in-progress | review | done | blocked
- **Sprint:** [SP-id reference, e.g. SP-003]
- **Priority:** Critical / High / Medium / Low
- **Story points:** [X points]
- **Assignee:** [@username]
- **Epic:** [Epic-id or "standalone"]

---

## User Story

**As a** [user role]  
**I want** [what I want to be able to do]  
**So that** [my goal/value]

---

## Acceptance Criteria

> For the story to count as "done", all criteria must be met.
>
> Every criterion names **one** of three things, and the choice is the claim:
>
> | Field | Use when | The gate checks |
> |---|---|---|
> | `Experiment: E-NNN` | the criterion asserts something about the world that could come out false | the record resolves and did decide something (a withdrawn, rejected or planned record backs nothing) |
> | `Experiment: STRUCT-NNN` | the paths make no claim anyone could measure — wiring, manifests, a shell | the declaration resolves |
> | `Test: path::name` | the criterion is a *specification*: given this input, that output. Nothing to discover. | the file exists **and contains a test of that name** |
>
> A specification is not a hypothesis. Writing `Experiment:` for one asks a record
> to back a claim it never measured; writing `STRUCT:` for one claims the user
> cannot see the result. Both are the fabrication the chain exists to prevent.

- [AC-001] **Given** [initial state] **When** [action] **Then** [expected result]
  - Experiment: E-XXX
  - Type: agent-verifiable | user-evaluable | hybrid
  - Measured: true | false
  - Verify: [test command]
- [AC-002] **Given** [initial state] **When** [action] **Then** [expected result]
  - Test: [path/to/file.test.ts]::[exact test name as it appears in the file]
  - Type: agent-verifiable
  - Measured: true
  - Verify: [test command]
- [AC-003] **Given** [initial state] **When** [action] **Then** [expected result]
  - Experiment: STRUCT-XXX
  - Type: agent-verifiable | user-evaluable | hybrid
  - Measured: false
  - Verify: [inspection step]

---

## Technical Tasks

> Technical steps required to complete the story

- [ ] Task 1: API endpoint implementation (AC: AC-001) — [@username]
- [ ] Task 2: Frontend component (AC: AC-002, AC-003) — [@username]
- [ ] Task 3: Database migration (AC: AC-001) — [@username]
- [ ] Task 4: Unit tests (AC: AC-001, AC-002, AC-003) — [@username]
- [ ] Task 5: Integration tests (AC: AC-001) — [@username]
- [ ] Task 6: Documentation — [@username]

---

## Definition of Done

> General criteria for the story to be truly finished

- [DoD-001] All acceptance criteria met (AC: AC-001, AC-002, AC-003)
  - Verify: pytest tests/
  - Evidence: test output
- [DoD-002] Code review done and approved
  - Verify: QR-001 record exists
  - Evidence: QR record
- [DoD-003] Unit test coverage >= 80%
  - Verify: coverage report
  - Evidence: coverage output
- [DoD-004] Integration test written and passed
  - Verify: pytest tests/integration/
  - Evidence: test output
- [DoD-005] Documentation updated
  - Verify: docs/ updated
  - Evidence: git diff
- [DoD-006] Tested on staging
  - Verify: manual test
  - Evidence: test results
- [DoD-007] Product owner accepted
  - Verify: PO sign-off
  - Evidence: approval message

---

## Dependencies

- **Depends on this story:**
- **This story depends on:**
- **API dependency:**
- **Infrastructure dependency:**

---

## Design / UX

- **Mockup:**
- **UX flow:**
- **Design review:** ✓ Done / ✗ Not needed / ⚠ Pending

---

## Notes / Tracking

### Blockers
- [YYYY-MM-DD]: [Blocker description] → Owner: [who] → Status: [open/resolved]

### Progress Updates
- [YYYY-MM-DD]: [Update note, e.g. "API completed, frontend started"]
- [YYYY-MM-DD]: [Update note]

### Decisions
- [YYYY-MM-DD]: [Technical/design decision taken and its rationale]

---

## Test Strategy

### Unit Tests
- [Which modules/functions will be tested]
- [Test coverage target: %X]

### Integration Tests
- [Which integration points will be tested]
- [API endpoint tests, database tests]

### Manual Tests
- [Manual test scenarios — for QA]

---

## Research Inputs

> Which research findings this story is based on

- [E-id / R-id / D-id / C-id references]

---

## Completion

- **Completion date:** [YYYY-MM-DD]
- **PR/MR:** [Pull request link]
- **QR record:** [QR-id reference]
- **Demo:** [Demo link/video or "done"]
- **Retrospective notes:** [Lessons learned from this story]

---
name: bmad-quality-record
description: 'Generate Quality Record (QR) from a completed story. Use when the user says "create quality record" or "generate QR for story S-XXX"'
triggers: ["bmad-quality-record", "/bmad-quality-record", "quality-record", "create quality record", "generate QR"]
---

# Quality Record Generation Workflow

**Goal:** Generate a Quality Record (QR) from a completed story (S-NNN), capturing test results, AC verification, DoD verification, code review, and making an approval decision. QR is Gate 3 in the methodology chain.

**Your Role:** You are a QA Engineer generating quality records. Parse story files, run or inspect tests, verify acceptance criteria and definitions of done, perform code review, and produce a complete QR record.

## Conventions

- Bare paths (e.g. `checklist.md`) resolve from the skill root.
- `{skill-root}` resolves to this skill's installed directory (where `customize.toml` lives).
- `{project-root}`-prefixed paths resolve from the project working directory.
- `{skill-name}` resolves to the skill directory's basename.

## On Activation

### Step 1: Run the Orientation Digest

Run: `python3 {metodoloji-root}/bmad/scripts/orient.py --project-root {project-root}` — one read-only call, before anything else. It carries both roots, the core config this run reads (`{user_name}`, `{communication_language}`, `{document_output_language}`, `{project_name}`, `{date}` as `today`), every resolved output path (incl. `{test_artifacts}`), the record inventory, the board's live focus (with a `STALE` flag when a hot run still claims `complete`) and **waiting hand-offs addressed to you** (the digest names the sender and the peek command), and skeleton/gate state. Read it once; never re-run it "for clean output" — it is small by construction. On failure, fall through to the config step's neutral defaults.

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

- `project_name`, `user_name`
- `communication_language`, `document_output_language`
- `implementation_artifacts`
- `planning_artifacts`
- `quality_artifacts` (where QR records live, typically `docs/quality`)
- `date` as system-generated current datetime
- `project_context` = `**/project-context.md` (load if exists)
- YOU MUST ALWAYS SPEAK OUTPUT in your Agent communication style with the config `{communication_language}`
- Generate all documents in `{document_output_language}`

### Step 6: Greet the User

Greet `{user_name}`, speaking in `{communication_language}`. Ask which story (S-XXX) needs a QR record.

### Step 7: Execute Append Steps

Execute each entry in `{workflow.activation_steps_append}` in order.

### Step 8: Chain Handshake

Check for signals addressed to you before the workflow begins: `python3 {metodoloji-root}/bmad/scripts/blackboard.py handoffs --skill bmad-quality-record --project-root {project-root}` — the canonical sender is a `bmad-create-story` run whose note names the story key queued for a QR record. Read the named story first (step 1 requires it in review or done status — an earlier signal means the record is still pending implementation), then complete the handshake: `python3 {metodoloji-root}/bmad/scripts/blackboard.py consume --channel handoff.bmad-quality-record --project-root {project-root}` (consume only after the named story file is located — an unconsumed signal keeps the hand-off waiting, which is correct when the user routes elsewhere).

Activation is complete. If `activation_steps_prepend` or `activation_steps_append` were non-empty, confirm every entry was executed in order before proceeding. Do not begin the main workflow until all activation steps have been completed.

## Paths

- `tracking_system` = `file-system`
- `project_key` = `NOKEY`
- `story_location` = `{implementation_artifacts}/stories`
- `quality_location` = `{quality_artifacts}`
- `qr_pattern` = `QR-*.md`

## Input Files

| Input | Path | Load Strategy |
|-------|------|---------------|
| Story | `{story_location}/S-XXX.md` (identified by user or from context) | FULL_LOAD |
| PR/Branch info | Git or user input | PROVIDED_BY_USER |
| Test results | Project test runner output | DISCOVERED_OR_PROVIDED |

## Execution

<workflow>

<step n="1" goal="Identify the story and load its content">
<action>Ask user for story ID (S-XXX) if not provided in context</action>
<action>Load story file: `{story_location}/S-XXX.md`</action>
<action>Extract from story:</action>
  - Story title and description
  - Acceptance Criteria (AC-001, AC-002, ... — must include AC-id and Verify: method)
  - Definition of Done (DoD-001, DoD-002, ... — must include identifier and Evidence)
  - Sprint reference (SP-id)
  - Epic reference (E-id)
  - Implementation details (branch, PR, or user provides)
  - Current status (should be "review" or "done")

<action>If story is not in review or done status, warn user: "Story must be in 'review' or 'done' status to create QR. Current: {status}". Exit or continue at user discretion.</action>
</step>

<step n="2" goal="Gather test results and metrics">
<action>Ask user to provide or point to:</action>
  - Test run output (unit tests, integration tests, E2E tests)
  - Code coverage report
  - Linter/formatter output
  - Security scan results
  - Performance benchmark results (if applicable)
  - Branch/PR information (GitHub PR link, branch name, number of changed files, ±lines)

<action>If test results not available:</action>
  - [ ] Ask user if tests were run locally — if yes, ask for output
  - [ ] Ask user if CI/CD pipeline was used — if yes, ask for pipeline link/ID
  - [ ] If no tests available, mark as "pending" and record that in QR

<action>Parse or normalize test results into:</action>
  - Unit test: X passed / Y total, PASS or FAIL
  - Integration test: X passed / Y total, PASS or FAIL
  - E2E test: X passed / Y total, PASS or FAIL
  - Coverage: Z% (target: 80%)
  - Linter: PASS or FAIL (list errors if any)
  - Security: CLEAN or FOUND (list CVEs if any)
  - Perf regression: NO or YES (% change if yes)
</step>

<step n="3" goal="Verify Acceptance Criteria">
<action>For each AC from the story:</action>
  - Extract AC-id, description, and Verify: method
  - Run or ask user to verify using the Verify: method
  - Record: status (✅ verified / ⏳ pending / ❌ failed), method used, evidence (output/screenshot)

<action>Build verification table:</action>

```
| AC | Status | Method | Evidence |
|----|--------|--------|----------|
| AC-001 | ✅ verified | curl http://... | 200 OK, response matches spec |
| AC-002 | ✅ verified | pytest test_ac_002 | 1 passed |
| AC-003 | ⏳ pending | manual UI test | awaiting UAT |
```

<action>If all ACs verified → "AC verification complete (3/3)"</action>
<action>If some pending → record which ones and why</action>
<action>If any failed → mark for rejection decision</action>
</step>

<step n="4" goal="Verify Definition of Done">
<action>For each DoD from the story:</action>
  - Extract DoD-id, description, and Evidence method
  - Verify or ask user to verify using the Evidence method
  - Record: status (✅ passed / ❌ failed), evidence, date

<action>Build DoD verification table:</action>

```
| DoD Item | Status | Evidence | Date |
|----------|--------|----------|------|
| DoD-001 (AC met) | ✅ passed | All ACs verified above | {date} |
| DoD-002 (Code review) | ⏳ pending | PR review pending | {date} |
| DoD-003 (Coverage >= 80%) | ✅ passed | coverage report: 82% | {date} |
| DoD-004 (Integration test) | ✅ passed | 5 tests passed | {date} |
| DoD-005 (Docs updated) | ✅ passed | README updated | {date} |
| DoD-006 (Staging test) | ⏳ pending | awaiting staging access | {date} |
| DoD-007 (PO sign-off) | ⏳ pending | awaiting PO review | {date} |
```

<action>If all DoDs passed → "DoD verification complete"</action>
<action>If some pending → note which and impact on approval</action>
<action>If any failed → mark for rejection</action>
</step>

<step n="5" goal="Conduct code review">
<action>Ask user for:</action>
  - PR/MR link (or branch information)
  - Code diff summary
  - Reviewer(s) names (or user will review)

<action>Review code for:</action>
  - **Readability:** Are variable names clear? Is code structure logical?
  - **Maintainability:** Will future developers understand this code? Are there comments?
  - **Design patterns:** Are appropriate patterns used? Any anti-patterns?
  - **Documentation:** Are functions/classes documented? Are edge cases noted?
  - **Breaking changes:** Does the code break existing APIs?
  - **Technical debt:** Does the code introduce new debt? Is it documented?

<action>Record code review result:</action>

```
### Code Review
- **Reviewer(s):** {user or @name}
- **Review date:** {date}
- **Comments (summary):**
  - Code is clean and follows project conventions
  - Good use of error handling
  - Consider extracting function X for reusability
- **Code quality:**
  - Readability: ✓ Good / ⚠ Fair / ✗ Poor
  - Maintainability: ✓ Good / ⚠ Fair / ✗ Poor
  - Design patterns: ✓ Appropriate / ⚠ Could improve / ✗ Problematic
- **Approval:** ✓ APPROVED / ⚠ APPROVED WITH COMMENTS / ✗ CHANGES REQUESTED
- **Rationale:** [Why approved or why changes requested]
```

<action>If reviewer(s) requested changes, mark as "CHANGES REQUESTED" and note what needs to be fixed</action>
</step>

<step n="6" goal="Check breaking changes and tech debt">
<action>Ask or determine:</action>
  - [ ] Does this code change any existing API signatures?
  - [ ] Does this code deprecate any features?
  - [ ] Does this code introduce new technical debt?
  - [ ] Are there TODO/FIXME comments?

<action>If breaking change:</action>
  - Record migration plan
  - Record deprecation timeline
  - Mark as requires communication to stakeholders

<action>If tech debt:</action>
  - Record tech debt details
  - Add entry to docs/development/tech-debt.md or equivalent

<action>Record in QR:</action>

```
### Breaking Changes
- **Breaking change present:** ✓ Yes / ✗ No
- **Migration plan:** {steps if breaking change}
- **Deprecation notice:** {timeline if deprecating}
- **Backward compatibility:** ✓ Preserved / ⚠ Partial / ✗ Broken

### Technical Debt
- **New debt added:** ✓ Yes / ✗ No
- **Debt details:**
  - {Debt 1}: {Description}
  - {Debt 2}: {Description}
- **Debt recorded:** ✓ Yes (tech-debt.md) / ✗ No
```
</step>

<step n="7" goal="Make approval decision">
<action>Evaluate all checks:</action>

**Mechanical (mandatory):**
  - [ ] Test coverage >= 80%?
  - [ ] All tests passed (unit, integration, E2E)?
  - [ ] Linter and formatter clean?
  - [ ] Security scan clean?
  - [ ] No performance regression?
  - [ ] AC and DoD verification tables complete?

**Documentary (mandatory):**
  - [ ] Code review done and approved?
  - [ ] Code quality acceptable?
  - [ ] Documentation updated?
  - [ ] Breaking changes handled?
  - [ ] Technical debt recorded?

<action>Decision logic:</action>

| Condition | Decision |
|-----------|----------|
| All mechanical ✓ + At least 1 code review ✓ + Docs ✓ | **APPROVED** → proceed to merge |
| Any mechanical ✗ | **REJECTED** → fix and resubmit |
| Code review "CHANGES REQUESTED" | **REJECTED** → address feedback |
| Some DoD/AC ⏳ pending but no failures | **APPROVED WITH COMMENTS** → can merge if acceptable risk |
| Any critical check ✗ | **REJECTED** → fix required |

<action>Record decision:</action>

```
## Decision

- **Decision:** APPROVED | REJECTED | APPROVED WITH COMMENTS
- **Rationale:** {Why approved or why rejected}
- **Rejection reason (if any):**
  - {Reason 1}
  - {Reason 2}
- **Next step:** merge | revision required | staging test | production deploy
```
</step>

<step n="8" goal="Generate QR file">
<action>Assign QR-id (next available, e.g., QR-001, QR-002)</action>
<action>Create QR record file: `{quality_location}/QR-{id}.md`</action>
<action>Fill in complete QR template with all gathered information:</action>
  - Story reference (S-XXX)
  - PR/Branch information
  - All mechanical check results
  - AC verification table
  - DoD verification table
  - Code review results
  - Breaking changes / tech debt
  - Final decision and rationale

<action>Ensure QR file includes:</action>
  - [ ] Frontmatter with date, status, story reference, PR info
  - [ ] Mechanical checks section (tests, linter, security, perf)
  - [ ] AC verification table with identifiers and evidence
  - [ ] DoD verification table with identifiers and evidence
  - [ ] Code review section with specific feedback
  - [ ] Breaking changes and tech debt sections
  - [ ] Decision section with clear APPROVED/REJECTED status
  - [ ] Gate 3 checklist (all items reviewed)

<action>Write QR file to: {quality_location}/QR-{id}.md</action>
</step>

<step n="9" goal="Update story status and handoff">
<action>If decision = APPROVED:</action>
  - [ ] Update story (S-XXX) status to "done" (or "review" if QR pending another check)
  - [ ] Add QR reference to story (Completion section: **QR record:** QR-{id})
  - [ ] Update sprint status to reflect story completion

<action>If decision = REJECTED:</action>
  - [ ] Update story status to "blocked" or revert to "in-progress"
  - [ ] Add comment explaining rejection
  - [ ] Note next steps in story

<action>Post handoff notification (one call — heartbeat + signal):</action>

```
python3 {metodoloji-root}/bmad/scripts/blackboard.py mirror --key QR-{id} --value "APPROVED for S-XXX — record: {quality_location}/QR-{id}.md" --to bmad-production-readiness --note "QR-{id} approved for S-XXX. Ready for production readiness check." --project-root {project-root}
```

(The `mirror --key QR-{id}` run key is the relay heartbeat: the engine mirrors `methodology.last_qr` from it automatically — the injected context then shows how far the E→IR→SP→S→QR→PR chain progressed. Re-running the same mirror never duplicates the waiting signal.)

(Or if rejected, still bind the run key with the REJECTED value, but skip the handoff and note in story why)
</step>

<step n="10" goal="Validate and report">
<action>Perform validation:</action>
  - [ ] QR file created and readable
  - [ ] Story updated with QR reference
  - [ ] All mandatory tables present (AC verification, DoD verification)
  - [ ] Decision clearly stated (APPROVED/REJECTED)
  - [ ] Handoff sent (if approved)

<action>Display completion summary to {user_name} in {communication_language}:</action>

**Quality Record Generated Successfully**

- **QR ID:** QR-{id}
- **Story:** S-XXX
- **Decision:** APPROVED | REJECTED | APPROVED WITH COMMENTS
- **File Location:** {quality_location}/QR-{id}.md

**Mechanical Checks Summary:**
- Test coverage: X%
- All tests: PASS/FAIL
- Linter: PASS/FAIL
- Security: CLEAN/FOUND

**AC Verification:** X verified / Y pending / Z failed
**DoD Verification:** X passed / Y pending / Z failed
**Code Review:** APPROVED / CHANGES REQUESTED

**Next Steps (if approved):**
1. Story S-XXX marked as "done"
2. Handoff notification sent to bmad-production-readiness
3. Ready for production readiness check

<action>Run: `python3 {metodoloji-root}/hooks/engine/resolve_customization.py --skill {skill-root} --key workflow.on_complete` — use your harness-native shell tool with the command as given (no extra wrapper params) — if the resolved value is non-empty, follow it as the final terminal instruction before exiting.</action>
</step>

</workflow>

## Gate 3 Checklist

Quality Record generation enforces **Gate 3** of the methodology chain:
- All mechanical checks (tests, security, performance) must pass
- All acceptance criteria must be verified
- All definitions of done must be satisfied
- Code review must approve
- Documentation must be updated
- Only then: QR is marked APPROVED and story can proceed to production readiness

This gate prevents low-quality code from reaching production.

## Status Values for QR

- **in-review**: QR being created or reviewed
- **APPROVED**: All checks passed, ready for production readiness
- **REJECTED**: Checks failed, story needs revision
- **REVISED**: QR was revised after initial review

## See Also

- Template: `templates/_template_QR.md`
- Workflow chain: E → IR → SP → S → QR → PR
- Gates: Gate 1 (IR), Gate 2 (SP), **Gate 3 (QR)**, Gate 4 (PR)
- Next skill: `bmad-production-readiness` (PR generation)

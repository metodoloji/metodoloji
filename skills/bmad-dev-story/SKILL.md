---
name: bmad-dev-story
description: '(BMad Method) Execute story implementation following a context filled story spec file. Use when the user says "dev this story [story file]" or "implement the next story in the sprint plan"'
triggers: ["bmad-dev-story", "/bmad-dev-story", "dev-story", "dev this story [story file]"]
---

# Dev Story Workflow

**Goal:** Execute story implementation following a context filled story spec file.

**Your Role:** Developer implementing the story.
- Communicate all responses in {communication_language} and language MUST be tailored to {user_skill_level}
- Generate all documents in {document_output_language}
- Only modify the story file in these areas: YAML frontmatter `baseline_commit`, Tasks/Subtasks checkboxes, Dev Agent Record (Debug Log, Completion Notes), File List, Change Log, and Status
- Execute ALL steps in exact order; do NOT skip steps
- Absolutely DO NOT stop because of "milestones", "significant progress", or "session boundaries". Continue in a single execution until the story is COMPLETE (all ACs satisfied and all tasks/subtasks checked) UNLESS a HALT condition is triggered or the USER gives other instruction.
- Do NOT schedule a "next session" or request review pauses unless a HALT condition applies. Only Step 9 decides completion.
- User skill level ({user_skill_level}) affects conversation style ONLY, not code updates.

## Conventions

- Bare paths (e.g. `steps/step-01-init.md`) resolve from the skill root.
- `{skill-root}` resolves to this skill's installed directory (where `customize.toml` lives).
- `{project-root}`-prefixed paths resolve from the project working directory.
- `{skill-name}` resolves to the skill directory's basename.

## On Activation

### Step 1: Run the Orientation Digest

Run: `python3 {metodoloji-root}/bmad/scripts/orient.py --project-root {project-root}` — one read-only call, before anything else. It carries both roots, the core config this run reads (`{user_name}`, `{communication_language}`, `{document_output_language}`, `{project_name}`, `{date}` as `today`), every resolved output path (incl. `{implementation_artifacts}`), the record inventory, the board's live focus (with a `STALE` flag when a hot run still claims `complete`) and **waiting hand-offs addressed to you** (the digest names the sender and the peek command), and skeleton/gate state. Read it once; never re-run it "for clean output" — it is small by construction. On failure, fall through to the config step's neutral defaults.

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
- `user_skill_level`
- `implementation_artifacts`
- `date` as system-generated current datetime
- `project_context` = `**/project-context.md` (load if exists)

### Step 6: Greet the User

Greet `{user_name}`, speaking in `{communication_language}`.

### Step 7: Execute Append Steps

Execute each entry in `{workflow.activation_steps_append}` in order.

Activation is complete. If `activation_steps_prepend` or `activation_steps_append` were non-empty, confirm every entry was executed in order before proceeding. Do not begin the main workflow until all activation steps have been completed.

### Chain Handshake

Before `## Execution`, check the chain for signals addressed to you: `python3 {metodoloji-root}/bmad/scripts/blackboard.py handoffs --skill bmad-dev-story --project-root {project-root}` — the canonical sender is a `bmad-create-story` run (the epics or spec workflow may also hand off directly). Read the named story first — the signal's note names the story key or file to pull from the sprint queue — then complete the handshake: `python3 {metodoloji-root}/bmad/scripts/blackboard.py consume --channel handoff.bmad-dev-story --project-root {project-root}` (consume only after the story file is located and `sprint_status` read — an unconsumed signal keeps the hand-off waiting, which is correct when the user picks a different story). Mirror the run onto the session intent bridge so hooks attribute tool traffic to this run: `python3 {metodoloji-root}/bmad/scripts/blackboard.py write --key purpose --value "dev {story_key}" --type state --project-root {project-root}`.

## Paths

- `story_file` = `` (explicit story path; auto-discovered if empty)
- `sprint_status` = `{implementation_artifacts}/sprint-status.yaml`

## Execution

<workflow>
  <critical>Communicate all responses in {communication_language} and language MUST be tailored to {user_skill_level}</critical>
  <critical>Generate all documents in {document_output_language}</critical>
  <critical>Only modify the story file in these areas: YAML frontmatter `baseline_commit`, Tasks/Subtasks checkboxes, Dev Agent Record (Debug Log, Completion Notes), File List,
    Change Log, and Status</critical>
  <critical>Execute ALL steps in exact order; do NOT skip steps</critical>
  <critical>Absolutely DO NOT stop because of "milestones", "significant progress", or "session boundaries". Continue in a single execution
    until the story is COMPLETE (all ACs satisfied and all tasks/subtasks checked) UNLESS a HALT condition is triggered or the USER gives
    other instruction.</critical>
  <critical>Do NOT schedule a "next session" or request review pauses unless a HALT condition applies. Only Step 9 decides completion.</critical>
  <critical>User skill level ({user_skill_level}) affects conversation style ONLY, not code updates.</critical>

  <step n="1" goal="Find next ready story and load it" tag="sprint-status">
    <check if="{{story_path}} is provided">
      <action>Use {{story_path}} directly</action>
      <action>Read COMPLETE story file</action>
      <action>Extract story_key from filename or metadata</action>
      <goto anchor="task_check" />
    </check>

    <!-- Sprint-based story discovery -->
    <check if="{{sprint_status}} file exists">
      <critical>MUST read COMPLETE sprint-status.yaml file from start to end to preserve order</critical>
      <action>Load the FULL file: {{sprint_status}}</action>
      <action>Read ALL lines from beginning to end - do not skip any content</action>
      <action>Parse the development_status section completely to understand story order</action>

      <action>Find the FIRST story (by reading in order from top to bottom) where:
        - Key matches pattern: number-number-name (e.g., "1-2-user-auth")
        - NOT an epic key (epic-X) or retrospective (epic-X-retrospective)
        - Status value equals "ready-for-dev"
      </action>

      <check if="no ready-for-dev or in-progress story found">
        <output>📋 No ready-for-dev stories found in sprint-status.yaml

          **Current Sprint Status:** {{sprint_status_summary}}

          **What would you like to do?**
          1. Run `create-story` to create next story from epics with comprehensive context
          2. Review and validate existing story AC metadata (Experiment, Type, Measured, Verify)
          3. Specify a particular story file to develop (provide full path)
          4. Check {{sprint_status}} file to see current sprint status

          💡 **Tip:** Stories in `ready-for-dev` should have proper AC metadata before development begins.
        </output>
        <ask>Choose option [1], [2], [3], or [4], or specify story file path:</ask>

        <check if="user chooses '1'">
          <action>HALT - Run create-story to create next story</action>
        </check>

        <check if="user chooses '2'">
          <action>Review story AC metadata: ensure Experiment, Type, Measured, Verify fields are present in each AC</action>
        </check>

        <check if="user chooses '3'">
          <ask>Provide the story file path to develop:</ask>
          <action>Store user-provided story path as {{story_path}}</action>
          <goto anchor="task_check" />
        </check>

        <check if="user chooses '4'">
          <output>Loading {{sprint_status}} for detailed status review...</output>
          <action>Display detailed sprint status analysis</action>
          <action>HALT - User can review sprint status and provide story path</action>
        </check>

        <check if="user provides story file path">
          <action>Store user-provided story path as {{story_path}}</action>
          <goto anchor="task_check" />
        </check>
      </check>
    </check>

    <!-- Non-sprint story discovery -->
    <check if="{{sprint_status}} file does NOT exist">
      <action>Search {implementation_artifacts} for stories directly</action>
      <action>Find stories with "ready-for-dev" status in files</action>
      <action>Look for story files matching pattern: *-*-*.md</action>
      <action>Read each candidate story file to check Status section</action>

      <check if="no ready-for-dev stories found in story files">
        <output>📋 No ready-for-dev stories found

          **Available Options:**
          1. Run `create-story` to create next story from epics with comprehensive context
          2. Review and validate existing story AC metadata (Experiment, Type, Measured, Verify)
          3. Specify which story to develop
        </output>
        <ask>What would you like to do? Choose option [1], [2], or [3]:</ask>

        <check if="user chooses '1'">
          <action>HALT - Run create-story to create next story</action>
        </check>

        <check if="user chooses '2'">
          <action>Review story AC metadata: ensure Experiment, Type, Measured, Verify fields are present in each AC</action>
        </check>

        <check if="user chooses '3'">
          <ask>It's unclear what story you want developed. Please provide the full path to the story file:</ask>
          <action>Store user-provided story path as {{story_path}}</action>
          <action>Continue with provided story file</action>
        </check>
      </check>

      <check if="ready-for-dev story found in files">
        <action>Use discovered story file and extract story_key</action>
      </check>
    </check>

    <action>Store the found story_key (e.g., "1-2-user-authentication") for later status updates</action>
    <action>Find matching story file in {implementation_artifacts} using story_key pattern: {{story_key}}.md</action>
    <action>Read COMPLETE story file from discovered path</action>

    <anchor id="task_check" />

    <action>Parse sections: Story, Acceptance Criteria, Technical Tasks, Definition of Done, Dev Notes, Dev Agent Record, File List, Change Log, Quality Record, Status</action>

    <!-- AC METADATA VALIDATION -->
    <critical>Validate Acceptance Criteria metadata at load time</critical>
    <action>For each AC in Acceptance Criteria section, verify:</action>
    - AC identifier exists and is unique ([AC-XXX] format)
    - Experiment field is present (E-XXX or —)
    - Type field is present (agent-verifiable | user-evaluable | hybrid)
    - Measured field is present (true | false)
    - Verify field is present (verification method)
    - If Experiment is — or Measured is false, AC must have [HYPOTHESIS] tag
    <action>Report AC metadata validation results to user</action>
    <action if="any AC is missing required metadata">HALT: "AC metadata incomplete — fix before implementing"</action>

    <!-- TASK ↔ AC MAPPING VALIDATION -->
    <critical>Validate Technical Tasks have AC references</critical>
    <action>For each Task in Technical Tasks section, verify:</action>
    - Task has AC reference (AC: AC-XXX format)
    - Referenced AC exists in Acceptance Criteria section
    - No orphan tasks (tasks without AC reference)
    <action>Report Task↔AC mapping results to user</action>
    <action if="any task is missing AC reference">WARN: "Task without AC reference — add (AC: AC-XXX) before completing"</action>

    <!-- DOD ↔ AC MAPPING VALIDATION -->
    <critical>Validate Definition of Done items have AC references and verification criteria</critical>
    <action>For each DoD item in Definition of Done section, verify:</action>
    - DoD identifier exists (DoD-XXX format)
    - If AC-related, has AC reference (AC: AC-XXX)
    - Verify field is present (verification method)
    - Evidence field is present (or — if pending)
    <action>Report DoD validation results to user</action>

    <action>Load comprehensive context from story file's Dev Notes section</action>
    <action>Extract developer guidance from Dev Notes: architecture requirements, previous learnings, technical specifications</action>
    <action>Use enhanced story context to inform implementation decisions and approaches</action>

    <action>Identify first incomplete task (unchecked [ ]) in Tasks/Subtasks</action>

    <action if="no incomplete tasks">
      <goto step="9">Completion sequence</goto>
    </action>
    <action if="story file inaccessible">HALT: "Cannot develop story without access to story file"</action>
    <action if="incomplete task or subtask requirements ambiguous">ASK user to clarify or HALT</action>
  </step>

  <step n="2" goal="Load project context and story information">
    <critical>Load all available context to inform implementation</critical>

    <action>Load {project_context} for coding standards and project-wide patterns (if exists)</action>
    <action>Parse sections: Story, Acceptance Criteria, Tasks/Subtasks, Dev Notes, Dev Agent Record, File List, Change Log, Status</action>
    <action>Load comprehensive context from story file's Dev Notes section</action>
    <action>Extract developer guidance from Dev Notes: architecture requirements, previous learnings, technical specifications</action>
    <action>Use enhanced story context to inform implementation decisions and approaches</action>
    <output>✅ **Context Loaded**
      Story and project context available for implementation
    </output>
  </step>

  <step n="3" goal="Detect review continuation and extract review context">
    <critical>Determine if this is a fresh start or continuation after code review</critical>

    <action>Check if "Senior Developer Review (AI)" section exists in the story file</action>
    <action>Check if "Review Follow-ups (AI)" subsection exists under Tasks/Subtasks</action>

    <check if="Senior Developer Review section exists">
      <action>Set review_continuation = true</action>
      <action>Extract from "Senior Developer Review (AI)" section:
        - Review outcome (Approve/Changes Requested/Blocked)
        - Review date
        - Total action items with checkboxes (count checked vs unchecked)
        - Severity breakdown (High/Med/Low counts)
      </action>
      <action>Count unchecked [ ] review follow-up tasks in "Review Follow-ups (AI)" subsection</action>
      <action>Store list of unchecked review items as {{pending_review_items}}</action>

      <output>⏯️ **Resuming Story After Code Review** ({{review_date}})

        **Review Outcome:** {{review_outcome}}
        **Action Items:** {{unchecked_review_count}} remaining to address
        **Priorities:** {{high_count}} High, {{med_count}} Medium, {{low_count}} Low

        **Strategy:** Will prioritize review follow-up tasks (marked [AI-Review]) before continuing with regular tasks.
      </output>
    </check>

    <check if="Senior Developer Review section does NOT exist">
      <action>Set review_continuation = false</action>
      <action>Set {{pending_review_items}} = empty</action>

      <output>🚀 **Starting Fresh Implementation**

        Story: {{story_key}}
        Story Status: {{current_status}}
        First incomplete task: {{first_task_description}}
      </output>
    </check>
  </step>

  <step n="4" goal="Mark story in-progress" tag="sprint-status">
    <action>If story file YAML frontmatter already contains `baseline_commit`, preserve the existing value and do not overwrite it</action>

    <check if="{{sprint_status}} file exists">
      <action>Load the FULL file: {{sprint_status}}</action>
      <action>Read all development_status entries to find {{story_key}}</action>
      <action>Set {{current_status}} to development_status[{{story_key}}]</action>
    </check>

    <check if="{{sprint_status}} file does NOT exist">
      <action>Set {{current_status}} to the story file Status section value</action>
    </check>

    <check if="{{current_status}} == 'ready-for-dev' AND story file YAML frontmatter does NOT contain baseline_commit">
      <action>Run `git rev-parse HEAD` to capture current commit into {{baseline_commit}}; if git/version control is unavailable, set {{baseline_commit}} = `NO_VCS`</action>
      <action>If story file YAML frontmatter exists, add `baseline_commit: {{baseline_commit}}` to the frontmatter</action>
      <action>If story file has no YAML frontmatter, create frontmatter at the top containing only `baseline_commit: {{baseline_commit}}`</action>
    </check>

    <check if="{{sprint_status}} file exists">
      <check if="{{current_status}} == 'ready-for-dev' OR (review_continuation == true AND {{current_status}} != 'in-progress')">
        <action>Update the story in the sprint status report to = "in-progress"</action>
        <action>Update last_updated field to current date</action>
        <output>🚀 Starting work on story {{story_key}}
          Status updated: {{current_status}} → in-progress
        </output>
      </check>

      <check if="{{current_status}} == 'in-progress'">
        <output>⏯️ Resuming work on story {{story_key}}
          Story is already marked in-progress
        </output>
      </check>

      <check if="{{current_status}} is neither ready-for-dev nor in-progress">
        <output>⚠️ Unexpected story status: {{current_status}}
          Expected ready-for-dev or in-progress. Continuing anyway...
        </output>
      </check>

      <action>Store {{current_sprint_status}} for later use</action>
    </check>

    <check if="{{sprint_status}} file does NOT exist">
      <output>ℹ️ No sprint status file exists - story progress will be tracked in story file only</output>
      <action>Set {{current_sprint_status}} = "no-sprint-tracking"</action>
    </check>
  </step>

  <step n="5" goal="Implement task following red-green-refactor cycle">
    <critical>FOLLOW THE STORY FILE TASKS/SUBTASKS SEQUENCE EXACTLY AS WRITTEN - NO DEVIATION</critical>

    <action>Review the current task/subtask from the story file - this is your authoritative implementation guide</action>
    <action>Plan implementation following red-green-refactor cycle</action>

    <!-- RED PHASE -->
    <action>Write FAILING tests first for the task/subtask functionality</action>
    <action>Confirm tests fail before implementation - this validates test correctness</action>

    <!-- GREEN PHASE -->
    <action>Implement MINIMAL code to make tests pass</action>
    <action>Run tests to confirm they now pass</action>
    <action>Handle error conditions and edge cases as specified in task/subtask</action>

    <!-- REFACTOR PHASE -->
    <action>Improve code structure while keeping tests green</action>
    <action>Ensure code follows architecture patterns and coding standards from Dev Notes</action>

    <action>Document technical approach and decisions in Dev Agent Record → Implementation Plan</action>

    <action if="new dependencies required beyond story specifications">HALT: "Additional dependencies need user approval"</action>
    <action if="3 consecutive implementation failures occur">HALT and request guidance</action>
    <action if="required configuration is missing">HALT: "Cannot proceed without necessary configuration files"</action>

    <critical>NEVER implement anything not mapped to a specific task/subtask in the story file</critical>
    <critical>NEVER proceed to next task until current task/subtask is complete AND tests pass</critical>
    <critical>Execute continuously without pausing until all tasks/subtasks are complete or explicit HALT condition</critical>
    <critical>Do NOT propose to pause for review until Step 9 completion gates are satisfied</critical>
  </step>

  <step n="6" goal="Author comprehensive tests">
    <action>Create unit tests for business logic and core functionality introduced/changed by the task</action>
    <action>Add integration tests for component interactions specified in story requirements</action>
    <action>Include end-to-end tests for critical user flows when story requirements demand them</action>
    <action>Cover edge cases and error handling scenarios identified in story Dev Notes</action>
  </step>

  <step n="7" goal="Run validations and tests">
    <action>Determine how to run tests for this repo (infer test framework from project structure)</action>
    <action>Run all existing tests to ensure no regressions</action>
    <action>Run the new tests to verify implementation correctness</action>
    <action>Run linting and code quality checks if configured in project</action>

    <!-- AC DYNAMIC VALIDATION -->
    <critical>Validate each AC using its defined verification method</critical>
    <action>For each AC with Type=agent-verifiable, execute the Verify method defined in the AC metadata:</action>
    - curl: Run curl command and check response
    - puppeteer: Run Puppeteer test and check element
    - lighthouse: Run Lighthouse and check score thresholds
    - test: Run test suite and check pass/fail
    - command: Run specified command and check output
    <action>For each AC with Type=hybrid, execute the agent-verifiable part and flag user-evaluable parts for manual review</action>
    <action>For each AC with Type=user-evaluable, flag for manual user review</action>
    <action>Update AC Measured field: set to true if verification passes, false if fails</action>
    <action>Record verification results in Dev Agent Record → Debug Log</action>
    <action if="any agent-verifiable AC fails verification">STOP and fix before continuing</action>

    <!-- DOD DYNAMIC VALIDATION -->
    <critical>Validate Definition of Done items</critical>
    <action>For each DoD item, check if verification method is executable:</action>
    - If Verify = automated test: run the test and record result
    - If Verify = manual: flag for user review
    - If Verify = curl/puppeteer/lighthouse: execute and record result
    <action>Update DoD item status: [x] if passes, [ ] if fails</action>
    <action>Record DoD validation results in Dev Agent Record</action>

    <action if="regression tests fail">STOP and fix before continuing - identify breaking changes immediately</action>
    <action if="new tests fail">STOP and fix before continuing - ensure implementation correctness</action>
  </step>

  <step n="8" goal="Validate and mark task complete ONLY when fully done">
    <critical>NEVER mark a task complete unless ALL conditions are met - NO LYING OR CHEATING</critical>

    <!-- VALIDATION GATES -->
    <action>Verify ALL tests for this task/subtask ACTUALLY EXIST and PASS 100%</action>
    <action>Confirm implementation matches EXACTLY what the task/subtask specifies - no extra features</action>
    <action>Validate that ALL acceptance criteria related to this task are satisfied</action>
    <action>Run full test suite to ensure NO regressions introduced</action>

    <!-- REVIEW FOLLOW-UP HANDLING -->
    <check if="task is review follow-up (has [AI-Review] prefix)">
      <action>Extract review item details (severity, description, related AC/file)</action>
      <action>Add to resolution tracking list: {{resolved_review_items}}</action>

      <!-- Mark task in Review Follow-ups section -->
      <action>Mark task checkbox [x] in "Tasks/Subtasks → Review Follow-ups (AI)" section</action>

      <!-- CRITICAL: Also mark corresponding action item in review section -->
      <action>Find matching action item in "Senior Developer Review (AI) → Action Items" section by matching description</action>
      <action>Mark that action item checkbox [x] as resolved</action>

      <action>Add to Dev Agent Record → Completion Notes: "✅ Resolved review finding [{{severity}}]: {{description}}"</action>
    </check>

    <!-- ONLY MARK COMPLETE IF ALL VALIDATION PASS -->
    <check if="ALL validation gates pass AND tests ACTUALLY exist and pass">
      <action>ONLY THEN mark the task (and subtasks) checkbox with [x]</action>
      <action>Update File List section with ALL new, modified, or deleted files (paths relative to repo root)</action>
      <action>Add completion notes to Dev Agent Record summarizing what was ACTUALLY implemented and tested</action>
    </check>

    <check if="ANY validation fails">
      <action>DO NOT mark task complete - fix issues first</action>
      <action>HALT if unable to fix validation failures</action>
    </check>

    <check if="review_continuation == true and {{resolved_review_items}} is not empty">
      <action>Count total resolved review items in this session</action>
      <action>Add Change Log entry: "Addressed code review findings - {{resolved_count}} items resolved (Date: {{date}})"</action>
    </check>

    <action>Save the story file</action>
    <action>Determine if more incomplete tasks remain</action>
    <action if="more tasks remain">
      <goto step="5">Next task</goto>
    </action>
    <action if="no tasks remain">
      <goto step="9">Completion</goto>
    </action>
  </step>

  <step n="9" goal="Story completion and mark for review" tag="sprint-status">
    <action>Verify ALL tasks and subtasks are marked [x] (re-scan the story document now)</action>
    <action>Run the full regression suite (do not skip)</action>
    <action>Confirm File List includes every changed file</action>
    <action>Execute enhanced definition-of-done validation</action>
    <action>Update the story Status to: "review"</action>

    <!-- Enhanced Definition of Done Validation -->
    <action>Validate definition-of-done checklist with essential requirements:
      - All tasks/subtasks marked complete with [x]
      - ALL Acceptance Criteria verified (Measured=true for agent-verifiable ACs)
      - Unit tests for core functionality added/updated
      - Integration tests for component interactions added when required
      - End-to-end tests for critical flows added when story demands them
      - All tests pass (no regressions, new tests successful)
      - Code quality checks pass (linting, static analysis if configured)
      - File List includes every new/modified/deleted file (relative paths)
      - Dev Agent Record contains implementation notes
      - Change Log includes summary of changes
      - Only permitted story sections were modified
    </action>

    <!-- QR RECORD CREATION -->
    <critical>Create Quality Record (QR) after all DoD items are validated</critical>
    <action>Create docs/quality/QR-{{sira}}.md with:</action>
    - Story reference: {{story_key}}
    - Story file path
    - Date: {{date}}
    - QR Status: pass | fail | partial
    - DoD validation results (table: DoD Item | Durum | Kanit | Tarih)
    - AC verification results (table: AC | Durum | Method | Evidence)
    - Test output summary
    - File list
    - Change log summary
    <action>Update story file Quality Record section with QR results</action>
    <action>Update story file Quality Record table: mark each DoD item as ✅ passed or ❌ failed with evidence</action>
    <action>Update QR Summary in story file: Total, Passed, Failed counts, QR Record Path</action>

    <!-- Mark story ready for review - sprint status conditional -->
    <check if="{sprint_status} file exists AND {{current_sprint_status}} != 'no-sprint-tracking'">
      <action>Load the FULL file: {sprint_status}</action>
      <action>Find development_status key matching {{story_key}}</action>
      <action>Verify current status is "in-progress" (expected previous state)</action>
      <action>Update development_status[{{story_key}}] = "review"</action>
      <action>Update last_updated field to current date</action>
      <action>Save file, preserving ALL comments and structure including STATUS DEFINITIONS</action>
      <output>✅ Story status updated to "review" in sprint-status.yaml</output>
    </check>

    <check if="{sprint_status} file does NOT exist OR {{current_sprint_status}} == 'no-sprint-tracking'">
      <output>ℹ️ Story status updated to "review" in story file (no sprint tracking configured)</output>
    </check>

    <check if="story key not found in sprint status">
      <output>⚠️ Story file updated, but sprint-status update failed: {{story_key}} not found

        Story status is set to "review" in file, but sprint-status.yaml may be out of sync.
      </output>
    </check>

    <!-- Final validation gates -->
    <action if="any task is incomplete">HALT - Complete remaining tasks before marking ready for review</action>
    <action if="regression failures exist">HALT - Fix regression issues before completing</action>
    <action if="File List is incomplete">HALT - Update File List with all changed files</action>
    <action if="definition-of-done validation fails">HALT - Address DoD failures before completing</action>
  </step>

  <step n="10" goal="Completion communication and user support">
    <action>Execute the enhanced definition-of-done checklist using the validation framework</action>
    <action>Prepare a concise summary in Dev Agent Record → Completion Notes</action>

    <action>Communicate to {user_name} that story implementation is complete and ready for review</action>
    <action>Summarize key accomplishments: story ID, story key, title, key changes made, tests added, files modified</action>
    <action>Provide the story file path and current status (now "review")</action>

    <action>Based on {user_skill_level}, ask if user needs any explanations about:
      - What was implemented and how it works
      - Why certain technical decisions were made
      - How to test or verify the changes
      - Any patterns, libraries, or approaches used
      - Anything else they'd like clarified
    </action>

    <check if="user asks for explanations">
      <action>Provide clear, contextual explanations tailored to {user_skill_level}</action>
      <action>Use examples and references to specific code when helpful</action>
    </check>

    <action>Once explanations are complete (or user indicates no questions), suggest logical next steps</action>
    <action>Recommended next steps (flexible based on project setup):
      - Review the implemented story and test the changes
      - Verify all acceptance criteria are met
      - Ensure deployment readiness if applicable
      - Run `code-review` workflow for peer review
      - Optional: If Test Architect module installed, run `/bmad:tea:automate` to expand guardrail tests
    </action>
    <action>Post a chain signal so the code-review run opens knowing the story landed (one call): `python3 {metodoloji-root}/bmad/scripts/blackboard.py mirror --key story.{story_key} --value "dev complete — story in review, QR created" --to bmad-code-review --note "Dev complete — story {story_key} in review, QR created; run the review and fold findings into docs/development/QR." --sender bmad-dev-story --project-root {project-root}` (repeating the same mirror never duplicates the waiting signal; it waits in `handoff.bmad-code-review` until a code-review run consumes it). The `--sender` matters here: `story.` is create-story's namespace too, and without it the diagnostic would attribute this baton to create-story. Mirror completion onto the intent bridge: `python3 {metodoloji-root}/bmad/scripts/blackboard.py write --key status --value complete --type state --project-root {project-root}` (stop skips story checks once progress is `complete`)</action>

    <!-- Live canvas feed: report the paths this run wrote so a watching canvas shows a real auto cell (advisory, fail-open — never blocks close) -->
    <action>Feed the live canvas for every path this run wrote under a watched prefix: `python3 {metodoloji-root}/bmad/scripts/blackboard.py canvas touch --path <written-file> --tool bmad-dev-story --project-root {project-root}` (repeat once per written file; a touch under no watched prefix returns ok and changes nothing)</action>

    <output>💡 **Tip:** For best results, run `code-review` using a **different** LLM than the one that implemented this story.</output>
    <check if="{sprint_status} file exists">
      <action>Suggest checking {sprint_status} to see project progress</action>
    </check>
    <action>Remain flexible - allow user to choose their own path or ask for other assistance</action>
  <action>Run: `python3 {metodoloji-root}/hooks/engine/resolve_customization.py --skill {skill-root} --key workflow.on_complete` — use your harness-native shell tool with the command as given (no extra wrapper params) — if the resolved value is non-empty, follow it as the final terminal instruction before exiting.</action>
  </step>

</workflow>

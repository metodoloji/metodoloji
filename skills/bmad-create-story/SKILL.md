---
name: bmad-create-story
description: '(BMad Method) Creates a dedicated story file with all the context the agent will need to implement it later. Use when the user says "create the next story" or "create story [story identifier]"'
triggers: ["bmad-create-story", "/bmad-create-story", "create-story", "create the next story"]
---

# Create Story Workflow

**Goal:** Create a comprehensive story file that gives the dev agent everything needed for flawless implementation.

**Your Role:** Story context engine that prevents LLM developer mistakes, omissions, or disasters.
- Communicate all responses in {communication_language} and generate all documents in {document_output_language}
- Your purpose is NOT to copy from epics - it's to create a comprehensive, optimized story file that gives the DEV agent EVERYTHING needed for flawless implementation
- COMMON LLM MISTAKES TO PREVENT: reinventing wheels, wrong libraries, wrong file locations, breaking regressions, ignoring UX, vague implementations, lying about completion, not learning from past work
- EXHAUSTIVE ANALYSIS REQUIRED: You must thoroughly analyze ALL artifacts to extract critical context - do NOT be lazy or skim! This is the most important function in the entire development process!
- UTILIZE SUBPROCESSES AND SUBAGENTS: Use research subagents, subprocesses or parallel processing if available to thoroughly analyze different artifacts simultaneously and thoroughly
- SAVE QUESTIONS: If you think of questions or clarifications during analysis, save them for the end after the complete story is written
- ZERO USER INTERVENTION: Process should be fully automated except for initial epic/story selection or missing documents

Subagents, when the capability is available, are an important part of this workflow. Use them as directed by the workflow steps.
If you need an explicit user instruction to run them, ask once now for the whole workflow run.

## Conventions

- Bare paths (e.g. `discover-inputs.md`) resolve from the skill root.
- `{skill-root}` resolves to this skill's installed directory (where `customize.toml` lives).
- `{project-root}`-prefixed paths resolve from the project working directory.
- `{skill-name}` resolves to the skill directory's basename.

## On Activation

### Step 1: Run the Orientation Digest

Run: `python3 {metodoloji-root}/bmad/scripts/orient.py --project-root {project-root}` — one read-only call, before anything else. It carries both roots, the core config this run reads (`{user_name}`, `{communication_language}`, `{document_output_language}`, `{project_name}`, `{date}` as `today`), every resolved output path (incl. `{planning_artifacts}`), the record inventory, the board's live focus (with a `STALE` flag when a hot run still claims `complete`) and **waiting hand-offs addressed to you** (the digest names the sender and the peek command), and skeleton/gate state. Read it once; never re-run it "for clean output" — it is small by construction. On failure, fall through to the config step's neutral defaults.

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
- `planning_artifacts`, `implementation_artifacts`
- `date` as system-generated current datetime

### Step 6: Greet the User

Greet `{user_name}`, speaking in `{communication_language}`.

### Step 7: Execute Append Steps

Execute each entry in `{workflow.activation_steps_append}` in order.

Activation is complete. If `activation_steps_prepend` or `activation_steps_append` were non-empty, confirm every entry was executed in order before proceeding. Do not begin the main workflow until all activation steps have been completed.

### Chain Handshake

Before loading the first step file, check the chain for signals addressed to you: `python3 {metodoloji-root}/bmad/scripts/blackboard.py handoffs --skill bmad-create-story --project-root {project-root}` — the canonical sender is a `bmad-create-epics-and-stories` run (a sprint-planning run may also hand off the sprint queue directly, as may a spec, PRD, or UX run). Read the named artifact first — when it is the epics file, that means the epic and story the signal names — alongside the epics/PRD/architecture inputs the step files select. Then complete the handshake: `python3 {metodoloji-root}/bmad/scripts/blackboard.py consume --channel handoff.bmad-create-story --project-root {project-root}` (consume only after `{implementation_artifacts}` is resolved and the story target located — an unconsumed signal keeps the hand-off waiting, which is correct when the user routes elsewhere). Mirror the run onto the session intent bridge so hooks attribute tool traffic to this run: `python3 {metodoloji-root}/bmad/scripts/blackboard.py write --key purpose --value "story {{story_key}}" --type state --project-root {project-root}`.

## Paths

- `sprint_status` = `{implementation_artifacts}/sprint-status.yaml`
- `epics_file` = `{planning_artifacts}/epics.md`
- `prd_file` = `{project-root}/docs/design/prds/prd.md`
- `architecture_file` = `{project-root}/docs/design/architecture/architecture.md`
- `ux_file` = `{project-root}/docs/design/ux-designs/ux-*/*.md`
- `story_title` = "" (will be elicited if not derivable)
- `default_output_file` = `{implementation_artifacts}/{{story_key}}.md`

## Input Files

| Input | Description | Path Pattern(s) | Load Strategy |
|-------|-------------|------------------|---------------|
| prd | PRD (fallback - epics file should have most content) | whole: `{project-root}/docs/design/prds/*prd*.md`, sharded: `{project-root}/docs/design/prds/*prd*/*.md` | SELECTIVE_LOAD |
| architecture | Architecture (fallback - epics file should have relevant sections) | whole: `{project-root}/docs/design/architecture/*architecture*.md`, sharded: `{project-root}/docs/design/architecture/*architecture*/*.md` | SELECTIVE_LOAD |
| ux | UX design (fallback - epics file should have relevant sections) | whole: `{project-root}/docs/design/ux-designs/*ux*.md`, sharded: `{project-root}/docs/design/ux-designs/*ux*/*.md` | SELECTIVE_LOAD |
| epics | Enhanced epics+stories file with BDD and source hints | whole: `{planning_artifacts}/*epic*.md`, sharded: `{planning_artifacts}/*epic*/*.md` | SELECTIVE_LOAD |

## Execution

<workflow>

<step n="1" goal="Determine target story">
  <check if="user provided the epic and story number such as 2-4 or 1.6 or epic 1 story 5">
    <action>Parse user-provided story path: extract epic_num, story_num, story_title from format like "1-2-user-auth"</action>
    <action>Set {{epic_num}}, {{story_num}}, {{story_key}} from user input</action>
    <action>GOTO step 2a</action>
  </check>

  <action>Check if {{sprint_status}} file exists for auto discover</action>
  <check if="sprint status file does NOT exist">
    <output>🚫 No sprint status file found and no story specified</output>
    <output>
      **Required Options:** (sprint planning is NOT a hard prerequisite — options 2 and 3 are first-class paths; the quality gate only fires for stories that reference an SP without one existing)
      1. Run `sprint-planning` to initialize sprint tracking (recommended)
      2. Provide specific epic-story number to create (e.g., "1-2-user-auth")
      3. Provide path to story documents if sprint status doesn't exist yet
    </output>
    <ask>Choose option [1], provide epic-story number, path to story docs, or [q] to quit:</ask>

    <check if="user chooses 'q'">
      <action>HALT - No work needed</action>
    </check>

    <check if="user chooses '1'">
      <output>Run sprint-planning workflow first to create sprint-status.yaml</output>
      <action>HALT - User needs to run sprint-planning</action>
    </check>

    <check if="user provides epic-story number">
      <action>Parse user input: extract epic_num, story_num, story_title</action>
      <action>Set {{epic_num}}, {{story_num}}, {{story_key}} from user input</action>
      <action>GOTO step 2a</action>
    </check>

    <check if="user provides story docs path">
      <action>Parse user-provided path: extract epic_num, story_num, story_title from path</action>
      <action>GOTO step 2a</action>
    </check>
  </check>

  <!-- Auto-discover from sprint status only if no user input -->
  <check if="no user input provided">
    <critical>MUST read COMPLETE {sprint_status} file from start to end to preserve order</critical>
    <action>Load the FULL file: {{sprint_status}}</action>
    <action>Read ALL lines from beginning to end - do not skip any content</action>
    <action>Parse the development_status section completely</action>

    <action>Find the FIRST story (by reading in order from top to bottom) where:
      - Key matches pattern: number-number-name (e.g., "1-2-user-auth")
      - NOT an epic key (epic-X) or retrospective (epic-X-retrospective)
      - Status value equals "backlog"
    </action>

    <check if="no backlog story found">
      <output>📋 No backlog stories found in sprint-status.yaml

        All stories are either already created, in progress, or done.

        **Options:**
        1. Run sprint-planning to refresh story tracking
        2. Load PM agent and run correct-course to add more stories
        3. Check if current sprint is complete and run retrospective
      </output>
      <action>HALT</action>
    </check>

    <action>Extract from found story key (e.g., "1-2-user-authentication"):
      - epic_num: first number before dash (e.g., "1")
      - story_num: second number after first dash (e.g., "2")
      - story_title: remainder after second dash (e.g., "user-authentication")
    </action>
    <action>Set {{story_id}} = "{{epic_num}}.{{story_num}}"</action>
    <action>Store story_key for later use (e.g., "1-2-user-authentication")</action>

    <!-- Mark epic as in-progress if this is first story -->
    <action>Check if this is the first story in epic {{epic_num}} by looking for {{epic_num}}-1-* pattern</action>
    <check if="this is first story in epic {{epic_num}}">
      <action>Load {{sprint_status}} and check epic-{{epic_num}} status</action>
      <action>If epic status is "backlog" → update to "in-progress"</action>
      <action>If epic status is "contexted" (legacy status) → update to "in-progress" (backward compatibility)</action>
      <action>If epic status is "in-progress" → no change needed</action>
      <check if="epic status is 'done'">
        <output>🚫 ERROR: Cannot create story in completed epic</output>
        <output>Epic {{epic_num}} is marked as 'done'. All stories are complete.</output>
        <output>If you need to add more work, either:</output>
        <output>1. Re-run `sprint-planning` — it re-scans the epic files and is the ONLY sanctioned writer of epic statuses (preserves advanced statuses, never downgrades)</output>
        <output>2. Create a new epic for additional work</output>
        <action>HALT - Cannot proceed</action>
      </check>
      <check if="epic status is not one of: backlog, contexted, in-progress, done">
        <output>🚫 ERROR: Invalid epic status '{{epic_status}}'</output>
        <output>Epic {{epic_num}} has invalid status. Expected: backlog, contexted, in-progress, or done</output>
        <output>Re-run `sprint-planning` to regenerate the file from the epic sources</output>
        <action>HALT - Cannot proceed</action>
      </check>
      <output>📊 Epic {{epic_num}} status updated to in-progress</output>
    </check>

    <action>GOTO step 2a</action>
  </check>
</step>

<step n="2" goal="Load and analyze core artifacts">
  <critical>🔬 EXHAUSTIVE ARTIFACT ANALYSIS - This is where you prevent future developer mistakes!</critical>

  <!-- Load all available content through discovery protocol -->
  <action>Read fully and follow `./discover-inputs.md` to load all input files</action>
  <note>Available content: {epics_content}, {prd_content}, {architecture_content}, {ux_content}, plus the project-context facts loaded during activation via `persistent_facts`.</note>

  <!-- Analyze epics file for story foundation -->
  <action>From {epics_content}, extract Epic {{epic_num}} complete context:</action> **EPIC ANALYSIS:** - Epic
  objectives and business value - ALL stories in this epic for cross-story context - Our specific story's requirements, user story
  statement, acceptance criteria - Technical requirements and constraints - Dependencies on other stories/epics - Source hints pointing to
  original documents <!-- Extract specific story requirements -->
  <action>Extract our story ({{epic_num}}-{{story_num}}) details:</action> **STORY FOUNDATION:** - User story statement
  (As a, I want, so that) - Detailed acceptance criteria (already BDD formatted) - Technical requirements specific to this story -
  Business context and value - Success criteria <!-- Previous story analysis for context continuity -->
  <check if="story_num > 1">
    <action>Find {{previous_story_num}}: scan {implementation_artifacts} for the story file in epic {{epic_num}} with the highest story number less than {{story_num}}</action>
    <action>Load previous story file: {implementation_artifacts}/{{epic_num}}-{{previous_story_num}}-*.md</action> **PREVIOUS STORY INTELLIGENCE:** -
  Dev notes and learnings from previous story - Review feedback and corrections needed - Files that were created/modified and their
  patterns - Testing approaches that worked/didn't work - Problems encountered and solutions found - Code patterns established <action>Extract
  all learnings that could impact current story implementation</action>
  </check>

  <!-- Git intelligence for previous work patterns -->
  <check
    if="previous story exists AND git repository detected">
    <action>Get last 5 commit titles to understand recent work patterns</action>
    <action>Analyze 1-5 most recent commits for relevance to current story:
      - Files created/modified
      - Code patterns and conventions used
      - Library dependencies added/changed
      - Architecture decisions implemented
      - Testing approaches used
    </action>
    <action>Extract actionable insights for current story implementation</action>
  </check>
</step>

<step n="3" goal="Architecture analysis for developer guardrails">
  <critical>🏗️ ARCHITECTURE INTELLIGENCE - Extract everything the developer MUST follow!</critical> **ARCHITECTURE DOCUMENT ANALYSIS:** <action>Systematically
  analyze architecture content for story-relevant requirements:</action>

  <!-- Load architecture - single file or sharded -->
  <check if="architecture file is single file">
    <action>Load complete {architecture_content}</action>
  </check>
  <check if="architecture is sharded to folder">
    <action>Load architecture index and scan all architecture files</action>
  </check> **CRITICAL ARCHITECTURE EXTRACTION:** <action>For
  each architecture section, determine if relevant to this story:</action> - **Technical Stack:** Languages, frameworks, libraries with
  versions - **Code Structure:** Folder organization, naming conventions, file patterns - **API Patterns:** Service structure, endpoint
  patterns, data contracts - **Database Schemas:** Tables, relationships, constraints relevant to story - **Security Requirements:**
  Authentication patterns, authorization rules - **Performance Requirements:** Caching strategies, optimization patterns - **Testing
  Standards:** Testing frameworks, coverage expectations, test patterns - **Deployment Patterns:** Environment configurations, build
  processes - **Integration Patterns:** External service integrations, data flows <action>Extract any story-specific requirements that the
  developer MUST follow</action>
  <action>Identify any architectural decisions that override previous patterns</action>

  <!-- Read existing code being modified — non-negotiable -->
  <critical>📂 READ FILES BEING MODIFIED — skipping this is the primary cause of implementation failures and review cycles</critical>
  <action>From the architecture directory structure, identify every file marked UPDATE (not NEW) that this story will touch</action>
  <action>Read each relevant UPDATE file completely. For each one, document in dev notes:
    - Current state: what it does today (state machine, API calls, data shapes, existing behaviors)
    - What this story changes: the specific sections or behaviors being modified
    - What must be preserved: existing interactions and behaviors the story must not break
  </action>
  <critical>A story implementation must leave the system working end-to-end — not just satisfy its stated ACs.
  If a behavior is required for the feature to work correctly in the existing system, it is a requirement
  whether or not it is explicitly written in the story. The dev agent owns this.</critical>
</step>

<step n="4" goal="Web research for latest technical specifics">
  <critical>🌐 ENSURE LATEST TECH KNOWLEDGE - Prevent outdated implementations!</critical> **WEB INTELLIGENCE:** <action>Identify specific
  technical areas that require latest version knowledge:</action>

  <!-- Check for libraries/frameworks mentioned in architecture -->
  <action>From architecture analysis, identify specific libraries, APIs, or
  frameworks</action>
  <action>For each critical technology, research latest stable version and key changes:
    - Latest API documentation and breaking changes
    - Security vulnerabilities or updates
    - Performance improvements or deprecations
    - Best practices for current version
  </action>
  **EXTERNAL CONTEXT INCLUSION:** <action>Include in story any critical latest information the developer needs:
    - Specific library versions and why chosen
    - API endpoints with parameters and authentication
    - Recent security patches or considerations
    - Performance optimization techniques
    - Migration considerations if upgrading
  </action>
</step>

<step n="5" goal="Create comprehensive story file">
  <critical>📝 CREATE ULTIMATE STORY FILE - The developer's master implementation guide!</critical>

  <action>Initialize from template.md:
  {default_output_file}</action>

  <!-- Template section mapping (each name fills the corresponding template heading):
       story_header              → # Story {{epic_num}}.{{story_num}}: {{story_title}} (title + Status)
       story_requirements        → ## Story + ## Acceptance Criteria
       developer_context_section → ## Dev Notes (architecture patterns, source tree, testing standards)
       technical_requirements    → ## Technical Tasks (tasks with AC references)
       architecture_compliance   → sub-section of Dev Notes (architecture alignment)
       library_framework_requirements → sub-section of Dev Notes (library versions, constraints)
       file_structure_requirements   → sub-section of Dev Notes (file paths, naming conventions)
       testing_requirements      → ## Definition of Done (test-related DoD items)
       previous_story_intelligence   → sub-section of Dev Notes (prior story learnings)
       git_intelligence_summary      → sub-section of Dev Notes (recent commit patterns)
       latest_tech_information       → sub-section of Dev Notes (library versions, API changes)
       project_context_reference    → sub-section of Dev Notes (project-context.md summary)
       story_completion_status   → ## Dev Agent Record (completion metadata)
  -->

  <template-output file="{default_output_file}">story_header</template-output>

  <!-- Story foundation from epics analysis -->
  <template-output
    file="{default_output_file}">story_requirements</template-output>

  <!-- Developer context section - MOST IMPORTANT PART -->
  <template-output file="{default_output_file}">
  developer_context_section</template-output>

  **DEV AGENT GUARDRAILS:** <template-output file="{default_output_file}">
  technical_requirements</template-output>
  <template-output file="{default_output_file}">architecture_compliance</template-output>
  <template-output
    file="{default_output_file}">library_framework_requirements</template-output>
  <template-output file="{default_output_file}">
  file_structure_requirements</template-output>
  <template-output file="{default_output_file}">testing_requirements</template-output>

  <!-- Previous story intelligence -->
  <check
    if="previous story learnings available">
    <template-output file="{default_output_file}">previous_story_intelligence</template-output>
  </check>

  <!-- Git intelligence -->
  <check
    if="git analysis completed">
    <template-output file="{default_output_file}">git_intelligence_summary</template-output>
  </check>

  <!-- Latest technical specifics -->
  <check if="web research completed">
    <template-output file="{default_output_file}">latest_tech_information</template-output>
  </check>

  <!-- Project context reference -->
  <template-output
    file="{default_output_file}">project_context_reference</template-output>

  <!-- Final status update -->
  <template-output file="{default_output_file}">
  story_completion_status</template-output>

  <!-- CRITICAL: Set status to ready-for-dev -->
  <action>Set story Status to: "ready-for-dev"</action>
  <action>Add completion note: "Ultimate
  context engine analysis completed - comprehensive developer guide created"</action>
</step>

<step n="6" goal="Update sprint status and finalize">
  <action>Validate the newly created story file {default_output_file} against `./checklist.md` and apply any required fixes before finalizing</action>
  <action>Save story document unconditionally</action>

  <!-- Update sprint status -->
  <check if="sprint status file exists">
    <action>Update {{sprint_status}}</action>
    <action>Load the FULL file and read all development_status entries</action>
    <action>Find development_status key matching {{story_key}}</action>
    <action>Verify current status is "backlog" (expected previous state)</action>
    <action>Update development_status[{{story_key}}] = "ready-for-dev"</action>
    <action>Update last_updated field to current date</action>

    <!-- Epic completion check: if all stories in this epic are now done/in-progress/ready-for-dev, mark epic as done -->
    <action>Check if all stories in epic {{epic_num}} have status != "backlog"</action>
    <check if="all stories in epic {{epic_num}} are non-backlog">
      <action>Check epic-{{epic_num}} status</action>
      <check if="epic status is 'in-progress' AND all stories in epic {{epic_num}} have status 'done'">
        <action>Update epic-{{epic_num}} status to "done"</action>
        <output>🎉 Epic {{epic_num}} complete — every story is done. Epic status updated to done.</output>
      </check>
      <check if="some stories in epic {{epic_num}} are still backlog or in early states">
        <output>ℹ️ Epic {{epic_num}} stays in-progress: {{remaining}} story/stories not done yet. The epic closes when code-review marks its last story done (step-04's epic completion check) — creating the story file is not completion.</output>
      </check>
    </check>

    <action>Save file, preserving ALL comments and structure including STATUS DEFINITIONS</action>
  </check>

  <!-- Chain hand-off: signal dev-story that this story is ready -->
  <action>Bind the story on the blackboard and fan out to both downstream runs (one heartbeat, two signals — the bridge): `python3 {metodoloji-root}/bmad/scripts/blackboard.py mirror --key story.{{story_key}} --value "context ready — story file final" --to bmad-dev-story --note "story ready-for-dev — file: {{default_output_file}}" --project-root {project-root}` (this run key is the relay heartbeat: the engine mirrors `methodology.last_story` from it automatically; the signal waits in `handoff.bmad-dev-story` until a dev run consumes it — that consumption completes the handshake). Queue the quality-record stage the same way: `python3 {metodoloji-root}/bmad/scripts/blackboard.py mirror --key story.{{story_key}} --value "context ready — story file final" --to bmad-quality-record --note "story {{story_key}} queued — create the QR record once the story reaches review/done (file: {{default_output_file}})" --project-root {project-root}` (waits in `handoff.bmad-quality-record` until a QR run consumes it). Mirror completion onto the intent bridge: `python3 {metodoloji-root}/bmad/scripts/blackboard.py write --key status --value complete --type state --project-root {project-root}` (stop skips story checks once progress is `complete`)</action>

  <!-- Live canvas feed: report the paths this run wrote so a watching canvas shows a real auto cell (advisory, fail-open — never blocks close) -->
  <action>Feed the live canvas for every path this run wrote under a watched prefix: `python3 {metodoloji-root}/bmad/scripts/blackboard.py canvas touch --path {{default_output_file}} --tool bmad-create-story --project-root {project-root}` (repeat once per written file; a touch under no watched prefix returns ok and changes nothing)</action>

  <action>Report completion</action>
  <output>**🎯 ULTIMATE BMad Method STORY CONTEXT CREATED, {user_name}!**

    **Story Details:**
    - Story ID: {{story_id}}
    - Story Key: {{story_key}}
    - File: {{default_output_file}}
    - Status: ready-for-dev

    **Next Steps:**
    1. Review the comprehensive story in {{default_output_file}}
    2. Run dev agents `dev-story` for optimized implementation
    3. Run `code-review` when complete (auto-marks done)
    4. Optional: If Test Architect module installed, run `/bmad:tea:automate` after `dev-story` to generate guardrail tests

    **The developer now has everything needed for flawless implementation!**
  </output>
  <action>Run: `python3 {metodoloji-root}/hooks/engine/resolve_customization.py --skill {skill-root} --key workflow.on_complete` — use your harness-native shell tool with the command as given (no extra wrapper params) — if the resolved value is non-empty, follow it as the final terminal instruction before exiting.</action>
</step>

</workflow>

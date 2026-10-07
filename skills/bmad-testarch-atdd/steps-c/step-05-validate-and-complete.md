---
name: 'step-05-validate-and-complete'
description: 'Validate ATDD outputs and summarize'
outputFile: '{test_artifacts}/atdd-checklist-{story_key}.md'
---

# Step 5: Validate & Complete

## STEP GOAL

Validate ATDD outputs and provide a completion summary.

## MANDATORY EXECUTION RULES

- 📖 Read the entire step file before acting
- ✅ Speak in `{communication_language}`
- ✅ Validate against the checklist

---

## EXECUTION PROTOCOLS:

- 🎯 Follow the MANDATORY SEQUENCE exactly
- 💾 Record outputs before proceeding
- 📖 Load the next step only when instructed

## CONTEXT BOUNDARIES:

- Available context: config, loaded artifacts, and knowledge fragments
- Focus: this step's goal only
- Limits: do not execute future steps
- Dependencies: prior steps' outputs (if any)

## MANDATORY SEQUENCE

**CRITICAL:** Follow this sequence exactly. Do not skip, reorder, or improvise.

## 1. Validation

Use `checklist.md` to validate:

- Prerequisites satisfied
- Test files created correctly
- Checklist matches acceptance criteria
- Tests are generated as red-phase scaffolds and marked with `test.skip()`
- Story metadata and handoff paths are captured for downstream workflows
- [ ] CLI sessions cleaned up (no orphaned browsers)
- [ ] Temp artifacts stored in `{test_artifacts}/` not random locations

Fix any gaps before completion.

---

## 2. Polish Output

Before finalizing, review the complete output document for quality:

1. **Remove duplication**: Progressive-append workflow may have created repeated sections — consolidate
2. **Verify consistency**: Ensure terminology, risk scores, and references are consistent throughout
3. **Check completeness**: All template sections should be populated or explicitly marked N/A
4. **Format cleanup**: Ensure markdown formatting is clean (tables aligned, headers consistent, no orphaned references)

---

## 3. Completion Summary

Report:

- Test files created
- Checklist output path
- Story key / story file handoff path
- Key risks or assumptions
- Next recommended workflow (usually `dev-story`; `automate` comes after implementation)

---

## 4. Save Progress

**Save this step's accumulated work to `{outputFile}`.**

- **If `{outputFile}` does not exist** (first save), create it with YAML frontmatter:

  ```yaml
  ---
  stepsCompleted: ['step-05-validate-and-complete']
  lastStep: 'step-05-validate-and-complete'
  lastSaved: '{date}'
  storyId: '{story_id}'
  storyKey: '{story_key}'
  storyFile: '{story_file}'
  atddChecklistPath: '{outputFile}'
  generatedTestFiles: []
  ---
  ```

  Then write this step's output below the frontmatter.

- **If `{outputFile}` already exists**, update:
  - Add `'step-05-validate-and-complete'` to `stepsCompleted` array (only if not already present)
  - Set `lastStep: 'step-05-validate-and-complete'`
  - Set `lastSaved: '{date}'`
  - Ensure `storyId`, `storyKey`, `storyFile`, and `atddChecklistPath` are present and populated
  - Ensure `generatedTestFiles` remains populated with the deterministic list of present generated test paths
  - Append this step's output to the appropriate section.

## 🚨 SYSTEM SUCCESS/FAILURE METRICS:

### ✅ SUCCESS:

- Step completed in full with required outputs

### ❌ SYSTEM FAILURE:

- Skipped sequence steps or missing outputs
  **Master Rule:** Skipping steps is FORBIDDEN.

## On Complete

Post the chain signal so the quality record opens knowing this evidence exists (one call):

`python3 {metodoloji-root}/bmad/scripts/blackboard.py mirror --key test.atdd.{story_key} --value "ATDD complete — {outputFile}" --to bmad-quality-record --note "ATDD tests/checklist ready for {story_key} — fold the mechanical checks into docs/quality/QR-<seq>.md" --project-root {project-root}`

The run key is this stage's heartbeat; repeating the same mirror never duplicates the waiting signal — it waits in `handoff.bmad-quality-record` until a QR run consumes it. This skill adds evidence to an existing QR record and never opens one of its own.

Run: `python3 {metodoloji-root}/hooks/engine/resolve_customization.py --skill {skill-root} --key workflow.on_complete` — use your harness-native shell tool with the command as given (no extra wrapper params)

If the resolver succeeds and returns a non-empty `workflow.on_complete`, execute that value as the final terminal instruction before exiting.

If the resolver fails, returns no output, or resolves an empty value, skip the hook and exit normally.

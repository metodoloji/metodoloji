---
name: 'step-05-validate-and-summary'
description: 'Validate against checklist and summarize'
outputFile: '{test_artifacts}/framework-setup-progress.md'
---

# Step 5: Validate & Summarize

## STEP GOAL

Validate framework setup and provide a completion summary.

## MANDATORY EXECUTION RULES

- 📖 Read the entire step file before acting
- ✅ Speak in `{communication_language}`

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

Validate against `checklist.md`:

- Preflight success
- Directory structure created
- Config correctness
- Fixtures/factories created
- Docs and scripts present

Fix any gaps before completion.

---

## 2. Completion Summary

Report:

- Framework selected
- Artifacts created
- Next steps (install deps, run tests)
- Knowledge fragments applied

---

### 3. Save Progress

**Save this step's accumulated work to `{outputFile}`.**

- **If `{outputFile}` does not exist** (first save), create it with YAML frontmatter:

  ```yaml
  ---
  stepsCompleted: ['step-05-validate-and-summary']
  lastStep: 'step-05-validate-and-summary'
  lastSaved: '{date}'
  ---
  ```

  Then write this step's output below the frontmatter.

- **If `{outputFile}` already exists**, update:
  - Add `'step-05-validate-and-summary'` to `stepsCompleted` array (only if not already present)
  - Set `lastStep: 'step-05-validate-and-summary'`
  - Set `lastSaved: '{date}'`
  - Append this step's output to the appropriate section of the document.

## 🚨 SYSTEM SUCCESS/FAILURE METRICS:

### ✅ SUCCESS:

- Step completed in full with required outputs

### ❌ SYSTEM FAILURE:

- Skipped sequence steps or missing outputs
  **Master Rule:** Skipping steps is FORBIDDEN.

## On Complete

Post the chain signal so the quality record opens knowing this evidence exists (one call):

`python3 {metodoloji-root}/bmad/scripts/blackboard.py mirror --key test.framework.<project-slug> --value "Test framework set up — {outputFile}" --to bmad-quality-record --note "Test framework/harness ready — fold the mechanical checks into docs/quality/QR-<seq>.md" --project-root {project-root}`

The run key is this stage's heartbeat; repeating the same mirror never duplicates the waiting signal — it waits in `handoff.bmad-quality-record` until a QR run consumes it. This skill adds evidence to an existing QR record and never opens one of its own.

Run: `python3 {metodoloji-root}/hooks/engine/resolve_customization.py --skill {skill-root} --key workflow.on_complete` — use your harness-native shell tool with the command as given (no extra wrapper params)

If the resolver succeeds and returns a non-empty `workflow.on_complete`, execute that value as the final terminal instruction before exiting.

If the resolver fails, returns no output, or resolves an empty value, skip the hook and exit normally.

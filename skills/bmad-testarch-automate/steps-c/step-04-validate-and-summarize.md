---
name: 'step-04-validate-and-summarize'
description: 'Validate outputs and produce automation summary'
outputFile: '{test_artifacts}/automation-summary.md'
---

# Step 4: Validate & Summarize

## STEP GOAL

Validate generated outputs and produce a concise automation summary.

## MANDATORY EXECUTION RULES

- 📖 Read the entire step file before acting
- ✅ Speak in `{communication_language}`
- ✅ Validate against the checklist before completion

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

## 1. Validate

Use `checklist.md` to validate:

- Framework readiness
- Coverage mapping
- Test quality and structure
- Fixtures, factories, helpers
- [ ] CLI sessions cleaned up (no orphaned browsers)
- [ ] Temp artifacts stored in `{test_artifacts}/` not random locations

Fix gaps before proceeding.

---

## 2. Polish Output

Before finalizing, review the complete output document for quality:

1. **Remove duplication**: Progressive-append workflow may have created repeated sections — consolidate
2. **Verify consistency**: Ensure terminology, risk scores, and references are consistent throughout
3. **Check completeness**: All template sections should be populated or explicitly marked N/A
4. **Format cleanup**: Ensure markdown formatting is clean (tables aligned, headers consistent, no orphaned references)

---

## 3. Summary Output

Write `{outputFile}` including:

- Coverage plan by test level and priority
- Files created/updated
- Key assumptions and risks
- Next recommended workflow (e.g., `test-review` or `trace`)

---

## 4. Save Progress

**Save this step's accumulated work to `{outputFile}`.**

- **If `{outputFile}` does not exist** (first save), create it with YAML frontmatter:

  ```yaml
  ---
  stepsCompleted: ['step-04-validate-and-summarize']
  lastStep: 'step-04-validate-and-summarize'
  lastSaved: '{date}'
  ---
  ```

  Then write this step's output below the frontmatter.

- **If `{outputFile}` already exists**, update:
  - Add `'step-04-validate-and-summarize'` to `stepsCompleted` array (only if not already present)
  - Set `lastStep: 'step-04-validate-and-summarize'`
  - Set `lastSaved: '{date}'`
  - Append this step's output to the appropriate section.

## 🚨 SYSTEM SUCCESS/FAILURE METRICS:

### ✅ SUCCESS:

- Step completed in full with required outputs

### ❌ SYSTEM FAILURE:

- Skipped sequence steps or missing outputs
  **Master Rule:** Skipping steps is FORBIDDEN.

## On Complete

Post the chain signal so the quality record opens knowing this evidence exists (one call):

`python3 {metodoloji-root}/bmad/scripts/blackboard.py mirror --key test.automate.<feature-slug> --value "Automation complete — {outputFile}" --to bmad-quality-record --note "Automated tests ready for <feature-slug> — fold the mechanical checks into docs/quality/QR-<seq>.md" --project-root {project-root}`

The run key is this stage's heartbeat; repeating the same mirror never duplicates the waiting signal — it waits in `handoff.bmad-quality-record` until a QR run consumes it. This skill adds evidence to an existing QR record and never opens one of its own.

Run: `python3 {metodoloji-root}/hooks/engine/resolve_customization.py --skill {skill-root} --key workflow.on_complete` — use your harness-native shell tool with the command as given (no extra wrapper params)

If the resolver succeeds and returns a non-empty `workflow.on_complete`, execute that value as the final terminal instruction before exiting.

If the resolver fails, returns no output, or resolves an empty value, skip the hook and exit normally.

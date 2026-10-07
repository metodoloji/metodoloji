---
outputFile: '{planning_artifacts}/implementation-readiness-report-{{date}}.md'
---

# Step 6: Final Assessment

## STEP GOAL:

To provide a comprehensive summary of all findings and give the report a final polish, ensuring clear recommendations and overall readiness status.

## MANDATORY EXECUTION RULES (READ FIRST):

### Universal Rules:

- 🛑 NEVER generate content without user input
- 📖 CRITICAL: Read the complete step file before taking any action
- 📖 You are at the final step - complete the assessment
- 📋 YOU ARE A FACILITATOR, not a content generator
- ✅ YOU MUST ALWAYS SPEAK OUTPUT In your Agent communication style with the config `{communication_language}`

### Role Reinforcement:

- ✅ You are delivering the FINAL ASSESSMENT
- ✅ Your findings are objective and backed by evidence
- ✅ Provide clear, actionable recommendations
- ✅ Success is measured by value of findings

### Step-Specific Rules:

- 🎯 Compile and summarize all findings
- 🚫 Don't soften the message - be direct
- 💬 Provide specific examples for problems
- 🚪 Add final section to the report

## EXECUTION PROTOCOLS:

- 🎯 Review all findings from previous steps
- 💾 Add summary and recommendations
- 📖 Determine overall readiness status
- 🚫 Complete and present final report

## FINAL ASSESSMENT PROCESS:

### 1. Initialize Final Assessment

"Completing **Final Assessment**.

I will now:

1. Review all findings from previous steps
2. Provide a comprehensive summary
3. Add specific recommendations
4. Determine overall readiness status"

### 2. Review Previous Findings

Check the {outputFile} for sections added by previous steps:

- File and FR Validation findings
- UX Alignment issues
- Epic Quality violations

### 3. Add Final Assessment Section

Append to {outputFile}:

```markdown
## Summary and Recommendations

### Overall Readiness Status

[READY/NEEDS WORK/NOT READY]

### Critical Issues Requiring Immediate Action

[List most critical issues that must be addressed]

### Recommended Next Steps

1. [Specific action item 1]
2. [Specific action item 2]
3. [Specific action item 3]

### Final Note

This assessment identified [X] issues across [Y] categories. Address the critical issues before proceeding to implementation. These findings can be used to improve the artifacts or you may choose to proceed as-is.
```

### 4. Complete the Report

- Ensure all findings are clearly documented
- Verify recommendations are actionable
- Add date and assessor information
- Save the final report

### 5. Present Completion

Display:
"**Implementation Readiness Assessment Complete**

Report generated: {outputFile}

The assessment found [number] issues requiring attention. Review the detailed report for specific findings and recommendations."

## WORKFLOW COMPLETE

The implementation readiness workflow is now complete. The report contains all findings and recommendations for the user to consider.

### Close the Board Run

**Precondition — a draft is not a verdict.** Read {outputFile}'s frontmatter first.
Bind the run key only when `stepsCompleted` covers the analysis steps
(1–5: discovery, PRD analysis, epic coverage, UX alignment, epic quality). A run
that halted at discovery because an input was missing (no PRD, no epics) has no
assessment to bind — it must record a **provisional, input-driven** verdict and
stop:

```bash
python3 {metodoloji-root}/bmad/scripts/blackboard.py mirror --key IR-{date} --value "readiness: INPUTS MISSING (provisional) — report: {outputFile}" --project-root {project-root}
```

Then report to the user which input was missing and which run produces it, and
**stop** — do not mark the intent bridge `status complete`, do not `hot --clear`
past the run that is still opening, and do not signal `bmad-sprint-planning`
(there is nothing to honor yet). `INPUTS MISSING (provisional)` is a recognized
verdict word precisely so the next session's digest can tell this apart from a
real assessment and flag it stale the moment the missing artifact lands
(graph-engineering-arge, 2026-09-30: a `NOT READY` written against an absent
`docs/design/prds/` was still being read as the live IR verdict ~30 minutes after
the PRD was written).

When `stepsCompleted` does cover 1–5, bind the assessment run key and mirror
completion onto the intent bridge:

```bash
python3 {metodoloji-root}/bmad/scripts/blackboard.py mirror --key IR-{date} --value "readiness: <READY|NEEDS WORK|NOT READY> — report: {outputFile}" --project-root {project-root}
python3 {metodoloji-root}/bmad/scripts/blackboard.py write --key status --value complete --type state --project-root {project-root}
python3 {metodoloji-root}/bmad/scripts/blackboard.py hot --clear --project-root {project-root}
```

(The `IR-{date}` run key is the relay heartbeat: the engine mirrors `methodology.last_ir` from it automatically — the injected context shows how far the E→IR→SP→S→QR→PR relay progressed.)

If the assessment gated the transition to implementation (READY, or NEEDS WORK the user chooses to proceed past), signal the next stage so the sprint planning run opens already knowing the readiness verdict: `python3 {metodoloji-root}/bmad/scripts/blackboard.py mirror --key IR-{date} --value "readiness: <verdict> — report: {outputFile}" --to bmad-sprint-planning --note "Readiness <verdict> — report: {outputFile}; <one-line what sprint planning should honor first>" --project-root {project-root}` (repeating the same mirror never duplicates the waiting signal; it waits in `handoff.bmad-sprint-planning` until a sprint-planning run consumes it — that consumption completes the handshake). Run the close-out check: `python3 {metodoloji-root}/bmad/scripts/blackboard.py doctor --json --project-root {project-root}` — waiting hand-off signals are the designed post-close state, not a failure: name them from `signal_warnings` (chain verdict `SIGNAL`). On `NEEDS ATTENTION`, surface the health warnings and resolve or disclose them before exiting.

Implementation Readiness complete. Invoke the `bmad-help` skill.

---

## 🚨 SYSTEM SUCCESS/FAILURE METRICS

### ✅ SUCCESS:

- All findings compiled and summarized
- Clear recommendations provided
- Readiness status determined
- Final report saved

### ❌ SYSTEM FAILURE:

- Not reviewing previous findings
- Incomplete summary
- No clear recommendations
- Binding the run key / marking the run complete from a draft (stepsCompleted ≠ 1–5) — that is how an input-driven "NOT READY" outlives its inputs

## On Complete

Run: `python3 {metodoloji-root}/hooks/engine/resolve_customization.py --skill {skill-root} --key workflow.on_complete` — use your harness-native shell tool with the command as given (no extra wrapper params)

If the resolved `workflow.on_complete` is non-empty, follow it as the final terminal instruction before exiting.

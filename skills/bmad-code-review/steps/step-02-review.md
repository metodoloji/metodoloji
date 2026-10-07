---
failed_layers: '' # set at runtime: comma-separated list of layers that failed or returned empty
---

# Step 2: Review

## RULES

- YOU MUST ALWAYS SPEAK OUTPUT in your Agent communication style with the config `{communication_language}`
- All review subagents should run at the same model capability as the current session. When spawning agents via the `Agent` tool, omit the `model` parameter to inherit the session's model. Only override if you have a specific reason (e.g., cost optimization for mechanical tasks).

## INSTRUCTIONS

1. If `{review_mode}` = `"no-spec"`, note to the user: "Acceptance Auditor skipped — no spec file provided."

2. Launch Blind Hunter and Edge Case Hunter in parallel without prior conversation context. If `{review_mode}` = `"full"`, include the Acceptance Auditor in the same parallel launch. If subagents are not available, generate prompt files in a temp directory (e.g., `{project-root}/.review-prompts/`) for each applicable reviewer role and HALT. Ask the user to run each in a separate session (ideally a different LLM) and paste back the findings. When findings are pasted, resume from this point and proceed to step 3.

   - **Blind Hunter** — prompt:
     > Invoke the `bmad-review-adversarial-general` skill on this diff:
     >
     > {diff_output}

   - **Edge Case Hunter** — prompt:
     > Invoke the `bmad-review-edge-case-hunter` skill on this diff:
     >
     > {diff_output}

   - **Acceptance Auditor** (only if `{review_mode}` = `"full"`) — prompt:
     > You are an Acceptance Auditor. Review the provided diff against `{spec_file}` and any loaded context docs. Check for:
     >
     > 1. **AC Compliance**: violations of acceptance criteria, deviations from spec intent, missing implementation of specified behavior
     > 2. **AC Metadata Validation**: for each AC in the spec:
     >    - Has [AC-XXX] identifier
     >    - Has Experiment reference (E-XXX or —)
     >    - Has Type (agent-verifiable/user-evaluable/hybrid)
     >    - Has Measured field (true/false)
     >    - Has Verify field (verification method)
     >    - If Experiment=— or Measured=false, has [HYPOTHESIS] tag
     > 3. **Task↔AC Traceability**: every Technical Task references at least one AC (AC: AC-XXX)
     > 4. **DoD Compliance**: every Definition of Done item has:
     >    - DoD identifier (DoD-XXX)
     >    - AC reference if AC-related
     >    - Verify field (verification method)
     >    - Evidence field
     > 5. **QR Completeness**: Quality Record section has results for all DoD items
     > 6. **Contradictions**: between spec constraints and actual code
     >
     > Output findings as a Markdown list. Each finding: one-line title, category (AC/Metadata/Task/DoD/QR), which AC/constraint it violates, severity (High/Med/Low), and evidence from the diff.
     >
     > Diff:
     > {diff_output}

3. **Subagent failure handling**: If any subagent fails, times out, or returns empty results, append the layer name to `{failed_layers}` (comma-separated) and proceed with findings from the remaining layers.

4. Collect all findings from the completed layers.

5. **Emit a Per-Layer Report** before triaging: one subsection per launched layer (**Blind Hunter**, **Edge Case Hunter**, **Acceptance Auditor**). List that layer's findings under its own heading; if a layer has no findings, write exactly "No findings" under that heading. Never collapse multiple layers into a single "other layers" line. When a spec/story is available, the Acceptance Auditor subsection must enumerate each acceptance criterion and mark it Pass/Fail with file:line evidence.

6. **Actionable fixes only**: if a finding proposes a code change, include a concrete, idiomatic fix snippet (e.g., ORM `select_related`/`prefetch_related`), never abstract advice. Explicitly flag N+1 query patterns (loops that issue per-row queries), quantify the query count growth (e.g., `1 + 2N`), and provide the idiomatic ORM fix.


## NEXT

Read fully and follow `./step-03-triage.md`

<!-- Record template — Development Gate 1: checks whether research findings are
     ready for development. /metodoloji:init copies this file to
     docs/development/_template_IR.md; copy THAT to docs/development/IR-NNN.md and
     replace every [bracketed] placeholder. Instruction text that survives into
     the record means the field was never judged — the template is a skeleton, not
     a document to ship.
     A verdict is a snapshot, not a property: when a judged input changes
     afterwards (a PRD lands, UX is revised), re-run the assessment as a new
     record — never edit a written verdict to match new inputs. -->

# Implementation Readiness: IR-XXX — [Short Title]

- **Date:** [YYYY-MM-DD]
- **Status:** preparing | READY | INCOMPLETE
- **Research inputs:** [E/R/D/C-id list]
  - Example: E-001 (BFS optimization), D-005 (user flow design), R-010 (user needs finding)
- **Design documents:**
  - PRD: [file path or "none"]
  - UX Spec: [file path or "none"]
  - Architecture Plan: [file path or "none"]
- **Success criteria:**
  - [Criterion 1] — Metric: [metric name]; Source: [field, file, or command that carries the value]; Target: [threshold, or "baseline first"]
  - [Criterion 2] — Metric: [metric name]; Source: [field, file, or command]; Target: [threshold]
  - Functional: [what the feature must do] — Verify: [how a reviewer or automated check confirms it]
  - Non-functional: [performance/security target] — Verify: [the measurement command or gate that runs it]
  - User: [acceptance criterion] — Verify: [who confirms it and how]
- **Instrumentation gaps:**
  - [Claim] → not measurable because [missing signal/instrumentation] → would need [what to add]
- **Technical dependencies:**
  - APIs: [which APIs needed, available?]
  - Libraries: [new dependencies, versions]
  - Infrastructure: [database, cache, queue ready?]
  - External services: [3rd party integrations]
- **Risk assessment:**
  - Risk 1: [description] → Mitigation: [how to reduce]
  - Risk 2: [description] → Mitigation: [how to reduce]
- **Gaps:**
  - [Gap item 1] → [Research mode: A/B/C/D] → [Estimated time]
  - [Gap item 2] → [Research mode: A/B/C/D] → [Estimated time]
- **Decision:** READY | INCOMPLETE → [Rationale]
- **Next step:** proceed to sprint planning | return to research: [which mode, which question]

---

## Notes

- **READY:** All inputs complete, dependencies ready, every checklist item below
  resolved, sprint can be planned. A READY verdict with an unchecked mandatory
  item is not READY — `check-methodology.sh` CHECK 7 fails it.
- **INCOMPLETE:** Gaps identified, research plan exists for each gap.
- If gaps exist, the sprint does not start; return to the research wing first
- This record is the "entry gate" of the development wing: research → development transition
- **Unmeasurable is a finding, not a failure.** When a product promise has no
  signal behind it, the honest verdict records it under *Instrumentation gaps*
  and names what it would take — the alternative (a qualitative "criterion" that
  can never fail) is what makes a gate decorative.

---

## Checklist (Gate 1 Check)

- [ ] At least one approved research record exists (E/R/D/C-id)
- [ ] PRD or story defined
- [ ] UX spec ready (if needed)
- [ ] Architecture plan ready (if needed)
- [ ] Success criteria clear and measurable — every criterion names a metric + source + target, or is recorded as an instrumentation gap
- [ ] Technical dependencies identified
- [ ] Risk assessment done
- [ ] Plan exists for gaps (if any)

<!-- Record template — Development Gate 2: guarantees the sprint scope is clear,
     measurable, and realistic. /metodoloji:init copies this file to
     docs/development/_template_SP.md; copy THAT to docs/development/SP-NNN.md and
     replace every [bracketed] placeholder. Instruction text that survives into
     the record means the field was never judged. -->

# Sprint: SP-XXX — [Sprint Name]

- **Date:** [Start: YYYY-MM-DD] — [End: YYYY-MM-DD]
- **Status:** planned | in-progress | completed | cancelled
- **Readiness input:** [IR-id + path this sprint plans from, e.g. IR-001 (`docs/development/IR-001.md`)]
- **Sprint goal:** [Single sentence — what will be achieved in this sprint]
- **Stories:**
  - S-001: [Story title] — Priority: High — [3 story points]
  - S-002: [Story title] — Priority: Medium — [5 story points]
  - S-003: [Story title] — Priority: Low — [2 story points]
  - **Total:** [X story points]
- **Capacity:**
  - Team velocity:
  - This sprint capacity: [X points]
- **Technical debt:**
  - [Debt item 1]: [description] — [estimated effort]
  - [Debt item 2]: [description] — [estimated effort]
  - **Total debt effort:** [Y points]
  - **Total sprint load:** [X feature points + Y debt points = Z total]
- **Blockers:**
  - [Blocker 1]: [description] → Solution: [plan] → Owner: [who]
  - [Blocker 2]: [description] → Solution: [plan] → Owner: [who]
- **Dependencies:**
  - [Dependency 1]: [description] → Status: [ready/waiting] → Contact: [who]
  - [Dependency 2]: [description] → Status: [ready/waiting] → Contact: [who]

---

## Daily Notes (Daily Standup — optional)

### [YYYY-MM-DD] — Day 1
- **Progress:** [What was completed]
- **Blockers:** [Any new blockers]
- **Today's plan:** [What will be done today]

### [YYYY-MM-DD] — Day 2
- **Progress:**
- **Blockers:**
- **Today's plan:**

[Daily notes optional; depends on team preference]

---

## Sprint Review (End of Sprint)

- **Completed stories:**
  - S-001: ✓ COMPLETED
  - S-002: ✓ COMPLETED
  - S-003: ✗ NOT COMPLETED (reason: [explanation])
- **Completion rate:** [X/Y stories completed] → [Z%]
- **Velocity:**
- **Reasons (for incomplete):**
  - [Story ID]: [Why not completed, carry over or cancel]
- **Demos:**
- **Next step:** [Next sprint plan | retrospective findings]

---

## Retrospective Findings (End of Sprint)

- **What went well:**
- **Could be improved:**
- **Actions:**
  - Action 1: [description] → Owner: [who]
  - Action 2: [description] → Owner: [who]

---

## Checklist (Gate 2 Check)

- [ ] Sprint goal is clear and expressible in one sentence
- [ ] Readiness input (IR) recorded — an SP that names no IR record fails the story chain check
- [ ] S-id record exists for every story
- [ ] Acceptance criteria written for every story
- [ ] Story points assigned and realistic
- [ ] Sprint capacity fits team velocity (not overloaded)
- [ ] Technical debt assessed and time-boxed
- [ ] Blockers identified and resolution plan exists
- [ ] External dependencies identified and contacted

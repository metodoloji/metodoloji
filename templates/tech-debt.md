# Technical Debt Tracking

> This file tracks technical debt accumulated in the project and the repayment plan.
> A time-box should be reserved for technical debt in every sprint.

**Last updated:** [YYYY-MM-DD]

> **How to use this template.** Copy it to `docs/development/tech-debt.md` on the first
> install and fill every bracketed slot with a real value. The bracketed grammar
> (`[X items]`, `[YYYY-MM-DD]`, `[Description]`, …) belongs to this template only: the live
> ledger must not keep a single one, and `scripts/check-techdebt.sh` §7 fails it if it does.
> The checker also compares this file's **shape** with the live ledger's — heading sequence
> and every table's header row — so keep the headings and columns exactly as they are while
> you rewrite the rows and the prose.

---

## Active Technical Debts

### Critical Priority (P0)

| ID | Description | Why Added | Date Added | Impact | Owner | Target Sprint |
|----|-------|---------------|---------------|------|--------|--------------|
| —    | —     | —             | —             | —    | —      | —            |

### High Priority (P1)

| ID | Description | Why Added | Date Added | Impact | Owner | Target Sprint |
|----|-------|---------------|---------------|------|--------|--------------|
| —    | —     | —             | —             | —    | —      | —            |

### Medium Priority (P2)

| ID | Description | Why Added | Date Added | Impact | Owner | Target Sprint |
|----|-------|---------------|---------------|------|--------|--------------|
| —    | —     | —             | —             | —    | —      | —            |

### Low Priority (P3)

| ID | Description | Why Added | Date Added | Impact | Owner | Target Sprint |
|----|-------|---------------|---------------|------|--------|--------------|
| TD-XXX | [Description] | [Why added] | [YYYY-MM-DD] | [Impact] | [@owner] | [Target sprint] |

---

## Paid Debts (Completed)

| ID | Description | Solution | Completion Date | Sprint | PR/QR |
|----|-------|-------|-------------------|--------|-------|
| TD-XXX | [Description] | [Solution] | [YYYY-MM-DD] | [Sprint] | [PR/QR] |
---

## Technical Debt Categories

### 1. Code Quality
- **Definition:** Poorly written code, code smells, duplicate code
- **Examples:**
  - Complex functions (high cyclomatic complexity)
  - Dead code (unused functions/classes)
  - Magic numbers (hardcoded constants)
  - God objects (too many responsibilities)

### 2. Test Debt
- **Definition:** Missing tests, flaky tests, low coverage
- **Examples:**
  - Unit test coverage < 80%
  - Missing integration tests
  - Insufficient E2E test coverage
  - Flaky tests (intermittent failures)

### 3. Documentation Debt
- **Definition:** Missing or outdated documentation
- **Examples:**
  - Missing API documentation
  - Insufficient code comments
  - README not up to date
  - Runbook missing or outdated

### 4. Architecture Debt
- **Definition:** Architecture decisions, tight coupling, scalability issues
- **Examples:**
  - Monolith to microservice migration needed
  - Tight coupling (too many dependencies)
  - Database schema normalization issue
  - Scalability bottlenecks

### 5. Infrastructure Debt
- **Definition:** Infrastructure, deployment, monitoring gaps
- **Examples:**
  - Manual deployment steps (missing automation)
  - Insufficient monitoring
  - Missing log aggregation
  - No disaster recovery plan

### 6. Dependency Debt
- **Definition:** Outdated dependencies, security vulnerabilities
- **Examples:**
  - Deprecated libraries
  - Known security vulnerabilities
  - End-of-life technologies

### 7. Performance Debt
- **Definition:** Performance optimizations, memory leaks
- **Examples:**
  - N+1 query problem
  - Memory leak
  - Inefficient algorithm (O(n²) → O(n log n))
  - Missing caching

---

## Technical Debt Metrics

### Current State
- **Total active debt:** [X items]
  - P0 (Critical): [X items]
  - P1 (High): [X items]
  - P2 (Medium): [X items]
  - P3 (Low): [X items]
- **Estimated repayment time:** [Y weeks]

### Sprint Allocation
- **Technical debt time-box per sprint:** 20% of capacity ([X] point(s) in a [Y]-point sprint)
- **Debt paid in the last 3 sprints:** [X items]
- **Debt added in the last 3 sprints:** [X items]
- **Net debt change:** [X - Y = Z] (negative = debt increasing)

### Trend
```
Sprint              | New Debt | Paid Debt | Net Change | Total Debt
--------------------|----------|-----------|------------|------------
[SP-NNN]            | [X]      | [Y]       | [Z]        | [W]
```

**How to read this table** (the derivation is part of the record, not left to the reader):
- One row per sprint, oldest first. Every row traces to a `Sprint`, `Completion Date` or
  dated note in this file — no number here is estimated.
- `New Debt` counts debts whose Date Added falls in that sprint.
- `Total Debt` is the active count at the sprint's end.

---

## Repayment Strategy

### Principle: Boy Scout Rule
> "Leave the code cleaner than you found it." Reduce debt by making small improvements in every PR.

### Time-Box Allocation
- **Every sprint:** Reserve 20% capacity for technical debt
- **Critical debt (P0):** Take into the immediate sprint, before features
- **High debt (P1):** Plan within the next 2 sprints
- **Medium/Low debt (P2/P3):** Handle opportunistically (during refactoring)

### Debt Prioritization Criteria
1. **Security risk:** If there is a vulnerability, P0
2. **Production incident risk:** If it could cause an incident, P0/P1
3. **Development speed impact:** If it slows down new feature development, P1
4. **Maintenance cost:** If it continuously produces bugs, P1
5. **Code smell:** If it's just bad code, P2/P3

---

## Debt Addition Rules

### Can Debt Be Added?
- ✓ **Yes:** If a temporary shortcut was taken to deliver quickly (conscious decision)
- ✓ **Yes:** If test coverage was temporarily lowered (with a repayment plan)
- ✗ **No:** If poor quality code is written saying "we'll fix it later" (unacceptable)
- ✗ **No:** If debt is left unrecorded (hidden debt is forbidden)

### Debt Addition Process
1. Identified during QR (Quality Review)
2. Added to `tech-debt.md` (ID, description, rationale, priority)
3. `// TODO: [TD-XXX] ...` comment added in code
4. Target sprint determined (mandatory for P0/P1)

### Debt Limit
- **Hard limit:** If P0 debt count > 5, no new features; pay off debt first
- **Soft limit:** If total active debt > 30, a debt repayment sprint is scheduled

---

## TODO Comment Standard

```python
# TODO: [TD-XXX] <Short description>
# Detail: <What needs to be fixed>
# Added: <YYYY-MM-DD> - <@username>
```

**Example:**
```python
# TODO: [TD-042] BFS algorithm is O(n²), should be optimized to O(n log n)
# Detail: Priority queue implementation using a heap instead of nested loop
# Added: 2026-08-15 - @ahmet
def bfs(graph):
    # Temporary implementation
    pass
```

---

## Review

### Weekly Review
- **Owner:** Tech Lead
- **What:** Newly added debt is reviewed, priorities updated
- **Action:** Critical debt is taken into the sprint

### Monthly Health Check
- **Owner:** Team + Engineering Manager
- **What:** Technical debt metrics reviewed, trend analysis done
- **Action:** Debt repayment strategy adjusted

### Quarterly Audit
- **Owner:** Entire engineering organization
- **What:** All technical debt audited, which ones are still valid
- **Action:** Old/irrelevant debt removed, new ones added

---

## Notes

- Technical debt **cannot be hidden** (development honesty rule 3)
- Technical debt is checked at every QR and recorded if present
- Debt repayment is **as important as feature delivery**
- Principle: not "move fast and break things" but "move at a sustainable pace"

---

## Related Documents

- Development Methodology — **plugin-canonical**, read `{metodoloji-root}/docs/bmad/development-methodology.md`
  (it is never copied into the project, so a relative link would be dead)
- [Quality Review Template](_template_QR.md)
- [Sprint Template](_template_SP.md)

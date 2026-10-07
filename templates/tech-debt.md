# Technical Debt Tracking

> This file tracks technical debt accumulated in the project and the repayment plan.
> A time-box should be reserved for technical debt in every sprint.

**Last updated:** 2026-10-03 (E-061 — TD-014 deepened to a single-board derivation)

> **2026-08-26 — SkillOpt tech-debt integration completed.** With 12 commits (957e5c2..8f2eb93),
> all debt in all 6 manifests was paid (TD-001/002/003/010/011/012), the SkillOpt `techdebt` task
> category was added (6/6 benchmarks), `scripts/check-techdebt.sh` inventory checks mechanically in
> 5 sections, baseline.json is in the repo (66/66), 151 tests PASS, 3/3 negtest. Active
> debt: **0**. The next debt cycle will start with new P3 records.

> **2026-09-28 — SP-001 retrospective.** Active debt: **1** (TD-015, P2 test debt). The blackboard integration
> completion (S-054/S-055, E-054/E-055, QR-013) added no debt of its own; TD-015 was recorded because the two
> scope-coverage guard tests read the live working tree and therefore report environmental failures on any dirty
> tree. Target sprint SP-003.
>
> **2026-09-28 — SP-004 repayment (E-058).** TD-015 paid: the scope-coverage family now runs against a fixture git
> repo (7 tests de-live-treed), verified by 35 bench trials including the hermeticity pair — the mirror still
> reports a planted probe in the live repo while `pytest -k scope` stays green. Active debt: **0**.
> Deleted-instance-artifact note (SP-003 retro Action 4) re-checked 2026-10-03: `scripts/tests` is green
> (0 failures) against the current tree, so the note no longer reproduces and no TD-016 was opened.

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
| —    | —     | —             | —             | —    | —      | —            |

---

## Paid Debts (Completed)

| ID | Description | Solution | Completion Date | Sprint | PR/QR |
|----|-------|-------|-------------------|--------|-------|
| TD-001 | `test_marketplace_json_content` (bmad-module-builder) marked as broken test — still listed as P0 in the manifest | Verified with pytest on 2026-08-26: 9/9 test_scaffold_standalone_module + 7/7 test_scaffold_setup_skill + 21/21 test_validate_module (total 37/37) PASS. Scaffold produces `marketplace_name = f"bmad-{args.module_code}"`, test expects `bmad-exc` — compatible. Debt record was old (2026-08-19) and scaffold had been working correctly since the initial commit; the record was incorrectly updated | 2026-08-26 | — | 1fcc9fd |
| TD-003 | Test suites of other installed skills (except bmad-workflow-builder) had never been run | Verified with pytest on 2026-08-26 — 9 test files, **114/114 PASS**: bmad-architecture (28) + bmad-brainstorming (27) + bmad-customize (13) + bmad-eval-runner (4+11=15) + bmad-forge-idea (16) + bmad-workflow-builder (2+7+6=15). All active skill tests deterministic and green | 2026-08-26 | — | 1fcc9fd |
| TD-002 | Monitor false-positive: `tech-debt.md` template placeholders were mistaken for real debt (`sprint-status.sh`/`tech-debt-monitor.sh` showed "4 active debts, P0:1") | `scripts/check-techdebt.sh` 5 sections: (1) template/live identity, (2) active ID unique + sequential, (3) P0 hard limit (<=5), (4) no active/paid overlap, (5) orphan TODO; 3-stage `--negtest`; integrated as `check-plugin.sh §6b` | 2026-08-26 | — | 1de54b1 |
| TD-010 | Windows UTF-8 corruption in all Python scripts (cp1254) | Added `encoding="utf-8", errors="replace"` to `subprocess.run(..., text=True)` calls (14 files, 19 calls) | 2026-08-19 | — | — |
| TD-011 | `check-methodology.sh` §2b bridge check false-positive (KÖPRÜ merge seemed "absent") | Added `encoding="utf-8"` to §2b subprocess; `STATUS: HEALTHY` | 2026-08-19 | — | — |
| TD-012 | SkillOpt API key hard-coded in source (`sk-*` setdefault fallbacks) — security debt + breaking deterministic training | 4 steps: (1) `.env.example` template + `.gitignore` `.env` protection, (2) removed setdefault fallbacks in `optimization/train.py` + `cli.py` + `envs/bmad/adapter.py`, (3) `train._require_llm_env()` errors on missing env in non-`--benchmark` paths, (4) added `check-plugin.sh §6a` 4 sub-checks + 3-stage `--negtest` | 2026-08-26 | — | cf679ea |
| TD-013 | Tighten gates to hard (`code_guard`/`quality_gate`/`deploy_guard` in `custom/config.toml [hooks]`) | Flipped all three to `"hard"` (`stop_guard` stays `"soft"`); evidence E-014 40/40 + E-015 36/36 + E-016 44/44 + E-017 54/54 + E-018 59/59 and 4 live rehearsals (code DENY, QR-less commit DENY, PR-less deploy DENY, own-commit ALLOW); `docs/CLAUDE.md` toml block synced; road here: S-006 (AC tables), S-007 (orphan), S-008 (status), S-009 (Check-2) | 2026-09-19 | SP-002 | QR-005 |
| TD-014 | Split-brain in hand-off diagnostics (replay vs snapshot) | Hybrid fix: waiting + stale from the folded snapshot, consumed from the event replay, poster sender persisted into snapshot alerts; evidence E-019 36/36 (ghost/cap/deleted-log agreement, sender attribution, stale flags, robustness, delegation, history) + 795 suite incl. 2 migrated stale-path tests. Deepened 2026-10-03 (E-061): the hybrid's replay is gone — `consumed` now folds into the same board (persisted plane, v3 migration) so no rotated/lost log can stage a divergence; bench 35/35 board_single_source_validity | 2026-09-19 | SP-004 | QR-011 |
| TD-015 | The scope-coverage guard tests read the *live* plugin working tree (`git -C <plugin-root> diff/ls-files` + its `docs/experiments/`), so any uncommitted edit read as a scope miss and the suite could not testify for a change made in the same session | Fixture repo: `_scope_repo()` builds a real git repo (baseline commit + deterministic edit set + an APPROVED record with a genuine token for a bench-local key) and every scope test points at it; 7 tests de-live-treed (4 planted probes in the real repo, 3 root reads). Evidence E-058 35/35 (20 mirror verdicts, 10 commit-time surfaces, 5 hermeticity incl. `pytest -k scope` green while an out-of-scope probe is planted in the live repo); the full engine suite is green on a dirty tree | 2026-09-28 | SP-004 | QR-016 |
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
  - P0 (Critical): [Y items]
  - P1 (High): [Z items]
  - P2 (Medium): [W items]
  - P3 (Low): [V items]
- **Estimated repayment time:** [X sprint / Y weeks]

### Sprint Allocation
- **Technical debt time-box per sprint:** [20% or X story points]
- **Debt paid in the last 3 sprints:** [X items]
- **Debt added in the last 3 sprints:** [Y items]
- **Net debt change:** [X - Y = Z] (positive = debt decreasing, negative = debt increasing)

### Trend
```
Sprint    | New Debt | Paid Debt | Net Change | Total Debt
----------|-----------|-------------|-------------|-------------
SP-001    | 5         | 2           | -3          | 15
SP-002    | 3         | 4           | +1          | 14
SP-003    | 2         | 3           | +1          | 13
```

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

- [Development Methodology](../bmad/development-methodology.md)
- [Quality Review Template](_template_QR.md)
- [Sprint Template](_template_SP.md)

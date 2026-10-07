---
baseline_commit: b04b635
experiment_refs:
  - id: E-013
    scope: "AC-001, AC-002, AC-003 — the bench is E-013's shipped instrument"
    status: APPROVED
---

# Story 1.1: Consistency bench runs in CI

Status: done

<!-- Methodology record: docs/development/stories/S-001.md (auto-filled) -->

> **Shipped example — not a live record.** This story and the
> `sprint-status.yaml` beside it are the plugin's reference sample of the
> Seçenek-A native output (the board lists 33 stories and ships no other story
> file, so it is a reference board, not a live chain). The records this sample
> points at — `S-001`, the `E-013`-era experiment, `QR-001`, `PR-001` — belonged
> to the 2026-09-16→18 dogfood round and were pruned by the `clean` commit
> (2026-09-26); the live round on this repo is `SP-001`…`SP-004` / `S-054`…`S-061`
> / `QR-013`…`QR-016`. The file name carries the `_` prefix for exactly that
> reason: `_iter_story_files` skips `_`-prefixed files (the same convention
> `_template_S.md` uses), so an example is never read as a story that reached
> `done` without a record chain.

- **Story key:** 1-1-bench-in-ci
- **Sprint:** SP-001 (`docs/development/SP-001.md`)
- **Priority:** High
- **Story points:** 2
- **Epic:** epic-1
- **Methodology record:** `docs/development/stories/S-001.md`
- **Readiness input:** `docs/development/IR-001.md`
- **Sprint status:** `docs/development/native/sprint-status.yaml`

## Story

As the maintainer of this repo,
I want the doc↔code consistency bench to run on every push inside the existing `plugin-health` job,
so that a live doc drifting from shipped behaviour fails the build instead of waiting for the next human audit.

## Acceptance Criteria

1. [AC-001] **Given** a checkout where `scripts/bench/bench_consistency.py` passes **When** the `plugin-health` CI job runs **Then** the bench executes in that job and its exit code decides the job's result
   - Experiment: E-013
   - Type: agent-verifiable
   - Measured: true
   - Verify: `sh scripts/check-plugin.sh` locally + inspect the `plugin-health` step list in `.github/workflows/ci.yml`

2. [AC-002] **Given** the bench as the CI step **When** any of its falsifiers is red **Then** the step fails — the full pytest suite stays in the bench, and `sync-hooks-json.py --check` still gates it
   - Experiment: E-013
   - Type: agent-verifiable
   - Measured: true
   - Verify: `python3 scripts/bench/bench_consistency.py` and read the falsifier lines of its output; `python3 -m pytest -q`

3. [AC-003] **Given** a live doc carrying a claim the tree no longer satisfies (e.g. a stale total-count) **When** the bench runs **Then** it exits non-zero and names the drifted file — the check is not a tautology that agrees with whatever it finds
   - Experiment: E-013
   - Type: agent-verifiable
   - Measured: true
   - Verify: seed one stale count into a live doc, run the bench, confirm the failure names it, then revert

## Technical Tasks

- [x] Task 1: Read `.github/workflows/ci.yml` and pick the insertion point in the `plugin-health` job — after the existing static checks (AC: AC-001)
  - [x] Subtask 1.1: Confirm the job's Python version and that no new dependency is needed (bench is stdlib-only) — imports: ast, pathlib, re, subprocess, sys, tomllib
  - [x] Subtask 1.2: Confirm the bench's working directory assumptions hold on `ubuntu-latest` — ROOT is derived from `__file__`, not cwd
- [x] Task 2: Add the bench step to the `plugin-health` job so its exit code gates the job (AC: AC-001)
- [x] Task 3: Measure the job's wall time with the bench in place and decide whether the suite falsifier needs the `--no-suite` escape hatch (AC: AC-002)
  - [x] Subtask 3.1: Record the measured job duration in SP-001's blocker B2 so the decision is written down, not assumed
  - [x] Subtask 3.2: If the budget is exceeded, add `--no-suite` and keep the suite in the `tests` job — not needed: bench ≈ 72 s locally, well inside the job budget
- [x] Task 4: Rehearse the failure path once: seed a stale total-count in a live doc, confirm the bench exits non-zero and names the file, then revert (AC: AC-003) — done: `130 skills` seeded into README → `FAIL count claims agree with the tree: README.md` → reverted → 44/44
- [x] Task 5: Update the CI section of `README.md` / `docs/USAGE-GUIDE.md` so the documented verification block matches what CI actually runs (AC: AC-001, AC: AC-003)
- [x] Task 6 (added during implementation): prototype a bench self-check pinning the CI wiring; REVERTED — Dev Notes forbid bench changes without their own E record (E-013 refuses a re-run). Decision recorded in S-001's Debug Log.

## Definition of Done

- [x] DoD-001: All acceptance criteria met (AC: AC-001, AC-002, AC-003)
  - Verify: `python3 scripts/bench/bench_consistency.py` (44/44) + the green `plugin-health` run
  - Evidence: AC table in S-001.md; bench output; CI run URL after the next push
- [x] DoD-002: The bench gates the job — a red bench makes CI red (AC: AC-001)
  - Verify: seed a failing check, observe the failure name the file, revert; the step's exit code gates the job
  - Evidence: rehearsal logged in S-001.md Debug Log; workflow diff
- [x] DoD-003: No regression: the full existing test suite still passes (AC: AC-002)
  - Verify: `python3 -m pytest -o addopts="" -q`
  - Evidence: 795 passed
- [x] DoD-004: No new runtime dependency and no network access in the bench
  - Verify: bench imports stay stdlib-only; job adds no install step
  - Evidence: workflow diff adds only the bench invocation
- [x] DoD-005: Docs match CI so a reader can reproduce every documented path (AC: AC-003)
  - Verify: `python3 scripts/bench/bench_consistency.py` documented-entry-point checks stay green
  - Evidence: bench 44/44; README + USAGE-GUIDE §17 list the bench step
- [x] DoD-006: Change reviewed and recorded (code review)
  - Verify: `docs/quality/QR-001.md` exists and references `1-1-bench-in-ci`
  - Evidence: QR record — pending at the review gate (next hop)

## Dev Notes

- The bench already exists (`scripts/bench/bench_consistency.py`, E-013). This story adds **no new checks** — it moves the instrument into the gate that already exists. S-003 is the story that extends coverage (to `custom/config.toml`'s hook comments).
- The bench is deliberately offline, stdlib-only and model-free; it shells out to pytest and to `sync-hooks-json.py --check` as falsifiers. Those two are the reason a CI run of the bench costs what a `tests` job costs — see blocker B2 in SP-001.
- Free zone: `scripts/bench/**` is a self-modification free zone in this repo, so a bench change is not write-gated by the guard. A change to the bench itself needs its own E record (E-013 is decided and refuses a re-run) — this story only calls it from CI, so it stays inside E-013.
- CI must not become the *only* place the bench runs: `sh scripts/check-plugin.sh` is the local equivalent, and the README's verification block lists both.

### Project Structure Notes

- Workflow: `.github/workflows/ci.yml` (jobs: `tests`, `plugin-health`)
- Bench: `scripts/bench/bench_consistency.py`
- Health checks: `scripts/check-plugin.sh`, `scripts/check-custom.sh`, `scripts/check-handoff.py`, `scripts/check-techdebt.sh`, `scripts/check-methodology.sh`
- Docs to keep in sync: `README.md` (verification block), `docs/USAGE-GUIDE.md` §17 ("What CI runs")

### References

- [Source: docs/experiments/E-013.md] — the bench's record (APPROVED + VERIFIED, n=44)
- [Source: docs/development/IR-001.md#Success criteria] — "the E-013 consistency bench runs on every push inside the existing `plugin-health` CI job"
- [Source: docs/development/SP-001.md#Blockers] — B2 (wall time) and B1 (free zone)
- [Source: docs/USAGE-GUIDE.md#§17 What CI runs] — the documented CI surface this story must keep true

## Dev Agent Record

### Agent Model Used

z-ai/glm-5.3-flash (Codebuff) — 2026-09-18

### Debug Log References

Implemented 2026-09-18. CI step added after the static checks; rehearsal + suite green. One decision recorded: a prototype bench self-check for the CI wiring was reverted (bench changes need their own E record) — see S-001.md Debug Log.

### Completion Notes List

- Bench step gates `plugin-health`; docs (README verification block, USAGE-GUIDE §17) updated to match CI.
- B2 measured: bench ≈ 72 s — `--no-suite` not needed.

### File List

- .github/workflows/ci.yml
- README.md
- docs/USAGE-GUIDE.md
- docs/development/stories/S-001.md
- docs/development/native/_example-1-1-bench-in-ci.md
- docs/development/SP-001.md (B2 measurement)

### Change Log

| Date | Version | Description |
|------|---------|-------------|
| 2026-09-18 | 1.0 | Story implemented: consistency bench runs in CI (`plugin-health`); status ready-for-dev → review |

## Quality Record (QR)

| DoD Item | Status | Evidence | Date |
|----------|--------|----------|------|
| DoD-001 | ✅ passed | AC table in S-001.md; bench output; CI run URL after the next push | 2026-09-18 |
| DoD-002 | ✅ passed | rehearsal logged in S-001.md Debug Log; workflow diff | 2026-09-18 |
| DoD-003 | ✅ passed | 795 passed | 2026-09-18 |
| DoD-004 | ✅ passed | workflow diff adds only the bench invocation | 2026-09-18 |
| DoD-005 | ✅ passed | bench 44/44; README + USAGE-GUIDE §17 list the bench step | 2026-09-18 |
| DoD-006 | ✅ passed | QR record — pending at the review gate (next hop) | 2026-09-18 |

### QR Summary
- **Total DoD Items**: 6
- **Passed**: 6
- **Failed**: 0
- **Pending**: 0
- **QR Record Path**: docs/quality/QR-001.md
- **PR Record Path**: docs/deployment/PR-001.md (READY)
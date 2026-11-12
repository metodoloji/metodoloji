# Architecture and failure boundaries

Metodoloji is a multi-runtime agent plugin. It is not a server and it is not
an operating-system sandbox. The runtime adapters expose calls to the shared
Python decision engine; the engine can only enforce policy on calls that reach
one of those registered interception points.

## Runtime request path

```text
Claude Code / OpenHands ── hooks manifest + shell dispatcher ─┐
OpenCode ───────────────── TypeScript bridge ────────────────┼─> Python hook engine
Hermes ─────────────────── in-process Python adapter ────────┘       │
                                                                      ├─ scope and record checks
                                                                      ├─ commit and deploy gates
                                                                      └─ report and context hooks
```

Adapters normalize host tool names and payloads. They must not reimplement
record, scope, commit, or deploy decisions. Runtime interception differences
and known gaps are listed in [runtime security coverage](runtime-security-coverage.md).

## Core code ownership

| Area | Owner | Responsibility |
|---|---|---|
| Hook dispatch | `hooks/engine/main.py`, `hooks/scripts/` | Input contract, runtime selection, hook output, and dispatch failure policy |
| Write scope | `hooks/engine/modules/guard.py`, `scope_gate.py`, `story_records.py` | Record verification, path/content scope, story metadata |
| Commit/deploy | `quality_gate.py`, `deploy_gate.py`, `record_chain_gate.py` | Chain and release readiness checks |
| Blackboard | `blackboard.py` plus `blackboard_*` modules | Cross-session state, handoffs, canvas, diagnostics, and archive operations |
| Workflow kernel | `bmad/workflow/engine.py`, `spec.py`, `evidence.py`, `store.py` | Declared stage order, evidence checks, durable run state |
| Runtime bridges | `opencode-plugin/`, `hermes_adapter.py`, root `__init__.py` | Host API mapping and engine invocation |

The large guard and blackboard modules are stable compatibility surfaces with
domain helpers already extracted into sibling modules. New behavior belongs in
the owning domain module; preserve compatibility imports only where existing
callers require them. Split a surface further when it has an independently
testable responsibility, rather than moving code based only on file length.

## Failure policy

| Operation | Failure behavior |
|---|---|
| Guard/pre hook | Fail closed: no usable decision means deny |
| Quality/deploy hook | Follow the configured hard/soft mode; hard denies, soft allows with a warning |
| Audit, session-start, stop | Fail open; these hooks report or add context and cannot block the action/session |
| Workflow state/spec mutation | Lock or state-write failure refuses the mutation; stale read-modify-write is rejected |
| Workflow event log | State remains authoritative; an event-log failure is returned as a warning with the successful mutation |
| Blackboard notification from workflow | Advisory only; workflow proceeds and the notification failure is written to stderr |
| Blackboard mutation (JSON backend) | Supported-lock failure raises `BoardError`; reads/reporting can degrade to safe defaults |
| Blackboard mutation (SQLite backend) | The transaction rolls back on any exception; a lock/contention failure surfaces as `BoardError`, never as a partial write |

Workflow state is updated under a thread mutex and an operating-system advisory
lock. A mutation also checks the
hash of the exact state bytes it read before replacing the file, so old state
formats without `updated_at` get the same protection and two agents cannot
silently overwrite one another's stage transition. State replacement uses a
unique same-directory temporary file and atomic rename. The append-only event log is
secondary evidence: if it cannot be updated after the state commit, the caller
receives a warning rather than a false failure that could encourage a duplicate
retry.

## Record kinds and rigor tiers

The record chain has two distinct claim types and three rigor tiers:

- **Experiment** (`Record Kind: experiment`, default) — a stochastic claim whose
  result could have come out otherwise. The gate applies sampling statistics
  (Wilson bounds, McNemar, Wilcoxon) and an exact-zero claim needs a `Census`.
- **Contract** (`Record Kind: contract`) — a deterministic test over a closed,
  enumerated input set. It must name the population in `Census`, claim 100%
  (`== 1.0` or `>= 1.0`), ship per-case samples, and pass every enumerated case.
  The gate writes a contract note instead of a sampling interval. A unit or
  regression suite is a contract; calling it an experiment is methodological
  theatre.

Stories declare `- **Rigor Tier:** 1|2|3`:

| Tier | Use | Required records |
|---|---|---|
| 1 | trivial / docs / hotfix | QR + regression evidence |
| 2 | feature / refactor | story record + AC metadata + unit tests; QR |
| 3 | probabilistic / security / core / ML | full `E → IR → SP → S → QR → PR` |

The tier is read from the story file; absent or malformed values fall back to
Tier 3, so no legacy story loses a gate. Tier 1/2 filter the upstream IR/SP/PR
checks only; the QR and the code guard are never relaxed. The commit/deploy
gates print the tier when they deny, so the claimed fast path stays visible.

New drafts are produced by `scripts/create-record.py`, which validates the
kind's schema before and after writing and stamps `metodoloji.record/v1`.
`run_experiment.py` refuses an experiment/contract draft dated on or after
2026-11-10 that lacks the attestation, so a hand-edited draft cannot reach a
decision; older records stay readable. `scripts/migrate-contract-records.py`
reports (and with `--apply`, writes) the `Record Kind` of the legacy corpus
without touching any gate-written line.

## Blackboard backends

The board has two interchangeable storage backends, selected by
`[hooks] blackboard_backend`:

- **SQLite WAL (shipped default).** One database
  (`.metodoloji/blackboard.sqlite`) holds the board JSON and the event stream.
  Every read-modify-write runs inside one `BEGIN IMMEDIATE` transaction with
  `journal_mode=WAL`, so a crash rolls the mutation back instead of leaving the
  snapshot and audit log disagreeing. The first read in SQLite mode imports an
  existing JSON snapshot and event log once; the JSON files remain on disk as
  the migration source and are not deleted.
- **JSON (legacy, opt-in).** One snapshot (`.metodoloji/blackboard.json`) plus
  an append-only event log and an advisory lock file. Mutations use a lock,
  atomic temp-file replacement, and bounded event-log truncation. Select it with
  `blackboard_backend = "json"`.

Diagnostics, rotation, and the live-relay check read through the active backend,
so a SQLite board is never diagnosed from a stale JSON file.

`METODOLOJI_BLACKBOARD_BACKEND=json|sqlite` overrides the file for one process.

## OpenCode distribution

The npm package installs the TypeScript bridge. It still needs a methodology
checkout containing `hooks/engine/`; root discovery is explicit in the
[OpenCode installation guide](OPENCODE.md). The bridge and lockfile versions
are owned by the repository version bump, and runtime dependencies are pinned
to exact versions so installs without a lockfile do not float to a new API.

## Validation boundaries

Pull request CI runs the Python checks and OpenCode bridge checks. Python unit
tests cover the Hermes adapter contract, but an installed Hermes host is not
part of CI. Runtime API changes therefore require updating the adapter, its
contract coverage, the runtime matrix, and the matching installation guide.

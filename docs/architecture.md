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
| Blackboard mutation | Supported-lock failure raises `BoardError`; reads/reporting can degrade to safe defaults |

Workflow state is updated under a thread mutex and an operating-system advisory
lock. A mutation also checks the
hash of the exact state bytes it read before replacing the file, so old state
formats without `updated_at` get the same protection and two agents cannot
silently overwrite one another's stage transition. State replacement uses a
unique same-directory temporary file and atomic rename. The append-only event log is
secondary evidence: if it cannot be updated after the state commit, the caller
receives a warning rather than a false failure that could encourage a duplicate
retry.

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

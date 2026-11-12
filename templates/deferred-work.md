# Deferred Work

Work knowingly deferred from a review, a quick-dev run, or an autonomous dev
loop. The ledger is append-only and lives at
`{implementation_artifacts}/deferred-work.md` (default
`docs/development/native/deferred-work.md`).

- One entry per deferred item, numbered `DW-1`, `DW-2`, … — never renumber.
- `status: open` until closed, then `status: done <date>` — never delete an entry.
- Before appending, scan for the same issue (same location and substance). If it
  exists, add a `seen-again: <date> (<origin>)` line instead of a duplicate.
- Canonical format, severity vocabulary and sweep annotations:
  `{metodoloji-root}/skills/bmad-loop-sweep/deferred-work-format.md`.
- Writers: `bmad-code-review`, `bmad-quick-dev`, `bmad-dev-auto`. Triage/close: `bmad-loop-sweep` (bmad-loop) or the maintainer.
- Nothing blocks by default: the orient digest counts open entries, the audit
  parses the format, and the commit gate names open entries as an advisory.

The entry shape (replace the placeholder, do not leave it in the ledger):

```markdown
### DW-1: <one-line title>

origin: <workflow + artifact + date, e.g. "code review of S-011, 2026-11-03">
location: <file:line or component, or "n/a" for deferred goals>
severity: <critical | high | medium | low>
reason: <why this was deferred rather than done now, one or two sentences>
status: open
```

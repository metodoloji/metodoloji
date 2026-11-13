# Deferred Work Format

Canonical entry format for `{implementation_artifacts}/deferred-work.md`. The
ledger is written by the three skills that can knowingly defer a finding or a
goal — `bmad-code-review`, `bmad-quick-dev`, `bmad-dev-auto` — and closed by
whoever fixes the item (the maintainer, or the next story that pays it). The
file is append-only: never rewrite or delete existing entries.

This file is the format's single owner. The manifesto
(`{metodoloji-root}/docs/bmad/development-methodology.md` §6.4) and the ledger
template (`{metodoloji-root}/templates/deferred-work.md`) point here rather
than restating it.

## Before appending: dedupe check

Scan the existing file for an entry describing the same issue or goal (same
location and same substance, even if worded differently). If one exists, do
NOT append a duplicate — add a `seen-again:` line to the existing entry
instead:

```markdown
seen-again: 2026-06-12 (code review of spec-3-3-export.md)
```

## Entry format

Number entries sequentially (`DW-1`, `DW-2`, …) by scanning the file for the
highest existing number. One entry per deferred item:

```markdown
### DW-<seq>: <one-line title>

origin: <workflow + artifact + date, e.g. "code review of spec-3-2-digest.md, 2026-06-12">
location: <file:line or component, or "n/a" for deferred goals>
severity: <critical | high | medium | low — how much it matters if never done>
reason: <why this was deferred rather than done now, one or two sentences>
status: open
```

`severity:` is optional — entries written before this field existed have none
and that is fine; readers must treat a missing or unrecognized value as
"unspecified". Use `critical` for correctness/security issues, `high` for
likely user-visible problems, `medium` for quality and robustness gaps, `low`
for polish and nice-to-haves.

When a deferred item is later completed, set its `status:` to `done` with the
date (e.g. `status: done 2026-06-20`) — do not delete the entry.

## Closing annotations

Two optional field lines may be added to an existing entry, both directly after
`status:`:

```markdown
resolution: <one line: what was built or why the entry was closed>
decision: <date> <chosen option label> — <detail>
```

- `resolution:` accompanies every close (`status: done <date>`): one line
  saying what was built or why the entry was closed.
- `decision:` records a human's choice about an entry. It does not by itself
  change `status:` — a `keep-open` decision leaves the entry open.

## Legacy freeform content

A ledger written before this format existed may carry flat `- ` bullets under
`## Deferred from: …` headings. Those are valid history, not errors: readers
count them as `legacy/unparsed`, and they are migrated to the entry shape above
when the item is next touched — never bulk-rewritten.

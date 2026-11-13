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

`--add` performs this check mechanically (normalized title match) and appends
the `seen-again:` line itself, so a writer that uses the CLI cannot skip it.

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

Numbers are never reused: two `### DW-3:` blocks mean two different findings
share one id and every cross-reference is ambiguous. `--check` reports it.

## The mechanical half: `deferred-work.py`

The format has one owner and one tool. Everything above can be done by hand,
but three operations must not depend on an LLM remembering a shape:

```bash
# validate: exit 1 on a malformed entry or a duplicate number
python3 {metodoloji-root}/skills/bmad-code-review/scripts/deferred-work.py --check

# convert legacy sections into canonical entries (dry run; --write applies)
python3 {metodoloji-root}/skills/bmad-code-review/scripts/deferred-work.py --migrate --write

# append one entry with the next free number and the dedupe check
python3 {metodoloji-root}/skills/bmad-code-review/scripts/deferred-work.py \
  --add --title "<one line>" --origin "<workflow + artifact + date>" \
  [--location <file:line>] [--severity <level>] [--reason "<why>"]
```

`--check` reads the same parser every other surface reads
(`hooks/engine/modules/state.py`), so the tool and the digest can never
disagree about what the ledger contains.

## Migrating a legacy ledger

A ledger written before this format existed carries flat `- ` bullets under
`## Deferred from: <origin>` headings. Those are valid history, not errors, and
they are **outstanding work**: every surface counts them as open.

`--migrate` converts each bullet into one canonical entry, deterministically:

- `origin:` is the section heading, including its date when present.
- `location:` is the first backticked `path:line` in the bullet, else `n/a`.
- the title is the bullet's first short clause (before a `;`, else its first
  sentence, else a word-boundary cut), and `reason:` carries the rest of the
  bullet **verbatim** — no text is dropped.
- `severity:` is omitted (unspecified), because the original never stated one.
- the section heading becomes `## Migrated from: <origin>` so the entry keeps
  its provenance.

The conversion is idempotent: a second run finds no `## Deferred from:`
heading and changes nothing. Dry-run by default; `--write` applies.

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

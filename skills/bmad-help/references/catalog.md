# Catalog interpretation (`bmad/_config/bmad-help.csv`)

Reference for `bmad-help`. Read this only when routing needs the catalog's
semantics — the activation body stays short on purpose (activation content is
truncated by the harness past roughly 2 KB, and a truncated body is what made a
real session re-read `SKILL.md` seven times).

## Format

```
module,skill,display-name,menu-code,description,action,args,phase,preceded-by,followed-by,required,output-location,outputs
```

**Special row type:** rows with `_meta` in the `skill` column are module
documentation entries, not skills. They carry a documentation URL in
`output-location`. Skip them when iterating skills; use them to answer
module-level questions.

## Phases

- `anytime` — available regardless of workflow state
- Numbered phases (`1-analysis`, `2-planning`, …) flow in order; naming varies by module

## Sequencing

Soft suggestions, not hard gates (see `required` for gating):

- `preceded-by` — skills that should ideally complete before this one
- `followed-by` — skills that should ideally run after this one
- Format: `skill-name` for single-action skills, `skill-name:action` for multi-action skills

## Required gates

- `required=true` items must complete before the user can meaningfully proceed to later phases
- A phase with no required items is entirely optional — recommend it but be clear about what is actually required next
- **`required` is a PHASE gate, never a same-phase prerequisite.** Two phase-4
rows both wearing `required=true` (SP and CS) do NOT make one the other's
precondition. Say "SP is required to close phase 4", never "CS requires SP" —
the fikir session (2026-10-01) mis-read it the second way and told the user a
dev hand-off was blocked when it was not.
- **CS's SP-less path is designed, not exceptional.** When sprint-status.yaml
is absent, create-story offers three options (run sprint-planning; name a
specific epic-story number; give a story-docs path) — options 2 and 3 are
first-class. A story created that way is chain-valid; the quality gate only
fires for stories that REFERENCE an SP and lack the SP record.
- **SP's two concrete forms** (the digest names the split when it matters):
the gate record `docs/development/SP-*.md` (written by the sprint-planning
close when scope is first cut; what quality/deploy gates and the record
inventory read) and the board heartbeat `SP-<sequence>` (the record's own id,
what surfaces on `methodology.last_sp`). A heartbeat without a record file means
a sprint REFRESH happened, not that planning was skipped.
- **A record covers a story only when it DECLARES it.** The link is the
declaration — a `| Story | <key or path> |` row (what `create-qr-record.py`
writes), a `- **Story:**` / `- **Stories:**` field, or a title naming the story.
A story key appearing anywhere else in the body is not a link: the guard used to
accept one, and the shipped QR-015 mentioned `1-1-bench-in-ci` only inside a
regression note about a moved test path, so that mention alone stood in for the
story's quality record (2026-10-01 audit). Same for the id links: `SP-2026-01-15`
never matches `SP-2026-11-02`, and "SP-002 opens next" in sprint 1's record does
not stand in for sprint 2's record.
- **A generated record is covered through its native story.** The QR is written
against the native story (the work item), and a generated methodology record
names that story in its `| Native Story |` field — the same hop
`scripts/sync-story-qr.py` resolves its QR with. So the QR that declares native
`S-056` covers generated `S-057`; the hop follows a declaration, so a record that
merely mentions the native story still covers nothing (shipped QR-014/015/016 →
S-057/059/061, closed 2026-10-01).
- **An SP record names the IR it plans from** (`- **Readiness input:** IR-001`).
The template carries the field: a sprint record that names no IR record fails the
story chain check, and one written without it cannot be repaired by prose.

## The catalog is a menu, not the skill inventory

The catalog models the **story cycle** — `SP → CS → VS → DS → CR → ER`
(plus the planning/readiness stages IR and the analysis agents). It is not an
exhaustive list of installed skills: agent skills carry no rows, no WDS workflow
does, and neither do the three methodology **record-machinery** producers —
`bmad-research-experiment` (E), `bmad-quality-record` (QR) and
`bmad-production-readiness` (PR). That is deliberate, not a gap to "fix" by
adding rows: those three are driven by the chain (they own or answer
`handoff.bmad-quality-record` batons) and by the digest's record inventory, and
they stay reachable by their own triggers ("create quality record", "prepare
for production"). Their records surface in the inventory line, not as menu
steps. Do not report them as "missing from help" — the story-cycle steps are
the menu; the record machinery is the relay underneath it.

## Completion detection

- Search resolved output paths for `outputs` patterns
- Fuzzy-match found files to catalog rows using substring matching: check if the filename contains the skill name or output pattern. Disambiguate by preferring exact matches, then shortest-path matches.
- User may state completion explicitly, or it may be evident from the conversation
- `output-location` values are templated defaults (`{project-root}/…`, `{output_folder}/…`, or a config key such as `planning_artifacts`). Resolve them through the merged config; when a skill's own `customize.toml` output path disagrees with the catalog row, **the skill's path wins** — it is what actually ran.

## Descriptions

Descriptions carry routing context — some contain cycle info and alternate paths
(e.g., "back to DS if fixes needed"). Read them as navigation hints, not just
display text.

## Intent-aware routing

The active blackboard intent steers recommendations (the restored
memlog-frontmatter capability, now read from the board instead of `.memlog.md`):

- **`purpose` / `topic` / `goal` / `idea`** names the subject — if a catalog row's description or skill name matches it, rank that skill first. E.g. a `purpose: PRD for billing` bridge value points straight at `bmad-prd`.
- **`scope`** names the touched area — when multiple skills fit, prefer the one whose `output-location` or description overlaps the scope.
- **`status`** tells the phase: `active` / `in-progress` → the user is mid-work, recommend continuation skills (`preceded-by` already satisfied → `followed-by`); `complete` → the work is done, recommend what is next in the chain or the wrap-up skill. A `status: complete` while a key is **hot** is stale — the live run is mid-work (orient names this; the run marks itself with `write --key status --value in-progress`).
- Intent is a soft signal, never a gate: if the user's question contradicts the bridge, the question wins — but mention the mismatch ("your board says X; you're asking about Y — is this a new task?").

## Menu codes are not globally unique

`bmad-help.csv` assembles every installed module, and modules reuse codes across
modules: `SP` is `BMad Method/bmad-sprint-planning`, `Game Dev Studio/gds-sprint-planning`
and the WDS conceptual-specs row; `CU` is `bmad-ux` and `gds-ux`; `CS` is
`bmad-create-story`, `gds-create-story` and the WDS conceptual-sketching row;
`DS`, `CR`, `CE`, `IR`, `PRD`, `VD`, `AT` and others collide too. The digest lists
the ambiguous ones.

`Core/bmad-spec` used to carry `SP` as well — the code was changed to `SPEC`,
because `SP` is this methodology's own Sprint-Plan record prefix and a Spec skill
wearing it is a routing hazard, not just an ambiguity. Keep new codes clear of
the chain's record prefixes (`E`, `IR`, `SP`, `S`, `QR`, `PR`).

When a code is ambiguous, never print the bare code: qualify it
(`[SP] BMad Method — Sprint Planning` vs `[SP] Core — Spec`) and prefer the
skill name for the invocation, because the user's own module decides which one
they mean. Never infer which module the user wants from the code alone.

## Waiting batons outrank the catalog

A recommendation engine that ignores live state recommends stale work. The
digest's `handoff` section (and `blackboard.py handoffs --project-root {project-root}`,
read-only — never consume on a peek) lists every waiting signal: an upstream run
finished and nobody picked up the baton. The skill owning the oldest waiting
baton is usually the true "what to do next", and it should surface at the top of
the recommendations with that reason named. When the board and the catalog
disagree, say both: the board is live state, the catalog is the map.

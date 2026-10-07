---
name: bmad-help
description: 'Analyzes current state and user query to answer BMad questions or recommend the next skill(s) to use. Use when user asks for help, bmad help, what to do next, or what to start with in BMad.'
triggers: ["bmad-help", "/bmad-help", "help"]
---

# BMad Help

## Conventions

- `{metodoloji-root}` = plugin root: **(1)** the SessionStart line
  `METODOLOJI active (plugin: PATH)` (verbatim — never search the filesystem);
  **(2)** `$CLAUDE_PLUGIN_ROOT` / `$METODOLOJI_PLUGIN_ROOT` if set;
  **(3)** fixed checkout `~/.claude/plugins/marketplaces/metodoloji` or
  versioned cache `~/.claude/plugins/cache/metodoloji/metodoloji/*`
  (newest first), else `~/.openhands/plugins/installed/metodoloji`.
  Never `find`/`ls` around for it.
- `{project-root}` = cwd. `{communication_language}` comes from core config.
- One command per tool call — never chain `echo "---SEP---"` dumps (their outputs
  interleave and you re-run commands you already had).

## Data Sources — start with the digest

```sh
python3 {metodoloji-root}/bmad/scripts/orient.py --project-root {project-root}
```

One read-only call returns everything routing needs: both roots, core config and
module output paths, the record inventory (E/IR/SP/S/QR/PR), the board (hot key,
focus `status`/`scope`, chain progress, alerts, and a `STALE` flag when a live run
still claims `complete` — plus a `STALE VERDICT` flag when a negative gate verdict
(`IR`/`PR`) predates the artifacts it judged), waiting hand-offs with age and sender, skeleton +
gate-key state, the catalog summary with its **ambiguous menu codes**, the
**docs overview** (subdir file counts + largest text files with sizes), and the
**MCP server inventory** (which servers this session can reach, from the harness
configs — read-only, never spawned). Add `--json` for tooling. Run it first; never
re-run it "for clean output" — it is small by construction.

## Anti-loop contract — read this before any other command

**Do not reload this skill.** If `bmad-help` is already active in this turn
(a digest ran, a recommendation was given), continue from the state you have —
never load this skill a second time in one turn "to be sure".
A second load is the loop, not confirmation.

## Skill-handoff contract — how to move to the recommended skill

A skill is instructions, not a tool call. When this turn routes to another
skill, NEVER invoke the Skill tool programmatically to "chain" into it:
a Skill invocation whose `skill` argument is not an exact known literal
(a variable, a guess, an empty/unknown value) fails harness validation
(`The parameter 'skill' ... provided as 'unknown'`) and kills the turn.
Instead, hand off one of these two ways — no exceptions:

1. **Continue inline (preferred).** Read the target skill's `SKILL.md`
   workflow with your native file-read tool and follow its steps directly
   in THIS turn, starting at its first question. No Skill tool call happens.
2. **Hand the slash command to the user.** Name the exact literal invocation
   (e.g. `/metodoloji:bmad-research-experiment`) and stop — the USER's next
   message triggers the load, never your tool call.

A real OpenHands session (2026-09-25, LIMX: 652 tool blocks, 65% hook echoes)
burned its run re-deriving state the digest already held: ~70 manual
`ls`/`find`/`wc`/`git status` probes, 10× re-reads of the same README, 6×
re-plans of one task list, 4× `orient.py` runs. Every item below is a
mechanical rule, not advice:

1. **Digest once.** After `orient.py`, NEVER run `ls`/`find`/`wc`/`git status`/
   `git log` to "confirm" — the digest's `artifacts` + `records` + `docs` +
   `board` sections ARE the confirmation. Re-running them is the loop.
2. **One command per tool call.** Never chain `echo "==="` dumps (`ls && git
   log && find …`): outputs interleave and you re-run commands you had.
3. **Windowed reads only.** The digest's `docs.largest` gives sizes upfront:
   read large files with offset/limit, never re-`cat` a file you read before.
   A capped/truncated output is complete — never retry the same read with a
   different shell command (`cat` → `sed -n` → `PAGER=cat head`); that retry
   loop re-read the same methodology doc 4× in a real session.
4. **Resume, don't restart.** On "kaldığımız yerden devam / continue": re-read
   the digest + board focus + waiting hand-offs, then continue the open work.
   NEVER restart with "let me explore the project structure".
5. **Out-of-scope paths.** If the user names a non-record tree (`docs/arge`,
   `docs/design`, …) and the record inventory is empty, say so explicitly —
   route from the digest's `docs` section, do NOT force BMad record routing
   onto research content, and do NOT hunt `memory/` or `.metodoloji/` for
   state the digest already reports.
6. **State lives in digest + board, nowhere else.** `.openhands/memory/`,
   `MEMORY.md` and daily-log paths are NOT metodoloji surfaces (zero code or
   skill references) — never probe them with `cat`/`ls`/`find`, and never
   `tail` the hook-audit log to "see state" (it is the hooks' own trail, not
   project state). Memory writes go through the `memory` skill; reads come
   from the digest.
7. **A flagged verdict is stale — relay it, don't re-derive it.** When the
   digest flags `STALE VERDICT (IR|PR)` — a negative gate verdict whose input
   artifacts changed after it — that flag *is* the finding. Name the superseding
   artifact and the re-run from the digest (`bmad-check-implementation-readiness`
   / `bmad-production-readiness`) and never open the gate report or the record to
   re-derive the caveat by hand: reading the readiness report to decide whether
   an old `NOT READY` still holds is the loop (graph-engineering-arge,
   2026-09-30 — the PRD landed 30m after the verdict and the digest already knew).
   Symmetrically, a verdict the digest does **not** flag is current as of its
   stamp — report it as the gate's live state, not as "probably outdated".

Then, only if a specific field is still missing:

- **Config**: `python3 {metodoloji-root}/bmad/scripts/resolve_config.py --project-root {project-root} --key <dotted.path>` (repeatable). **Never a module dump and never the full dump** — the full one arrives truncated, and re-running it is the loop the digest exists to end.
- **Waiting batons**: the digest's `handoff` section; for the raw list, `blackboard.py handoffs --project-root {project-root}` (read-only peek — consuming is the receiving skill's step, not help's).
- **Catalog**: `{metodoloji-root}/bmad/_config/bmad-help.csv` — read it only for rows the digest's summary cannot answer, and read `references/catalog.md` for the column, phase, sequencing, completion-detection and menu-code semantics.
- **Module docs**: `_meta` rows carry a doc URL/path per module; fetch those to answer general questions.

## Purpose

Help the user understand where they are in their BMad workflow and what to do next, and answer broader questions that can be grounded in remote sources such as module documentation.

## Desired Outcomes

When this skill completes, the user should:

1. **Know where they are** — which module and phase they are in, what is already completed
2. **Know what to do next** — the next recommended and/or required step, with clear reasoning
3. **Know how to invoke it** — skill name, menu code, action context, and any args that shortcut the conversation
4. **Get offered a quick start** — when a single skill is the clear next step, offer to run it right now rather than just listing it (name its slash command; the actual start follows the handoff contract above)
5. **Feel oriented, not overwhelmed** — surface only what is relevant to their position; never dump the catalog
6. **Get answers to general questions** — when the question does not map to a specific skill, ground the answer in the module's registered documentation
7. **Route to the tools that exist** — the digest names the MCP servers this session can reach; prefer them for the work they serve (docs lookup, repo search, browser, issue trackers) and say so when recommending a step they would serve. When none are configured and the coming work would clearly benefit from one, offer to add it (project `.mcp.json` or the harness's user config) — an offer, never a requirement

## Methodology

Bound to `{metodoloji-root}/docs/bmad/research-methodology.md` — the qualitative (B/C/D) and quantitative (Mode A) routes: when a recommendation sounds like a rule, it is a gate from this manifesto, not a preference.
Also bound to `{metodoloji-root}/docs/bmad/development-methodology.md` — the E → IR → SP → S → QR → PR record chain: always name the record the next step creates (or the gate it satisfies) instead of improvising an order.
Routing a user to code is not code-writing permission — code always requires Mode A mechanical approval (`/metodoloji:verify` + the guard hook). Fabricated evidence/measurements are fraud.

## Catalog reference (moved out of the activation body)

The CSV format, phases, sequencing, completion detection, intent-aware routing,
menu-code collisions and the waiting-baton precedence live in
`references/catalog.md` under this skill — deliberately out of the body, because
activation content is truncated past roughly 2 KB and a truncated body is what
made a real session re-read this file seven times. Load it when routing needs
those semantics.

## Response Format

For each recommended item, present:

- `[menu-code]` **Display name** — e.g., "[PRD] Create Edit and Review PRD"; qualify the module when the code is ambiguous (`references/catalog.md` lists the colliding codes)
- Skill name in backticks — e.g., `bmad-prd`
- For multi-action skills: action invocation context — e.g., "tech-writer lets create a mermaid diagram!"
- Description if present in CSV; otherwise your existing knowledge of the skill suffices
- Args if available

**Ordering**: waiting batons first (live state), then optional items, then the next required item. Make it clear which is which. A `STALE VERDICT` line leads the chain summary: it changes what "required" means (a stale `IR: NOT READY` does not rule epics out — it asks for a re-run), so report it before the required list instead of burying it.

**Naming the chain**: the digest's chain line carries each stage's actual run key
(`IR-2026-09-30`, `E-001`, `SP-…`) — quote that key. The record inventory may
show a different file id for the same stage (`IR-001.md` is the record; the run
key is the board identity). Never merge the two into one label: an
`IR-001 … NOT READY` hybrid is a fabricated identifier (graph-engineering-arge,
2026-09-30 — the record said `INCOMPLETE`, the run key said `NOT READY`).
When the digest is unavailable, say which data source failed instead of
reconstructing the chain from filenames.

## Improvement-request routing (generic "iyileştir / improve / refactor")

A bare improvement request ("projeyi iyileştir", "improve the project",
"make it better") with an empty record inventory is NOT a help-loop case —
it is a brownfield experiment start:

- Route to `bmad-research-experiment` (KILAVUZ/GUIDE #5 P3 brownfield path): ask the
  ONE scoping question first (which module + hypothesis threshold), then open
  E-001. Do not end the turn with "no records, nothing to do".
  "Route to" means option 1 of the handoff contract above: read that skill's
  `SKILL.md` and follow it inline in this turn — never a Skill tool call.
- The "fresh context window" below is a recommendation for long experiments,
  not a gate: same-window handoff is fine — say "fresh window recommended,
  continuing here works too" and proceed to the first question instead of
  forcing the user to restart.

## Constraints

- Present all output in `{communication_language}`
- A help trigger with trailing task text (`/bmad-help docs/arge …`) is TWO
  requests: answer the routing question first, then OFFER the task — never
  auto-start it. The trailing path may be out-of-scope (see rule 5 above).
  (This no-auto-start rule binds help-triggered turns only. When the USER
  directly asked for work — e.g. "projeyi iyileştir" — and help routed it to
  a skill, proceed to that skill's first question after the offer instead of
  stalling (inline per the handoff contract — never a Skill tool call);
  see Improvement-request routing above.)
- Running each skill in a **fresh context window** is recommended for long
  experiments, not required — never force the user to restart to get started
- Match the user's tone — conversational when they're casual, structured when they want specifics
- If the active module is ambiguous, retrieve all `_meta` rows from the catalog to find module documentation URLs and use them to help answer the user's question
- Never call a step complete because a file "probably" exists: cite the digest's record inventory or the resolved path
- Report only the facts the digest and the catalog give you; if a data source was unavailable, say which instead of guessing
# AGENT-GUIDE.md — Using metodoloji Entirely Through Prompts

> **Turkish:** [AGENT-GUIDE-TR.md](AGENT-GUIDE-TR.md)
>
> **What this file is.** A prompt-driven usage guide for the `metodoloji` plugin (v0.1.0, dual-runtime: OpenHands + Claude Code, BMad Method compatible). You never run a command yourself, never open a file by hand, never touch a key. You **send prompts**; the agent does the rest and reports back in a vocabulary you can read.
> **How to use it.** Find your situation in the [prompt catalogue](#9-prompt-catalogue-index) or the [combination matrix](#6-combination-matrix--project-state--goal--prompt), copy the prompt, paste it, then follow the follow-ups listed under it.
> **Language note.** Prompts are written in English because commands, record names and field labels are English (the gate parses them). You can phrase the same intent in your own language — the agent maps it to the same action.
> **Licence:** MIT (`LICENSE`). Built on BMAD-METHOD (BMad Code, MIT); see the `ATTRIBUTION` section at the end of `LICENSE`.

---

## Contents

1. [How Prompt-Driven Operation Works](#1-how-prompt-driven-operation-works)
2. [The Session Bootstrap Prompt (paste once per session)](#2-the-session-bootstrap-prompt-paste-once-per-session)
3. [Setup Prompts — by Project Type](#3-setup-prompts--by-project-type)
4. [Build Prompts — Every Delivery Scenario](#4-build-prompts--every-delivery-scenario)
5. [Maintenance Prompts — Audit, Tighten, Update, Recover](#5-maintenance-prompts--audit-tighten-update-recover)
6. [Combination Matrix — Project State × Goal → Prompt](#6-combination-matrix--project-state--goal--prompt)
7. [Reading the Answers — What the Agent's Reply Means](#7-reading-the-answers--what-the-agents-reply-means)
8. [Troubleshooting Prompts — Symptom → Prompt to Send](#8-troubleshooting-prompts--symptom--prompt-to-send)
9. [Prompt Catalogue Index](#9-prompt-catalogue-index)
10. [Anti-Patterns — Prompts That Fail, and the Rewrite](#10-anti-patterns--prompts-that-fail-and-the-rewrite)

---

## 1. How Prompt-Driven Operation Works

### 1.1 The division of labour

| You do | The agent does |
|---|---|
| Describe the goal in a prompt | Pick the skills, plan, open records |
| Paste the slash command or the scenario prompt | Execute it and report the result |
| Ask "why was it blocked?" | Read the gate, explain the exact reason |
| Decide when something is good enough | Verify every claim mechanically before saying "done" |
| **Never:** run shell commands, edit records, read/print the gate key | Run gates, write records, enforce scope, run the checks |

### 1.2 Two kinds of prompt

1. **Slash prompts** — four fixed commands. Say them verbatim; they are the whole setup surface:
   - `/metodoloji:init` — install the `docs/` record skeleton in your project (once; later calls are no-ops)
   - `/metodoloji:gate-setup` — create the machine-local gate key (once per machine; never printed, never shared)
   - `/metodoloji:verify` — check one experiment record: `VERIFIED` / `FORGED` / `REJECTED` / `ADVISORY-BLOCK`
   - `/metodoloji:audit` — full health report: plugin integrity, bridge wiring, record-chain gaps, gate modes
2. **Free-form prompts** — natural language. The template that always works:

```
<what you want> + <where it lives> + <what "done" means>
```

Example: `Add JWT authentication under src/auth/. Done means tests pass and a QR record exists before commit.`

### 1.3 The chain you are driving (you prompt the next link, the agent proves it)

```
E (experiment, measured by the gate) → IR (ready?) → SP (sprint plan) → S (story)
  → code inside the approved scope → QR (quality) → commit → PR (production) → deploy
```

Code requires permission: an experiment record that the gate itself measured and signed. That is why a prompt that skips straight to "just write the code" comes back with a gate answer — see [#10](#10-anti-patterns--prompts-that-fail-and-the-rewrite).

### 1.4 Status vocabulary you must be able to read

| You see | Meaning | What you say next |
|---|---|---|
| `VERIFIED` | the experiment is real and its scope is open | "go ahead, implement it" |
| `APPROVED` | the gate accepted the measurement | continue the chain |
| `REJECTED` | the measurement failed the threshold | "revise the hypothesis and open a new experiment" |
| `ADVISORY-BLOCK` | token is real but code stays closed (sample too small / metric mismatch) | "bigger sample, matching metric, re-measure in a new record" |
| `FORGED` | token does not match the record — hand edit, or signed on another machine | "regenerate it" (never "fix the token") |
| `DENY (hard)` | the mode blocks it outright | complete the missing record |
| `warn + pass (soft)` | the mode let it through with `methodology_warnings` | fix it anyway; hard mode would deny |
| `report-only` (Stop hook) | the session summary — never a block | ignore it as an obstacle; read it as a summary |

The Stop hook is **report-only**: it summarises the session (in-progress stories, this window's writes, a pending handoff) and never blocks anything.

### 1.5 What a good reply looks like

Every answer from the agent should carry four things; if one is missing, prompt for it:

1. **What was done** — with record ids (`E-045`, `S-027`, `QR-028`), not vague verbs.
2. **What the gate said** — `APPROVED`/`VERIFIED`/… **and the mode** (warned vs blocked).
3. **The next step** — the next link in the chain, or the input it needs from you.
4. **What is still open** — soft-mode gaps, pending handoffs, `NEEDS ATTENTION` items.

Follow-up prompt that forces all four: `Give me the four-line report: what changed, what the gate said, next step, what's still open.`

---

## 2. The Session Bootstrap Prompt (paste once per session)

A single self-contained prompt that puts the agent in the right posture before any work. Paste it at the start of a session (or make it your project's standing instruction).

```
You are operating a project that has the metodoloji plugin installed
(OpenHands + Claude Code, v0.1.0). Work through prompts only — I will not
run commands or edit files myself.

Operating rules you must follow:
1. Writing code requires an approved, gate-measured experiment record
   (docs/experiments/E-NNN.md → APPROVED → VERIFIED). No record, no write.
   The only way past a block is to complete the record, never to route
   around it (another tool, $var obfuscation, mode downgrade, forged token).
2. Never print, copy, cat or share the gate key; never edit gate-written
   fields (Decision, Gate Evidence, Next Step, Raw Results, Uncertainty,
   Metric, Measurement Command, Status).
3. Records and artifacts go under my project's docs/; templates, config and
   scripts are read from the plugin root — never write into the plugin.
4. Report every step with: what changed (record ids) / what the gate said
   (including soft-vs-hard) / next step / what is still open. Never claim
   "done" before the gate has said so.
5. Start each session by reading the injected context (chain progress,
   record inventory, pending handoffs) and tell me in one line where we are.

First: report the current state (init marker, gate key status, chain
progress, mode: which gates are soft/hard), then wait for my goal.
```

**Adaptations (pick the line that matches you):**

- Empty/new project: add `Project type: greenfield. Put code_guard, quality_gate and deploy_guard in hard mode after setup.`
- Existing codebase: add `Project type: brownfield. Start with code_guard = "soft" only; tighten it after the first VERIFIED scope.`
- Working on the plugin itself: add `Project type: self-hosting (metodoloji repo). hooks/, scripts/, skills/, custom/ are free here; regenerate hooks.json with scripts/sync-hooks-json.py --write instead of editing it.`
- Windows + WSL: add `I use both PowerShell and WSL homes. If tokens come back FORGED on the other side, tell me to re-sync the key — do not regenerate it twice.`

---

## 3. Setup Prompts — by Project Type

### 3.1 Empty project (greenfield) — Claude Code

```
Install the metodoloji plugin:
/plugin marketplace add https://github.com/metodoloji/metodoloji
/plugin install metodoloji@metodoloji

Then in this project run /metodoloji:init, /metodoloji:gate-setup and
/metodoloji:audit, and set code_guard, quality_gate and deploy_guard to
"hard" in custom/config.toml. Finish with a health summary.
```

**OpenHands variant:**

```
Install the plugin with install_plugin("github:metodoloji/metodoloji"),
then run /metodoloji:init, /metodoloji:gate-setup and /metodoloji:audit in
this project, set all three gates to "hard", and give me a health summary.
```

**Expected reply:** init ran once (marker written), key created once (status only, never the content), `check-plugin.sh` exit status, mode row reported (all three hard), and the suggested next prompt (first experiment).

**Follow-up:** `Open my first experiment for <module> with a narrow scope and tell me which bench script to write.`

### 3.2 Existing codebase (brownfield)

```
Bring the methodology onto this existing repo:
1. Run /metodoloji:init (it must not overwrite my files) and /metodoloji:gate-setup.
2. Set ONLY code_guard = "soft" — leave quality_gate and deploy_guard alone.
3. Run /metodoloji:audit and list which modules are most active so we can
   pick the first experiment target.
Warn me before any setting that would affect other projects.
```

**Expected reply:** init confirmed non-destructive, soft mode active, an audit with chain gaps, and a candidate first module (e.g. `src/billing/**`).

**Follow-ups:** `Open a narrow experiment for <module> only.` → after it verifies: `Now switch code_guard back to "hard".`

### 3.3 The plugin's own repository (self-hosting)

```
We are working inside the metodoloji repo itself. Tell me the rules for this
mode (which trees are free, how hooks.json must be regenerated), then run the
full check suite so I know the baseline before I change anything.
```

**Expected reply:** self-modification zone explained (`hooks/`, `scripts/`, `skills/`, `custom/` free *here*), `hooks.json` regeneration rule (`scripts/sync-hooks-json.py --write`), baseline of `pytest` + `check-plugin.sh`.

### 3.4 Team / multiple machines

```
We are two developers on two machines. Set this project up for that:
each machine keeps its own gate key (never share one), personal settings go
to *.user.toml and team settings to committed TOML, and if I get FORGED on a
teammate's record explain the Re-Measured-By procedure instead of touching
the token. Then show me the blackboard view for handoffs.
```

**Expected reply:** key policy stated, config split explained, `handoffs`/`chain-health` output.

### 3.5 Spike / prototype (no chain wanted)

```
I only want to try an idea — no records. Work in scratch/, and when
something stabilises tell me which bench to promote into scripts/bench/ and
what an experiment would look like if we kept it.
```

**Expected reply:** free-zone confirmation (secret patterns still denied) and a promotion plan.

### 3.6 First feature after setup (the "get going" prompt)

```
Chain is set up. Start the first piece of work: pick the smallest useful
scope, open the experiment, run the gate, and stop before writing code until
you can show me VERIFIED.
```

**Expected reply:** `E-001` drafted with theory/hypothesis/metric/scope, `--dry-run` shown, gate result, then `--verify` output.

---

## 4. Build Prompts — Every Delivery Scenario

Each block: the prompt to send, the follow-ups it unlocks, and what the reply must contain. All of them assume the bootstrap prompt of #2 (or an equivalent session instruction).

### 4.1 Build from idea to production (full chain)

**Send:**

```
Turn this idea into production: <one paragraph of the idea>.
Walk the chain end to end — idea pressure test, brainstorm, PRD, architecture,
readiness check, epics, story, experiment, implementation, QR, commit, PR —
and stop at each gate to show me the result before moving on.
```

**Follow-ups (in order):** `Show me the PRD draft.` → `Show the architecture decision map and the draft Code Scope.` → `Are we ready? Run the readiness check and stop if anything is missing.` → `Open the experiment for the first scope.` → `The story is drafted — finalize it now (refs, AC set, tasks, DoD).` → `Implement it.` → `Open the QR before I commit.` → `Commit, then prepare the PR.`

**Expected reply at each gate:** record ids, gate result, mode. Order traps the agent must respect (prompt it if it doesn't): experiment **after** the story draft but **before** finalizing; readiness checked **twice** (after architecture, after the experiment); architecture drafts the scope, the experiment locks it; a rejected experiment renews **only** that link — nothing rewinds.

### 4.2 Add a feature to an existing project

**Send:**

```
Add <feature> under <path>. Open the experiment with a narrow scope covering
only <path>, run the gate, and do not write any code until you can show me
VERIFIED for that scope.
```

**Follow-ups:** `Dry-run the measurement first.` → `Good — now implement it inside the scope.` → `Story + QR before the commit.`
**Expected reply:** `E-NNN` with `Code Scope: <path>` → `VERIFIED` → code written → `S-NNN` → `QR-NNN` → commit gated on `IR✓ QR✓ SP✓`.

### 4.3 Bugfix / hotfix

**Send:**

```
Hotfix <symptom> in <file or module>. Use a single-file narrow experiment with
a regression bench, then QR, commit and PR — the PR must include rollback and
a kill switch even though it is small. Do not skip any link because it is small.
```

**Expected reply:** narrow `E`, regression bench under `scripts/bench/`, fix, `QR` with test evidence, PR with rollback/kill-switch filled.

### 4.4 A research question appeared mid-work

**Send:**

```
Before we continue: we need to answer <question>. Choose the right mode
(A if measurable, else B/C/D), open the record, and tell me whether this
blocks code or is documentation only.
```

**Expected reply:** mode choice + the statement that **only Mode A opens code**; the chain continues where it was.

### 4.5 Sprint planning

**Send:**

```
Plan the sprint: goal in one sentence, stories with ids and points, capacity
against our velocity, tech-debt items time-boxed, and a plan for every
blocker. Reject the plan if any story has no S record.
```

**Expected reply:** `SP-NNN` with the checklist satisfied; anything incomplete is called out rather than smoothed over.

### 4.6 Write the story (with all required metadata)

**Send:**

```
Write the story for <goal>. Frontmatter must reference an APPROVED experiment,
every AC needs its four fields (Experiment, Type, Measured, Verify), every
task must point at an existing AC, and every DoD item needs DoD-NNN + Verify.
Show me the gaps before you tell me it's ready.
```

**Expected reply:** `S-NNN` + an explicit gap list (orphans, missing fields) — never a clean bill of health that the commit gate later disproves.

### 4.7 Quality review before commit

**Send:**

```
Open the QR for <story>: coverage, tests, lint, security scan, review — and
the two standard tables (AC and DoD). If a DoD row is still pending while the
story is done, fix the record first; do not commit yet.
```

**Expected reply:** `QR-NNN` in `docs/quality/`, both tables in standard format, and either `APPROVED` or an explicit list of failing rows. If QR coherence denies the commit, the fix is `python3 scripts/sync-story-qr.py --apply`.

### 4.8 Commit

**Send:**

```
Commit this story with a conventional message including the story and
experiment ids. If the commit gate blocks, tell me which link is missing
(IR, QR, SP) and complete it rather than softening anything.
```

**Expected reply:** `git commit` result, or the exact missing link in chain order `IR → QR → SP` (+ story metadata/QR coherence).

### 4.9 Deploy

**Send:**

```
Prepare production: complete the PR (staging, rollback, monitoring, feature
flag, runbook, window, approval), then deploy. If the deploy gate blocks,
name the missing link.
```

**Expected reply:** `PR-NNN` sections filled → deploy command → gate checked `IR✓ QR✓ SP✓ PR✓` → **Deploy Result** filled afterwards (metrics, PM id if any).

> Remember: `git push origin main` counts as a deploy, so a push without a PR denies in hard mode.

### 4.10 Run several experiments in parallel

**Send:**

```
We are running <A> and <B> at the same time. Keep both experiments narrow and
non-overlapping, name the covering experiment in every commit and story, and
tell me if a file would fall under both scopes.
```

### 4.11 Record tech debt

**Send:**

```
The review found this debt: <description>. Add it to tech-debt.md with a
priority (P0/P1 need a target sprint), put a matching TODO in the code, and
time-box it into the next sprint. Tell me when it's paid so it moves to the
Paid table.
```

### 4.12 Incident → post-mortem

**Send:**

```
SEV1/SEV2 just happened: <what>. Within this session write the blameless
post-mortem (UTC timeline, impact, 5 whys, detection/response, lessons,
owned actions), turn any resulting debt into a TD record, and note the PM id
in the PR's Deploy Result.
```

### 4.13 Drive a declared process (workflow engine)

**Send:**

```
Run the seo-visibility workflow for this project and take it stage by stage:
show me `next`, do the stage's work, only mark it complete with the required
evidence, and stop if evidence is missing instead of marking it done.
```

**Follow-ups:** `What is the current stage and the computed next one?` → `Block it — reason: <no access>.` → `Resume when access arrives.`
**Expected reply:** JSON-backed status; `complete` refusing without evidence; `regressed` flag consumed so a failing loop runs once; code still gated by the experiment chain.

### 4.14 Set a team rule (TOML customization)

**Send:**

```
Add a team rule for <skill>: <the rule>. Put it in the committed custom layer,
then prove it is actually visible at runtime — a rule sitting in a file is not
enough. Personal preferences go in the gitignored *.user.toml instead.
```

**Expected reply:** the file written + a `resolve_customization.py -k …` proof of the effective value.

### 4.15 Coordinate through the blackboard

**Send:**

```
Put <focus> on the blackboard as hot, track open questions in the run list,
then hand off to <next skill> when this stage is done — and show me pending
handoffs and chain-health before you do.
```

**Expected reply:** focus set, run list populated, `handoffs`/`chain-health`/`doctor --json` output; at close the focus cleared and `NEEDS ATTENTION` surfaced to you.

### 4.16 Choose/build with the skill families

| Your prompt | Skills the agent reaches for |
|---|---|
| `Pressure-test this idea before we build anything.` | `bmad-forge-idea`, `bmad-brainstorming`, `bmad-prfaq` |
| `Write the PRD: goal, scope, NFRs, acceptance criteria.` | `bmad-prd` (shims `bmad-create-prd`/`bmad-validate-prd` route here) |
| `Produce the architecture: decisions, module boundaries, draft Code Scope.` | `bmad-architecture` |
| `Design the UX for <flow>.` | `bmad-ux`, `wds-*` |
| `Turn this into epics and stories.` | `bmad-create-epics-and-stories`, `bmad-create-story` |
| `Implement this story.` | `bmad-dev-story` (or `bmad-quick-dev` single, `bmad-dev-auto` batch) |
| `Review the code and record quality.` | `bmad-code-review`, `bmad-quality-record` |
| `Add tests / raise coverage / wire CI checks.` | `bmad-testarch-*`, `bmad-qa-generate-e2e-tests` |
| `Prepare production readiness.` | `bmad-production-readiness` |
| `We went off track — correct course.` | `bmad-correct-course`, `bmad-retrospective` |
| `Build a game:` | `gds-*` (brief → GDD → architecture → story → dev → test → playtest) |
| `Build a web/UX product:` | `wds-*` + `bmad-ux` |
| `Run this skill in a clean room and report transcript, time and tokens.` | `bmad-eval-runner` |

---

---

## 5. Maintenance Prompts — Audit, Tighten, Update, Recover

### 5.1 Health and audit

```
Run /metodoloji:audit and give me the four-line report: what passed, what
failed, what is open, and the one thing to fix first.
```

Follow-up for depth: `Run the negative tests too — prove the gates still catch breakage.` (answer: `sh scripts/check-plugin.sh --negtest` and `sh scripts/check-custom.sh --negtest`).

### 5.2 Verify a single record

```
/metodoloji:verify <record id>
```

Expected: `VERIFIED` / `FORGED` / `REJECTED` / `ADVISORY-BLOCK`, plus the next step from #7.

### 5.3 Where are we? (daily prompt)

```
Where are we? Show chain progress, the record inventory, review/in-progress
stories, pending handoffs and anything NEEDS ATTENTION — from the board and
the files, not from memory.
```

### 5.4 What happened this session?

```
Read the audit log for this session and summarise it: every write, every deny,
every methodology warning, and whether any of them are still unresolved.
```

(The agent reads `.metodoloji/logs/hook-audit.log` between the `session_start` and `session_stop` markers; bodies are previews only.)

### 5.5 Tighten the gates (soft → hard)

```
We now have a VERIFIED scope. Move code_guard to "hard" (and quality_gate /
deploy_guard to "hard" if the chain exists), then prove it with one out-of-
scope write attempt and show me the deny.
```

### 5.6 Update the plugin

```
Update the metodoloji plugin, rerun the test suite and the plugin audit, and
tell me if hooks.json drifted (it must be regenerated, never hand-edited).
```

### 5.7 Security hygiene

```
Check .env hygiene: .env must not be tracked, .env.example must exist, and
.gitignore must cover .env. Also sweep the audit log for methodology_warnings.
```

### 5.8 Recovery prompts (send the symptom verbatim)

| You send | What the agent must do |
|---|---|
| `Everything is denied with exit 2.` | check Python ≥3.11 and the engine path (`hooks/engine/main.py` + `modules/`) — fail-closed means a broken engine denies everything |
| `This record says FORGED.` | determine hand-edit vs other-machine: regenerate, **or** the `Re-Measured-By` procedure with a fresh record under your key |
| `Tokens are FORGED only inside WSL (or only in PowerShell).` | re-sync the single key between the two homes — never `--init-secret` twice |
| `init says already installed but there is no skeleton.` | broken init: one-off repair `python3 {metodoloji-root}/bmad/scripts/skeleton.py --install` |
| `The stop report appears twice.` | duplicate Stop registration → remove one via `/hooks` |
| `Claude reports "2 async PostToolUse hooks completed".` | duplicate plugin install: manifest + manual `settings.json` entry + stale marketplace cache |
| `hooks.json does not match.` | regenerate with `python3 scripts/sync-hooks-json.py --write` |
| `The commit is denied and I don't know why.` | name the missing link in chain order `IR → QR → SP`, plus story metadata / QR coherence, and fix the record |
| `A bench was rejected.` | the bench sat on a free surface → move it to `scripts/bench/` and re-run **in a new record** |

---

## 6. Combination Matrix — Project State × Goal → Prompt

### 6.1 The master grid

| Project state ↓ / Goal → | Setup | Build | Research | Quality | Deploy | Customize | Audit |
|---|---|---|---|---|---|---|---|
| **Empty (greenfield)** | #3.1 | #4.1 or #4.2 | #4.4 | #4.7 | #4.9 (from row 8 mode) | #4.14 | #5.1 |
| **Existing repo (brownfield)** | #3.2 | #4.2 (code_guard soft → tighten after first VERIFIED) | #4.4 | #4.7 | #4.9 | #4.14 | #5.1 |
| **Plugin repo (self-host)** | #3.3 | #4.2 (note: `hooks/`, `scripts/`, `skills/`, `custom/` are free here) | #4.4 | #4.7 | #4.9 | #4.14 (committed plugin layer) | #5.1 + #5.6 |
| **Team / multi-machine** | #3.4 | #4.2 + #4.10 | #4.4 | #4.7 | #4.9 | #4.14 (team vs `*.user.toml`) | #5.1 + #5.3 |
| **Spike (no chain)** | #3.5 | #5 (work in `scratch/`, promote later) | #4.4 | — | — | — | #5.1 |
| **In-flight work** | — | #4.2 / #4.6 | #4.4 | #4.7 / #4.8 | #4.9 | #4.14 / #4.15 | #5.3 / #5.4 |
| **Broken / blocked** | #5.8 | #5.8 | — | #4.7 (QR coherence fix) | #5.8 | #5.8 | #5.1 |

### 6.2 Runtime dimension (does the prompt change?)

| Thing | Claude Code | OpenHands |
|---|---|---|
| Install wording | `/plugin marketplace add …` + `/plugin install metodoloji@metodoloji` + `claude plugin enable metodoloji` | `install_plugin("github:yunusgungor/metodoloji")` |
| Slash prompts | `/metodoloji:*` work as-is | same prompts, executed through the runtime's skill/command surface |
| Everything else (free-form prompts, follow-ups, reports) | identical | identical |

Rule of thumb: **only the install prompt differs between runtimes** — after that the prompts are the same because the agent normalises its own tool vocabulary.

### 6.3 Mode dimension (what the same prompt will produce)

The same build prompt behaves differently depending on the configured mode (`custom/config.toml [hooks]`):

| Goal of the prompt | `code_guard = "soft"` | `code_guard = "hard"` |
|---|---|---|
| "add this feature" | the write passes **with warnings** you must still relay | blocked until a VERIFIED scope covers the path |
| "commit it" | chain gaps surface as warnings | `IR → QR → SP` (+ story metadata, QR coherence) must exist or `DENY` |
| "deploy" | gaps warn | `IR → QR → SP → PR` must exist or `DENY` |
| "close the session" | Stop reports, never blocks | Stop reports, never blocks |

Always ask once per session: `Which gates are currently soft and which are hard?`

### 6.4 Chain-state dimension (what to prompt next)

| The agent reports… | Your next prompt |
|---|---|
| experiment drafted | `Dry-run it, then run the gate.` |
| `APPROVED` | `Verify it, then start coding inside the scope.` |
| `REJECTED` | `Revise the hypothesis and open a new record — do not edit the old one.` |
| `ADVISORY-BLOCK` | `Bigger sample, matching metric, re-measure in a new record.` |
| `FORGED` | `Regenerate it (or Re-Measured-By if it came from another machine).` |
| IR missing | `Complete the readiness check — stop if something is missing.` |
| SP missing | `Plan the sprint: one-sentence goal, stories, capacity, debt, blockers.` |
| story drafted | `Finalize it: refs, AC set, task↔AC, DoD.` |
| QR missing / pending DoD row | `Open the QR and clear the pending rows before commit.` |
| PR missing | `Complete the PR: staging, rollback, monitoring, flag, window, approval.` |
| all green | `Commit, deploy, fill Deploy Result, then report the four lines.` |

---

---

## 7. Reading the Answers — What the Agent's Reply Means

### 7.1 Experiment verification — exit codes and meanings

| Exit | Output | Meaning | Your move |
|---|---|---|---|
| 0 | `VERIFIED` | real approval, scope open | "implement it" |
| 1 | `FORGED` | token ≠ record (hand edit, or another machine's key) | "regenerate" / "Re-Measured-By" — never "repair the token" |
| 1 | `REJECTED` or undecided | the gate refused (or never ran) | "revise and open a new record" |
| 2 | `ADVISORY-BLOCK` | real token, code still closed (small sample / `n unknown` / metric `MISMATCH`) | "bigger sample, matching metric, new record" |

### 7.2 Record statuses

| Record | Statuses you may see | Healthy state |
|---|---|---|
| E | `planned` → `APPROVED` / `REJECTED` (+ gate-written rows) | `APPROVED` + `VERIFIED` |
| IR | `READY` / `INCOMPLETE` | `READY` before any sprint |
| SP | `planned` / `in-progress` / `completed` / `cancelled` | `in-progress` during a sprint |
| Story | `backlog` / `sprint` / `in-progress` / `review` / `done` / `blocked` | `done` only with an approved QR |
| QR | `in-review` / `APPROVED` / `REJECTED` / `REVISED` | `APPROVED` before commit |
| PR | `preparing` / `READY` / `WAITING` | `READY` before deploy |

### 7.3 Asking for proof (never accept an unsupported claim)

```
Show me the evidence for that claim: the exact gate output, the record id,
and the file you verified. If you cannot, mark it as not done.
```

Useful evidence prompts:

- `Run /metodoloji:verify on every experiment and give me the VERIFIED/FORGED split.`
- `Show the QR tables (AC and DoD) you produced.`
- `Show the effective value of that customization, not the file it sits in.`
- Run `doctor --json` and show me what needs attention.
- `Which gate produced this answer, and in which mode?`

### 7.4 Warning vs block

A **warning** (`methodology_warnings`, soft mode) means the agent got through with a recorded gap. Ask: `List every methodology warning from this session and fix each one.` A **block** (deny) means the missing record is now the only path forward.

---

## 8. Troubleshooting Prompts — Symptom → Prompt to Send

| You observe | Send this |
|---|---|
| "Nothing gets written" | `Everything is denied with exit 2 — check Python and the engine path and report the cause.` |
| "No approved experiment record" | `Open a narrow experiment for that path and take it through the gate.` |
| "Gate key not configured" | `Run /metodoloji:gate-setup once and confirm the key status without showing me its content.` |
| A `FORGED` record | `Is this a hand edit or another machine? Apply the right fix — never rewrite the token.` |
| `ADVISORY-BLOCK` | `Increase the sample, match the metric, and re-measure in a new record.` |
| Commit denied | `Which link is missing — IR, QR, SP, story metadata, or QR coherence? Complete it.` |
| Deploy denied | `Which link is missing — IR, QR, SP or PR? Complete the PR and retry.` |
| Bench rejected | `Move the bench into scripts/bench/ and re-run it in a new record.` |
| `hooks.json` mismatch | `Regenerate hooks.json with the sync script; do not hand-edit it.` |
| Doubled Stop report | `There is a duplicate Stop registration — clean it up via /hooks.` |
| Doubled async hook warning | `Duplicate plugin install — clean manifest, settings.json and the stale cache.` |
| `pending` DoD row on a done story | `Sync the QR result back into the story, then re-check coherence.` |
| Not sure where you are | `Where are we? Chain progress, inventory, pending handoffs, NEEDS ATTENTION.` |
| You disagree with a gate answer | `Explain which field the gate parsed and why it reached that verdict.` |
| Tool error / missing parameter (`file_path`/`command`) | `Call tools by bare harness names (Claude Code: Read, Bash, Write, Edit, Glob, Grep; OpenHands: execute_bash, str_replace_editor); never use namespace prefixes or empty blocks.` |

---

## 9. Prompt Catalogue Index

| ID | Prompt | Section |
|---|---|---|
| BOOT | Session bootstrap (operating rules + state report) | #2 |
| SETUP-GREEN-CLAUDE | Install + init + gate-setup + audit + hard mode (Claude Code) | #3.1 |
| SETUP-GREEN-OH | Same for OpenHands | #3.1 |
| SETUP-BROWN | Existing repo, `code_guard = "soft"` only | #3.2 |
| SETUP-SELF | Plugin repo self-hosting rules + baseline | #3.3 |
| SETUP-TEAM | Multi-machine keys + config split + handoffs | #3.4 |
| SETUP-SPIKE | Scratch-only prototype, promotion plan | #3.5 |
| FIRST-EXPERIMENT | Smallest scope → gate → stop at `VERIFIED` | #3.6 |
| BUILD-FULL | Idea → production, gate at every link | #4.1 |
| BUILD-FEATURE | Add a feature under a path | #4.2 |
| BUILD-HOTFIX | Single-file experiment + rollback PR | #4.3 |
| BUILD-RESEARCH | Mode choice mid-work | #4.4 |
| BUILD-SPRINT | Sprint plan with checklist | #4.5 |
| BUILD-STORY | Story with all mandatory metadata | #4.6 |
| BUILD-QR | QR + standard tables before commit | #4.7 |
| BUILD-COMMIT | Commit, explain any block | #4.8 |
| BUILD-DEPLOY | PR completeness → deploy → Deploy Result | #4.9 |
| BUILD-PARALLEL | Two non-overlapping experiments | #4.10 |
| BUILD-DEBT | Tech-debt row + TODO + sprint item | #4.11 |
| BUILD-PM | Blameless post-mortem | #4.12 |
| BUILD-WORKFLOW | Declared process, evidence-gated stages | #4.13 |
| BUILD-TOML | Team rule + runtime proof | #4.14 |
| BUILD-BOARD | Focus, run list, handoff | #4.15 |
| SKILL-MAP | Which prompt reaches which skill family | #4.16 |
| M-AUDIT | Full health report | #5.1 |
| M-VERIFY | Verify one record | #5.2 |
| M-WHERE | Daily state prompt | #5.3 |
| M-LOG | Session audit-log summary | #5.4 |
| M-TIGHTEN | Soft → hard, prove the deny | #5.5 |
| M-UPDATE | Plugin update + drift check | #5.6 |
| M-ENV | `.env` hygiene + warnings sweep | #5.7 |
| RECOVER-* | Symptom-specific recovery prompts | #5.8 |
| GRID | Project state × goal → prompt | #6.1 |
| NEXT | Chain state → next prompt | #6.4 |
| PROOF | Evidence-for-your-claim prompt | #7.3 |
| TROUBLE-* | Symptom → prompt | #8 |

---

## 10. Anti-Patterns — Prompts That Fail, and the Rewrite

| Don't send | Why it fails | Send instead |
|---|---|---|
| `Just write the code, it's two lines.` | guard denies without a covering VERIFIED scope | `Open a narrow experiment for that file, run the gate, and only then write it.` |
| `Show me / share the gate key.` | key references are denied on purpose | `Check the key status only; fix problems with /metodoloji:gate-setup.` |
| `Mark the record APPROVED manually.` | editing a gate-written field yields `FORGED` | `Re-run the measurement properly, or open a new record.` |
| `Run the gate without --dry-run so I can see the format.` | it commits a verdict | `Preview with --dry-run; only run it for real when we mean to decide.` |
| `Skip the QR, we're in a hurry.` | commit gate denies in hard mode anyway | `Open the QR now — even a small one — then commit.` |
| `Leave all gates on soft permanently.` | warnings accumulate into a false green | `Start soft (brownfield), tighten to hard at the first VERIFIED scope.` |
| `Delete this item from the TOML.` | merges have no deletion mechanism | `Override it with the same code/id and a noop description, or fork the skill.` |
| `Put the bench in scratch/ so it's quick.` | the gate refuses measurements from free surfaces | `Put it in scripts/bench/ before we measure.` |
| `Edit hooks.json directly.` | generated file, byte-identical check | `Regenerate it with scripts/sync-hooks-json.py --write.` |
| `Copy the manifestos into the project.` | stale copies confuse | `Read them from the plugin; delete any leftover docs/bmad/ copy.` |
| `Mark the workflow stage complete — I'm sure it works.` | `complete` refuses without evidence | `Attach the evidence (artifact/command/note), then complete it.` |
| `Report it as done and we'll verify later.` | a false completion is worse than a deny | `Report: done / not done, with the gate output for each claim.` |
| `Set the scope to the whole codebase.` | wide scope = weak evidence + high `FORGED` risk | `One module per experiment; keep the first scope deliberately small.` |
| `Call tools with namespace prefix or empty blocks.` | harness rejects prefixed names as unknown; empty blocks fail parameter validation | `Call tools by exact bare names (Read, Bash, Write, Edit, Glob, Grep) with all required parameters.` |

---

**Closing note.** This file is written for `metodoloji` v0.1.0. When the plugin evolves, re-check the promises against `docs/CLAUDE.md`, `templates/` and `.plugin/plugin.json`. Licence and BMAD attribution: `LICENSE`.


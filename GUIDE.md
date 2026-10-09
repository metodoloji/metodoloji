# AGENT RUNBOOK — metodoloji (v0.1.0)

---

## Contents

1. [How to Use This Runbook](#0-how-to-use-this-runbook)
2. [Operating Contract — Invariants](#1-operating-contract--invariants)
3. [Mental Model — Understand the System in 60 Seconds](#2-mental-model--understand-the-system-in-60-seconds)
4. [Decision Tree — Map the User's Request to a Playbook](#3-decision-tree--map-the-users-request-to-a-playbook)
5. [P0 — Session Opening (every session)](#4-p0--session-opening-every-session)
6. [P1–P6 — Setup and Getting-Started Playbooks](#5-p1p6--setup-and-getting-started-playbooks)
7. [P7–P18 — Delivery Chain Playbooks](#6-p7p18--delivery-chain-playbooks)
8. [P19–P27 — Maintenance Playbooks](#7-p19p27--maintenance-playbooks)
9. [Record Chain Reference E → IR → SP → S → QR → PR](#8-record-chain-reference-e--ir--sp--s--qr--pr)
10. [Hook Engine and Mechanical Gates](#9-hook-engine-and-mechanical-gates)
11. [Free Zone / Protected Area / Secret Scanning](#10-free-zone--protected-area--secret-scanning)
12. [Security — Gate Key, Trust Ring, and HMAC](#11-security--gate-key-trust-ring-and-hmac)
13. [Skill Catalog — When to Call Which Skill](#12-skill-catalog--when-to-call-which-skill)
14. [TOML Customization — 3 Layers + 8-Layer Config](#13-toml-customization--3-layers--8-layer-config)
15. [Blackboard — Working Context](#14-blackboard--working-context)
16. [Command Reference](#15-command-reference)
17. [Auditing and Health Checks](#16-auditing-and-health-checks)
18. [Troubleshooting — Symptom → Your Action](#17-troubleshooting--symptom--your-action)
19. [NEVER List — 60 Details That Hurt If Skipped](#18-never-list--60-details-that-hurt-if-skipped)
20. [Reporting Contract to the User](#19-reporting-contract-to-the-user)
21. [Reference — Architecture Map, Root Resolution, Glossary](#20-reference--architecture-map-root-resolution-glossary)

---

## 0. How to Use This Runbook

Do not treat this as text to read once from cover to cover. It is a **decision table**: recognise your situation, go to the section, execute the instruction.


| Your situation                                        | Go to                                                    |
| ----------------------------------------------------- | -------------------------------------------------------- |
| First time in the target project                      | #4 (P0 session opening) → #5 (P1: init/gate-setup/audit) |
| The user asked you to build something                 | #3 (decision tree) → #6 (the relevant P7–P18)            |
| The user asked "why was it blocked / why did it fail" | #17 (troubleshooting) → #19 (how you report)             |
| The chain/commit/deploy was rejected                  | #17 → #8 (record fields) → #9 (which gate, which mode)   |
| The user asked for a record/plan/customization        | #8, #13, #14                                             |
| The user said "check everything"                      | #16 (auditing)                                           |
| You think you are about to break a rule               | #1 (invariants) + #18 (60 items)                         |


**Reading discipline — from the source, not from memory:**

1. Take the chain's real state from the **SessionStart output** (`Chain progress`, record inventory, sprint line, PROACTIVE nudge). The session's own state is authoritative, not your recollection of the model.
2. Never invent the `{metodoloji-root}` value: use the SessionStart `METODOLOJI active (plugin: PATH)` line **verbatim**. If you cannot find it, use the resolution order in #20.
3. If you cannot recall a script's interface, run `--help` (`run_experiment.py --help`, `blackboard.py --help`, `workflow.py --help`). The engine and CLIs are canonical; this document summarises them.
4. This document is for v0.1.0. When in doubt, cross-check: `docs/CLAUDE.md` (engine summary), `docs/bmad/*-methodology.md` (manifesto), `templates/` (current templates), `.plugin/plugin.json` (version).

---

## 1. Operating Contract — Invariants

These are not preferences; they are the system's operating conditions. Violating one either gets mechanically rejected (deny) or silently fabricates evidence — both are unacceptable.

1. **The only path to code is a VERIFIED experiment.** Without `APPROVED` + a real `GATE-OK-...` token on `docs/experiments/E-NNN.md`, do not write code. "It's a small change" is not an exception.
2. **The gate runs the measurement; you do not declare it.** You do not write the number, the metric or the decision — the gate derives them from the output it ran itself.
3. **You never touch gate-written fields.** `Decision`, `Gate Evidence`, `Next Step`, `Raw Results`, `Uncertainty`, `Metric`, `Measurement Command`, `Status` lines. Editing by hand breaks the token → FORGED.
4. **What the file *is* decides its root.** Records/artifacts → `{project-root}`; templates/config/scripts → `{metodoloji-root}`. The single exception is `bmad-customize` (writes into the plugin's `custom/`).
5. **You never touch the gate key.** `~/.bmad/gate-key` lives outside the repo; you never print, copy, `cat`, share or overwrite it. Key problems are `/metodoloji:gate-setup`'s job, not the shell's.
6. **The free zone is not a security hole.** Secret scanning runs **before** the free-zone check; even `scratch/` denies `gate-key`/`.bmad`/`gate_token` patterns.
7. **Benches live in a protected directory** (`scripts/bench/`). The gate will not run a measurement from any free surface: `scratch/`, `tmp/`, `temp/`, `graft/`, `openhands/`, `_bmad/`, `.metodoloji/`, root-level `explore_*`.
8. **The way "past" a deny is to complete the record** — not to write the file through another tool, hide the target behind `$var`, downgrade the mode, or hand-forge the token. Guard sees shell writes too.
9. **You do not choose the order.** The chain (E→IR→SP→S→QR→PR) and the declared workflow's next stage are computed by the kernel; you do the stage's **work**.
10. **`FORGED` for another machine is normal.** Keys are machine-local; a foreign signature is provenance information. The fix is in #6 P11 — not hand-repairing the token.
11. **The Stop gate never blocks you** (report-only). Blaming a session close on a deny is a misdiagnosis (#17).
12. **A report does not say "done" before the work is done.** If you passed in soft mode with a warning, say so; in hard mode the same gap denies.

---

## 2. Mental Model — Understand the System in 60 Seconds

1. **Writing code requires permission.** Permission = the `docs/experiments/E-NNN.md` record running the gate and producing `APPROVED` + a `GATE-OK-...` token.
2. **Chain:** `E (experiment) → IR (are we ready?) → SP (sprint plan) → S (story) → QR (quality) → PR (prod readiness)`. Each link unlocks the next one.
3. **Guards (hooks):** On every file-write / commit / deploy attempt the engine asks: "is there a covering VERIFIED experiment? is the chain complete?" If not, `DENY` (hard) or warning (soft).
4. **Output always goes to the project root:** Methodology *output* (story, experiment, planning/test artifacts — all under `docs/`) is never written inside the plugin. Plugin *source* (template, TOML, script) is read from the plugin root.
5. **Single exception:** `bmad-customize` writes user overrides under the plugin's `custom/`. Decision rule: *a file goes where it belongs* (record/artifact → `{project-root}`, template/config/script → `{metodoloji-root}`).
6. **The key is machine-local, trust is your ring:** `~/.bmad/gate-key` never enters the repo, is never shared. Signing uses a single key: the one on your machine. Verification looks at the ring: your own key + peer machine keys you imported under `~/.bmad/gate-keys/` via `--import-key` — a record signed on one of your own machines verifies on all of them. A record from a key outside the ring yields `FORGED` — this is design, not attack.

```
User request → E record → run gate → APPROVED → write code (within scope)
             → IR → SP → S → implement → QR → commit → PR → deploy → report
```

---

## 3. Decision Tree — Map the User's Request to a Playbook

The user's intent is usually implicit; **you map the request to a playbook and state what you are about to do** (not to ask permission, but to avoid running the wrong playbook).


| What the user says (representative)              | Playbook                                          |
| ------------------------------------------------ | ------------------------------------------------- |
| "Set up / start the system in this project"      | #5 P1 → (new project) P2 / (existing repo) P3     |
| "Build this application from scratch"            | #6 P8 (main chain) — on top of P2 setup           |
| "Put the methodology on my existing project"     | #5 P3 → #7 P19 (tighten after the first VERIFIED) |
| "Improve the plugin itself"                      | #5 P4                                             |
| "Just a quick experiment/prototype"              | #5 P5 (scratch — gapless)                         |
| "As a team / it should work on my other machine" | #5 P6                                             |
| "Add this feature"                               | #6 P7 (full flow)                                 |
| "The gate rejected it / no APPROVED"             | #6 P9 (REJECTED) / P10 (ADVISORY-BLOCK)           |
| "This record looks FORGED"                       | #6 P11                                            |
| "I got stuck writing a story"                    | #6 P12                                            |
| "The sprint overran / a story is blocked"        | #6 P13                                            |
| "A new question came up while coding"            | #6 P14                                            |
| "I need a bugfix/hotfix"                         | #6 P15                                            |
| "Record this debt"                               | #6 P16                                            |
| "There was an incident in prod"                  | #6 P17                                            |
| "Let's run two pieces of work in parallel"       | #6 P18                                            |
| "Come off soft mode, tighten it up"              | #7 P19                                            |
| "I can't get the commit/deploy through"          | #7 P20                                            |
| "What happened this session / show me the log"   | #7 P21                                            |
| "Check everything"                               | #7 P22 → #16                                      |
| "Whose turn is it / which handoff is pending"    | #7 P23 → #14                                      |
| "Let's set a team rule"                          | #7 P24 → #13                                      |
| "Update the plugin"                              | #7 P25                                            |
| "Is `.env` safe"                                 | #7 P26                                            |
| "Run this declared process (workflow)"           | #7 P27                                            |
| "Plugin health / what is broken"                 | #16 + #17                                         |


**Decision tree — when you do not know where to start:**

```
Does the target project have .metodoloji/initialized?
├── NO
│   ├── Project empty?            → P1 → P2 (greenfield)
│   ├── Project full (code exists)?→ P1 → P3 (brownfield)
│   └── The plugin's own repo?    → P1 → P4 (self-hosting)
└── YES
    ├── The user only wants a spike       → P5 (scratch, gapless)
    ├── There is production work          → P7 (feature flow) / P8 (from idea)
    └── Only maintenance/audit requested  → P22 (audit) + P19 (gate tightness)
```

---

## 4. P0 — Session Opening (every session)

**Trigger:** every new session, without exception. Run it before the user asks for anything.

**Do:**

1. **Read the SessionStart context** (the engine prints it, you consume it): chain reminder + gate key status + init status + **project state** (record inventory + sprint line: review/in-progress stories, epic-lag flags) + PROACTIVE nudge if a handoff is pending + chain progress (`Chain progress`) if the board carries E/IR/SP/S/QR/PR run keys. Open the session from those lines, not from memory.
2. **Note the `METODOLOJI active (plugin: PATH)` line** — that is `{metodoloji-root}`; use it verbatim, do not search (#20).
3. **Status probe:** pending handoffs (`handoffs`), open run list, `hot` key (#14). Give the user a one-sentence "where we left off".
4. **Interpret the warnings:** "marker exists → do not re-run init", "no marker → run once", skeleton present + no marker → **broken init** (one-off repair: `python3 {metodoloji-root}/bmad/scripts/skeleton.py --install`).
5. **Take the user's request** → map it via the #3 decision tree and state which playbook you will run.

**Don't:** run a full audit at session start (`check-plugin.sh` takes \~65s) — leave that to P22 unless the user asks.

### 4.1 Routine checklist (apply to yourself)

**Every session start:**

- [ ] `/metodoloji:audit` or at least `git status` + a look at pending handoffs (`handoffs`)
- [ ] Is today's story's E/IR/SP link intact (`--verify` green?)
- [ ] Blackboard focus (`write --hot`) + run list open?

**Before every code write:**

- [ ] Is the target file inside a VERIFIED scope?
- [ ] Is the bench in a protected directory? Did you see `--dry-run`?

**Before every commit:**

- [ ] AC four-field set + task↔AC + DoD-NNN complete?
- [ ] Is the done story's QR in `docs/quality/`? Are the tables in standard format?
- [ ] Do the IR/SP references exist?

**Before every deploy:**

- [ ] PR: staging PASS, rollback tested, alerts wired, kill switch present, window+approval done?

**Weekly:**

- [ ] `pytest` + 4 static checks green (`check-custom`, `check-plugin`, `check-methodology`, `check-techdebt` + `check-handoff.py`)?
- [ ] Tech-debt table current? Does every P0/P1 have a target sprint?
- [ ] `chain-health` + `doctor --json` clean? Pending handoffs consumed?
- [ ] `.env` hygiene (#6a) + a `methodology_warnings` sweep in the audit log

---

## 5. P1–P6 — Setup and Getting-Started Playbooks

### P1 — First install: `init` → `gate-setup` → `audit`

**Trigger:** "set up the system", "get started", or the target project has no `.metodoloji/initialized`.

**The order is strictly:** `init` → `gate-setup` → `audit` → first E record. Do not reorder, do not skip.

**Step 0 — Prerequisite check (offline, no credentials):**


| Requirement             | Minimum    | Note                                                                      |
| ----------------------- | ---------- | ------------------------------------------------------------------------- |
| Python                  | 3.11+      | Required for `tomllib`; the engine tries `python3 → python → py` in order |
| OpenHands / Claude Code | Current    | Plugin/hook API support                                                   |
| Git                     | 2.x        | Version control                                                           |
| Shell                   | POSIX `sh` | Git Bash is enough on Windows                                             |


Windows tricks: bootstrap finds Python automatically, translates paths when `cygpath` exists, silently ignores `chmod 600`, and `.sh` files are normalised to LF by `.gitattributes`.

**Step 1 — Install path (pick one for the user's runtime):**

OpenHands (SDK):

```python
from openhands.sdk.plugin import install_plugin
install_plugin("github:yunusgungor/metodoloji")
# → ~/.openhands/plugins/installed/metodoloji/
```

Temporary trial (including a local path):

```python
from openhands.sdk.plugin import Plugin
p = Plugin.load(Plugin.fetch("github:yunusgungor/metodoloji"))
p = Plugin.load("/path/to/metodoloji")  # local repo root
```

Claude Code (marketplace):

```bash
/plugin marketplace add https://github.com/metodoloji/metodoloji
/plugin install metodoloji@metodoloji
claude plugin enable metodoloji
```

> The plugin is **opt-in** (`defaultEnabled: false`). Fail-closed guard hooks do not run unless explicitly enabled.

Manual install (verification):

```bash
git clone https://github.com/metodoloji/metodoloji.git
cd metodoloji
ls .plugin/plugin.json
ls hooks/hooks.json
ls hooks/engine/main.py
ls .claude-plugin/marketplace.json
python3 --version  # must be >= 3.11
```

**Step 2 — Know what SessionStart already does for you (don't duplicate it):** `bootstrap.sh` (fail-open):

1. Creates `~/.bmad/gate-key` if missing (0600 on POSIX).
2. Creates the `.metodoloji/logs/` directory (runtime, needed every session). `docs/experiments/` is created **only when the init marker is absent** — init is a once-per-project job.
3. Injects context: chain reminder + gate key status + init status + project state + pending-handoff nudge + `Chain progress` (see #4).

**Step 3 — `/metodoloji:init` (once per project):**


| Work        | Detail                                                                                                                                                                                                                                          |
| ----------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Directories | `docs/experiments/`, `docs/development/stories/`, `docs/quality/` (QR's canonical home), `docs/research/`, `docs/design/`, `docs/design/prds/`, `docs/design/ux-designs/`, `docs/design/architecture/`, `scratch/` — leaves existing ones alone |
| Templates   | E, BD, C, IR, SP, QR, PR, S + README + tech-debt + scratch-README — **does not overwrite**, preserves existing files                                                                                                                            |
| Marker      | Writes `.metodoloji/initialized` (`initialized_at` + `plugin_version`). On later calls, if the marker exists init **short-circuits**: no directory/template work is repeated. To reinstall: `--force` or delete the marker.                     |
| Manifests   | Does not copy them (plugin-canonical)                                                                                                                                                                                                           |
| Warning     | Points to `/metodoloji:gate-setup` when the gate key is missing                                                                                                                                                                                 |


**Step 4 — `/metodoloji:gate-setup` (once per machine):**

```bash
python3 {metodoloji-root}/skills/bmad-research-experiment/scripts/run_experiment.py --init-secret
python3 {metodoloji-root}/skills/bmad-research-experiment/scripts/run_experiment.py --check-secret  # check without creating
```

Rules: the file lives **outside** the repo, 0600, generated with `secrets.token_hex(32)`, **never overwritten** if present (that would break old evidence), and its content is **never printed or copied**.

> **Windows + WSL:** the PowerShell home (`C:\Users\<you>\.bmad\gate-key`) and the WSL home (`~/.bmad/gate-key`) are different, yet the methodology treats them as one machine — if the key exists on only one side, every token on the other side shows as `FORGED` (#0/#3 fail). Generate the key **once** and sync it to the other side (`cp /mnt/c/Users/<you>/.bmad/gate-key ~/.bmad/gate-key && chmod 600 ...`); never run `--init-secret` twice. If you rotate it, re-sync immediately.

**Step 5 — `/metodoloji:audit`:**

```bash
sh scripts/check-plugin.sh
```

The scope and the # map are in #16. Exit 0 = HEALTHY; on problems go to #17.

**Step 6 — Install verification (all offline):**

```bash
python -m pytest -q             # 1100+ tests: hook engine, bridge, skills
sh scripts/check-custom.sh      # bridge TOML static audit
sh scripts/check-plugin.sh      # plugin structure audit (#0–#6f)
sh scripts/check-methodology.sh # record format audit
```

CI note: there is no `.github/workflows/` at the repo root — do not assume "CI runs on every push". After a change, run the 6 checks locally (pytest + 4 static checks + handoff lint).

### P2 — Greenfield (empty folder, recommended path)

**Trigger:** the user is starting a new project and the folder is empty.

```bash
# 0. Folder + git
mkdir my-project && cd my-project && git init

# 1. Install the plugin (Claude): /plugin marketplace add ... + install + enable
#    or OpenHands: install_plugin(...)

# 2. Skeleton + key + audit (init is ONCE per project)
/metodoloji:init          # writes the marker; later calls are a no-op
/metodoloji:gate-setup
/metodoloji:audit        # or: sh {metodoloji-root}/scripts/check-plugin.sh

# 3. Go to hard mode (do not wait in greenfield! brownfield-soft is not for you)
# custom/config.toml [hooks]: code_guard="hard", quality_gate="hard", deploy_guard="hard"
# stop_guard is report-only anyway — the key stays for compatibility, it has no effect.

# 4. Write the first experiment (NARROW scope — trick: keep the first scope deliberately small)
cp {metodoloji-root}/templates/_template_E.md docs/experiments/E-001.md
# fill E-001.md: Theory/Hypothesis/Metrics/Design/Code Scope (e.g. src/auth/**)
mkdir -p scripts/bench
# ... write scripts/bench/bench_auth.py (this bench belongs in a protected dir — not in a free zone!)
python3 {metodoloji-root}/skills/bmad-research-experiment/scripts/run_experiment.py \
  --record docs/experiments/E-001.md --run "python scripts/bench/bench_auth.py" --dry-run
python3 {metodoloji-root}/skills/bmad-research-experiment/scripts/run_experiment.py \
  --record docs/experiments/E-001.md --run "python scripts/bench/bench_auth.py"
python3 {metodoloji-root}/skills/bmad-research-experiment/scripts/run_experiment.py \
  --verify --record docs/experiments/E-001.md   # expect VERIFIED

# 5. Build the chain: IR → SP → S
cp {metodoloji-root}/templates/_template_IR.md docs/development/IR-001.md   # Status: READY
cp {metodoloji-root}/templates/_template_SP.md docs/development/SP-001.md   # one-sentence goal + S list
cp {metodoloji-root}/templates/_template_S.md docs/development/stories/S-001.md
# fill S-001 frontmatter experiment_refs + AC(*) + Task(AC:..) + DoD(DoD-NNN)

# 6. Implement (guard scope is open) → QR → commit → PR → deploy
# ... write code under src/auth/**
cp {metodoloji-root}/templates/_template_QR.md docs/quality/QR-001.md
git commit -m "feat(auth): S-001 ..."     # the quality gate checks IR→QR→SP
cp {metodoloji-root}/templates/_template_PR.md docs/development/PR-001.md
# ... deploy command → the deploy gate checks IR→QR→SP→PR
```

**P2 rules:** Write the first E to cover "a single module", not "the world" (narrow guard scope = small blast radius). Put the bench in a protected directory BEFORE the code (moving it later breaks the `Measurement Command` binding — in practice that means a new record). Never run `--run` without `--dry-run` first.

### P3 — Brownfield (retrofitting the methodology onto an existing project)

**Trigger:** working code exists, no experiment history. **Straight to hard mode = every write DENY = you are locked out.**

```bash
cd existing-project
/metodoloji:init          # ONCE; does not overwrite existing files — safe; writes the marker
/metodoloji:gate-setup
# custom/config.toml [hooks] → set ONLY code_guard="soft"
# (WARNING: this file is plugin-global policy — it affects every project; soften it
# temporarily and put it back to "hard" at the first VERIFIED scope. Do NOT touch
# quality_gate/deploy_guard — they are the commit/deploy guards; stop_guard is already
# "soft" and nothing reads it.)
# Start working in this "warn + pass" mode.
/metodoloji:audit
```

Then build a **coverage map**: pick the most active module (e.g. `src/billing/**`), write a NARROW E-001 for it, put the bench in `scripts/bench/`, get APPROVED, see `--verify` report VERIFIED. The moment the first VERIFIED scope exists:

```toml
[hooks]
code_guard = "hard"     # tighten
quality_gate = "hard"
deploy_guard = "hard"
```

**P3 rules:** Do not try to open the whole codebase with one E (the wider the scope, the weaker the evidence and the bigger the FORGED risk). Go module by module: one E per module. The fact that stop ignores a stale `sprint-status.yaml` is deliberate, for brownfield leftovers: if your old status file predates the session start, stop will not report it — don't panic. Legacy `docs/development/QR-NNN.md` records do not need moving — they are accepted; but open NEW QRs in `docs/quality/`.

### P4 — Self-hosting (working inside the plugin's own repo)

**Trigger:** you are changing code inside the `metodoloji` repo (engine, skill, template).

The conditional self-modification zone is active: when the protected project root is the plugin's own repo, `hooks/`, `scripts/`, `skills/`, `custom/` are free. In an ordinary project those trees require an experiment.

**Extra rules:** do not hand-edit `hooks.json` — generate it with `scripts/sync-hooks-json.py --write` (`check-plugin.sh` #1b demands byte-identical). After a change run `python -m pytest -q` plus the static checks. Document behaviour changes with an E record (bench + E — the improvement loop in #18).

### P5 — Solo-spike (quick one-person experiment)

**Trigger:** "would this library do the job?" — the user does not want a record chain.

Write into `scratch/` — gapless, free. Cook bench experiments in scratch, then **promote** the stabilised one to `scripts/bench/` (the gate will not run a bench from a free zone). Remember that secret patterns deny even inside scratch (`gate-key`, `gate_token`, ...). If the prototype is going to production, return to P2: open an E, measure, open the scope, move the code.

### P6 — Team + multiple machines

**Trigger:** 2+ developers, each on their own machine.

Everyone generates their own key (`gate-setup` — sharing is FORBIDDEN). Ali's APPROVED record shows `FORGED` on Ayşe's machine — normal. The flow: Ayşe runs the same measurement on her own machine under a new record (e.g. E-012) and adds `- **Re-Measured-By:** E-012 (date)` to the old record → the audit reports a `CROSS-MACHINE` warning (not an error). Personal settings live in `*.user.toml` (gitignored), team settings in the committed TOML. The blackboard is the dashboard: `write --hot`, run list, handoff chain (#14).

---

## 6. P7–P18 — Delivery Chain Playbooks

### P7 — Full feature flow (happy path, end to end)

**Trigger:** "add this feature" — the chain is already in place (P1/P2/P3 done).

```
E-045 (bench: scripts/bench/bench_bfs.py → APPROVED, VERIFIED)
 → IR-012 (READY: E-045 + PRD + architecture input)
  → SP-003 (one-sentence goal, S-027 3 points, capacity 18 of 20, debt time-boxed)
   → S-027 (frontmatter refs + 3 ACs + task↔AC + DoD-NNN)
    → code (src/search/** — INSIDE scope)
     → QR-028 (coverage 87%, tests green, review APPROVED)
      → git commit (quality: IR✓ QR✓ SP✓)
       → PR-007 (staging PASS, rollback tested, alerts wired)
        → deploy (deploy gate: IR✓ QR✓ SP✓ PR✓)
         → fill Deploy Result → done
```

Command skeleton:

```bash
cp templates/_template_E.md docs/experiments/E-045.md        # fill it in
python3 {metodoloji-root}/skills/bmad-research-experiment/scripts/run_experiment.py \
  --record docs/experiments/E-045.md --run "python scripts/bench/bench_bfs.py"
# APPROVED → fill IR/SP/S → implement → fill QR → commit → fill PR → deploy
python3 scripts/create-qr-record.py --story docs/development/stories/S-027.md  # QR skeleton
git commit -m "feat(search): S-027 bfs ..."
```

With the blackboard (recommended): the PRD skill does `write --hot prd.acme`, mirrors to UX (`mirror --to bmad-ux`), the architecture decision map goes to a canvas, open questions to the run list. At close: `doctor --json` + `hot --clear` + mirror. Fill every link's fields from #8.

### P8 — Idea → Brainstorming → Architecture → PRD → Epic → Story → Experiment → Production (main chain)

**Trigger:** you will run the whole pipeline from a raw idea to production (this is P7 including its "before").

```
Idea
 → forge-idea (pressure test: it lives / it dies cheap)
  → brainstorming (branch it with techniques: SCAMPER, six hats, mind map)
   → PRD (bmad-prd: goal, scope, NFRs, draft ACs) + UX (bmad-ux if needed)
    → Architecture (bmad-architecture: decision backbone, module boundaries, draft Code Scope)
     → check-implementation-readiness (if PRD/UX/architecture are missing, STOP — do not improvise through it)
      → Epics + Stories (bmad-create-epics-and-stories → bmad-create-story: draft)
       → IR-00N (READY: E/R/D/C inputs + PRD + architecture + success criteria)
        → Experiment E-00N (bench under scripts/bench/, --dry-run → gate → --verify=VERIFIED)
         → Story finalize (experiment_refs + AC four-field set + task↔AC + DoD-NNN)
          → SP-00N (one-sentence goal + story list + capacity + debt)
           → Production (bmad-dev-story / quick-dev: red-green-refactor, code INSIDE scope)
            → QR (docs/quality/) → commit (quality: IR→QR→SP) → PR → deploy
```

The blackboard relay flows in this order: `prd → ux → architecture → spec → create-epics-and-stories → create-story → dev-story`; each skill mirrors on completion (`mirror --key <run-key> --value ... --to <next>`: one call, heartbeat + signal, repeating it does not copy the signal), and the next one takes over with `handoffs --skill <self>` + `consume`. The experiment skill (`bmad-research-experiment`) leaves a signal to `bmad-check-implementation-readiness` through the gate mirror on APPROVED; the dev skill signals `bmad-code-review` when it finishes — that terminal gate is reported as a first-class hop in `chain-health` and the sender signature is written explicitly with `--sender bmad-dev-story` (because it shares the `story.` namespace with create-story).

**Critical ordering notes:**

1. Run the experiment AFTER the story DRAFT and BEFORE the story FINALIZE: you take the scope from the story, then verify `experiment_refs` and close the story.
2. Check IR twice: once after architecture (draft readiness), once after the experiment (the READY decision). Do not open SP without the second one.
3. The architecture produces the draft Code Scope; the experiment narrows and locks it. Never invent a scope for an experiment without architecture.
4. A REJECTED/ADVISORY-BLOCK does NOT rewind the chain — only the experiment link is renewed (a new E record); the PRD/architecture/story drafts stand.
5. Each link's output is the next skill's input: no epic without a PRD, no story without epics, no experiment without a story, no code without an experiment.

### P9 — E came back REJECTED

The gate wrote `REJECTED` → code stays closed. Revise the hypothesis and open a **new record** (E-002); do not try to flip the old record to APPROVED by hand (that produces FORGED). Note "E-001 was rejected because..." in E-002's `Theory` so the chain stays readable. Tell the user which measurement threshold failed (#19).

### P10 — ADVISORY-BLOCK (token is real, code stays closed)

`--verify` exits 2 with `ADVISORY-BLOCK`: small sample (Wilson lower bound below threshold) / `n unknown` / metric MISMATCH. Fix: enlarge the sample (raise the denominator x/y in the bench), match the metric to the record, and re-measure with a **new record**. Do not poke at an ADVISORY record hoping the guard "opens if I push" — it will not; that is the design.

### P11 — FORGED / cross-machine

**Symptoms:** `--verify` → `FORGED`. **Causes:** a gate-written field (claim/measured/command/token) was edited by hand; or the record was signed on another machine.

- If the former: regenerate the record (`--record ... --run <command>`).
- If the latter: the `Re-Measured-By` procedure in #8 — run the same measurement under a new record with your own key and add a plain line to the end of the old record.

Never rewrite the token by hand and never repair a gate-written field. The second one *would* be forging.

### P12 — Getting stuck while writing a story


| Symptom                                      | Cause                                                           | Your action                                                                                                                                                |
| -------------------------------------------- | --------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `experiment_refs` deny                       | E file missing / not APPROVED / not VERIFIED                    | Find it with `ls docs/experiments/E-*.md` + `--verify`; if absent, open an E                                                                               |
| Missing AC metadata                          | the quality gate catches it at commit (hard=deny, soft=warning) | Not being blocked while writing is not a licence to postpone: finish all 4 AC sub-fields (`Experiment/Type/Measured/Verify`) before calling the story done |
| Orphan task (`AC: AC-999` but AC-999 absent) | the task↔AC link broke                                          | Fix it so every task's AC exists                                                                                                                           |
| A DoD item has no `DoD-NNN`                  | an unidentified DoD item cannot pass the audit                  | Add the identifier + `Verify:`                                                                                                                             |


### P13 — Sprint overrun / story blocked

S-031 did not finish inside SP-003: write "✗ NOT COMPLETED (reason)" into the Sprint Review, update velocity, add an action to the Retrospective (owner + what will change). When moving a story to `blocked`, fill the Blocker note (date → owner → status). If the blocker is permanent, update the SP's Blocker+Dependency section and consider returning to IR (Gaps → research mode A/B/C/D + estimated duration).

### P14 — A new research question came up while coding

Do not stop the flow, extend the chain: formulate the question → choose the mode (A if measurable, otherwise B/C/D) → open a new record (`docs/experiments/E-0NN` or `docs/research/`, `docs/design/`) → return to development after approval/review. **B/C/D records do not open code — code requires Mode A.**

### P15 — Bugfix / hotfix

Do not skip the chain because "it's a small fix". Narrow-scope quick E (e.g. only `src/payments/stripe.py`) + bench (regression test) → APPROVED → fix → QR (small, but still: test result + review) → commit → PR (hotfix release type, rollback step). In a hotfix the PR's rollback + kill-switch sections are critical — do not leave them empty.

### P16 — Tech-debt loop

Debt surfaced in QR → add a row to `docs/development/tech-debt.md` + add `// TODO: [TD-XXX] description` in the code + a priority (P0/P1 REQUIRE a target sprint). Put the debt item into the next SP with a time-box. When paid: move it to the Active→Paid table (resolution + sprint + QR id) and delete the TODO. `check-techdebt.sh` (#6b) audits drift/ID/P0/orphan — run it periodically.

### P17 — Incident → PM

Within 24–48 hours of a SEV1/SEV2, write `docs/development/incidents/PM-XXX.md` (blameless: the system, not a person). Timeline (UTC), impact, 5 Whys, detection/response analysis, lessons, actions (owner+deadline+status). Debt coming out of a PM becomes a TD record → next sprint. Record the PM id in the PR's "Deploy Result".

### P18 — Parallel experiments

Several Es can be active at once — the guard checks whether the target file falls inside **any** VERIFIED scope. Keep the scopes narrow and non-overlapping (overlap = it becomes unclear which experiment proved what). Write the relevant E id into the commit message/story.

---

## 7. P19–P27 — Maintenance Playbooks

### P19 — Soft → hard tightening

Once the first VERIFIED scope exists, move the brownfield-start gates from soft to hard:

```toml
[hooks]
code_guard = "hard"
quality_gate = "hard"
deploy_guard = "hard"
# stop_guard: no effect (report-only), kept for compatibility
```

`/metodoloji:audit` #5b shows the live mode. Config is read per call — no reload. After tightening, observe a deny with one `git commit` and one out-of-scope write attempt (negative test).

### P20 — Commit / deploy discipline

- Before every commit: do the done stories have QRs? Do the SP references exist? Is there an IR? (quality order: IR→QR→SP).
- If you see a warning in soft mode, do not say "it passed anyway" and leave the record incomplete — with hard mode the same gap denies.
- Deploy commands are recognised by regex (#9.4). `git push origin main` counts as a deploy — a PR-less push DENIES in hard mode. On purpose.

### P21 — Watching the audit log

```bash
cat .metodoloji/logs/hook-audit.log
tail -10 .metodoloji/logs/hook-audit.log
grep '"tool": "file_editor"' .metodoloji/logs/hook-audit.log
grep 'methodology_warnings' .metodoloji/logs/hook-audit.log
```

The body is redacted to a 300-character preview — do not expect the full content (by design). Separate session boundaries with the `session_stop` markers. "What happened this session?" is answered here: the records between `session_start` and `session_stop`.

### P22 — Local periodic audit

Run locally after a change (CI is release-numbers only — `.github/workflows/release-numbers.yml` verifies the numbers and cuts the tags; it never writes a version or adds a commit, that is the pre-commit hook's job: `git config core.hooksPath .githooks`; auditing is local here). The number is absolute: the patch equals the commits since the first commit, the commit that owns it writes it, **a new minor block opens every 100 commits** (`v0.2.0` is position 100) and a major line opens only by explicit declaration — the rule and the rebuilt tag chain are in `docs/VERSION-HISTORY.md`:

```bash
python -m pytest -q
sh scripts/check-custom.sh
sh scripts/check-plugin.sh
sh scripts/check-methodology.sh
sh scripts/check-techdebt.sh
python scripts/check-handoff.py
```

Suggested order: pytest first (engine behaviour), then check-plugin (the widest static surface), then the rest. `check-methodology.sh` exits 0 with a warning on a fresh install without records — don't panic.

**CHECK 8 (template hygiene):** if a record shows a `> This template is used…` banner, a duplicated `## <type>: <id>` heading, or a placeholder that repeats its own label: **in the template** → ISSUE (fix the source), **in a generated record** → WARN (a written verdict is never silently changed; the next record is copied from the clean template).

Negative tests (proof the gates actually work):

```bash
sh scripts/check-plugin.sh --negtest   # 7 stages: .env/.gitignore, 2 BRIDGE removals (#2b), #1b hooks.json, #6c template, #6d marker, #6e help catalog → all caught and restored
sh scripts/check-custom.sh --negtest   # 3 tests: #3 hard-gate + #7 bridge drift ×2 (#2.3 removal + "bolum N.N" injection)
```

### P23 — Team flow over the blackboard

Carry the PRD → UX → architecture → spec → epics → story → dev relay over the handoff channels (#14). Every morning: `handoffs` (pending), `chain-health` (pending/consumed per hop), `doctor --json` (NEEDS ATTENTION). Keep a live picture with a canvas (document map with `watch docs/` + the skills' `touch` notifications). At session start the PROACTIVE nudge tells you whose baton was not picked up — it announces, it does not consume; the relevant skill `consume`s.

### P24 — Discipline the team with TOML

- Team rule (committed): `custom/{skill}.toml` — e.g. review rigour, risk threshold.
- Personal (gitignored): `custom/{skill}.user.toml` — e.g. language preference.
- No deletion: fork or noop-override instead of removing. After a change, verify the BRIDGE is visible at runtime with `resolve_customization.py -k ...` (that is what #2b looks at).
- Project level: `{project-root}/bmad/config.toml` (team) + `.user.toml` (personal) — configuration without touching the plugin.

### P25 — Updating the plugin

```bash
cd /path/to/metodoloji
git pull
python -m pytest -q && sh scripts/check-plugin.sh
```

If you have a local edit in `hooks.json`, regenerate it with `sync-hooks-json.py --write` (otherwise you get the #1b byte-identical failure — that failure is protecting you).

### P26 — `.env` hygiene (#6a)

`.env` must not be in the repo, `.env.example` must exist, and `.gitignore` must cover `.env`. Audit #6a checks all three. Stage 1 of the negative test proves it.

### P27 — Running the declared workflow (process engine)

**Trigger:** the user wants a multi-stage process ("fix SEO visibility", "run this flow end to end").

Skills know "how to do a step", hooks know "which file was touched"; the layer that knows the **order** is the workflow kernel: an **intent** becomes a declared process and the kernel runs it as a deterministic state machine. The model writes the spec and does the stage's work; **the model does not pick the next stage — the kernel computes it.**

**Why a separate kernel:** the blackboard is a *context network* (key/list/canvas/link/alert) — it has no notion of stage, precondition or transition. `bmad-loop` is an external package that manages dev/review sessions. The process engine stands on its own without inheriting either's semantics.

**How it works:**

- **The spec is data.** `id`, `start`, `stages[]` — each stage declares `evidence` and `next`. The structure is validated first, then executed.
- **Evidence, not claims.** Every non-terminal stage declares its evidence: `artifact` (file + token + minimum count), `command` (argv + expected exit code), `note` (`--note` required). Without evidence, `complete` **refuses** — that is where the "no step was skipped" guarantee comes from.
- **Transitions are computed.** A conditional `next` edge reads a flag (e.g. `regressed=true`); taking that edge **consumes** the flag, so the loop runs once and then falls through to the default edge.
- **The order is mechanical, hooks are informational.** The runtime enforces order with evidence; hooks only report position (the SessionStart line tells you the active run, its stage and the computed next stage). Code writing still depends on the E record's gate — the kernel does not replace the gate.

```sh
python3 {metodoloji-root}/bmad/scripts/workflow.py validate --spec file.json --project-root {project-root}
python3 {metodoloji-root}/bmad/scripts/workflow.py create --spec file.json --project-root {project-root}
python3 {metodoloji-root}/bmad/scripts/workflow.py create --builtin seo-visibility --project-root {project-root}
python3 {metodoloji-root}/bmad/scripts/workflow.py list --project-root {project-root}
python3 {metodoloji-root}/bmad/scripts/workflow.py status --project-root {project-root}
python3 {metodoloji-root}/bmad/scripts/workflow.py next --project-root {project-root}
python3 {metodoloji-root}/bmad/scripts/workflow.py complete --slug seo-visibility --stage analyze --project-root {project-root}
python3 {metodoloji-root}/bmad/scripts/workflow.py flag --slug seo-visibility --key regressed --value true --project-root {project-root}
python3 {metodoloji-root}/bmad/scripts/workflow.py block --slug seo-visibility --reason "no GSC access" --project-root {project-root}
python3 {metodoloji-root}/bmad/scripts/workflow.py resume --slug seo-visibility --project-root {project-root}
```

Every command prints JSON: exit `0` on success, exit `1` on refusal (`ok:false` + an `error` field naming what is missing). State lives under `{project-root}/.metodoloji/workflow/` and is **never edited by hand**.

**Ready-made example — the `seo-visibility` builtin spec:** `analyze` (list problems in `sorunlar.md` with `ISSUE-`) → `map` (bind each problem to code with `MAP:`) → `plan` (score scenarios, pick with `FIX:`) → `apply` (`APPLIED:`) → `test` (`RESULT:`; on failure return to `apply` with the `regressed` flag) → `done`.

Details: `skills/bmad-workflow/authoring.md` (writing specs), `skills/bmad-workflow/running.md` (running a run), process engine code in `bmad/workflow/`.

---

## 8. Record Chain Reference E → IR → SP → S → QR → PR

Open every record **from a template** (`{metodoloji-root}/templates/`), fill its fields, run its gate. The table tells you which record goes where:


| Record | Type   | Gate                                 | Output directory                                                          |
| ------ | ------ | ------------------------------------ | ------------------------------------------------------------------------- |
| **E**  | Mode A | Mechanical gate (`GATE-OK-...`)      | `docs/experiments/E-NNN.md`                                               |
| **IR** | Gate 1 | Are the research records approved    | `docs/development/IR-NNN.md`                                              |
| **SP** | Gate 2 | S-id backlog + capacity + debt check | `docs/development/SP-NNN.md`                                              |
| **S**  | Dev    | Task↔AC match + experiment reference | `docs/development/stories/S-NNN.md`                                       |
| **QR** | Gate 3 | Coverage ≥ 80%, tests/lint clean     | `docs/quality/QR-NNN.md` (canonical; `docs/development/` legacy accepted) |
| **PR** | Gate 4 | Staging + rollback + monitoring      | `docs/development/PR-NNN.md`                                              |


### 8.1 E — Experiment (Mode A, mechanical gate)

**The only legitimate path to code.** Writing code without an experiment is mechanically cut by the guard.

**1. Create the file:**

```bash
cp templates/_template_E.md docs/experiments/E-001.md
```

**2. Fill the mandatory fields (`REQUIRED_DRAFT` — without these the gate will not run):**

```markdown
## Experiment: E-001 — DB index optimisation
- **Date:** 20.08.2026
- **Status:** planned
- **Theory:** On large tables a B+ tree index reduces lookup from O(n) to O(log n)
- **Hypothesis:** H-001: "query_time_ms <= 20"
- **Measurement Metrics:** query_time_ms <= 20
- **Experiment Design:** inputs, procedure, control variables, repeatability
- **Sample Size n:** 40 (informational — the gate parses x/y from the measurement output)
- **Code Scope:** src/db/**/*.py, lib/engine/*.py
```

> **English field labels are MANDATORY** — the gate parses them. Translate them and the gate will not recognise the record.

`Code Scope` glob syntax: `**` any depth, `*` a single segment, `?` a single character; separated by commas/spaces; `none` = an experiment that produces no code.

**3. Run the gate (the gate runs the measurement itself; it does not accept the operator's declared number):**

```bash
python3 {metodoloji-root}/skills/bmad-research-experiment/scripts/run_experiment.py \
  --record docs/experiments/E-001.md \
  --run "python scripts/bench/bench_query.py"
```

Rules:

- The measurement script **must not live on any free surface** (`scratch/`, `tmp/`, `temp/`, `graft/`, `openhands/`, `_bmad/`, `.metodoloji/`, root `explore_*` are forbidden) — put it in a protected directory such as `scripts/bench/`.
- You cannot re-run a record that has a `Decision` — open a new record.
- The `--measured` parameter was removed — reality is mechanical.

**4. Outcome:** `APPROVED` → the guard opens this scope; `REJECTED` → revise the hypothesis and re-measure with a new record (P9).

**5. Verify before writing code:**

```bash
python3 {metodoloji-root}/skills/bmad-research-experiment/scripts/run_experiment.py \
  --verify --record docs/experiments/E-001.md
```


| Exit | Output                 | Meaning                                                                                                                      |
| ---- | ---------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `0`  | `VERIFIED`             | APPROVED + real token — code is allowed inside the scope                                                                     |
| `1`  | `FORGED`               | The token does not match the record's claim/measured/command — invalid                                                       |
| `1`  | `REJECTED` / undecided | Did not pass the gate (or never ran)                                                                                         |
| `2`  | `ADVISORY-BLOCK`       | Token is real but **does not open code**: small sample (Wilson lower bound below threshold), `n unknown`, or metric MISMATCH |


**Dry run (preview without writing a Decision):**

```bash
python3 {metodoloji-root}/skills/bmad-research-experiment/scripts/run_experiment.py \
  --record docs/experiments/E-001.md \
  --run "python scripts/bench/bench_query.py" --dry-run
```

> Do not run `--run` on its own "just to see" format/record checks — you will commit the record to a verdict by accident. Always preview with `--dry-run`.

**Cross-machine:** because the key is machine-local, an `APPROVED` record signed on another machine shows **by design** as `FORGED` on yours — that is provenance information, not necessarily tampering. Do not hand-edit gate-written fields and do not rewrite the token by hand (that *would really* be forging). Instead: run the same measurement under a **new record** with your own key and add a plain line to the end of the old record:

```markdown
- **Re-Measured-By:** E-012 (17.09.2026)
```

`check-plugin.sh` #3 counts this as a `CROSS-MACHINE` warning (not an error) — provided the new record verifies under your own key.

**Field table:**


| Field                 | Mandatory   | Description                                                      |
| --------------------- | ----------- | ---------------------------------------------------------------- |
| `Date`                | Yes         | DD.MM.YYYY                                                       |
| `Status`              | Yes         | planned / APPROVED / REJECTED                                    |
| `Theory`              | Yes         | Which theory/framework ("I was curious" is not enough)           |
| `Hypothesis`          | Yes         | H-NNN: "metric &gt;= threshold" format                           |
| `Measurement Metrics` | Yes         | Metric name + threshold, numeric                                 |
| `Experiment Design`   | Yes         | Input, procedure, control, repeatability                         |
| `Code Scope`          | Yes         | Glob list or `none`                                              |
| `Sample Size n`       | Info        | The gate parses the denominator from the output                  |
| `Measurement Command` | Gate writes | The `--run` command; changing it later breaks the token (FORGED) |
| `Raw Results`         | Gate writes | Measurement output                                               |
| `Uncertainty`         | Gate writes | small sample / none / n unknown                                  |
| `Metric`              | Gate writes | consistent / MISMATCH                                            |
| `Decision`            | Gate writes | APPROVED / REJECTED                                              |
| `Gate Evidence`       | Gate writes | GATE-OK-...                                                      |
| `Next Step`           | Gate writes | Proceed to Code / Return to Theory                               |


### 8.2 IR — Implementation Readiness (Gate 1)

Location: `docs/development/IR-NNN.md`. Status: `READY` | `INCOMPLETE` (legacy Turkish accepted).

Checklist: an approved research record exists (E/R/D/C-id); PRD or story defined; UX + architecture ready where required; success criteria clear/measurable; technical dependencies + risk assessment done; gaps have a plan.

**If there are gaps, the sprint does not start** — you go back to the research wing first.

### 8.3 SP — Sprint Planning (Gate 2)

Location: `docs/development/SP-NNN.md`. Status: `planned` | `in-progress` | `completed` | `cancelled` (legacy `canceled`/Turkish accepted).

Fields: one-sentence sprint goal; story list (S-id + priority + points); capacity (against velocity); tech-debt assessment; blockers + resolution plan; dependencies.

Checklist: the goal is one sentence; every story has an S record; points are realistic; capacity fits velocity; debt is time-boxed; every blocker has a resolution plan.

### 8.4 S — Story

Location: `docs/development/stories/S-NNN.md`. Status: `backlog` | `sprint` | `in-progress` | `review` | `done` | `blocked`.

**1. Frontmatter (mandatory):**

```yaml
---
experiment_refs:
  - id: E-001
    scope: "src/db/**"
    status: APPROVED
---
```

**2. Acceptance Criteria (mandatory sub-fields per AC):**

- `[AC-NNN]` identifier
- `Experiment:` (E-NNN, or `—` on an AC tagged `[HYPOTHESIS]`)
- `Type:` (`agent-verifiable` | `user-evaluable` | `hybrid`)
- `Measured:` (`true` | `false`)
- `Verify:` (verification method)

**3. Technical Tasks:** Every top-level task (`- [ ]`/`- [x]`) must reference an existing AC with `AC: AC-NNN`.

**4. Definition of Done:** Every item must carry a `DoD-NNN` identifier (+ `Verify:`).

**Guard checks (while the story file is being written):**

- `experiment_refs` → records exist + verify; `PENDING`/`REJECTED` → **deny** whatever the mode.
- AC metadata / Task↔AC / DoD / chain checks moved to **commit time** — mid-edit writes to a story are not blocked (the single exception is the `experiment_refs` frontmatter check, which denies on write).

**Generating a record from a native story:**

```bash
python3 scripts/create-methodology-record.py --story docs/development/stories/S-001.md
```

### 8.5 QR — Quality Review (Gate 3)

Location: `docs/quality/QR-NNN.md` (canonical; `docs/development/` legacy accepted). Status: `in-review` | `APPROVED` | `REJECTED` | `REVISED`.

Mechanical (automatic): coverage ≥ 80%; all tests green; linter/formatter clean; security scan clean; no performance regression.

Documentary (manual): code review approval; documentation current; breaking-change migration plan; tech-debt record (`docs/development/tech-debt.md`).

The QR template requires two standard tables (the audit checks them structurally):

```markdown
| AC | Status | Method | Evidence |
| DoD Item | Status | Evidence | Date |
```

Creation:

```bash
python3 scripts/create-qr-record.py --story docs/development/stories/S-001.md
```

### 8.6 PR — Production Readiness (Gate 4)

Location: `docs/development/PR-NNN.md`. Status: `preparing` | `READY` | `WAITING`.

Sections: staging test (deploy, smoke, integration); rollback plan (trigger, step, DB rollback); monitoring/alerting; feature flag (kill switch, gradual rollout); runbook; incident response (contacts, severity, post-mortem template `docs/development/incidents/PM-XXX.md`); deploy window.

Checklist: all mechanical checks PASS; rollback ready+tested; monitoring wired; window known; change approved. After deploy, fill the "Deploy Result" section (metrics, PM id if any).

---

## 9. Hook Engine and Mechanical Gates

What this means for you: **know which gate will inspect a tool call before you make it.** This section is how you diagnose a deny.

### 9.1 hooks.json — 4 entry points, one engine


| Hook              | Matcher                                                    | Policy                                | Timeout |
| ----------------- | ---------------------------------------------------------- | ------------------------------------- | ------- |
| SessionStart      | —                                                          | fail-open (context injection)         | 10s     |
| PreToolUse `pre`  | Write|Edit|MultiEdit|Bash|PowerShell|file\_editor|terminal | fail-closed (guard inside)            | 10s     |
| PostToolUse audit | Write|Edit|MultiEdit|Bash|PowerShell|file\_editor|terminal | fail-open (log-only)                  | 2s      |
| Stop              | —                                                          | fail-open (report-only, never blocks) | 5s      |


The single `pre` entry runs **guard → quality → deploy** in one process (the first deny short-circuits, soft warnings accumulate). Beyond `pre`, the modes are: `guard`, `quality`, `deploy`, `audit`, `stop`, `session_start`.

Each gate keeps its own config key — only the process is shared (it used to be three hook dispatches + three python cold-starts per tool call). Thanks to the union matcher a Claude `Bash` call reaches guard too — a shell write such as `echo x > src/a.py` can no longer sidestep the experiment gate. A normal call takes \~40ms; the 10s ceiling is a hang safety net.

Hook commands locate the plugin root themselves (`$CLAUDE_PLUGIN_ROOT`, `$METODOLOJI_PLUGIN_ROOT`, marketplace cache, OpenHands install dir; the first one containing `hooks/scripts/run-hook.sh` wins). `hooks.json` is **generated** — the canonical dispatch list is `scripts/sync-hooks-json.py` plus the discovery list inside `hooks/scripts/run-hook.sh`. If you change a path, run `python3 scripts/sync-hooks-json.py --write`; `check-plugin.sh` #1b verifies byte-identical.

### 9.2 Guard (PreToolUse) — Fail-Closed, runs first

Scope: `Write`/`Edit`/`MultiEdit` + `Bash`/`PowerShell` + `file_editor`/`terminal`/`notebook_editor` (internally normalised to `file_editor`/`terminal`). `PowerShell` is Claude Code's shell tool on Windows; without it in the matcher, `Set-Content src/a.py ...` writes would never reach guard.

Behaviour:

- Writing a story file (`S-NNN.md` or `N-N-slug.md`): `experiment_refs` is verified (`PENDING`/`REJECTED` → always deny); duplicate record IDs are scanned on new files (not on edits of existing files — duplicates are only born at creation).
- Code target outside the free zone → a **covering VERIFIED** experiment is required. Otherwise `DENY` (hard) or `allow + methodology_warnings` (`code_guard="soft"`).
- `$var`/`${var}` targets in a terminal command are dropped one by one — `$var` is never treated as a literal path, but static targets in the same command are still checked.
- Writing to a story file via terminal/heredoc: if the payload is visible, `experiment_refs` is verified; if it is opaque, you get a "deferred to commit time" warning.
- Secret reference (terminal command or written content) → `DENY`. Content scanning runs **before** the free-zone check — agents cannot bypass it through a zone.

**Code target classification (whitelist — an unknown extension is protected):**


| Category          | Example                                                                        | Protected? |
| ----------------- | ------------------------------------------------------------------------------ | ---------- |
| Code basename     | `Makefile`, `Dockerfile`, `CMakeLists.txt`, `Justfile`, `Taskfile.*`           | ✅          |
| Code directory    | `src/`, `lib/`, `tools/`, `bin/`, `core/`, `app/`                              | ✅          |
| Exec config       | `.github/workflows/*`, `.gitlab-ci.yml`, `docker-compose*.yml`, `package.json` | ✅          |
| Unknown extension | anything not on the non-code list                                              | ✅          |
| Toolchain config  | `prisma/vite/jest.config.*`, `tsconfig*.json`, lockfiles                       | ❌ free     |
| Documentation     | `.md`, `.txt`, `.rst`                                                          | ❌ free     |
| Data/config       | `.json`, `.toml`, `.yaml`, `.csv`, `.log`, `.lock`                             | ❌ free     |
| Media/asset       | `.png`, `.jpg`, `.svg`, fonts, archives                                        | ❌ free     |
| Meta              | `.gitignore`, `README`, `LICENSE`, `.editorconfig`, `.npmrc`                   | ❌ free     |


### 9.3 Quality (PreToolUse) — Config-gated (soft by default), runs second

Scope: only `Bash`/`terminal` containing `git commit`.

Chain: a done story exists but there is no IR at all → deny (Gate 1) → a done story without a QR → deny (Gate 3) → an SP record with a missing reference → deny (Gate 2). Order: IR (project) → QR (story) → SP (story).

**QR-coherence step (quality's 4th step since SP-022):** even with a done story + APPROVED QR, if the story's Definition of Done table still has a `pending`/`⏳` row the commit `DENY`s (hard) or warns (soft) — quality reads the status text in the table, it does not verify tokens. The reason also names the fix: run `python3 scripts/sync-story-qr.py --apply` to sync the past result from the QR back into the story, then re-check coherence (done S + APPROVED QR ⇒ zero pending rows — `bench_sp021.py` pins the tree state, `bench_sp020.py` the operation). Conservative skips: in-progress stories whose QR does not exist yet and unsupported table layouts are not checked (silent pass, not deny).

With `quality_gate="soft"` a deny becomes `allow + methodology_warnings`; with `"hard"` it blocks. Config is read live per call — no reload needed.

### 9.4 Deploy (PreToolUse) — Config-gated (soft by default), runs last

No deploy command → `allow`. If there is one, the same chain plus **PR**: IR → QR → SP → PR. `deploy_guard` soft/hard works exactly like quality.

Recognised deploy commands (regex, case-insensitive): `terraform apply|destroy|plan`, `kubectl apply|rollout|deploy`, `docker (compose) up|deploy`, `ansible playbook|deploy`, `git push origin|upstream main|master|production|prod`, `deploy`.

### 9.5 Audit (PostToolUse) — Fail-open, synchronous

- Appends every call as a single JSON line. The body is redacted to a 300-character preview (`content`, `code`, `source`...); path/command/flags stay intact (so stop works); the full body never enters the log.
- The QR DoD warning is warn-only (the done-story→QR directory scan is in `check-plugin.sh`, not on the per-write hot path). QR DoD is verified with the **same parser/rules** as story DoD (`.utils.dod_issues`): every DoD item needs an identifier + a verification record (in a story `Verify:`; in a QR a `Verify:`/`Evidence:` line, or a result marker `→ ✓ PASS` in a `- DoD-NNN …` bullet, or a filled cell in the `| DoD Item | Status | Evidence | Date |` table).
- Log-only: no blackboard read/write, no canvas/bridge mutation — the engine hot path is blackboard-free.
- A log-write failure is fail-open (stderr note, never a crash).
- It runs **synchronously** (no `"async": true`): with a 2s timeout in fail-open mode there is no functional loss, and it reduces the `InputValidationError: Bash was called with input that could not be parsed as JSON` symptom born of an async PostToolUse race. The real root cause is usually the plugin being registered **twice** in Claude (if you see "2 async PostToolUse hooks completed", clean up the duplicate install: manifest + manual `settings.json` entry + stale marketplace cache).

### 9.6 Stop — Report-only, fail-open (never blocks)

1. Prints a `session_stop` marker into the audit log.
2. Always returns **`allow`** plus a one-line warn-only report: in-progress stories from `sprint-status.yaml` (a file older than the session start is treated as a stale brownfield leftover and ignored) + **this session's** code writes (PostToolUse records after the `session_start` marker; older files are invisible; `$var` targets are dropped) + a PROACTIVE handoff nudge if a signal is pending (read-only peek — it does not consume; the relevant skill completes the handshake).
3. Sprint status search order: `docs/development/native/sprint-status.yaml` → legacy `bmad-output/implementation-artifacts/...` → legacy `_bmad-output/...` → `.metodoloji/sprint-status.yaml`.
4. The `stop_guard` config key remains for backward compatibility but **has no blocking effect**. The old "Stop does not close the session" belief had two real causes: a duplicate Stop registration ("Ran 2 stop hooks" → remove one via `/hooks`) or blaming guard/quality denies on stop. Enforcement happens at write time (guard) and at commit time (quality/deploy).

### 9.7 hook-entry.sh — Single dispatch

```
hook-entry.sh pre      → combined PreToolUse: guard → quality → deploy (fail-closed)
hook-entry.sh guard    → guard only (fail-closed)
hook-entry.sh quality  → quality only (soft/hard)
hook-entry.sh deploy   → deploy only (soft/hard)
hook-entry.sh audit    → audit (fail-open, synchronous)
hook-entry.sh stop     → stop (fail-open, report-only)
```

`hooks.json` only dispatches the combined `pre`; the individual modes exist for direct calls and for the `check-plugin.sh` #1 smoke test.

- Runtime: 2. CLI arg &gt; `METODOLOJI_RUNTIME` env &gt; default `openhands`.
- Python resolver: `python3 → python → py`, then common Windows paths.
- If the engine/Python is missing: pre/guard → `DENY` + exit 2 (fail-closed); quality/deploy/audit/stop/session\_start → silent pass (fail-open).

### 9.8 Bash target detection


| Command/Pattern             | Detected target                   |
| --------------------------- | --------------------------------- |
| `> file` / `>> file`        | Redirect target                   |
| `tee file`                  | Tee output                        |
| `sed -i '...' file`         | Sed target                        |
| `cp src dst` / `mv src dst` | Last argument                     |
| `curl -o file`              | The -o target                     |
| `tar -xf archive`           | Archive contents (bomb-protected) |
| `unzip archive`             | Archive contents                  |
| `git apply patch`           | Patch targets                     |
| `python -c 'open("x","w")'` | The open() target                 |


Archive bomb protection (tar+zip): single file ≤512MB, compressed archive ≤64MB, ≤200,000 members, uncompressed total ≤2GB.

### 9.9 Input normalisation

`normalize_hook_input()`: Claude `Write`/`Edit`/`MultiEdit` → `file_editor` (`file_path` → `path`), `Bash`/`PowerShell` → `terminal` (`cmd` → `command`); OpenHands passes through unchanged. The runtime is inferred from `METODOLOJI_RUNTIME` (the `--runtime=` flag) or the raw tool name. **Known deviation:** an unrecognised tool name normalises to `unknown` and guard **warns**, it does not deny (so an unknown tool cannot hard-block a call).

---

## 10. Free Zone / Protected Area / Secret Scanning

The order guard asks before a write: **scan for secrets → deny if dirty → is it a free zone? → is it non-code? → is there a VERIFIED experiment?**

### 10.1 Free zone (no approval needed)


| Prefix/Directory     | Description                                                                                 |
| -------------------- | ------------------------------------------------------------------------------------------- |
| `_bmad/`             | Legacy module data                                                                          |
| `scratch/`           | Prototype, throwaway script, research note (gapless)                                        |
| `graft/`             | Graft code                                                                                  |
| `.git/`              | Git directories                                                                             |
| `tmp/`, `temp/`      | Temporaries                                                                                 |
| `openhands/`         | OpenHands directories                                                                       |
| `.metodoloji/`       | Plugin state directory                                                                      |
| `docs/*.md`          | Documents under `docs/`                                                                     |
| `docs/*/raw/`        | Raw data                                                                                    |
| `explore_*`          | Root-level exploration FILE (`explore_x.py` is free; `explore_dir/a.py` is protected)       |
| Infrastructure files | `scripts/check-methodology.sh`, `skills/bmad-research-experiment/scripts/run_experiment.py` |


> **Two narrowings (evidenced):** `explore_*` frees only the root **file** — the inside of an `explore_foo/` directory is protected (otherwise the gate could be bypassed with a directory name). Conversely, the gate **refuses to run a measurement from any** guard-free surface: besides `scratch/`, `tmp/`, `temp/`, the benches in `graft/`, `openhands/`, `_bmad/`, `.metodoloji/` and root `explore_*` are rejected too — measurement always lives in a protected directory (`scripts/bench/`).

**Conditional self-modification:** `hooks/`, `scripts/`, `skills/`, `custom/`, `bmad/tests/` (plugin source trees) are free **only when the protected project root is the plugin's own repo** (working on the plugin itself). In an ordinary target project these trees are **protected** — editing plugin source requires an approved experiment too. The `bmad/tests/` exemption is for circularity: the gate's own tests cannot be gated behind the gate's permission.

### 10.2 Protected area (requires approval)

All source code + executable config (the #9.2 table). Note: the gate **refuses to run a measurement script from any free surface** (`scratch/`, `tmp/`, `temp/`, `graft/`, `openhands/`, `_bmad/`, `.metodoloji/`, root `explore_*`) — benches live in a protected directory such as `scripts/bench/`.

### 10.3 Secret scanning (BEYOND the free zone)

Even inside `scratch/` these contents **DENY**: the `.bmad` directory, `gate-key`, `bmad_gate_key`, `gate_token` patterns. `load_secret` / `secret_file` / `secret_env` deny only in an access context (call/assignment/bracket — `load_secret(`, `secret_file =`, `secret_env[`) — a plain mention in prose is free. A `gate-key` / `.bmad` reference in a terminal command → DENY.

---

## 11. Security — Gate Key, Trust Ring, and HMAC

- Location: `~/.bmad/gate-key` (OUTSIDE the repo). Permission 0600 (ignored on Windows). Content is 64 hex (32 random bytes). Lifetime is machine-local; every developer generates their own key.
- **Trust ring (multi-machine development):** signing uses one key (this machine's); verification looks at the ring — your own key plus peer machine keys imported into `~/.bmad/gate-keys/*.key` via `--import-key` (feed the material through the `BMAD_PEER_GATE_KEY` env var, never argv — command history leaks). A token re-derived under any key in the ring is real; the ring does not widen the door a token opens (the claim/measured/command/Code Scope bindings are re-checked under the matching key).
- Every approval is signed with HMAC-SHA256: `GATE-OK-<hash>`. The token is bound to the record's **claim + measured + experiment id + (new type) `Measurement Command`** — editing any of them later breaks the token (FORGED). A legacy record without `Measurement Command` verifies with a legacy token; adding the field and changing the command counts as a downgrade and yields FORGED.
- A FORGED detection → the record is invalid, no code may be written. Two remedies: import the approving machine's key into the ring (`--import-key`) — the record then verifies in place — or regenerate the record (`--record ... --run <command>`).

---

## 12. Skill Catalog — When to Call Which Skill

Families:

- **bmad-**\* (core): PRD, architecture, story, sprint, dev-story/quick-dev/dev-auto, code-review, testarch-*, QA e2e, TEA, retrospective, correct-course, loop-*, eval-runner, forge-idea, brainstorming, research skills.
- **gds-**\* (games): game-brief, GDD, narrative, game-architecture, playtest-plan, performance-test, e2e-scaffold + the rápido dev/test counterparts.
- **wds-**\* (web design): 0-alignment-signoff → 8-product-evolution + the freya-ux / mimir-builder / saga-analyst agents.
- **cis-**\* (creativity): design-thinking, innovation-strategy, problem-solving, storytelling + coach agents.
- **Tool/meta (NO bridge — by design):** `bmad-customize`, `bmad-help`, `memory`, `sync`.

**BRIDGE distribution (33 active):**

- **Producer (17, creates/updates records):** `bmad-dev-story`, `bmad-quick-dev`, `bmad-dev-auto`, `bmad-agent-dev`, `bmad-code-review`, `bmad-create-story`, `bmad-sprint-planning`, `bmad-check-implementation-readiness`, `gds-dev-story`, `gds-quick-dev`, `gds-code-review`, `gds-create-story`, `gds-sprint-planning`, `gds-check-implementation-readiness`, `gds-agent-game-dev`, `gds-agent-game-solo-dev`, `wds-5-agentic-development`.
- **Feeder (16, feeds an existing QR):** `bmad-testarch-*` (atdd, automate, ci, framework, nfr, test-design, test-review, trace), `bmad-qa-generate-e2e-tests`, `gds-test-*` (automate, design, framework, review), `gds-e2e-scaffold`, `gds-performance-test`, `gds-playtest-plan`.
- 3 of them are agent-principles surfaces (`bmad-agent-dev`, `gds-agent-game-dev`, `gds-agent-game-solo-dev` — inside the BRIDGE `[agent].principles`).
- Producer BRIDGEs carry a **VERIFY** step — the LLM must confirm the file exists with `ls -la` right after creating the record (automatic anti-skip check; audit #2c).

**Pick the skill by job:**


| Job               | Skill                                                                                                  |
| ----------------- | ------------------------------------------------------------------------------------------------------ |
| Idea → hypothesis | `bmad-forge-idea`, `bmad-brainstorming`, `bmad-prfaq`, `bmad-product-brief`                            |
| Write a PRD       | `bmad-prd`, `bmad-create-prd`, `bmad-validate-prd`, `bmad-agent-pm`                                    |
| Architecture      | `bmad-architecture`, `bmad-create-architecture`, `bmad-agent-architect`                                |
| Sprint/story      | `bmad-sprint-planning`, `bmad-create-epics-and-stories`, `bmad-create-story`, `bmad-dev-story`         |
| Fast code         | `bmad-quick-dev` (single story), `bmad-dev-auto` (batch), `bmad-agent-dev`                             |
| Quality           | `bmad-code-review`, `bmad-quality-record`, `bmad-testarch-*`, `bmad-qa-generate-e2e-tests`             |
| Deploy readiness  | `bmad-production-readiness`                                                                            |
| Correction route  | `bmad-correct-course`, `bmad-retrospective`, `bmad-teach-me-testing`                                   |
| Game project      | the `gds-*` family (brief→GDD→architecture→story→dev→test→playtest)                                    |
| Web/UX project    | `wds-*` + `bmad-ux`                                                                                    |
| Evaluation        | `bmad-eval-runner` (runs a skill in a clean room: transcript, duration, tokens, persistent run folder) |


---

## 13. TOML Customization — 3 Layers + 8-Layer Config

### 13.1 Three layers per skill (highest priority first)

```
1. {custom}/{name}.user.toml   (personal, gitignored — highest)
2. {custom}/{name}.toml        (team/org, committed to git)
3. {skill-root}/customize.toml (skill default — the base)
→ resolve_customization.py → effective runtime config
```

`{custom}` prefers the project root's `custom/`, otherwise it falls back to the Claude layout `_bmad/custom/`. With the plugin installed, the team layer is the plugin's own `custom/{name}.toml` (user + team in the same directory; merge order defaults → team → user).

Merge rules: for scalars the override wins; tables deep-merge (recursively); for arrays, if all elements carry the same `code`/`id` they merge by key, otherwise they append. **There is NO deletion mechanism** — to remove an item, fork the skill or override it with the same `code`/`id` and a noop description.

Resolution:

```bash
python3 hooks/engine/resolve_customization.py -s skills/bmad-dev-story
python3 hooks/engine/resolve_customization.py -s skills/bmad-dev-story -k workflow.activation_steps_append
python3 hooks/engine/resolve_customization.py -s skills/bmad-dev-story -k agent.name -k workflow.activation_steps_append
```

(The canonical implementation is `bmad/scripts/resolve_customization.py` — stdlib only, py3.11+; `uv run` also works.)

### 13.2 Central config — 8 layers (later ones win)


| Priority    | Layer                                       | Owner                                |
| ----------- | ------------------------------------------- | ------------------------------------ |
| 1 (lowest)  | `{metodoloji-root}/bmad/config.toml`        | installer (produced at install time) |
| 2           | `{metodoloji-root}/bmad/config.user.toml`   | installer                            |
| 3           | `{metodoloji-root}/custom/config.toml`      | plugin team (committed)              |
| 4           | `{metodoloji-root}/custom/config.user.toml` | plugin user (gitignored)             |
| 5           | `{project-root}/bmad/config.toml`           | **project team (committed)**         |
| 6           | `{project-root}/bmad/config.user.toml`      | **project user (gitignored)**        |
| 7           | `{project-root}/docs/config.toml`           | project team, output-local           |
| 8 (highest) | `{project-root}/docs/config.user.toml`      | project user, output-local           |


> Legacy `{project-root}/bmad-output/config.toml` (+ `.user.toml`) is read as a fallback for pre-migration projects — never written.

The project-root layers let you configure the methodology from your own repo without touching the plugin. Team settings go into the committed `{project-root}/bmad/config.toml`, personal settings into the gitignored `.user.toml`:

```toml
# {project-root}/bmad/config.user.toml (personal — not committed)
[core]
user_name = "Ayla"
communication_language = "Turkish"

# {project-root}/bmad/config.toml (team — committed)
[modules.tea]
risk_threshold = "p0"
```

Legacy YAML bridge: module `config.yaml` files from old installs are read as a layer **between** the plugin TOML and the project TOML (it overrides the plugin default and loses to the project TOML). Migrate by moving the values into the project TOML and deleting the YAMLs.

Flat module output (CLI/legacy shape only):

```bash
python3 bmad/scripts/resolve_config.py --project-root . --module tea
```

Returns the shared `core` plus that module's keys (the old module config.yaml shape). `--module core` returns only core. Artifact paths in the plugin layers point the output at `{project-root}` (`docs/`), not inside the plugin.

**That is not the rule for activations:** they ask for the named keys — `--key` (repeatable). `--module tea` is 23 lines / 818 B while a targeted read is 5 lines / 142 B; the module view also carries keys the run never uses and teaches the habit of "run the dump, read what comes back" (see #18 item 50).

### 13.3 Manifesto wiring

Every surface must reference these documents: `research-methodology.md` (all surfaces), `project-context.md` (all surfaces), `development-methodology.md` (the development wing). The bridge document: `{metodoloji-root}/docs/bmad/dev-skill-to-methodology-bridge.md` (#-numbered — `check-custom.sh` #7 verifies that `custom/` references stay in sync with it).

---

## 14. Blackboard — Working Context

One event-sourced JSON: `.metodoloji/blackboard.json` + an append-only event log. Hooks read it for context, skills use it to write focus. Full design: `hooks/engine/modules/blackboard.py` + `bmad/scripts/blackboard.py --help` (the old `docs/BLACKBOARD.md` was pruned — the code is canonical).

**Concepts:** `hot`/`hot_canvas` (the single key + single canvas in focus — surfaced at session start/end); `keys` (named text, max 128, oldest fade); lists (ordered under one key, max 100); canvases (grid/free live surfaces; `watch` file paths and turn a `touch` notification into an auto-cell); `links` (directed graph edges); subscription (glob → channel); alert (bounded, channel-addressed notification — the `session` channel is injected at session start, the `stop` channel at the end); `tags`/`contributions`/`watchers`.

**Invariants:** atomic writes under an exclusive lock; event-sourced (a snapshot may be deleted, the log can rebuild it); bounded (nothing grows without limit); fail-open (a corrupt/missing board never blocks work); exactly one `hot` + one `hot_canvas`.

**The engine's write path is blackboard-free:** hooks do not write to the board. Read-only peeks (fail-open, non-consuming): the PROACTIVE handoff nudge + chain progress at SessionStart (`Chain progress`), the PROACTIVE nudge at Stop. Audit is log-only. Board writes are the job of the skill-side CLI (`blackboard.py`) and the gate mirror (`run_experiment.py --run` processes `E-<id>` + the readiness hand-off automatically after the decision).

**CLI (the memorisation set):**

```bash
python3 bmad/scripts/blackboard.py write --key prd.acme --value "PRD v1: discovery" --type state --hot
python3 bmad/scripts/blackboard.py write --key prd.acme --value "PRD v1: finalize" --type state
python3 bmad/scripts/blackboard.py list-add --key prd.acme.pending --item "Confirm NFR-2"
python3 bmad/scripts/blackboard.py list-remove --key prd.acme.pending --item "Confirm NFR-2"
python3 bmad/scripts/blackboard.py list-clear --key prd.acme.pending
python3 bmad/scripts/blackboard.py canvas create --name doc-map --grid 8x8 --focus
python3 bmad/scripts/blackboard.py canvas set --name doc-map --cell A1 --content "nav hub" --kind decision
python3 bmad/scripts/blackboard.py canvas watch --name doc-map --path docs/
python3 bmad/scripts/blackboard.py canvas touch --path docs/design/spec.md --tool bmad-architecture
python3 bmad/scripts/blackboard.py canvas-read --name doc-map
python3 bmad/scripts/blackboard.py canvas focus --clear
python3 bmad/scripts/blackboard.py link --a prd.acme --b arch.acme --relation informs
python3 bmad/scripts/blackboard.py neighbors --node prd.acme
python3 bmad/scripts/blackboard.py subscribe --watcher session --pattern "prd.*" --channel session
python3 bmad/scripts/blackboard.py alerts
python3 bmad/scripts/blackboard.py consume --channel stop
python3 bmad/scripts/blackboard.py notify --channel stop --kind risk --text "NFR unapproved"
python3 bmad/scripts/blackboard.py mirror --key prd.acme --value "PRD final" --to bmad-ux --note "PRD final — start from NFR-3"
python3 bmad/scripts/blackboard.py handoffs
python3 bmad/scripts/blackboard.py handoffs --skill bmad-ux
python3 bmad/scripts/blackboard.py consume --channel handoff.bmad-ux
python3 bmad/scripts/blackboard.py chain-health
python3 bmad/scripts/blackboard.py doctor
python3 bmad/scripts/blackboard.py tag --tag crm
python3 bmad/scripts/blackboard.py contribute --who bmad-prd --what "PRD v1 draft"
python3 bmad/scripts/blackboard.py stats
python3 bmad/scripts/blackboard.py read --context
python3 bmad/scripts/blackboard.py hot --clear
```

**Handoff handshake (critical):** when upstream work finishes it leaves a signal with `handoff --to <skill>`; the signal waits until downstream looks with `handoffs --skill <self>` and consumes it with `consume --channel handoff.<skill>` — consuming **is** the handshake. While waiting it appears on two announce-only surfaces (which never consume): the session\_start context nudge (delivery chain + methodology\_chain + sub-chains + `bmad-help`; tool-workflow channels excluded) and the stop wrap-up report (methodology\_chain signals). Delivery relay: prd → ux → architecture → spec → create-epics-and-stories → create-story → dev-story.

**Terminal hops (`CHAIN_TERMINAL`):** receivers that consume the relay and pass it nowhere are hops too — the signal waits and is consumed there. If they are not declared, the terminal baton is reported as out-of-chain `extra` noise and the stage that sent it is credited wrongly. That is why `bmad-dev-story → bmad-code-review` is declared explicitly (the `chain-health` row carries `terminal: true`; `chain` = 6 stage hops + this gate). GDS already does this in its own `gds` sub-chain; the hand-written receiver constant in `pending_handoff_channels` was removed.

**Sender signature (`--sender`):** the `story.` working-key namespace is written by **two** stages (create-story opens it, dev-story continues it), so attributing by prefix credits the dev closure to create-story. In a shared namespace like this the closure is signed with `mirror … --sender bmad-dev-story` (the optional field is written into the event record; an invalid value is ignored and falls back to the prefix).

**Sub-chains (phase-based, the `SUB_CHAINS` registry):** phases other than the two canonical relays are defined in one registry and get the same first-class treatment under `sub_chains` in `chain-health` output — `extra` is reserved for genuinely unknown receivers. Adding a phase = adding one row to the registry; `doctor` (pending hops), the session\_start/stop nudge and the static lint follow automatically.

- **discovery** — ideas, research and the CIS facilitators fan into the brief/PRD input (`bmad-forge-idea`, `bmad-brainstorming`, `bmad-prfaq`, the three research skills, four CIS tools (`bmad-cis-design-thinking`, `-innovation-strategy`, `-problem-solving`, `-storytelling`) → `bmad-product-brief`/`bmad-prd`; run-key prefixes `forge.`, `brainstorm.`, `brief.`, `prfaq.`, `research.market.`, `research.domain.`, `research.tech.`, `cis.design.`, `cis.innovation.`, `cis.solving.`, `cis.story.`). A CIS session that stays purely exploratory leaves its hop silent — the signal is sent when a direction worth building on emerges. Discovery skills are **entry points** on the activation side: no hop points at them, so they never promise a peek that can never be satisfied.
- **alt\_dev** — alternative routes of the development stage: `bmad-quick-dev` (`quickdev.`), `bmad-dev-auto` (`devauto.`) and the dev persona `bmad-agent-dev` (`agentdev.`; its BRIDGE carries S→QR→PR) converge on the delivery relay's terminal gate `bmad-code-review`. These are chain **entry points** (no peek, post at close) and do not stamp an E→IR→SP→S→QR→PR position — a branch, not a stage.
- **testing** — the TEA toolbox feeds the quality record's mechanical checks (`bmad-testarch-atdd`, `-automate`, `-ci`, `-framework`, `-nfr`, `-test-design`, `-test-review`, `-trace` and `bmad-qa-generate-e2e-tests` → `bmad-quality-record`; run-key prefixes `test.<tool>.`); TEA Academy (`bmad-teach-me-testing`, prefix `teach.`) routes its graduate into the chain it taught (`bmad-testarch-framework`). A feeder **adds evidence to the existing QR record**, it does not open its own record and does not stamp a methodology position. The only TEA workflow that peeks is `bmad-testarch-framework` — the academy hands off to it.
- **governance** — the hops that close the loop: retrospective (`bmad-retrospective`, prefix `retro.`) returns its testable lessons to the start (`bmad-research-experiment`), while a course correction (`bmad-correct-course`, prefix `change.`) lands on the surface that owns the backlog (`bmad-sprint-planning`) — so even a large re-plan enters the chain from there. Both are chain **entry points** (no peek, post at close) and, because they re-enter an existing relay skill, they neither extend the relay nor stamp a position. Their targets **do** peek: `bmad-research-experiment` consumes the incoming lesson (the return edge is a baton, not a notification) — the `origin` exemption is gone, because a receiving stage must be able to consume.
- **gds** — the games vertical reuses the canonical relay shape with game-native artifacts: idea/research fan into the brief (`gds-domain-research`, `gds-brainstorm-game` → `gds-create-game-brief`), the GDD is the primary design gate and the formal game PRD is an optional alternative (`gds-create-narrative`, `gds-prd` → `gds-gdd` / `gds-game-architecture`), then readiness → planning → story → dev → review (`gds-check-implementation-readiness`, `gds-sprint-planning`, `gds-create-story`, `gds-dev-story` → `gds-code-review`, a terminal gate mirroring the bmad twin). The QA toolbox feeds the QR record of the review session (`gds-test-design`, `-framework`, `-automate`, `-review`, `gds-e2e-scaffold`, `gds-performance-test`, `gds-playtest-plan`), `gds-retrospective`/`gds-correct-course` re-enter planning, `gds-sprint-status` reports live state, `gds-investigate` feeds the course correction, and the dev variants (`gds-quick-dev`, `gds-agent-game-dev`, `gds-agent-game-solo-dev`) converge on the same review gate. Run-key prefixes `gds.<stage>.`.
- **wds** — Freya's design line runs its numbered phases in order: `wds-0-project-setup` → `wds-0-alignment-signoff` seeds the alignment approval, the brief flows through `wds-1-project-brief` → `wds-2-trigger-mapping` → `wds-3-scenarios` → `wds-4-ux-design`, then the build phases: `wds-5-agentic-development` → `wds-6-asset-generation` → `wds-7-design-system` (terminal). Brownfield evolution re-enters from the brief (`wds-8-product-evolution` → `wds-1-project-brief`) — the WDS loop. Run-key prefixes `wds.<phase>.`. Closure signal: GDS stages throw their hop through the `workflow.on_complete` bridge in `custom/<skill>.toml` (stages run with `workflow.md` anchors), while WDS phases carry closure in the `## On Complete` section of their own SKILL.md (shell files have no `on_complete` anchor). None of them stamps a methodology position.
- **Routing personas** (`bmad-agent-pm` / `-analyst` / `-architect` / `-ux-designer` / `-tech-writer`, the six `bmad-cis-agent-*` coaches, three WDS personas `wds-agent-saga-analyst` / `-freya-ux` / `-mimir-builder` and three GDS consultant personas `gds-agent-game-architect` / `-designer` / `-tech-writer` — the same persona template: identity + menu + dispatch) are the session's front door: they take identity, open against the board and route the user into a workflow. They never post (so nothing routes to them either); their mandatory half is the **read** side — an orientation digest before greeting (relay position *and* pending batons in one bounded call; if the digest fails, the fallback is the baton list alone), so the menu is not a generic list but *this* project's real next step. `check-handoff.py` checks the read side each rationale promises as one **bounded** call: routing and reporting via the digest, guest contributors via a single `read --context`. The old two-call read (`read --context` + `handoffs`) can no longer appear in any activation — a label alone is not enough.
- **Contributor (`bmad-advanced-elicitation`, `bmad-party-mode`, `bmad-eval-runner`)** — an in-line technique booster: called *inside* another skill's session, it reads the caller's board context and leaves exactly one `contribute` trace; all other writes are forbidden (focus, run list and closure belong to the caller).
- **Reporter (`bmad-sprint-status`)** — reports project state, so it cannot report from files alone: it reads the board and surfaces a pending baton as live work the plan does not yet know about.
- **Help router (`bmad-help`)** — suggests the next skill; it also reads pending batons (read-only) and puts them ahead of the catalog — a pending signal is live work the map does not yet show.
- **In-line (`bmad-review-*`, `bmad-editorial-review-*`, `bmad-loop-sweep`)** — returns findings to the caller, posts no hop; reviewers do **not** read the caller's board context — the information asymmetry is by design. `bmad-loop-sweep` runs only inside a bmad-loop session and returns machine-readable triage to the orchestrator.
- **Human-decision entry points (`bmad-checkpoint-preview`, `bmad-loop-resolve`)** — called at the user's request and managing a human decision; they open against the board (read-only) before deciding — a decision taken without chain context is a decision taken from a vacuum.
- **Tool (`bmad-agent-builder`, `bmad-document-project`, `bmad-generate-project-context`, `bmad-index-docs`, `bmad-shard-doc`, the builders: `bmad-loop-setup`, `bmad-bmb-setup`, `bmad-module-builder`, `bmad-workflow-builder`, `bmad-customize`; WDS backends: `memory`, `sync`; vertical document tools: `gds-document-project`, `gds-generate-project-context`)** — produces an output with no chain record (an agent skill, a project document, an installed module, a progress file): neither hop nor board contract. The output of the four document/context skills (`project-context.md` etc.) is *read* by many stages — but not as a baton: as a `persistent_facts` file at activation. The registry models that dependency without inventing a signal nobody will consume.
- **Deprecated shims** (`bmad-create-prd`, `bmad-validate-prd` → `bmad-prd`; `bmad-create-architecture` → `bmad-architecture`) carry no chain logic of their own; `check-handoff.py` verifies the shim is DEPRECATED and points at a skill that **is** a chain member.

**Two structural invariants of the lint (above the rationale labels):** (1) *A hop receiver cannot say "nobody hands off to me"* — a receiver labelled `entry point`/`feeder`/`origin` never looks at its channel, so a declared baton waits until it goes stale; also every receiver must be a registered member (the lint never checks an unregistered receiver). (2) *A run-key namespace written by more than one stage must be signed* — because `story.` is written by both sides, the dev closure carries `--sender bmad-dev-story`; without the signature the prefix attribution credits create-story and the terminal hop looks like an undeclared pair. Terminal receivers are declared explicitly too (`CHAIN_TERMINAL`).

**Skill contract (producing skills: PRD, UX, brief, architecture, brainstorming, forge, eval-runner):**

- **Focus (mandatory):** one state key with `write --hot` at activation; refresh the value at milestones; `hot --clear` at close.
- **Run list (mandatory wherever threads emerge):** an open question/pending mock/failing case goes to `list-add --key <run-key>.pending` as work appears, and leaves via `list-remove`/`list-clear` when resolved. An empty list at close = a finished run; anything left open = a handoff signal to the next skill.
- **Canvas (wherever a live picture is needed):** `ux` surface map, `architecture` decision map, `brainstorming` idea board, `eval-runner` round arc; keep cells current, register `watch`, and `canvas focus --clear` at close (leave the canvas standing for downstream to read).
- **Closure check (all):** after clearing focus+list but before posting your own handoff, run `blackboard.py doctor --json` and show the `NEEDS ATTENTION` items to the user.

`custom/config.toml [hooks] blackboard = "on"|"off"` turns skill-side usage on/off; the engine's read-only peek is independent of it.

---

## 15. Command Reference

### 15.1 The four slash commands (you run them on the user's behalf)


| Command                  | What you do                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| ------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/metodoloji:init`       | Install the directory+template skeleton under `{project-root}`. Runs **once** and writes the `.metodoloji/initialized` marker; if the marker exists it short-circuits with "already installed" (use `--force` to reinstall). Never overwrites existing files. Does not copy manifests. Warns when the gate key is missing. Prints a brownfield note. Prints a summary (next step: `/metodoloji:audit`).                                                                                                                                                                      |
| `/metodoloji:gate-setup` | If `~/.bmad/gate-key` exists, say "already installed" and stop (do NOT overwrite). Otherwise run `--init-secret`. Check 0600. **Never** print/copy/move the content. A sample `GATE-OK-...` output = done. Next step: the first E record.                                                                                                                                                                                                                                                                                                                                    |
| `/metodoloji:verify`     | Verify one record: `python3 {metodoloji-root}/skills/bmad-research-experiment/scripts/run_experiment.py --verify --record {project-root}/docs/experiments/<id>.md`. `VERIFIED` = scope open; `FORGED` = regenerate the record; `REJECTED` = revise the hypothesis; `ADVISORY-BLOCK` (exit 2) = re-measure in a new record (it does not open code).                                                                                                                                                                                                                           |
| `/metodoloji:audit`      | Health check: (1) run and summarise `sh {metodoloji-root}/scripts/check-plugin.sh` (exit 0 = HEALTHY). (2) If a custom-focused report is wanted, `scripts/check-custom.sh` (#0–#7). (3) List `{project-root}/docs/experiments/` + `docs/development/` records and note chain gaps (an S without a QR, etc.). (4) Run `--verify` on every E and report the VERIFIED/FORGED split. (5) Read the `custom/config.toml [hooks]` soft/hard values. (6) PASS/FAIL + a fix suggestion. When a bridge is suspect, show break→catch→restore evidence with `check-custom.sh --negtest`. |


### 15.2 Quick command card

```bash
# Setup (init is ONCE per project; later calls no-op)
/metodoloji:init
/metodoloji:gate-setup
/metodoloji:audit

# Experiment
cp {metodoloji-root}/templates/_template_E.md docs/experiments/E-001.md
python3 {metodoloji-root}/skills/bmad-research-experiment/scripts/run_experiment.py \
  --record docs/experiments/E-001.md --run "python scripts/bench/bench.py" --dry-run
python3 {metodoloji-root}/skills/bmad-research-experiment/scripts/run_experiment.py \
  --record docs/experiments/E-001.md --run "python scripts/bench/bench.py"
python3 {metodoloji-root}/skills/bmad-research-experiment/scripts/run_experiment.py \
  --verify --record docs/experiments/E-001.md

# Records
python3 scripts/create-methodology-record.py --story docs/development/stories/S-001.md
python3 scripts/create-qr-record.py --story docs/development/stories/S-001.md

# Audit
python -m pytest -q
sh scripts/check-custom.sh
sh scripts/check-plugin.sh
sh scripts/check-methodology.sh
sh scripts/check-techdebt.sh
python scripts/check-handoff.py

# Customization
python3 hooks/engine/resolve_customization.py -s skills/bmad-dev-story -k workflow.activation_steps_append
python3 bmad/scripts/resolve_config.py --project-root . --key core.communication_language --key modules.tea.test_artifacts
python3 scripts/sync-hooks-json.py --write

# Blackboard
python3 bmad/scripts/blackboard.py write --key prd.acme --value "..." --type state --hot
python3 bmad/scripts/blackboard.py mirror --key prd.acme --value "PRD final" --to bmad-ux --note "..."
python3 bmad/scripts/blackboard.py mirror --key story.1-1 --value "dev complete" --to bmad-code-review --sender bmad-dev-story
python3 bmad/scripts/blackboard.py doctor

# Workflow (process engine)
python3 bmad/scripts/workflow.py list --project-root .
python3 bmad/scripts/workflow.py next --project-root .
python3 bmad/scripts/workflow.py create --builtin seo-visibility --project-root .
python3 bmad/scripts/workflow.py complete --slug seo-visibility --stage analyze --project-root .
python3 bmad/scripts/workflow.py validate --spec docs/workflows/process.json

# Log
tail -10 .metodoloji/logs/hook-audit.log
grep 'methodology_warnings' .metodoloji/logs/hook-audit.log
```

---

## 16. Auditing and Health Checks

```bash
sh scripts/check-plugin.sh        # full audit #0–#6f (0=HEALTHY, 1=problem)
sh scripts/check-plugin.sh --negtest
sh scripts/check-custom.sh        # bridge TOML #0–#7
python scripts/check-handoff.py   # handoff wiring lint
sh scripts/check-methodology.sh   # project record format (warn+exit 0 on a fresh install without records)
sh scripts/check-techdebt.sh      # debt drift/ID/P0/orphan (also runs as #6b)
```

**# map:** #0 gate key · #0b base config project-name leak · #1 engine selfcheck (combined `pre` + individual modes) · #1b hooks.json byte-identical · #2 manifesto wiring · #2b bridge runtime visibility (30 workflow + 3 agent-principles) · #2c bridge VERIFY (13 skills) · #3 approved inventory (VERIFIED/ADVISORY-BLOCK + CROSS-MACHINE warning) · #3b code-scope coverage · #4 documentary records · #5 engine integrity (py\_compile) · #5b hard-gate validity · #5c custom/ static · #6 development format · #6a `.env` · #6b tech-debt · #6c template copy identity · #6d init marker integrity · #6d2 command→script reference integrity · #6d3 plugin version consistency (`.plugin/plugin.json` ↔ `.plugin/marketplace.json` ↔ `.claude-plugin/plugin.json` ↔ `.claude-plugin/marketplace.json` ↔ `pyproject`) · #6e help catalog integrity · #6f trigger/description collision.

**Single-record verification + inventory:**

```bash
python3 {metodoloji-root}/skills/bmad-research-experiment/scripts/run_experiment.py \
  --verify --record docs/experiments/E-001.md
for f in docs/experiments/E-*.md; do
  python3 {metodoloji-root}/skills/bmad-research-experiment/scripts/run_experiment.py \
    --verify --record "$f"
done
```

---

## 17. Troubleshooting — Symptom → Your Action

When you see a deny/warning, do not tell the user "the system is broken": find the symptom, confirm the cause, apply the action, then report.


| Symptom                              | Cause                                                                                  | Your action                                                                                                                                |
| ------------------------------------ | -------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| "No approved experiment record"      | write outside scope                                                                    | Open an E → fill it → run the gate → VERIFIED                                                                                              |
| "Gate key not configured"            | no `~/.bmad/gate-key`                                                                  | `--init-secret` (through the gate-setup path)                                                                                              |
| Everything DENY + exit 2             | Python/engine path broken                                                              | `python3 --version` (≥3.11), `ls hooks/engine/main.py modules/`, `python3 -c "import sys; sys.path.insert(0,'hooks/engine'); import main"` |
| "BRIDGE merge problem"               | TOML deep\_merge did not take / root `customize.toml` missing                          | Look for the BRIDGE in `resolve_customization.py -s ... -k workflow.activation_steps_append` output                                        |
| "Story experiment validation failed" | invalid refs / no E / PENDING-REJECTED                                                 | E existence + APPROVED + `--verify`                                                                                                        |
| ADVISORY-BLOCK                       | small sample / n unknown / MISMATCH                                                    | Bigger sample + matching metric in a new record (P10)                                                                                      |
| FORGED                               | hand edit / cross-machine                                                              | Regenerate, or the Re-Measured-By procedure (P11)                                                                                          |
| Stop report duplicated / odd         | duplicate Stop registration                                                            | Remove one via `/hooks` (stop does not block anyway)                                                                                       |
| JSON parse error (Bash input)        | async race / duplicate plugin                                                          | The synchronous audit is already in place; clean up the duplicate install + stale cache + update Claude                                    |
| Commit deny (hard)                   | missing IR/QR/SP                                                                       | Complete them in order (IR→QR→SP); fix the story metadata                                                                                  |
| Deploy deny (hard)                   | missing IR/QR/SP/PR                                                                    | Complete the PR (staging+rollback+monitoring+approval)                                                                                     |
| Bench rejected                       | on a free surface (scratch/tmp/temp/graft/openhands/*bmad/.metodoloji/root explore*\*) | Move it to `scripts/bench/`, run it in a new record                                                                                        |
| hooks.json mismatch                  | hand edit                                                                              | `sync-hooks-json.py --write`                                                                                                               |


**The "why" the user asks most often:**

- **"What do soft/hard do?"** With the engine running and the chain incomplete: soft = warn+pass, hard = DENY. Guard's `experiment_refs` check denies regardless of mode. Story metadata/chain checks hang off the quality gate at commit. Stop is report-only regardless of mode.
- **"Can I write code in `scratch/` without an experiment?"** Yes — it is free; meant for prototypes/research notes. It cannot go to production; benches are promoted to `scripts/bench/`; secret patterns are forbidden in scratch too.
- **"Can I share my key?"** No — it is machine-local; sharing kills the HMAC.
- **"Parallel experiments?"** Yes — each E opens its own scope and guard looks for a covering one (P18).
- **"The old `bmad-hooks.py`?"** Removed — there is no single file; the modular engine (`hooks/engine/main.py` + `modules/`) is current.
- **"Deleting an item from TOML?"** There is no mechanism — fork or noop-override.

---

## 18. NEVER List — 60 Details That Hurt If Skipped

These 60 items are not "good practice" — they are the system's mechanical boundaries. A call that violates one gets denied or silently fabricates evidence.

**E / Gate (1–15):**

1. English field labels are mandatory — translating them blinds the gate.
2. Never hand-write gate-written fields (`Decision`, `Gate Evidence`, `Next Step`, `Status` lines, `Raw Results`, `Uncertainty`, `Metric`, `Measurement Command`).
3. There is no `--measured` — you cannot declare the number, the gate parses it.
4. A bench must not live on any free surface (`scratch/`, `tmp/`, `temp/`, `graft/`, `openhands/`, `_bmad/`, `.metodoloji/`, root `explore_*`) — use a protected directory such as `scripts/bench/`.
5. Do not re-run a record that has a `Decision` with `--run` — open a new record.
6. Do not run `--run` "just to check" — it writes a verdict; preview with `--dry-run`.
7. `Code Scope: none` means an experiment that produces no code — if you then write code, guard will block; don't be surprised.
8. Scope globs: `**` any depth, `*` a single segment, `?` a single character; separate with commas/spaces.
9. Keep the scope narrow — a wide scope means weak evidence + a large blast radius.
10. Date format is DD.MM.YYYY in E (YYYY-MM-DD in S/IR/SP/QR/PR) — do not mix them up.
11. The hypothesis follows `H-NNN: "metric >= threshold"` with a numeric threshold.
12. Keep raw data under `docs/experiments/E-NNN/raw/`.
13. The token is bound to claim+measured+id+command — editing any of the four yields FORGED.
14. Adding `Measurement Command` to a legacy record and changing the command counts as a downgrade = FORGED.
15. A cross-machine FORGED is provenance; use a `Re-Measured-By:` line + a local new record (never hand-write the token!).

**Story/Sprint (16–28):**

16. The `experiment_refs` frontmatter is checked at write time — PENDING/REJECTED = deny in any mode.
17. An AC's 4 sub-fields (`Experiment/Type/Measured/Verify`) are checked at commit — not being blocked while writing is not a licence to postpone.
18. An AC tagged `[HYPOTHESIS]` may have `Experiment: —`; otherwise, with `experiment_refs` present, `Experiment` is mandatory.
19. Every task needs `AC: AC-NNN` and that AC must exist (no orphans).
20. Every DoD item needs a `DoD-NNN` (+ `Verify:`).
21. A story marked `done` requires a QR record; `review`/`done` require the methodology (S) record; an `SP-NNN` reference requires the SP record.
22. Duplicate record IDs are scanned only at creation — not while editing an existing file.
23. When writing a story via terminal/heredoc with an opaque payload, the check is deferred to commit (do not ignore that warning).
24. The sprint goal is one sentence — a two-sentence goal is scope leakage.
25. Check capacity against velocity; include debt effort in the total (X feature + Y debt = Z total).
26. P0/P1 debt requires a target sprint.
27. The QR home is `docs/quality/` — open new ones there (legacy `docs/development/` is accepted but not canonical).
28. QR AC/DoD tables must be in standard format (`| AC | Status | Method | Evidence |`, `| DoD Item | Status | Evidence | Date |`) — the audit reads them structurally.

**Hook/Security (29–45):**

29. An unknown extension is protected — do not assume "it doesn't know this extension, so it passes"; outside the whitelist = a code target.
30. `package.json` counts as exec-config and is protected; `tsconfig*.json`/lockfiles are free — memorise the distinction.
31. `echo x > src/a.py` hits guard (union matcher) — there is no escaping through the shell.
32. `$var` targets in a command are dropped but static targets are checked — `cat $X > src/a.py` is still caught.
33. Secret scanning runs BEFORE the free-zone check — scratch will not save you.
34. In prose `load_secret` is free, `load_secret(` denies — it looks for a call/assignment/bracket.
35. Do not `cat` the gate key in a terminal or write its content to a file — guard DENIES (and is right to).
36. Do not share the key, put it in the repo, or overwrite it (`--init-secret` will not overwrite an existing key — don't force it).
37. Without the engine, pre/guard DENY + exit 2 — the answer to "why is everything blocked" is usually the Python/engine path.
38. Python below 3.11 = no `tomllib` = the TOML merge dies — check the version.
39. Config is read live — no reload/restart after a TOML edit.
40. Timeouts are ceilings, not budgets (pre 10s, audit 2s, stop 5s; normal \~40ms).
41. Audit is synchronous + fail-open — a slowness complaint is usually a duplicate hook registration, not audit.
42. If you see "2 async PostToolUse hooks completed" there is a duplicate install — clean the manifest + `settings.json` + stale cache.
43. A duplicate Stop ("Ran 2 stop hooks") is cleaned up via `/hooks` — stop does not block anyway, but the report doubles.
44. An unknown tool name becomes `unknown` → warning, not deny — so an unknown tool cannot lock you out.
45. Archive limits (512MB/64MB/200k members/2GB) — the `tar/unzip` target scan has bomb protection.

**TOML/Blackboard/Output (46–60):**

46. There is no deletion in merges — noop-override or fork.
47. Array merge looks at `code`/`id` — arrays without those keys append (watch for duplicate bloat).
48. Personal settings in `*.user.toml` (gitignored), team settings committed — the reverse leaks information or causes conflicts.
49. Legacy `config.yaml` loses to the project TOML — migrate and delete the YAML.
50. Activations read config in a targeted way: `resolve_config.py --key <dotted.path>` (repeatable). `--module tea` (23 lines / 818 B) is a CLI shape only for old installs — it gives the flat view a module sees, but it is not the shape to run; the controller has `check_targeted_config_reads` + `check_no_module_dump_in_skills`.
51. Output rule: record/artifact → `{project-root}`; template/config/script → `{metodoloji-root}`. When in doubt ask "what IS the file?".
52. Manifests are not copied into the project — delete a stale `docs/bmad/` copy.
53. `init` never overwrites — that is why "the template didn't update"; update it by hand if needed.
54. Verify a BRIDGE change with `resolve_customization.py -k workflow.activation_steps_append` — being in the file is not enough, it must be visible at runtime.
55. If the VERIFY step (`ls -la`) in a producer BRIDGE is skipped, #2c catches it — do not create a record and move on without confirming it.
56. The engine hot path is blackboard-free — hook decisions are deterministic/fast; do not expect board writes from a hook.
57. Skill contract: `write --hot` at activation, `hot --clear` + empty run list + `doctor --json` at close.
58. Consuming a handoff IS the handshake — a peek (`handoffs --skill`) looks, `consume` closes; hooks never consume.
59. `read --context` gives a compact summary (the guest contributor's single bounded read that inherits the caller's focus) — do not expect or request a full board dump. At activation, pairing it with `handoffs` is forbidden: relay position and pending batons arrive together in the orientation digest.
60. Change order: change the code → `pytest` (the failing test module tells you which decision moved) → the static checks → bench+E for the methodological record.

---

## 19. Reporting Contract to the User

The user does not want mechanical detail; they want **the state and the next step**. But flattening the system into "it works / it doesn't" is not allowed either.

**Every report carries these 4 lines:**

1. **What was done** — with record ids (E-045, S-027, QR-028...), not vague verbs like "I improved it".
2. **What the gate said** — `APPROVED`/`REJECTED`/`VERIFIED`/`FORGED`/`ADVISORY-BLOCK`, and in which mode (soft warning vs hard deny).
3. **The next step** — the chain's next link (IR/SP/S/QR/PR) or the input you need from the user.
4. **What stayed open** — a gap that passed in soft mode, a pending handoff, `NEEDS ATTENTION` items from `doctor --json`.

**How to say it:**

- **Explain the deny, don't defend against it:** "Guard stopped the write because there is no VERIFIED experiment for `src/auth/**`. I opened E-003; if the measurement threshold holds I will continue writing." A deny is not a failure, it is the system working.
- **Do not hide a soft warning:** if it passed with a warning, say "it passed, but with this gap" — the same gap blocks once hard mode is on.
- **Interpret the exit code:** `--verify` exit `0` VERIFIED, `1` FORGED/REJECTED, `2` ADVISORY-BLOCK (token real, code closed) — report these to the user as three distinct states, not one "error".
- **Do not frame a cross-machine FORGED as an accusation:** "this record was signed on another machine; I need to re-measure with my own key" — that is the design.
- **If it is not finished, do not say "done".** Name the unfinished link and why; a false completion report is worse than a deny.

---

## 20. Reference — Architecture Map, Root Resolution, Glossary

### 20.1 Architecture map

```
metodoloji/
├── .plugin/plugin.json                  # plugin definition
├── .claude-plugin/marketplace.json      # defaultEnabled:false → opt-in
├── hooks/
│   ├── hooks.json                       # SINGLE unified hook manifest (both runtimes find this; generated)
│   ├── engine/main.py                   # entry (pre/guard/quality/deploy/audit/stop/session_start)
│   ├── engine/modules/                  # guard (guard+quality+deploy), audit, stop, plan, mirror, state, config, archive, bash_targets, blackboard, utils
│   ├── engine/resolve_customization.py  # thin re-export (real: bmad/scripts/resolve_customization.py)
│   └── scripts/bootstrap.sh, hook-entry.sh, run-hook.sh
├── skills/                              # 125 BMAD skills (native bodies)
├── custom/                              # 121 TOMLs (33 BRIDGE active) + config.toml (soft/hard)
├── bmad/                                # module data (bmm,cis,gds,wds,tea,core,bmb,loop,_config) + scripts/ (blackboard, workflow, orient, resolve_config, resolve_customization, skeleton)
├── templates/                           # _template_E/IR/SP/QR/PR/S/BD/C + README/tech-debt/scratch-README
├── commands/                            # /metodoloji:init, gate-setup, verify, audit (.md)
├── scripts/                             # check-plugin/custom/methodology/techdebt, check-handoff, create-*-record, sync-*, bench/
├── docs/                                # CLAUDE.md, bmad manifests, record templates (experiments/development/quality/research/design) + native outputs (development/native/)
└── .metodoloji/                         # blackboard.json + logs/hook-audit.log + workflow/ (created on the first init/audit run — never committed)
```

> Note: the old `bmad-output/` directory is legacy — moved, never written. Guard scans for records under `docs/` + `bmad-output/` + `_bmad-output/` + `design-artifacts/` (read fallback, see `guard.py:_RECORD_SCAN_ROOTS`).

### 20.2 Root resolution (memorise it)


| Placeholder         | Real value                                                                                                                                                                                                                                                                                                                                                                                     |
| ------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `{project-root}`    | Target project root (`$CLAUDE_PROJECT_DIR` / `$OPENHANDS_PROJECT_DIR`, else cwd)                                                                                                                                                                                                                                                                                                               |
| `{metodoloji-root}` | Plugin install root (in order: the SessionStart `METODOLOJI active (plugin: PATH)` line — verbatim, do not search → `$CLAUDE_PLUGIN_ROOT` / `$METODOLOJI_PLUGIN_ROOT` if set → the fixed checkout `~/.claude/plugins/marketplaces/metodoloji/` → the versioned cache `~/.claude/plugins/cache/metodoloji/metodoloji/*` (newest first) → OpenHands `~/.openhands/plugins/installed/metodoloji`) |
| `{skill-root}`      | The skill's location inside the plugin                                                                                                                                                                                                                                                                                                                                                         |


**Instrument-as-tool rule:** you *run* a plugin script (`run_experiment.py`, `resolve_customization.py`) from `{metodoloji-root}`; the record it produces is still written to `{project-root}`. What the output *is* decides its root, not which script produced it.

**Manifesto rule:** the `docs/bmad/*-methodology.md` files are plugin-canonical — they are not copied into the project, they are read from the plugin. Delete a leftover `docs/bmad/` copy from an old `init` (stale copies confuse).

### 20.3 Glossary


| Term                                          | Meaning                                                                                  |
| --------------------------------------------- | ---------------------------------------------------------------------------------------- |
| BMAD                                          | Build Methodology for Agent-Driven development                                           |
| E                                             | Experiment (Mode A, quantitative). The ONLY mechanical path to code                      |
| IR                                            | Implementation Readiness (Gate 1)                                                        |
| SP                                            | Sprint Planning (Gate 2)                                                                 |
| S                                             | Story (atomic unit of work)                                                              |
| QR                                            | Quality Review (Gate 3, before commit)                                                   |
| PR                                            | Production Readiness (Gate 4, before deploy)                                             |
| Mode B / D                                    | Qualitative / contextual research → `docs/research/` (documentary, does not open code)   |
| Mode C                                        | Design → `docs/design/` (documentary, does not open code)                                |
| Guard                                         | PreToolUse guard — cuts code writing without a VERIFIED experiment                       |
| Quality                                       | `git commit` chain check (IR→QR→SP)                                                      |
| Deploy                                        | Deploy-command chain check (IR→QR→SP→PR)                                                 |
| Audit                                         | PostToolUse — writes every call into `.metodoloji/logs/hook-audit.log` as a JSON line    |
| Stop                                          | Session-close report — **never blocks** (report-only)                                    |
| BRIDGE                                        | The TOML step that links a native skill output to a methodology record                   |
| VERIFIED / FORGED / REJECTED / ADVISORY-BLOCK | `--verify` outputs (see #8.1)                                                            |
| Fail-closed / fail-open                       | If the engine cannot run: pre/guard DENY+exit 2; quality/deploy/audit/stop pass silently |
| Soft / hard                                   | Engine running but the chain incomplete: soft=warn+pass, hard=DENY                       |
| Free zone                                     | Outside guard inspection (`scratch/`, `docs/`, `tmp/`...)                                |
| Code target                                   | A file under guard protection (whitelist classification)                                 |
| Gate key / token                              | `~/.bmad/gate-key` and the `GATE-OK-...` HMAC signature                                  |
| Blackboard                                    | `.metodoloji/blackboard.json` — event-sourced working context                            |


### 20.4 Maintenance note

This runbook was written for `metodoloji` v0.1.0. As the plugin evolves, cross-check these files: `docs/CLAUDE.md` (engine summary), `docs/bmad/*-methodology.md` (manifesto), `.plugin/plugin.json` + `.claude-plugin/*.json` + `pyproject.toml` (version), `templates/` (current templates), `scripts/` (command names). For the license + BMAD attribution see `LICENSE`.


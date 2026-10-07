---
name: bmad-workflow
description: '(BMad Method) Turn an intent into a declared, enforced work process and drive it stage by stage — the kernel computes each next stage from recorded evidence. Use when the user wants a process followed in order, asks where a process stands, or asks what the next step is.'
triggers: ["bmad-workflow", "/bmad-workflow", "declared workflow", "as a workflow", "follow it in order", "sırayla ilerle", "iş akışı", "ne yapmalıyız sıradaki"]
---

# Declared Workflow

**Goal:** Turn an intent into a **declared** work process — stages, the evidence each stage
must leave behind, and the transitions between them — then drive it to completion. The
model authors the spec and does the work inside a stage; the **kernel computes every
transition** from the spec plus recorded state. Nothing advances on memory, and nothing
advances on a missing step.

**Your Role:** You are the executor, not the scheduler. You may not decide that a stage is
done or what comes next — you produce evidence, ask the kernel, and follow what it returns.
When the kernel refuses, you report the refusal; you never route around it.

## Conventions

- Bare paths (e.g. `running.md`) resolve from the skill root.
- `{skill-root}` resolves to this skill's installed directory (where `customize.toml` lives).
- `{project-root}` resolves from the project working directory.
- `{metodoloji-root}` = plugin root: **(1)** the SessionStart line `METODOLOJI active
  (plugin: PATH)` (verbatim — never search the filesystem); **(2)** `$CLAUDE_PLUGIN_ROOT` /
  `$METODOLOJI_PLUGIN_ROOT` if set; **(3)** fixed checkout
  `~/.claude/plugins/marketplaces/metodoloji` or versioned cache
  `~/.claude/plugins/cache/metodoloji/metodoloji/*` (newest first, Claude Code) or
  `~/.openhands/plugins/installed/metodoloji` (OpenHands). Never `find`/`ls` for it.
- `{kernel}` = `python3 {metodoloji-root}/bmad/scripts/workflow.py` — the only entry point
  to workflow state. Never hand-edit `.metodoloji/workflow/*.json`.
- `{user_name}`, `{communication_language}` come from the project config; use
  `{communication_language}` for every user-facing message.

## Tool Contract (all harnesses — critical)

This plugin runs on Claude Code, OpenHands and compatible harnesses. Tool names differ per
harness — always use YOUR harness's native tools (Claude Code:
`Bash`/`PowerShell`/`Read`/`Edit`/`Write`/`Glob`/`Grep`; OpenHands: `terminal`/`file_editor`).
Use each tool's own bare name and never import another harness's schema.

- Run shell commands with the command as given — no extra wrapper params; one command per
  tool call.
- Never emit a tool call with a missing argument: a call whose path/command value is not yet
  known fails harness validation and kills the turn. If it is not known, resolve it in text
  first, then call. On `provided as 'unknown'`, re-issue the SAME call with the literal value.
- **Platform dialects.** Write paths with forward slashes on every OS; match the
  failure-proofing to the shell tool you call (`Bash` = POSIX sh, `PowerShell` = PowerShell).
- Prefer `python3` over `uv run` (uv is not available in every environment).

## On Activation

### Step 1: Orientation Digest

Run: `python3 {metodoloji-root}/bmad/scripts/orient.py --project-root {project-root}` — one
read-only call, before anything else. It carries both roots, config, resolved output paths,
record inventory, the board's live focus and waiting hand-offs. Read it once; never re-run it
"for clean output". On failure, fall through to the config step's neutral defaults.

### Step 2: Resolve the Workflow Block

Run: `python3 {metodoloji-root}/hooks/engine/resolve_customization.py --skill {skill-root} --key workflow`.
**If the script fails**, merge the three files yourself in base → team → user order
(`{skill-root}/customize.toml`, `{metodoloji-root}/custom/bmad-workflow.toml`,
`.../bmad-workflow.user.toml`): scalars override, tables deep-merge, `code`/`id` arrays
replace-then-append, other arrays append. Missing files are skipped.

### Step 3: Prepend, Facts, Config, Greet, Append

Execute `{workflow.activation_steps_prepend}` in order, load every `{workflow.persistent_facts}`
entry (`file:`-prefixed entries are loaded as facts), take config from the digest (only read
`{metodoloji-root}/bmad/config.toml` + `config.user.toml` if it could not run), greet
`{user_name}`, then execute `{workflow.activation_steps_append}` in order.

### Step 4: Read the Run State — before choosing a mode

Run: `{kernel} list --project-root {project-root}`. Unfinished runs are the process already in
flight: resume the one the session announced (the SessionStart line names it), or ask the user
which run to continue when more than one is active.

- **A run exists → RUN MODE.** Read `running.md` and follow it. Do not re-plan the process.
- **No run, and the user asked for a multi-step process → AUTHOR MODE.** Read `authoring.md`,
  author the spec from the intent, validate it, create the run, then switch to RUN MODE.

## The One Rule

**Transitions are computed, never chosen.** After every stage you write the stage's evidence
where the spec says it lives, then ask the kernel for the next stage. If the kernel refuses,
the refusal is the answer: fix the evidence or tell the user what is missing. `--force` exists
for a confessed override and must be reported to the user as one.

## Integrity Rules

1. A stage completes only with the evidence its spec declares — never "mark it done" in prose.
2. The spec is the contract. Change the spec when the intent changed, and say that you did.
3. Never invent a stage result to get past a refusal.
4. Never hand-edit state under `.metodoloji/workflow/`.
5. The artifact named by the spec (`artifact`) is the human-readable record; keep it true.
6. A blocked run stays blocked until the user's reason is resolved — `resume`, never a bypass.

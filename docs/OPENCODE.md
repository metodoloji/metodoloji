# OpenCode

`metodoloji` ships a third runtime adapter — an [OpenCode](https://opencode.ai)
plugin — alongside the Claude Code and OpenHands ones. It does **not**
reimplement the methodology: every decision still comes from the shared Python
hook engine (`hooks/engine/main.py`). The plugin is a thin TypeScript bridge that
translates the OpenCode V2 plugin API to that engine's JSON contract.

```
OpenCode tool call
      │  execute.before / execute.after / session context / session.idle
      ▼
opencode-plugin/src/index.ts      (TypeScript bridge)
      │  python hooks/engine/main.py --runtime=opencode <mode>   (stdin JSON → stdout JSON)
      ▼
hooks/engine/…                    (the one engine all three runtimes use)
```

Because the engine is the single source of truth, a gate that blocks in Claude
Code blocks identically here. The bridge adds no second opinion, so the
runtimes cannot drift apart.

---

## Requirements

- **OpenCode V2** (the `Plugin.define` / `ctx.tool.hook` API).
- **Python 3** on `PATH` (`python3`, `python` or `py`).
- The **methodology root** reachable — the checkout that owns `hooks/engine/`.
  The bridge finds it from `METODOLOJI_PLUGIN_ROOT`, `CLAUDE_PLUGIN_ROOT`, its own
  location (a checkout install), the Claude Code / OpenHands install dirs, or
  their versioned caches.
  If none resolves, the guard **fails closed** (writes are denied) with the same
  sentence `hooks/scripts/hook-entry.sh` prints.

---

## Install

**From a checkout (recommended while developing):**

```jsonc title="opencode.jsonc"
{
  "$schema": "https://opencode.ai/config.json",
  "plugins": ["./opencode-plugin"]
}
```

**From Git, single repository:**

```sh
opencode plugin add 'github:metodoloji/metodoloji#main::path:opencode-plugin'
```

**From npm (once the package is published):**

```jsonc title="opencode.jsonc"
{
  "$schema": "https://opencode.ai/config.json",
  "plugins": ["metodoloji-opencode-plugin"]
}
```

**Or drop the package into the discovery directory** — no config entry needed:

```
<project>/.opencode/plugins/metodoloji-opencode-plugin/   ← this package's files
```

> The Git/`npm` forms install only `opencode-plugin/`. Point
> `METODOLOJI_PLUGIN_ROOT` at a metodoloji checkout (or install metodoloji for
> Claude Code / OpenHands) so the bridge can find `hooks/engine/`, otherwise the
> guard fails closed.

### What OpenCode's loader actually requires

Verified on **OpenCode v2.0.29**: an entry must resolve to a **directory holding
`index.ts`** (or `index.js`) at its root. That root `index.ts` ships with this
package (`export { default } from "./src/index.ts"`) because OpenCode **ignores
`package.json` `exports`/`main`** for a plugin directory: a package with only
`src/index.ts` is skipped **silently** — no error, no hook, no gate. Keep the
root `index.ts`.

Two more loader behaviours worth knowing:

- **Config entries must be directories.** A file path (`"./src/index.ts"`) is
  rejected with `configured plugin path must be a directory`.
- **On Windows use a relative path** (resolved from the config file) or the
  `.opencode/plugins/` discovery directory. A bare `C:/…` path is read as a URI
  scheme and dropped without a warning, and a junction/symlink plugin directory
  is not always followed.

To confirm the plugin is live, run the server with `--print-logs`; a successful
load prints `msg="loading plugin" id="…/opencode-plugin/index.ts"`. If no such
line appears, the guard is **off** for that project.

---

## What it wires

| metodoloji hook | OpenCode V2 API | Behavior |
|---|---|---|
| `PreToolUse` → `pre` (guard + quality + deploy) | `ctx.tool.hook("execute.before")` | deny = `throw` (the message reaches the model) |
| `PostToolUse` → `audit` | `ctx.tool.hook("execute.after")` | log-only |
| `SessionStart` → `session_start` | `ctx.session.hook("context")` | context injected **once per session** |
| `Stop` → `stop` | `ctx.event.subscribe()` on `session.idle` | report-only, **once per session**, via `ctx.session.synthetic` |
| deny fallback | `ctx.permission.hook("evaluate")` | catches writes `execute.before` may not see |
| custom tools | `ctx.tool.transform` | registers the `metodoloji_verify`, `metodoloji_audit` tools |

### Gate coverage

The write gate covers `write`, `edit` and `patch` (whose input is `patchText`)
plus the shell tool (`shell`). The older `apply_patch` / `bash` spellings are
accepted too. Read-only tools (`read`, `glob`, `grep`, `webfetch`, `websearch`,
`question`, `skill`, `subagent`, …) are allowed without a warning. An unknown
tool name is reported as `unknown` (warn + allow) exactly as on the other
runtimes.

`patch` is gated on **every** file its `patchText` touches — a multi-file patch
that starts in the free zone but writes code is still denied.

---

## Options

```jsonc title="opencode.jsonc"
{
  "plugins": [
    {
      "package": "./opencode-plugin",
      "options": { "permissionFallback": true }
    }
  ]
}
```

- `permissionFallback` (default `true`) — also evaluate the gate through
  `ctx.permission.hook("evaluate")`. This is a belt-and-suspenders layer for
  writes the tool hook may not observe (for example Code Mode nested edits). Set
  it to `false` to rely on `execute.before` alone.

---

## Behavior notes

- **Fail-closed guard.** If the engine cannot run (no Python, missing engine,
  blank output), the write gate denies with the byte-identical sentence from
  `hook-entry.sh`. Audit, session context and stop are fail-open, matching the
  shell adapter's policy.
- **Warnings.** OpenCode's `execute.before` has no `additionalContext` channel,
  so soft-gate / quality / deploy warnings are buffered and flushed into the next
  model request through `session.hook("context")`.
- **One session marker.** The engine stamps a `session_start` marker once per
  session (deduped through `ctx.storage`), so the Stop report window stays
  correct without per-request writes.
- **Loop-safe stop.** The Stop report is published at most once per session; the
  engine also dedupes through the audit log.
- **Cost.** Each tool call spawns the engine twice (`execute.before` +
  `execute.after`) and each model request runs the `context` hook once; the
  discovered root and interpreter are cached after the first call. That overhead
  is the deliberate price of reusing the one engine instead of porting the gates.

---

## Custom tools

- `metodoloji_verify { record }` — verifies an experiment record is APPROVED and
  its gate token is not forged (wraps `run_experiment.py --verify`).
- `metodoloji_audit` — runs the blackboard health check (`blackboard.py doctor
  --json`) for the current project.

Both wrap plugin-owned Python scripts (never a POSIX shell), so they work on
Windows too.

---

## Skills and commands

The 125 skills (`skills/<id>/SKILL.md`) are portable: they carry the `name` and
`description` frontmatter OpenCode needs, and the directory name is the skill ID.
Point OpenCode at the checkout's `skills/` directory:

```jsonc title="opencode.jsonc"
{
  "$schema": "https://opencode.ai/config.json",
  "skills": ["<metodoloji-root>/skills"]
}
```

(Relative entries resolve from the **active working directory**, so use an
absolute path unless metodoloji *is* the working directory. OpenCode also reads
`.claude/skills` / `.agents/skills` automatically.)

The slash commands in `commands/*.md` are **Claude Code format** — they use
`{metodoloji-root}` placeholders and Claude/OpenHands-specific harness notes, so
they are not auto-discovered by OpenCode and are not meant to be copied as-is.
The gate itself never depends on them: every decision comes from the hooks. The
two tools that need a live project (`metodoloji_verify`, `metodoloji_audit`) are
registered directly by the bridge (`ctx.tool.transform`).

---

## Tests

```sh
node --test "opencode-plugin/tests/*.test.ts"     # bridge contract + real engine round-trip
python -m pytest hooks/engine/tests/test_opencode_runtime.py   # engine-side vocabulary
```

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Writes are **never** blocked (the plugin is silently off) | No `msg="loading plugin"` line in `--print-logs`. The plugin directory is missing its root `index.ts`, or the config entry points at a file, or a bare `C:/…` path was dropped. See *What OpenCode's loader actually requires*. |
| Every write is denied with “no python or missing engine” | Python is not on `PATH`, or the methodology root was not found. Set `METODOLOJI_PLUGIN_ROOT` to the checkout. |
| `execute.before` did not block a write | Confirm the tool name is `write`/`edit`/`patch` or the shell tool (`shell`); check `docs/OPENCODE.md` gate coverage. `permissionFallback` adds a second layer. |
| The session never gets context | `session.hook("context")` must be supported by your OpenCode build; the injection is once per session by design. |

The runtime id is **`opencode`** (`python hooks/engine/main.py --runtime=opencode …`).

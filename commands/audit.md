---
description: Run the full methodology audit trail check (plugin integrity, bridge TOMLs, record chain).
---

# /metodoloji:audit — Methodology health check (plugin variant)

Mechanically audits the plugin's own integrity and the record discipline in the target project.

## Steps

1. Plugin integrity: capture the FULL output ONCE to a file, then slice the
   file — never re-run the script per question (each run costs a full pass):
   ```sh
   sh {metodoloji-root}/scripts/check-plugin.sh > .metodoloji/logs/plugin-audit.log 2>&1
   ```
   (the capture path is project-relative — `/tmp` does not exist on Windows;
   `.metodoloji/logs/` is the portable runtime home the hooks already create).
   Then probe the capture with single read-only calls — `grep -E "^\["`
   / `grep -A8 "== 6c)"` when your shell is POSIX, `Select-String` when it is
   PowerShell. Summarize from the file.
   (0 issues = HEALTHY; §5c custom/ static quality check runs inside this script
   as §5c — no need to run it separately. Section headers carry the scope:
   "plugin copies" = `{metodoloji-root}` internals, everything under
   `{project-root}/docs/` = the target project — localize the failure to one
   side before touching anything.)

2. Custom/ bridge TOMLs (only if an audit is requested): run `scripts/check-custom.sh`
   to see §0–§7 in detail. `check-plugin.sh` §5c already runs the same sections; this
   step is only used when a custom/-focused report is wanted.

3. Record chain status: list the records under `{project-root}/docs/experiments/` and
   `{project-root}/docs/development/`; note chain link gaps (e.g. S exists but no QR).

4. Approved experiment inventory: run `--verify` on each E record and report the
   VERIFIED/FORGED distribution (`/metodoloji:verify` logic).

5. Hook configuration: read the `quality_gate`/`deploy_guard`/`code_guard`/
   `stop_guard` values in `custom/config.toml [hooks]` (soft/hard). That file
   is plugin-global policy (every project shares it; the hook engine reads
   only that file — there is no per-project override). Shipped values are
   explicit and win over code fallback defaults (quality/deploy soft,
   code hard when a key is absent). Brownfield projects relax ONLY
   `code_guard` to soft until the first VERIFIED scope exists, then
   re-harden; flag any softened `quality_gate` / `deploy_guard` as
   over-softening.

## Harness note (Claude Code + OpenHands)

Run **one shell command per tool call** with your harness-native shell tool.
Call every tool by the exact bare name your harness lists — if a call is
rejected as unknown, re-issue it with the bare name (prefixed tool names
do not exist). Never emit a call whose command/path is not yet known (it
fails validation with `provided as 'unknown'` — resolve the value in text
first, then call).

**Platform dialects (Windows + macOS).** Write paths with forward slashes on
every OS: `C:/Users/...` works in Bash, PowerShell and Python, while a
`C:\...` path collapses inside a POSIX shell (`ls C:\a\b` reads as `ls C:ab`).
Keep one statement per line (or join with `;`); never newline-separate
statements. Make every probe failure-proof **in the dialect of the tool you
call** — `Bash` is POSIX sh (`2>/dev/null || echo MISSING`), `PowerShell` is
PowerShell (`2>$null`, `;`, and a guarded probe written in PowerShell syntax):
a non-zero exit is reported as a tool error even when the output is what you
needed. Heredoc writes are rejected — use the file editor tool. Resolve
`{metodoloji-root}` / `{project-root}` from the session-start hook context
(forward-slash paths, verbatim — never search the filesystem for the plugin).
Never reference the gate key file in a shell command (`ls`/`cat` of any
`gate-key` path is guard-denied); check gate state via the orient digest's
`gate_key` line or `run_experiment.py --check-secret`.

6. Result report: PASS/FAIL list + fix suggestions. If you find an issue requiring a
   negative test (e.g. bridge cannot resolve), show the script's
   break→catch→restore output; prove the custom/ drift check is live with
   `check-custom.sh --negtest`.

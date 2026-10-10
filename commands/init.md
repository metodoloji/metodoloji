---
description: Install the methodology record skeleton (directories + templates) into the target project. One-time and idempotent.
---

# /metodoloji:init — Install the record skeleton into the target project

This command installs the directories and templates required for the methodology
record chain under **{project-root}**. {metodoloji-root} = this plugin's installation
root, resolved in this order (first hit wins — stop at the first hit, never
probe further):

1. The SessionStart line `METODOLOJI active (plugin: PATH)` — use PATH
   verbatim, never search the filesystem for it. (`$CLAUDE_PLUGIN_ROOT` /
   `$METODOLOJI_PLUGIN_ROOT` are usually EMPTY in slash-command context on
   Claude Code — do not depend on them, do not echo them to "verify".)
2. Fixed checkout `~/.claude/plugins/marketplaces/metodoloji/`
3. Versioned cache `~/.claude/plugins/cache/metodoloji/metodoloji/*`
   (newest first; e.g. `.../0.1.24/` on Claude Code v2.1.x)
4. OpenHands install directory `~/.openhands/plugins/installed/metodoloji/`

(An old `~/.claude/plugins/cache/yunusgungor/metodoloji/*` path appears in
older docs — it does not exist on current Claude Code layouts. Do not probe
it first; it is a last-resort fallback only.)

## Init is one-time

Init runs **once per project**. Its completion is recorded by the marker
`{project-root}/.metodoloji/initialized`, which the SessionStart hook reads: the
marker present means every downstream skill (`bmad-prd`, `bmad-architecture`, …)
must **not** re-run init. Re-running the whole skeleton flow at each step is
wasted work and makes the chain look like it re-initializes the project.

## Steps

0. **Already initialized?** If `{project-root}/.metodoloji/initialized` exists and
   the user did not pass `--force`, do **not** create or copy anything. Say
   "already installed" (report the marker's `initialized_at` / `plugin_version`),
   print the next step (`/metodoloji:audit`), and finish. Run the remaining steps
   only when the marker is missing or `--force` is passed.

1. **Run the installer** — this command is the contract; the script is the
   executable half of it, so the slash command and any other harness
   (OpenHands, a plain terminal) install the same skeleton:

   ```sh
   python3 {metodoloji-root}/bmad/scripts/skeleton.py --install --project-root {project-root}
   ```

    **Precondition — verify the script exists first, with ONE probe.** Check
    `{metodoloji-root}/bmad/scripts/skeleton.py` is a file with a single
    read-only call that can never fail the turn (note the `|| echo` guard —
    every existence probe in this command MUST end with one, otherwise a
    missing path exits non-zero and the harness reports the whole call as
    an error):

    ```sh
    ls {metodoloji-root}/bmad/scripts/skeleton.py 2>/dev/null || echo MISSING-SKELETON
    ```

    If it is **missing**, STOP: the installed plugin copy is stale (a version-skewed
    install — e.g. new `commands/` over an old plugin tree). Do NOT
    improvise a manual `mkdir`/`cp` equivalent: hand copies drift from the
    contract (missed pairs, wrong bytes, marker mismatches) and hide the
    stale install. Instead tell the user to update/reinstall the plugin to
    ≥ the version in `{metodoloji-root}/.claude-plugin/plugin.json` and
    re-run `/metodoloji:init` afterwards. Do NOT go hunting with
    `find / -name skeleton.py` or `ls -dt ~/.claude/plugins/...` spirals —
    at most ONE fallback probe (marketplaces dir, then versioned cache with
    `ls -d ... 2>/dev/null || echo ...`), then stop and report.

   Idempotent, byte-exact copies, never overwrites an existing file (add
   `--force` to replace copies and rewrite the marker deliberately).
   `skeleton.py --status` reports the state without changing anything. Steps
   2–3 below describe what that run does — check them against the script's
   summary rather than hand-copying files, so the two can never drift.

2. The installer creates these directories (skip if they exist):
   - `docs/experiments/` — Mode A experiment records (E-NNN.md)
   - `docs/development/stories/` — story records (S-NNN.md)
   - `docs/quality/` — Quality Record home (QR-NNN.md; canonical per the bridge
     doc and `check-methodology.sh` CHECK 2/5 — `docs/development/QR-NNN.md`
     stays accepted as the legacy location)
   - `docs/research/` — Mode B/D documentary records
   - `docs/design/` — Mode C documentary records
   - `docs/design/prds/` — PRD outputs
   - `docs/design/ux-designs/` — UX design outputs
   - `docs/design/architecture/` — Architecture spine outputs
   - `scratch/` — free zone for exploration code

3. The installer copies these templates (do not overwrite — preserve existing):
   - `{metodoloji-root}/templates/_template_E.md` → `docs/experiments/_template.md`
   - `{metodoloji-root}/templates/_template_BD.md` → `docs/research/_template.md`
   - `{metodoloji-root}/templates/_template_C.md` → `docs/design/_template.md`
   - `{metodoloji-root}/templates/_template_IR.md` → `docs/development/_template_IR.md`
   - `{metodoloji-root}/templates/_template_SP.md` → `docs/development/_template_SP.md`
   - `{metodoloji-root}/templates/_template_QR.md` → `docs/development/_template_QR.md`
   - `{metodoloji-root}/templates/_template_PR.md` → `docs/development/_template_PR.md`
   - `{metodoloji-root}/templates/_template_S.md` → `docs/development/stories/_template_S.md`
   - `{metodoloji-root}/templates/README.md` → `docs/development/README.md`
   - `{metodoloji-root}/templates/tech-debt.md` → `docs/development/tech-debt.md`
   - `{metodoloji-root}/templates/scratch-README.md` → `scratch/README.md`

4. **The init marker is written by `skeleton.py --install`, never by hand.**
    Do NOT run any shell to create `.metodoloji/initialized` yourself
    (no `mkdir -p .metodoloji`, no `VERSION=$(python3 -c ...)`, no
    `echo ... > .../initialized`, no heredocs — heredoc appends are
    rejected by the harness and multi-line shell blocks with unresolved
    `{placeholders}` fail validation with an empty-command error).
    The installer writes `initialized_at` (first-init stamp; a redundant
    run never churns it) and `plugin_version` itself. Read the marker with
    the file-reader tool to report it.

    (Delete the marker, or run `/metodoloji:init --force`, to re-install the
    skeleton deliberately.)

5. Manifestos are plugin-canonical — do NOT copy them into the project.
   The three methodology manifestos (`research-methodology.md`,
   `development-methodology.md`, `dev-skill-to-methodology-bridge.md`) live
   under `{metodoloji-root}/docs/bmad/` and are read from there via the
   `{metodoloji-root}` entries in `custom/*.toml` `persistent_facts`. Do not
   create `docs/bmad/` in the target project. (A legacy copy left over from an
   older init is harmless but stale — remove it to avoid confusion.)

6. Gate key: NEVER probe it with shell (`ls`/`cat`/`echo` of any path
    containing `gate-key`, `gate-keys`, or `bmad_gate_key` is denied by the
    guard — including `ls ~/.bmad/gate-key` — and the denial looks like a
    confusing failure). To check gate state, read the orient digest's
    `gate_key` line or run `/metodoloji:verify` logic
    (`run_experiment.py --check-secret`); never reference the key file in a
    command. If the digest says the key is MISSING, point at
    `/metodoloji:gate-setup` — do not shell-probe further.

7. Existing project (brownfield) note: if the target already has code AND
    no experiment history yet, you MAY propose `code_guard = "soft"` in
    `custom/config.toml [hooks]` so writes warn instead of deny — but ASK
    FIRST and never apply silently, because that file is plugin-global
    policy shared by every project on this machine (the softening leaks to
    all of them). If experiment history already exists (e.g.
    `docs/experiments/E-*.md` present) or the guards are already `"hard"`,
    leave them alone and say so. Tighten `code_guard` back to `"hard"`
    after the first VERIFIED experiment scope exists. Do NOT soften
    `quality_gate` / `deploy_guard` for adoption (they guard
    commits/deploys, not exploration writes), and leave `stop_guard =
    "soft"` alone (report-only key, nothing reads it).

## Harness note (Claude Code + OpenHands)

Run **one command per tool call** with your harness-native shell tool — a call
with several newline-separated statements is rejected. Send single commands,
or chain with `&&` / `;` on one line. **Every probe MUST be failure-proof:**
append `2>/dev/null || echo <WHAT-IS-MISSING>` (or `|| true`) so a missing
path never exits non-zero — the harness reports a non-zero exit as a tool
error even when the output is exactly what you needed (e.g. a trailing
`ls` of a not-yet-created dir fails the whole call).
Heredoc appends (`cat >> file << 'EOF'`) are rejected too: use the file
editor tool for file writes and keep shell calls to single read-only
probes. Call every tool by the exact bare name your harness lists, with complete
arguments — if a call is rejected as unknown, re-issue it with the bare
name — and never emit a call whose command/path is not yet known
(it fails validation with `provided as 'unknown'`; resolve the value in
text first, then call). Always supply all required parameters; never emit
empty invocation blocks. **Write paths with forward slashes on every OS**:
`C:/Users/...` works in Bash, PowerShell and Python, while a `C:\...` path
collapses inside a POSIX shell (`ls C:\a\b` reads as `ls C:ab`). Use the
dialect of the shell tool you call — `Bash` is POSIX `sh` (`2>/dev/null`,
`|| true`); `PowerShell` is PowerShell (`2>$null`, `;` separators, and a
failure-proof probe written in PowerShell syntax).
Slash commands do not expand here — resolve `{metodoloji-root}`
from the session-start hook context (it prints the concrete install path;
use it verbatim, never search the filesystem for it).

8. Print a summary: installed directories, skipped (existing) files, marker
   written (or "already installed"), and the next step (`/metodoloji:audit`
   health check).

# metodoloji — Methodology Plugin (Compatible with BMad Method)

Claude Code plugin for methodology enforcement, compatible with BMad Method.

## What this does

- **Record chain**: E → IR → SP → S → QR → PR (experiment → implementation readiness → sprint planning → story → quality review → production readiness)
- **Mechanical gates**: guard (write/edit blocking, config-gated), quality (`git commit` chain check, config-gated), deploy (deploy-command chain check, config-gated), audit (post-write trail), stop (session-end report, never blocks)
- **125 skills** with **121 customization TOMLs** + `config.toml` (33 active BRIDGEs linking native outputs to methodology records). `bmad-customize`, `bmad-help`, `memory`, `sync` are tool/meta skills with no bridge TOML by design (see check-plugin.sh §2 EXCLUDED / §6b pairing).

## Hooks

All hooks run via `hooks/scripts/hook-entry.sh` which dispatches to the Python engine at `hooks/engine/main.py` (`pre`/`guard`/`quality`/`deploy`/`audit`/`stop`/`session_start` modes; `hooks.json` dispatches the merged `pre` for PreToolUse).

One unified `hooks/hooks.json` serves both runtimes: Claude Code auto-discovers it from
its default hooks location (`./hooks/hooks.json`), and OpenHands auto-discovers the same
file. Matchers are regexes covering both tool vocabularies; hook commands self-locate the
plugin root and dispatch to the same `hooks/engine/` core.

| Hook | Matcher | Policy | Timeout |
|------|---------|--------|---------|
| SessionStart | — | fail-open (context injection) | 10s |
| PreToolUse `pre` | Write\|Edit\|MultiEdit\|Bash\|PowerShell\|file_editor\|terminal | fail-closed (guard inside) | 10s |
| PostToolUse audit | Write\|Edit\|MultiEdit\|Bash\|PowerShell\|file_editor\|terminal | fail-open (log-only) | 2s |
| Stop | — | fail-open (report-only, never blocks) | 5s |

> **One PreToolUse entry, three gates, one process.** `pre` runs
> guard → quality → deploy in order inside a single engine invocation; the
> first deny short-circuits and soft-gate warnings accumulate. Each gate keeps
> its own config key (`code_guard` / `quality_gate` / `deploy_guard`), so they
> remain independent policies — only the process is shared (it used to be
> three hook dispatches + three python cold-starts per tool call).
> The matcher is the union of both tool vocabularies, which also closes a gap:
> a Claude Code `Bash` call now reaches guard, so a shell write such as
> `echo x > src/a.py` can no longer sidestep the experiment gate (it previously
> matched quality/deploy only). `PowerShell` is in the union for the same
> reason: Claude Code exposes it as the shell tool on Windows, and without it
> every `Set-Content src/a.py …` / `Out-File` write ran unguarded. That union also means guard's secret
> protection now applies to `Bash`: a command referencing the gate key
> (e.g. `cat ~/.bmad/gate-key`) is denied where the old per-gate dispatch
> allowed it to slip past guard.
>
> Measured engine latency (bench over direct `main.py` calls, Windows): every
> hook (pre/audit/stop/session_start) p50 ≈ **40–45 ms** — lazy per-mode
> imports (only the matching gate module loads, blackboard never on the hot
> path), a `[hooks]` config parse cache (mtime+size keyed), a resolved
> plugin-root/python cache, and a shell fast lane that skips glob/stat when an
> explicit root is set. `hooks.json` timeouts were trimmed to match
> (SessionStart 10s, Pre 10s, Post 2s, Stop 5s) so a hung hook fails open
> inside the budget instead of being silently killed. The old advisory locks
> (`fcntl`/`msvcrt` blocking) and the `verify_record` thread timeout were
> removed — a hook process is short-lived and the TOCTOU race they guarded
> does not justify the Windows self-deadlock they caused. Verify uses
> `(mtime_ns, size)`-keyed LRU caches for both the HMAC result and
> `record_scope`, so repeated writes in a session do not re-verify unchanged
> records.

### Gate strictness (soft/hard)

`custom/config.toml [hooks]` controls the commit/deploy gates. Values are read live
per-call, so config edits apply without a reload:

```toml
[hooks]
quality_gate = "hard"   # hardened 2026-09-19 (S-005, TD-013 paid)
deploy_guard = "hard"   # hardened 2026-09-19 (S-005, TD-013 paid)
code_guard = "hard"     # hardened 2026-09-19 (S-005, TD-013 paid)
stop_guard = "soft"     # backward compat — stop is report-only and never blocks in either mode
```

- **soft** — a missing IR/QR/SP (quality) or IR/QR/SP/PR (deploy) record
  becomes a warning: `allow` + `methodology_warnings`. Nothing blocks.
- **hard** — the same missing records cause `DENY`.
(Code fallback defaults when a key is absent are soft for quality/deploy — but the shipped values above are explicit `hard` and win. Brownfield adoption softens ONLY `code_guard`, temporarily; `custom/config.toml` is plugin-global.)
- **guard** is fail-closed by default (mechanical): a code/tool write requires an
  approved, scope-matching VERIFIED experiment record. Brownfield projects set
  `code_guard = "soft"` until the first VERIFIED scope exists.
- **stop is report-only and never blocks** (fail-open): it stamps a session_stop
  marker and returns `allow` with a warn-only one-line report of in-progress
  stories, this session's code writes, and the PROACTIVE hand-off nudge for
  waiting `methodology_chain` signals (a read-only peek — never consumed by the
  hook). The deny budget and hook state-machine validation were removed —
  enforcement lives at write time (guard) and commit time (quality/deploy
  gates), so closing the loop can never wedge the session. Never duplicate the
  Stop registration
  (plugin manifest + manual `settings.json` entry = "Ran 2 stop hooks").
- **Story metadata / chain validation runs at commit time**, not on every write:
  editing a story file never blocks; `git commit` checks AC metadata, orphan
  experiment links, and the methodology chain for every `S-*.md`, gated by
  `quality_gate` (hard → deny, soft → warn). The `experiment_refs` frontmatter
  check is the exception — it always denies at write time.
- **QR-table coherence is quality's fourth step** (SP-022): a done story with an
  APPROVED QR but a stale `pending`/`⏳` row left in its Definition of Done table
  denies `git commit` (hard) or warns (soft) — quality reads the table's status
  text, never verifies tokens. The deny reason names the remedy:
  `python3 scripts/sync-story-qr.py --apply`, then re-verify coherence
  (done S + APPROVED QR implies zero pending rows). In-progress stories whose QR
  is not born yet and unsupported table layouts are conservatively skipped.
- **The engine write path is blackboard-free**: no hook ever WRITES the
  board (the pre gate's relay peek writes only its own throttle state file,
  `.metodoloji/relay-note.state` — never the board). Board contact is
  read-only peeks (fail-open, never consume): the handoff-channel peek
  behind the PROACTIVE nudge (stop/session_start), the freshest-batons peek
  the merged `pre` gate ships mid-session through PostToolUse
  (`mirror.pending_note`, throttled), and the chain-progress peek
  (`compact_context` methodology values) in session_start. The blackboard
  mutation surface stays a skill-side CLI concern (`blackboard.py`, plus the
  gate's automatic decision mirror in `run_experiment.py --run`), keeping
  hook decisions deterministic and fast.
- Two independent layers: the fail-open/fail-closed column above is what happens when
  the **engine cannot run** (pre/guard deny + exit 2; quality/deploy/audit/stop pass
  silently), while soft/hard is what happens when the engine runs but the **record
  chain is incomplete**.

## Commands

- `/metodoloji:init` — install the record skeleton (one-time; writes `.metodoloji/initialized`).
  Any harness without slash commands runs the same installer directly:
  `python3 {metodoloji-root}/bmad/scripts/skeleton.py --install` (`--status` to only report)
- `/metodoloji:gate-setup` — generate `~/.bmad/gate-key` (machine-local, not committed).
  Multi-machine development: import a peer machine's key into the trust ring
  (`~/.bmad/gate-keys/`) with `run_experiment.py --import-key` (key material via the
  `BMAD_PEER_GATE_KEY` env var, label via `BMAD_PEER_LABEL`) — records APPROVED on that
  machine then verify here; signing always uses this machine's own key
- `/metodoloji:verify` — verify an experiment record against this machine's trust ring
  (own key + imported peer keys). Outcomes (exit codes):
  `VERIFIED` (0 — APPROVED with genuine token; the guard opens the record's `Code Scope`),
  `FORGED` (1 — token does not match the record's claim/measured/measurement-command
  under any trusted key),
  `REJECTED` or undecided (1 — did not pass the gate),
  `ADVISORY-BLOCK` (2 — token genuine but does **not** unlock code: small sample
  (Wilson bound below threshold), `n unknown`, or metric MISMATCH)
- `/metodoloji:audit` — run full audit trail check

## Declared workflows

A process that must not skip a step is a **workflow**: a JSON spec (stages,
per-stage evidence, computed transitions) that the kernel runs as a state
machine. The model authors the spec and does the work inside a stage; the kernel
sets the order and refuses a stage whose evidence is missing. Entry point:
`python3 {metodoloji-root}/bmad/scripts/workflow.py <validate|create|list|status|next|complete|block|resume|flag|history> --project-root {project-root}`,
skill `bmad-workflow`, design in `docs/WORKFLOW.md`. The SessionStart line names
the active run's stage and its computed next stage; state lives under
`{project-root}/.metodoloji/workflow/` and is never hand-edited.

## Orientation (start here)

```sh
python3 {metodoloji-root}/bmad/scripts/orient.py --project-root {project-root}
```

Read-only digest: both roots, resolved core + module output paths, board focus
(hot key, `status`/`scope`, chain progress, a `STALE` flag when a live run
still claims `complete`, and a `STALE VERDICT` flag when a negative gate verdict
(`IR`/`PR`) predates the artifacts it judged — with the superseding path, its
age and the re-run skill named), waiting batons with age, the record inventory, the
parsed sprint line (story/epic split + epic-lag flags) with sprint-derived NEXT
steps (review story → code-review, epic lag → roll-up + retro, next ready
story → dev-story), skeleton and gate-key state, the catalog's ambiguous
menu codes, and the **MCP server inventory** (`state.mcp_inventory()` — the
harness configs' configured servers, read-only, never spawned; enabled ones
surface as a steering NEXT step: prefer the tools that exist for the work they
serve; when none are configured, offer to add one only if the work clearly
fits). Its records/sprint/mcp sections share one parser (`hooks/engine/
modules/state.py`) with the session-start inject and stop's story scan, so the
surfaces cannot disagree. Prefer it over a
full `resolve_config.py` dump: that output is large enough to arrive truncated,
which is what made a real session re-run it repeatedly. It is activation step 1
for every skill that starts a run — the E → IR → SP → S → dev → review → QR → PR
stage openers, the governance openers, the planning chain, the discovery entry
points, the persona front doors and the human-review entry point — 80 run
openers and front doors across the BMad, GDS and WDS verticals (`DIGEST_FIRST`
in `scripts/check-handoff.py`). For a router or persona the digest *is* the
board grounding, so it replaces the raw `read --context` + `handoffs` pair; a
guest contributor that only needs the invoking run's focus keeps the single
bounded `read --context`. Config beyond the digest: `resolve_config.py --key
<dotted.path>` — no dump of any kind, the module-scoped one (`--module`)
included: `--module tea` is 23 lines / 818 B here against 5 lines / 142 B for a
targeted read, and the unbounded dump is every key of every layer (~13.7 KB).
`check_targeted_config_reads` enforces the named-keys rule on every activation,
`check_bounded_board_reads` refuses the old board pair, and
`check_no_module_dump_in_skills` refuses a dump anywhere in the shipped package
(measured in `docs/research/B-001`).

`{metodoloji-root}` is named on the SessionStart line (`METODOLOJI active
(plugin: PATH)`) and in `$CLAUDE_PLUGIN_ROOT` / `$METODOLOJI_PLUGIN_ROOT` — never
search the filesystem for it.

## Installation (Claude Code)

The plugin ships a marketplace manifest at `.claude-plugin/marketplace.json` with
`defaultEnabled: false` on the plugin entry (and the same flag in the plugin manifest):
the fail-closed guard hooks are opt-in.

```sh
/plugin marketplace add ./      # or the GitHub repo URL
/plugin install metodoloji@metodoloji
claude plugin enable metodoloji # defaultEnabled: false → enable explicitly
```

Once per project: `/metodoloji:init` (writes `.metodoloji/initialized`; later invocations short-circuit as already installed) and `/metodoloji:gate-setup` (machine-local, once per machine). Skills must not re-run init — the SessionStart hook reads the marker and stops advertising it.

## Requirements

- Python 3.11+ (python3, python, or py on Windows) — stdlib `tomllib` is required by the TOML merge
- Git Bash or POSIX-compatible shell (sh) for the hook scripts; the agent-side
  shell may be `Bash` (POSIX) or `PowerShell` (Windows)

## Cross-platform notes

- Bootstrap auto-detects python via `python3 → python → py` fallback chain
- Roots injected into agent context are forward-slash on every OS (`C:/Users/...`),
  the one path form Bash, PowerShell and Python all accept; the bootstrap also
  normalizes `CLAUDE_PROJECT_DIR` before filesystem use, because a native
  `C:\...` path collapses inside a POSIX shell
- The hook matcher covers **both** shell vocabularies (`Bash` and `PowerShell`)
  so a Windows session cannot pick a lower enforcement level by tool choice
- Windows paths are converted via `cygpath` when available under Git Bash
- `chmod 600` on gate-key is silently ignored on Windows (no POSIX permissions)
- `.sh` files are normalized to LF line endings via `.gitattributes`
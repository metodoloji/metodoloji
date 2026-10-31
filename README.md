# metodoloji

![version](https://img.shields.io/badge/version-0.2.11-0b7285?style=flat-square) ![license](https://img.shields.io/badge/license-MIT-2ea44f?style=flat-square) ![python](https://img.shields.io/badge/python-3.11%2B-3776ab?style=flat-square) ![tests](https://img.shields.io/badge/tests-1%2C400%2B-4c1?style=flat-square) ![runtimes](https://img.shields.io/badge/runtimes-Claude%20Code%20%7C%20OpenHands%20%7C%20OpenCode%20%7C%20Hermes-555?style=flat-square)

**A methodology plugin that applies approval gates to coding-agent actions through supported runtime hooks.**

For **Claude Code**, **OpenHands**, **OpenCode**, and **Hermes**: a 6-stage record chain (`E → IR → SP → S → QR → PR`), hooks that gate writes/commits/deploys using approved records, declared structural scope, and configured policy, 129 skills, a blackboard that carries context across sessions, and a declared workflow engine. Compatible with the [BMad Method](https://github.com/bmad-code-org/BMAD-METHOD).

---

## Contents

- [What you get](#what-you-get)
- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [The five commands](#the-five-commands)
- [Two rules that save you pain](#two-rules-that-save-you-pain)
- [Verify your install](#verify-your-install)
- [Project layout](#project-layout)
- [Documentation](#documentation)
- [Contributing](#contributing)
- [License](#license)

---

## What you get

- **A mechanical experiment gate.** Code is written only from a record that the gate itself measured and signed (`APPROVED` + a `GATE-OK-...` token). No record, no write — `DENY`, not a lecture.
- **A chain that unlocks itself.** `E → IR → SP → S → QR → PR`: research, readiness, sprint, story, quality, production readiness. Each link checks the one before it, on commit and on deploy.
- **129 skills, 121 TOMLs, 33 BRIDGEs.** PRD, architecture, story, sprint, dev, code review, test/QA, retro and correction workflows for product, game and web/UX projects — customized per skill in up to three TOML layers.
- **Context that survives sessions.** The blackboard (`write --hot`, run lists, handoffs, canvases) carries focus and pending batons between skills and across windows.
- **One manifest, two shell runtimes.** A single `hooks/hooks.json` serves both Claude Code and OpenHands; hook commands self-locate the plugin, and the engine fails closed when it cannot run.
- **Four runtimes, one decision engine.** OpenCode and Hermes join Claude Code and OpenHands through adapters that call the same Python hook engine. The decision logic is shared; tool coverage and interception points differ by runtime. See [`docs/runtime-security-coverage.md`](docs/runtime-security-coverage.md), [`docs/OPENCODE.md`](docs/OPENCODE.md), and [`HERMES.md`](HERMES.md).
- hook engine (main.py + modules/): pre, guard, quality, deploy, audit, stop, session_start.
- **Declared workflow engine.** Multi-stage processes (`analyze → map → plan → apply → test → done`) run as a deterministic state machine that refuses a stage without evidence.

## How it works

```
  E ──────── IR ──────── SP ──────── S ──────── QR ──────── PR
  experiment readiness   sprint     story      quality     production
  measured by the gate   inputs     tasks      ≥80% cov    rollback
      │
      └─── guard: only a VERIFIED experiment opens a code scope
```

Three hooks guard the three moments that matter:

| Hook | Watches | Behaviour |
|---|---|---|
| **guard** | Adapter-mapped write and shell calls (runtime-specific; see [coverage matrix](docs/runtime-security-coverage.md)) | No covering VERIFIED experiment → `DENY` in hard mode; warn in soft mode |
| **quality** | A mapped terminal command recognized as `git commit` | Chain check `IR → QR → SP`, plus story metadata and QR coherence |
| **deploy** | A mapped terminal command matching the deploy-command rules | Chain check `IR → QR → SP → PR` |

What it looks like in practice:

```text
# the agent wants to write code, but no experiment covers it
Write src/auth/jwt.py
  → guard: DENY — "No approved experiment record"
  → the agent opens E-001, runs the gate, gets APPROVED, re-verifies
  → the same write now passes, and only for the approved scope
```

`Stop` is **report-only** — it summarises the session (in-progress stories, this window's writes, a pending handoff) and never blocks.

### Cross-session memory and baton handoffs

The blackboard carries focus, run lists, and pending handoffs across skills and windows so context is preserved when you restart your terminal:

![Session-Persistent Blackboard and Baton Handoff](docs/assets/launch/card3-blackboard-handoff-en.jpg)

### Declared workflow engine

Multi-stage processes (`analyze → map → plan → apply → test → done`) run as a deterministic state machine that refuses transitions without verifiable evidence (`command exit 0` or artifact tokens):

![Declared Workflow Engine](docs/assets/launch/card4-workflow-engine-en.jpg)

## Requirements

| Requirement | Minimum | Note |
|---|---|---|
| Python | 3.11+ | `tomllib` for the TOML layers; the engine resolves `python3 → python → py` |
| OpenHands / Claude Code | current | plugin + hook API support |
| OpenCode | v2 | third runtime — a TypeScript bridge (`opencode-plugin/`) spawns the same Python engine |
| Hermes Agent | current | fourth runtime — `plugin.yaml` + `pre_tool_call`, gates run in the gateway process ([HERMES.md](HERMES.md)) |
| Git | 2.x | record and commit gating |
| Shell | POSIX `sh` | Git Bash is enough on Windows |

The Python engine currently runs from the plugin checkout; this repository is
not published as an installable Python library. `pyproject.toml` carries project
metadata and test configuration. OpenCode's npm package is a runtime bridge and
still needs access to the methodology checkout that contains the Python engine.

## Quick start

You don't operate this plugin by hand — you **ask your agent**. Install once, then prompt.

**1. Install** (Claude Code):

```bash
/plugin marketplace add https://github.com/metodoloji/metodoloji
/plugin install metodoloji@metodoloji
```

<sub>or OpenHands:</sub>

```python
from openhands.sdk.plugin import install_plugin
install_plugin("github:metodoloji/metodoloji")
```

<sub>or OpenCode (V2):</sub>

```jsonc
// opencode.jsonc — a checkout keeps hooks/engine/ reachable
{ "plugins": ["./opencode-plugin"] }
```

> The Git/npm forms install only `opencode-plugin/`; point
> `METODOLOJI_PLUGIN_ROOT` at a metodoloji checkout so the bridge finds the
> engine. Details: [`docs/OPENCODE.md`](docs/OPENCODE.md).

<sub>or Hermes Agent:</sub>

```bash
hermes plugins install metodoloji/metodoloji
hermes plugins enable metodoloji
```

> The plugin ships `plugin.yaml` + `register(ctx)` at the repo root, so the
> Git install is the whole package — 5 tools, 6 hooks, the full skill catalog
> and 6 slash commands. Details, limits and the fail-closed contract:
> [`HERMES.md`](HERMES.md).

**2. Set up your project** (once):

> Run `/metodoloji:init`, then `/metodoloji:gate-setup`, then `/metodoloji:audit`. After a plugin update, init can be re-run safely to add newly shipped directories/templates; it never replaces existing project files.

The agent installs the `docs/` record skeleton (one time, never overwrites), creates your machine-local gate key (one time per machine, never printed), and reports project health.

**3. Describe what you want built:**

> Add JWT authentication to the API under `src/auth/`.

The agent picks the skills, writes the experiment, and runs the measurement gate. Product code requires a verified scope; measurement benches can be staged under an undecided record, while non-measurement structural work must use a declared structural scope. If a write is denied, the guard returns the reason so the agent can correct the record or scope.

**4. Keep going through the chain:**

> Continue with IR, sprint plan, story, then implement. Open a QR before committing.

The agent advances `E → IR → SP → S → code → QR → commit → PR → deploy`, verifying each link.

## The five commands

| Tell the agent | What happens |
|---|---|
| `/metodoloji:init` | Installs the `docs/` skeleton + templates; safe re-runs add only missing assets and preserve existing files |
| `/metodoloji:gate-setup` | Creates the machine-local gate key (one time per machine; never shared, never in the repo) |
| `/metodoloji:verify` | Checks one experiment record: `VERIFIED` (code allowed), `FORGED`, `REJECTED` or `ADVISORY-BLOCK` |
| `/metodoloji:audit` | Full health check: plugin integrity, bridge wiring, record-chain gaps, gate modes |
| `/metodoloji:add <source>` | Analyzes a repository URL, docs site, or local PDF/Markdown source against this project and registers a complete `docs/research/external/<slug>/context.md`; the analysis is agent-led, scaffold/validation/indexing are mechanical |

**How a task flows:** you state the goal → the agent researches and plans with skills (`bmad-prd`, `bmad-architecture`, `bmad-sprint-planning`…) → it opens an experiment, measures, gets `APPROVED` → implements inside the verified scope → records the quality review → commits. Multi-step work is carried over the blackboard (`write --hot`, run lists, handoffs), so context survives across sessions.

## Two rules that save you pain

- **Never touch the gate key.** Don't print, copy, or `cat` it — the guard denies key references on purpose. Key problems are solved with `/metodoloji:gate-setup`, not with the shell.
- **`FORGED` on a teammate's record is normal.** Keys are machine-local by design; each machine re-runs the measurement under its own record instead of sharing keys.

And one clarification: methodology **output** (records, artifacts) is written to *your* project under `docs/`; the plugin itself is never written to while it runs in someone else's project.

## Verify your install

Every check runs offline, with no credentials:

```bash
python -m pytest -q                # 1,400+ tests: hook engine, bridges, skills
sh scripts/check-plugin.sh         # plugin structure audit (#0–#6h)
sh scripts/check-custom.sh         # bridge TOML static audit
sh scripts/check-methodology.sh    # record format audit
python scripts/check-runtime-coverage.py  # adapter-to-documentation drift check
sh scripts/check-techdebt.sh       # tech-debt inventory audit
python scripts/check-handoff.py    # handoff wiring lint
python scripts/check-triggers.py   # trigger/description collision audit
```

CI runs two separate workflows. `.github/workflows/quality.yml` runs the Python suite and plugin/record audits on pull requests; the OpenCode bridge tests run in their own Node job. `.github/workflows/release-numbers.yml` remains release-number only: it writes no version and creates no commit — the number is written into the commit that owns it by the committed pre-commit hook (install once per clone: `git config core.hooksPath .githooks`), and the pipeline verifies each pushed commit's tree against the number its position owns, then publishes `vX.Y.Z`. A wrong number is a red job naming the number it should be, never a bot commit. The number follows one rule: on the automatic line the patch is the count of commits since the first commit (`0.1.<n>`, absolute — derived from git), and a new minor block opens every 100 commits. A major line (`1.0.0`) opens only by explicit human declaration. The history and tag corrections are in [`docs/VERSION-HISTORY.md`](docs/VERSION-HISTORY.md). Negative gate checks remain available locally with `check-plugin.sh --negtest` and `check-custom.sh --negtest`.

## Project layout

```
metodoloji/
├── hooks/engine/        # shared hook engine; gate policy and blackboard domains are split into modules
├── hooks/hooks.json     # generated dispatch manifest (sync-hooks-json.py --write)
├── skills/              # 129 skills (native bodies)
├── custom/              # 121 TOMLs (33 BRIDGE active) + config.toml (soft/hard)
├── bmad/                # module data (bmm, cis, gds, wds, tea, core, bmb, loop) + scripts/
├── templates/           # _template_E/IR/SP/QR/PR/S/PM/BD/C/STRUCT + README/tech-debt/scratch-README/external-context-README/external-context
├── commands/            # /metodoloji:init, gate-setup, verify, audit (.md)
├── scripts/             # static checks, record generators, bench harness, sync-hooks-json
└── docs/                # engine notes, bmad manifestos, record templates, native outputs
```

## Documentation

See [Architecture and failure boundaries](docs/architecture.md) for module
ownership, mutation guarantees, and runtime validation limits.

| Guide                              | Audience                                                                   |
| ---------------------------------- | -------------------------------------------------------------------------- |
| [KILAVUZ.md](KILAVUZ.md)           | Agent runbook, Turkish (canonical — 21 sections, 27 playbooks, 60 details) |
| [GUIDE.md](GUIDE.md)               | Agent runbook, English mirror of KILAVUZ.md                                |
| [AGENT-GUIDE.md](AGENT-GUIDE.md)   | Prompt-driven usage guide — every scenario as a copy-paste prompt (Turkish: [AGENT-GUIDE-TR.md](AGENT-GUIDE-TR.md)) |
| [HERMES.md](HERMES.md)             | Hermes Agent port — install, tools/hooks/commands, runtime mapping, known limits |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Dev setup, quality checks, PR process                                      |
| [SECURITY.md](SECURITY.md)         | Security architecture + vulnerability reporting                            |

The two runbooks are written **for the agent**, not the human: the user makes a request, the agent picks the playbook. Start with the runbook's decision tree if you want to see how a request becomes a chain.

## Contributing

Issues and PRs are welcome — across the skills, the hook engine, the workflow kernel and the docs. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and the quality bar (tests + the eight checks above, conventional commits).

## License

MIT — see [LICENSE](LICENSE).

Built on [BMAD-METHOD](https://github.com/bmad-code-org/BMAD-METHOD) by BMad Code (MIT). "BMad" and "BMAD-METHOD" are trademarks of BMad Code, LLC — see [TRADEMARK.md](https://github.com/bmad-code-org/BMAD-METHOD/blob/main/TRADEMARK.md). Not affiliated with or endorsed by BMad Code, LLC. The `ATTRIBUTION` section at the end of `LICENSE` credits BMAD-METHOD and its trademark owner.

---
name: metodoloji-init
description: Install the Metodoloji record skeleton (docs/experiments, docs/development, docs/quality, templates, .metodoloji marker) into a project. Use on first contact with a project that has no .metodoloji/initialized marker.
triggers: ["metodoloji-init", "/metodoloji-init", "install methodology", "init metodoloji"]
---

# Metodoloji — install the record skeleton

One-time, idempotent installation. The canonical contract is
`{metodoloji-root}/commands/init.md`; this skill is the Hermes entry point to it.

`{metodoloji-root}` is the path printed by the session-start context line
`metodoloji-root: …`. Use it verbatim — never search the filesystem for it.

## Steps

0. **Already installed?** Read `{project-root}/.metodoloji/initialized`. If it
   exists and the marker is written:
   - without `--force` passed by the user: report `initialized_at` and
     `plugin_version` from the marker, say "already installed", and finish.
   - with `--force`: continue (the marker and templates are rewritten).

1. **Install** with the tool, not with hand-rolled `mkdir`/`cp` (hand copies
   drift from the declared skeleton and hide a stale install):

   ```
   metodoloji_init()                        # current session project
   metodoloji_init(project_root="/path")    # explicit target
   metodoloji_init(force=True)              # deliberate re-install
   ```

   The tool runs `{metodoloji-root}/bmad/scripts/skeleton.py --install`, which
   creates the ten record directories, copies the twelve templates (never
   overwriting an existing file), and writes `.metodoloji/initialized`.

2. **Report** from the returned JSON: directories created, templates copied vs
   kept, marker state, and the next step (`/metodoloji-audit`).

## Notes

- The methodology manifestos live under `{metodoloji-root}/docs/bmad/`.
  Do **not** copy them into the project.
- Never probe the gate key with shell (`cat`/`ls` of `~/.bmad/gate-key` is
  denied by the guard by design). Gate state comes from
  `/metodoloji-verify` with `action="check_secret"` or the orient digest.
- Brownfield project (existing code, no experiment history)? You MAY propose
  `code_guard = "soft"` in `custom/config.toml [hooks]` so unapproved writes
  warn instead of deny — but ASK FIRST. That file is plugin-global policy for
  every project on this machine. Re-harden to `"hard"` once a VERIFIED
  experiment scope exists. Never soften `quality_gate` / `deploy_guard`.

---
name: wds-2-trigger-mapping
description: "Map business goals to user psychology through structured workshops"
triggers: ["wds-2-trigger-mapping", "/wds-2-trigger-mapping"]
---

## Metodoloji

Bu yuzey arastirma metodolojisine baglidir: `{metodoloji-root}/docs/bmad/research-methodology.md` — Mod B+C (nitel/tasarim) — tetik haritalama; R-id/D-id kaydi.
Belgesel karar kod yazma izni degildir; kod her durumda Mod A mekanik onayini ister
(run_experiment.py --verify + guard-code.sh). Uydurma kanit/olcum sahtekarliktir.


## On Activation

1. **Run the orientation digest first — one read-only call, before anything else:** `python3 {metodoloji-root}/bmad/scripts/orient.py --project-root {project-root}`. It carries both roots, this phase's config (`{user_name}`, `{communication_language}`, `{document_output_language}`, `{project_name}`, `{date}` as `today`), the resolved `output_folder` and every module path, the record inventory, the board's live focus (with a `STALE` flag when a hot run still claims `complete`) and **waiting hand-offs addressed to you** (the project brief's baton usually waits here — the digest names the sender and the peek command), and skeleton/gate state. Read it once; never re-run it "for clean output" — it is small by construction.
2. **Run the prepend steps, then load facts:** execute `{workflow.activation_steps_prepend}` in order (from the `Customization` resolution below), then load `{workflow.persistent_facts}` (`file:` entries are paths/globs — `{metodoloji-root}/…` resolves under the plugin, everything else under `{project-root}`; skip silently only entries that match nothing).
3. Follow the instructions in `./workflow.md`.
4. **Before finishing, run the append steps:** execute `{workflow.activation_steps_append}` in order — they carry the obligations this phase owes its successor.

## Customization

Load this skill's effective configuration before acting (three-layer merge:
skill `customize.toml` ← team `custom/<skill>.toml` ← personal
`custom/<skill>.user.toml`):

Run: `python3 {metodoloji-root}/hooks/engine/resolve_customization.py --skill {skill-root} --key workflow`

## Chain Handshake (activation)

Check for upstream hand-off signals before starting: `python3 {metodoloji-root}/bmad/scripts/blackboard.py handoffs --skill wds-2-trigger-mapping --project-root {project-root}`. When a waiting baton is addressed to this skill, consume it: `python3 {metodoloji-root}/bmad/scripts/blackboard.py consume --channel handoff.wds-2-trigger-mapping --project-root {project-root}`.

## On Complete

Post the chain signal so the next phase opens knowing this one landed (one call):

`python3 {metodoloji-root}/bmad/scripts/blackboard.py mirror --key wds.trigger.<slug> --value "trigger map complete" --to wds-3-scenarios --note "trigger map ready — pick up the next phase" --project-root {project-root}`

The run key is this phase's heartbeat; repeating the same mirror never duplicates the waiting signal. Then run: `python3 {metodoloji-root}/hooks/engine/resolve_customization.py --skill {skill-root} --key workflow.on_complete`

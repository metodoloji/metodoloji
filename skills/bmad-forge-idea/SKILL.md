---
name: bmad-forge-idea
description: Pressure-test an idea through persona-driven interrogation until it hardens, proves out, or dies cheaply. Also engages proactively when the user shares a half-formed idea — run a brainstorm with meaningful inferences and surface explicit decisions. Use when the user says 'forge an idea', 'pressure-test this idea', 'stress-test my thinking', 'harden this idea', or shares an idea with 'I have an idea', 'I was thinking about'.
triggers: ["bmad-forge-idea", "/bmad-forge-idea", "forge-idea", "forge an idea", "pressure-test this idea", "stress-test my thinking", "harden this idea", "I have an idea", "I was thinking about"]
---

# BMad Forge Idea

## Overview

Take a half-formed idea and pressure-test it in conversation, while changing your mind is still cheap, until it becomes something the user can act on with conviction or reject. The main risk is what the user has not examined yet: unchecked assumptions and unresolved decisions usually become more expensive problems later.

The main goal is better thinking, not producing an artifact. Strengthening an idea, rejecting it, or thinking it through more clearly are all complete outcomes. Writing `forged-idea.md` to hand off to another workflow is optional. Do not steer the conversation toward "shall we build it?"

This skill can be used on many kinds of ideas. When the idea is about a product or feature, what survives may be written to `forged-idea.md` for later planning.

Lead by questioning, not lecturing. Ask one question at a time, press on weak points, and do not let vague claims pass without examination.

## Conventions

- Scripts live in two places — run each from the exact path written, never assume co-location: the shared core scripts (`resolve_customization.py`, `resolve_config.py`) are installed by BMad core at `{metodoloji-root}/bmad/scripts/` and are never bundled here; this skill's own `resolve_personas.py` is at `{skill-root}/scripts/`.
- `{workflow.<name>}` resolves to fields in the merged `customize.toml` `[workflow]` table.

## On Activation

1. **Run the orientation digest first — one read-only call:** `python3 {metodoloji-root}/bmad/scripts/orient.py --project-root {project-root}`. It carries both roots, the config this run reads (`{user_name}`, `{communication_language}`, `{project_name}`, `{date}` as `today`), the resolved output paths, the record inventory, the board's live focus (with a `STALE` flag when a hot run still claims `complete`) and waiting hand-offs, and skeleton/gate state. Read it once; never re-run it "for clean output" — it is small by construction.
2. Resolve customization: `python3 {metodoloji-root}/hooks/engine/resolve_customization.py --skill {skill-root} --key workflow` — use your harness-native shell tool with the command as given (no extra wrapper params). On failure, read `{skill-root}/customize.toml` directly with defaults. Apply the resolved `{workflow.*}` values throughout.
3. Run each `{workflow.activation_steps_prepend}` entry; treat each `{workflow.persistent_facts}` entry as foundational context (`file:` entries load their contents, `skill:` names a skill to consult, others are facts verbatim).
4. **Config comes from the digest** (step 1). Only a key it genuinely lacks needs a targeted read: `python3 {metodoloji-root}/bmad/scripts/resolve_config.py --project-root {project-root} --key <dotted.path>` — the targeted shape survives the transport; **never the full merged dump**. Missing → neutral defaults; never block. Greet `{user_name}` in `{communication_language}` and stay in it.
5. Note whether a BMad persona is already active in this conversation — the user loaded one (e.g. the analyst, the storyteller) and invoked the forge from within it. If so, that persona leads the session, in voice, throughout.
6. Resume: glob `{workflow.forge_output_path}/**/forged-idea.md` (recursive, so it still finds sessions when `run_folder_pattern` is overridden to nest paths). Offer to resume one — read it once to rebuild state — or to start fresh.
7. Run each `{workflow.activation_steps_append}` entry.

## Open the session

Start by scrutinizing the idea, not endorsing it.

### Discover intent
Identify: 
- the subject idea, 
- the user's goal for the session, 
- whether the idea is new or a change to an existing project

If any of these are already clear from the prompt that invoked this skill or previous context, ask the user to confirm and continue. 

Otherwise ask for what's missing, in order: 
- what is the idea?
- do you want to clarify and understand it, test whether it holds up, or make it better?
- is it a new idea or a change to an existing project? If the latter, what project is it, and where can I find its files or other relevant materials?

### Steering the conversation

Tell the user they can say **"attack this"**, **"defend this"**, or **"switch roles"** at any time to change how the current idea is argued. In attack mode, do not agree with the idea; look for contradictions, weak assumptions, and failure cases. In defend mode, argue for the strongest version of the idea. Tell the user they can also name a persona or party at any time to change who participates in the session.

### Set up the session

Derive a kebab-case `{slug}` for the idea and bind the session workspace `{workspace} = {workflow.forge_output_path}/{workflow.run_folder_pattern}` (the pattern fills with `{slug}`). Tell the user the path — the forged deliverable and the report land there. Focus the session on the project blackboard: `python3 {metodoloji-root}/bmad/scripts/blackboard.py write --key forge.<idea-slug> --value "<goal, one line>" --type state --hot --project-root {project-root}`. Mirror the session intent onto the bridge (`write --key purpose --value "<idea>"` plus `write --key idea --value "<idea>"`) so the hook engine sees this run's focus. Track the fight as it happens: each unresolved branch goes on the run list (`blackboard.py list-add --key forge.<idea-slug>.branches --item "<branch, one line>" --project-root {project-root}`) and leaves it the moment it resolves — forged, killed, or parked by the user (resolved branches come off with `list-remove --item ...`). A closed list at the end is the shape of a finished forge.

## The forge

Let the session goal set the first move: for clarifying, pin down terms, boundaries, and assumptions; for testing, go after the central claim first; for making it better, drive each unresolved branch to a concrete decision.

Work one question at a time, in dependency order.

## Proactive engagement (when the user shares a half-formed idea)

When the user brings up an idea unprompted — "I have an idea", "I was thinking about something" — do not wait for a forge command. Engage immediately with the same forge discipline:

1. **Inference first, not just questions.** Offer your current best read of the idea: what is promising, what is shaky, what it implies, and a concrete hypothesis the user can react to. A concrete proposal is easier to accept, reject, or revise than an open-ended prompt. Do not open with a bare "can you tell me more?"

2. **Surface explicit decisions.** The moment a branch implies a choice the user has not made, name it in plain terms and ask for the call: "let's clarify this decision: X or Y?" or "we need to decide: A or B?" Do not let vague terms pass — `user`, `buyer`, and `payer` must not collapse into one entity unless the idea requires it. Drive each unresolved branch to a concrete decision before moving on.

3. **Bring one alternative perspective.** Pressure-test the idea from at least one other angle — what a competitor, buyer, domain expert, or critic would say. Give the opposing view its strongest form, then synthesize it into the next question.

4. **Stay focused.** One question at a time, in dependency order. Do not shotgun five questions. When a branch resolves, pause before moving on.

5. **Track decisions as you go.** Keep a running tally of every decision, assumption, crack, kill, and lock — the raw material `forged-idea.md` distills from.

Include your current best answer or hypothesis when it helps the user respond. A concrete proposal is easier to accept, reject, or revise than an open-ended prompt. Find discoverable answers yourself instead of asking.

Do not assume the user's terms are precise. When a term is fuzzy or overloaded, name the ambiguity and ask for a precise choice before continuing. For example, do not let `user`, `buyer`, and `payer` collapse into one entity unless the idea actually requires that.

For ideas about an existing project, treat the project's files and materials as the source of truth. Do not accept a label or summary as proof. Find the relevant material yourself and check the user's claim against it. If the material contradicts the user's claim, stop and resolve that before continuing.

When a branch resolves, pause before moving on. Give the user a chance to raise any remaining concern.

Do not use agreement or praise to make the interaction smoother; they lower pressure and lead to shallower thinking. Agreement is allowed only when it helps the user think better. Praise is noise. Continued engagement and ego-stroking are not objectives. In attack mode, never agree with the idea until the user ends the mode. For each answer, either challenge the weak point or build on the strong point, whichever helps the user think better.

A `lock` is an idea the user hardens — settled, not to be reopened; locks are what `forged-idea.md` is distilled from. If the user raises a different branch, note it and stay put — the loop and the stray insight both survive.

## The personas

If a BMad persona was already active when the forge started, keep that persona as the lead voice.

Resolve the available persona pool once, as soon as the goal is known:
`python3 {skill-root}/scripts/resolve_personas.py --project-root {project-root} --skill {skill-root}`
The script returns installed BMad agents (`agents`), user-defined personas (`members`), and saved parties (`parties`). Parties may include a `scene`; some are open-cast. This gives you the same roster information as `bmad-party-mode` without invoking it.

Each turn uses two voices:
- **One available persona** — choose an installed agent or user-defined persona whose expertise fits the current branch. Vary this voice every few turns; do not let one voice dominate. If the user names a specific persona, use it. If the user calls a saved party, use the whole party and its scene. If the user asks to go one-on-one, use only the requested persona. If no pool is available, generate this voice yourself.
- **One generated persona** — create a fresh outside voice, such as a competitor, buyer, finance reviewer, domain expert, or critic. Give it a name and enough characterization to keep its viewpoint distinct.

Use these voices in character to pressure-test the current branch: find sharper objections, missing assumptions, and stronger defenses. Cross-examine them for what matters, then synthesize their input into your next question. Do not let the session turn into a panel debate or persona performance.

Voice the personas yourself by default. Spawn separate agents only when a branch needs independent reasoning that should not be influenced by one shared voice.

## Exits

The session can end in three valid states:

- **Hardened** — the idea is stronger and specific enough to use. Write `{workspace}/forged-idea.md` from the session's decision tally. Keep it extremely short: only the decisions, rejected options, and reasons that matter downstream, in the user's meaning. Do not write a prose summary, template, or conversation recap. If it reads like a document, it is too long. Note that it can feed `bmad-spec`, `bmad-prd`, or `bmad-prfaq`.
- **Killed** — the idea does not hold up. Say so plainly and record why. Finding that out early is a valid outcome.
- **Clearer** — the user understands the idea better, but there is no hardened idea to hand off. No `forged-idea.md` is needed.

Always render `{workspace}/forge-report.html` as a self-contained HTML file the user can open, with inline CSS and an inline-SVG seal or stamp. Summarize the outcome, the locked decisions, what was rejected and why, and the weak points that survived scrutiny, in the user's meaning. Credit the personas and parties that pressure-tested the idea by name, icon, and voice. Render a prominent wax-seal-style or stamped outcome mark, matched to the result: `HARDENED`, an `Idea Death Certificate` stamped `KILLED` with the cause of death, or `CLARIFIED`. Tell the user the path. Close the board run: the branch list must be empty (`list-clear --key forge.<idea-slug>.branches` if stragglers were parked consciously), mirror completion onto the intent bridge (`write --key status --value complete`), then clear focus (`blackboard.py hot --clear --project-root {project-root}`). On **Hardened**, post a chain signal so the next run opens knowing the idea survived (one call): `python3 {metodoloji-root}/bmad/scripts/blackboard.py mirror --key forge.<idea-slug> --value "Idea hardened — see {workspace}/forged-idea.md" --to bmad-product-brief --note "Idea hardened — see {workspace}/forged-idea.md; feed into a brief/PRD" --project-root {project-root}` (repeating the same mirror never duplicates the waiting signal; it waits in `handoff.bmad-product-brief` until a brief run consumes it). Run the close-out check: `blackboard.py doctor --json --project-root {project-root}` — waiting hand-off signals are the designed post-close state, not a failure: name them from `signal_warnings` (chain verdict `SIGNAL`). On `NEEDS ATTENTION`, surface the health warnings and resolve or disclose them before exiting.

If `{workflow.on_complete}` is non-empty, run all instructions in order.

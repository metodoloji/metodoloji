# Working-State Patterns

How a skill's work survives across turns and context compaction. This is a design axis of its own, separate from persona, intent modes, and degradation, and it has more than one answer. Load this file when building or revising a multi-turn skill that builds something, or when a skill already carries a structured working artifact.

## The choice

A multi-turn skill that builds something has to hold state somewhere. Pick by the shape of the work, not by default.

| Strategy | Holds | Choose when |
|---|---|---|
| Structured working artifact | the *what* — work-in-progress in a custom schema that transforms into the output | the work decomposes into a natural intermediate the user iterates on directly, which later becomes the deliverable |
| Neither | nothing across turns | a one-shot transform, a stateless utility, or a purely conversational skill where the input/output contract or the live conversation is the state |

For reasoning that must survive revisits — decisions, directions, rejected alternatives — record it inside the structured artifact itself (a decisions section, a changelog, inline rationale), not in a separate side log. State that is split across files drifts; one artifact stays canonical.

## Structured working artifact: the work-in-progress itself

Some skills need no side log because the work has a natural intermediate form that carries its own state. The skill builds a custom file with its own schema — story beats, an outline, character sheets, a shot list, a spec kernel, a requirements matrix — that the user reads and edits directly, and that later transforms into the deliverable: beats into prose, an outline into an article, a spec into code, a storyboard into a video.

State lives in the artifact's structure, so cross-turn continuity is just re-reading the file. Choose this when the work is constructive and decomposes, when the user benefits from seeing and shaping the intermediate, and when the final output is a transformation of it. The artifact's schema is the skill's real contract, so design it deliberately and make each section earn its place the same way a SKILL.md does.

The transform is part of the pattern: name where the intermediate ends and the deliverable begins, and whether the transform is a separate intent ("draft from beats") or the tail of the same run.

### Resume, update, validate, finalize

- **Resume**: on activation, locate the working artifact at its known path. If found, surface it, read it once to rebuild state, and offer to resume. The single read recovers full context regardless of compaction.
- **Update**: read the artifact first; the change request enters as a signal against the standing record. If it contradicts a prior decision recorded in the artifact, surface the conflict before applying.
- **Validate**: challenge the artifact against the standards the user themselves set, not a generic rubric.
- **Finalize**: transform the artifact into the deliverable and say where the intermediate lives afterward.

## Treatment style

State the principle once where it first applies, typically inside the Create intent as a single clause ("write the primary skeleton to `<path>`; that file is canonical process state"). Mention reads at the moments that matter: Update reads before changing decisions, Validate before critiquing, Finalize transforms at handoff. That is the entire treatment. Do NOT open with a "state discipline" enumeration of what to record, write a separate `## Workspace` meta-section, include a tree diagram, or split workspace creation into "for new" and "for existing" sub-sections — "create if absent, update if present" is one sentence.

## When none of this applies

A one-shot transform, a stateless utility, or a purely conversational skill keeps no cross-turn state: the input/output contract or the live conversation is all there is. Do not bolt an intermediate artifact onto a skill that does one deterministic thing and returns.

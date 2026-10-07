# Driving a run

The kernel owns the order. Your loop is: ask what is current → do that work → leave the
evidence → ask the kernel to complete the stage → follow what it computes. Repeat until the
run ends.

## The loop

```sh
# 1. Where are we? (current stage, its evidence, where the kernel will go next)
python3 {metodoloji-root}/bmad/scripts/workflow.py next --project-root {project-root}

# 2. Do the stage's work. Its `goal`/`actions` come from `next`; its evidence target is
#    the artifact the spec names. Write the tokens the spec requires.

# 3. Ask the kernel to accept the stage and compute the next one.
python3 {metodoloji-root}/bmad/scripts/workflow.py complete --slug <slug> \
  --stage <stage-id> [--note "…"] --project-root {project-root}
```

- `status` gives the full position (completed stages, flags, blocker, total stages).
- `history` shows the transition trail (`enter` / `complete` / `block` / `resume` / `done`).
- `list` shows every run; a single run may omit `--slug` on `status`/`next`/`resume`/`history`.
- Every command prints JSON and exits **0** on success, **1** on a refusal.

## Evidence discipline

`complete` refuses until the spec's evidence exists (exit 1, `ok: false`, an `error` naming what
is missing). The refusal is the correct outcome — it is the guarantee that no step was skipped.
Handle it as follows:

1. Read the `error` / `evidence` field; it names the file, the tokens and the count needed.
2. Produce the missing work — the stage's real deliverable, not a stub token to unblock the
   kernel. Writing `ISSUE-` into a file without issues is exactly the failure the gate exists
   to stop.
3. Re-run `complete`.

`--force` skips the check. It exists for a knowingly overridden step (for example evidence that
genuinely cannot exist in this environment). When you use it, you **must** tell the user which
stage was forced and why — a silent `--force` makes the whole process worthless.

## Branches and flags

A conditional edge reads a flag. For a verify stage that loops back when something regressed:

```sh
# a fix failed its test → the kernel will route back to the apply stage
python3 {metodoloji-root}/bmad/scripts/workflow.py flag --slug <slug> --key regressed \
  --value true --project-root {project-root}
```

The taken edge **consumes** the flag, so the next pass follows the default edge. Set a flag
only when the condition is genuinely true; never set one to steer the run somewhere you prefer.

## Blocked, paused and finished

- **Blocked by the user's world** (no access, a decision pending, an external dependency):
  `block --slug <slug> --reason "…"` records it and stops the run being silently abandoned.
  `resume` lifts it. Never work around a block.
- **Blocked by the kernel** (`next` returns no transition matching the state): the spec has no
  edge for the flags you set. Fix the flags (clear the one you set wrongly) or, if the intent
  really changed, author/validate a new spec — do not hand-edit state.
- **Finished**: `status` reads `completed` and `current` is null — or the run ended at an
  `end` edge. Report the outcome from the artifact, not from memory.

## Reporting to the user

- Name the run, the stage just completed, and the stage the kernel computed next.
- Keep the artifact (`artifact` in the spec) as the human-readable record; the user should be
  able to read the process's whole history there, in order.
- If the user's intent changes mid-run, say so and change the spec deliberately — a run whose
  spec no longer matches its intent is worse than a new run.
- Never present a stage the kernel refused as completed, and never restate the process from
  memory when `next` can answer in one call.

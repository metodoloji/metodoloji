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
- `list` shows every run **with its derived evidence verdict** (E-058) — pick the run to continue by whether its foundation still holds, not by its status string; a single run may omit `--slug` on `status`/`next`/`resume`/`history`.
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

A completion is a claim about the workspace, so it is dated and re-checked (E-056): completing an
`artifact` stage records the predicate that satisfied it, and every read re-derives
`CURRENT | STALE | UNKNOWN` against the artifact on disk. `status` and `next` carry an `evidence`
block (`stale_evidence` names the completed stages whose artifact no longer holds), the session
advisory prints `FOUNDATION MOVED: …`, and `complete` refuses to advance onto a **STALE**
foundation — re-complete the broken stage, or `--force` knowingly. `UNKNOWN` (a completion
recorded before witnesses existed, or an artifact that cannot be read) never blocks. An edit that
keeps the declared tokens stays `CURRENT`, so a tracked artifact may still be edited by later
stages.

That re-derivation is **bounded** (E-058) because it runs on the session edge: at most 200 glob
matches are scanned, and no candidate wider than 4 MiB is read. A match found inside the budget
answers exactly as before; an artifact past it answers `UNKNOWN` with the reason (`cannot be
re-derived within the read budget`) and the remedy — split the artifact, or narrow the path
pattern. The bound can never produce a `STALE`, and accepting a stage is not bounded (the artifact
you just produced is read once, so a large deliverable can still complete).

The read is also **confined to the project root** (E-060): a run's evidence is a claim about its
own project, so a path that resolves outside the root (`../…`, `docs/../../…`, an absolute path,
or a symlink pointing out) is never opened. The read surfaces answer `UNKNOWN` naming the root,
and `complete` refuses such a stage outright — write the evidence inside the project, or the
stage cannot be accepted at all. Nothing is read on your behalf outside the tree you are working
in.

The same boundary governs the kernel's own storage (E-062): the run's workflow definition is
resolved only from inside the project, so a `--slug` is a name (every filename built from it goes
through one sanitizer) and a state whose recorded `spec_file` points outside the root is not
opened — the run then reads as having no spec, and `complete` says so. If a run suddenly reports
`total_stages: 0`, its definition moved out of the project or its state was edited; re-create the
run with `create --spec`, do not hand-fix the state.

Commands that take no `--slug` (`status`, `next`, `resume`, `history`) resolve the run from the
stored names first (E-069): one run ⇒ that run, several ⇒ the CLI asks you for `--slug`. The
lookup reads no evidence, so it costs nothing and cannot derive a verdict for a run you did not
ask about; naming the run explicitly is the only thing that classifies it.

A spec may also declare a top-level `artifact` — the run's durable source of truth — and that
pointer is repeated back to you (`status.artifact`, and "tracked in <it>" in the session line), so
it obeys the same boundary (E-067): only a path that resolves inside the project is delivered. An
outside one arrives as `artifact: null` with an `artifact_note` naming what the spec declares and
the root, and the session line says the same thing instead of naming a location to track — fix the
spec, or the run's own `artifact` stays withheld. `create` refuses such a spec outright, before
anything is written; a run an older tree already saved keeps working, its read surfaces just
report the outside pointer.

A `command` completion ("this argv exited 0") cannot be re-derived by looking, so it is dated
too (E-064): completing it records the **tracked code state** it ran in — the commit HEAD names
plus a digest of the tracked working-tree changes — and every read compares that with the state in
force. Unchanged answers `CURRENT`; a new commit or a dirty edit of a tracked file answers
`UNKNOWN` naming both states. A command is never `STALE` and never blocks an advance: a look
cannot falsify `exit 0`, so the surface reports what it can compare instead of pretending to
falsify. Where the state cannot be established at all (not a git work tree, no commit yet, git
missing) the answer is `UNKNOWN` with the reason, and a completion recorded before this dating
says `re-complete the stage to date it` — re-run the command and complete again, since the
kernel will not re-execute a command on your behalf. Only **tracked** files are in the
fingerprint, which every reason string says; an untracked file a command read can change without
moving the verdict. The probe costs one bounded git call per read pass, shared across a `list`
that classifies several runs and skipped entirely when no run has a command witness.

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

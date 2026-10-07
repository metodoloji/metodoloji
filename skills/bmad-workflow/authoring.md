# Authoring a workflow spec

A workflow is **declared data**, not a plan in your head: stages, the evidence each stage must
leave, and the transitions between them. You author it once, from the user's intent, and the
kernel then runs it mechanically. If the intent changes, the spec changes — visibly.

## Where specs live

- Authored spec: any path you choose (a project path such as `docs/workflows/<name>.json` is
  a good home — it is reviewable and versioned with the project).
- Built-in reference specs ship with the plugin: `{metodoloji-root}/bmad/workflow/builtin/`.
  `seo-visibility.json` is the worked example (an SEO visibility + speed process) — read it
  before authoring your first spec.
- On `create`, the kernel copies the spec to `{project-root}/.metodoloji/workflow/specs/`, so
  the run stays reproducible even if the authored file moves.

## Schema

```json
{
  "id": "seo-visibility",
  "version": 1,
  "title": "SEO visibility + performance",
  "intent": "one paragraph: what this workflow is for",
  "artifact": "sorunlar.md",
  "start": "analyze",
  "stages": [
    {
      "id": "analyze",
      "title": "Analyze the site and list the issues",
      "goal": "what this stage achieves, in the user's terms",
      "actions": ["concrete step the executor must take", "..."],
      "evidence": {"kind": "artifact", "path": "sorunlar.md",
                   "contains": ["ISSUE-"], "min": 1},
      "next": [{"to": "map"}]
    },
    {
      "id": "test",
      "title": "Verify",
      "evidence": {"kind": "artifact", "path": "sorunlar.md",
                   "contains": ["RESULT:"], "min": 1},
      "next": [{"to": "apply", "when": {"key": "regressed", "equals": "true"}},
               {"to": "done"}]
    },
    {"id": "done", "title": "Complete", "terminal": true}
  ]
}
```

| Field | Rule |
| --- | --- |
| `id` | lowercase `[a-z0-9._-]`, starts alphanumeric. Becomes the run slug by default. |
| `title` | required, non-empty. |
| `intent` | optional but strongly recommended — the paragraph a later session reads instead of guessing. |
| `artifact` | optional; the single human-readable file the process maintains. Advisory to the user, and the usual target of `artifact` evidence. |
| `start` | must name a stage. |
| `stages[].id` | unique, same character rule as `id`. |
| `stages[].requires` | stage ids that must be completed first (usually the `next` predecessor; declare it when a stage can be reached by more than one path). |
| `stages[].evidence` | **required on every non-terminal stage** — see below. |
| `stages[].next` | non-empty list of edges. Edge `{"to": "<stage-id>"}` or `{"to": "end"}`. |
| `stages[].terminal` | `true` → the run ends here and `next` must be absent. A terminal stage with **no** evidence finishes the run the moment it is entered (an end marker); a terminal stage **with** evidence is real work and must be completed explicitly. |

Order of `next` edges is significant: the **first** edge whose condition holds wins. Always
put the conditional edge before the default one, and give every stage a default so a run can
never wedge on an unmatched condition.

## Evidence — how a stage proves it is done

`evidence` is what makes "no skipped step" mechanical instead of a promise. A stage cannot
complete until the kernel sees it.

| `kind` | Fields | The kernel checks |
| --- | --- | --- |
| `artifact` | `path` (project-relative), `contains` (list of literal tokens, optional), `min` (default 1) | the file exists; the total count of `contains` tokens is `>= min`. With no `contains`, existence is enough. |
| `command` | `argv` (list), `rc` (default 0, optional), `timeout` (seconds, default 600, optional), `retry_delay` (optional) | runs `argv` in the project root and requires the exit code. Tests, linters, measurements. |
| `note` | — | requires `--note TEXT` on `complete`. For stages whose output is a decision or a conversation, not a file. |

**Design the tokens, not just the path.** `contains: ["ISSUE-"]` with `min: 1` proves the list
exists; the SEO spec's `MAP:` / `FIX:` / `APPLIED:` / `RESULT:` tokens make each stage's own
contribution required, in order. If a stage can be satisfied by a file a previous stage wrote,
the spec is wrong — give it a token nothing else emits.

## Condition grammar (`when`)

Deliberately tiny and total. Anything not matching is rejected at validation time.

```
{"key": "<flag>", "equals": "<string>"}   flag value == string
{"all": [<cond>, ...]}                    every condition holds
{"any": [<cond>, ...]}                    at least one holds
{}  or null                               always true
```

Flags are set with `flag --key K --value V` (e.g. the `test` stage sets `regressed=true` when
a fix fails). **A taken conditional edge consumes its flags**, so the default edge wins next
time round — that is what makes a loop-back branch fire once instead of looping forever.

## Authoring procedure

1. **Read the intent, then write the stages as they will be executed**, in order. One stage per
   distinct outcome (analyze → map → plan → apply → verify → done), not one per tool call.
2. **Give each non-terminal stage its evidence** using the table above. Prefer `artifact` with
   a stage-unique token; use `command` when the stage's own result is a test/lint/measurement;
   use `note` when the output is a judgement.
3. **Every stage gets a `next`**, and conditional edges get a default sibling.
4. **Validate before creating:**
   `{kernel} validate --spec <path>` — exit 0 means runnable. Validation rejects a malformed
   `id`, a duplicate stage id, a missing `evidence` on a non-terminal stage, an edge naming an
   unknown stage, a terminal stage that declares `next`, and any stage **unreachable** from
   `start` (an unreachable stage is dead weight: the process does not do what it appears to).
   Fix every problem it names before continuing.
5. **Create the run:**
   `{kernel} create --spec <path> [--slug <slug>] --project-root {project-root}` (or
   `--builtin seo-visibility`). `create` refuses to clobber an existing run unless `--force`
   is given — a reset is a confession, report it to the user.
6. Reuse before authoring: `{kernel} list --project-root {project-root}`. If a run of the same
   process already exists, resume it instead of creating a second one.
7. Then read `running.md` and drive the run.

## Rules

1. One stage = one outcome a user could describe. If a stage's title needs "and", it is two stages.
2. Evidence is a check, not a reminder. "Write the report somewhere" is not evidence;
   "`docs/report.md` containing `FINDING-`, min 3" is.
3. Never add a stage the executor cannot finish differently from passing it by accident.
4. Never edit a run's copied spec to unblock yourself; fix the authored spec, validate it, and
   create a new run if the change is structural.

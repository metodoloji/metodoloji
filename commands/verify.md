---
description: Verify whether an experiment record is APPROVED and its gate token is not forged.
---

# /metodoloji:verify — Verify an experiment record

Verifies whether the `{project-root}/docs/experiments/<experiment-id>.md` record is
APPROVED and whether the gate token is not forged.

## Usage

```sh
python3 {metodoloji-root}/skills/bmad-research-experiment/scripts/run_experiment.py \
  --verify --record {project-root}/docs/experiments/<experiment-id>.md
```

## Meaning of outputs

| Output | Meaning | What to do |
|---|---|---|
| `VERIFIED` | Record is APPROVED and token is valid | the guard allows code writes matching the globs in the record's `Code Scope` field |
| `FORGED` | Token does not match the key | record invalid — no code can be written; regenerate the record (`--record ... --run <command>`) |
| `REJECTED` / other | Did not pass the gate | revise the hypothesis, measure again |
| `ADVISORY-BLOCK` (exit 2) | Token genuine but small sample / n unknown / metric MISMATCH | does **not** unlock code; re-measure in a new record |

Cross-machine note: the gate key is machine-local, so APPROVED on another machine reports FORGED on yours by design — re-run under a new record locally and link with a `Re-Measured-By:` line.

The guard already performs this verification before code is written; this command is for manual checks.

## Harness note

Call every tool by the exact bare name your harness lists, with complete
arguments; if a call is rejected as unknown, re-issue it with the bare name
(never emit a call whose record path is not yet known — resolve it in
text first). Write paths with forward slashes on every OS (`C:/...` works in
Bash, PowerShell and Python, while a `C:\...` path collapses in a POSIX
shell). Make probes failure-proof in the dialect of the tool you call —
`Bash`: `|| true`; `PowerShell`: a guarded statement. Never `ls`/`cat` the key
file itself — gate state comes from this command's own output, not from shell
inspection of `~/.bmad/`.

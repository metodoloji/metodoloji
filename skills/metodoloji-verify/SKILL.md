---
name: metodoloji-verify
description: Verify whether a Metodoloji experiment record is APPROVED and its HMAC gate token is genuine, check whether this machine holds the gate key, or validate a documentary record. Use before claiming a code scope is approved.
triggers: ["metodoloji-verify", "/metodoloji-verify", "verify experiment record", "gate token"]
---

# Metodoloji — verify a record

Canonical contract: `{metodoloji-root}/commands/verify.md`.

```
metodoloji_verify(record="docs/experiments/E-001.md")                 # verify
metodoloji_verify(action="check_secret")                              # gate key
metodoloji_verify(record="docs/research/R-003.md", action="validate") # documentary
```

The tool shells out to
`{metodoloji-root}/skills/bmad-research-experiment/scripts/run_experiment.py`,
so its verdict is the same one the guard reaches before allowing a write.

## Verdicts

| Verdict | Meaning | What to do |
|---|---|---|
| `VERIFIED` | APPROVED and the token is genuine | writes inside the record's `Code Scope` are allowed |
| `FORGED` | Token does not match the key | record invalid; regenerate it (`--record … --run <cmd>`) |
| `REJECTED` | Did not pass the gate | revise the hypothesis, measure again |
| `ADVISORY-BLOCK` (exit 2) | Genuine token, but small sample / n unknown / metric MISMATCH | does NOT unlock code; re-measure in a new record |

A record approved on another machine reports `FORGED` here by design: the gate
key is machine-local. Re-run under a new local record and link it with a
`Re-Measured-By:` line — do not "fix" the token.

## Gate key

`check_secret` reports the trust ring (`own key only`, peers, or MISSING).
When the key is missing, point the user at
`{metodoloji-root}/commands/gate-setup.md` (`--init-secret` writes it outside
the repo, once per machine). Never `cat`, `ls` or `echo` the key path — the
guard denies those on purpose and the denial looks like a tool failure.

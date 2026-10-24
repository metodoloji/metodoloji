---
name: metodoloji-gate-setup
description: Generate or inspect the machine-local HMAC gate key (~/.bmad/gate-key) and its trust ring that the Metodoloji mechanical gates verify against. Use when verification reports the key is MISSING, or when adopting a second machine.
triggers: ["metodoloji-gate-setup", "/metodoloji-gate-setup", "gate key", "gate-setup"]
---

# Metodoloji — gate key setup

Bound to `{metodoloji-root}/docs/bmad/research-methodology.md` — the key is what
makes that manifesto's approval mechanical and machine-local.

Canonical contract: `{metodoloji-root}/commands/gate-setup.md`.

The key is machine-local, lives **outside the repo** with `0600`, and is never
committed. Every APPROVED record's `GATE-OK-…` token is an HMAC under it, and
verification consults the whole **trust ring**: this machine's key plus every
peer key imported into `~/.bmad/gate-keys/`.

## Steps

1. **Exists?** Ask the gate, never the filesystem:

   ```
   metodoloji_verify(action="check_secret")
   ```

   `own key only` / `trust ring: N keys` → already installed, finish.
   Never run `ls`, `test -f`, `cat` or `echo` against a path containing
   `gate-key` / `gate-keys` / `bmad_gate_key`: the guard denies those by
   design and the denial reads as a confusing tool error, not as a policy hit.

2. **Missing?** Generate once per machine:

   ```sh
   python3 {metodoloji-root}/skills/bmad-research-experiment/scripts/run_experiment.py --init-secret
   ```

   Then re-run `metodoloji_verify(action="check_secret")`.

3. **Never** print, copy or move key content; never run `--init-secret` twice.
   A second key silently invalidates every token the first one issued.

## Windows + WSL (two homes, one key)

PowerShell and WSL have different home directories, but the methodology treats
them as one machine. Generate once, then copy the file to the other home
(`chmod 600`) — a token issued under one home otherwise reports FORGED under
the other.

## Trust ring (records approved on another machine)

Import a peer key out-of-band (never through shell history):

```sh
BMAD_PEER_GATE_KEY=$(cat /path/to/peer/key-file) BMAD_PEER_LABEL=laptop2 \
  python3 {metodoloji-root}/skills/bmad-research-experiment/scripts/run_experiment.py --import-key
```

`--check-secret` reports the ring size afterwards. A record signed outside the
ring still reports FORGED — the ring only ever contains machines of yours.

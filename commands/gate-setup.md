---
description: Generate the machine-local ~/.bmad/gate-key used by the methodology's mechanical gates.
---

# /metodoloji:gate-setup — Generate the gate key (gate-key init)

The methodology's mechanical gates (Mode A approval, QR/PR hard mode) verify against
this machine's **trust ring** — the machine-local key in `~/.bmad/gate-key` plus every
peer key imported into `~/.bmad/gate-keys/` (see Trust ring below). The key file must be
**outside the repo, with 0600 permissions**, and is never committed.

## Steps

1. If `~/.bmad/gate-key` exists: say "already installed" and finish (do not overwrite —
   old evidence would break). Determine existence ONLY via
   `run_experiment.py --check-secret` or the orient digest's `gate_key`
   line — never `ls`/`test -f`/`cat` the key path in shell (the guard
   denies any command referencing it, including existence probes).

2. If not, run this command:
   ```sh
   python3 {metodoloji-root}/skills/bmad-research-experiment/scripts/run_experiment.py --init-secret
   ```

3. Verify the result with the gate itself — **never** `ls`/`cat` the key file
   (the guard denies key-file references in commands, which is confusing):
   ```sh
   python3 {metodoloji-root}/skills/bmad-research-experiment/scripts/run_experiment.py --check-secret
   ```
   **Never** print, copy, or move the key content — the guard blocks such traces.

4. If a `GATE-OK-...` token example appears in the output, setup is complete. Next step:
   create the first experiment record (`docs/experiments/E-001.md`) so the guard-code
   allows code writing.

## Windows + WSL (two homes, one key)

PowerShell and WSL/WSL-bash have **different** home directories
(`C:\Users\<you>\.bmad\gate-key` vs `~/.bmad/gate-key`), but the methodology
treats them as one machine: tokens issued under one home report FORGED under
the other. Generate the key **once** (either side), then sync it to the other
side — never `--init-secret` twice (the second key silently invalidates every
token the first key issued):

```sh
mkdir -p ~/.bmad
cp /mnt/c/Users/<you>/.bmad/gate-key ~/.bmad/gate-key
chmod 600 ~/.bmad/gate-key
```

Re-run `check-plugin.sh §0/§3` to confirm both sides verify the same records.
If the key is ever regenerated, re-sync immediately.

## Trust ring (records approved on your other machines)

Developing the same repo in parallel on several computers does NOT require
re-measurement ceremonies: signing always uses this machine's own key, but verification
consults your whole trust ring — the machine's own key plus every peer key imported
into `~/.bmad/gate-keys/`. Import a peer machine's key once:

```sh
BMAD_PEER_GATE_KEY=$(cat /path/to/peer/key-file) \
BMAD_PEER_LABEL=laptop2 \
  python3 {metodoloji-root}/skills/bmad-research-experiment/scripts/run_experiment.py --import-key
```

Records APPROVED on that machine then verify here natively. Transfer key material
out-of-band (password manager, `scp` of the key FILE) — never through shell history;
`--import-key` reads it from the environment, not argv. `--check-secret` reports the
ring size. A record signed outside the ring still reports FORGED — the ring only ever
contains machines of yours.

## MCP servers the session can reach

After setup, the orientation digest also reports which MCP servers this session can
reach (project `.mcp.json` plus the harness's user config — read-only, never spawned).
If the digest says servers are configured but **all disabled**, enable the ones the
coming work needs in their harness config; they surface here once active. When none
are configured and the work would clearly benefit from one (docs lookup, browser
automation, repo indexing), offer to add it — an offer, never a requirement.

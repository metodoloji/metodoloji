---
name: metodoloji-audit
description: Run the Metodoloji health check — record chain gaps (E→IR→SP→S→QR→PR), approved-experiment inventory, gate-key state, and optionally the plugin's own integrity audit. Use before a release or when the chain looks inconsistent.
triggers: ["metodoloji-audit", "/metodoloji-audit", "methodology audit", "record chain check"]
---

# Metodoloji — health check

Canonical contract: `{metodoloji-root}/commands/audit.md`.

## Fast pass (default)

```
metodoloji_audit()                 # scope="records"
```

Returns the orient digest: skeleton state, record inventory per stage,
gate-key presence, board focus, waiting hand-offs, open plans, `next_steps`.
Read `next_steps` first — it is the digest's own prioritized list.

Then check the chain by eye: a stage with records while its upstream is empty
is a chain gap (e.g. `S` exists but no `QR`). Report gaps with the record ids,
not with a general "chain incomplete".

## Full pass

```
metodoloji_audit(scope="full")
```

Runs the plugin's own `scripts/check-plugin.sh` (1–2 min) and returns
`status` (`HEALTHY` / `N problems found`), the `[ERROR]` lines, and a tail.
Section headers in `tail` name the scope: "plugin copies" refers to
`{metodoloji-root}` internals, everything under `{project-root}/docs/` is the
target project. Localize a failure to one side before touching anything.

## Approved-experiment inventory

For each `E-*.md` under `{project-root}/docs/experiments/`:

```
metodoloji_verify(record="docs/experiments/E-001.md")
```

Report the VERIFIED / FORGED / ADVISORY-BLOCK distribution. `FORGED` on a
record approved on another machine is expected (the gate key is
machine-local) — say so instead of calling it tampering.

## Hook policy

Read `[hooks]` in `{metodoloji-root}/custom/config.toml`:
`quality_gate`, `deploy_guard`, `code_guard`, `stop_guard` (soft/hard).
Shipped defaults: quality/deploy soft, code hard when absent. Flag
`quality_gate` or `deploy_guard` softened — that is over-softening;
`code_guard = "soft"` is legitimate only for brownfield adoption and must be
re-hardened after the first VERIFIED scope.

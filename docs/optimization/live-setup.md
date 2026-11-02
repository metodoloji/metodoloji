# Live upstream setup

**Türkçe özet:** Bu sayfa, bu çekirdeğin dışa aktardığı artifact'ları *gerçek*
Hermit ve SkillOpt araçlarıyla doğrulamak için gereken kurulumu anlatır. İki
komut vardır: `scripts/setup-upstream.sh` kurar, `scripts/verify-upstream.sh`
ölçer. Kurulum proje içinde kalır (`vendor/hermit`, `.venv-upstream`) ve git'e
girmez. Windows'ta Hermit 0.1.0'ın beş taşınabilirlik hatası vardır; yama
`patches/hermit-0.1.0-windows.patch` olarak saklanır.

Everything here is optional. The kernel itself has no network access and no API
key; this setup exists so an export can be checked by the tools it targets,
instead of only by this repository's own validator.

## Install

```bash
bash scripts/setup-upstream.sh     # pinned checkout + venv + hermit-harness + SkillOpt
bash scripts/verify-upstream.sh    # runs both tools; prints one line per check
```

| Component | Version | Where it comes from |
|---|---|---|
| Hermit | 0.1.0, commit `56fb676` | source checkout at `vendor/hermit`, installed editable |
| SkillOpt | 0.2.0 | the commit Hermit's `--group skillopt` pins |
| Python | 3.12+ | `.venv-upstream` |

Hermit is installed **from source, not from PyPI**. The published 0.1.0 wheel has
no `hermit/agent` package, so every command fails with
`No module named 'hermit.agent'`. Only the checkout runs the documented
quickstart.

## What Windows needed, and why

`patches/hermit-0.1.0-windows.patch` touches six files. Each item is a defect
found by running the real tool, not a style preference:

| File | Symptom | Fix |
|---|---|---|
| `agent/base/login.py` | `ModuleNotFoundError: fcntl` on every command | `msvcrt.locking` shim with `flock` semantics |
| `sandbox.py` | `asyncio` dies with `WinError 10106`: no Winsock | keep `SystemRoot`/`TEMP`/`PATHEXT`/... and compare env names case-insensitively (Windows stores `SYSTEMROOT`) |
| `isolate.py` + `worker.py` | `AssertionError: pass_fds not supported on Windows` | the worker hands its result back through a file instead of an inherited pipe |
| `trainer/material.py` | `OSError: [WinError 1314]` on `learning_material/current` | fall back to a directory junction, which needs no privilege |
| `tree.py` | `PermissionError` deleting git's read-only object files | clear the read-only bit before `rmtree` |

The first two break *every* run; the last two break training only. A Linux host
needs none of them.

## What the verification proves

Last run of `scripts/verify-upstream.sh`:

```text
== Hermit quickstart (docs/_examples/first_training) ==
  check ..... ok
  train ..... ok
  eval ...... ok (mean_score 1.0)
== optimization export --target hermit, checked and rolled out by Hermit ==
  train ..... ok (exit 3)
  export .... ok
  check ..... ok (template, evolver, task, sandbox, agent probe)
  eval ...... ok (4 tasks, mean 1.0, errors 0)
== optimization export --target skillopt --lane native, parsed by skillopt-train ==
  config .... ok (all six sections parsed; stops at the env adapter, as documented)
```

Three things are worth reading from that output:

- **The quickstart moves 0.0 → 1.0.** The upstream teaching example starts at
  0.0 and ends at 1.0 with zero execution errors, so the install is functional
  and not merely importable.
- **`hermit check` accepts the generated `run.yaml`.** It reports `template`,
  `evolver`, `task`, `sandbox`, budgets and an `agent probe` at score 1.0. This
  is the check that caught two real bugs in `bridges/hermit.py`: `task_info`
  was nested under `data` (Hermit rejects the unknown key), and `sandbox.env: []`
  handed the worker no credentials, so the acting pipeline never reached its
  endpoint.
- **`hermit eval` rolls the export out.** 4 test tasks, mean 1.0, no errors, on
  a local stub endpoint. The adapter context, `judge.py`, `pipeline.py` and the
  split files are all exercised by Hermit's own worker.

## Live SkillOpt training run

The bundle ships its own environment adapter, so `env.name` resolves and the
native lane trains. One epoch against a real OpenAI-compatible server, with the
key passed through the environment only:

```bash
export OPENAI_COMPATIBLE_BASE_URL=... OPENAI_COMPATIBLE_API_KEY=... \
       OPENAI_COMPATIBLE_MODEL=...
python run_skillopt.py train --config skillopt.yaml \
    --cfg-options model.optimizer_backend=openai_compatible \
                  model.target_backend=openai_compatible \
                  model.optimizer=<id> model.target=<id> \
                  train.num_epochs=1 train.batch_size=4
```

| step | rollout hard | patches | action | best |
|---|---|---|---|---|
| 1 | 0.00 | 0 | `skip_no_patches` | 0.00 |
| 2 | 0.50 | 2 | `accept_new_best` | **0.75** |
| 3 | 0.50 | 2 | `reject` | 0.75 |
| 4 | 1.00 | 1 | `reject` | 0.75 |
| 5 | 0.75 | 0 | `skip_no_patches` | 0.75 |
| 6 | 0.25 | 0 | `skip_no_patches` | 0.75 |

The whole loop ran for real: rollouts, a persisted
`predictions/<id>/conversation.json` per case, reflection, patch merge, and a
validation gate that accepted one candidate and rejected two. The gate's two
rejections are the documented strict-improvement rule at work — a step whose
validation score did not move is not allowed to displace the artifact — not a
malfunction. `skills/skill_v0002.md` became `best_skill.md`.

Two things the run taught that the config contract does not say:

- **Both model ids must be set.** Overriding only the backends leaves
  `model.optimizer` / `model.target` at SkillOpt's documented default
  (`gpt-5.5`), and the first call fails with a model-not-supported error from
  the provider even though the transport is correct.
- **A failed analyst does not stop training.** When the provider answered `429`
  (free-tier quota), reflection failed and the step recorded
  `skip_no_patches`; the run continued and the artifact stayed at the last
  accepted version. Read the run's `history.json`, not its exit code, to see
  whether the later steps learned anything.

## Limits

- **No paid model.** The export check points both the acting pipeline and the
  `llm` section at a local OpenAI-compatible stub (`scripts/verify-upstream.sh`
  starts it), which always answers `answer`. Scores of 1.0 measure the wiring,
  not model quality.
- **SkillOpt's scores are not comparable.** The generated adapter is a small
  harness for *these* cases — one target call, token scoring — not one of
  SkillOpt's published benchmarks, so the 0.75 above measures this fixture and
  nothing else. `skillopt-train --config skillopt.yaml` directly still refuses
  `env.name`: only the bundle's `run_skillopt.py` registers the adapter in
  SkillOpt's registry. See [`skillopt-native.md`](skillopt-native.md).
- **Sandbox is unconfined on Windows.** Hermit's `required` enforcement needs
  bubblewrap, which is Linux-only, so the generated config asks for
  `best_effort`: Hermit runs the command, warns, and records that isolation was
  not enforced. Do not read a passing `check` here as proof of isolation.
- **Linux is untested.** The patch is guarded by `os.name`/`ImportError`, so a
  Linux host should take the original code paths, but this repository has only
  run the setup on Windows.

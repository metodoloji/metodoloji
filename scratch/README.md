# scratch/

Free experimentation area — no guard checks, no gate required.

## What goes here?

- Quick prototyping code
- One-off experiment scripts
- Temporary analysis files
- Investigation notes
- Benchmark trials (permanent ones move to `scripts/bench/`)

## What does NOT go here?

- Permanent production code (guard blocks it)
- Gate-passing experiment records (they go to `docs/experiments/`)
- Methodology outputs (they go to `docs/design/`, `docs/research/`, `docs/development/`)

## Rules

- Files under `scratch/` **require no gate** — freely writable
- But **measurement scripts** **cannot** live in scratch — they go to `scripts/bench/`
- Files in scratch may be added to `.gitignore` (if not permanent)
- Security patterns (`gate-key`, `secret`, `token`) are **forbidden** in scratch

## Organization

```
scratch/
├── README.md           ← this file
├── <experiment-name>/  ← each experiment in its own folder
│   ├── explore.py
│   └── notes.md
└── ...
```

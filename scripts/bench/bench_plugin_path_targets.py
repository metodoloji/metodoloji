#!/usr/bin/env python3
"""No shipped surface names a `{metodoloji-root}` path that does not exist (E-159).

    python3 scripts/bench/bench_plugin_path_targets.py --samples docs/experiments/E-159/raw/samples.jsonl
    python3 scripts/bench/bench_plugin_path_targets.py --broken   # rule-7 control

E-152 retired ten dead docs and pinned every LIVE surface against the retired
list, but it left its own Known Gap 5 open: the general invariant "every
`{metodoloji-root}/...` path a shipped surface names resolves" was never
measured, only the five targets that one repoint happened to name. E-158 then
measured the surfaces that EXECUTE a named artifact, and the surfaces that
merely NAME one stayed invisible.

They are not decorative. Measured 2026-11-14 on this tree: bmad-customize's
preflight says "No `{metodoloji-root}/bmad/` -> BMad isn't installed. Say so,
stop", and `bmad/` was retired in E-149, so the skill refuses to start for
every user. The rest are example paths a reader copies verbatim: three
`customize.toml` files and two builder assets still spell `bmad/style-guides/`,
`bmad/standards/`, `bmad-output/...`, and the override directory under the
plugin root instead of the project root.

The population is closed and DERIVED by glob, never hand-listed (E-039): four
families, one probe each, every file in each family enumerated.

  1. skills/**      (every shipped skill file: docs, configs, scripts, assets)
  2. templates/**   (the record/template surfaces an install copies)
  3. commands/**    (the slash-command runbooks)
  4. docs/*.md and docs/design/*.md (the shipped reader-facing docs)

Historical records (docs/experiments, docs/development, docs/quality,
docs/research) are provenance, not guidance, and are excluded by path.

`--broken` injects one phantom reference per family, so each probe can be seen
to fail on the same claim. Stdlib only, offline, deterministic.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
BROKEN = False

# The token, then the target. A trailing sentence punctuation mark is prose, not
# a path; a placeholder (`{...}`, `<...>`, `…`, `*`) names no single file.
_REF = re.compile(r"\{metodoloji-root\}/([^\s`\"'()\[\]<>|,;]+)")
_SUFFIXES = {".md", ".toml", ".json", ".py", ".sh", ".yaml", ".yml", ".csv", ".ts"}

FAMILIES = (
    ("skills", ("skills/**/*",)),
    ("templates", ("templates/**/*",)),
    ("commands", ("commands/*.md",)),
    ("docs", ("docs/*.md", "docs/design/*.md")),
)

def _surfaces(root: pathlib.Path, globs: tuple[str, ...]) -> list[pathlib.Path]:
    files = []
    for pattern in globs:
        files.extend(p for p in root.glob(pattern)
                     if p.is_file() and p.suffix in _SUFFIXES)
    return sorted(set(files))

def _targets(text: str) -> list[str]:
    """Every concrete target, skipping the ones a placeholder leaves open.

    A placeholder after the root (`custom/{skill-name}.toml`, `docs/…`) names no
    single file — its prefix is a directory, not a target. A literal path is one.
    """
    found = []
    for raw in _REF.findall(text):
        if re.search(r"[{<…*]", raw):
            continue
        target = raw.rstrip(".,;:")
        if target:
            found.append(target)
    return found

def _case_family(root: pathlib.Path, name: str,
                 globs: tuple[str, ...]) -> tuple[bool, str]:
    problems, checked = [], 0
    surfaces = _surfaces(root, globs)
    for path in surfaces:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            problems.append(f"{path.relative_to(root).as_posix()}: unreadable ({exc})")
            continue
        rel = path.relative_to(root).as_posix()
        for target in _targets(text):
            checked += 1
            if not (root / target.rstrip("/")).exists():
                problems.append(f"{rel} -> {{metodoloji-root}}/{target}")
    if BROKEN:
        checked += 1
        problems.append(f"phantom {name} -> {{metodoloji-root}}/ghost/{name}.md")
    if problems:
        return False, "; ".join(sorted(set(problems))[:4])
    return True, f"{checked} path(s) across {len(surfaces)} {name} surface(s) resolve"

def run(root: pathlib.Path) -> list[dict]:
    results = []
    for name, globs in FAMILIES:
        try:
            ok, detail = _case_family(root, name, globs)
        except Exception as exc:  # a crashed probe is a failed check, never a pass
            ok, detail = False, f"probe crashed: {type(exc).__name__}: {exc}"
        results.append({"check": f"every {name} path resolves", "ok": bool(ok),
                        "detail": detail})
    return results

def main() -> int:
    global BROKEN
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--broken", action="store_true", help="rule-7 control arm")
    parser.add_argument("--samples", help="write per-probe JSONL samples here")
    args = parser.parse_args()
    BROKEN = args.broken

    results = run(ROOT)
    for result in results:
        print(f"  {'ok  ' if result['ok'] else 'FAIL'}  {result['check']}: {result['detail']}")
    passed = sum(1 for result in results if result["ok"])
    if args.samples:
        path = pathlib.Path(args.samples)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(result, ensure_ascii=False) + "\n"
                                for result in results), encoding="utf-8")
    print(f"plugin_path_accuracy={passed / len(results):.2f} ({passed}/{len(results)})")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

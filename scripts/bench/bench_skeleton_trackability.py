#!/usr/bin/env python3
"""Every skeleton directory survives a fresh clone (E-233).

    python3 scripts/bench/bench_skeleton_trackability.py --samples docs/experiments/E-233/raw/samples.jsonl
    python3 scripts/bench/bench_skeleton_trackability.py --broken   # rule-7 control

`hooks/scripts/skeleton.py` declares twelve project directories. Empty
directories do not survive git, so a clone of a project that ran init loses
them and `skeleton.py --status` reports an incomplete skeleton. The installer
therefore writes an empty `.gitkeep` into every directory it leaves empty.

The population is the twelve `skeleton.DIRECTORIES` entries, each measured in
two contexts: the plugin tree (the dogfood copy) and a fresh `install()` in a
temp project. A probe passes only when both contain at least one file.

`--broken` removes the placeholders from the fresh install, reproducing the
pre-fix shape (the three empty design directories, 9/12).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hooks" / "scripts"))
import skeleton  # noqa: E402  (the one owner of the directory contract)

BROKEN = False


def _has_file(path: pathlib.Path) -> bool:
    return path.is_dir() and any(child.is_file() for child in path.iterdir())


def _install_without_placeholders(project: pathlib.Path) -> None:
    """The pre-fix installer: create/copy everything, then leave empty dirs."""
    skeleton.install(project)
    for rel in skeleton.DIRECTORIES:
        target = project / rel
        placeholder = target / skeleton.PLACEHOLDER_NAME
        if placeholder.is_file() and len(list(target.iterdir())) == 1:
            placeholder.unlink()


def run(project: pathlib.Path) -> list[dict]:
    results = []
    for rel in skeleton.DIRECTORIES:
        repo_has = _has_file(ROOT / rel)
        installed_has = _has_file(project / rel)
        ok = repo_has and installed_has
        results.append({
            "check": f"{rel} carries a trackable file",
            "ok": ok,
            "detail": f"repo={repo_has} installed={installed_has}",
        })
    return results


def main() -> int:
    global BROKEN
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", default=None,
                    help="write one JSON object per line here (the gate recomputes x/n)")
    ap.add_argument("--broken", action="store_true",
                    help="rule-7 control: drop the placeholders the fix writes")
    args = ap.parse_args()
    BROKEN = args.broken

    with tempfile.TemporaryDirectory(prefix="skeleton-track-") as td:
        project = pathlib.Path(td) / "project"
        if BROKEN:
            _install_without_placeholders(project)
        else:
            skeleton.install(project)
        results = run(project)

    if args.samples:
        path = pathlib.Path(args.samples)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for item in results:
                fh.write(json.dumps(item, ensure_ascii=False) + "\n")

    ok = sum(1 for item in results if item["ok"])
    n = len(results)
    for item in results:
        print(f"  {'ok  ' if item['ok'] else 'FAIL'}  {item['check']}: {item['detail']}")
    print(f"skeleton_trackability_accuracy={ok / n:.2f} ({ok}/{n})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

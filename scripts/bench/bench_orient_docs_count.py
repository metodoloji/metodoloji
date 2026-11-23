#!/usr/bin/env python3
"""The orient docs line names the areas it withholds (E-218).

The docs line bounded its area list at twelve and then appended a bare
`… +N`, so a tree with fifteen areas listed twelve and read as complete, and
the reader could not tell areas from files. The marker now names its unit
(area).

Claim, closed set: a set of twelve or fewer areas prints whole with no marker;
thirteen prints `… (+1 more area)`; fifteen prints `… (+3 more areas)`; twenty
prints `… (+8 more areas)`; and the unit is always present.

Control: `--broken` runs the same probes against `PREFIX_REV`, where the tail
is a bare `… +N`.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import tarfile
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
PREFIX_REV = "f88ce5f"  # E-210 commit: the bare `… +N` marker

PROBES = (
    "five_areas_whole",
    "twelve_areas_whole",
    "thirteen_areas_singular",
    "fifteen_areas_plural",
    "twenty_areas_larger",
)


def _docs(root: pathlib.Path, count: int) -> None:
    docs = root / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    for i in range(1, count + 1):
        d = docs / f"area{i}"
        d.mkdir()
        (d / "note.md").write_text("# x\n", encoding="utf-8")


def _panel(tree: pathlib.Path, proj: pathlib.Path) -> str:
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(proj))
    env.pop("OPENHANDS_PROJECT_DIR", None)
    result = subprocess.run(
        [sys.executable, str(tree / "hooks" / "scripts" / "orient.py"),
         "--project-root", str(proj)],
        capture_output=True, text=True, cwd=str(tree), env=env,
        stdin=subprocess.DEVNULL)
    return result.stdout + result.stderr


def _docs_line(text: str) -> str:
    for line in text.splitlines():
        if "docs            :" in line:
            return line
    return ""


def _probe(name: str, tree: pathlib.Path) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as raw:
        proj = pathlib.Path(raw) / "proj"
        proj.mkdir()
        counts = {
            "five_areas_whole": 5,
            "twelve_areas_whole": 12,
            "thirteen_areas_singular": 13,
            "fifteen_areas_plural": 15,
            "twenty_areas_larger": 20,
        }[name]
        _docs(proj, counts)
        line = _docs_line(_panel(tree, proj))
        if name in ("five_areas_whole", "twelve_areas_whole"):
            ok = "more" not in line and "docs            :" in line
        elif name == "thirteen_areas_singular":
            ok = "… (+1 more area)" in line
        elif name == "fifteen_areas_plural":
            ok = "… (+3 more areas)" in line
        else:
            ok = "… (+8 more areas)" in line
        return ok, line.strip()[-80:]


def driver(name: str, tree: pathlib.Path) -> None:
    ok, detail = _probe(name, tree)
    print(json.dumps({"ok": ok, "detail": detail}, ensure_ascii=False))


def _prefix_tree(base: pathlib.Path) -> pathlib.Path:
    archive = base / "prefix.tar"
    with archive.open("wb") as handle:
        subprocess.run(["git", "archive", PREFIX_REV], cwd=ROOT,
                       stdout=handle, stdin=subprocess.DEVNULL, check=True)
    tree = base / "prefix"
    tree.mkdir()
    with tarfile.open(archive) as tar:
        tar.extractall(tree)
    return tree


def _run(tree: pathlib.Path, samples: pathlib.Path | None, broken: bool) -> bool:
    rows, passed = [], 0
    for name in PROBES:
        result = subprocess.run(
            [sys.executable, str(pathlib.Path(__file__).resolve()), "--driver", name,
             "--tree", str(tree)], capture_output=True, text=True, cwd=str(tree),
            stdin=subprocess.DEVNULL)
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            payload = {"ok": False, "detail": (result.stderr or result.stdout)[-200:]}
        ok = bool(payload.get("ok"))
        passed += 1 if ok else 0
        rows.append({"probe": name, "ok": ok, "detail": payload.get("detail", "")})
        print(f"  {'PASS' if ok else 'FAIL'}  {name}  — {payload.get('detail', '')}")
    total = len(PROBES)
    value = passed / total
    print(f"orient_docs_count_accuracy={value:.2f} ({passed}/{total})")
    if samples is not None:
        samples.parent.mkdir(parents=True, exist_ok=True)
        with samples.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps({"broken": broken, **row}, ensure_ascii=False) + "\n")
    return passed == total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=pathlib.Path, default=None)
    parser.add_argument("--broken", action="store_true")
    parser.add_argument("--driver", default=None)
    parser.add_argument("--tree", type=pathlib.Path, default=None)
    args = parser.parse_args()

    if args.driver:
        driver(args.driver, args.tree or ROOT)
        return 0

    with tempfile.TemporaryDirectory() as tmp:
        tree = _prefix_tree(pathlib.Path(tmp)) if args.broken else ROOT
        print(f"tree: {tree}")
        ok = _run(tree, args.samples, args.broken)
    return 0 if (ok or args.broken) else 1


if __name__ == "__main__":
    raise SystemExit(main())

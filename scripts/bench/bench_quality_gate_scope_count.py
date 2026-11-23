#!/usr/bin/env python3
"""The scope-coverage warning names its withheld paths (E-217).

A commit that touches files outside every approved Code Scope produces:

    SCOPE COVERAGE: a, b, c (+N more) — outside every approved Code Scope; ...

The tail carried no unit, so the reader could not tell withheld files from
withheld records. The warning is the sentence the agent acts on, so the count
names its unit (path).

Claim, closed set: the warning lists up to three paths and counts the rest as
paths, a set of three or fewer prints whole with no marker, and the unit is
always present.

Control: `--broken` runs the same probes against `PREFIX_REV`, where the tail
is a bare `… (+N more)`.
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
PREFIX_REV = "f88ce5f"  # E-210 commit: the bare (+N more) marker

PROBES = (
    "one_file_whole",
    "three_files_whole",
    "four_files_singular",
    "five_files_plural",
    "six_files_larger",
)


def _repo(root: pathlib.Path, count: int) -> None:
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=root, check=True,
                       stdin=subprocess.DEVNULL)

    git("init", "-q")
    git("config", "user.email", "t@t.com")
    git("config", "user.name", "t")
    (root / "a.txt").write_text("x", encoding="utf-8")
    git("add", "a.txt")
    git("commit", "-q", "-m", "init")
    for i in range(1, count + 1):
        (root / f"f{i}.txt").write_text("y", encoding="utf-8")


def _warning(tree: pathlib.Path, root: pathlib.Path) -> str:
    script = (
        "import sys, os, json\n"
        "sys.path[:0] = [str(__import__('pathlib').Path(sys.argv[1]) / 'hooks' / 'engine')]\n"
        "os.environ['CLAUDE_PROJECT_DIR'] = sys.argv[2]\n"
        "from modules import guard, quality_gate\n"
        "res = quality_gate.quality(\n"
        "    {'tool_name': 'Bash', 'tool_input': {'command': 'git commit -m x'}},\n"
        "    guard_module=guard)\n"
        "ws = res.get('methodology_warnings') or []\n"
        "print(json.dumps({'warn': ws[0] if ws else ''}))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(tree), str(root)],
        capture_output=True, text=True, cwd=str(tree),
        stdin=subprocess.DEVNULL, env=dict(os.environ, CLAUDE_PROJECT_DIR=str(root)))
    try:
        return str(json.loads(result.stdout).get("warn") or "")
    except (json.JSONDecodeError, AttributeError):
        return ""


def _probe(name: str, tree: pathlib.Path) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as raw:
        root = pathlib.Path(raw)
        counts = {
            "one_file_whole": 1,
            "three_files_whole": 3,
            "four_files_singular": 4,
            "five_files_plural": 5,
            "six_files_larger": 6,
        }[name]
        _repo(root, counts)
        warn = _warning(tree, root)
        if name in ("one_file_whole", "three_files_whole"):
            ok = "more" not in warn and "SCOPE COVERAGE:" in warn
        elif name == "four_files_singular":
            ok = "… (+1 more path)" in warn
        elif name == "five_files_plural":
            ok = "… (+2 more paths)" in warn
        else:
            ok = "… (+3 more paths)" in warn
        return ok, warn[-70:]


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
    print(f"quality_gate_scope_count_accuracy={value:.2f} ({passed}/{total})")
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

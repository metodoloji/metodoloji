#!/usr/bin/env python3
"""The stop report counts every in-progress story its bound withholds (E-216).

The stop report lists in-progress stories as `1-1-x, 1-2-x, …, 1-5-x, … (+3 more)`:
a bare remainder with no unit, so a session with eight in-progress stories read
as five and the reader could not tell stories from files.

Claim, closed set: the list names its unit (`story` / `stories`) and the true
count, prints a set that fits the bound verbatim with no marker, and stays
silent about the remainder when nothing is withheld.

Control: `--broken` runs the same probes against `PREFIX_REV`, where the tail
is a bare `… (+N more)`.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import tarfile
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
PREFIX_REV = "f88ce5f"  # E-210 commit: the bare (+N more) marker

PROBES = (
    "three_stories_whole",
    "five_stories_whole",
    "six_stories_singular",
    "seven_stories_plural",
    "nine_stories_larger",
)


def _sprint(root: pathlib.Path, count: int) -> None:
    base = root / "docs" / "development" / "native"
    base.mkdir(parents=True, exist_ok=True)
    (base / "sprint-status.yaml").write_text(
        "development_status:\n" + "".join(
            f"  1-{i}-x: in-progress\n" for i in range(1, count + 1)) + "\n",
        encoding="utf-8")


def _report(tree: pathlib.Path, root: pathlib.Path) -> str:
    script = (
        "import sys, json\n"
        "sys.path[:0] = ['hooks/engine']\n"
        "from modules import stop\n"
        "out = stop.stop({'cwd': sys.argv[1], 'hook_event_name': 'Stop'})\n"
        "print(json.dumps({'reason': out.get('reason', '')}))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(root)],
        capture_output=True, text=True, cwd=str(tree),
        stdin=subprocess.DEVNULL)
    try:
        return str(json.loads(result.stdout).get("reason") or "")
    except (json.JSONDecodeError, AttributeError):
        return ""


def _probe(name: str, tree: pathlib.Path) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as raw:
        root = pathlib.Path(raw)
        counts = {
            "three_stories_whole": 3,
            "five_stories_whole": 5,
            "six_stories_singular": 6,
            "seven_stories_plural": 7,
            "nine_stories_larger": 9,
        }[name]
        _sprint(root, counts)
        reason = _report(tree, root)
        if name in ("three_stories_whole", "five_stories_whole"):
            ok = "more" not in reason and "in-progress stories:" in reason
        elif name == "six_stories_singular":
            ok = "… (+1 more story)" in reason
        elif name == "seven_stories_plural":
            ok = "… (+2 more stories)" in reason
        else:
            ok = "… (+4 more stories)" in reason
        return ok, reason[-70:]


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
    print(f"stop_story_count_accuracy={value:.2f} ({passed}/{total})")
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

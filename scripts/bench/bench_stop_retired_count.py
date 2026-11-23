#!/usr/bin/env python3
"""The stop report counts every retirement its bound withholds (E-221).

The stop report listed claims retired this session as `a, b, …, e, … (+N more)`:
a bare remainder with no unit, so a session retiring eight claims read as five.

Claim, closed set: the list names its unit (`target` / `targets`) and the true
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
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
PREFIX_REV = "f88ce5f"  # E-210 commit: the bare (+N more) marker

PROBES = (
    "three_retired_whole",
    "five_retired_whole",
    "six_retired_singular",
    "seven_retired_plural",
    "nine_retired_larger",
)


def _setup(root: pathlib.Path, count: int) -> None:
    # Session-start marker older than the retirements, so the session window
    # keeps them (stop filters retired entries by ts >= session_start).
    log = root / ".metodoloji" / "logs" / "hook-audit.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(json.dumps({"type": "session_start", "hook_event": "SessionStart",
                              "session_id": "bench", "timestamp": time.time() - 100})
                   + "\n", encoding="utf-8")
    sys.path[:0] = [str(ROOT / "hooks" / "engine")]
    from modules import blackboard as bb
    for i in range(1, count + 1):
        bb.retire(str(root), f"target{i}", reason="bench")


def _report(tree: pathlib.Path, root: pathlib.Path) -> str:
    script = (
        "import sys, json, os\n"
        "os.environ['CLAUDE_PROJECT_DIR'] = sys.argv[1]\n"
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
        return result.stderr or result.stdout or ""


def _probe(name: str, tree: pathlib.Path) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as raw:
        root = pathlib.Path(raw)
        counts = {"three_retired_whole": 3, "five_retired_whole": 5,
                  "six_retired_singular": 6, "seven_retired_plural": 7,
                  "nine_retired_larger": 9}[name]
        _setup(root, counts)
        reason = _report(tree, root)
        if name in ("three_retired_whole", "five_retired_whole"):
            ok = "more" not in reason and "retired this session:" in reason
        elif name == "six_retired_singular":
            ok = "… (+1 more target)" in reason
        elif name == "seven_retired_plural":
            ok = "… (+2 more targets)" in reason
        else:
            ok = "… (+4 more targets)" in reason
        return ok, reason[-80:]


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
    print(f"stop_retired_count_accuracy={value:.2f} ({passed}/{total})")
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

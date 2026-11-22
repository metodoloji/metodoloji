#!/usr/bin/env python3
"""The session-start workflow offer counts every story its bound withholds (E-213).

The offer names the in-flight stories that no declared run claims (E-186). It
printed at most `_OFFER_LIMIT` = 2 and then stopped scanning one row past the
bound, so its tail could only ever read `(+1 more)` — a project with five
in-progress stories and no run was told three, and the marker said no unit:

    PROACTIVE — no declared workflow run for S-101 (in-progress), S-102 (in-progress) (+1 more): ...

The count was the same `+1` for three stories and for thirty, which is the
failure mode the whole bounded-list rule exists to prevent.

Claim, closed set: the sentence names the whole withheld remainder with its
unit (`stories` / `story`), prints a set that fits the bound verbatim with no
marker, and stays silent once a run claims the story.

Control: `--broken` runs the same probes against `PREFIX_REV`, where the tail
is a bare `(+1 more)` however many stories the bound cut.
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
PREFIX_REV = "f88ce5f"  # E-210 commit: the scan that could only count one

PROBES = (
    "single_story_whole",
    "two_stories_whole",
    "remainder_counted",
    "singular_remainder",
    "claimed_story_silent",
)


def _stories(root: pathlib.Path, count: int) -> None:
    base = root / "docs" / "development" / "stories"
    base.mkdir(parents=True, exist_ok=True)
    for n in range(101, 101 + count):
        (base / f"S-{n}.md").write_text(
            f"# Story: S-{n} — demo\n\n- **Status:** in-progress\n",
            encoding="utf-8")


def _offer(tree: pathlib.Path, root: pathlib.Path) -> str:
    script = (
        "import sys, json\n"
        "sys.path[:0] = ['hooks/engine', 'hooks/scripts', '.']\n"
        "from modules import audit\n"
        "print(json.dumps({'offer': audit._workflow_offer(sys.argv[1])}))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(root)],
        capture_output=True, text=True, cwd=str(tree),
        stdin=subprocess.DEVNULL)
    try:
        return str(json.loads(result.stdout).get("offer") or "")
    except (json.JSONDecodeError, AttributeError):
        return ""


def _probe(name: str, tree: pathlib.Path) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as raw:
        root = pathlib.Path(raw)

        if name == "single_story_whole":
            _stories(root, 1)
            line = _offer(tree, root)
            ok = ("for S-101 (in-progress):" in line and "…" not in line)
            return ok, line[:90]
        if name == "two_stories_whole":
            _stories(root, 2)
            line = _offer(tree, root)
            ok = ("S-101 (in-progress)" in line and "S-102 (in-progress)" in line
                  and "more" not in line)
            return ok, line[:120]
        if name == "remainder_counted":
            _stories(root, 5)
            line = _offer(tree, root)
            ok = "… (+3 more stories)" in line and "(+1 more)" not in line
            return ok, line[:140]
        if name == "singular_remainder":
            _stories(root, 3)
            line = _offer(tree, root)
            ok = "… (+1 more story)" in line
            return ok, line[:140]
        if name == "claimed_story_silent":
            _stories(root, 1)
            runs = root / ".metodoloji" / "workflow"
            runs.mkdir(parents=True, exist_ok=True)
            (runs / "s-101.state.json").write_text(
                json.dumps({"status": "running"}), encoding="utf-8")
            line = _offer(tree, root)
            ok = line == ""
            return ok, line[:90]
        raise SystemExit(f"unknown probe: {name}")


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
    print(f"workflow_offer_count_accuracy={value:.2f} ({passed}/{total})")
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

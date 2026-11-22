#!/usr/bin/env python3
"""The orientation digest counts the stories its step bound withheld (E-201).

The digest turns live sprint state into next steps: one line per story in
'review' and per story in-progress. Both lists were sliced at three with no
marker, so a board with five review and five in-progress stories produced three
steps each and the digest read as the whole backlog — the agent opens the first
three, finds them fine, and never learns two more stories are waiting in review.

Claim, closed set: on every digest run, the first three live stories per status
are named and any remainder is counted with the sprint file's own total; a
board that fits carries no remainder line; the named keys are the file's keys;
the text render shows the same counted line; the ready-story fallback still
fires when no live story exists; and orient.py carries no bare `[:3]` step
slice.

Control: `--broken` runs the same probes against `PREFIX_REV`, whose digest
slices both lists at three.
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
PREFIX_REV = "bad08ce"  # HEAD before E-201: for k in in_review[:3]

PROBES = (
    "review_remainder_counted",
    "in_progress_remainder_counted",
    "count_matches_the_file",
    "named_stories_whole",
    "fitting_board_unmarked",
    "text_render_carries_it",
    "ready_fallback_kept",
    "no_bare_step_slice",
)

_REL = "docs/development/native/sprint-status.yaml"


def _board(root: pathlib.Path, rows: list[str]) -> pathlib.Path:
    path = root / _REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("development_status:\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return root


def _steps(tree: pathlib.Path, root: pathlib.Path) -> list[str]:
    sys.path.insert(0, str(tree / "hooks" / "scripts"))
    sys.path.insert(0, str(tree / "hooks" / "engine"))
    import orient
    return orient.build(root)["next_steps"]


def _probe(name: str, tree: pathlib.Path) -> tuple[bool, str]:
    if name == "no_bare_step_slice":
        code = (tree / "hooks" / "scripts" / "orient.py").read_text(encoding="utf-8")
        ok = "in_review[:3]" not in code and "in_prog[:3]" not in code
        return ok, "both loops name their limit and count the remainder"

    root = _board(pathlib.Path(tempfile.mkdtemp(prefix="orient-steps-")),
                  [f"  1-{i}-s{i}: review" for i in range(1, 6)]
                  + [f"  2-{i}-t{i}: in-progress" for i in range(1, 6)])

    if name == "ready_fallback_kept":
        other = _board(pathlib.Path(tempfile.mkdtemp(prefix="orient-ready-")),
                       ["  3-1-first: ready-for-dev", "  3-2-second: backlog"])
        steps = _steps(tree, other)
        ok = any("3-1-first" in s for s in steps)
        return ok, f"ready={[s[:40] for s in steps if '3-1' in s]}"

    if name == "fitting_board_unmarked":
        other = _board(pathlib.Path(tempfile.mkdtemp(prefix="orient-fit-")),
                       ["  1-1-a: review", "  2-1-b: in-progress"])
        steps = _steps(tree, other)
        named = sum(1 for s in steps if s.startswith("story "))
        ok = named == 2 and not any("more stories" in s for s in steps)
        return ok, f"named={named}"

    if name == "text_render_carries_it":
        result = subprocess.run(
            [sys.executable, str(tree / "hooks" / "scripts" / "orient.py"),
             "--project-root", str(root)], capture_output=True, text=True,
            cwd=str(tree), stdin=subprocess.DEVNULL)
        ok = "2 more stories also sit in 'review'" in result.stdout
        return ok, f"stdout={'2 more stories' in result.stdout}"

    if name == "count_matches_the_file":
        other = _board(pathlib.Path(tempfile.mkdtemp(prefix="orient-7-")),
                       [f"  1-{i}-s{i}: review" for i in range(1, 8)])
        steps = _steps(tree, other)
        ok = any("4 more stories also sit in 'review'" in s and "lists 7" in s
                 for s in steps)
        return ok, f"match={[s[:60] for s in steps if 'more stories' in s]}"

    steps = _steps(tree, root)
    if name == "review_remainder_counted":
        ok = any("2 more stories also sit in 'review'" in s for s in steps)
        return ok, f"review_steps={sum(1 for s in steps if 'review' in s)}"
    if name == "in_progress_remainder_counted":
        ok = any("2 more stories are also in-progress" in s for s in steps)
        return ok, f"prog_steps={sum(1 for s in steps if 'in-progress' in s)}"
    if name == "named_stories_whole":
        named = [s.split(" ", 2)[1] for s in steps if s.startswith("story ")]
        expected = {f"1-{i}-s{i}" for i in range(1, 4)} | {f"2-{i}-t{i}" for i in range(1, 4)}
        ok = set(named) == expected
        return ok, f"named={named}"
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
    print(f"orient_step_bound_accuracy={value:.2f} ({passed}/{total})")
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

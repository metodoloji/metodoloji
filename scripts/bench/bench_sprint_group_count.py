#!/usr/bin/env python3
"""The sprint line's status group counts the stories it withholds (E-211).

`state.format_sprint_line` bounds each status group to three names. It printed
the remainder as a bare `, …`:

    sprint: 16 done (1-1-change-radar-contract-measured,
    10-1-forced-completion-honest, 11-1-feasibility-guard, …)

Live on this tree 2026-11-22: sixteen stories are `done`, three were named, and
the lone ellipsis could mean one more or a hundred. The same line feeds the
session-start inject, the stop report and orient, so the reader sizing "how much
is left" sized it from three names. The parts around it already use the shared
counted form (E-196…E-198); the group was the last bare marker in the line.

Claim, closed set: a wide status group counts its withheld stories; no bare
`, …` remains in the line; a group of three or fewer prints whole with no
marker; a single withheld story reads "+1 more story"; the shared join pluralises
a consonant+y noun ("stories") and keeps plain nouns; and the 200-character bound
still holds on a forty-story board.

Control: `--broken` runs the same probes against `PREFIX_REV`, where the group
prints `, …` with no count and the join appends a bare `s` to every noun.
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
PREFIX_REV = "f88ce5f"  # E-210 commit: the bare `, …` group
LIMIT = 200
_SPRINT_REL = "docs/development/native/sprint-status.yaml"


def _write_sprint(root: pathlib.Path, rows: list[str]) -> None:
    path = root / _SPRINT_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("development_status:\n" + "\n".join(rows) + "\n",
                    encoding="utf-8")


def _line(tree: pathlib.Path, root: pathlib.Path) -> str:
    sys.path.insert(0, str(tree / "hooks" / "engine"))
    from modules import state
    return state.format_sprint_line(state.sprint_summary(root))


def _counted(tree: pathlib.Path, items: list[str], limit: int, noun: str) -> str:
    sys.path.insert(0, str(tree / "hooks" / "engine"))
    from modules.counting import counted_list
    return counted_list(items, limit, noun=noun)


def _probe(name: str, tree: pathlib.Path) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "proj"
        root.mkdir()

        if name == "group_counts_remainder":
            _write_sprint(root, [f"  1-{i}-story-{i}: done" for i in range(1, 17)])
            line = _line(tree, root)
            ok = "16 done (" in line and "(+13 more stories)" in line
            return ok, line[:150]
        if name == "no_bare_ellipsis":
            _write_sprint(root, [f"  1-{i}-story-{i}: done" for i in range(1, 17)])
            line = _line(tree, root)
            ok = ", …)" not in line and ("…" not in line or "(+" in line)
            return ok, f"markers={line.count('…')} tail={line[-40:]!r}"
        if name == "small_group_whole":
            _write_sprint(root, ["  1-1-a: done", "  1-2-b: done", "  1-3-c: done"])
            line = _line(tree, root)
            ok = "3 done (1-1-a, 1-2-b, 1-3-c)" in line and "more" not in line
            return ok, line[:120]
        if name == "singular_remainder":
            _write_sprint(root, [f"  1-{i}-story-{i}: in-progress"
                                 for i in range(1, 5)])
            line = _line(tree, root)
            ok = "(+1 more story)" in line
            return ok, line[:140]
        if name == "plural_rule":
            stories = _counted(tree, ["a", "b", "c", "d"], 2, "story")
            paths = _counted(tree, ["a", "b", "c", "d"], 2, "path")
            ok = stories.endswith("(+2 more stories)") and paths.endswith("(+2 more paths)")
            return ok, f"story={stories!r} path={paths!r}"
        if name == "bound_holds":
            _write_sprint(root, [f"  1-{i}-story-{i}-with-a-longer-slug: done"
                                 for i in range(1, 41)])
            line = _line(tree, root)
            return len(line) <= LIMIT, f"len={len(line)}"
        raise SystemExit(f"unknown probe: {name}")


PROBES = (
    "group_counts_remainder",
    "no_bare_ellipsis",
    "small_group_whole",
    "singular_remainder",
    "plural_rule",
    "bound_holds",
)


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
    print(f"sprint_group_count_accuracy={value:.2f} ({passed}/{total})")
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

#!/usr/bin/env python3
"""The sprint line names the unit of every withheld part (E-225).

`state._bounded_parts_line` drops whole parts when the 200-character bound is
reached and printed the remainder as a bare marker:

    sprint: 8 in-progress (…); 5 review (…); … (+3 more)

E-203…E-224 named the unit on every other bounded list (stories, paths, stages,
servers, areas, codes, alerts). This one still left the reader to guess whether
the withheld entries were stories, epic warnings or status groups — on the line
the session-start inject, the stop report and orient all print.

Claim, closed set: every withheld part names its unit, singular for one and
plural for more; the marker fits inside the same 200-character bound; a line
that fits still prints with no marker; a lone oversized part keeps its
remainder; the bare `(+N more)` regex finds nothing; and the session-start
inject carries the unit. All probes run the shipped formatter or the shipped
session edge; nothing is sampled.

Control: `--broken` runs the same probes against `PREFIX_REV`, the tree before
this change, where the marker is bare (five of seven probes fail).
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import tarfile
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
PREFIX_REV = "2497e68"  # HEAD before E-225: the bare `; … (+N more)` marker
LIMIT = 200
_SPRINT_REL = "docs/development/native/sprint-status.yaml"

PROBES = (
    "fits_unmarked",
    "singular_remainder",
    "plural_remainder",
    "oversized_remainder",
    "no_bare_marker",
    "bound_holds",
    "live_inject_uses_unit",
)


def _write_sprint(root: pathlib.Path, rows: list[str]) -> None:
    path = root / _SPRINT_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("development_status:\n" + "\n".join(rows) + "\n",
                    encoding="utf-8")


def _wide(root: pathlib.Path) -> None:
    rows = [f"  1-{i}-story-{i}: in-progress" for i in range(1, 9)]
    rows += [f"  1-{i}-story-{i}: review" for i in range(11, 16)]
    rows += [f"  2-{i}-hero-{i}: done" for i in range(1, 4)]
    for n in range(2, 4):
        rows += [f"  {n}-1-slice: done", f"  epic-{n}: in-progress"]
    _write_sprint(root, rows)


def _line(tree: pathlib.Path, root: pathlib.Path) -> str:
    sys.path.insert(0, str(tree / "hooks" / "engine"))
    from modules import state
    return state.format_sprint_line(state.sprint_summary(root))


def _parts(tree: pathlib.Path, parts: list[str], limit: int) -> str:
    sys.path.insert(0, str(tree / "hooks" / "engine"))
    from modules import state
    return state._bounded_parts_line("sprint: ", parts, limit=limit)


def _probe(name: str, tree: pathlib.Path) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "proj"
        root.mkdir()

        if name == "fits_unmarked":
            _write_sprint(root, ["  1-1-login: in-progress", "  1-2-out: done"])
            line = _line(tree, root)
            ok = "…" not in line and "more" not in line
            return ok, f"line={line!r}"

        if name == "singular_remainder":
            line = _parts(tree, ["a" * 80, "b" * 80], 120)
            ok = "(+1 more entry)" in line and "(+1 more)" not in line
            return ok, f"line={line!r}"

        if name == "plural_remainder":
            line = _parts(tree, ["a" * 40, "b" * 40, "c" * 40, "d" * 40], 90)
            ok = "(+3 more entries)" in line and "(+3 more)" not in line
            return ok, f"line={line!r}"

        if name == "oversized_remainder":
            line = _parts(tree, ["x" * 300, "b"], 50)
            ok = "(+1 more entry)" in line and "(+1 more)" not in line
            return ok, f"line={line!r}"

        if name == "no_bare_marker":
            line = _parts(tree, ["a" * 40, "b" * 40, "c" * 40, "d" * 40], 90)
            ok = re.search(r"\(\+\d+ more\)", line) is None and "more entr" in line
            return ok, f"line={line!r}"

        if name == "bound_holds":
            _wide(root)
            line = _line(tree, root)
            return len(line) <= LIMIT, f"len={len(line)}"

        if name == "live_inject_uses_unit":
            _wide(root)
            os.environ["CLAUDE_PROJECT_DIR"] = str(root)
            os.environ.pop("OPENHANDS_PROJECT_DIR", None)
            sys.path.insert(0, str(tree / "hooks" / "engine"))
            from modules.audit import session_start
            text = session_start({"cwd": str(root)}).get("additionalContext", "")
            ok = ("more entries)" in text or "more entry)" in text) \
                and "more)" not in text.replace("more entries)", "")
            return ok, f"tail={text[-120:]!r}"

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
    print(f"sprint_parts_count_accuracy={value:.2f} ({passed}/{total})")
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
        base = pathlib.Path(tmp)
        tree = _prefix_tree(base) if args.broken else ROOT
        print(f"tree: {tree}")
        ok = _run(tree, args.samples, args.broken)
    # The control is expected to miss the claim; a non-zero exit would read as
    # a crashed measurement instead of a measured refutation (rule 7).
    return 0 if (ok or args.broken) else 1


if __name__ == "__main__":
    raise SystemExit(main())

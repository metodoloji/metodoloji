#!/usr/bin/env python3
"""The sprint line cuts on a part boundary and counts what it withheld (E-198).

Found live on this tree, 2026-11-22, driving the shipped formatter over a
realistic board (8 in-progress, 5 review, 3 done, one lagging epic):

    sprint: 8 in-progress (…); 5 review (…); 3 done (2-1-hero, 2-2-nav,
    2-3-foot); ⚠ epic-2: stories all done — epic …

The bound sliced the finished line at 197 characters. So the session-start
inject (audit.session_start) and the stop/orient renderings of the same line
delivered half a discrepancy — the finding without its action ("roll up to done
+ run the retro") — and every ⚠ past the cut disappeared behind one bare `…`.
A part is the semantic unit here (a status group or one epic mismatch), and a
cut inside a part turns a report into a fragment.

Claim, closed set: on every surface that renders state.format_sprint_line, the
line is bounded at 200 characters by dropping whole parts and naming how many
it dropped; epic warnings lead, so a board wide enough to overflow still shows
its discrepancies; a line that fits prints with no marker; and one part wider
than the whole line is elided on a word boundary with an explicit remainder.

Control: `--broken` runs the same probes against the pre-change tree
(`PREFIX_REV`), whose formatter still cuts at 197 characters.
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
PREFIX_REV = "bad08ce"  # HEAD before E-198: the character slice at 197
LIMIT = 200

PROBES = (
    "warning_kept_whole",
    "warnings_lead",
    "remainder_counted",
    "bound_respected_wide",
    "bound_respected_extreme",
    "fits_unmarked",
    "status_only_board_intact",
    "lone_oversized_part_elided",
    "live_inject_keeps_the_action",
    "no_character_slice_left",
)

_ACTION = "roll up to done + run the retro"
_SPRINT_REL = "docs/development/native/sprint-status.yaml"


def _write_sprint(root: pathlib.Path, rows: list[str]) -> None:
    path = root / _SPRINT_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("development_status:\n" + "\n".join(rows) + "\n",
                    encoding="utf-8")


def _wide(root: pathlib.Path, lags: int = 2) -> None:
    rows = [f"  1-{i}-story-{i}: in-progress" for i in range(1, 9)]
    rows += [f"  1-{i}-story-{i}: review" for i in range(11, 16)]
    rows += [f"  2-{i}-hero-{i}: done" for i in range(1, 4)]
    for n in range(2, 2 + lags):
        rows += [f"  {n}-1-slice: done", f"  epic-{n}: in-progress"]
    _write_sprint(root, rows)


def _summary(tree: pathlib.Path, root: pathlib.Path) -> dict:
    sys.path.insert(0, str(tree / "hooks" / "engine"))
    from modules import state
    return state.sprint_summary(root)


def _line(tree: pathlib.Path, root: pathlib.Path) -> str:
    summary = _summary(tree, root)
    sys.path.insert(0, str(tree / "hooks" / "engine"))
    from modules import state
    return state.format_sprint_line(summary)


def _probe(name: str, tree: pathlib.Path) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "proj"
        root.mkdir()

        if name == "no_character_slice_left":
            code = (tree / "hooks" / "engine" / "modules" / "state.py").read_text(
                encoding="utf-8")
            code = "\n".join(line for line in code.splitlines()
                             if not line.lstrip().startswith("#"))
            ok = "line = line[:197]" not in code and "[:197]" not in code
            return ok, "formatter no longer slices the finished line"

        if name == "live_inject_keeps_the_action":
            _wide(root)
            os.environ["CLAUDE_PROJECT_DIR"] = str(root)
            os.environ.pop("OPENHANDS_PROJECT_DIR", None)
            sys.path.insert(0, str(tree / "hooks" / "engine"))
            from modules.audit import session_start
            text = session_start({"cwd": str(root)}).get("additionalContext", "")
            ok = _ACTION in text and not text.rstrip().endswith("epic …")
            return ok, f"sprint={text.split('sprint: ', 1)[-1][:110]!r}"

        if name == "lone_oversized_part_elided":
            giant = "9-1-" + "slug" * 60
            _write_sprint(root, [f"  {giant}: in-progress", "  1-1-a: done"])
            line = _line(tree, root)
            ok = (len(line) <= LIMIT and line.endswith("; … (+1 more entry)")
                  and "…" in line.split(";")[0])
            return ok, f"len={len(line)} tail={line[-40:]!r}"

        if name == "fits_unmarked":
            _write_sprint(root, ["  1-1-login: in-progress", "  1-2-out: done"])
            line = _line(tree, root)
            ok = "…" not in line and "more" not in line
            return ok, f"line={line!r}"

        if name == "status_only_board_intact":
            _write_sprint(root, [f"  1-{i}-story-{i}: in-progress"
                                 for i in range(1, 5)]
                          + ["  1-9-out: done"])
            line = _line(tree, root)
            ok = ("4 in-progress (" in line and "1-1-story-1" in line
                  and "1 done" in line and "⚠" not in line)
            return ok, f"line={line!r}"

        _wide(root)
        if name == "bound_respected_extreme":
            rows = [f"  1-{i}-story-{i}: in-progress" for i in range(1, 9)]
            rows += [f"  2-{i}-x: done" for i in range(1, 60)]
            for n in range(2, 8):
                rows += [f"  {n}-1-x: done", f"  epic-{n}: in-progress"]
            _write_sprint(root, rows)
            line = _line(tree, root)
            return len(line) <= LIMIT, f"len={len(line)}"

        line = _line(tree, root)
        if name == "warning_kept_whole":
            ok = (f"⚠ epic-2: stories all done — epic still in-progress, {_ACTION}" in line
                  and f"⚠ epic-3: stories all done — epic still in-progress, {_ACTION}" in line
                  and not line.endswith("epic …"))
            return ok, f"tail={line[-70:]!r}"
        if name == "warnings_lead":
            ok = line.startswith("sprint: ⚠ epic-2:")
            return ok, f"head={line[:50]!r}"
        if name == "remainder_counted":
            ok = "… (+3 more entries)" in line and line.count("in-progress (") == 0
            return ok, f"tail={line[-24:]!r}"
        if name == "bound_respected_wide":
            return len(line) <= LIMIT, f"len={len(line)}"
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
    print(f"sprint_line_accounting_accuracy={value:.2f} ({passed}/{total})")
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
    # The control is EXPECTED to miss the claim; a non-zero exit would read as a
    # crashed measurement instead of a measured refutation (rule 7).
    return 0 if (ok or args.broken) else 1


if __name__ == "__main__":
    raise SystemExit(main())

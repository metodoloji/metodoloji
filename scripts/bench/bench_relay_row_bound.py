#!/usr/bin/env python3
"""The doctor relay row prints the value the report bounded (E-207).

`doctor` bounds each methodology stage value at 80 characters through
`brief_text`, which lands on a boundary, keeps a gate token whole or drops it
whole, and marks the elision (E-187). The panel then re-cut that value at 50
with a raw slice. Observed live on 2026-11-22 on a scratch board:

    relay     E: E-201: APPROVED: 8/8 orient step bound (GATE-OK-E- | SP: SP-004: sprint ready, 7 stories, epic-3 in progres

A half gate token is not evidence — it cannot be verified and reads as a
corrupted token — and a broken word reads as a typo in the claim. The panel is
the surface operators paste into issues, so the defect travels.

Claim, closed set: the row carries each stage value exactly as the report
bounded it, so the token is whole, words are not broken, a short value stays
verbatim, a long value carries exactly the report's single `…`, and every
stage the report has is listed.

Control: `--broken` runs the same probes against `PREFIX_REV`, whose `v[:50]`
slice cuts the token, the word and the marker.
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
PREFIX_REV = "26b83f8"  # E-205 commit: the panel's `v[:50]` re-cut

TOKEN = "GATE-OK-E-201-f83137"
LONG = "E-900: APPROVED: " + "measured " * 12 + "(GATE-OK-E-900-abc)"


def _imports(tree: pathlib.Path):
    sys.path.insert(0, str(tree / "hooks" / "engine"))
    from modules import blackboard as bb
    return bb


def _panel(tree: pathlib.Path, root: pathlib.Path) -> str:
    cli = tree / "hooks" / "scripts" / "blackboard.py"
    result = subprocess.run([sys.executable, str(cli), "doctor",
                             "--project-root", str(root)],
                            capture_output=True, text=True, cwd=str(tree),
                            stdin=subprocess.DEVNULL)
    return result.stdout


def _board(tree: pathlib.Path, base: pathlib.Path, values: dict) -> pathlib.Path:
    bb = _imports(tree)
    root = base / "proj"
    root.mkdir(parents=True)
    for key, value in values.items():
        bb.write_key(str(root), f"methodology.last_{key}", value)
    return root


def _probe(name: str, tree: pathlib.Path) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        if name == "short_value_verbatim":
            root = _board(tree, base, {"experiment": "E-1: APPROVED (GATE-OK-E-1-ab)"})
        elif name == "all_stages_listed":
            root = _board(tree, base, {"experiment": "E-1: APPROVED (x)",
                                       "sp": "SP-2: sprint ready (y)"})
        elif name == "single_elision":
            root = _board(tree, base, {"experiment": LONG})
        else:
            root = _board(tree, base, {
                "experiment": f"E-201: APPROVED: 8/8 orient step bound ({TOKEN})",
                "sp": "SP-004: sprint ready, 7 stories, epic-3 in progress "
                      "(GATE-OK-SP-4-aa11)"})

        panel = _panel(tree, root)
        row = next((l for l in panel.splitlines() if l.startswith("relay ")), "")
        progress = _imports(tree).doctor(str(root))["checks"]["methodology"]["progress"]

        if name == "token_whole":
            return TOKEN in row, f"row={row[:110]!r}"
        if name == "word_boundary":
            ok = "in progress" in row and "progres |" not in row
            return ok, f"row={row[:110]!r}"
        if name == "report_value_verbatim":
            ok = all(f"{stage}: {value}" in row for stage, value in progress.items())
            return ok, f"stages={sorted(progress)}"
        if name == "short_value_verbatim":
            ok = "E: E-1: APPROVED (GATE-OK-E-1-ab)" in row and "…" not in row
            return ok, f"row={row[:110]!r}"
        if name == "all_stages_listed":
            ok = "E: " in row and "SP: " in row
            return ok, f"row={row[:110]!r}"
        if name == "single_elision":
            value = progress.get("E", "")
            ok = value.endswith("…") and value in row and row.count("…") == 1
            return ok, f"value={value[-14:]!r} markers={row.count('…')}"
    raise SystemExit(f"unknown probe: {name}")


PROBES = (
    "token_whole",
    "word_boundary",
    "report_value_verbatim",
    "short_value_verbatim",
    "all_stages_listed",
    "single_elision",
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
    print(f"relay_row_bound_accuracy={value:.2f} ({passed}/{total})")
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

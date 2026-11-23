#!/usr/bin/env python3
"""The orient ambiguous-code line names the codes it withholds (E-219).

The catalog line listed at most six ambiguous menu codes with no count, so a
catalog with twenty colliding codes read as six and the reader could not tell
how many more needed qualifying.

Claim, closed set: six or fewer ambiguous codes print whole with no marker;
seven prints `… (+1 more ambiguous code)`; eight prints `… (+2 more ambiguous
codes)`; twelve prints `… (+6 more ambiguous codes)`; and the unit is always
present.

Control: `--broken` runs the same probes against `PREFIX_REV`, where the list
is sliced at six with no marker at all.
"""

from __future__ import annotations

import argparse
import io
import json
import pathlib
import subprocess
import sys
import tarfile
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
PREFIX_REV = "f88ce5f"  # E-210 commit: the sliced, uncounted list

PROBES = (
    "three_codes_whole",
    "six_codes_whole",
    "seven_codes_singular",
    "eight_codes_plural",
    "twelve_codes_larger",
)


def _ambiguous(n: int) -> dict:
    return {f"C{i}": [f"m{i}/s{i}", f"m{i}/t{i}"] for i in range(1, n + 1)}


def _render(tree: pathlib.Path, n: int) -> str:
    script = (
        "import sys, io, contextlib\n"
        "sys.path[:0] = [sys.argv[1] + '/hooks/scripts']\n"
        "import orient\n"
        "orig = orient.catalog_digest\n"
        "def fake():\n"
        "    return {'path': 'x', 'available': True, 'modules': [],\n"
        "            'skill_count': 1, 'ambiguous_menu_codes': __import__('json').loads(sys.argv[2]),\n"
        "            'required': [], 'skills': []}\n"
        "orient.catalog_digest = fake\n"
        "buf = io.StringIO()\n"
        "with contextlib.redirect_stdout(buf):\n"
        "    orient._print_text(orient.build(__import__('pathlib').Path(sys.argv[3])))\n"
        "print(buf.getvalue())\n"
    )
    with tempfile.TemporaryDirectory() as raw:
        proj = pathlib.Path(raw) / "proj"
        proj.mkdir()
        (proj / "docs").mkdir()
        result = subprocess.run(
            [sys.executable, "-c", script, str(tree), json.dumps(_ambiguous(n)),
             str(proj)],
            capture_output=True, text=True, cwd=str(tree),
            stdin=subprocess.DEVNULL)
        return result.stdout + result.stderr


def _ambiguous_line(text: str) -> str:
    for line in text.splitlines():
        if "ambiguous codes" in line:
            return line
    # the marker may be on its own continuation line
    for line in text.splitlines():
        if "more ambiguous" in line:
            return line
    return ""


def _probe(name: str, tree: pathlib.Path) -> tuple[bool, str]:
    n = {"three_codes_whole": 3, "six_codes_whole": 6,
         "seven_codes_singular": 7, "eight_codes_plural": 8,
         "twelve_codes_larger": 12}[name]
    text = _render(tree, n)
    line = _ambiguous_line(text)
    if name in ("three_codes_whole", "six_codes_whole"):
        ok = ("ambiguous codes :" in text and "more ambiguous" not in text)
    elif name == "seven_codes_singular":
        ok = "… (+1 more ambiguous code)" in text
    elif name == "eight_codes_plural":
        ok = "… (+2 more ambiguous codes)" in text
    else:
        ok = "… (+6 more ambiguous codes)" in text
    detail = (line or text).strip()
    if name not in ("three_codes_whole", "six_codes_whole"):
        # The marker sits on its own continuation line; the sample must show
        # the count it claims, not the truncated list above it.
        marker = [ln.strip() for ln in text.splitlines() if "more ambiguous" in ln]
        if marker:
            detail = marker[0]
    return ok, detail[-80:]


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
    print(f"orient_ambiguous_count_accuracy={value:.2f} ({passed}/{total})")
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

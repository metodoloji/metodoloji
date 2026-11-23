#!/usr/bin/env python3
"""The docs overview counts the areas and files it withheld (E-202).

The orientation digest's docs section caps two lists: the walk stops at
``_MAX_DOCS_SUBDIRS`` (12) and the text render prints ``_MAX_LISTED`` (6) of
the largest files. Neither cap said anything. Live on 2026-11-22, a tree with
fifteen area directories rendered twelve of them and a tree with twelve large
files printed six, so both read as the complete inventory — the reader has no
reason to look further, and the digest exists precisely to save the manual
``ls``/``wc`` sweep it was hiding.

The artifact line in the same render already counts its remainder
(``… +N``); these two lists now do the same, and the area remainder names
its unit (``… (+N more areas)``).

Claim, closed set: the digest reports the area total and the render counts the
areas and files the caps withheld, with counts that match the inventory; a tree
that fits carries no remainder marker; hidden directories are not counted;
the size ordering is unchanged; and an empty docs tree still says ``(empty)``.

Control: `--broken` runs the same probes against `PREFIX_REV`, whose render
prints both lists silently.
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
PREFIX_REV = "bad08ce"  # HEAD before E-202: no subdirs_total, largest[:6] silent


def _tree(base: pathlib.Path, areas: int, big: int = 0,
          hidden: int = 0) -> pathlib.Path:
    root = base / "proj"
    docs = root / "docs"
    for i in range(areas):
        area = docs / f"area-{i:02d}"
        area.mkdir(parents=True)
        if i < big:
            (area / f"note-{i}.md").write_text("x" * (1000 * (i + 1)),
                                              encoding="utf-8")
    for i in range(hidden):
        (docs / f".hidden-{i}").mkdir(parents=True)
    return root


def _digest(tree: pathlib.Path, root: pathlib.Path) -> dict:
    sys.path.insert(0, str(tree / "hooks" / "scripts"))
    sys.path.insert(0, str(tree / "hooks" / "engine"))
    import orient
    return orient.build(root)["docs"]


def _area_remainder(hidden: int) -> str:
    """The docs line's withheld-area marker (E-218 named the unit)."""
    return f"… (+{hidden} more area{'' if hidden == 1 else 's'})"


def _render(tree: pathlib.Path, root: pathlib.Path) -> str:
    result = subprocess.run(
        [sys.executable, str(tree / "hooks" / "scripts" / "orient.py"),
         "--project-root", str(root)], capture_output=True, text=True,
        cwd=str(tree), stdin=subprocess.DEVNULL)
    return result.stdout


def _probe(name: str, tree: pathlib.Path) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        if name == "fit_unmarked":
            root = _tree(base, areas=2, big=2)
            out = _render(tree, root)
            docs_line = [l for l in out.splitlines() if "  docs " in l]
            ok = bool(docs_line) and "… +" not in docs_line[0] and "more by size" not in out
            return ok, f"line={docs_line[0][:80] if docs_line else None!r}"
        if name == "hidden_dirs_not_counted":
            root = _tree(base, areas=2, big=1, hidden=3)
            d = _digest(tree, root)
            ok = d.get("subdirs_total") == 2
            return ok, f"total={d.get('subdirs_total')} listed={len(d.get('subdirs') or [])}"
        if name == "size_order_kept":
            root = _tree(base, areas=4, big=4)
            d = _digest(tree, root)
            sizes = [f["bytes"] for f in d.get("largest") or []]
            ok = sizes == sorted(sizes, reverse=True) and len(sizes) == 4
            return ok, f"sizes={sizes}"
        if name == "empty_docs_marked":
            root = base / "empty"
            (root / "docs").mkdir(parents=True)
            out = _render(tree, root)
            line = [l for l in out.splitlines() if "  docs " in l]
            ok = bool(line) and "(empty)" in line[0]
            return ok, f"line={line[0][-20:] if line else None!r}"

        root = _tree(base, areas=15, big=12)
        d = _digest(tree, root)
        out = _render(tree, root)
        docs_line = next((l for l in out.splitlines() if "  docs " in l), "")
        if name == "subdirs_remainder_counted":
            hidden = 15 - len(d.get("subdirs") or [])
            ok = _area_remainder(hidden) in docs_line
            return ok, f"tail={docs_line[-40:]!r}"
        if name == "largest_remainder_counted":
            ok = "more by size" in out and "… +" in out
            return ok, f"tail={[l for l in out.splitlines() if 'more by size' in l]}"
        if name == "counts_match_inventory":
            largest = d.get("largest") or []
            subdirs = d.get("subdirs") or []
            ok = (d.get("subdirs_total") == 15
                  and _area_remainder(15 - len(subdirs)) in docs_line
                  and f"… +{len(largest) - 6} more by size" in out)
            return ok, f"total={d.get('subdirs_total')} largest={len(largest)}"
    raise SystemExit(f"unknown probe: {name}")


PROBES = (
    "subdirs_remainder_counted",
    "largest_remainder_counted",
    "counts_match_inventory",
    "fit_unmarked",
    "hidden_dirs_not_counted",
    "size_order_kept",
    "empty_docs_marked",
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
    print(f"docs_overview_bound_accuracy={value:.2f} ({passed}/{total})")
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

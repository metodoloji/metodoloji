#!/usr/bin/env python3
"""The install digest finds every shadow copy and counts what it names (E-203).

Two silent caps sat on the duplicate-install hunt. The scan walked each cache
base with `[:5]`, so a home with eight cache copies reported five — the other
three were never discovered, and nothing said the scan had stopped. Both
surfaces that print the shadows then joined `[:3]` of that list, so five looked
like three.

The cost is the one this section exists to remove: an agent told there are
three shadow installs has no reason to suspect a fourth, and the digest's own
docstring records a session that burned turns catting SKILL.md from each copy.

Claim, closed set: the scan lists every cache copy, the digest carries the
shadow count, both surfaces name three and count the remainder, a single copy
carries no marker, a copy that resolves to the canonical root is not a shadow,
and the count matches the list on every probe.

Control: `--broken` runs the same probes against `PREFIX_REV`, whose scan stops
at five per base and whose two surfaces join three silently.
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
PREFIX_REV = "f7cf51d"  # HEAD before E-203: cache_base[:5], shadows[:3] silent

_REL = pathlib.Path(".claude/plugins/cache/yunusgungor/metodoloji")


def _home(base: pathlib.Path, copies: int) -> pathlib.Path:
    home = base / "home"
    cache = home / _REL
    for i in range(copies):
        (cache / f"0.3.{i}").mkdir(parents=True)
    return home


def _orient(tree: pathlib.Path):
    sys.path.insert(0, str(tree / "hooks" / "scripts"))
    sys.path.insert(0, str(tree / "hooks" / "engine"))
    import orient
    return orient


def _digest(tree: pathlib.Path, home: pathlib.Path) -> dict:
    return _orient(tree).install_digest()


def _steps(tree: pathlib.Path, home: pathlib.Path) -> list[str]:
    root = pathlib.Path(tempfile.mkdtemp(prefix="orient-root-"))
    return _orient(tree).build(root)["next_steps"]


def _render(tree: pathlib.Path, home: pathlib.Path) -> str:
    root = pathlib.Path(tempfile.mkdtemp(prefix="orient-render-"))
    result = subprocess.run(
        [sys.executable, str(tree / "hooks" / "scripts" / "orient.py"),
         "--project-root", str(root)], capture_output=True, text=True,
        cwd=str(tree), stdin=subprocess.DEVNULL)
    return result.stdout


def _probe(name: str, tree: pathlib.Path) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)

        if name == "canonical_copy_not_a_shadow":
            home = base / "home"
            cache = home / _REL
            cache.mkdir(parents=True)
            orient = _orient(tree)
            os.environ["HOME"] = str(home)
            (cache / "self").symlink_to(orient._PLUGIN_ROOT)
            d = orient.install_digest()
            ok = d["duplicate"] is False and d.get("shadow_count") == 0
            return ok, f"duplicate={d['duplicate']} count={d.get('shadow_count')}"

        copies = 1 if name == "single_copy_unmarked" else 8
        home = _home(base, copies)
        os.environ["HOME"] = str(home)  # Path.home() is read per call
        d = _digest(tree, home)
        steps = _steps(tree, home)
        step = next((s for s in steps if "duplicate plugin installs" in s), "")
        out = _render(tree, home)

        if name == "scan_finds_every_copy":
            ok = len(d["found"]) == copies and len(d["shadows"]) == copies
            return ok, f"found={len(d['found'])} shadows={len(d['shadows'])}"
        if name == "count_matches":
            ok = (d.get("shadow_count") == len(d["shadows"]) == copies
                  and f"(+{copies - 3} more shadow installs)" in step
                  and f"(+{copies - 3} more shadow installs)" in out)
            return ok, f"count={d.get('shadow_count')}"
        if name == "step_counts_remainder":
            ok = f"… (+{copies - 3} more shadow installs)" in step
            return ok, f"tail={step[-70:]!r}"
        if name == "render_counts_remainder":
            ok = f"… (+{copies - 3} more shadow installs)" in out
            return ok, f"tail={[l[-60:] for l in out.splitlines() if 'shadows:' in l]}"
        if name == "step_names_three":
            named = [f"0.3.{i}" in step for i in range(copies)]
            ok = named[:3] == [True, True, True] and not any(named[3:])
            return ok, f"named={sum(named)}"
        if name == "single_copy_unmarked":
            ok = "…" not in step and "(+" not in step and "0.3.0" in step
            return ok, f"step_len={len(step)}"
    raise SystemExit(f"unknown probe: {name}")


PROBES = (
    "scan_finds_every_copy",
    "count_matches",
    "step_counts_remainder",
    "render_counts_remainder",
    "step_names_three",
    "single_copy_unmarked",
    "canonical_copy_not_a_shadow",
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
    home = pathlib.Path(tempfile.mkdtemp(prefix="bench-home-")) / "home"
    home.mkdir()
    for name in PROBES:
        env = {**os.environ, "HOME": str(home)}
        result = subprocess.run(
            [sys.executable, str(pathlib.Path(__file__).resolve()), "--driver", name,
             "--tree", str(tree)], capture_output=True, text=True, cwd=str(tree),
            env=env, stdin=subprocess.DEVNULL)
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
    print(f"shadow_install_accuracy={value:.2f} ({passed}/{total})")
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

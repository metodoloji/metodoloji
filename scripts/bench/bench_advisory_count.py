#!/usr/bin/env python3
"""The workflow advisory names its withheld runs and stale stages (E-215).

Two bounded lists on the session-start inject:

* `peek` lists unfinished runs and tails the rest as `(+N more run(s))` — the
  parenthesised plural is the only place in the tree that still uses it.
* `_describe` lists the stages whose evidence went stale as
  `completed 'a', 'b' (+N more)` — a bare count with no unit, so a run with
  five stale stages read as three.

Claim, closed set: `peek` names its unit as `run`/`runs`, and the foundation
list names its unit as `stage`/`stages` with the true count.

Control: `--broken` runs the same probes against `PREFIX_REV`, where the tail
is `run(s)` and the foundation count is bare.
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
PREFIX_REV = "f88ce5f"  # E-210 commit: run(s) and the bare (+N more)

PROBES = (
    "peek_singular",
    "peek_plural",
    "peek_whole",
    "foundation_counted",
    "foundation_singular",
    "foundation_whole",
)


def _driver_probe(name: str, tree: pathlib.Path) -> tuple[bool, str]:
    sys.path.insert(0, str(tree))
    import json as _json
    import tempfile as _tmp
    from hooks.workflow import advisory, evidence as evidence_mod

    if name.startswith("peek_"):
        with _tmp.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            base = root / ".metodoloji" / "workflow"
            base.mkdir(parents=True)
            counts = {"peek_singular": 3, "peek_plural": 4, "peek_whole": 2}[name]
            for i in range(counts):
                (base / f"run{i}.state.json").write_text(
                    _json.dumps({"status": "running"}), encoding="utf-8")
            line = advisory.peek(str(root), limit=2)
            if name == "peek_singular":
                ok = "(+1 more run)" in line and "run(s)" not in line
            elif name == "peek_plural":
                ok = "(+2 more runs)" in line and "run(s)" not in line
            else:
                ok = "more" not in line
            return ok, line[-70:]
    # foundation
    count = {"foundation_counted": 5, "foundation_singular": 3,
             "foundation_whole": 1}[name]
    stages = [f"s{i}" for i in range(count)]
    evidence_mod.verify = lambda pr, st, sp: {"stale": stages}
    line = advisory._describe(
        "run", {"current": "s1", "status": "running", "completed": []}, {}, "/tmp")
    if name == "foundation_counted":
        ok = f"(+{count - 2} more stages)" in line
    elif name == "foundation_singular":
        ok = "(+1 more stage)" in line
    else:
        ok = "more" not in line and "FOUNDATION MOVED" in line
    return ok, line[-80:]


def driver(name: str, tree: pathlib.Path) -> None:
    ok, detail = _driver_probe(name, tree)
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
    print(f"advisory_count_accuracy={value:.2f} ({passed}/{total})")
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

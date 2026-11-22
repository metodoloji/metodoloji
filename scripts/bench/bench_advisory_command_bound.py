#!/usr/bin/env python3
"""The session-edge advisory names the whole evidence command (E-208).

`_evidence_hint` printed `argv[:3]`. For the builtin methodology-chain spec that
is `{python} {metodoloji-root}/hooks/scripts/chain-gate.py --stage` — a dangling
flag with no value, for all six non-terminal stages, identical between them.
Observed live on 2026-11-22:

    experiment -> command `{python} .../chain-gate.py --stage` (rc=0)
    implementation-readiness -> command `{python} .../chain-gate.py --stage` (rc=0)

The advisory exists to tell a resumed session what the current stage completes
with, and a command elided in the middle is a different command: the agent runs
the gate without its stage, slug or project root, or cannot tell two stages
apart by their evidence at all.

Claim, closed set: every builtin stage names its full argv, its stage value, and
its slug/project-root arguments; two stages no longer render the same command;
a short argv still prints verbatim; and no hint ends on a bare flag.

Control: `--broken` runs the same probes against `PREFIX_REV`, whose `argv[:3]`
cut leaves every stage with the same dangling `--stage`.
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
PREFIX_REV = "26b83f8"  # E-205 commit: advisory's `argv[:3]`


def _modules(tree: pathlib.Path):
    sys.path.insert(0, str(tree / "hooks"))
    from workflow import advisory, spec as spec_mod
    return advisory, spec_mod


def _hint(tree: pathlib.Path, stage_id: str) -> str:
    advisory, spec_mod = _modules(tree)
    spec = spec_mod.load(str(tree / "hooks" / "workflow" / "builtin"
                              / "methodology-chain.json"))
    stage = next(s for s in spec["stages"] if s["id"] == stage_id)
    return advisory._evidence_hint(stage)


def _probe(name: str, tree: pathlib.Path) -> tuple[bool, str]:
    advisory, spec_mod = _modules(tree)
    spec = spec_mod.load(str(tree / "hooks" / "workflow" / "builtin"
                              / "methodology-chain.json"))
    stages = [s for s in spec["stages"] if not s.get("terminal")]

    if name == "builtin_full_argv":
        missing = []
        for stage in stages:
            argv = " ".join(str(a) for a in stage["evidence"]["argv"])
            if argv not in advisory._evidence_hint(stage):
                missing.append(stage["id"])
        return not missing, f"incomplete={missing}"

    if name == "builtin_stage_value":
        missing = [s["id"] for s in stages
                   if f"--stage {s['id']}" not in advisory._evidence_hint(s)]
        return not missing, f"unnamed={missing}"

    if name == "builtin_slug_and_root":
        missing = [s["id"] for s in stages
                   if "--slug" not in advisory._evidence_hint(s)
                   or "--project-root" not in advisory._evidence_hint(s)]
        return not missing, f"missing={missing}"

    if name == "stages_distinguishable":
        first, second = stages[0]["id"], stages[-1]["id"]
        one, two = _hint(tree, first), _hint(tree, second)
        return one != two and first in one and second in two, f"{first}!={second}"

    if name == "no_dangling_flag":
        bad = [s["id"] for s in stages if advisory._evidence_hint(s).endswith("--stage` (rc=0)")]
        return not bad, f"dangling={bad}"

    if name == "short_argv_verbatim":
        hint = advisory._evidence_hint(
            {"evidence": {"kind": "command", "argv": ["pytest", "-q"], "rc": 0}})
        return hint == "command `pytest -q` (rc=0)", f"hint={hint!r}"

    raise SystemExit(f"unknown probe: {name}")


PROBES = (
    "builtin_full_argv",
    "builtin_stage_value",
    "builtin_slug_and_root",
    "stages_distinguishable",
    "no_dangling_flag",
    "short_argv_verbatim",
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
    print(f"advisory_command_bound_accuracy={value:.2f} ({passed}/{total})")
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

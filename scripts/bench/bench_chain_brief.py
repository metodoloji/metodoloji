#!/usr/bin/env python3
"""A bound that never cuts a gate token in half (E-187).

Ground truth measured live on this tree, 2026-11-20 (session_start):

    Chain progress — E: E-186: APPROVED: measured=1.0 == threshold=1.0 (GATE-OK-E-18

A run key's value is `E-186: APPROVED: … (GATE-OK-E-186-<hash>)`. Three shipped
surfaces bound that value — `compact_context` at 80 chars, the doctor's
methodology block at 80, the session edge again at 60 — and every one of them
sliced it, so the most durable surface a session sees handed out an
unverifiable stub that reads as a corrupted gate token.

Claim, closed set: every bound elides at a boundary, keeps the decision and
claim, drops an unclosed bracket whole, and never emits a partial token. The
probes drive the real functions and the real hook entry point.

Control: `--broken` runs the same probes against the pre-change tree
(`PREFIX_REV`), where the slice is still a raw `[:80]` / `[:60]`.
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
PREFIX_REV = "c302171"  # the parent of the commit that ships brief_text
BROKEN = False

TOKEN = "GATE-OK-E-186-a365d69b823ca8478568acfa31b3e8f9"
VALUE = ("E-186: APPROVED: measured=1.0 == threshold=1.0 "
         f"({TOKEN}) — code landed")
PROSE = "APPROVED — engine changed on the shared scope " * 4

PROBES = (
    "short_value_verbatim",
    "boundary_not_mid_word",
    "token_never_fragmented",
    "claim_survives_elision",
    "unclosed_bracket_dropped",
    "marker_once",
    "doctor_elides",
    "session_edge_elides",
    "session_stage_bounded",
    "budget_holds",
)


# ---------------------------------------------------------------- helpers

def _imports(tree: pathlib.Path) -> None:
    sys.path.insert(0, str(tree / "hooks" / "engine"))
    sys.path.insert(0, str(tree))


def _board(tree: pathlib.Path, base: pathlib.Path,
           value: str = VALUE) -> tuple[pathlib.Path, object]:
    from modules import blackboard as bb
    root = base / "project"
    root.mkdir()
    bb.write_key(str(root), "E-186", value)
    return root, bb


def _compact(root, bb) -> str:
    return bb.compact_context(str(root))["methodology"].get("E", "")


def _doctor(root) -> str:
    from modules import blackboard_diagnostics as diag
    return (((((diag.doctor(str(root)).get("checks") or {})
              .get("methodology") or {}).get("progress")) or {}).get("E") or "")


def _stage(ctx: str) -> str:
    line = ctx.split("Chain progress", 1)[1].split(". ", 1)[0]
    return line.split("E: ", 1)[1].split(" [", 1)[0] if "E: " in line else line


def _safe(value: str) -> tuple[bool, str]:
    """The invariant every surface owes: no fragment, balanced brackets."""
    fragmented = "GATE-OK-E-18" in value and TOKEN not in value
    balanced = value.count("(") == value.count(")")
    ok = (not fragmented) and balanced and "APPROVED" in value
    return ok, f"value={value!r}"


# ---------------------------------------------------------------- probes

def _probe(name: str, tree: pathlib.Path, base: pathlib.Path) -> tuple[bool, str]:
    _imports(tree)

    if name == "short_value_verbatim":
        root, bb = _board(tree, base, "APPROVED: m=1.0")
        out = _compact(root, bb)
        return out == "E-186: APPROVED: m=1.0", f"out={out!r}"

    if name == "boundary_not_mid_word":
        root, bb = _board(tree, base, PROSE)
        out = _compact(root, bb)
        ok = out.endswith("…") and out[:-1][-1].isalnum() \
            and out[:-1] == out[:-1].rstrip() and len(out) <= 81
        return ok, f"out={out!r}"

    if name == "token_never_fragmented":
        root, bb = _board(tree, base)
        bad = [surface for surface, value in (
            ("compact", _compact(root, bb)), ("doctor", _doctor(root)))
            if ("GATE-OK-E-18" in value and TOKEN not in value)
            or value.count("(") != value.count(")")]
        return not bad, f"bad={bad}"

    if name == "claim_survives_elision":
        root, bb = _board(tree, base)
        out = _compact(root, bb)
        # The token is elided WHOLE: not even its prefix may survive, or the
        # reader is back to an unverifiable stub.
        ok = ("APPROVED" in out and "measured=1.0" in out
              and "GATE-OK-E-186" not in out)
        return ok, f"out={out!r}"

    if name == "unclosed_bracket_dropped":
        root, bb = _board(tree, base, "APPROVED(" + "x" * 80)
        out = _compact(root, bb)
        ok = out.count("(") == out.count(")") and out.endswith("…")
        return ok, f"out={out!r}"

    if name == "marker_once":
        root, bb = _board(tree, base, PROSE)
        out = _compact(root, bb)
        return out.count("…") == 1, f"out={out!r}"

    if name == "doctor_elides":
        root, _bb = _board(tree, base)
        value = _doctor(root)
        ok, detail = _safe(value)
        return ok and value.endswith("…"), detail

    if name == "session_edge_elides":
        root, bb = _board(tree, base)
        from modules.audit import session_start
        ctx = session_start({"cwd": str(root)})["additionalContext"]
        line = ctx.split("Chain progress", 1)[1].split(". ", 1)[0]
        ok, detail = _safe(line)
        ok = ok and TOKEN not in line and "…" in line and not line.endswith("….")
        return ok, detail

    if name == "session_stage_bounded":
        root, bb = _board(tree, base, PROSE)
        from modules.audit import session_start
        ctx = session_start({"cwd": str(root)})["additionalContext"]
        stage = _stage(ctx)
        ok = len(stage) <= 61 and stage.endswith("…")
        return ok, f"stage={stage!r}"

    if name == "budget_holds":
        # The 80/60 bounds stay — only where they land changed.
        root, bb = _board(tree, base)
        from modules.audit import session_start
        stage = _stage(session_start({"cwd": str(root)})["additionalContext"])
        ok = (len(_compact(root, bb)) <= 81 and len(_doctor(root)) <= 81
              and len(stage) <= 61)
        return ok, f"compact={len(_compact(root, bb))} doctor={len(_doctor(root))} stage={len(stage)}"

    raise SystemExit(f"unknown probe: {name}")


# ---------------------------------------------------------------- driver mode

def driver(name: str, tree: pathlib.Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ok, detail = _probe(name, tree, pathlib.Path(tmp))
    print(json.dumps({"ok": ok, "detail": detail}, ensure_ascii=False))


# ---------------------------------------------------------------- bench mode

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
    print(f"chain_brief_accuracy={value:.2f} ({passed}/{total})")
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

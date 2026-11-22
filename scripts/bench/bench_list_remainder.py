#!/usr/bin/env python3
"""A bounded list names the items it hid, on every surface (E-196).

Found live on this tree, 2026-11-22. Four anchorless hand-offs posted to each of
two methodology stages — eight flagged signals, one stop report::

    BUT: E-001 (UNKNOWN, for bmad-sprint-planning), E-002 (...), E-003 (...),
    E-004 (...), E-001 (UNKNOWN, for bmad-create-story) may no longer apply

Five names, no marker, three invisible — and the outer bound of five was reached
only because a per-skill `signals[:3]` had already dropped two. The session-start
inject had the mirror defect in the same sentence family: `NOTE: 4 of them carry
no code-state anchor (UNKNOWN: E-001, E-002, E-003)` — a count that contradicts
the list beside it.

E-195 gave the story denials a counted join; this generalises it to one helper
(`utils.counted_list`) behind both, so the denomination, the singular/plural and
the separator are decided once. Claim, closed set: every bounded list built by
this repo's stop report, inject and story denials names its remainder; the
visible bounds are unchanged; a list that fits prints verbatim; and no raw
`[:N]` slice of a claim list survives on those paths.

Control: `--broken` runs the same probes against the pre-change tree
(`PREFIX_REV`), which has neither `counted_list` nor the counted joins.
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
PREFIX_REV = "0953f6b"  # the parent of the commit that ships the counted list
LIMIT = 5

PROBES = (
    "remainder_counted",
    "visible_bound_kept",
    "singular_and_plural",
    "at_limit_unmarked",
    "short_list_verbatim",
    "separator_preserved",
    "issue_summary_unchanged",
    "stop_report_counts_live",
    "inject_counts_live",
    "no_raw_slice_left",
)

STAGES = ("bmad-sprint-planning", "bmad-create-story")


def _engine(tree: pathlib.Path):
    sys.path.insert(0, str(tree / "hooks" / "engine"))
    from modules import blackboard as bb
    from modules.utils import counted_list, issue_summary
    return bb, counted_list, issue_summary


def _counted_list(tree: pathlib.Path):
    sys.path.insert(0, str(tree / "hooks" / "engine"))
    try:
        from modules.utils import counted_list
    except ImportError:
        return None
    return counted_list


def _board(root: pathlib.Path, per_stage: int = 4) -> None:
    """Seed `per_stage` anchorless hand-offs for each methodology stage.

    Posted through the CLI in its own process, so seeding cannot shadow the
    tree under test in `sys.modules`.
    """
    root.mkdir(parents=True, exist_ok=True)
    cli = ROOT / "hooks" / "scripts" / "blackboard.py"
    for stage in STAGES:
        for n in range(1, per_stage + 1):
            subprocess.run(
                [sys.executable, str(cli), "handoff", "--to", stage,
                 "--from-key", f"E-00{n}", "--note", f"hand off {n}"],
                capture_output=True, text=True, cwd=str(root),
                stdin=subprocess.DEVNULL, check=True)


def _probe(name: str, tree: pathlib.Path) -> tuple[bool, str]:
    if name == "stop_report_counts_live" or name == "inject_counts_live":
        return _live(name, tree)

    if name == "no_raw_slice_left":
        # comment lines carry the old slices as documentation; only code counts
        code = "\n".join(
            line for path in ("stop.py", "audit.py")
            for line in (tree / "hooks" / "engine" / "modules" / path
                         ).read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#"))
        ok = ("signals[:3]" not in code and "labels[:5]" not in code
              and "unknown[:3]" not in code
              and "(stale + unknown)[:5]" not in code)
        return ok, "no raw claim-list slice remains"

    counted_list = _counted_list(tree)
    if counted_list is None:
        return False, "utils.counted_list absent in this tree"

    if name == "remainder_counted":
        out = counted_list([f"E-00{n}" for n in range(1, 9)], LIMIT, noun="signal")
        ok = out.endswith("… (+3 more signals)") and out.count("E-00") == LIMIT
        return ok, f"tail={out[-30:]!r}"

    if name == "visible_bound_kept":
        out = counted_list([f"E-00{n}" for n in range(1, 9)], LIMIT, noun="signal")
        named = [n for n in range(1, 9) if f"E-00{n}" in out]
        ok = len(named) == LIMIT
        return ok, f"named={len(named)}"

    if name == "singular_and_plural":
        one = counted_list([f"E-00{n}" for n in range(1, 7)], LIMIT, noun="signal")
        many = counted_list([f"E-00{n}" for n in range(1, 12)], LIMIT, noun="signal")
        ok = one.endswith("(+1 more signal)") and many.endswith("(+6 more signals)")
        return ok, f"one={one[-20:]!r} many={many[-22:]!r}"

    if name == "at_limit_unmarked":
        out = counted_list([f"E-00{n}" for n in range(1, LIMIT + 1)], LIMIT)
        ok = "more " not in out and "…" not in out
        return ok, f"out={out!r}"

    if name == "short_list_verbatim":
        out = counted_list(["E-001"], LIMIT, noun="signal")
        ok = out == "E-001"
        return ok, f"out={out!r}"

    if name == "separator_preserved":
        out = counted_list(["k1", "k2", "k3", "k4"], 3, noun="key", sep=" ")
        ok = out == "k1 k2 k3 … (+1 more key)"
        return ok, f"out={out!r}"

    if name == "issue_summary_unchanged":
        # E-195's shipped strings must survive the delegation, byte for byte
        _, _, issue_summary = _engine(tree)
        out = issue_summary([f"i{n}" for n in range(4)], 3)
        ok = out == "i0; i1; i2; … (+1 more issue)"
        return ok, f"out={out!r}"

    raise SystemExit(f"unknown probe: {name}")


def _live(name: str, tree: pathlib.Path) -> tuple[bool, str]:
    """Drive the shipped engine against a real board with eight flagged signals."""
    sys.path.insert(0, str(tree / "hooks" / "engine"))
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "proj"
        _board(root)
        os.environ["CLAUDE_PROJECT_DIR"] = str(root)
        os.environ.pop("OPENHANDS_PROJECT_DIR", None)
        if name == "stop_report_counts_live":
            from modules.stop import stop
            text = stop({}).get("reason", "")
        else:
            from modules.audit import session_start
            text = session_start({"cwd": str(root)}).get("additionalContext", "")
    if name == "stop_report_counts_live":
        ok = ("8 unclaimed signal(s)" in text
              and "… (+3 more signals)" in text
              and text.split("BUT: ", 1)[-1].count("E-00") == LIMIT)
        return ok, f"but={text.split('BUT: ', 1)[-1][:120]!r}"
    ok = ("4 of them carry no code-state anchor" in text
          and "UNKNOWN: E-001, E-002, E-003, … (+1 more key)" in text)
    return ok, f"note={text.split('NOTE: ', 1)[-1][:90]!r}"


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
    print(f"list_remainder_accuracy={value:.2f} ({passed}/{total})")
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

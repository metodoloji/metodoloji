#!/usr/bin/env python3
"""Routed alerts on the stop channel are bounded, not silently chopped (E-192).

Found live on this tree, 2026-11-21, by posting an alert and letting the Stop
hook report it::

    routed alert(s) on the stop channel: … notes/segment/notes/segment/no — clear with …

The alert text was sliced at exactly 160 characters with no marker at all. It
is the sibling failure of E-187/E-191 on the surface that ends a session: the
reader cannot tell the text was shortened, a path stops mid-name (so it cannot
be opened), and a gate token near the boundary would arrive as a fragment that
looks whole. The engine already owns the right cutter (`utils.brief_text`: cut
on a boundary, drop an unclosed bracket group whole, mark the elision).

Claim, closed set: the stop note's alert preview is produced by the shared
cutter — marked elision, boundary cut, token whole or absent, short text
verbatim, the "(+N more)" tail intact, and Stop still report-only.

Control: `--broken` runs the same probes against the pre-change tree
(`PREFIX_REV`), whose preview is `text[:160]`.
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
PREFIX_REV = "0953f6b"  # the parent of the commit that ships the shared cutter here
TOKEN = "GATE-OK-E-191-ffd473aac263a0ce20b3aee288b5a2e2"
BOUNDARY = " \t\n—–-:;,/|("

PROBES = (
    "token_whole_or_absent",
    "elision_marked_not_silent",
    "cut_lands_on_a_boundary",
    "short_alert_verbatim",
    "preview_within_bound",
    "more_count_preserved",
    "unclosed_bracket_dropped_whole",
    "path_dropped_whole",
    "report_only_still_allows",
    "shared_cutter_equality",
)


# ---------------------------------------------------------------- fixtures
def _engine(tree: pathlib.Path):
    sys.path.insert(0, str(tree / "hooks" / "engine"))
    from modules import blackboard as bb
    from modules.stop import stop
    return bb, stop


def _root(base: pathlib.Path) -> pathlib.Path:
    root = base / "project"
    root.mkdir(parents=True)
    return root


def _preview(reason: str) -> str:
    if "stop channel: " not in reason:
        return ""
    tail = reason.split("stop channel: ", 1)[1]
    tail = tail.split(" — clear with", 1)[0]
    # The withheld-alert tail is a marker, not part of the previewed text: drop
    # it in both its unitless (E-192) and unit-named (E-222) shapes.
    return re.sub(r" \(\+\d+ more(?: alerts?)?\)$", "", tail)


def _long_alert(filler: str = "notes/segment/") -> str:
    # The token starts past 160 so the old raw slice cut through the path and
    # dropped the token entirely without a marker.
    return ("release run finished and the report is ready: "
            + filler * 9 + f"report.md ({TOKEN}) — verify before clearing")


def _probe(name: str, tree: pathlib.Path, base: pathlib.Path) -> tuple[bool, str]:
    bb, stop = _engine(tree)
    root = _root(base)
    os.environ["CLAUDE_PROJECT_DIR"] = str(root)

    def run(text: str, count: int = 1) -> str:
        for index in range(count):
            bb.post_alert(str(root), "stop", "info", text if index == 0 else f"{text} #{index}")
        return stop({}).get("reason", "")

    if name == "token_whole_or_absent":
        preview = _preview(run(_long_alert()))
        return TOKEN[:20] not in preview, f"preview={preview[-40:]!r}"

    if name == "elision_marked_not_silent":
        text = _long_alert()
        preview = _preview(run(text))
        ok = preview.endswith("…") and preview != text[:160]
        return ok, f"preview={preview[-40:]!r}"

    if name == "cut_lands_on_a_boundary":
        text = _long_alert()
        body = _preview(run(text))[:-1]
        ok = (text.startswith(body) and len(body) < len(text)
              and text[len(body)] in BOUNDARY)
        return ok, f"body={body[-30:]!r}"

    if name == "short_alert_verbatim":
        preview = _preview(run("deploy finished: ok"))
        return preview == "deploy finished: ok", f"preview={preview!r}"

    if name == "preview_within_bound":
        preview = _preview(run(_long_alert("word ")))
        return len(preview) <= 161, f"len={len(preview)}"

    if name == "more_count_preserved":
        reason = run("deploy finished: ok", count=2)
        return "(+1 more alert)" in reason, f"reason={reason[-60:]!r}"

    if name == "unclosed_bracket_dropped_whole":
        text = "release run finished " + "w " * 70 + f"({TOKEN}) — verify"
        preview = _preview(run(text))
        ok = "(GATE-OK" not in preview and "(" not in preview[-2:]
        return ok, f"preview={preview[-40:]!r}"

    if name == "path_dropped_whole":
        preview = _preview(run(_long_alert()))
        ok = "notes/segment/no" not in preview and "notes/segment/" not in preview
        return ok, f"preview={preview[-40:]!r}"

    if name == "report_only_still_allows":
        run(_long_alert())
        return stop({}).get("decision") == "allow", "decision=allow"

    if name == "shared_cutter_equality":
        from modules.utils import brief_text
        text = _long_alert()
        ok = _preview(run(text)) == brief_text(text, 160)
        return ok, "preview == brief_text(text, 160)"

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
    print(f"stop_alert_bound_accuracy={value:.2f} ({passed}/{total})")
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

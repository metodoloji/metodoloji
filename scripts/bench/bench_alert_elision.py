#!/usr/bin/env python3
"""A shortened alert on the session edge says it was shortened (E-199).

Found live on this tree, 2026-11-22: a missed-scope commit posted a scope-debt
alert whose text names the files outside any approved scope, and the very next
session was told

    SCOPE DEBT — commit missed 3 files outside any approved scope:
    docs/development/native/segments/07-runtime/segment-note-runtime-tail.md,
    hooks/engine/modules/blackboard_diag.

The session edge sliced the alert at 160 characters — mid-path, and with no
marker at all, so the sentence read as complete while ending in a file that
does not exist. E-192 fixed exactly this slice on the stop edge; the session
edge, the surface every session actually opens with, kept the raw `[:160]`
(and so did the routed-alert branch beside it).

Claim, closed set: on the session edge both alert branches bound their text
through the shared cutter, so a shortened alert is a prefix of the original,
cut on a token boundary (never mid-path), marked with `…`; a short alert is
quoted verbatim; the withheld alerts are still counted; an unclosed bracket
group is dropped whole; and the board keeps the full text.

Control: `--broken` runs the same probes against `PREFIX_REV`, whose edge
slices at 160 characters.
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
PREFIX_REV = "bad08ce"  # HEAD before E-199: the raw [:160] on both branches

PROBES = (
    "scope_alert_cut_marked",
    "scope_alert_cut_on_boundary",
    "routed_alert_cut_marked",
    "remainder_still_counted",
    "short_alert_verbatim",
    "open_bracket_dropped_whole",
    "board_keeps_the_full_text",
    "no_raw_slice_left",
)

_LONG = ("commit missed 3 files outside any approved scope: "
         "docs/development/native/segments/07-runtime/segment-note-runtime-tail.md, "
         "hooks/engine/modules/blackboard_diagnostics.py — keep going")


def _ctx(tree: pathlib.Path) -> tuple[str, dict]:
    sys.path.insert(0, str(tree / "hooks" / "engine"))
    from modules import blackboard, audit
    root = tempfile.mkdtemp(prefix="alert-fit-")
    os.environ["CLAUDE_PROJECT_DIR"] = root
    os.environ.pop("OPENHANDS_PROJECT_DIR", None)
    blackboard.post_alert(root, "scope", "info", _LONG)
    blackboard.post_alert(root, "session", "info", _LONG)
    pending = len(blackboard.pending_alerts(root, "scope"))
    return audit.session_start({"cwd": root})["additionalContext"], {
        "root": root, "scope_alerts": pending}


def _shown(ctx: str, label: str) -> str | None:
    start = ctx.find(label)
    if start < 0:
        return None
    start += len(label)
    end = ctx.find("…", start)
    return ctx[start:] if end < 0 else ctx[start:end]


def _probe(name: str, tree: pathlib.Path) -> tuple[bool, str]:
    if name == "no_raw_slice_left":
        code = (tree / "hooks" / "engine" / "modules" / "audit.py").read_text(
            encoding="utf-8")
        ok = "get(\"text\", \"\"))[:160]" not in code and "get('text', ''))[:160]" not in code
        return ok, "session edge bounds both alert branches through the cutter"

    ctx, facts = _ctx(tree)
    shown = _shown(ctx, "SCOPE DEBT — ")
    routed = _shown(ctx, "session channel: ")

    if name == "scope_alert_cut_marked":
        ok = shown is not None and "…" in ctx and "blackboard_diag." not in ctx
        return ok, f"scope={shown[-50:] if shown else None!r}"
    if name == "scope_alert_cut_on_boundary":
        ok = (shown is not None and _LONG.startswith(shown)
              and (_LONG[len(shown):len(shown) + 1] in " ,;:" or shown == _LONG))
        return ok, f"next={_LONG[len(shown):len(shown) + 1] if shown is not None else '?'!r}"
    if name == "routed_alert_cut_marked":
        ok = (routed is not None and "…" in ctx and "blackboard_diag." not in ctx
              and _LONG.startswith(routed)
              and _LONG[len(routed):len(routed) + 1] in " ,;:")
        return ok, f"routed={routed[-40:] if routed else None!r}"
    if name == "board_keeps_the_full_text":
        sys.path.insert(0, str(tree / "hooks" / "engine"))
        from modules import blackboard
        board = blackboard.pending_alerts(facts["root"], "scope")
        ok = bool(board) and board[0].get("text") == _LONG
        return ok, f"alerts={facts['scope_alerts']}"

    sys.path.insert(0, str(tree / "hooks" / "engine"))
    from modules import blackboard
    root = tempfile.mkdtemp(prefix="alert-more-")
    os.environ["CLAUDE_PROJECT_DIR"] = root
    if name == "remainder_still_counted":
        for i in range(3):
            blackboard.post_alert(root, "scope", "info", f"missed file {i}")
        for i in range(3):
            blackboard.post_alert(root, "session", "info", f"routed note {i}")
        from modules import audit
        text = audit.session_start({"cwd": root})["additionalContext"]
        ok = "(+2 older)" in text and "(+2 more routed alerts)" in text
        return ok, (f"older={'+2 older' in text} "
                    f"routed={'+2 more routed alerts' in text}")
    if name == "short_alert_verbatim":
        short = "handoff needs a consumer"
        blackboard.post_alert(root, "scope", "info", short)
        from modules import audit
        text = audit.session_start({"cwd": root})["additionalContext"]
        ok = f"SCOPE DEBT — {short}." in text and f"{short}…" not in text
        return ok, f"line={text[text.find('SCOPE DEBT'):][:60]!r}"
    if name == "open_bracket_dropped_whole":
        alert = ("pending review of the release candidate (this parenthetical runs "
                 "well past the bound and describes the candidate build, its "
                 "provenance, the mirror freshness, the tag chain and the remaining "
                 "acceptance work without ever closing")
        blackboard.post_alert(root, "scope", "info", alert)
        from modules import audit
        text = audit.session_start({"cwd": root})["additionalContext"]
        shown = _shown(text, "SCOPE DEBT — ") or ""
        ok = shown.count("(") == shown.count(")") and "…" in text
        return ok, f"scope={shown[-40:]!r}"
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
    print(f"alert_elision_accuracy={value:.2f} ({passed}/{total})")
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
    return 0 if (ok or args.broken) else 1


if __name__ == "__main__":
    raise SystemExit(main())

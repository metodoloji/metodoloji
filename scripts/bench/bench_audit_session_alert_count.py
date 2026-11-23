#!/usr/bin/env python3
"""The audit session_start surfaces every routed alert its bound withholds (E-223).

The session edge surfaced routed alerts on the session channel as
`first text (+N more routed)`: a bare remainder with no unit, so five routed
alerts read as one and the reader could not tell withheld alerts from withheld
paths.

Claim, closed set: the parenthetical names its unit (`routed alert` /
`routed alerts`) and the true count, stays empty when a single alert is
surfaced, and keeps the first-alert text intact.

Control: `--broken` runs the same probes against `PREFIX_REV`, where the tail
is a bare `(+N more routed)`.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
import tarfile
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
PREFIX_REV = "f88ce5f"  # E-210 commit: the bare (+N more) marker

PROBES = (
    "one_alert_whole",
    "two_alerts_singular",
    "three_alerts_plural",
    "five_alerts_larger",
    "six_alerts_larger2",
)


def _setup(root: pathlib.Path, count: int) -> None:
    log = root / ".metodoloji" / "logs" / "hook-audit.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(json.dumps({"type": "session_start", "hook_event": "SessionStart",
                              "session_id": "bench",
                              "timestamp": time.time() - 100}) + "\n",
                   encoding="utf-8")
    sys.path[:0] = [str(ROOT / "hooks" / "engine")]
    from modules import blackboard as bb
    for i in range(1, count + 1):
        bb.post_alert(str(root), "session", "info", f"alert text {i}")


def _context(tree: pathlib.Path, root: pathlib.Path) -> str:
    script = (
        "import sys, json, os\n"
        "os.environ['CLAUDE_PROJECT_DIR'] = sys.argv[1]\n"
        "sys.path[:0] = ['hooks/engine']\n"
        "from modules import audit\n"
        "out = audit.session_start({'cwd': sys.argv[1]})\n"
        "print(json.dumps({'ctx': out.get('additionalContext', '')}))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(root)],
        capture_output=True, text=True, cwd=str(tree),
        stdin=subprocess.DEVNULL)
    try:
        return str(json.loads(result.stdout).get("ctx") or "")
    except (json.JSONDecodeError, AttributeError):
        return result.stderr or result.stdout or ""


def _probe(name: str, tree: pathlib.Path) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as raw:
        root = pathlib.Path(raw)
        counts = {"one_alert_whole": 1, "two_alerts_singular": 2,
                  "three_alerts_plural": 3, "five_alerts_larger": 5,
                  "six_alerts_larger2": 6}[name]
        _setup(root, counts)
        ctx = _context(tree, root)
        if name == "one_alert_whole":
            ok = "more" not in ctx and "routed alert(s) on the session channel:" in ctx
        elif name == "two_alerts_singular":
            ok = "(+1 more routed alert)" in ctx
        elif name == "three_alerts_plural":
            ok = "(+2 more routed alerts)" in ctx
        elif name == "five_alerts_larger":
            ok = "(+4 more routed alerts)" in ctx
        else:
            ok = "(+5 more routed alerts)" in ctx
        marker = re.search(r"routed alert\(s\) on the session channel:.*?(?: \(\+\d+ more routed alerts?\)|;\s*clear with)",
                           ctx)
        detail = marker.group(0)[:150] if marker else ctx[-70:]
        return ok, detail


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
    print(f"audit_session_alert_count_accuracy={value:.2f} ({passed}/{total})")
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

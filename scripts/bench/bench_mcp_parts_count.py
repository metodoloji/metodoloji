#!/usr/bin/env python3
"""The MCP steering line names its withheld servers (E-226).

`state._bounded_parts_line` is shared by the sprint line and the MCP steering
line. E-225 named the sprint caller's unit (`entries`); the MCP caller still
passed none, so the session-start inject printed:

    mcp: 8 active server(s) — …, … (+3 more)

E-224 had already named the servers on orient's MCP line; this one surface kept
the bare count. The caller now passes `server`, singular for one and plural for
more, without changing the count, the 200-character bound or the names kept.

Claim, closed set: a fitting line prints with no marker; a wide line ends
`(+3 more servers)`; no bare `(+N more)` remains; the session-start inject
carries the unit; and the line stays within 200 characters. Every probe runs the
shipped renderer or the shipped session edge; nothing is sampled.

Control: `--broken` runs the same probes against `PREFIX_REV`, the tree before
E-225, where the shared marker is bare (three of five probes fail).
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
PREFIX_REV = "2497e68"  # HEAD before E-225: the bare `(+N more)` marker
LIMIT = 200

_LONG = (
    "claude.ai Atlassian Jira Cloud (production instance)",
    "claude.ai Figma Context Server (design workspace)",
    "claude.ai Google Drive (shared drives only)",
    "claude.ai Slack Workspace (engineering org)",
    "claude.ai Playwright Browser Automation",
    "claude.ai Sentry Production Errors",
)

PROBES = (
    "fits_unmarked",
    "plural_remainder",
    "no_bare_marker",
    "live_inject_uses_unit",
    "bound_holds",
)


def _line(tree: pathlib.Path, names: list[str]) -> str:
    sys.path.insert(0, str(tree / "hooks" / "engine"))
    from modules import state
    return state.mcp_steering_line(
        {"servers": [{"name": n, "enabled": True} for n in names]})


def _probe(name: str, tree: pathlib.Path) -> tuple[bool, str]:
    if name == "fits_unmarked":
        line = _line(tree, ["s0", "s1"])
        ok = "…" not in line and "more" not in line
        return ok, f"line={line!r}"

    if name == "plural_remainder":
        line = _line(tree, list(_LONG))
        ok = "(+3 more servers)" in line and "(+3 more)" not in line
        return ok, f"line={line!r}"

    if name == "no_bare_marker":
        line = _line(tree, list(_LONG))
        ok = re.search(r"\(\+\d+ more\)", line) is None and "more server" in line
        return ok, f"line={line!r}"

    if name == "live_inject_uses_unit":
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / ".mcp.json").write_text(
                json.dumps({"mcpServers": {n: {"command": "npx x"} for n in _LONG}}),
                encoding="utf-8")
            os.environ["CLAUDE_PROJECT_DIR"] = str(root)
            os.environ.pop("OPENHANDS_PROJECT_DIR", None)
            sys.path.insert(0, str(tree / "hooks" / "engine"))
            from modules.audit import session_start
            ctx = session_start({"cwd": str(root)})["additionalContext"]
            at = ctx.find("mcp: ")
            line = ctx[at:].split(". ", 1)[0] if at >= 0 else ""
            ok = at >= 0 and "(+3 more servers)" in line and len(line) <= LIMIT
            return ok, f"line={line[:120]!r}"

    if name == "bound_holds":
        line = _line(tree, list(_LONG))
        return len(line) <= LIMIT, f"len={len(line)}"

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
    print(f"mcp_parts_count_accuracy={value:.2f} ({passed}/{total})")
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

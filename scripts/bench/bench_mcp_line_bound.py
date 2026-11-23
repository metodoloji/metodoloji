#!/usr/bin/env python3
"""The MCP steering line counts the servers it withheld (E-200).

`mcp_steering_line` was modelled on the sprint line and copied its defect: it
joined four names and then a bare ``", …"``, so seven enabled servers read as
four with nothing saying three were missing — and the finished line was still
sliced by ``line[:200]``, which can cut a name in half *and* eat the only marker
admitting the shortening. Found by inspection of the same class E-198 removed
from the sprint line, and confirmed live: a board with seven enabled servers
emitted ``mcp: 7 active server(s) — … Linear, …`` with no remainder count.

Claim, closed set: every enabled server is either named whole or counted in
``… (+N more)``; the line never exceeds 200 characters; a single name wider
than the line is shortened with its prefix kept and the elision marked; a line
that fits names every server with no marker; disabled servers are not named and
not counted; and the shipped session-start inject carries that counted line.

Control: `--broken` runs the same probes against `PREFIX_REV`, whose function
slices the finished line at 200.
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
PREFIX_REV = "bad08ce"  # HEAD before E-200: names[:4] + line[:200]

PROBES = (
    "withheld_counted",
    "long_names_counted",
    "shown_names_whole",
    "bound_respected",
    "oversized_name_marked",
    "disabled_not_counted",
    "live_inject_names_the_count",
    "no_trailing_slice",
)

_SHORT = ["claude.ai Google Drive", "claude.ai Notion", "claude.ai Linear",
          "claude.ai Figma Context Server", "claude.ai Atlassian Jira Cloud",
          "claude.ai Slack Workspace", "claude.ai Sentry Production"]
_LONG = ["claude.ai Atlassian Jira Cloud (production instance)",
         "claude.ai Figma Context Server (design workspace)",
         "claude.ai Google Drive (shared drives only)",
         "claude.ai Slack Workspace (engineering org)",
         "claude.ai Playwright Browser Automation",
         "claude.ai Sentry Production Errors"]


def _line(tree: pathlib.Path, names: list[str],
          disabled: list[str] | None = None) -> tuple[str, pathlib.Path]:
    sys.path.insert(0, str(tree / "hooks" / "engine"))
    from modules import state
    root = pathlib.Path(tempfile.mkdtemp(prefix="mcp-fit-"))
    servers = {n: {"command": "npx x"} for n in names}
    servers.update({n: {"command": "npx x", "disabled": True}
                    for n in (disabled or [])})
    (root / ".mcp.json").write_text(json.dumps({"mcpServers": servers}),
                                    encoding="utf-8")
    return state.mcp_steering_line(state.mcp_inventory(str(root))), root


def _hidden(line: str) -> int | None:
    if "(+" not in line:
        return 0
    tail = line.rsplit("(+", 1)[1]
    try:
        return int(tail.split(" ", 1)[0])
    except ValueError:
        return None


def _probe(name: str, tree: pathlib.Path) -> tuple[bool, str]:
    if name == "no_trailing_slice":
        code = (tree / "hooks" / "engine" / "modules" / "state.py").read_text(
            encoding="utf-8")
        ok = ("return line[:200]" not in code
              and 'line = f"mcp:' not in code)
        return ok, "mcp line no longer slices the finished sentence"

    if name == "live_inject_names_the_count":
        sys.path.insert(0, str(tree / "hooks" / "engine"))
        from modules import audit
        root = pathlib.Path(tempfile.mkdtemp(prefix="mcp-inject-"))
        os.environ["CLAUDE_PROJECT_DIR"] = str(root)
        os.environ.pop("OPENHANDS_PROJECT_DIR", None)
        (root / ".mcp.json").write_text(
            json.dumps({"mcpServers": {n: {"command": "npx x"} for n in _LONG}}),
            encoding="utf-8")
        ctx = audit.session_start({"cwd": str(root)})["additionalContext"]
        at = ctx.find("mcp: ")
        line = ctx[at:].split(". ", 1)[0] if at >= 0 else ""
        ok = at >= 0 and "(+3 more servers)" in line and len(line) <= 200
        return ok, f"line={line[:110]!r}"

    if name == "disabled_not_counted":
        line, _root = _line(tree, _SHORT[:2], disabled=["claude.ai Off Server"])
        ok = ("mcp: 2 active server(s)" in line and "Off Server" not in line
              and "(+" not in line)
        return ok, f"line={line!r}"

    if name == "oversized_name_marked":
        giant = "claude.ai " + "very-long-server-name-" * 20
        line, _root = _line(tree, [giant])
        ok = len(line) <= 200 and line.endswith("…") and line.startswith("mcp: 1 ")
        return ok, f"len={len(line)} tail={line[-24:]!r}"

    if name in ("withheld_counted", "long_names_counted", "shown_names_whole",
                "bound_respected"):
        names = _SHORT if name == "withheld_counted" else _LONG
        line, _root = _line(tree, names)
        hidden = _hidden(line)
        if name == "bound_respected":
            return len(line) <= 200, f"len={len(line)}"
        if name == "withheld_counted":
            shown = sum(1 for n in names if n in line)
            ok = (hidden is not None and "(+" in line
                  and hidden == len(names) - shown)
            return ok, f"shown={shown} hidden={hidden} line={line[-40:]!r}"
        if name == "long_names_counted":
            shown = sum(1 for n in names if n in line)
            ok = hidden == len(names) - shown and hidden > 0 and "(+" in line
            return ok, f"shown={shown} hidden={hidden}"
        # shown_names_whole: every name fragment in the line is a real name.
        body = line.split(" — ", 1)[-1]
        for marker in ("… (+", ", …"):
            if marker in body:
                body = body.split(marker, 1)[0]
        fragments = [f.strip() for f in body.split(", ") if f.strip()]
        ok = all(f in names for f in fragments) and bool(fragments)
        return ok, f"fragments={fragments[-2:]!r}"
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
    print(f"mcp_line_bound_accuracy={value:.2f} ({passed}/{total})")
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

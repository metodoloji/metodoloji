#!/usr/bin/env python3
"""The orient panel names MCP config sources once and counts what it withholds (E-212).

Live panel of the shipped `orient.py`, five enabled servers in one project
`.mcp.json` (2026-11-22):

    mcp: 5 active server(s) — srv1, srv2, srv3, srv4, srv5
                        sources: project:./.mcp.json, project:./.mcp.json, project:./.mcp.json

The sources line bounded the list at three *before* printing and never counted
the rest, so five servers from one file became the same path three times — the
line was simultaneously redundant and silent about two servers. With servers
spread over four or more configs the same bound hid whole config files. The
steering next-step one block up had the matching shape: `, … (+1)` — a count
with no noun, so it read as "+1 what?".

Claim, closed set: the sources line dedupes by config source (one `.mcp.json`
prints once) and counts what its bound withheld; a source set of three or fewer
prints whole with no marker; and the steering step's server remainder names its
unit, singular and plural.

Control: `--broken` runs the same probes against `PREFIX_REV`, where the line
repeats the path and counts nothing, and the step prints a bare `(+N)`.
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
PREFIX_REV = "f88ce5f"  # E-210 commit: the uncounted, undeduped sources line

PROBES = (
    "single_config_printed_once",
    "distinct_sources_counted",
    "small_source_set_whole",
    "steering_server_plural",
    "steering_server_singular",
)


def _servers(names: list[str], disabled: bool = False) -> str:
    return json.dumps({"mcpServers": {
        name: ({"command": "x", "disabled": True} if disabled else {"command": "x"})
        for name in names}})


def _project(tmp: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    proj = tmp / "proj"
    proj.mkdir()
    home = tmp / "home"
    home.mkdir()
    return proj, home


def _panel(tree: pathlib.Path, proj: pathlib.Path, home: pathlib.Path) -> str:
    env = dict(os.environ, HOME=str(home), CLAUDE_PROJECT_DIR=str(proj))
    env.pop("OPENHANDS_PROJECT_DIR", None)
    result = subprocess.run(
        [sys.executable, str(tree / "hooks" / "scripts" / "orient.py"),
         "--project-root", str(proj)],
        capture_output=True, text=True, cwd=str(tree), env=env,
        stdin=subprocess.DEVNULL)
    return result.stdout + result.stderr


def _source_line(text: str) -> str:
    lines = [ln for ln in text.splitlines() if "sources:" in ln]
    return lines[0] if lines else ""


def _step(text: str) -> str:
    for line in text.splitlines():
        if "mcp servers reachable" in line:
            return line
    return ""


def _five_distinct_sources(proj: pathlib.Path, home: pathlib.Path) -> None:
    (home / ".openhands").mkdir(parents=True, exist_ok=True)
    files = {
        proj / ".mcp.json": "s1",
        proj / ".vscode" / "mcp.json": "s2",
        proj / ".cursor" / "mcp.json": "s3",
        home / ".claude.json": "s4",
        home / ".openhands" / "mcp.json": "s5",
    }
    for path, name in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_servers([name]), encoding="utf-8")


def _probe(name: str, tree: pathlib.Path) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as raw:
        proj, home = _project(pathlib.Path(raw))

        if name == "single_config_printed_once":
            (proj / ".mcp.json").write_text(
                _servers([f"srv{i}" for i in range(1, 6)]), encoding="utf-8")
            line = _source_line(_panel(tree, proj, home))
            ok = (line.count("project:./.mcp.json") == 1
                  and "…" not in line and "more" not in line)
            return ok, line.strip()
        if name == "distinct_sources_counted":
            _five_distinct_sources(proj, home)
            line = _source_line(_panel(tree, proj, home))
            ok = "(+2 more sources)" in line and line.count("…") == 1
            return ok, line.strip()
        if name == "small_source_set_whole":
            (proj / ".mcp.json").write_text(_servers(["a"]), encoding="utf-8")
            (proj / ".vscode").mkdir()
            (proj / ".vscode" / "mcp.json").write_text(_servers(["b"]), encoding="utf-8")
            (proj / ".cursor").mkdir()
            (proj / ".cursor" / "mcp.json").write_text(_servers(["c"]), encoding="utf-8")
            line = _source_line(_panel(tree, proj, home))
            ok = (line.count("project:./.mcp.json") == 1
                  and "project:./.vscode/mcp.json" in line
                  and "project:./.cursor/mcp.json" in line
                  and "more" not in line)
            return ok, line.strip()
        if name == "steering_server_plural":
            (proj / ".mcp.json").write_text(
                _servers([f"srv{i}" for i in range(1, 7)]), encoding="utf-8")
            step = _step(_panel(tree, proj, home))
            ok = "… (+2 more servers)" in step
            return ok, step.split(" reachable this session: ")[-1][:60]
        if name == "steering_server_singular":
            (proj / ".mcp.json").write_text(
                _servers([f"srv{i}" for i in range(1, 6)]), encoding="utf-8")
            step = _step(_panel(tree, proj, home))
            ok = "… (+1 more server)" in step
            return ok, step.split(" reachable this session: ")[-1][:60]
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
    print(f"orient_sources_count_accuracy={value:.2f} ({passed}/{total})")
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

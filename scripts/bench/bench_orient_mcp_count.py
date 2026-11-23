#!/usr/bin/env python3
"""The orient MCP steering line counts every server its bound withholds (E-224).

The orient panel listed MCP servers reachable this session as a hand-rolled
`…, srv4, … (+N more server(s))` marker, duplicating counted_list. The line now
goes through the common limiter (`orient._named` → `counted_list`).

Claim, closed set: four or fewer enabled servers print whole with no marker;
five prints `… (+1 more server)`; six prints `… (+2 more servers)`; eight
prints `… (+4 more servers)`; the unit is always present; and the steering line
is rendered THROUGH the shared limiter (a probe the previous inline builder
cannot pass).

Control: `--broken` runs the same probes against `PREFIX_REV` — the tree before
this change, whose line still carries the marker text but builds it inline, so
the delegation probe fails (5/6).
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
PREFIX_REV = "bc1e93a"  # HEAD before this change: same marker, built inline

PROBES = (
    "two_servers_whole",
    "four_servers_whole",
    "five_servers_singular",
    "six_servers_plural",
    "eight_servers_larger",
    "shared_limiter_called",
)


def _mcp(root: pathlib.Path, count: int) -> None:
    (root / ".mcp.json").write_text(
        json.dumps({"mcpServers": {
            f"srv{i}": {"command": "x"} for i in range(1, count + 1)}}),
        encoding="utf-8")


def _step(tree: pathlib.Path, root: pathlib.Path) -> str:
    env = {}
    result = subprocess.run(
        [sys.executable, str(tree / "hooks" / "scripts" / "orient.py"),
         "--project-root", str(root)],
        capture_output=True, text=True, cwd=str(tree), env=None,
        stdin=subprocess.DEVNULL)
    for line in (result.stdout + result.stderr).splitlines():
        if "mcp servers reachable" in line:
            return line
    return ""


def _delegation(tree: pathlib.Path, root: pathlib.Path) -> dict:
    """Render the steering step with `orient._named` spied on.

    The parent's line is textually identical; only the delegation differs, so
    this is the one probe that can tell the two implementations apart.
    """
    script = (
        "import sys, json, pathlib\n"
        "sys.path[:0] = [sys.argv[1] + '/hooks/scripts']\n"
        "import orient\n"
        "calls = []\n"
        "orig = orient._named\n"
        "def spy(items, limit, noun):\n"
        "    calls.append(noun)\n"
        "    return orig(items, limit, noun)\n"
        "orient._named = spy\n"
        "digest = orient.build(pathlib.Path(sys.argv[2]))\n"
        "step = next(s for s in digest['next_steps']\n"
        "            if s.startswith('mcp servers reachable'))\n"
        "print(json.dumps({'step': step, 'nouns': calls}))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(tree), str(root)],
        capture_output=True, text=True, cwd=str(tree),
        stdin=subprocess.DEVNULL)
    try:
        return json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return {"step": "", "nouns": []}


def _probe(name: str, tree: pathlib.Path) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as raw:
        root = pathlib.Path(raw)
        if name == "shared_limiter_called":
            _mcp(root, 6)
            payload = _delegation(tree, root)
            ok = ("server" in (payload.get("nouns") or [])
                  and "… (+2 more servers)" in payload.get("step", ""))
            return ok, (f"nouns={payload.get('nouns')} "
                        f"step={str(payload.get('step'))[-45:]!r}")
        counts = {"two_servers_whole": 2, "four_servers_whole": 4,
                  "five_servers_singular": 5, "six_servers_plural": 6,
                  "eight_servers_larger": 8}[name]
        _mcp(root, counts)
        step = _step(tree, root)
        if name in ("two_servers_whole", "four_servers_whole"):
            ok = "more" not in step and "mcp servers reachable" in step
        elif name == "five_servers_singular":
            ok = "… (+1 more server)" in step
        elif name == "six_servers_plural":
            ok = "… (+2 more servers)" in step
        else:
            ok = "… (+4 more servers)" in step
        return ok, step.strip()[-70:]


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
    print(f"orient_mcp_count_accuracy={value:.2f} ({passed}/{total})")
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

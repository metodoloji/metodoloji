#!/usr/bin/env python3
"""The doctor panel counts the orphans and signals its lists withhold (E-206).

`doctor` bounds two printed lists — graph orphan endpoints at ten in the
report, freshness unclear signals at three in the panel — and then printed
what it had as the whole. Observed live on 2026-11-22 on a scratch board with
14 edges and 6 waiting signals:

    graph     14 edge(s) (10 orphan endpoint(s): ux.ghost00, ux.ghost01, ux.ghost02)
    ...sprint-2 (UNKNOWN): no code-state anchor (no witness commit)

The report's own cap read as the graph's orphan count, seven of the ten were
named nowhere, and three of six unclear signals were simply absent. An
operator triaging a mis-slugged key or deciding whether to retire a baton
reads both lines as the complete set.

Claim, closed set: the JSON report carries the full orphan and unclear totals,
the panel prints the full orphan count, names the withheld remainder on both
lines, keeps the report lists bounded, and stays silent when everything fits.

Control: `--broken` runs the same probes against `PREFIX_REV`, which has no
totals and prints the truncated lists as complete.
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
PREFIX_REV = "26b83f8"  # E-205 commit: orphans[:10] / unclear[:3], no totals


def _imports(tree: pathlib.Path):
    sys.path.insert(0, str(tree / "hooks" / "engine"))
    from modules import blackboard as bb
    return bb


def _board(tree: pathlib.Path, base: pathlib.Path, edges: int,
           signals: int) -> pathlib.Path:
    bb = _imports(tree)
    root = base / "proj"
    root.mkdir(parents=True)
    bb.write_key(str(root), "prd.acme", "v1", hot=True)
    for i in range(edges):
        bb.link(str(root), "prd.acme", f"ux.ghost{i:02d}", relation="informs")
    for i in range(signals):
        bb.post_handoff(str(root), "bmad-create-story", f"sprint-{i}", f"ready {i}")
    return root


def _panel(tree: pathlib.Path, root: pathlib.Path) -> str:
    cli = tree / "hooks" / "scripts" / "blackboard.py"
    result = subprocess.run([sys.executable, str(cli), "doctor",
                             "--project-root", str(root)],
                            capture_output=True, text=True, cwd=str(tree),
                            stdin=subprocess.DEVNULL)
    return result.stdout


def _probe(name: str, tree: pathlib.Path) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        if name == "under_bound_silent":
            root = _board(tree, base, edges=1, signals=0)
            panel = _panel(tree, root)
            ok = ("more orphan endpoint" not in panel and "more unclear" not in panel
                  and "1 orphan endpoint(s): ux.ghost00)" in panel)
            return ok, f"line={next((l for l in panel.splitlines() if 'graph ' in l), '')[:80]!r}"

        root = _board(tree, base, edges=14, signals=6)
        report = _imports(tree).doctor(str(root))
        graph = report["checks"]["graph"]
        fresh = report["checks"]["freshness"]
        panel = _panel(tree, root)

        if name == "orphan_total_not_the_cap":
            ok = graph.get("orphans_total") == 14 and len(graph["orphans"]) == 10
            return ok, f"total={graph.get('orphans_total')} listed={len(graph['orphans'])}"
        if name == "unclear_total_not_the_cap":
            ok = fresh.get("unclear_total") == 6 and len(fresh["unclear"]) == 6
            return ok, f"total={fresh.get('unclear_total')} listed={len(fresh['unclear'])}"
        if name == "panel_prints_the_orphan_total":
            ok = "14 orphan endpoint(s)" in panel
            return ok, f"line={next((l for l in panel.splitlines() if 'graph ' in l), '')[:90]!r}"
        if name == "panel_counts_the_orphan_remainder":
            ok = "… (+11 more orphan endpoints)" in panel
            return ok, f"has_marker={'+11 more' in panel}"
        if name == "panel_counts_the_unclear_remainder":
            ok = ("… (+3 more unclear signals)" in panel
                  and panel.count("no code-state anchor") == 3)
            return ok, f"rows={panel.count('no code-state anchor')}"
        if name == "report_lists_stay_bounded":
            ok = len(graph["orphans"]) == 10
            return ok, f"listed={len(graph['orphans'])}"
    raise SystemExit(f"unknown probe: {name}")


PROBES = (
    "orphan_total_not_the_cap",
    "unclear_total_not_the_cap",
    "panel_prints_the_orphan_total",
    "panel_counts_the_orphan_remainder",
    "panel_counts_the_unclear_remainder",
    "report_lists_stay_bounded",
    "under_bound_silent",
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
    print(f"doctor_panel_bound_accuracy={value:.2f} ({passed}/{total})")
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

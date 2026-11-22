#!/usr/bin/env python3
"""The stale-approval denial reports the whole blast radius (E-209).

`scope_gate.find_approved` prints, under `[hooks] freshness = deny`, why an
APPROVED and scope-matched record no longer opens code:

    record docs/experiments/E-XXX.md is APPROVED and scope-matched, but the code
    has moved past it: N file(s) in the claim's scope changed since it was
    recorded (a.py, b.py, c.py changed after approval, witness 0123…).

`fresh['changed'][:3]` cut that enumeration at three paths with no marker. The
STALE detail carries up to `[freshness] max_digest_files` paths — live on this
tree 2026-11-22, 82 records were STALE with more than three changed paths
(E-148: 145, E-038: 271) — so the denial named three and let the reader take
them for the blast radius. The decision that follows (re-measure, or amend the
plan) is sized on that list.

Claim, closed set: five changed paths render three plus "(+2 more paths)"; a
twenty-path list renders exactly three plus the remaining seventeen; a list
that fits prints verbatim with no marker; a verdict with no paths still reads
"code state" and prints no marker; a 145-path list counts its 142 withheld; and
the witness anchor survives.

Control: `--broken` runs the same probes against `PREFIX_REV`, where `[:3]`
prints three paths and no count.
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
PREFIX_REV = "26b83f8"  # E-205 commit: scope_gate's `changed[:3]`

PATHS = [f"src/mod_{i:03d}.py" for i in range(300)]


def _message(tree: pathlib.Path, changed: list[str], reason: str) -> str:
    """The shipped denial string, driven through find_approved itself."""
    sys.path.insert(0, str(tree / "hooks" / "engine"))
    from modules import guard as guard_mod
    from modules import freshness as freshness_mod

    with tempfile.TemporaryDirectory() as project:
        return _run(tree, project, guard_mod, freshness_mod, changed, reason)


def _run(tree, project_root, guard_mod, freshness_mod, changed, reason) -> str:
    project = pathlib.Path(project_root)
    (project / "docs/experiments").mkdir(parents=True, exist_ok=True)
    rec = project / "docs/experiments/E-001.md"
    rec.write_text("# E\n", encoding="utf-8")
    guard_mod.verify_record = lambda _rec: (0, "src/**")
    guard_mod._load_gate = lambda: True
    guard_mod.gate = type("G", (), {"scope_matches": staticmethod(lambda s, t: True)})()
    guard_mod.record_scope_cached = lambda _rec: "src/**"
    guard_mod._record_freshness = lambda _root, _rec: {
        "verdict": "STALE", "reason": reason, "changed": list(changed),
        "commit": "0123456789abcdef0123", "record": "E-001.md"}
    freshness_mod.mode = lambda: "deny"
    freshness_mod.unknown_policy = lambda: "warn"
    guard_mod._VERIFY_CACHE.clear()
    _ok, detail, _fresh = guard_mod.find_approved("src/a.py", root=str(project))
    return detail


def _snippet(msg: str) -> str:
    """The clause the probes are about, without the remediation tail."""
    head = msg.split("changed after approval", 1)[0]
    return head.split("has moved past it: ", 1)[-1][:160]


def _probe(name: str, tree: pathlib.Path) -> tuple[bool, str]:
    reason = "5 file(s) in the claim's scope changed since it was recorded"
    if name == "counts_remainder":
        msg = _message(tree, PATHS[:5], reason)
        return ("src/mod_000.py, src/mod_001.py, src/mod_002.py, "
                "… (+2 more paths)") in msg, _snippet(msg)
    if name == "bound_is_three":
        msg = _message(tree, PATHS[:20], "20 file(s) in the claim's scope changed since it was recorded")
        named = sum(1 for i in range(20) if f"src/mod_{i:03d}.py" in msg)
        return named == 3 and "(+17 more paths)" in msg, f"named={named}"
    if name == "fits_verbatim":
        msg = _message(tree, ["src/a.py", "src/b.py"], "2 file(s) in the claim's scope changed since it was recorded")
        return "(src/a.py, src/b.py changed after approval" in msg and "more path" not in msg, _snippet(msg)
    if name == "empty_code_state":
        msg = _message(tree, [], "superseded by E-54 (cascade invalidation)")
        return "(code state changed after approval" in msg and "more path" not in msg, _snippet(msg)
    if name == "hundreds_counted":
        msg = _message(tree, PATHS[:145], "145 file(s) in the claim's scope changed since it was recorded")
        return "(+142 more paths)" in msg, _snippet(msg)
    if name == "witness_anchor":
        msg = _message(tree, PATHS[:5], reason)
        return "witness 0123456789ab)" in msg, _snippet(msg)
    raise SystemExit(f"unknown probe: {name}")


PROBES = (
    "counts_remainder",
    "bound_is_three",
    "fits_verbatim",
    "empty_code_state",
    "hundreds_counted",
    "witness_anchor",
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


def _run_all(tree: pathlib.Path, samples: pathlib.Path | None, broken: bool) -> bool:
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
    print(f"stale_notice_blast_radius_accuracy={value:.2f} ({passed}/{total})")
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
        ok = _run_all(tree, args.samples, args.broken)
    return 0 if (ok or args.broken) else 1


if __name__ == "__main__":
    raise SystemExit(main())

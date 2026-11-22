#!/usr/bin/env python3
"""The stale-approval denial tells a supersede apart from code drift (E-210).

`scope_gate.find_approved` has one STALE branch, so a record that a later
re-measurement superseded was denied with the drift wording:

    record docs/experiments/E-117.md is APPROVED and scope-matched, but the code
    has moved past it: superseded by E-118 (cascade invalidation) (code state
    changed after approval, witness 18ed6a8097f0). Re-measure in a new E record
    (add a plain `- **Re-Measured-By:** E-NNN (date)` line to E-117.md), …

Live on this tree 2026-11-22: 27 records carry a resolving supersede marker,
and E-117's notice claimed exactly that. Nothing in the tree had moved — the
record had been re-measured — and the remedy pointed at the Re-Measured-By
line the record already carried, which is what made it STALE. The warn-mode
line (`[hooks] freshness = warn`, the shipped default) carried the same advice.

Claim, closed set: a supersede denial says a later record supersedes it, never
that the code moved, prints no witness, and offers no marker to add; a drift
denial keeps its code-moved wording and witness; a short drift list still
prints whole; and the warn-line advice is supersede-aware while the drift
advice keeps the marker remedy.

Control: `--broken` runs the same probes against `PREFIX_REV`, where the one
STALE branch treats a supersede as drift and the advice always asks for the
marker.
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
PREFIX_REV = "f88ce5f"  # E-209 commit: the single STALE branch

SUPERSEDE_REASON = "superseded by E-118 (cascade invalidation)"
DRIFT_REASON = "2 file(s) in the claim's scope changed since it was recorded"
PATHS = ["src/a.py", "src/b.py"]


def _load(tree: pathlib.Path):
    sys.path.insert(0, str(tree / "hooks" / "engine"))
    from modules import guard as guard_mod
    from modules import freshness as freshness_mod
    return guard_mod, freshness_mod


def _message(tree: pathlib.Path, changed: list[str], reason: str,
             superseded_by: str) -> str:
    """The shipped denial string, driven through find_approved itself."""
    guard_mod, freshness_mod = _load(tree)
    with tempfile.TemporaryDirectory() as project_root:
        project = pathlib.Path(project_root)
        (project / "docs/experiments").mkdir(parents=True, exist_ok=True)
        (project / "docs/experiments/E-001.md").write_text("# E\n", encoding="utf-8")
        guard_mod.verify_record = lambda _rec: (0, "src/**")
        guard_mod._load_gate = lambda: True
        guard_mod.gate = type("G", (), {
            "scope_matches": staticmethod(lambda s, t: True)})()
        guard_mod.record_scope_cached = lambda _rec: "src/**"
        guard_mod._record_freshness = lambda _root, _rec: {
            "verdict": "STALE", "reason": reason, "changed": list(changed),
            "superseded_by": superseded_by,
            "commit": "0123456789abcdef0123", "record": "E-001.md"}
        freshness_mod.mode = lambda: "deny"
        freshness_mod.unknown_policy = lambda: "warn"
        guard_mod._VERIFY_CACHE.clear()
        _ok, detail, _fresh = guard_mod.find_approved("src/a.py", root=str(project))
        return detail


def _advice(tree: pathlib.Path) -> str:
    guard_mod, _ = _load(tree)
    return guard_mod._freshness_advice({
        "verdict": "STALE", "reason": SUPERSEDE_REASON,
        "changed": [], "superseded_by": "E-118"})


def _probe(name: str, tree: pathlib.Path) -> tuple[bool, str]:
    if name == "supersede_is_named":
        msg = _message(tree, [], SUPERSEDE_REASON, "E-118")
        return "a later record supersedes it: superseded by E-118" in msg, msg[:120]
    if name == "no_code_moved_claim":
        msg = _message(tree, [], SUPERSEDE_REASON, "E-118")
        return ("the code has moved past it" not in msg
                and "code state changed after approval" not in msg), msg[:120]
    if name == "no_marker_advice":
        msg = _message(tree, [], SUPERSEDE_REASON, "E-118")
        return ("Re-Measured-By:** E-NNN" not in msg
                and "--amend-plan" in msg
                and "0123456789ab" not in msg), msg[-160:]
    if name == "drift_keeps_the_code_moved_wording":
        msg = _message(tree, PATHS, DRIFT_REASON, "")
        return ("the code has moved past it" in msg
                and "(src/a.py, src/b.py changed after approval, "
                    "witness 0123456789ab)" in msg), msg[:160]
    if name == "warn_advice_is_supersede_aware":
        advice = _advice(tree)
        return ("Re-Measured-By" not in advice
                and "--amend-plan" in advice), advice
    if name == "warn_advice_keeps_the_marker_remedy":
        guard_mod, _ = _load(tree)
        advice = guard_mod._freshness_advice({
            "verdict": "STALE", "reason": DRIFT_REASON, "changed": PATHS})
        return "Re-Measured-By" in advice and "--amend-plan" in advice, advice
    raise SystemExit(f"unknown probe: {name}")


PROBES = (
    "supersede_is_named",
    "no_code_moved_claim",
    "no_marker_advice",
    "drift_keeps_the_code_moved_wording",
    "warn_advice_is_supersede_aware",
    "warn_advice_keeps_the_marker_remedy",
)


def driver(name: str, tree: pathlib.Path) -> None:
    try:
        ok, detail = _probe(name, tree)
    except Exception as exc:  # a missing helper in the control tree is a FAIL
        ok, detail = False, f"{type(exc).__name__}: {exc}"[:160]
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
    print(f"supersede_notice_wording_accuracy={value:.2f} ({passed}/{total})")
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

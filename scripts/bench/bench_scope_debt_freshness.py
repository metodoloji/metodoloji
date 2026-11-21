#!/usr/bin/env python3
"""A persisted scope-debt claim is re-derived before it is repeated (E-188).

Ground truth measured live on this tree, 2026-11-20 (session_start):

    SCOPE DEBT — SCOPE COVERAGE: uv.lock — outside every approved Code Scope;
    open an E record with this file in its scope before relying on these edits

while `git status` was clean and the commit gate's own coverage check answered
`([], True)` — no edited file outside an approved scope. The "scope" channel
holds an observation from commit time, and the session edge replayed it in the
present tense forever: an alert posted while `uv.lock` briefly differed kept
instructing every new session to open an E record for a file that had gone back
to matching HEAD.

Claim, closed set: the edge labels a scope debt STALE exactly when the gate's
own coverage check RUNS and finds no uncovered edit; it never consumes the
alert, and an unverifiable tree is never called clean.

Control: `--broken` runs the same probes against the pre-change tree
(`PREFIX_REV`), which repeats the claim with no verdict at all.
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
PREFIX_REV = "c302171"  # the parent of the commit that ships the STALE label
KEY = "bench-scope-key"
CLAIM = "fixture_metric >= 0.9"

PROBES = (
    "paid_debt_marked_stale",
    "live_debt_present_tense",
    "unverifiable_not_clean",
    "inscope_edit_pays",
    "stale_not_consumed",
    "remedy_runnable",
    "verdict_matches_gate",
    "older_siblings_counted",
    "line_bounded",
    "no_alert_no_line",
)


# ---------------------------------------------------------------- fixtures

def _imports(tree: pathlib.Path) -> None:
    sys.path.insert(0, str(tree / "hooks" / "engine"))
    sys.path.insert(0, str(tree))


def _git(root: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(root), capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=30, stdin=subprocess.DEVNULL)


def _approved_record(root: pathlib.Path, tree: pathlib.Path, scope: str) -> None:
    gate_scripts = tree / "skills" / "bmad-research-experiment" / "scripts"
    if str(gate_scripts) not in sys.path:
        sys.path.insert(0, str(gate_scripts))
    import run_experiment as gate_mod
    token = gate_mod.gate_token(CLAIM, 1.0, "E-001", KEY.encode("utf-8"))
    path = root / "docs" / "experiments" / "E-001-scope-fixture.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "## Experiment: E-001 — scope fixture\n"
        "- **Status:** completed\n"
        f'- **Hypothesis:** H-001: "{CLAIM}"\n'
        f"- **Measurement Metrics:** {CLAIM}\n"
        f"- **Code Scope:** {scope}\n"
        "- **Raw Results:** measured=1.0; n=35\n"
        "- **Decision:** APPROVED — H-001: measured=1.0 >= threshold=0.9\n"
        f'- **Gate Evidence:** measured=1.0 claim="{CLAIM}" {token}\n',
        encoding="utf-8")


def _repo(base: pathlib.Path, tree: pathlib.Path, *, scope: str | None = None,
          probe: str | None = None, edit_tracked: bool = False) -> pathlib.Path:
    root = base / "project"
    (root / "lib").mkdir(parents=True)
    (root / "README.md").write_text("baseline\n", encoding="utf-8")
    (root / "lib" / "baseline.py").write_text("# baseline\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=bench@example.invalid",
         "-c", "user.name=bench", "commit", "-q", "-m", "baseline")
    if edit_tracked:
        (root / "lib" / "baseline.py").write_text("# baseline\n# edited\n",
                                                 encoding="utf-8")
    if probe:
        target = root / probe
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# probe\n", encoding="utf-8")
    if scope:
        _approved_record(root, tree, scope)
    return root


def _alert(root: pathlib.Path, text: str = "SCOPE COVERAGE: uv.lock — outside "
                                          "every approved Code Scope") -> None:
    from modules import blackboard as bb
    bb.post_alert(str(root), "scope", "warn", text, sender="quality-gate")


def _context(root: pathlib.Path) -> str:
    from modules.audit import session_start
    return session_start({"cwd": str(root)})["additionalContext"]


def _clause(ctx: str) -> str:
    return ctx.split("SCOPE DEBT", 1)[1].split(" — SCOPE COVERAGE", 1)[0]


# ---------------------------------------------------------------- probes

def _probe(name: str, tree: pathlib.Path, base: pathlib.Path) -> tuple[bool, str]:
    os.environ["BMAD_GATE_KEY"] = KEY
    _imports(tree)
    from modules import blackboard as bb

    if name == "paid_debt_marked_stale":
        root = _repo(base, tree)
        _alert(root)
        clause = _clause(_context(root))
        ok = "[STALE" in clause and "consume --channel scope" in clause
        return ok, f"clause={clause!r}"

    if name == "live_debt_present_tense":
        root = _repo(base, tree, probe="lib/new.py")
        _alert(root)
        ctx = _context(root)
        ok = "SCOPE DEBT — " in ctx and "[STALE" not in ctx
        return ok, f"clause={_clause(ctx)!r}"

    if name == "unverifiable_not_clean":
        root = base / "bare"
        root.mkdir()
        _alert(root)
        ctx = _context(root)
        ok = "SCOPE DEBT — " in ctx and "[STALE" not in ctx
        return ok, f"clause={_clause(ctx)!r}"

    if name == "inscope_edit_pays":
        root = _repo(base, tree, scope="lib/**", edit_tracked=True)
        _alert(root)
        clause = _clause(_context(root))
        return "[STALE" in clause, f"clause={clause!r}"

    if name == "stale_not_consumed":
        root = _repo(base, tree)
        _alert(root)
        clause = _clause(_context(root))
        pending = len(bb.pending_alerts(str(root), "scope"))
        ok = "[STALE" in clause and pending == 1
        return ok, f"pending={pending}"

    if name == "remedy_runnable":
        root = _repo(base, tree)
        _alert(root)
        clause = _clause(_context(root))
        ok = ("python3 " in clause and "blackboard.py consume --channel scope" in clause
              and (tree / "hooks" / "scripts" / "blackboard.py").is_file())
        return ok, f"clause={clause!r}"

    if name == "verdict_matches_gate":
        paid = _repo(base / "paid", tree)
        _alert(paid)
        paid_ctx = _context(paid)
        live = _repo(base / "live", tree, probe="lib/new.py")
        _alert(live)
        live_ctx = _context(live)
        from modules import guard
        misses_paid, ran_paid = guard._check_scope_coverage(str(paid))
        misses_live, ran_live = guard._check_scope_coverage(str(live))
        ok = (ran_paid and not misses_paid and "[STALE" in paid_ctx
              and ran_live and bool(misses_live) and "[STALE" not in live_ctx)
        return ok, (f"paid={(misses_paid, ran_paid)} live={misses_live} "
                    f"labels={('[STALE' in paid_ctx, '[STALE' in live_ctx)}")

    if name == "older_siblings_counted":
        root = _repo(base, tree)
        _alert(root, "SCOPE COVERAGE: uv.lock — outside every approved Code Scope")
        _alert(root, "SCOPE COVERAGE: docs/old.md — outside every approved Code Scope")
        ctx = _context(root)
        ok = "[STALE" in ctx and "(+1 older)" in ctx
        return ok, f"clause={_clause(ctx)!r}"

    if name == "line_bounded":
        root = _repo(base, tree)
        _alert(root)
        clause = _clause(_context(root))
        ok = "[STALE" in clause and len(clause) <= 460 and "\n" not in clause
        return ok, f"len={len(clause)}"

    if name == "no_alert_no_line":
        root = _repo(base, tree)
        ctx = _context(root)
        return "SCOPE DEBT" not in ctx, f"context_has_line={'SCOPE DEBT' in ctx}"

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
    print(f"scope_debt_freshness_accuracy={value:.2f} ({passed}/{total})")
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

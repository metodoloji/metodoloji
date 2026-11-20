#!/usr/bin/env python3
"""git's own rename verb names its destination (E-185).

    python3 scripts/bench/bench_git_mv_target.py --samples docs/experiments/E-185/raw/samples.jsonl
    python3 scripts/bench/bench_git_mv_target.py --broken   # rule-7 control

The guard gates what `bash_targets` names: a command word that yields no target
is judged as a read. `mv a b` is gated by its destination operand, but the same
rename spelled with git's own porcelain, `git mv a b`, had no arm under `git`
at all — so it named nothing and was allowed while it created a gated file
(probe, 2026-11-20).

The bench is the closed set: six `git mv` spellings that must be denied, three
already-denied twins (the `mv` operand spelling, `cp`, and `git restore`), and
seven invocations that must stay allowed — a dry run, `--help`, a bare verb and
a one-operand usage error (none of which moves anything), a rename whose
destination is outside the code path, and a read. Nothing is sampled; every
probe drives the real `guard()`.

`--broken` runs the same probes against the pre-fix tree, so the control fails
for exactly the reason this change names. Stdlib + subprocess, offline,
deterministic.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
PREFIX_REV = "55c860846b823293d138f9754aa7d5f79f9f11c9"  # last commit before this surface
TARGET = "src/zzz_probe.py"  # a code path this repository gates
CLAIM = "git_mv_target_accuracy"
BROKEN = False

_PREFIX_CACHE: pathlib.Path | None = None


def _prefix_tree() -> pathlib.Path:
    global _PREFIX_CACHE
    if _PREFIX_CACHE is not None:
        return _PREFIX_CACHE
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="git-mv-prefix-"))
    out = subprocess.run(["git", "-C", str(ROOT), "archive", PREFIX_REV],
                         capture_output=True, stdin=subprocess.DEVNULL)
    if out.returncode != 0:
        raise RuntimeError(f"cannot archive {PREFIX_REV}: {out.stderr[:200]!r}")
    subprocess.run(["tar", "-x", "-C", str(tmp)], input=out.stdout, check=True)
    _PREFIX_CACHE = tmp
    return tmp


def _tree() -> pathlib.Path:
    return ROOT if not BROKEN else _prefix_tree()


# --- the closed set: command → the verdict guard() must reach -----------------
# `deny` is the only verdict a rename that writes a gated code path may get;
# `allow` is a twin that must not start denying because the surface grew.

PROBES: dict[str, tuple[str, str]] = {
    # renames whose destination is a gated code path
    "git_mv_into_code_path": ("git mv docs/x.md src/zzz_probe.py", "deny"),
    "git_mv_rename_in_code": ("git mv src/a.py src/zzz_probe.py", "deny"),
    "git_mv_force": ("git mv -f docs/x.md src/zzz_probe.py", "deny"),
    "git_mv_verbose": ("git mv -v src/a.py src/zzz_probe.py", "deny"),
    "git_mv_dir_dest": ("git mv docs/x.md src/", "deny"),
    "git_mv_separator": ("git mv -- docs/x.md src/zzz_probe.py", "deny"),
    # already denied — the fix must not lose them
    "mv_operand_dest": ("mv docs/x.md src/zzz_probe.py", "deny"),
    "cp_operand_dest": ("cp docs/x.md src/zzz_probe.py", "deny"),
    "git_restore": (f"git restore {TARGET}", "deny"),
    # non-writes: no rename happens, or the destination is outside the tree
    "git_mv_dry_run": ("git mv -n src/a.py src/b.py", "allow"),
    "git_mv_help": ("git mv --help", "allow"),
    "git_mv_bare": ("git mv", "allow"),
    "git_mv_one_operand": ("git mv src/a.py", "allow"),
    "git_mv_out_of_code": ("git mv docs/a.md docs/b.md", "allow"),
    "readonly_listing": ("ls src", "allow"),
}


def _driver(name: str, tree: pathlib.Path) -> tuple[bool, str]:
    sys.path.insert(0, str(tree / "hooks" / "engine"))
    from modules import guard  # noqa: WPS433 — the tree under test

    command, want = PROBES[name]
    # The project root is the same for both arms, so the arms differ only in
    # the code that decides whether a write was seen.
    decision = guard.guard({"tool_name": "Bash",
                            "tool_input": {"command": command},
                            "cwd": str(ROOT)})
    got = str(decision.get("decision") or "")
    reason = str(decision.get("reason") or decision.get("methodology_warnings") or "")
    return got == want, f"{command} -> {got or '?'} (want {want}) {reason[:80]}"


def _run_probe(name: str) -> tuple[bool, str]:
    proc = subprocess.run(
        [sys.executable, str(pathlib.Path(__file__).resolve()),
         "--driver", name, "--tree", str(_tree())],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180, stdin=subprocess.DEVNULL)
    try:
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return False, f"driver crashed: {proc.stderr.strip()[:200]}"
    return bool(payload["ok"]), str(payload["detail"])


def main() -> int:
    global BROKEN
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--broken", action="store_true", help="rule-7 control arm")
    ap.add_argument("--samples", help="write per-check JSONL samples here")
    ap.add_argument("--driver", help=argparse.SUPPRESS)
    ap.add_argument("--tree", help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.driver:
        ok, detail = _driver(args.driver, pathlib.Path(args.tree))
        print(json.dumps({"check": args.driver, "ok": ok, "detail": detail},
                         ensure_ascii=False))
        return 0

    BROKEN = args.broken
    results = []
    try:
        for name in PROBES:
            ok, detail = _run_probe(name)
            results.append({"check": name, "ok": ok, "detail": detail})
            print(f"[{'OK' if ok else 'MISS'}] {name}: {detail}")
    finally:
        if _PREFIX_CACHE is not None:
            shutil.rmtree(_PREFIX_CACHE, ignore_errors=True)

    x = sum(1 for r in results if r["ok"])
    n = len(results)
    if args.samples:
        path = pathlib.Path(args.samples)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for r in results:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{CLAIM}={x / n:.2f} ({x}/{n})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

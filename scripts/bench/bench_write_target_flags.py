#!/usr/bin/env python3
"""A destination carried by a flag is still a destination (E-184).

    python3 scripts/bench/bench_write_target_flags.py --samples docs/experiments/E-184/raw/samples.jsonl
    python3 scripts/bench/bench_write_target_flags.py --broken   # rule-7 control

The guard gates what `bash_targets` names: a command word that yields no target
is judged as a read. Two families of `cp`/`mv`/`install`/`ln` and `git checkout`
spellings name the gated path only in a FLAG or after a revision, so the
shipped extractor (probe, 2026-11-20) read them as reads and `guard()` allowed
them while the same intent spelled `cp X src/`, `git restore X` or
`git checkout -- X` is denied:

* `cp -t src zzz_probe.py` — the only operand is the SOURCE; the destination is
  the `-t` value (also `--target-directory[=]`, `-at`, `-tsrc`), and mv,
  install and ln take the same flag;
* `git checkout src/zzz_probe.py` and `git checkout HEAD src/zzz_probe.py` —
  separator-less checkout rewrites the working tree exactly like
  `git checkout --` / `git restore`, which are covered.

The bench is the closed set: nine flag-carrying writes that must be denied,
three already-denied twins (the operand spelling, the `--` spelling, and
`rsync -t` whose `-t` means "preserve times", not a target directory), and six
commands that must stay allowed — the branch switches `git checkout main`,
`git checkout -b`, `git checkout HEAD~2`, two out-of-tree destinations and a
listing. Nothing is sampled; every probe drives the real `guard()`.

`--broken` runs the same probes against the pre-fix tree, so the control fails
for exactly the reasons this change names. Stdlib + subprocess, offline,
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
CLAIM = "flag_target_accuracy"
BROKEN = False

_PREFIX_CACHE: pathlib.Path | None = None


def _prefix_tree() -> pathlib.Path:
    global _PREFIX_CACHE
    if _PREFIX_CACHE is not None:
        return _PREFIX_CACHE
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="write-target-prefix-"))
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
# `deny` is the only verdict a write to a gated code path may get; `allow` is
# the non-write twin that must not start denying because the surface grew.

PROBES: dict[str, tuple[str, str]] = {
    # writes whose destination rides a flag
    # The source lives OUTSIDE the tree: the old last-operand rule reported it
    # as the destination, and an out-of-tree path was dropped as a non-write,
    # so the copy into src/ was allowed. The new rule joins the flag's
    # directory with the source's basename and names the file actually written.
    "cp_target_dir": ("cp -t src /tmp/zzz_probe.py", "deny"),
    "cp_target_dir_long": ("cp --target-directory=src /tmp/zzz_probe.py", "deny"),
    "cp_target_dir_bundle": ("cp -at src /tmp/zzz_probe.py", "deny"),
    "cp_target_dir_attached": ("cp -tsrc /tmp/zzz_probe.py", "deny"),
    "mv_target_dir": ("mv -t src /tmp/zzz_probe.py", "deny"),
    "install_target_dir": ("install -t src /tmp/zzz_probe.py", "deny"),
    "ln_target_dir": ("ln -t src /tmp/zzz_probe.py", "deny"),
    # writes whose path follows a revision (no `--` separator)
    "git_checkout_path": (f"git checkout {TARGET}", "deny"),
    "git_checkout_rev_path": (f"git checkout HEAD {TARGET}", "deny"),
    # already denied — the fix must not lose them
    "cp_operand_dest": ("cp zzz_probe.py src/", "deny"),
    "git_checkout_separator": (f"git checkout -- {TARGET}", "deny"),
    "rsync_times_is_not_a_target": (f"rsync -t /tmp/x {TARGET}", "deny"),
    # non-writes: a branch switch, an out-of-tree destination, a read
    "git_checkout_branch": ("git checkout main", "allow"),
    "git_checkout_create_branch": ("git checkout -b probe-branch", "allow"),
    "git_checkout_revision": ("git checkout HEAD~2", "allow"),
    "cp_out_of_tree": ("cp zzz_probe.py /tmp/out/", "allow"),
    "cp_target_dir_out_of_tree": ("cp -t /tmp zzz_probe.py", "allow"),
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

#!/usr/bin/env python3
"""Every verb that writes a code path reaches the gate (E-183).

    python3 scripts/bench/bench_bash_write_surface.py --samples docs/experiments/E-183/raw/samples.jsonl
    python3 scripts/bench/bench_bash_write_surface.py --broken   # rule-7 control

The guard gates what `bash_targets` names: a command word that yields no target
is judged as a read. Probing the shipped extractor (2026-11-17) with seventeen
write forms aimed at one gated code path left three verbs naming nothing:

* `rsync -a /tmp/x src/zzz_probe.py` — a copy, destination LAST, like cp;
* `ln -sf /tmp/x src/zzz_probe.py` — creates or replaces the link name;
* `git restore src/zzz_probe.py` — rewrites the working tree exactly like
  `git checkout -- src/zzz_probe.py`, which IS covered — but only because that
  branch requires the `--` separator.

All three reached `guard()` as read-only commands and were allowed, while the
same intent spelled `cp`, `mv`, `install` or `git checkout --` is denied. The
probes below are the closed set: five writes that must be denied, five that
already were, and two that must stay allowed (an rsync whose gated path is only
the SOURCE, and an ln whose destination is outside the tree) so the fix cannot
be satisfied by denying more.

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
PREFIX_REV = "89db961"  # last commit before the write surface was completed
TARGET = "src/zzz_probe.py"  # a code path this repository gates
CLAIM = "write_surface_accuracy"
BROKEN = False

_PREFIX_CACHE: pathlib.Path | None = None


def _prefix_tree() -> pathlib.Path:
    global _PREFIX_CACHE
    if _PREFIX_CACHE is not None:
        return _PREFIX_CACHE
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="write-surface-prefix-"))
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
# the read-only twin that must not start denying because the surface grew.

PROBES: dict[str, tuple[str, str]] = {
    # writes the pre-fix surface did not name
    "rsync_dest": (f"rsync -a /tmp/x {TARGET}", "deny"),
    "rsync_prefixed": (f"sudo rsync -a /tmp/x {TARGET}", "deny"),
    "ln_dest": (f"ln -sf /tmp/x {TARGET}", "deny"),
    "git_restore": (f"git restore {TARGET}", "deny"),
    "git_restore_redirect": (f"git restore {TARGET} > /dev/null", "deny"),
    # writes that already denied — the fix must not lose them
    "cp_dest": (f"cp /tmp/x {TARGET}", "deny"),
    "mv_dest": (f"mv /tmp/x {TARGET}", "deny"),
    "touch_dest": (f"touch {TARGET}", "deny"),
    # non-writes: the gated path is read, or the destination is elsewhere
    "rsync_source_read": (f"rsync -a {TARGET} /tmp/out/", "allow"),
    "ln_outside": ("ln -sf /tmp/x /tmp/zzz_link", "allow"),
    "readonly_listing": ("ls src", "allow"),
    "git_status": ("git status", "allow"),
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

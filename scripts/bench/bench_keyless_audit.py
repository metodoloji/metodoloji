#!/usr/bin/env python3
"""The plugin audit passes on a fresh clone, where no gate key exists (E-164).

    python3 scripts/bench/bench_keyless_audit.py --samples docs/experiments/E-164/raw/samples.jsonl
    python3 scripts/bench/bench_keyless_audit.py --broken   # rule-7 control

Measured 2026-11-15 on this tree: `.github/workflows/quality.yml` runs
`sh scripts/check-plugin.sh` on every pull request, and that audit exited 1 on
any machine without a gate key. Two independent causes, both structural:

  1. §0 made a missing key a hard ERROR. The HMAC key lives OUTSIDE the repo
     (`~/.bmad/gate-key`), so no fresh clone and no CI runner can have one, and
     every approved record consequently reads UNVERIFIABLE — which the audit's
     own header already declares a WARNING class.
  2. §3b builds its approved-scope union from records that VERIFY under this
     machine's ring. With no key, no record verifies, the union is empty, and a
     genuinely-approved file (`conftest.py`, in E-146's scope) reads as "outside
     every approved Code Scope". The section reported the absence of an operator
     secret as a scope violation.

Both made the CI audit step unconditionally red for reasons no contributor
could fix from the tree. E-157 measured the same defect class on the tag bench:
an instrument that goes red on every run measures the environment, not the
claim.

Four probes, each a distinct property:

  1. a missing key is not a hard error by default (it is under --strict-keys)
  2. scope coverage reports itself unmeasured when no record can verify
  3. the audit exits 0 with no key present — the CI condition itself
  4. the audit still exits 0 with a key present, so the guard did not simply
     disable the section

`--broken` evaluates the pre-repair script (PREFIX_REV, which fails 1 and 2 and
therefore 3) and must score below the threshold. Stdlib, offline, deterministic.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
AUDIT_REL = "scripts/check-plugin.sh"
PREFIX_REV = "e18a677"  # the tree this record repairs
BROKEN = False

_PREFIX_CACHE: pathlib.Path | None = None


def _tree() -> pathlib.Path:
    global _PREFIX_CACHE
    if not BROKEN:
        return ROOT
    if _PREFIX_CACHE is not None:
        return _PREFIX_CACHE
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="keyless-audit-"))
    out = subprocess.run(["git", "-C", str(ROOT), "archive", PREFIX_REV],
                         capture_output=True, stdin=subprocess.DEVNULL)
    if out.returncode != 0:
        raise RuntimeError(f"cannot archive {PREFIX_REV}: {out.stderr[:200]!r}")
    subprocess.run(["tar", "-x", "-C", str(tmp)], input=out.stdout, check=True)
    _PREFIX_CACHE = tmp
    return tmp


def _home_without_keys() -> str:
    """A HOME with no `.bmad` — exactly what a CI runner has."""
    return tempfile.mkdtemp(prefix="keyless-home-")


def _run_audit(tree: pathlib.Path, home: str, *extra: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOME": home, "USERPROFILE": home}
    env.pop("BMAD_GATE_KEY", None)
    env.pop("BMAD_PEER_GATE_KEY", None)
    return subprocess.run(["sh", str(tree / AUDIT_REL), *extra], cwd=str(tree),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=900, stdin=subprocess.DEVNULL,
                          env=env)


def _case_missing_key_is_not_fatal_by_default() -> tuple[bool, str]:
    """A missing key warns; it does not count as a problem."""
    tree = _tree()
    proc = _run_audit(tree, _home_without_keys())
    out = (proc.stdout or "") + (proc.stderr or "")
    if "gate key MISSING" in out and "[ERROR] gate key MISSING" in out:
        return (False, "a missing gate key is still a hard ERROR by default")
    if "[WARN] gate key not installed" not in out:
        return (False, "the missing-key case is not reported at all — it is "
                       "silent, not warned")
    return (True, "a missing key warns and does not fail the audit")


def _case_scope_coverage_is_skipped_without_a_key() -> tuple[bool, str]:
    """§3b reports itself unmeasured when no record can verify."""
    tree = _tree()
    proc = _run_audit(tree, _home_without_keys())
    out = (proc.stdout or "") + (proc.stderr or "")
    if "SKIPPED: no gate key" not in out:
        return (False, "§3b did not announce itself unmeasured without a key")
    if "MISS:" in out:
        return (False, "§3b still reports MISS lines with no key — the empty "
                       "scope union is being read as a violation")
    return (True, "§3b reports itself unmeasured and names no MISS")


def _case_the_audit_passes_with_no_key() -> tuple[bool, str]:
    """The CI condition: a fresh clone with no key must exit 0."""
    tree = _tree()
    proc = _run_audit(tree, _home_without_keys())
    if proc.returncode != 0:
        tail = " / ".join((proc.stdout or "").strip().splitlines()[-2:])
        return (False, f"exit={proc.returncode} on a keyless machine: {tail}")
    return (True, "the audit exits 0 with no gate key")


def _case_the_audit_still_passes_with_a_key() -> tuple[bool, str]:
    """The guard did not disable §3b for machines that DO hold a key."""
    tree = _tree()
    proc = _run_audit(tree, os.environ.get("HOME", str(pathlib.Path.home())))
    out = (proc.stdout or "") + (proc.stderr or "")
    if "SKIPPED: no gate key" in out:
        return (False, "§3b skipped even though this machine holds a key")
    if proc.returncode != 0:
        tail = " / ".join((proc.stdout or "").strip().splitlines()[-2:])
        return (False, f"exit={proc.returncode} on the keyed machine: {tail}")
    return (True, "the audit exits 0 with a key and §3b still runs")


PROBES = (
    ("a missing key is not fatal by default", _case_missing_key_is_not_fatal_by_default),
    ("scope coverage skips without a key", _case_scope_coverage_is_skipped_without_a_key),
    ("the audit passes with no key", _case_the_audit_passes_with_no_key),
    ("the audit still passes with a key", _case_the_audit_still_passes_with_a_key),
)


def run() -> list[dict]:
    results = []
    for name, probe in PROBES:
        try:
            ok, detail = probe()
        except Exception as exc:  # a probe that cannot answer is a failure
            ok, detail = False, f"probe error: {type(exc).__name__}: {exc}"
        results.append({"check": name, "ok": bool(ok), "detail": detail})
    return results


def main() -> int:
    global BROKEN
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", default=None,
                    help="write one JSON object per line here (the gate recomputes x/n)")
    ap.add_argument("--broken", action="store_true",
                    help="rule-7 control: evaluate the pre-repair script")
    args = ap.parse_args()
    BROKEN = args.broken

    results = run()
    if args.samples:
        path = pathlib.Path(args.samples)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for item in results:
                fh.write(json.dumps(item, ensure_ascii=False) + "\n")

    ok = sum(1 for r in results if r["ok"])
    n = len(results)
    for r in results:
        print(f"  {'ok  ' if r['ok'] else 'FAIL'}  {r['check']}: {r['detail']}")
    print(f"keyless_audit_accuracy={ok / n:.2f} ({ok}/{n})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

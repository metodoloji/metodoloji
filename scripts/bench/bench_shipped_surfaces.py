#!/usr/bin/env python3
"""A shipped surface carries content, and the rule that checks it is not empty (E-161).

    python3 scripts/bench/bench_shipped_surfaces.py --samples docs/experiments/E-161/raw/samples.jsonl
    python3 scripts/bench/bench_shipped_surfaces.py --broken   # rule-7 control

Measured 2026-11-14 on this tree at e18a677: commit 2064fa9 deleted README.md,
LICENSE and SECURITY.md, and a later commit re-created all three as ZERO-BYTE
files. Four removal benches went red — each asserts that the reader-facing
surface declares the surviving skill count, which an empty file cannot — and
nobody saw them, because no workflow runs a bench.

The deeper defect is how the red was silenced. E-152's commit emptied
`bench_consistency`'s four `_AUDIT_*` tuples to match the emptied README, so the
audit-inventory probes iterated over nothing and passed vacuously: the one
instrument that reads README for a living was told to read no documents. A check
that names no document cannot disagree with a missing one. That is E-159's dead
reference one level up — not a surface naming nothing, but a RULE measuring
nothing, which is strictly worse because it reports success.

Two properties, and both are cheap to lose silently:

  1. every shipped reader-facing file is non-empty — README, LICENSE, SECURITY,
     and every docs/*.md a reader is pointed at; and the manifests that declare
     `"license": MIT` ship a LICENSE that actually says MIT
  2. no shipped check is vacuously satisfiable — every `_AUDIT_*` tuple in
     bench_consistency names at least one document, and every document it names
     ships

`--broken` evaluates the pre-repair tree (PREFIX_REV, where the three files are
empty and the tuples are empty too) and must score below the threshold.

Stdlib, offline, deterministic.
"""

from __future__ import annotations

import argparse
import ast
import json
import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONSISTENCY = ROOT / "scripts" / "bench" / "bench_consistency.py"
PREFIX_REV = "e18a677"  # the tree this record repairs
BROKEN = False

# Reader-facing files that ship with the plugin and must say something.
REQUIRED_NONEMPTY = ("README.md", "LICENSE", "SECURITY.md")
MANIFESTS = (".claude-plugin/plugin.json", ".claude-plugin/marketplace.json",
             ".plugin/plugin.json", ".plugin/marketplace.json",
             ".opencode-plugin/plugin.json", "plugin.yaml", "pyproject.toml")

_PREFIX_CACHE: pathlib.Path | None = None


def _tree() -> pathlib.Path:
    global _PREFIX_CACHE
    if not BROKEN:
        return ROOT
    if _PREFIX_CACHE is not None:
        return _PREFIX_CACHE
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="shipped-surfaces-"))
    out = subprocess.run(["git", "-C", str(ROOT), "archive", PREFIX_REV],
                         capture_output=True, stdin=subprocess.DEVNULL)
    if out.returncode != 0:
        raise RuntimeError(f"cannot archive {PREFIX_REV}: {out.stderr[:200]!r}")
    subprocess.run(["tar", "-x", "-C", str(tmp)], input=out.stdout, check=True)
    _PREFIX_CACHE = tmp
    return tmp


def _case_reader_facing_files_are_nonempty() -> tuple[bool, str]:
    """Every required reader-facing file ships content, not a zero-byte stub."""
    root = _tree()
    empty = [rel for rel in REQUIRED_NONEMPTY
             if not (root / rel).is_file() or (root / rel).stat().st_size == 0]
    if empty:
        return (False, f"zero-byte or missing: {empty}")
    return (True, f"{len(REQUIRED_NONEMPTY)} reader-facing file(s) carry content")


def _case_license_claim_matches_the_file() -> tuple[bool, str]:
    """A manifest that declares a license ships a LICENSE saying the same thing."""
    root = _tree()
    claims = []
    for rel in MANIFESTS:
        path = root / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r'(?i)(?:"license"\s*:\s*"|^license:\s*)([A-Za-z0-9.\-]+)',
                             text, re.MULTILINE):
            claims.append((rel, m.group(1)))
    if not claims:
        return (False, "no manifest declares a license — the claim is unstated")
    lic = root / "LICENSE"
    body = lic.read_text(encoding="utf-8", errors="replace") if lic.is_file() else ""
    bad = []
    for rel, name in claims:
        # MIT declares itself in its own first line; any SPDX-style name is a
        # substring test, which is enough to catch an empty or wrong file.
        if name.upper() not in body.upper():
            bad.append(f"{rel} declares {name!r}, LICENSE does not say it")
    if bad:
        return (False, "; ".join(bad[:3]))
    return (True, f"{len(claims)} license claim(s) agree with LICENSE")


def _audit_tuples(tree: ast.Module) -> list[tuple[str, list[str]]]:
    """Every module-level `_AUDIT_*` tuple assignment and its string members."""
    out = []
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or not target.id.startswith("_AUDIT_"):
            continue
        if not isinstance(node.value, (ast.Tuple, ast.List)):
            continue
        members = []
        for elt in node.value.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                members.append(elt.value)
            elif isinstance(elt, (ast.Tuple, ast.List)) and elt.elts \
                    and isinstance(elt.elts[0], ast.Constant):
                members.append(elt.elts[0].value)
        out.append((target.id, members))
    return out


def _case_no_vacuous_check() -> tuple[bool, str]:
    """Every `_AUDIT_*` tuple names a document, and every document ships.

    The E-152 defect in one assertion: the four tuples were set to `()` so the
    probes iterated over nothing. A check whose population is empty reports a
    pass it never measured.
    """
    root = _tree()
    path = root / "scripts" / "bench" / "bench_consistency.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    tuples = _audit_tuples(tree)
    if not tuples:
        return (False, "no _AUDIT_* tuple found — the probe's own population is empty")
    vacuous = [name for name, members in tuples if not members]
    if vacuous:
        return (False, f"empty population (checks nothing): {vacuous}")
    missing = []
    for name, members in tuples:
        for rel in members:
            if not (root / rel).is_file():
                missing.append(f"{name} names {rel}, which does not ship")
    if missing:
        return (False, "; ".join(missing[:4]))
    return (True, f"{len(tuples)} audit tuple(s) name {sum(len(m) for _, m in tuples)} "
                  f"document(s), all shipped")


def _case_the_live_tree_passes_every_probe() -> tuple[bool, str]:
    """The same two probes, always against the live tree (pins this record)."""
    global BROKEN
    was = BROKEN
    BROKEN = False
    try:
        ok1, d1 = _case_reader_facing_files_are_nonempty()
        ok2, d2 = _case_no_vacuous_check()
    finally:
        BROKEN = was
    return (ok1 and ok2, f"{d1}; {d2}" if ok1 and ok2 else f"{d1}; {d2}")


PROBES = (
    ("reader-facing files carry content", _case_reader_facing_files_are_nonempty),
    ("a license claim matches LICENSE", _case_license_claim_matches_the_file),
    ("no shipped check is vacuously satisfiable", _case_no_vacuous_check),
    ("the live tree passes every probe", _case_the_live_tree_passes_every_probe),
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
                    help="rule-7 control: evaluate the pre-repair tree")
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
    print(f"shipped_surface_accuracy={ok / n:.2f} ({ok}/{n})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""The freshness row reports the records its cap did not read (E-205).

`_freshness_chain_row` keeps the commit path cheap by reading at most
`_FRESHNESS_ROW_MAX_RECORDS` record files. It then reported as if the corpus
ended there: an approval past the cap was neither CURRENT nor STALE, just
unvisited, and the row said nothing. Observed live on 2026-11-22 on a scratch
project with 600 APPROVED records — the warn row and the deny row both read
500 and presented the result as complete.

A scan is only as wide as its bound (E-002 made the same point about module
lists). The cap stays; what changes is that a truncated scan says how many
record files it did not read, on the warn branch, the deny branch, and even
when it finds nothing stale.

Claim, closed set: the warn row names the unread count, the deny reason names
it too, a corpus under the cap carries no scan note, the stated numbers match
the corpus and the cap, the decisions themselves are unchanged, and the cap
value in the message is the constant the code uses.

Control: `--broken` runs the same probes against `PREFIX_REV`, which truncates
without a word.
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
PREFIX_REV = "f7cf51d"  # HEAD before E-205: e_records[:cap] silent


def _root(base: pathlib.Path, records: int) -> pathlib.Path:
    root = base / "proj"
    exp = root / "docs" / "experiments"
    exp.mkdir(parents=True)
    for i in range(records):
        (exp / f"E-{i:04d}.md").write_text(
            f"## Experiment: E-{i:04d} — fixture\n"
            f"- **Decision:** APPROVED — fixture\n", encoding="utf-8")
    return root


def _row(tree: pathlib.Path, root: pathlib.Path, stale: bool,
         deny: bool) -> dict:
    sys.path.insert(0, str(tree / "hooks" / "engine"))
    from modules import freshness as fr
    from modules import guard

    verdict = fr.STALE if stale else fr.CURRENT
    guard._record_freshness = lambda r, rec: {
        "verdict": verdict, "reason": "moved", "changed": [], "age_s": 1.0,
        "commit": "deadbeef", "record": pathlib.Path(rec).name}
    fr.blocks = (lambda v: str(v).upper() == fr.STALE) if deny else (lambda v: False)
    return guard._freshness_chain_row(str(root), "git commit")


def _text(row: dict) -> str:
    if row.get("decision") == "deny":
        return row.get("reason") or ""
    return " ".join(row.get("methodology_warnings") or [])


def _probe(name: str, tree: pathlib.Path) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        records = 100 if name == "under_cap_silent" else 600
        stale = name != "cap_note_in_empty_scan"
        row = _row(tree, _root(base, records), stale=stale, deny=name == "cap_note_in_deny")
        text = _text(row)
        hidden = records - 500

        if name == "under_cap_silent":
            ok = "FRESHNESS SCAN" not in text
            return ok, f"warnings={len(row.get('methodology_warnings') or [])}"
        if name == "cap_note_in_warn":
            note = next((w for w in row.get("methodology_warnings") or []
                         if w.startswith("FRESHNESS SCAN")), "")
            ok = f"read 500 of {records}" in note and "not measured" in note
            return ok, f"note={note[:90]!r}"
        if name == "cap_note_in_deny":
            ok = row.get("decision") == "deny" and f"read 500 of {records}" in text
            return ok, f"decision={row.get('decision')}"
        if name == "cap_note_in_empty_scan":
            note = next((w for w in row.get("methodology_warnings") or []
                         if w.startswith("FRESHNESS SCAN")), "")
            ok = row.get("decision") == "allow" and f"the other {hidden}" in note
            return ok, f"note={note[:90]!r}"
        if name == "count_matches":
            ok = f"the other {hidden} were not measured" in text
            return ok, f"hidden={hidden}"
        if name == "cap_value_stated":
            ok = "cap 500" in text
            return ok, f"text_has_cap={'cap 500' in text}"
        if name == "decisions_unchanged":
            warn = _row(tree, _root(base / "w", 600), stale=True, deny=False)
            deny = _row(tree, _root(base / "d", 600), stale=True, deny=True)
            ok = warn.get("decision") == "allow" and deny.get("decision") == "deny"
            return ok, f"warn={warn.get('decision')} deny={deny.get('decision')}"
    raise SystemExit(f"unknown probe: {name}")


PROBES = (
    "cap_note_in_warn",
    "cap_note_in_deny",
    "cap_note_in_empty_scan",
    "under_cap_silent",
    "count_matches",
    "cap_value_stated",
    "decisions_unchanged",
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
    print(f"freshness_scan_bound_accuracy={value:.2f} ({passed}/{total})")
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

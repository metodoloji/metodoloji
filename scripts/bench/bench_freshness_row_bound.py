#!/usr/bin/env python3
"""The freshness row counts the approvals its list withholds (E-204).

The commit/deploy freshness row names the approvals that no longer cover the
code. It stated the count ("5 approval(s) the code moved past") and then joined
`stale[:3]` with no marker — the same for the deny branch and the warn branch.
Observed live on 2026-11-22 on a scratch project holding five APPROVED records
the code moved past: the denial named E-900, E-901, E-902 and stopped, so a
reader who retired exactly those three was denied again by the two the line
never mentioned, and nothing told them the list was partial.

The bound stays at three (a denial is read on every commit); what changes is
that the three now end in `… (+N more approvals)`, through the repo's
`counted_list` — the join E-196/197 put behind every other bounded list.

Claim, closed set: the warn row and the deny row both count their remainder,
the count in the sentence still matches the records visited, the row names
exactly the first three and no later one, a two-record corpus is named whole
with no marker, the warn row still decides `allow`, and named plus hidden
equals the stated count.

Control: `--broken` runs the same probes against `PREFIX_REV`, whose two
branches join three silently.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
import tarfile
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
PREFIX_REV = "f7cf51d"  # HEAD before E-204: stale[:3] / (stale + unknown)[:3]


def _root(base: pathlib.Path, records: int) -> pathlib.Path:
    root = base / "proj"
    exp = root / "docs" / "experiments"
    exp.mkdir(parents=True)
    for i in range(records):
        (exp / f"E-90{i}.md").write_text(
            f"## Experiment: E-90{i} — fixture\n"
            f"- **Decision:** APPROVED — fixture\n", encoding="utf-8")
    return root


def _row(tree: pathlib.Path, root: pathlib.Path, deny: bool) -> dict:
    sys.path.insert(0, str(tree / "hooks" / "engine"))
    from modules import freshness as fr
    from modules import guard

    guard._record_freshness = lambda r, rec: {
        "verdict": fr.STALE, "reason": "moved", "changed": [], "age_s": 1.0,
        "commit": "deadbeef", "record": pathlib.Path(rec).name}
    fr.blocks = (lambda v: str(v).upper() == fr.STALE) if deny else (lambda v: False)
    return guard._freshness_chain_row(str(root), "git commit")


def _text(row: dict) -> str:
    if row.get("decision") == "deny":
        return row.get("reason") or ""
    return " ".join(row.get("methodology_warnings") or [])


def _named(text: str) -> list[str]:
    return re.findall(r"E-9\d\d", text)


def _probe(name: str, tree: pathlib.Path) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        deny = name.startswith("deny")
        records = 2 if name == "two_records_unmarked" else 5
        row = _row(tree, _root(base, records), deny)
        text = _text(row)

        if name == "two_records_unmarked":
            ok = "…" not in text and "more approval" not in text and len(_named(text)) == 2
            return ok, f"text={text[-60:]!r}"
        if name in ("warn_counts_remainder", "deny_counts_remainder"):
            ok = "… (+2 more approvals)" in text
            return ok, f"tail={text[-90:]!r}"
        if name in ("deny_names_three", "warn_names_three"):
            named = _named(text)
            ok = named[:3] == ["E-900", "E-901", "E-902"] and len(named) == 3
            return ok, f"named={named}"
        if name == "counts_stated":
            ok = "5 approval(s) the code moved past" in text
            return ok, f"tail={text[:60]!r}"
        if name == "warn_stays_allow":
            ok = row.get("decision") == "allow" and "report, not a block" in text
            return ok, f"decision={row.get('decision')}"
        if name == "arithmetic_matches":
            named = len(_named(text))
            hidden = 2 if "+2 more approvals" in text else 0
            ok = (named == 3 and hidden == 2
                  and f"{named + hidden} approval(s) the code moved past" in text)
            return ok, f"named={named} hidden={hidden}"
    raise SystemExit(f"unknown probe: {name}")


PROBES = (
    "warn_counts_remainder",
    "deny_counts_remainder",
    "deny_names_three",
    "warn_names_three",
    "two_records_unmarked",
    "counts_stated",
    "warn_stays_allow",
    "arithmetic_matches",
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
    print(f"freshness_row_bound_accuracy={value:.2f} ({passed}/{total})")
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

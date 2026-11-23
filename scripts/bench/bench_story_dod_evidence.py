#!/usr/bin/env python3
"""Every done story's DoD item carries its canonical Evidence line (E-229/DW-7).

DW-7 recorded that DoD-001…004 in S-011/S-012/S-013 omit the `- Evidence:` field
the canonical story template requires (`_template_S.md`: each DoD item has a
`Verify:` and an `Evidence:` line). The deferral was scoped to the three stories
the SP-014 review happened to read; the same rule is broken in S-014…S-016,
which no review pass inspected.

The deferral named the three stories SP-014 reviewed; scanning the same rule
found S-014…S-016 also missing it. The census is therefore every done story
whose QR review carries the canonical `| DoD Item | Status | Evidence | Date |`
table — the table is the source the story must mirror. The earlier QR-017…025
reviews predate that table, so those stories are outside the census rather than
silently exempt.

Claim, closed set: every DoD item of every censused story has both an indented
`- Verify:` and an indented `- Evidence:` line. The bench parses the shipped
records; nothing is sampled.

Control: `--broken` runs the same probes against `PREFIX_REV`, the tree before
this change, where six of the seven stories lack the evidence lines (1/7).
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
PREFIX_REV = "2497e68"  # HEAD before E-229: 24 DoD items without Evidence
STORIES = pathlib.Path("docs/development/stories")
QUALITY = pathlib.Path("docs/quality")
QR_TABLE = "| DoD Item | Status | Evidence | Date |"

DOD_RE = re.compile(r"(?m)^-\s+(?:\[[ x]\]\s+)?\[(DoD-\d+)\]")
STATUS_RE = re.compile(r"(?m)^- \*\*Status:\*\*\s*(\S+)")
VERIFY_RE = re.compile(r"(?m)^\s+- Verify:")
EVIDENCE_RE = re.compile(r"(?m)^\s+- Evidence:")


def _qr_table(tree: pathlib.Path, text: str) -> str | None:
    """The latest QR review of this story that carries a DoD evidence table."""
    refs = sorted({int(ref) for ref in re.findall(r"QR-(\d+)", text)})
    for number in reversed(refs):
        path = tree / QUALITY / f"QR-{number:03d}.md"
        if path.is_file() and QR_TABLE in path.read_text(encoding="utf-8"):
            return path.name
    return None


def _story_probe(path: pathlib.Path, tree: pathlib.Path) -> tuple[bool, bool, str]:
    """Return (in_census, ok, detail)."""
    text = path.read_text(encoding="utf-8")
    status = STATUS_RE.search(text)
    if status is None or status.group(1) != "done":
        return False, True, f"status={status.group(1) if status else 'unknown'} (not done)"
    qr = _qr_table(tree, text)
    if qr is None:
        return False, True, "no QR DoD evidence table (outside the census)"
    starts = [match.start() for match in DOD_RE.finditer(text)]
    missing = []
    for index, start in enumerate(starts):
        if index + 1 < len(starts):
            end = starts[index + 1]
        else:
            # The last DoD block ends at the section rule, not at EOF: an
            # Evidence line in a later section must not satisfy this item.
            rule = text.find("\n---", start)
            end = rule if rule != -1 else len(text)
        block = text[start:end]
        dod = DOD_RE.match(text, start).group(1)
        if not VERIFY_RE.search(block):
            missing.append(f"{dod}:Verify")
        if not EVIDENCE_RE.search(block):
            missing.append(f"{dod}:Evidence")
    if not starts:
        return True, False, f"{qr}: no DoD items"
    if missing:
        return True, False, f"{qr}: missing " + ", ".join(missing)
    return True, True, f"{qr}: {len(starts)} DoD items complete"


def _run(tree: pathlib.Path, samples: pathlib.Path | None, broken: bool) -> bool:
    paths = sorted((tree / STORIES).glob("S-*.md"))
    rows, passed = [], 0
    for path in paths:
        census, ok, detail = _story_probe(path, tree)
        if not census:
            print(f"  SKIP  {path.stem}  — {detail}")
            continue
        passed += 1 if ok else 0
        rows.append({"probe": path.stem, "ok": bool(ok), "detail": detail})
        print(f"  {'PASS' if ok else 'FAIL'}  {path.stem}  — {detail}")
    total = len(rows)
    value = passed / total if total else 0.0
    print(f"story_dod_evidence_accuracy={value:.2f} ({passed}/{total})")
    if samples is not None:
        samples.parent.mkdir(parents=True, exist_ok=True)
        with samples.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps({"broken": broken, **row}, ensure_ascii=False) + "\n")
    return total > 0 and passed == total


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=pathlib.Path, default=None)
    parser.add_argument("--broken", action="store_true")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        tree = _prefix_tree(pathlib.Path(tmp)) if args.broken else ROOT
        print(f"tree: {tree}")
        ok = _run(tree, args.samples, args.broken)
    return 0 if (ok or args.broken) else 1


if __name__ == "__main__":
    raise SystemExit(main())

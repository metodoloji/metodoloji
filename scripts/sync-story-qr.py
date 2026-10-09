#!/usr/bin/env python3
"""Sync embedded QR tables in S records from their QR-*.md records (SP-020).

Structural gap it closes: the record chain flows forward (S → QR file) but
nothing ever propagated QR approval BACK into the S file's embedded
"## Quality Record (QR)" table — 24 of 26 S files read Status: done atop an
all-pending table, and no gate alarmed because nothing mechanical reads the
embedded copy. The closing discipline from here on: QR APPROVED →
`python3 scripts/sync-story-qr.py --apply`; done S + APPROVED QR implies no
pending rows (pinned by bench_sp020.py).

Mapping is robust, not positional: each S file names its QR file in its own
`QR Record Path` row, and when that name resolves to nothing the QR record that
declares the story (`| Story |`) wins, so a stale prediction is repaired rather
than silently ignored. A declared path that resolves to nothing at all is
reported as DANGLING (and makes `--check` fail). Only files whose embedded
table still holds a pending row AND whose S status is done AND whose QR status
is APPROVED are rewritten, plus path-only repairs — everything else (already
filled tables, any non-done story) is left byte-identical.

Usage:
  python3 scripts/sync-story-qr.py --dry-run   # report only (bench uses this)
  python3 scripts/sync-story-qr.py --apply     # rewrite embedded tables + paths
  python3 scripts/sync-story-qr.py --check     # exit 1 if any sync/repair/dangling is pending
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

QR_SECTION_RE = re.compile(
    r"##\s+Quality\s+Record\s*\(QR\).*?(?=\n##\s|\Z)",
    re.DOTALL | re.IGNORECASE)
QR_PATH_RE = re.compile(
    r"QR Record Path\*\*:\s*(docs/quality/QR-\d+\.md)")
QR_STORY_RE = re.compile(r"^\|\s*Story\s*\|\s*(.+?)\s*\|", re.MULTILINE)
NATIVE_STORY_RE = re.compile(r"^\|\s*Native Story\s*\|\s*(.+?)\s*\|",
                             re.MULTILINE)
S_STATUS_RE = re.compile(r"^\|\s*Status\s*\|\s*(.+?)\s*\|", re.MULTILINE)
QR_STATUS_RE = re.compile(r"^\s*-\s*\*\*Status:\*\*\s*(.+?)\s*$", re.MULTILINE)


def _read_text(path: Path) -> str | None:
    """File text, or None when it is missing or not valid UTF-8.

    A record written by another tool (or damaged) can be non-UTF-8; reading it
    must never crash the linter (E-016: the E-009 decode seam on the record
    tooling). Callers treat None as CORRUPT and REPORT it — never guess — so a
    broken record surfaces instead of silently reading as "nothing to sync".
    """
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _qr_table_rows(text: str) -> list[dict]:
    """Parse a | DoD Item | Status | Evidence | Date | table into rows."""
    rows = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4 or not re.match(r"DoD-\d+", cells[0]):
            continue
        status = cells[1]
        if "✅" in status:
            state = "passed"
        elif "❌" in status:
            state = "failed"
        else:
            state = "pending"
        rows.append({"id": re.search(r"DoD-\d+", cells[0]).group(0),
                     "label": cells[0], "status": state,
                     "evidence": cells[2], "raw_status": status})
    return rows


def _embedded_qr_section(s_text: str) -> str:
    m = QR_SECTION_RE.search(s_text)
    return m.group(0) if m else ""


def qr_story_owner(qr_text: str) -> str:
    """The story file name a QR record declares (`| Story | <path or name> |`)."""
    m = QR_STORY_RE.search(qr_text)
    if not m or m.group(1) in ("", "—"):
        return ""
    return Path(m.group(1).strip()).name


def qr_index(project_root: Path) -> dict[str, str]:
    """story file name -> QR path it owns, read from the QR records themselves.

    The declaration inside the S record is a prediction (written before the QR
    existed); the QR record's own `Story` field is the fact. This index is what
    lets a stale or dangling prediction be resolved instead of silently
    ignored — a missing file used to make `qr_status` empty, which read as "no
    sync needed" rather than "this record names nothing".
    """
    index: dict[str, str] = {}
    for qr_file in sorted((project_root / "docs" / "quality").glob("QR-*.md")):
        if qr_file.name.startswith("_"):
            continue
        text = _read_text(qr_file)
        if text is None:          # missing OR non-UTF-8 → no owner to index
            continue
        owner = qr_story_owner(text)
        if owner:
            index.setdefault(owner, qr_file.relative_to(project_root).as_posix())
    return index


def compute_sync_rows(project_root: Path = ROOT) -> list[dict]:
    """One row per S file. Pure (no writes).

    `qr` is always the path of a file that EXISTS when one can be resolved: the
    declared row first, else the QR record that declares this story, else the
    positional guess. `declared` keeps what the record says, `repair` is the
    path the record should say, and `dangling` is a declared path that resolves
    to nothing at all.
    """
    out = []
    index = qr_index(project_root)
    stories = sorted((project_root / "docs" / "development" / "stories"
                      ).glob("S-*.md"))
    for s_file in stories:
        if s_file.name.startswith("_"):
            continue
        s_text = _read_text(s_file)
        if s_text is None:
            # A non-UTF-8 S record is corrupt: report it, do not guess.
            out.append({"story": s_file.name, "qr": "", "declared": "",
                        "repair": "", "dangling": "", "s_status": "",
                        "qr_status": "", "pending": 0, "qr_rows": 0,
                        "needs_sync": False, "unreadable": True})
            continue
        m = S_STATUS_RE.search(s_text)
        s_status = m.group(1).strip().lower() if m else ""
        section = _embedded_qr_section(s_text)
        s_rows = _qr_table_rows(section)
        pending = [r for r in s_rows if r["status"] == "pending"]
        m = QR_PATH_RE.search(s_text)
        declared = m.group(1) if m else ""
        guess = f"docs/quality/QR-{s_file.stem.split('-')[1]}.md"
        qr_rel = declared or guess
        repair = ""
        dangling = ""
        if not (project_root / qr_rel).is_file():
            owner = index.get(s_file.name, "")
            if not owner:
                # A methodology record owns its QR through the native story it
                # names (`| Native Story |`), and the QR record declares that
                # native story — not the record's own file name. Without this
                # hop the real case (native S-056 → S-057 → QR-014) could not be
                # resolved at all.
                nm = NATIVE_STORY_RE.search(s_text)
                if nm and nm.group(1) not in ("", "—"):
                    owner = index.get(Path(nm.group(1).strip()).name, "")
            if owner:
                qr_rel, repair = owner, owner
            else:
                dangling = declared or guess
        qr_file = project_root / qr_rel
        qr_status, qr_rows = "", []
        if qr_file.is_file():
            qr_text = _read_text(qr_file)
            if qr_text is not None:
                qm = QR_STATUS_RE.search(qr_text)
                qr_status = qm.group(1).strip() if qm else ""
                qr_rows = _qr_table_rows(qr_text)
        out.append({"story": s_file.name, "qr": qr_rel, "declared": declared,
                    "repair": repair, "dangling": dangling,
                    "s_status": s_status, "qr_status": qr_status,
                    "pending": len(pending), "qr_rows": len(qr_rows),
                    "needs_sync": bool(pending) and s_status == "done"
                    and qr_status == "APPROVED" and bool(qr_rows),
                    "unreadable": False})
    return out


def build_section(qr_rel: str, qr_rows: list[dict], date_fallback: str) -> str:
    lines = []
    passed = failed = 0
    for r in qr_rows:
        if r["status"] == "passed":
            icon, passed = "✅ passed", passed + 1
        elif r["status"] == "failed":
            icon, failed = "❌ failed", failed + 1
        else:
            icon = "⏳ pending"
        date = r.get("date", "") or date_fallback
        lines.append(f"| {r['label']} | {icon} "
                     f"| {r.get('evidence') or '—'} | {date} |")
    table = "\n".join(lines)
    total = len(qr_rows)
    return f"""## Quality Record (QR)

| DoD Item | Status | Evidence | Date |
|----------|--------|----------|------|
{table}
### QR Summary
- **Total DoD Items**: {total}
- **Passed**: {passed}
- **Failed**: {failed}
- **Pending**: {total - passed - failed}
- **QR Record Path**: {qr_rel}
"""


def _qr_rows_with_dates(qr_text: str) -> list[dict]:
    rows = _qr_table_rows(qr_text)
    for line in qr_text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 4 and re.match(r"DoD-\d+", cells[0]):
            for r in rows:
                if r["id"] in cells[0] and not r.get("date"):
                    r["date"] = cells[3] if cells[3] != "—" else ""
    return rows


def apply_sync(project_root: Path = ROOT, dry_run: bool = True) -> list[str]:
    """Rewrite embedded tables and stale QR paths where needed.

    Two kinds of row are touched: a row whose embedded table still holds a
    pending item (table sync), and a row whose `QR Record Path` resolves to a
    QR file that exists but is not the one the record names (path repair).
    Everything else stays byte-identical.
    """
    from datetime import date
    changed = []
    for row in compute_sync_rows(project_root):
        if not row["needs_sync"] and not row["repair"]:
            continue
        s_file = (project_root / "docs" / "development" / "stories"
                  / row["story"])
        s_text = s_file.read_text(encoding="utf-8")
        if row["needs_sync"]:
            qr_file = project_root / row["qr"]
            qr_text = qr_file.read_text(encoding="utf-8")
            section = build_section(row["qr"], _qr_rows_with_dates(qr_text),
                                    date.today().isoformat())
            new_text = QR_SECTION_RE.sub(lambda _m: section.strip(), s_text,
                                         count=1)
        else:
            # Path-only repair. The lambda keeps the path out of the template
            # parser (a string replacement would read a backslash escape).
            new_text = QR_PATH_RE.sub(
                lambda _m: "QR Record Path**: " + row["repair"], s_text)
        if new_text != s_text and not dry_run:
            s_file.write_text(new_text, encoding="utf-8")
            changed.append(row["story"])
        elif new_text != s_text:
            changed.append(row["story"] + " (dry-run)")
    return changed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--project-root", default=".")
    args = ap.parse_args()
    root = Path(args.project_root).resolve()
    rows = compute_sync_rows(root)
    needy = [r for r in rows if r["needs_sync"]]
    repairs = [r for r in rows if r["repair"] and not r["needs_sync"]]
    dangling = [r for r in rows if r["dangling"]]
    unreadable = [r for r in rows if r.get("unreadable")]
    print(f"stories scanned: {len(rows)}, needing sync: {len(needy)}")
    for r in needy:
        print(f"  {r['story']} <- {r['qr']} "
              f"({r['pending']} pending rows, QR {r['qr_status']})")
    for r in repairs:
        print(f"  PATH REPAIR: {r['story']} declares {r['declared'] or '(none)'} "
              f"-> {r['qr']}")
    for r in dangling:
        print(f"  DANGLING: {r['story']} names {r['dangling']}, which does not "
              f"exist and no QR record declares this story")
    for r in unreadable:
        print(f"  UNREADABLE: {r['story']} is not valid UTF-8 (corrupt record)")
    if args.apply:
        changed = apply_sync(root, dry_run=False)
        print(f"rewrote {len(changed)} file(s)")
    if args.check:
        return 1 if (needy or repairs or dangling or unreadable) else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())

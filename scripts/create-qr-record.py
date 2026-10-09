#!/usr/bin/env python3
"""Create Quality Record (QR-XXX.md) from story file DoD validation.

This script implements bridge #3 from the bridge document.
It reads a story file and creates a QR record.

Usage:
    python3 scripts/create-qr-record.py --story <story-file> [--sira <number>]

The script:
1. Reads the native story file
2. Extracts DoD items and their verification status
3. Creates docs/quality/QR-<sira>.md
4. Updates story file Quality Record section
"""

import argparse
import os
import re
import sys
from datetime import date
from pathlib import Path


# A DoD bullet appears in TWO canonical forms and both must count, because the
# engine's shared parser (hooks/engine/modules/utils.py:scan_dod_items — used
# by guard AND audit) recognises both:
#   checkbox (dev-story style)  `- [ ] DoD-001: …` / `- [x] DoD-001: …`
#   token    (record template)  `- [DoD-001] …`
# A QR generator that only knew the checkbox form produced an EMPTY DoD table
# for every story written from the shipped story template, which uses the token
# form — a QR record that silently drops the items it is supposed to evidence.
# scripts/tests/test_record_generators.py pins this contract for both
# generators against the engine's scanner.
_DOD_ID_RE = re.compile(r"[\[(]?DoD-(\d+)[\])]?")
# Tolerant of leading whitespace so the helpers stay correct for any caller
# (the section parsers hand them top-level lines, but a stray indented line
# must not leak the box into the description).
_CHECKBOX_RE = re.compile(r"^\s*-\s+\[([ xX])\]\s*")


def _dod_item_start(line: str) -> tuple[str, bool] | None:
    """(id, checked) when *line* opens a DoD item, else None (both forms)."""
    body = re.sub(r"^-\s+", "", line, count=1)
    checked = False
    box = _CHECKBOX_RE.match(line)
    if box:
        checked = box.group(1).lower() == "x"
        body = _CHECKBOX_RE.sub("", line, count=1)
    id_match = _DOD_ID_RE.search(body)
    if not id_match:
        return None
    return f"DoD-{id_match.group(1)}", checked


def _dod_description(line: str) -> str:
    """The item's text with the bullet and the identifier marker removed.

    The checkbox is stripped FIRST (its pattern includes the leading bullet);
    stripping the bare bullet first leaves "[x] …" and the ^-anchored box
    pattern can no longer match, so the box leaks into the description."""
    body = _CHECKBOX_RE.sub("", line, count=1)
    body = re.sub(r"^\s*-\s+", "", body, count=1)
    body = re.sub(r"[\[(]?DoD-\d+[\])]?\s*:?\s*", "", body, count=1)
    return body.strip()


def extract_dod_items(content: str) -> list[dict]:
    """Extract DoD items and their status from story file."""
    items = []

    dod_section = re.search(
        r"##\s+Definition\s+of\s+Done\s*\n(.*?)(?=\n##\s|\Z)",
        content,
        re.DOTALL | re.IGNORECASE,
    )
    if not dod_section:
        return items

    lines = dod_section.group(1).splitlines()
    current_item = None

    for line in lines:
        stripped = line.strip()

        # Top-level DoD item — checkbox or record-template token form
        start = None if line[:1].isspace() else _dod_item_start(stripped)
        if start:
            if current_item:
                items.append(current_item)

            dod_id, is_checked = start
            if not _DOD_ID_RE.search(stripped):
                dod_id = f"DoD-{len(items)+1}"

            desc = _dod_description(stripped)

            # Extract AC references
            ac_refs = re.findall(r"AC-(\d+)", stripped)
            ac_refs = [f"AC-{r}" for r in ac_refs]

            # Extract Verify method
            verify_match = re.search(r"Verify:\s*(.+)", stripped)
            verify = verify_match.group(1).strip() if verify_match else ""

            current_item = {
                "id": dod_id,
                "checked": is_checked,
                "description": desc,
                "ac_refs": ac_refs,
                "verify": verify,
                "evidence": "",
                "status": "passed" if is_checked else "pending",
            }

        # Sub-lines with Verify/Evidence
        elif current_item and stripped.startswith("- Verify:"):
            current_item["verify"] = stripped.replace("- Verify:", "").strip()
        elif current_item and stripped.startswith("- Evidence:"):
            current_item["evidence"] = stripped.replace("- Evidence:", "").strip()

    if current_item:
        items.append(current_item)

    return items


def extract_story_metadata(content: str) -> dict:
    """Extract basic story metadata."""
    meta = {"title": "", "story_key": ""}

    title_match = re.search(r"^#\s+Story\s+(\S+)\s*:\s*(.+)$", content, re.MULTILINE)
    if title_match:
        meta["story_key"] = title_match.group(1).strip()
        meta["title"] = title_match.group(2).strip()

    return meta


def create_qr_record(
    meta: dict,
    dod_items: list[dict],
    sira: int,
    project_root: Path,
) -> Path:
    """Create QR-<sira>.md record."""
    qr_dir = project_root / "docs" / "quality"
    qr_dir.mkdir(parents=True, exist_ok=True)
    qr_path = qr_dir / f"QR-{sira:03d}.md"

    today = date.today().isoformat()

    passed = sum(1 for d in dod_items if d["status"] == "passed")
    failed = sum(1 for d in dod_items if d["status"] == "failed")
    total = len(dod_items)

    if total == 0:
        qr_status = "pending"
    elif passed == total:
        qr_status = "pass"
    elif failed > 0:
        qr_status = "fail"
    else:
        qr_status = "partial"

    # Build DoD table
    dod_table = ""
    for d in dod_items:
        status_icon = "✅" if d["status"] == "passed" else ("❌" if d["status"] == "failed" else "⏳")
        dod_table += f"| {d['id']} | {status_icon} {d['status']} | {d.get('evidence', '—') or '—'} | {today} |\n"

    record = f"""# Quality Record: QR-{sira:03d}

| Field | Value |
|------|-------|
| Story | {meta.get('story_key', '—')} |
| Story Title | {meta.get('title', '—')} |
| Date | {today} |
| QR Status | {qr_status} |

- **Status:** in-review

## DoD Verification Results

| DoD Item | Status | Evidence | Date |
|----------|-------|-------|-------|
{dod_table}
## AC Verification Results

| AC | Status | Method | Evidence |
|----|-------|--------|----------|
| — | — | — | — |

## Test Summary

- Unit tests: —
- Integration tests: —
- Regression: —

## File List

- —

## Change Summary

- —

## Summary

- **Total DoD Items**: {total}
- **Passed**: {passed}
- **Failed**: {failed}
- **Pending**: {total - passed - failed}
"""

    qr_path.write_text(record, encoding="utf-8")
    return qr_path


def update_story_qr_section(story_path: Path, qr_path: Path, dod_items: list[dict], project_root: Path):
    """Update the Quality Record section in the story file."""
    if not story_path.exists():
        return

    content = story_path.read_text(encoding="utf-8")
    # posix: the record is written into a document, and a Windows path would
    # put backslashes into it
    rel_qr = qr_path.relative_to(project_root).as_posix()

    today = date.today().isoformat()
    passed = sum(1 for d in dod_items if d["status"] == "passed")
    failed = sum(1 for d in dod_items if d["status"] == "failed")
    total = len(dod_items)

    # Build QR table
    qr_table = ""
    for d in dod_items:
        status_icon = "✅" if d["status"] == "passed" else ("❌" if d["status"] == "failed" else "⏳")
        qr_table += f"| {d['id']} | {status_icon} {d['status']} | {d.get('evidence', '—') or '—'} | {today} |\n"

    # Columns follow the shipped story template (English, like every other
    # record); the earlier Turkish header was the only non-English row in the
    # record set. `Failed` counts recorded failures only — an unchecked item is
    # pending, not failed.
    new_qr_section = f"""## Quality Record (QR)

| DoD Item | Status | Evidence | Date |
|----------|--------|----------|------|
{qr_table}
### QR Summary
- **Total DoD Items**: {total}
- **Passed**: {passed}
- **Failed**: {failed}
- **Pending**: {total - passed - failed}
- **QR Record Path**: {rel_qr}
"""

    # Replace existing QR section or append.
    # The replacement must be a CALLABLE: a string replacement is parsed as a
    # template, so a Windows path in it ("...\quality\QR-001.md") raised
    # "bad escape \q" and, worse, a "\1"-looking sequence would silently inject
    # a capture group instead of the text.
    qr_pattern = r"##\s+Quality\s+Record\s*\(QR\).*?(?=\n##\s|\Z)"
    if re.search(qr_pattern, content, re.DOTALL | re.IGNORECASE):
        content = re.sub(qr_pattern, lambda _m: new_qr_section.strip(), content,
                         flags=re.DOTALL | re.IGNORECASE)
    else:
        content += "\n" + new_qr_section

    story_path.write_text(content, encoding="utf-8")


QR_PATH_LINE_RE = re.compile(r"(- \*\*QR Record Path\*\*:\s*)(\S+)(\s*)$",
                             re.MULTILINE)
METHOD_POINTER_RE = re.compile(r"<!--\s*Methodology record:\s*([^\s>]+)\s*-->")


def _retarget_path_line(path: Path, rel_qr: str) -> bool:
    """Point `path`'s `QR Record Path` row at `rel_qr`. True when it changed."""
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    new = QR_PATH_LINE_RE.sub(lambda m: m.group(1) + rel_qr + m.group(3), text)
    if new == text:
        return False
    path.write_text(new, encoding="utf-8")
    return True


def retarget_qr_references(story_path: Path, qr_path: Path,
                           project_root: Path) -> list[str]:
    """Name the record just created in every record that belongs to it.

    A story record is written BEFORE its QR exists, so it can only predict the
    name (`create-methodology-record.py` now predicts it from the QR directory,
    but a QR created in between still shifts it). Once the real file exists the
    prediction is checked and corrected here, so no record is left pointing at
    a QR file that was never created — and the native story's pointer is
    followed to its methodology record, which is the record that used to keep
    the stale number.
    """
    rel_qr = qr_path.relative_to(project_root).as_posix()
    touched = []
    if _retarget_path_line(story_path, rel_qr):
        touched.append(story_path.relative_to(project_root).as_posix())
    try:
        text = story_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        text = ""
    m = METHOD_POINTER_RE.search(text)
    if m:
        method = project_root / m.group(1)
        if _retarget_path_line(method, rel_qr):
            touched.append(method.relative_to(project_root).as_posix())
    return touched


def find_next_sira(quality_dir: Path) -> int:
    """Find the next available QR-XXX number."""
    existing = list(quality_dir.glob("QR-*.md"))
    if not existing:
        return 1

    max_sira = 0
    for f in existing:
        match = re.search(r"QR-(\d+)", f.name)
        if match:
            num = int(match.group(1))
            if num > max_sira:
                max_sira = num

    return max_sira + 1


def _mirror_heartbeat(project_root: Path, run_key: str, value: str,
                      to: str = "", note: str = "", sender: str = "") -> None:
    """Chain heartbeat for the created record, plus the downstream hand-off.

    Producer-side relay (2026-09-23 fix): this script IS the close-out point
    when a session creates the record through the guard-approved script path,
    so waiting for the skill text to also mirror `--to` left the baton
    unposted (the code-review signal that was forgotten one turn later).
    Mirror dedup makes the double-post safe: whichever side posts first, the
    second sees the identical `<run_key>:` baton waiting and skips."""
    if os.environ.get("METODOLOJI_NO_BLACKBOARD", "").strip() == "1":
        return
    try:
        engine = str(Path(__file__).resolve().parent.parent / "hooks" / "engine")
        if engine not in sys.path:
            sys.path.insert(0, engine)
        from modules import mirror as mir
        mir.mirror(str(project_root), run_key, value,
                   to=to or None, note=note, sender=sender or None)
    except Exception as exc:
        print(f"blackboard mirror skipped ({exc})", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Create QR record from story DoD")
    parser.add_argument("--story", required=True, help="Path to native story file")
    parser.add_argument("--sira", type=int, default=0, help="QR number (auto if 0)")
    parser.add_argument("--project-root", default=".", help="Project root directory")
    parser.add_argument("--no-blackboard", action="store_true",
                        help="skip the blackboard heartbeat (record is still created)")
    args = parser.parse_args()

    _proj = os.environ.get("OPENHANDS_PROJECT_DIR") or args.project_root
    project_root = Path(_proj).resolve()
    story_path = Path(args.story).resolve()

    if not story_path.exists():
        print(f"❌ Story file not found: {story_path}", file=sys.stderr)
        sys.exit(1)

    # Read and parse story — a non-UTF-8 file is an honest refusal, never a
    # traceback (E-016: the E-009 decode seam on the record tooling).
    try:
        content = story_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        print(f"❌ Story file is not valid UTF-8: {story_path}", file=sys.stderr)
        sys.exit(1)
    meta = extract_story_metadata(content)
    dod_items = extract_dod_items(content)

    if not dod_items:
        print("⚠️  No DoD items found in story file", file=sys.stderr)
        sys.exit(1)

    # Determine sira number
    quality_dir = project_root / "docs" / "quality"
    quality_dir.mkdir(parents=True, exist_ok=True)

    if args.sira > 0:
        sira = args.sira
    else:
        sira = find_next_sira(quality_dir)

    # Create QR record
    qr_path = create_qr_record(meta, dod_items, sira, project_root)

    # Update story file QR section
    update_story_qr_section(story_path, qr_path, dod_items, project_root)

    # Correct any record that predicted a different QR name (the methodology
    # record is written before this file exists, so its path is a prediction).
    retargeted = retarget_qr_references(story_path, qr_path, project_root)

    passed = sum(1 for d in dod_items if d["status"] == "passed")
    total = len(dod_items)

    failed = sum(1 for d in dod_items if d["status"] == "failed")
    print(f"✅ QR record created: {qr_path}")
    print(f"   Story: {meta.get('title', '—')}")
    print(f"   DoD items: {total}")
    print(f"   Passed: {passed}")
    # An unchecked box is PENDING work, not a failure — same rule the record
    # itself follows; the old `total - passed` summary read unreviewed items
    # as failures.
    print(f"   Failed: {failed}")
    print(f"   Pending: {total - passed - failed}")
    if retargeted:
        print(f"   Retargeted QR path in: {', '.join(retargeted)}")
    if not args.no_blackboard:
        # The baton claims readiness — post it only when every DoD item
        # passed (the mirror dedup keeps a skill-side close-out safe either
        # way: whichever side posts first, the other sees the identical
        # `<run_key>:` baton waiting and skips).
        if total and passed == total:
            _mirror_heartbeat(project_root, f"QR-{sira:03d}",
                              to="bmad-production-readiness",
                              sender="bmad-quality-record",
                              note=(f"QR-{sira:03d} ready for the production "
                                    f"readiness check."),
                              value=f"QR record created — {meta.get('title', '—')} "
                                    f"({passed}/{total} DoD passed)")
        else:
            _mirror_heartbeat(project_root, f"QR-{sira:03d}",
                              value=f"QR record created — {meta.get('title', '—')} "
                                    f"({passed}/{total} DoD passed, pending review)")


if __name__ == "__main__":
    main()

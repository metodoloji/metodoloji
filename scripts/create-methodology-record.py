#!/usr/bin/env python3
"""Create methodology story record (S-XXX.md) from native story file.

This script implements bridge #1 from the bridge document.
It reads a native story file and creates a methodology record.

Usage:
    python3 scripts/create-methodology-record.py --story <story-file> [--sira <number>]

The script:
1. Reads the native story file
2. Extracts metadata (title, epic, AC, experiment refs)
3. Creates docs/development/stories/S-<sira>.md from template
4. Updates story file with methodology reference comment
"""

import argparse
import os
import re
import sys
from pathlib import Path


# Markdown decoration the story templates wrap around field values: a story
# writes `- **Epic:** epic-1`, so a naive `Epic:\s*(.+)` captures `** epic-1`.
_FIELD_DECOR_RE = re.compile(r"[*_`]+")

# A DoD bullet appears in TWO canonical forms, and both must count:
#   checkbox (dev-story style)  `- [ ] DoD-001: …` / `- [x] DoD-001: …`
#   token    (record template)  `- [DoD-001] …`
# The engine's shared parser (hooks/engine/modules/utils.py:scan_dod_items,
# used by guard AND audit alike) recognises both. A generator that only knew
# the checkbox form reported "0 DoD items" for every story written from the
# shipped story template — because that template itself uses the token form.
_DOD_ID_RE = re.compile(r"[\[(]?DoD-(\d+)[\])]?")
# Tolerant of leading whitespace so the helpers stay correct for any caller
# (the section parsers hand them top-level lines, but a stray indented line
# must not leak the box into the description).
_CHECKBOX_RE = re.compile(r"^\s*-\s+\[([ xX])\]\s*")


def _clean_field(value: str) -> str:
    """Field value without the markdown decoration a story line may carry."""
    v = re.sub(r"^[-*]\s+", "", (value or "").strip())
    return _FIELD_DECOR_RE.sub("", v).strip()


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


def _parse_dod_items(section: str) -> list[dict]:
    """DoD items in a story's Definition of Done section (both bullet forms)."""
    items: list[dict] = []
    current: dict | None = None
    for raw in section.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        indented = line[:1].isspace()
        start = None if indented else _dod_item_start(line)
        if start:
            if current:
                items.append(current)
            dod_id, checked = start
            verify = re.search(r"Verify:\s*(.+)", line)
            current = {
                "id": dod_id,
                "checked": checked,
                "description": _dod_description(line),
                "ac_refs": [f"AC-{n}" for n in re.findall(r"AC-(\d+)", line)],
                "verify": verify.group(1).strip() if verify else "",
                "evidence": "",
            }
            continue
        if current and indented:
            stripped = line.strip()
            for field, key in (("Verify:", "verify"), ("Evidence:", "evidence")):
                if field in stripped:
                    current[key] = stripped.split(field, 1)[1].strip()
                    break
    if current:
        items.append(current)
    return items


def _ac_field(block: str, pattern: str) -> str:
    """First capture of *pattern* inside an AC block, or ""."""
    m = re.search(pattern, block)
    return m.group(1).strip() if m else ""


def extract_story_metadata(content: str) -> dict:
    """Extract metadata from a native story file."""
    meta = {
        "title": "",
        "epic": "",
        "status": "",
        "sprint": "",
        "acceptance_criteria": [],
        "experiment_refs": [],
        "tasks": [],
        "dod": [],
    }

    # Title
    title_match = re.search(r"^#\s+Story\s+\S+\s*:\s*(.+)$", content, re.MULTILINE)
    if title_match:
        meta["title"] = _clean_field(title_match.group(1))

    # Status — line-start anchored (with optional list bullet / bold), so an
    # indented YAML `status: APPROVED` under experiment_refs never matches.
    # Vocabulary is the engine's (hooks/engine/modules/guard.py), not the
    # older list in the bridge document.
    status_match = re.search(
        r"^(?:[-*]\s+)?\*{0,2}Status\*{0,2}\s*:\s*\*{0,2}\s*(.+)$",
        content, re.MULTILINE | re.IGNORECASE,
    )
    if status_match:
        meta["status"] = _clean_field(status_match.group(1))

    # Epic / Sprint
    epic_match = re.search(r"Epic:\s*(.+)$", content, re.MULTILINE)
    if epic_match:
        meta["epic"] = _clean_field(epic_match.group(1))
    sprint_match = re.search(r"Sprint:\s*(.+)$", content, re.MULTILINE)
    if sprint_match:
        meta["sprint"] = _clean_field(sprint_match.group(1))

    # Acceptance Criteria — one block per AC, so each AC keeps its OWN
    # metadata. The engine parses the same five fields, from the same shapes
    # (hooks/engine/modules/guard.py:_parse_ac_metadata); a record whose
    # Type/Measured/Verify columns stayed empty could not show that an AC is
    # verifiable at all.
    ac_match = re.search(
        r"##\s+Acceptance\s+Criteria\s*\n(.*?)(?=\n##\s|\Z)",
        content,
        re.DOTALL | re.IGNORECASE,
    )
    if ac_match:
        for block in re.split(r"(?=\[AC-\d+\])", ac_match.group(1)):
            id_match = re.search(r"\[AC-(\d+)\]", block)
            if not id_match:
                continue
            meta["acceptance_criteria"].append({
                "id": f"AC-{id_match.group(1)}",
                "experiment": _ac_field(block, r"Experiment:\s*(E-\d+|\u2014|-)"),
                "type": _ac_field(block, r"Type:\s*([A-Za-z-]+)"),
                "measured": _ac_field(block, r"Measured:\s*(true|false)"),
                "verify": _ac_field(block, r"Verify:\s*(.+)"),
                "status": "pending",
            })

    # Experiment refs from frontmatter
    fm_match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if fm_match:
        frontmatter = fm_match.group(1)
        in_refs = False
        current_ref = {}
        for line in frontmatter.splitlines():
            stripped = line.strip()
            if stripped.startswith("experiment_refs"):
                in_refs = True
                continue
            if in_refs:
                if stripped.startswith("- "):
                    if current_ref:
                        meta["experiment_refs"].append(current_ref)
                    current_ref = {}
                    inner = stripped[2:].strip()
                    kv = inner.split(":", 1)
                    if len(kv) == 2:
                        current_ref[kv[0].strip()] = kv[1].strip()
                elif ":" in stripped and current_ref:
                    kv = stripped.split(":", 1)
                    current_ref[kv[0].strip()] = kv[1].strip()
                elif stripped and not stripped.startswith("-"):
                    break
        if current_ref:
            meta["experiment_refs"].append(current_ref)

    # Technical Tasks — the line verbatim (indentation + checkbox state kept),
    # so the record reproduces the task tree instead of flattening subtasks and
    # double-prefixing them with a second bullet.
    task_section = re.search(
        r"##\s+Technical\s+Tasks\s*\n(.*?)(?=\n##\s|\Z)",
        content,
        re.DOTALL | re.IGNORECASE,
    )
    if task_section:
        for line in task_section.group(1).splitlines():
            if line.strip().startswith(("- [ ]", "- [x]", "- [X]")):
                meta["tasks"].append(line.rstrip())

    # DoD — items with their identifiers, notes and checkbox state (both forms).
    dod_section = re.search(
        r"##\s+Definition\s+of\s+Done\s*\n(.*?)(?=\n##\s|\Z)",
        content,
        re.DOTALL | re.IGNORECASE,
    )
    if dod_section:
        meta["dod"] = _parse_dod_items(dod_section.group(1))

    return meta


def create_methodology_record(meta: dict, sira: int, project_root: Path) -> Path:
    """Create S-<sira>.md methodology record from template."""
    # Canonical story template lives in stories/ (see templates/_template_S.md
    # header); fall back to the legacy docs/development/_template_S.md copy.
    template_path = project_root / "docs" / "development" / "stories" / "_template_S.md"
    legacy_path = project_root / "docs" / "development" / "_template_S.md"
    if not template_path.exists():
        template_path = legacy_path
    output_path = project_root / "docs" / "development" / "stories" / f"S-{sira:03d}.md"

    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if template_path.exists():
        template = template_path.read_text(encoding="utf-8")
    else:
        # Minimal template if file doesn't exist
        template = "# Methodology Record: S-{{sira}}\n\n| Field | Value |\n|------|-------|\n| Date | {{tarih}} |\n| Status | {{durum}} |\n"

    # Build AC table — the metadata columns come from the story, so the record
    # shows how each AC is verified, not merely that an AC exists.
    ac_table = ""
    for ac in meta["acceptance_criteria"]:
        ac_table += (
            f"| {ac['id']} | ⏳ pending | {ac.get('experiment') or '—'} "
            f"| {ac.get('type') or '—'} | {ac.get('measured') or '—'} "
            f"| {ac.get('verify') or '—'} |\n"
        )
    ac_ids_str = ", ".join(ac["id"] for ac in meta["acceptance_criteria"]) or "—"

    # Build experiment refs string
    exp_refs_str = ", ".join(
        f"{r.get('id', '?')} ({r.get('status', '?')})"
        for r in meta["experiment_refs"]
    ) or "—"

    # Build task list (verbatim bullet lines; already bullets)
    task_list = "\n".join(meta["tasks"]) or "- —"

    # Build DoD table — one row per item the STORY declares, including items
    # written in the record-template token form the old parser never saw.
    dod_table = ""
    for dod in meta["dod"]:
        label = dod["id"]
        if dod.get("description"):
            label += f" — {dod['description']}"
        dod_table += (
            f"| {label} | {'✅ passed' if dod['checked'] else '⏳ pending'} "
            f"| {dod.get('evidence') or '—'} | — |\n"
        )
    dod_passed = sum(1 for d in meta["dod"] if d["checked"])
    # A checkbox records done or not-done; it never records a FAILURE. Counting
    # the unchecked items as failed made every unstarted record claim
    # "Failed: N" — a plan reading as a verdict against itself.
    dod_failed = sum(1 for d in meta["dod"] if d.get("status") == "failed")

    # Sprint reference: the SP record the story names, plus the status file the
    # bridge §2.3 field points at.
    sprint_ref = f"{meta['sprint']} — sprint-status.yaml" if meta.get("sprint") else "sprint-status.yaml"

    # QR reference: the name the QR generator will actually pick. The record
    # used to embed `QR-<story number>.md`, which only coincides with the real
    # file while story and QR numbering stay in lockstep — once they diverge the
    # record points at a QR file that is never created (native S-056 → S-057,
    # real QR-014, embedded QR-057).
    qr_rel = next_qr_rel(project_root)

    # Substitute in template
    from datetime import date
    today = date.today().isoformat()

    record = f"""# Methodology Record: S-{sira:03d}

| Field | Value |
|------|-------|
| Date | {today} |
| Status | {meta.get('status') or 'backlog'} |
| Story Title | {meta.get('title') or '—'} |
| Epic | {meta.get('epic') or '—'} |
| Acceptance Criteria | {ac_ids_str} |
| Experiment Refs | {exp_refs_str} |
| File List | — (filled after implementation) |
| Sprint Ref | {sprint_ref} |
| Native Story | {meta.get('native_story_path') or '—'} |

## Acceptance Criteria Details

| AC | Status | Experiment | Type | Measured | Verify |
|----|-------|------------|------|----------|--------|
{ac_table}
## Technical Tasks

{task_list}

## Definition of Done

| DoD Item | Status | Evidence | Date |
|----------|-------|-------|-------|
{dod_table}
## Dev Agent Record

### Debug Log
- —

### Completion Notes
- —

### File List
- —

### Change Log
- —

## Quality Record (QR)

| DoD Item | Status | Evidence | Date |
|----------|-------|-------|-------|
{dod_table}
### QR Summary
- **Total DoD Items**: {len(meta['dod'])}
- **Passed**: {dod_passed}
- **Failed**: {dod_failed}
- **Pending**: {len(meta['dod']) - dod_passed - dod_failed}
- **QR Record Path**: {qr_rel}
"""

    output_path.write_text(record, encoding="utf-8")
    return output_path


def update_story_with_reference(story_path: Path, methodology_path: Path, project_root: Path):
    """Add methodology reference comment to story file."""
    if not story_path.exists():
        return

    content = story_path.read_text(encoding="utf-8")
    # posix: the pointer goes into a document. A Windows path rendered as
    # `docs\development\stories\S-001.md` (a) is not the form bridge §2.3
    # specifies and (b) never matches the same pointer written the normal way,
    # so every regeneration appended a duplicate comment.
    rel_methodology = methodology_path.relative_to(project_root).as_posix()

    # Check if reference already exists
    if str(rel_methodology) in content:
        return

    # Add reference after frontmatter or at top
    ref_comment = f"\n<!-- Methodology record: {rel_methodology} -->\n"

    # Find insertion point (after frontmatter --- block)
    fm_end = re.search(r"^---\s*\n.*?\n---", content, re.DOTALL | re.MULTILINE)
    if fm_end:
        insert_pos = fm_end.end()
        content = content[:insert_pos] + ref_comment + content[insert_pos:]
    else:
        content = ref_comment + content

    story_path.write_text(content, encoding="utf-8")


def next_qr_rel(project_root: Path) -> str:
    """`docs/quality/QR-<next>.md` — the file `create-qr-record.py` will write.

    Both generators number their records independently, so the QR a story record
    predicts must be computed from the QR directory (same `max + 1` rule the QR
    generator uses), never from the story number. `sync-story-qr.py` re-resolves
    and repairs the path if anything creates a QR record in between.
    """
    max_sira = 0
    try:
        for f in (project_root / "docs" / "quality").glob("QR-*.md"):
            match = re.search(r"QR-(\d+)", f.name)
            if match:
                max_sira = max(max_sira, int(match.group(1)))
    except OSError:
        pass
    return f"docs/quality/QR-{max_sira + 1:03d}.md"


def find_next_sira(stories_dir: Path) -> int:
    """Find the next available S-XXX number."""
    existing = list(stories_dir.glob("S-*.md"))
    if not existing:
        return 1

    max_sira = 0
    for f in existing:
        match = re.search(r"S-(\d+)", f.name)
        if match:
            num = int(match.group(1))
            if num > max_sira:
                max_sira = num

    return max_sira + 1


def _mirror_heartbeat(project_root: Path, run_key: str, value: str,
                      to: str = "", note: str = "", sender: str = "") -> None:
    """Chain heartbeat for the created record, plus the downstream hand-off.

    Producer-side relay (2026-09-23 fix): this script IS the close-out point
    when a session creates the story through the guard-approved script path,
    so waiting for the skill text to also mirror `--to` left the baton
    unposted. Mirror dedup makes the double-post safe: whichever side posts
    first, the second sees the identical `<run_key>:` baton waiting and skips."""
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
    parser = argparse.ArgumentParser(description="Create methodology story record")
    parser.add_argument("--story", required=True, help="Path to native story file")
    parser.add_argument("--sira", type=int, default=0, help="Record number (auto if 0)")
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

    # Read and parse story
    content = story_path.read_text(encoding="utf-8")
    meta = extract_story_metadata(content)
    meta["native_story_path"] = story_path.relative_to(project_root).as_posix()

    # Determine sira number
    stories_dir = project_root / "docs" / "development" / "stories"
    stories_dir.mkdir(parents=True, exist_ok=True)

    if args.sira > 0:
        sira = args.sira
    else:
        sira = find_next_sira(stories_dir)

    # Create methodology record
    output_path = create_methodology_record(meta, sira, project_root)

    # Update story file with reference
    update_story_with_reference(story_path, output_path, project_root)

    print(f"✅ Methodology record created: {output_path}")
    print(f"   Story: {meta.get('title', '—')}")
    print(f"   AC count: {len(meta['acceptance_criteria'])}")
    print(f"   Experiment refs: {len(meta['experiment_refs'])}")
    print(f"   Tasks: {len(meta['tasks'])}")
    print(f"   DoD items: {len(meta['dod'])}")
    if not args.no_blackboard:
        _mirror_heartbeat(project_root, f"S-{sira:03d}",
                          to="bmad-dev-story",
                          sender="bmad-create-story",
                          note=(f"story record S-{sira:03d} ready-for-dev — open it "
                                f"with bmad-dev-story."),
                          value=f"story record created — {meta.get('title', '—')}")


if __name__ == "__main__":
    main()

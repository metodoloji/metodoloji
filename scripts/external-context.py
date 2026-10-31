#!/usr/bin/env python3
"""Scaffold, validate, migrate and audit external-technology context entries.

`/metodoloji:add` leaves the analysis to the agent — it fetches pages, reads
repositories and extracts supplied documents with the harness's own tools.
This tool owns the deterministic half:

    prepare        create docs/research/external/<slug>/context.md from the template
    finalize       validate a completed entry, record the review and rebuild the index
    check          audit every registered entry, the schema and the generated index
    refresh        start a revalidation without losing the last reviewed analysis
    discard        archive an abandoned entry under .archive/ (never deletes)
    list           list entries with status and computed freshness
    rebuild-index  regenerate the README index from the entries themselves
    migrate        upgrade schema-1 entries to the current schema

Stdlib only, offline. It never fetches a URL and never parses a PDF; the agent
records what it actually inspected, and `finalize` refuses placeholders.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_COMMON_NAME = "external_context_common"
# A stale cached copy from another tree must not shadow this script's sibling
# module: the bench loads the same CLIs from seeded copies of the tree.
_cached_common = sys.modules.get(_COMMON_NAME)
if _cached_common is not None:
    _cached_file = getattr(_cached_common, "__file__", "")
    try:
        same = Path(_cached_file).resolve() == (
            _SCRIPT_DIR / f"{_COMMON_NAME}.py").resolve()
    except (OSError, TypeError):
        same = False
    if not same:
        del sys.modules[_COMMON_NAME]
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from external_context_common import (  # noqa: E402
    ARCHIVE_DIR,
    DEFAULT_REVALIDATE_DAYS,
    INTEGRATION_BULLETS,
    PLUGIN_README_REL,
    PLUGIN_TEMPLATE_REL,
    PROJECT_README_REL,
    PROJECT_TEMPLATE_REL,
    REGISTRY_REL,
    REQUIRED_FIELDS,
    REQUIRED_SECTIONS,
    SCHEMA_VERSION,
    STATUSES,
    ContextError,
    atomic_write_text,
    cell,
    classify_source,
    derive_name,
    field as _field,
    fold,
    freshness,
    future_date,
    heading_name,
    inside,
    link_text,
    parse_date,
    parse_fields,
    parse_sections,
    read_text,
    registry_lock,
    registry_root,
    rel as _rel,
    require_inside,
    require_inside_target,
    resolve_today,
    slugify,
    source_key,
    source_rows,
    table_cells,
    utc_stamp,
)

REVIEWER_PENDING = {"", "pending", "<reviewer name or role>"}
_CITATION_RE = re.compile(
    r"(docs/[\w./-]+|E-\d{3,}|STORY-[\w-]+|\bIR-[\w-]+|\bSP-[\w-]+|ADR-[\w-]+)",
    re.IGNORECASE)
_MIN_SECTION_CHARS = {
    "Summary": 40,
    "Maintenance, license, and security": 20,
    "Risks, alternatives, and open questions": 20,
    "Use in this project": 10,
}
BEGIN_MARKER = "<!-- BEGIN GENERATED REGISTRY -->"
END_MARKER = "<!-- END GENERATED REGISTRY -->"
EMPTY_REGISTRY = "No external contexts registered yet."


# --------------------------------------------------------------------------
# Template and metadata helpers
# --------------------------------------------------------------------------

def _template(root: Path) -> tuple[Path, str]:
    project = root / PROJECT_TEMPLATE_REL
    if project.is_file():
        return project, read_text(project)
    plugin = Path(__file__).resolve().parents[1] / PLUGIN_TEMPLATE_REL
    if plugin.is_file():
        return plugin, read_text(plugin)
    raise ContextError(
        "no external-context template found: run /metodoloji:init (expected "
        f"{PROJECT_TEMPLATE_REL} or {PLUGIN_TEMPLATE_REL})")


def _placeholders(template: str) -> set[str]:
    return set(re.findall(r"<[^<>\n]+>", template))


def _set_field(text: str, name: str, value: str) -> str:
    """Replace (or insert) one `- **Name:** value` bullet.

    The replacement is a callable, never a replacement template: a Windows
    path in `value` used to reach `re.sub` as `\\U...` and raise
    `re.PatternError: bad escape \\U` (F-01).
    """
    line = f"- **{name}:** {value}".rstrip()
    pattern = rf"(?m)^-\s*\*\*{re.escape(name)}:\*\*.*$"
    if re.search(pattern, text):
        return re.sub(pattern, lambda _match: line, text, count=1)
    heading = re.search(r"(?m)^#\s+.*$", text)
    if heading:
        insert_at = heading.end()
        return text[:insert_at] + "\n\n" + line + text[insert_at:]
    return line + "\n\n" + text


def _has_placeholder(value: str, placeholders: set[str]) -> str:
    return next((token for token in sorted(placeholders) if token in value), "")


def _revision_rows(text: str) -> list[str]:
    section = parse_sections(text).get("Revision history", "")
    return source_rows(section)


def _append_revision(text: str, day: str, ref: str, reviewer: str,
                     note: str = "reviewed") -> str:
    """Append one revision row unless the same review is already the last row."""
    rows = _revision_rows(text)
    new_row = (f"| {day} | {cell(ref or 'unknown')} | {cell(note)} | "
               f"{cell(reviewer or 'unrecorded')} |")
    if rows:
        last = table_cells(rows[-1])
        if len(last) >= 4 and last[0] == day and last[1] == (ref or "unknown"):
            return text
    if "## Revision history" not in text:
        text = text.rstrip() + (
            "\n\n## Revision history\n\n"
            "| Date | Version/ref | Change | Reviewer |\n"
            "|---|---|---|---|\n" + new_row + "\n")
        return text
    pattern = re.compile(r"(?ms)^##\s+Revision history\s*$(.*?)(?=^##\s+|\Z)")
    match = pattern.search(text)
    if not match:
        return text
    body = match.group(1).rstrip()
    if not body:
        body = "\n\n| Date | Version/ref | Change | Reviewer |\n|---|---|---|---|"
    updated = body + "\n" + new_row + "\n\n"
    return text[:match.start(1)] + updated + text[match.end(1):]


# --------------------------------------------------------------------------
# Entry reading, collisions, validation
# --------------------------------------------------------------------------

def _entries(registry: Path) -> list[dict]:
    """Every live entry, sorted by slug; dot-directories (archives) skipped."""
    found: list[dict] = []
    try:
        folders = sorted(registry.iterdir(), key=lambda path: path.name.casefold())
    except OSError as exc:
        raise ContextError(
            f"registry could not be listed ({type(exc).__name__})") from exc
    for folder in folders:
        if not folder.is_dir() or folder.name.startswith("."):
            continue
        entry = folder / "context.md"
        if not entry.is_file():
            continue
        # The folder itself may be a symlink that escapes the registry; the
        # root guard cannot see that, and `migrate` writes this path.
        entry = require_inside(entry, registry, f"entry {folder.name}")
        text = read_text(entry)
        fields = parse_fields(text)
        found.append({
            "slug": folder.name,
            "entry": entry,
            "text": text,
            "name": heading_name(text, folder.name),
            "status": fields.get("status", "").casefold(),
            "ref": fields.get("analyzed version/ref", ""),
            "analyzed_on": fields.get("analyzed on", ""),
            "revalidate_after": fields.get("revalidate after", ""),
            "last_reviewed": fields.get("last reviewed on", ""),
            "reviewer": fields.get("reviewer", ""),
            "schema": fields.get("schema", ""),
            "aliases": [value.strip() for value in re.split(
                r"[,;]", fields.get("aliases", "")) if value.strip()],
        })
    return found


def _alias_index(entries: list[dict]) -> dict[str, str]:
    index: dict[str, str] = {}
    for entry in entries:
        for value in [entry["name"], *entry["aliases"]]:
            key = fold(value).strip()
            if key:
                index.setdefault(key, entry["slug"])
    return index


def _collision(root: Path, slug: str, name: str, canonical: str) -> None:
    registry = registry_root(root, create=True)
    entries = _entries(registry)
    aliases = _alias_index(entries)
    key = fold(name).strip()
    owner = aliases.get(key)
    if owner and owner != slug:
        raise ContextError(
            f"'{name}' (or one of its aliases) is already registered as "
            f"{REGISTRY_REL}/{owner}/context.md; update that entry instead of "
            "creating a duplicate")
    for entry in entries:
        if fold(entry["slug"]) == fold(slug):
            raise ContextError(
                f"slug '{slug}' is already registered as "
                f"{_rel(root, entry['entry'])}; choose --slug for a distinct entry")
        if entry["slug"].casefold() == slug.casefold():
            raise ContextError(
                f"slug '{slug}' differs only by case from '{entry['slug']}'")
    wanted = source_key(canonical, root)
    for entry in entries:
        rows = source_rows(parse_sections(entry["text"]).get("Sources inspected", ""))
        for row in rows:
            cells = table_cells(row)
            if len(cells) >= 1 and source_key(cells[0], root) == wanted:
                raise ContextError(
                    f"this source is already registered under "
                    f"{REGISTRY_REL}/{entry['slug']}/context.md; update it with "
                    "refresh instead of creating a second entry")


def _entry_report_text(root: Path, text: str,
                       today: date | None = None) -> list[dict]:
    """Structured findings for one entry's text: error or warning (F-18).

    Validation is separated from the file read so `finalize` can validate a
    candidate before it writes anything. `today` is injectable for the same
    reason the finder accepts `--today`: a date-sensitive warning must not
    depend on the calendar of the machine running the audit.
    """
    findings: list[dict] = []

    def add(severity: str, message: str) -> None:
        findings.append({"severity": severity, "message": message})

    _, template = _template(root)
    placeholders = _placeholders(template)
    fields = parse_fields(text)

    def placeholder(label: str, value: str) -> None:
        hit = _has_placeholder(value, placeholders)
        if hit:
            add("error", f"{label} still holds a template placeholder: {hit}")

    if not re.search(r"(?m)^#\s+\S", text):
        add("error", "missing the '# <name> Context' heading")
    for name in REQUIRED_FIELDS:
        value = fields.get(name.casefold(), "")
        if not value:
            add("error", f"missing or empty field: {name}")
            continue
        placeholder(name, value)

    schema = fields.get("schema", "")
    if schema and schema.strip() != str(SCHEMA_VERSION):
        add("error", f"Schema must be {SCHEMA_VERSION}; run `migrate` for "
                     f"schema {schema.strip()} entries")
    status = fields.get("status", "").casefold()
    if status and status not in STATUSES:
        add("error", "Status must be one of: " + ", ".join(STATUSES))
    if status == "draft":
        add("error", "Status is 'draft' (entry is still a draft, not registered)")
    if status == "revalidating":
        add("warning", "revalidation is in progress; the last reviewed analysis "
                       "is still readable but is not current evidence")
    if status == "stale":
        add("warning", "entry is explicitly marked stale; refresh and finalize it")
    if status == "superseded":
        if not fields.get("superseded by", "").strip():
            add("error", "Status is 'superseded' but 'Superseded by' is empty")
        else:
            add("warning", "entry is superseded and should not be used as "
                           "current evidence")

    analyzed = parse_date(fields.get("analyzed on", ""))
    if fields.get("analyzed on") and analyzed is None:
        add("error", "Analyzed on must be a YYYY-MM-DD date")
    revalidate = parse_date(fields.get("revalidate after", ""))
    if fields.get("revalidate after") and revalidate is None:
        add("error", "Revalidate after must be a YYYY-MM-DD date")
    last_reviewed = parse_date(fields.get("last reviewed on", ""))
    if fields.get("last reviewed on") and last_reviewed is None \
            and fields.get("last reviewed on", "").casefold() != "pending":
        add("error", "Last reviewed on must be a YYYY-MM-DD date or 'pending'")
    if status == "reviewed" and not fields.get("last reviewed on", "").strip():
        add("error", "reviewed entry has no 'Last reviewed on' date")
    if status == "reviewed" and fields.get("last reviewed on", "").casefold() == "pending":
        add("error", "reviewed entry still has 'Last reviewed on: pending'; run finalize")
    if analyzed and revalidate and revalidate < analyzed:
        add("error", "Revalidate after is earlier than Analyzed on")
    if revalidate and revalidate < (today or date.today()) and status == "reviewed":
        add("warning", "Revalidate after has passed; the entry is stale")
    reviewer = fields.get("reviewer", "").strip()
    if status == "reviewed" and reviewer.casefold() in REVIEWER_PENDING:
        add("error", "reviewed entry has no reviewer; set the 'Reviewer' field")

    sections = parse_sections(text)
    for section in REQUIRED_SECTIONS:
        if section not in sections:
            add("error", f"missing section: ## {section}")
            continue
        if not sections[section]:
            add("error", f"empty section: ## {section}")
            continue
        minimum = _MIN_SECTION_CHARS.get(section)
        if minimum and len(sections[section]) < minimum:
            add("error", f"section is too thin to be evidence: ## {section} "
                         f"({len(sections[section])} < {minimum} characters)")

    integration = sections.get("Integration guidance", "")
    for label in INTEGRATION_BULLETS:
        match = re.search(
            rf"(?m)^-\s*\*\*{re.escape(label)}:\*\*\s*(.*)$", integration)
        if not match:
            add("error", f"Integration guidance is missing the '{label}' bullet")
            continue
        value = match.group(1).strip()
        if not value:
            add("error", f"Integration guidance bullet is empty: {label}")
            continue
        placeholder(label, value)

    risks = sections.get("Risks, alternatives, and open questions", "")
    gaps = re.search(r"(?m)^-\s*\*\*Known gaps:\*\*\s*(.*)$", risks)
    if not gaps:
        add("error", "Risks section is missing the '- **Known gaps:**' bullet")
    elif not gaps.group(1).strip():
        add("error", "the 'Known gaps' bullet is empty; write 'none observed'")

    rows = source_rows(sections.get("Sources inspected", ""))
    if not rows:
        add("error", "Sources inspected has no data row (a header alone is not evidence)")
    seen_sources: set[str] = set()
    for row in rows:
        cells = table_cells(row)
        if len(cells) != 4:
            add("error", f"Sources inspected row has {len(cells)} cells, expected 4")
            continue
        if any(not value for value in cells):
            add("error", "Sources inspected row has an empty cell")
            continue
        placeholder("Sources inspected", row)
        source = cells[0]
        if not (re.match(r"^https?://", source, re.IGNORECASE)
                or "/" in source or "\\" in source
                or re.match(r"^[A-Za-z]:", source)):
            add("error", "Sources inspected row does not name a URL or a path: "
                         f"{source!r}")
        if parse_date(cells[2]) is None:
            add("error", "Sources inspected row has no access date (YYYY-MM-DD): "
                         f"{cells[2]!r}")
        key = source_key(source)
        if key in seen_sources:
            add("warning", f"duplicate source row: {source}")
        seen_sources.add(key)

    revisions = _revision_rows(text)
    if not revisions:
        add("error", "Revision history has no data row")
    else:
        for row in revisions:
            cells = table_cells(row)
            if len(cells) != 4:
                add("error", "Revision history row has the wrong number of cells")
                break
        last = table_cells(revisions[-1])
        if len(last) == 4:
            if parse_date(last[0]) is None:
                add("error", "Revision history row has no YYYY-MM-DD date")
            if fields.get("analyzed version/ref") and last[1] != fields.get(
                    "analyzed version/ref"):
                add("warning", "the last revision row does not name the analyzed ref")
            if not last[3]:
                add("error", "Revision history row has no reviewer")

    use = sections.get("Use in this project", "")
    if use and not _CITATION_RE.search(use):
        add("warning", "Use in this project cites no project record or docs path")

    if any(token in text for token in placeholders):
        remaining = sorted(token for token in placeholders if token in text)
        add("error", f"template placeholders remain: {', '.join(remaining[:3])}")

    seen: set[tuple[str, str]] = set()
    unique: list[dict] = []
    for finding in findings:
        key = (finding["severity"], finding["message"])
        if key not in seen:
            seen.add(key)
            unique.append(finding)
    return unique


def _entry_report(root: Path, entry: Path,
                  today: date | None = None) -> list[dict]:
    """Read one entry inside the registry and validate its text."""
    findings: list[dict] = []

    def add(severity: str, message: str) -> None:
        findings.append({"severity": severity, "message": message})

    try:
        registry = registry_root(root)
    except ContextError as exc:
        add("error", str(exc))
        return findings
    if not inside(entry, registry):
        add("error", "entry is outside the external-context registry")
        return findings
    try:
        text = read_text(entry)
    except ContextError as exc:
        add("error", str(exc))
        return findings
    return _entry_report_text(root, text, today)


def validate(project_root: str | Path, entry: str | Path) -> list[str]:
    """Error messages that keep an entry from being registered (compat shape)."""
    root = Path(project_root).resolve()
    path = Path(entry)
    if not path.is_absolute():
        path = root / path
    try:
        path = path.resolve()
    except OSError:
        return ["entry cannot be resolved"]
    try:
        registry = registry_root(root)
    except ContextError as exc:
        return [str(exc)]
    if not inside(path, registry):
        return ["entry is outside the external-context registry"]
    return [finding["message"] for finding in _entry_report(root, path)
            if finding["severity"] == "error"]


def validate_report(project_root: str | Path, entry: str | Path) -> list[dict]:
    """Every finding, split into errors and warnings."""
    root = Path(project_root).resolve()
    path = Path(entry)
    if not path.is_absolute():
        path = root / path
    return _entry_report(root, path)


def _require_entry(registry: Path, root: Path, slug: str, verb: str) -> Path:
    """The entry path, proven to resolve inside the registry.

    `finalize`, `refresh` and `discard` used to build `registry/<slug>/context.md`
    and write it without checking the folder: a symlinked slug could move the
    write outside the project. The error keeps the old "no entry to <verb>"
    shape for a missing file and adds a distinct refusal for an escaping one.
    """
    entry = registry / slug / "context.md"
    if not entry.is_file():
        raise ContextError(f"no entry to {verb}: {_rel(root, entry)}")
    return require_inside(entry, registry, _rel(root, entry))


# --------------------------------------------------------------------------
# Index generation
# --------------------------------------------------------------------------

def _index_line(entry: dict) -> str:
    name = entry["name"] or entry["slug"]
    ref = entry["ref"] or "unknown"
    day = entry["analyzed_on"] or "unknown"
    return f"- [{link_text(name)}]({entry['slug']}/context.md) — {ref} ({day})"


def _indexable(entries: list[dict]) -> list[dict]:
    # A draft is not registered and must not appear in the index. Legacy
    # entries without a Status field stay listed (their line says unknown), and
    # a superseded entry stays listed so readers can follow the replacement.
    return [entry for entry in entries if entry["status"] != "draft"]


def _generated_block(entries: list[dict]) -> str:
    lines = [_index_line(entry) for entry in _indexable(entries)]
    body = "\n".join(lines) if lines else EMPTY_REGISTRY
    return f"{BEGIN_MARKER}\n{body}\n{END_MARKER}"


def _write_index(root: Path, registry: Path) -> tuple[bool, str]:
    """Regenerate the README index from the entries themselves (F-16)."""
    index = root / PROJECT_README_REL
    if not index.is_file():
        seeded = Path(__file__).resolve().parents[1] / PLUGIN_README_REL
        if not seeded.is_file():
            raise ContextError("registry README is missing and no template is available")
        atomic_write_text(index, read_text(seeded))
    text = read_text(index)
    entries = _entries(registry)
    block = _generated_block(entries)
    if BEGIN_MARKER in text and END_MARKER in text:
        updated = re.sub(
            re.escape(BEGIN_MARKER) + r".*?" + re.escape(END_MARKER),
            lambda _match: block, text, count=1, flags=re.DOTALL)
    else:
        start = text.find("## Registered contexts")
        if start < 0:
            raise ContextError("registry README has no '## Registered contexts' section")
        following = text.find("\n## ", start + 1)
        end = len(text) if following < 0 else following
        section = "## Registered contexts\n\n" + block + "\n\n"
        updated = text[:start] + section + text[end:]
    changed = updated != text
    if changed:
        atomic_write_text(index, updated)
    lines = [line for line in block.splitlines() if line.startswith("- [")]
    return changed, (lines[0] if lines else EMPTY_REGISTRY)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def prepare(project_root: str | Path, source: str, name: str = "",
            slug: str = "", today: str | None = None) -> dict:
    root = Path(project_root).resolve()
    registry = registry_root(root, create=True)
    kind, identity, canonical = classify_source(source)
    template_path, template = _template(root)
    chosen_name = (name or derive_name(canonical, kind, identity)).strip()
    if not chosen_name or chosen_name.isdigit():
        raise ContextError("cannot derive a name from the source; pass --name")
    chosen_slug = slugify(slug or chosen_name)
    if "<Technology or Project>" not in template:
        raise ContextError("the template has no '<Technology or Project>' heading placeholder")
    day_date = resolve_today(today)
    day = day_date.isoformat()
    text = template.replace("<Technology or Project>", chosen_name, 1)
    text = _set_field(text, "Status", "draft")
    text = _set_field(text, "Analyzed on", day)
    text = _set_field(text, "Revalidate after", future_date(
        DEFAULT_REVALIDATE_DAYS, day_date))
    text = _set_field(text, "Last reviewed on", "pending")
    source_row = (f"| {cell(canonical)} | {kind} | <ref>; {day} | "
                  "<what was inspected> |")
    text, replaced = re.subn(
        r"(?m)^\| <URL or local path> \|.*$", lambda _match: source_row, text, count=1)
    if not replaced:
        raise ContextError("the template has no source-table placeholder row")
    # Collision detection and the write must be one critical section: two
    # concurrent `prepare` calls could both see no collision and one would
    # silently overwrite the other's scaffold.
    with registry_lock(registry):
        _collision(root, chosen_slug, chosen_name, canonical)
        entry = registry / chosen_slug / "context.md"
        # The folder is new, so the E-092 per-entry guard (which needs an
        # existing file) cannot see a pre-existing symlink/junction here. Prove
        # the nearest existing ancestor stays inside before mkdir/write.
        require_inside_target(entry, registry, _rel(root, entry))
        if entry.exists():
            raise ContextError(
                f"entry already exists: {_rel(root, entry)} — update it instead of "
                "creating a second folder (choose --slug for a distinct entry)")
        atomic_write_text(entry, text)
    return {
        "ok": True, "action": "prepare", "slug": chosen_slug, "name": chosen_name,
        "source": canonical, "source_kind": kind, "date": day,
        "entry": _rel(root, entry), "template": _rel(root, template_path),
        "next": f"complete the analysis in {_rel(root, entry)}, then run finalize",
    }


def finalize(project_root: str | Path, slug: str, today: str | None = None) -> dict:
    """Register a completed entry; a failed run leaves the file untouched.

    The review date and revision row used to be written before validation, so
    a rejected registration still left review evidence behind. Build a
    candidate, validate it, and write only when there are no errors.
    """
    root = Path(project_root).resolve()
    registry = registry_root(root)
    slug = slugify(slug)
    entry = _require_entry(registry, root, slug, "finalize")
    day_date = resolve_today(today)
    day = day_date.isoformat()
    with registry_lock(registry):
        text = read_text(entry)
        fields = parse_fields(text)
        status = fields.get("status", "").casefold()
        candidate = text
        if status == "reviewed":
            if not fields.get("last reviewed on", "").strip() \
                    or fields.get("last reviewed on", "").casefold() == "pending":
                candidate = _set_field(candidate, "Last reviewed on", day)
            reviewer = fields.get("reviewer", "").strip()
            if reviewer.casefold() in REVIEWER_PENDING:
                reviewer = "unrecorded"
            candidate = _append_revision(
                candidate, day, fields.get("analyzed version/ref", ""), reviewer)
        report = _entry_report_text(root, candidate, day_date)
        errors = [finding["message"] for finding in report
                  if finding["severity"] == "error"]
        warnings = [finding["message"] for finding in report
                    if finding["severity"] == "warning"]
        if status != "reviewed":
            errors.append("Status must be 'reviewed' after the analysis is complete")
        if errors:
            return {"ok": False, "action": "finalize", "entry": _rel(root, entry),
                    "findings": errors, "warnings": warnings,
                    "next": "fix the findings and re-run finalize"}
        if candidate != text:
            atomic_write_text(entry, candidate)
        changed, line = _write_index(root, registry)
        entries = _entries(registry)
        own = next((item for item in entries if item["slug"] == slug), None)
        return {"ok": True, "action": "finalize", "entry": _rel(root, entry),
                "index": _rel(root, root / PROJECT_README_REL),
                "index_line": line, "updated": changed, "warnings": warnings,
                "entry_line": _index_line(own) if own else line}


def refresh(project_root: str | Path, slug: str, today: str | None = None) -> dict:
    """Start revalidation without destroying or hiding the last reviewed analysis.

    The previous behaviour set `Status: draft`, which made the finder hide an
    entry whose reviewed analysis was still the best evidence available (F-03).
    `revalidating` keeps the analysis, keeps `Analyzed on` and
    `Last reviewed on`, and marks the entry as no longer current.
    """
    root = Path(project_root).resolve()
    registry = registry_root(root)
    slug = slugify(slug)
    entry = _require_entry(registry, root, slug, "refresh")
    day = resolve_today(today).isoformat()
    with registry_lock(registry):
        text = read_text(entry)
        fields = parse_fields(text)
        previous_status = fields.get("status", "")
        if previous_status.casefold() == "draft":
            raise ContextError(
                f"{_rel(root, entry)} is still a draft; complete and finalize it "
                "instead of refreshing it")
        text = _set_field(text, "Status", "revalidating")
        text = _set_field(text, "Revalidation started on", day)
        atomic_write_text(entry, text)
    return {"ok": True, "action": "refresh", "slug": slug,
            "entry": _rel(root, entry), "date": day,
            "previous_status": previous_status,
            "last_analyzed_on": fields.get("analyzed on", ""),
            "next": "re-analyze the source, update the refs/sections, set "
                    "Status: reviewed and Last reviewed on, then run finalize"}


def discard(project_root: str | Path, slug: str, force: bool = False,
            reason: str = "", today: str | None = None) -> dict:
    """Archive an entry under `<registry>/.archive/` instead of deleting it.

    `--force` is required for any entry that was ever reviewed, not only for one
    that is reviewed right now: `refresh` used to reset the status to draft, so
    a reviewed entry could be archived without force immediately after refresh
    (F-04). The folder is moved, never removed.
    """
    root = Path(project_root).resolve()
    registry = registry_root(root)
    slug = slugify(slug)
    entry_dir = registry / slug
    entry = _require_entry(registry, root, slug, "discard")
    day = resolve_today(today).isoformat()
    with registry_lock(registry):
        text = read_text(entry)
        fields = parse_fields(text)
        status = fields.get("status", "").casefold()
        last_reviewed = fields.get("last reviewed on", "").strip()
        ever_reviewed = (
            status in ("reviewed", "revalidating", "stale")
            or bool(parse_date(last_reviewed)))
        if ever_reviewed and not force:
            raise ContextError(
                f"{_rel(root, entry)} was reviewed (status: {status or 'unknown'}); "
                "pass --force to archive it (the folder is kept under .archive/, "
                "not deleted)")
        archive_root = registry / ARCHIVE_DIR
        # `.archive` is skipped by the audit (dot-directory), so a linked
        # archive directory would move the entry outside the project without
        # any entry-level guard noticing. Refuse before creating or moving.
        require_inside_target(archive_root, registry, _rel(root, archive_root))
        archive_root.mkdir(parents=True, exist_ok=True)
        stamp = utc_stamp()
        target = archive_root / f"{stamp}-{slug}"
        suffix = 1
        while target.exists():
            suffix += 1
            target = archive_root / f"{stamp}-{slug}-{suffix}"
        require_inside_target(target, registry, _rel(root, target))
        shutil.move(str(entry_dir), str(target))
        note = (
            f"# Archived external context: {heading_name(text, slug)}\n\n"
            f"- **Archived on:** {day}\n"
            f"- **Previous status:** {status or 'unknown'}\n"
            f"- **Analyzed on:** {fields.get('analyzed on', 'unknown')}\n"
            f"- **Analyzed version/ref:** "
            f"{fields.get('analyzed version/ref', 'unknown')}\n"
            f"- **Last reviewed on:** {last_reviewed or 'never'}\n"
            f"- **Reason:** {reason or 'not recorded'}\n")
        atomic_write_text(target / "ARCHIVED.md", note)
        changed, _line = _write_index(root, registry)
    return {"ok": True, "action": "discard", "slug": slug,
            "archived": _rel(root, target), "index_updated": changed}


def list_entries(project_root: str | Path, today: str | None = None) -> dict:
    root = Path(project_root).resolve()
    registry = registry_root(root)
    day = resolve_today(today)
    entries = []
    for entry in _entries(registry):
        state = entry["status"] or "unknown"
        entries.append({
            "slug": entry["slug"], "name": entry["name"], "status": state,
            "analyzed_on": entry["analyzed_on"], "analyzed_version_ref":
                entry["ref"] or "unknown",
            "revalidate_after": entry["revalidate_after"] or "",
            "freshness": freshness(state, entry["revalidate_after"], day),
            "reviewer": entry["reviewer"] or "unrecorded",
            "schema": entry["schema"] or "1",
            "path": _rel(root, entry["entry"]),
        })
    return {"ok": True, "action": "list", "entries": entries,
            "count": len(entries)}


def rebuild_index(project_root: str | Path) -> dict:
    root = Path(project_root).resolve()
    registry = registry_root(root)
    with registry_lock(registry):
        changed, line = _write_index(root, registry)
    return {"ok": True, "action": "rebuild-index",
            "index": _rel(root, root / PROJECT_README_REL),
            "updated": changed, "index_line": line}


def migrate(project_root: str | Path, dry_run: bool = False,
            today: str | None = None) -> dict:
    """Upgrade schema-1 entries in place; the only writer of missing fields."""
    root = Path(project_root).resolve()
    registry = registry_root(root)
    day_date = resolve_today(today)
    day = day_date.isoformat()
    changed: list[str] = []
    with registry_lock(registry):
        for entry in _entries(registry):
            text = entry["text"]
            fields = parse_fields(text)
            if fields.get("schema", "").strip() == str(SCHEMA_VERSION):
                continue
            status = fields.get("status", "").casefold()
            analyzed_on = fields.get("analyzed on", "")
            analyzed = parse_date(analyzed_on) or day_date
            text = _set_field(text, "Schema", str(SCHEMA_VERSION))
            if not fields.get("revalidate after", "").strip():
                explicit = parse_date(fields.get("revalidate when", ""))
                text = _set_field(
                    text, "Revalidate after",
                    (explicit or analyzed + timedelta(
                        days=DEFAULT_REVALIDATE_DAYS)).isoformat())
            if status == "reviewed" and not fields.get("last reviewed on", "").strip():
                text = _set_field(text, "Last reviewed on", analyzed_on or day)
            if not fields.get("reviewer", "").strip():
                text = _set_field(text, "Reviewer", "unrecorded (migrated)")
            if "## Revision history" not in text:
                text = _append_revision(
                    text, analyzed_on or day,
                    fields.get("analyzed version/ref", ""),
                    fields.get("reviewer", "") or "unrecorded (migrated)",
                    note="migrated")
            changed.append(_rel(root, entry["entry"]))
            if not dry_run:
                atomic_write_text(entry["entry"], text)
    return {"ok": True, "action": "migrate", "dry_run": dry_run,
            "changed": changed, "count": len(changed)}


def check(project_root: str | Path, strict: bool = False,
          today: str | None = None) -> dict:
    root = Path(project_root).resolve()
    day = resolve_today(today)
    registry_path = root / REGISTRY_REL
    if not registry_path.exists() and not registry_path.is_symlink():
        return {"ok": False, "action": "check", "registry": "missing",
                "entries": 0, "findings": [], "warnings": [], "index_findings": [
                    f"registry is missing: {REGISTRY_REL}"]}
    try:
        registry = registry_root(root)
    except ContextError as exc:
        return {"ok": False, "action": "check", "registry": "unsafe",
                "entries": 0, "findings": [], "warnings": [],
                "index_findings": [str(exc)]}
    try:
        entries = _entries(registry)
    except ContextError as exc:
        return {"ok": False, "action": "check", "registry": "unreadable",
                "entries": 0, "findings": [], "warnings": [],
                "index_findings": [str(exc)]}

    findings: list[dict] = []
    warnings: list[str] = []
    try:
        folders = sorted(registry.iterdir(), key=lambda path: path.name.casefold())
    except OSError:
        folders = []
    for folder in folders:
        if (folder.is_dir() and not folder.name.startswith(".")
                and not (folder / "context.md").is_file()):
            warnings.append(
                f"{_rel(root, folder)}: folder has no context.md (orphan entry)")
    for entry in entries:
        report = _entry_report(root, entry["entry"], day)
        errors = [finding["message"] for finding in report
                  if finding["severity"] == "error"]
        entry_warnings = [finding["message"] for finding in report
                          if finding["severity"] == "warning"]
        if errors:
            findings.append({"entry": _rel(root, entry["entry"]),
                             "findings": errors, "warnings": entry_warnings})
        warnings.extend(f"{_rel(root, entry['entry'])}: {message}"
                        for message in entry_warnings)

    aliases: dict[str, str] = {}
    for entry in entries:
        for value in [entry["name"], *entry["aliases"]]:
            key = fold(value).strip()
            if not key:
                continue
            if key in aliases and aliases[key] != entry["slug"]:
                findings.append({
                    "entry": _rel(root, entry["entry"]),
                    "findings": [f"alias '{value}' is also used by "
                                 f"{aliases[key]}/context.md"],
                    "warnings": []})
            else:
                aliases[key] = entry["slug"]

    # One source, one entry: the audit derives the same identity `prepare`
    # compares, so a legacy duplicate written before the canonical spelling
    # existed is reported instead of surviving because its text differs.
    sources: dict[str, str] = {}
    for entry in entries:
        rows = source_rows(parse_sections(entry["text"]).get("Sources inspected", ""))
        for row in rows:
            cells = table_cells(row)
            if len(cells) != 4:
                continue
            key = source_key(cells[0], root)
            if not key:
                continue
            owner = sources.get(key)
            if owner and owner != entry["slug"]:
                findings.append({
                    "entry": _rel(root, entry["entry"]),
                    "findings": [f"source '{cells[0]}' is also registered by "
                                 f"{owner}/context.md"],
                    "warnings": []})
            else:
                sources[key] = entry["slug"]

    index = registry / "README.md"
    index_findings: list[str] = []
    if not index.is_file():
        index_findings.append("registry README is missing")
    else:
        listed = re.sub(r"<!--.*?-->", "", read_text(index), flags=re.DOTALL)
        linked = set(re.findall(r"\(([^)/<>]+)/context\.md\)", listed))
        slugs = {entry["slug"] for entry in _indexable(entries)}
        missing = sorted(slugs - linked)
        dead = sorted(linked - slugs)
        if missing:
            index_findings.append(f"entries missing from the index: {missing}")
        if dead:
            index_findings.append(f"index links with no entry: {dead}")
        by_slug = {entry["slug"]: entry for entry in entries}
        for slug in sorted(slugs & linked):
            match = re.search(
                rf"(?m)^-\s*\[([^\]]*)\]\({re.escape(slug)}/context\.md\)"
                rf"\s*—\s*(.*?)\s*\((\d{{4}}-\d{{2}}-\d{{2}})\)\s*$", listed)
            if not match:
                index_findings.append(
                    f"index line for {slug} is not in the format finalize writes")
                continue
            entry = by_slug[slug]
            expected_name = entry["name"] or slug
            expected_ref = entry["ref"] or "unknown"
            expected_date = entry["analyzed_on"] or "unknown"
            if match.group(2) != expected_ref:
                index_findings.append(
                    f"index line for {slug} shows ref '{match.group(2)}' but the "
                    f"entry analyzes '{expected_ref}'")
            if match.group(3) != expected_date:
                index_findings.append(
                    f"index line for {slug} shows date '{match.group(3)}' but the "
                    f"entry was analyzed on '{expected_date}'")
            if match.group(1) != expected_name:
                index_findings.append(
                    f"index line for {slug} names '{match.group(1)}' but the "
                    f"entry heading is '{expected_name}'")
        if BEGIN_MARKER not in read_text(index):
            index_findings.append(
                "registry index is hand-maintained; run `rebuild-index` to "
                "generate it from the entries")
    ok = not findings and not index_findings and not (strict and warnings)
    return {"ok": ok, "action": "check", "registry": "available",
            "entries": len(entries), "findings": findings,
            "warnings": warnings, "index_findings": index_findings,
            "strict": strict}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _date_arg(value: str) -> str:
    """argparse type for `--today`: reject a value instead of ignoring it."""
    if parse_date(value) is None:
        raise argparse.ArgumentTypeError("expected a YYYY-MM-DD date")
    return value


def _emit(result: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if result.get("error"):
        print(f"ERROR: {result['error']}", file=sys.stderr)
        return
    action = result.get("action")
    if action == "prepare":
        print(f"Prepared {result['entry']} ({result['source_kind']}, slug {result['slug']}).")
        print(f"Next: {result['next']}.")
        return
    if action == "finalize":
        if result.get("ok"):
            print(f"Registered {result['entry']}.")
            print(f"Index: {result.get('entry_line') or result['index_line']}")
        else:
            print(f"Not registered: {result['entry']}")
            for finding in result["findings"]:
                print(f"- {finding}")
        for warning in result.get("warnings", []):
            print(f"Warning: {warning}")
        return
    if action == "refresh":
        print(f"Started revalidation for {result['entry']} "
              f"(was {result['previous_status'] or 'unknown'}, now revalidating, "
              f"date {result['date']}).")
        print(f"Last reviewed analysis kept (Analyzed on: "
              f"{result['last_analyzed_on'] or 'unknown'}).")
        print(f"Next: {result['next']}.")
        return
    if action == "discard":
        print(f"Archived {result['archived']} (removed from the registry).")
        print("Index: rebuilt." if result["index_updated"]
              else "Index: unchanged.")
        return
    if action == "list":
        print(f"{result['count']} external context entr(y/ies):")
        for entry in result["entries"]:
            print(f"- {entry['slug']}: {entry['status']} / {entry['freshness']} "
                  f"({entry['analyzed_version_ref']}, {entry['analyzed_on']})")
        return
    if action == "rebuild-index":
        print(f"Rebuilt {result['index']} "
              f"({'updated' if result['updated'] else 'already current'}).")
        return
    if action == "migrate":
        mode = "would change" if result["dry_run"] else "changed"
        print(f"Migrate {mode} {result['count']} entr(y/ies).")
        for entry in result["changed"]:
            print(f"- {entry}")
        return
    if action == "check":
        print(f"External context registry: {result['entries']} entries, "
              f"{len(result['findings'])} entr(y/ies) with findings, "
              f"{len(result['warnings'])} warning(s), "
              f"{len(result['index_findings'])} index finding(s)")
        for item in result["findings"]:
            print(f"- {item['entry']}")
            for finding in item["findings"]:
                print(f"  - {finding}")
            for warning in item.get("warnings", []):
                print(f"  warning: {warning}")
        for warning in result["warnings"]:
            print(f"- warning: {warning}")
        for finding in result["index_findings"]:
            print(f"- {finding}")
        if result.get("strict") and result["warnings"]:
            print("- strict: warnings are failures in this mode")
        return


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare", help="create a draft entry from the template")
    prep.add_argument("--project-root", required=True)
    prep.add_argument("--source", required=True,
                      help="repository/docs URL, or a local PDF/Markdown file or directory")
    prep.add_argument("--name", default="", help="display name (derived from the source by default)")
    prep.add_argument("--slug", default="", help="registry folder name (derived from the name by default)")
    prep.add_argument("--today", type=_date_arg, default=None,
                      help="pin the scaffold date (YYYY-MM-DD)")
    prep.add_argument("--json", action="store_true")
    fin = commands.add_parser("finalize", help="validate a completed entry and index it")
    fin.add_argument("--project-root", required=True)
    fin.add_argument("--slug", required=True)
    fin.add_argument("--today", type=_date_arg, default=None,
                     help="pin the review date (YYYY-MM-DD)")
    fin.add_argument("--json", action="store_true")
    chk = commands.add_parser("check", help="audit every entry and the registry index")
    chk.add_argument("--project-root", required=True)
    chk.add_argument("--strict", action="store_true",
                     help="treat warnings as failures (CI mode)")
    chk.add_argument("--today", type=_date_arg, default=None,
                     help="pin freshness checks (YYYY-MM-DD)")
    chk.add_argument("--json", action="store_true")
    ref = commands.add_parser(
        "refresh", help="start a revalidation (keeps the last reviewed analysis)")
    ref.add_argument("--project-root", required=True)
    ref.add_argument("--slug", required=True)
    ref.add_argument("--today", type=_date_arg, default=None,
                     help="pin the revalidation start date (YYYY-MM-DD)")
    ref.add_argument("--json", action="store_true")
    dsc = commands.add_parser(
        "discard", help="archive an abandoned entry under .archive/ (never deletes)")
    dsc.add_argument("--project-root", required=True)
    dsc.add_argument("--slug", required=True)
    dsc.add_argument("--force", action="store_true",
                     help="also archive an entry that was ever reviewed")
    dsc.add_argument("--reason", default="", help="why the entry is archived")
    dsc.add_argument("--today", type=_date_arg, default=None,
                     help="pin the archive date (YYYY-MM-DD)")
    dsc.add_argument("--json", action="store_true")
    lst = commands.add_parser("list", help="list entries with status and freshness")
    lst.add_argument("--project-root", required=True)
    lst.add_argument("--today", type=_date_arg, default=None,
                     help="pin computed freshness (YYYY-MM-DD)")
    lst.add_argument("--json", action="store_true")
    reb = commands.add_parser(
        "rebuild-index", help="regenerate the README index from the entries")
    reb.add_argument("--project-root", required=True)
    reb.add_argument("--json", action="store_true")
    mig = commands.add_parser("migrate", help="upgrade schema-1 entries in place")
    mig.add_argument("--project-root", required=True)
    mig.add_argument("--dry-run", action="store_true")
    mig.add_argument("--today", type=_date_arg, default=None,
                     help="pin the migration date (YYYY-MM-DD)")
    mig.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare(args.project_root, args.source, args.name, args.slug,
                             today=args.today or None)
        elif args.command == "finalize":
            result = finalize(args.project_root, args.slug,
                              today=args.today or None)
        elif args.command == "discard":
            result = discard(args.project_root, args.slug, args.force, args.reason,
                             today=args.today or None)
        elif args.command == "refresh":
            result = refresh(args.project_root, args.slug,
                             today=args.today or None)
        elif args.command == "list":
            result = list_entries(args.project_root, today=args.today or None)
        elif args.command == "rebuild-index":
            result = rebuild_index(args.project_root)
        elif args.command == "migrate":
            result = migrate(args.project_root, args.dry_run,
                             today=args.today or None)
        else:
            result = check(args.project_root, args.strict,
                           today=args.today or None)
    except ContextError as exc:
        result = {"ok": False, "action": args.command, "error": str(exc)}
        _emit(result, args.json)
        return 2
    _emit(result, args.json)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

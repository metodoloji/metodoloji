#!/usr/bin/env python3
"""Scaffold, validate and audit external-technology context entries.

`/metodoloji:add` leaves the analysis to the agent — it fetches pages, reads
repositories and extracts supplied documents with the harness's own tools.
This tool owns the deterministic half:

    prepare   create docs/research/external/<slug>/context.md from the template
    finalize  validate a completed entry and update the registry README index
    check     audit every registered entry and the index

Stdlib only, offline. It never fetches a URL and never parses a PDF; the agent
records what it actually inspected, and `finalize` refuses placeholders.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

REGISTRY_REL = "docs/research/external"
PROJECT_TEMPLATE_REL = f"{REGISTRY_REL}/_template.md"
PLUGIN_TEMPLATE_REL = "templates/external-context.md"
PLUGIN_README_REL = "templates/external-context-README.md"
PROJECT_README_REL = f"{REGISTRY_REL}/README.md"

REQUIRED_FIELDS = (
    "Status", "Analyzed on", "Analyzed version/ref", "Revalidate when",
    "Project relevance",
)
REQUIRED_SECTIONS = (
    "Sources inspected", "Summary", "Integration guidance",
    "Maintenance, license, and security",
    "Risks, alternatives, and open questions", "Use in this project",
)
INTEGRATION_BULLETS = (
    "Supported runtimes/platforms", "Key interfaces and APIs",
    "Data/control flow and boundary", "Setup/configuration",
    "Failure modes and operational constraints",
    "Compatibility/version constraints",
)
REPO_HOSTS = frozenset({
    "github.com", "gitlab.com", "bitbucket.org", "codeberg.org",
    "gitea.com", "sr.ht",
})


class ContextError(Exception):
    """An input or environment problem (exit 2), not a validation finding."""


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ContextError(f"cannot read {path}: {exc.strerror or exc}") from exc


def _rel(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return str(path)


def _field(text: str, name: str) -> str:
    match = re.search(rf"(?m)^-\s*\*\*{re.escape(name)}:\*\*\s*(.*)$", text)
    return match.group(1).strip() if match else ""


def _sections(text: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        heading = re.match(r"^##\s+(.+?)\s*$", line)
        if heading:
            current = heading.group(1)
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return {name: "\n".join(lines).strip() for name, lines in sections.items()}


def _table_cells(row: str) -> list[str]:
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


def _source_rows(section: str) -> list[str]:
    rows: list[str] = []
    seen_separator = False
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = _table_cells(line)
        if not seen_separator:
            if cells and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
                seen_separator = True
            continue
        rows.append(line)
    return rows if seen_separator else []


def _template(root: Path) -> tuple[Path, str]:
    project = root / PROJECT_TEMPLATE_REL
    if project.is_file():
        return project, _read(project)
    plugin = Path(__file__).resolve().parents[1] / PLUGIN_TEMPLATE_REL
    if plugin.is_file():
        return plugin, _read(plugin)
    raise ContextError(
        "no external-context template found: run /metodoloji:init (expected "
        f"{PROJECT_TEMPLATE_REL} or {PLUGIN_TEMPLATE_REL})")


def _placeholders(template: str) -> set[str]:
    return set(re.findall(r"<[^<>\n]+>", template))


def _link_text(value: str) -> str:
    return value.replace("[", "(").replace("]", ")").replace("\n", " ").strip()


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:64].strip("-") or "external-context"


def classify_source(source: str) -> tuple[str, str]:
    """Return (template type, identity) for a URL or a local path."""
    source = source.strip()
    if not source:
        raise ContextError("--source is required")
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", source):
        parsed = urlparse(source)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ContextError("only http(s) URLs can be analyzed")
        host = parsed.netloc.casefold().removeprefix("www.")
        return ("repository" if host in REPO_HOSTS else "official docs"), host
    path = Path(source).expanduser()
    if not path.exists():
        raise ContextError(f"source path does not exist: {source}")
    if path.is_file():
        return "supplied file", str(path.resolve())
    if path.is_dir():
        return "supplied path", str(path.resolve())
    raise ContextError(f"source is neither a file nor a directory: {source}")


def _derive_name(source: str, kind: str, identity: str) -> str:
    if kind == "repository":
        segment = unquote(urlparse(source).path.rstrip("/").rsplit("/", 1)[-1])
        return segment[:-4] if segment.endswith(".git") else segment
    if kind == "official docs":
        labels = [label for label in identity.split(".") if label]
        return labels[-2] if len(labels) >= 2 else identity
    return Path(identity).stem if kind == "supplied file" else Path(identity).name


def prepare(project_root: str | Path, source: str, name: str = "",
            slug: str = "", today: str | None = None) -> dict:
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise ContextError(f"project root is not a directory: {project_root}")
    kind, identity = classify_source(source)
    template_path, template = _template(root)
    chosen_name = (name or _derive_name(source, kind, identity)).strip()
    if not chosen_name:
        raise ContextError("cannot derive a name from the source; pass --name")
    chosen_slug = slugify(slug or chosen_name)
    entry = root / REGISTRY_REL / chosen_slug / "context.md"
    if entry.exists():
        raise ContextError(
            f"entry already exists: {_rel(root, entry)} — update it instead of "
            "creating a second folder (choose --slug for a distinct entry)")
    if "<Technology or Project>" not in template:
        raise ContextError("the template has no '<Technology or Project>' heading placeholder")
    day = today or date.today().isoformat()
    text = template.replace("<Technology or Project>", chosen_name, 1)
    text = text.replace("Status:** draft | reviewed", "Status:** draft", 1)
    text = text.replace("Analyzed on:** YYYY-MM-DD", f"Analyzed on:** {day}", 1)
    source_row = f"| {_cell(source)} | {kind} | <ref>; {day} | <what was inspected> |"
    text, replaced = re.subn(r"(?m)^\| <URL or local path> \|.*$", source_row, text, count=1)
    if not replaced:
        raise ContextError("the template has no source-table placeholder row")
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text(text, encoding="utf-8")
    return {
        "ok": True, "action": "prepare", "slug": chosen_slug, "name": chosen_name,
        "source": source, "source_kind": kind, "date": day,
        "entry": _rel(root, entry), "template": _rel(root, template_path),
        "next": f"complete the analysis in {_rel(root, entry)}, then run finalize",
    }


def validate(project_root: str | Path, entry: str | Path) -> list[str]:
    """Every structural problem that keeps an entry from being registered."""
    root = Path(project_root).resolve()
    registry = (root / REGISTRY_REL).resolve()
    path = Path(entry)
    if not path.is_absolute():
        path = root / path
    try:
        path = path.resolve()
        path.relative_to(registry)
    except (OSError, ValueError):
        return ["entry is outside the external-context registry"]
    try:
        text = _read(path)
    except ContextError as exc:
        return [str(exc)]
    _, template = _template(root)
    placeholders = _placeholders(template)
    findings: list[str] = []

    def placeholder_finding(label: str, value: str) -> None:
        hit = sorted(token for token in placeholders if token in value)
        if hit:
            findings.append(f"{label} still holds a template placeholder: {hit[0]}")

    if not re.search(r"(?m)^#\s+\S", text):
        findings.append("missing the '# <name> Context' heading")
    for field in REQUIRED_FIELDS:
        value = _field(text, field)
        if not value:
            findings.append(f"missing or empty field: {field}")
            continue
        placeholder_finding(field, value)
        if field == "Status" and value.casefold() not in {"draft", "reviewed"}:
            findings.append("Status must be 'draft' or 'reviewed'")
        if field == "Analyzed on" and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            findings.append("Analyzed on must be a YYYY-MM-DD date")
    sections = _sections(text)
    for section in REQUIRED_SECTIONS:
        if section not in sections:
            findings.append(f"missing section: ## {section}")
        elif not sections[section]:
            findings.append(f"empty section: ## {section}")
    integration = sections.get("Integration guidance", "")
    for label in INTEGRATION_BULLETS:
        match = re.search(rf"(?m)^-\s*\*\*{re.escape(label)}:\*\*\s*(.*)$", integration)
        if not match:
            findings.append(f"Integration guidance is missing the '{label}' bullet")
            continue
        value = match.group(1).strip()
        if not value:
            findings.append(f"Integration guidance bullet is empty: {label}")
            continue
        placeholder_finding(label, value)
    rows = _source_rows(sections.get("Sources inspected", ""))
    if not rows:
        findings.append("Sources inspected has no data row (a header alone is not evidence)")
    for row in rows:
        cells = _table_cells(row)
        if len(cells) != 4:
            findings.append(f"Sources inspected row has {len(cells)} cells, expected 4")
        elif any(not cell for cell in cells):
            findings.append("Sources inspected row has an empty cell")
        placeholder_finding("Sources inspected", row)
    if any(token in text for token in placeholders):
        remaining = sorted(token for token in placeholders if token in text)
        findings.append(f"template placeholders remain: {', '.join(remaining[:3])}")
    # De-duplicate identical findings while keeping order.
    seen: set[str] = set()
    return [finding for finding in findings
            if not (finding in seen or seen.add(finding))]


def _upsert_index(index: Path, slug: str, name: str, ref: str, day: str) -> tuple[bool, str]:
    text = _read(index)
    line = f"- [{_link_text(name)}]({slug}/context.md) — {ref} ({day})"
    existing = re.compile(rf"(?m)^-\s*\[[^\]]*\]\({re.escape(slug)}/context\.md\).*$")
    match = existing.search(text)
    if match:
        updated = text[:match.start()] + line + text[match.end():]
        if updated != text:
            index.write_text(updated, encoding="utf-8")
        return updated != text, line
    start = text.find("## Registered contexts")
    if start < 0:
        raise ContextError("registry README has no '## Registered contexts' section")
    following = text.find("\n## ", start + 1)
    end = len(text) if following < 0 else following
    section = text[start:end]
    if "No external contexts registered yet." in section:
        section = section.replace("No external contexts registered yet.", line, 1)
    else:
        section = section.rstrip() + "\n\n" + line + "\n\n"
    index.write_text(text[:start] + section + text[end:], encoding="utf-8")
    return True, line


def finalize(project_root: str | Path, slug: str) -> dict:
    root = Path(project_root).resolve()
    slug = slugify(slug)
    entry = root / REGISTRY_REL / slug / "context.md"
    if not entry.is_file():
        raise ContextError(f"no entry to finalize: {_rel(root, entry)}")
    findings = validate(root, entry)
    if _field(_read(entry), "Status").casefold() != "reviewed":
        findings.append("Status must be 'reviewed' after the analysis is complete")
    if findings:
        return {"ok": False, "action": "finalize", "entry": _rel(root, entry),
                "findings": findings, "next": "fix the findings and re-run finalize"}
    text = _read(entry)
    index = root / PROJECT_README_REL
    if not index.is_file():
        seeded = Path(__file__).resolve().parents[1] / PLUGIN_README_REL
        if not seeded.is_file():
            raise ContextError("registry README is missing and no template is available")
        index.parent.mkdir(parents=True, exist_ok=True)
        index.write_text(_read(seeded), encoding="utf-8")
    heading = next((line[2:].strip() for line in text.splitlines()
                    if line.startswith("# ")), slug)
    name = heading[:-len(" Context")] if heading.endswith(" Context") else heading
    updated, line = _upsert_index(index, slug, name,
                                  _field(text, "Analyzed version/ref"),
                                  _field(text, "Analyzed on"))
    return {"ok": True, "action": "finalize", "entry": _rel(root, entry),
            "index": _rel(root, index), "index_line": line, "updated": updated}


def refresh(project_root: str | Path, slug: str, today: str | None = None) -> dict:
    """Mark a registered entry for revalidation without losing its analysis.

    The previous analysis stays in place; only the provenance state moves:
    Status becomes draft and Analyzed on gets a new date. The agent updates the
    refs and sections, sets Status: reviewed, and runs finalize to re-index.
    """
    root = Path(project_root).resolve()
    slug = slugify(slug)
    entry = root / REGISTRY_REL / slug / "context.md"
    if not entry.is_file():
        raise ContextError(f"no entry to refresh: {_rel(root, entry)}")
    text = _read(entry)
    if not re.search(r"(?m)^-\s*\*\*Status:\*\*", text):
        raise ContextError("entry has no Status field")
    if not re.search(r"(?m)^-\s*\*\*Analyzed on:\*\*", text):
        raise ContextError("entry has no 'Analyzed on' field")
    previous_status = _field(text, "Status")
    day = today or date.today().isoformat()
    updated = re.sub(r"(?m)^-\s*\*\*Status:\*\*\s*.*$",
                     "- **Status:** draft", text, count=1)
    updated = re.sub(r"(?m)^-\s*\*\*Analyzed on:\*\*\s*.*$",
                     f"- **Analyzed on:** {day}", updated, count=1)
    entry.write_text(updated, encoding="utf-8")
    return {"ok": True, "action": "refresh", "slug": slug,
            "entry": _rel(root, entry), "date": day,
            "previous_status": previous_status,
            "next": "re-analyze the source, update the refs/sections, set Status: "
                    "reviewed, then run finalize"}


def _remove_index_entry(index: Path, slug: str) -> bool:
    """Drop one registry link and restore the empty-registry sentence if needed."""
    text = _read(index)
    pattern = re.compile(
        rf"(?m)^-\s*\[[^\]]*\]\({re.escape(slug)}/context\.md\).*\n?")
    if not pattern.search(text):
        return False
    updated = pattern.sub("", text, count=1)
    start = updated.find("## Registered contexts")
    if start >= 0:
        following = updated.find("\n## ", start + 1)
        end = len(updated) if following < 0 else following
        section = updated[start:end]
        if not re.search(r"(?m)^-\s*\[[^\]]*\]\([^)]+/context\.md\)", section) \
                and "No external contexts registered yet." not in section:
            section = section.rstrip() + "\n\nNo external contexts registered yet.\n\n"
            updated = updated[:start] + section + updated[end:]
    index.write_text(updated, encoding="utf-8")
    return True


def discard(project_root: str | Path, slug: str, force: bool = False) -> dict:
    """Archive an entry under `<registry>/.archive/` instead of deleting it.

    A reviewed entry needs `--force`: discarding finished evidence is a
    deliberate act. The folder is moved, never removed, and its index line is
    dropped (the empty-registry sentence comes back when the last link goes).
    """
    root = Path(project_root).resolve()
    slug = slugify(slug)
    entry_dir = root / REGISTRY_REL / slug
    entry = entry_dir / "context.md"
    if not entry.is_file():
        raise ContextError(f"no entry to discard: {_rel(root, entry)}")
    status = _field(_read(entry), "Status").casefold()
    if status == "reviewed" and not force:
        raise ContextError(
            f"{_rel(root, entry)} is reviewed; pass --force to archive it "
            "(the folder is kept under .archive/, not deleted)")
    archive_root = root / REGISTRY_REL / ".archive"
    archive_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = archive_root / f"{stamp}-{slug}"
    suffix = 1
    while target.exists():
        suffix += 1
        target = archive_root / f"{stamp}-{slug}-{suffix}"
    shutil.move(str(entry_dir), str(target))
    index = root / PROJECT_README_REL
    index_updated = _remove_index_entry(index, slug) if index.is_file() else False
    return {"ok": True, "action": "discard", "slug": slug,
            "archived": _rel(root, target), "index_updated": index_updated}


def check(project_root: str | Path) -> dict:
    root = Path(project_root).resolve()
    registry = root / REGISTRY_REL
    if not registry.is_dir():
        return {"ok": False, "action": "check", "registry": "missing",
                "entries": 0, "findings": [], "index_findings": [
                    f"registry is missing: {REGISTRY_REL}"]}
    try:
        folders = sorted(registry.iterdir(), key=lambda path: path.name.casefold())
    except OSError as exc:
        return {"ok": False, "action": "check", "registry": "unreadable",
                "entries": 0, "findings": [], "index_findings": [
                    f"registry could not be listed ({type(exc).__name__})"]}
    slugs: list[str] = []
    findings: list[dict] = []
    for folder in folders:
        if not folder.is_dir() or folder.name.startswith("."):
            continue
        entry = folder / "context.md"
        if not entry.is_file():
            continue
        slugs.append(folder.name)
        problems = validate(root, entry)
        if _field(_read(entry), "Status").casefold() != "reviewed":
            problems.append("Status is not 'reviewed' (entry is still a draft)")
        if problems:
            findings.append({"entry": _rel(root, entry), "findings": problems})
    index = registry / "README.md"
    index_findings: list[str] = []
    if not index.is_file():
        index_findings.append("registry README is missing")
    else:
        listed = re.sub(r"<!--.*?-->", "", _read(index), flags=re.DOTALL)
        linked = set(re.findall(r"\(([^)/<>]+)/context\.md\)", listed))
        missing = sorted(set(slugs) - linked)
        dead = sorted(linked - set(slugs))
        if missing:
            index_findings.append(f"entries missing from the index: {missing}")
        if dead:
            index_findings.append(f"index links with no entry: {dead}")
        # A line that outlived its entry: `finalize` is the only writer of the
        # `— ref (date)` line, while `refresh` and hand edits move the entry's
        # fields immediately. Link existence alone left the README claiming an
        # old ref/date/name while the audit stayed green (E-087).
        for slug in sorted(set(slugs) & linked):
            match = re.search(
                rf"(?m)^-\s*\[([^\]]*)\]\({re.escape(slug)}/context\.md\)"
                rf"\s*—\s*(.*?)\s*\((\d{{4}}-\d{{2}}-\d{{2}})\)\s*$", listed)
            if not match:
                index_findings.append(
                    f"index line for {slug} is not in the format finalize writes")
                continue
            entry_text = _read(registry / slug / "context.md")
            heading = next((line[2:].strip() for line in entry_text.splitlines()
                            if line.startswith("# ")), slug)
            expected_name = (heading[:-len(" Context")]
                             if heading.endswith(" Context") else heading)
            expected_ref = _field(entry_text, "Analyzed version/ref")
            expected_date = _field(entry_text, "Analyzed on")
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
    return {"ok": not findings and not index_findings, "action": "check",
            "registry": "available", "entries": len(slugs),
            "findings": findings, "index_findings": index_findings}


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
            print(f"Index: {result['index_line']}")
        else:
            print(f"Not registered: {result['entry']}")
            for finding in result["findings"]:
                print(f"- {finding}")
        return
    if action == "refresh":
        print(f"Marked {result['entry']} for revalidation "
              f"(was {result['previous_status'] or 'unknown'}, now draft, "
              f"date {result['date']}).")
        print(f"Next: {result['next']}.")
        return
    if action == "discard":
        print(f"Archived {result['archived']} (removed from the registry).")
        print("Index: line removed." if result["index_updated"]
              else "Index: no line to remove.")
        return
    if action == "check":
        print(f"External context registry: {result['entries']} entries, "
              f"{len(result['findings'])} entr(y/ies) with findings, "
              f"{len(result['index_findings'])} index finding(s)")
        for item in result["findings"]:
            print(f"- {item['entry']}")
            for finding in item["findings"]:
                print(f"  - {finding}")
        for finding in result["index_findings"]:
            print(f"- {finding}")


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
    prep.add_argument("--json", action="store_true")
    fin = commands.add_parser("finalize", help="validate a completed entry and index it")
    fin.add_argument("--project-root", required=True)
    fin.add_argument("--slug", required=True)
    fin.add_argument("--json", action="store_true")
    chk = commands.add_parser("check", help="audit every entry and the registry index")
    chk.add_argument("--project-root", required=True)
    chk.add_argument("--json", action="store_true")
    ref = commands.add_parser(
        "refresh", help="mark an existing entry for revalidation (keeps its analysis)")
    ref.add_argument("--project-root", required=True)
    ref.add_argument("--slug", required=True)
    ref.add_argument("--json", action="store_true")
    dsc = commands.add_parser(
        "discard", help="archive an abandoned entry under .archive/ (never deletes)")
    dsc.add_argument("--project-root", required=True)
    dsc.add_argument("--slug", required=True)
    dsc.add_argument("--force", action="store_true",
                     help="also archive a reviewed entry (finished evidence)")
    dsc.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare(args.project_root, args.source, args.name, args.slug)
        elif args.command == "finalize":
            result = finalize(args.project_root, args.slug)
        elif args.command == "discard":
            result = discard(args.project_root, args.slug, args.force)
        elif args.command == "refresh":
            result = refresh(args.project_root, args.slug)
        else:
            result = check(args.project_root)
    except ContextError as exc:
        result = {"ok": False, "action": args.command, "error": str(exc)}
        _emit(result, args.json)
        return 2
    _emit(result, args.json)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

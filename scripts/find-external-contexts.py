#!/usr/bin/env python3
"""Find reusable external-technology context entries for a workflow topic.

Read-only and deterministic. Scoring uses content terms only: template labels
and lifecycle vocabulary are removed, fields carry different weights, and each
matched term is weighted by inverse document frequency so a term shared by every
entry cannot dominate. Every result names the terms that matched and a short
snippet, and every entry reports its status, computed freshness and whether it
needs revalidation.

Stdlib only, offline. It never fetches a URL and never injects context into a
workflow; the caller decides what to read.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
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
    REGISTRY_REL,
    ContextError,
    fold,
    freshness,
    heading_name,
    idf,
    parse_date,
    parse_fields,
    parse_sections,
    refs_match,
    search_terms,
    source_rows,
    table_cells,
)

MAX_CONTEXT_BYTES = 512_000
SNIPPET_CHARS = 180
_FIELD_WEIGHTS = (
    ("slug", 3.0),
    ("heading", 4.0),
    ("aliases", 3.0),
    ("relevance", 2.0),
    ("body", 1.0),
    ("sources", 1.0),
)
_SKIP_SECTIONS = frozenset({"Sources inspected", "Revision history"})


def _entry_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ContextError(f"cannot read {path}: {exc.strerror or exc}") from exc


def _body(text: str) -> str:
    sections = parse_sections(text)
    parts = [value for name, value in sections.items()
             if name not in _SKIP_SECTIONS]
    preamble = text.split("\n## ", 1)[0]
    preamble = "\n".join(
        line for line in preamble.splitlines()
        if line.strip() and not line.lstrip().startswith("- **")
        and not line.startswith("#"))
    return "\n".join([preamble, *parts]).strip()


def _sources(text: str) -> str:
    rows = source_rows(parse_sections(text).get("Sources inspected", ""))
    return " ".join(table_cells(row)[0] for row in rows if table_cells(row))


def _snippet(value: str, terms: set[str]) -> str:
    if not value:
        return ""
    flat = " ".join(value.split())
    lowered = fold(flat)
    positions = [lowered.find(term) for term in terms if lowered.find(term) >= 0]
    start = max(0, (min(positions) if positions else 0) - 40)
    end = min(len(flat), start + SNIPPET_CHARS)
    prefix = "…" if start else ""
    suffix = "…" if end < len(flat) else ""
    return f"{prefix}{flat[start:end]}{suffix}"


def _status_value(fields: dict[str, str]) -> str:
    return fields.get("status", "").strip().casefold()


def find_contexts(root: Path, query: str, version: str = "", limit: int = 10,
                  today: str | None = None) -> dict:
    root = root.resolve()
    registry = root / REGISTRY_REL
    today_value = parse_date(today) if today else None
    if today and today_value is None:
        raise ContextError(f"--today must be a YYYY-MM-DD date: {today!r}")
    if not registry.exists():
        if registry.is_symlink():
            return {"registry": "invalid", "query": query, "results": [],
                    "drafts": [], "superseded": [],
                    "warnings": ["External context registry is a broken link."],
                    "message": "External context registry is a broken link; no entries were read."}
        return {"registry": "missing", "query": query, "results": [],
                "drafts": [], "superseded": [], "warnings": [],
                "message": "No external context registry is present."}
    try:
        registry_root = registry.resolve(strict=True)
        registry_root.relative_to(root)
    except (OSError, ValueError):
        return {"registry": "unsafe", "query": query, "results": [],
                "drafts": [], "superseded": [],
                "warnings": ["External context registry resolves outside the project root."],
                "message": "External context registry is unsafe; no entries were read."}
    if not registry_root.is_dir():
        return {"registry": "invalid", "query": query, "results": [],
                "drafts": [], "superseded": [],
                "warnings": ["External context registry path is not a directory."],
                "message": "External context registry is not a directory; no entries were read."}
    try:
        folders = sorted(registry.iterdir(), key=lambda path: path.name.casefold())
    except OSError as exc:
        return {"registry": "unreadable", "query": query, "results": [],
                "drafts": [], "superseded": [],
                "warnings": [f"External context registry could not be listed "
                             f"({type(exc).__name__})."],
                "message": "External context registry could not be listed; "
                           "no entries were read."}

    query_terms = search_terms(query)
    if not query_terms:
        return {"registry": "available", "query": query, "results": [],
                "drafts": [], "superseded": [], "warnings": [],
                "message": "The query has no content terms after template "
                           "vocabulary was removed; no entry was matched."}

    warnings: list[str] = []
    drafts: list[str] = []
    superseded: list[str] = []
    documents: list[dict] = []
    for folder in folders:
        if not folder.is_dir() or folder.name.startswith("."):
            continue
        path = folder / "context.md"
        try:
            resolved = path.resolve(strict=True)
        except FileNotFoundError:
            continue
        except (OSError, RuntimeError):
            warnings.append(f"Skipped unreadable entry: {folder.name}")
            continue
        try:
            resolved.relative_to(registry_root)
            if not resolved.is_file() or resolved.is_symlink():
                warnings.append(f"Skipped non-file entry: {folder.name}")
                continue
            size = resolved.stat().st_size
        except (OSError, ValueError):
            warnings.append(f"Skipped unsafe or unreadable entry: {folder.name}")
            continue
        if size > MAX_CONTEXT_BYTES:
            warnings.append(f"Skipped oversized entry: {folder.name}")
            continue
        try:
            text = _entry_text(resolved)
        except ContextError:
            warnings.append(f"Skipped unreadable entry: {folder.name}")
            continue
        if not text.strip():
            continue
        fields = parse_fields(text)
        heading = heading_name(text, folder.name)
        status = _status_value(fields)
        analyzed = fields.get("analyzed version/ref", "").strip()
        if not analyzed or analyzed.casefold() == "unknown" \
                or (analyzed.startswith("<") and analyzed.endswith(">")):
            analyzed = "unknown"
        document = {
            "path": resolved.relative_to(root).as_posix(),
            "slug": folder.name,
            "title": heading,
            "status": status,
            "analyzed": analyzed,
            "revalidate_after": fields.get("revalidate after", ""),
            "aliases": fields.get("aliases", ""),
            "relevance": fields.get("project relevance", ""),
            "text": text,
            "terms": {
                "slug": search_terms(folder.name.replace("-", " ")),
                "heading": search_terms(heading),
                "aliases": search_terms(fields.get("aliases", "")),
                "relevance": search_terms(fields.get("project relevance", "")),
                "body": search_terms(_body(text)),
                "sources": search_terms(_sources(text)),
            },
        }
        documents.append(document)

    document_frequency = {
        term: sum(1 for document in documents
                  if any(term in document["terms"][field]
                         for field, _weight in _FIELD_WEIGHTS))
        for term in query_terms
    }

    results: list[dict] = []
    for document in documents:
        matched: list[str] = []
        score = 0.0
        for term in sorted(query_terms):
            weight = 0.0
            for field, field_weight in _FIELD_WEIGHTS:
                if term in document["terms"][field]:
                    weight = max(weight, field_weight)
            if weight:
                matched.append(term)
                score += weight * idf(document_frequency.get(term, 1),
                                      len(documents))
        if not matched:
            continue
        status = document["status"]
        path = document["path"]
        if status == "draft":
            drafts.append(path)
            warnings.append(f"Draft entry matched but is not registered: {path}")
            continue
        if status == "superseded":
            superseded.append(path)
            warnings.append(f"Superseded entry matched and is not current: {path}")
            continue
        state = freshness(status, document["revalidate_after"], today_value)
        revalidate = state != "fresh"
        if status == "revalidating":
            warnings.append(f"Entry is being revalidated; its last reviewed "
                            f"analysis is stale: {path}")
        elif state == "stale":
            warnings.append(f"Entry is stale (Revalidate after has passed): {path}")
        results.append({
            "path": path,
            "title": document["title"],
            "score": round(score, 3),
            "matched_terms": matched,
            "snippet": _snippet(document["text"], set(matched)),
            "analyzed_version_ref": document["analyzed"],
            "status": status if status in ("reviewed", "revalidating", "stale")
                      else "unknown",
            "freshness": state,
            "revalidate_after": document["revalidate_after"] or "",
            "needs_revalidation": revalidate,
            "version_match": (None if not version or document["analyzed"] == "unknown"
                              else refs_match(version, document["analyzed"])),
        })

    results.sort(key=lambda item: (-item["score"], item["path"].casefold()))
    return {"registry": "available", "query": query,
            "query_terms": sorted(query_terms),
            "results": results[:limit], "drafts": drafts,
            "superseded": superseded, "warnings": warnings,
            "message": "No matching registered context." if not results else ""}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--query", required=True, help="Technology or project topic")
    parser.add_argument("--version", default="",
                        help="Version/ref used by this project (semver range, tag or commit)")
    parser.add_argument("--max-results", type=int, default=10)
    parser.add_argument("--today", default="",
                        help="Override today's date (YYYY-MM-DD) for deterministic freshness")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args()
    if not args.query.strip() or not 1 <= args.max_results <= 50:
        parser.error("--query must be non-empty and --max-results must be from 1 to 50")
    try:
        result = find_contexts(args.project_root, args.query, args.version,
                               args.max_results, args.today or None)
    except ContextError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result.get("message") or f"Matching external contexts for: {args.query}")
        for item in result.get("results", []):
            notes = [f"analyzed {item['analyzed_version_ref']}",
                     f"status {item['status']}",
                     f"freshness {item['freshness']}"]
            if item["version_match"] is False:
                notes.append("VERSION MISMATCH: revalidate primary sources")
            if item["needs_revalidation"]:
                notes.append("NEEDS REVALIDATION")
            print(f"- {item['path']} ({', '.join(notes)})")
            if item.get("matched_terms"):
                print(f"  matched: {', '.join(item['matched_terms'])}")
            if item.get("snippet"):
                print(f"  {item['snippet']}")
        for warning in result.get("warnings", []):
            print(f"Warning: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Find reusable external-technology context entries for a workflow topic."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

MAX_CONTEXT_BYTES = 512_000


def _terms(value: str) -> set[str]:
    return {part.casefold() for part in re.findall(r"[\w.+#-]+", value) if len(part) > 1}


def _normalize_ref(value: str) -> str:
    normalized = value.strip()
    if re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", normalized):
        return normalized.casefold()
    return normalized[1:] if re.match(r"^v(?=\d)", normalized) else normalized


def _same_ref(requested: str, analyzed: str) -> bool:
    """Preserve ref case; normalize lowercase v and hexadecimal commit IDs only."""
    return bool(requested and analyzed and _normalize_ref(requested) == _normalize_ref(analyzed))


def _field_value(line: str) -> str:
    """Value of a `- **Label:** value` metadata bullet — never the label itself.

    Template labels (Project relevance, Aliases, ...) are navigation words, not
    content: scoring them made every registered entry match a query containing
    "project" or "relevance". A bullet without a colon keeps its text.
    """
    value = line.partition(":**")[2].strip()
    return value if value else line


def _analyzed_ref(metadata: str) -> str:
    """Extract a concrete analyzed ref, treating absent/template values as unknown."""
    value = metadata.partition(":**")[2].strip() if metadata else ""
    if (not value or value.casefold() == "unknown" or
            (value.startswith("<") and value.endswith(">"))):
        return "unknown"
    return value


def find_contexts(root: Path, query: str, version: str = "", limit: int = 10) -> dict:
    root = root.resolve()
    registry = root / "docs/research/external"
    results = []
    drafts = []
    warnings = []
    if not registry.exists():
        if registry.is_symlink():
            return {"registry": "invalid", "query": query, "results": [],
                    "warnings": ["External context registry is a broken link."],
                    "message": "External context registry is a broken link; no entries were read."}
        return {"registry": "missing", "query": query, "results": [],
                "message": "No external context registry is present."}
    try:
        registry_root = registry.resolve(strict=True)
        registry_root.relative_to(root)
    except (OSError, ValueError):
        return {"registry": "unsafe", "query": query, "results": [],
                "warnings": ["External context registry resolves outside the project root."],
                "message": "External context registry is unsafe; no entries were read."}
    if not registry_root.is_dir():
        return {"registry": "invalid", "query": query, "results": [],
                "warnings": ["External context registry path is not a directory."],
                "message": "External context registry is not a directory; no entries were read."}
    try:
        folders = sorted(registry.iterdir(), key=lambda p: p.name.casefold())
    except OSError as exc:
        return {"registry": "unreadable", "query": query, "results": [],
                "warnings": [f"External context registry could not be listed "
                             f"({type(exc).__name__})."],
                "message": "External context registry could not be listed; "
                           "no entries were read."}
    query_terms = _terms(query)
    for folder in folders:
        if not folder.is_dir():
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
            if not resolved.is_file():
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
            text = resolved.read_text(encoding="utf-8", errors="replace")
        except OSError:
            warnings.append(f"Skipped unreadable entry: {folder.name}")
            continue
        if not text.strip():
            continue
        # Score searchable identity/relevance fields more heavily than body mentions.
        heading = next((line.lstrip("# ").strip() for line in text.splitlines()
                        if line.startswith("# ")), folder.name)
        relevance = " ".join(_field_value(line) for line in text.splitlines()
                             if line.startswith("- **Project relevance:**") or
                             line.startswith("- **Aliases:**"))
        metadata = " ".join(line for line in text.splitlines()
                            if line.startswith("- **Analyzed version/ref:**"))
        status_value = next(
            (line.partition(":**")[2].strip() for line in text.splitlines()
             if line.startswith("- **Status:**")), "").casefold()
        # Metadata bullets enter the haystack by VALUE only: the labels are
        # template vocabulary, not searchable content (E-083).
        body = "\n".join(_field_value(line) if line.lstrip().startswith("- **") else line
                          for line in text.splitlines())
        haystack = _terms(folder.name.replace("-", " ") + " " + heading + " " +
                          relevance + " " + body)
        score = sum(3 if term in _terms(folder.name + " " + heading) else
                    2 if term in _terms(relevance) else 1
                    for term in query_terms if term in haystack)
        if score == 0:
            continue
        analyzed = _analyzed_ref(metadata)
        rel_path = resolved.relative_to(root).as_posix()
        # `finalize` is what registers an entry; `check` fails a draft. The
        # consumer half must agree: an explicitly unreviewed scaffold is
        # reported as a draft, never handed to a workflow as registered
        # context (E-088). An entry with no Status field keeps the shape the
        # discovery contract pins and is returned as `unknown`.
        if status_value == "draft":
            drafts.append(rel_path)
            warnings.append(f"Draft entry matched but is not registered: {rel_path}")
            continue
        results.append({"path": rel_path, "title": heading,
                        "score": score, "analyzed_version_ref": analyzed or "unknown",
                        "status": "reviewed" if status_value == "reviewed" else "unknown",
                        "version_match": (None if not version or analyzed == "unknown"
                                          else _same_ref(version, analyzed))})
    results.sort(key=lambda item: (-item["score"], item["path"].casefold()))
    return {"registry": "available", "query": query, "results": results[:limit],
            "drafts": drafts,
            "warnings": warnings,
            "message": "No matching registered context." if not results else ""}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--query", required=True, help="Technology or project topic")
    parser.add_argument("--version", default="", help="Version/ref used by this project")
    parser.add_argument("--max-results", type=int, default=10)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args()
    if not args.query.strip() or not 1 <= args.max_results <= 50:
        parser.error("--query must be non-empty and --max-results must be from 1 to 50")
    result = find_contexts(args.project_root, args.query, args.version, args.max_results)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result["message"] or f"Matching external contexts for: {args.query}")
        for item in result["results"]:
            version_note = f" (analyzed {item['analyzed_version_ref']})"
            if item["version_match"] is False:
                version_note += " [VERSION MISMATCH: revalidate primary sources]"
            print(f"- {item['path']}{version_note}")
        for warning in result["warnings"]:
            print(f"Warning: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

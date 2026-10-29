import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "find-external-contexts.py"
SPEC = importlib.util.spec_from_file_location("find_external_contexts", SCRIPT)
finder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(finder)


def _entry(root: Path, slug: str, title: str, relevance: str, version: str = "1.2",
            status: str = ""):
    path = root / "docs/research/external" / slug / "context.md"
    path.parent.mkdir(parents=True)
    status_line = f"- **Status:** {status}\n" if status else ""
    path.write_text(f"# {title}\n\n{status_line}- **Analyzed version/ref:** {version}\n"
                    f"- **Project relevance:** {relevance}\n", encoding="utf-8")
    return path


def test_finds_relevant_entry_deterministically_and_reports_version_mismatch(tmp_path):
    _entry(tmp_path, "mastra", "Mastra Framework", "AI agent runtime", "0.10.0")
    _entry(tmp_path, "unrelated", "Unrelated Tool", "database migrations")
    result = finder.find_contexts(tmp_path, "Mastra AI agent", version="0.11.0")
    assert [row["path"] for row in result["results"]] == [
        "docs/research/external/mastra/context.md"]
    assert result["results"][0]["version_match"] is False


def test_version_match_requires_exact_ref_and_accepts_leading_v(tmp_path):
    _entry(tmp_path, "mastra", "Mastra Framework", "AI agent runtime", "11.2.0")
    result = finder.find_contexts(tmp_path, "Mastra", version="1.2")
    assert result["results"][0]["version_match"] is False

    context = tmp_path / "docs/research/external/mastra/context.md"
    context.write_text(context.read_text(encoding="utf-8").replace("11.2.0", "v1.2.3"),
                       encoding="utf-8")
    equal = finder.find_contexts(tmp_path, "Mastra", version="1.2.3")
    assert equal["results"][0]["version_match"] is True


def test_version_match_preserves_prerelease_and_tag_case_but_normalizes_full_hash(tmp_path):
    context = _entry(tmp_path, "mastra", "Mastra Framework", "AI agent runtime",
                     "1.2.3-Alpha")
    assert finder.find_contexts(tmp_path, "Mastra", version="1.2.3-alpha")[
        "results"][0]["version_match"] is False
    context.write_text(context.read_text(encoding="utf-8").replace(
        "1.2.3-Alpha", "Release-Alpha"), encoding="utf-8")
    assert finder.find_contexts(tmp_path, "Mastra", version="release-alpha")[
        "results"][0]["version_match"] is False
    commit = "a" * 40
    context.write_text(context.read_text(encoding="utf-8").replace(
        "Release-Alpha", commit), encoding="utf-8")
    assert finder.find_contexts(tmp_path, "Mastra", version=commit.upper())[
        "results"][0]["version_match"] is True


def test_unknown_or_template_analyzed_ref_is_not_a_known_mismatch(tmp_path):
    context = tmp_path / "docs/research/external/mastra/context.md"
    context.parent.mkdir(parents=True)
    for metadata in ("", "- **Analyzed version/ref:** unknown\n",
                     "- **Analyzed version/ref:** <release, tag, commit, or unknown>\n"):
        context.write_text("# Mastra\n" + metadata +
                           "- **Project relevance:** AI agents\n", encoding="utf-8")
        result = finder.find_contexts(tmp_path, "Mastra", version="1.2.3")
        assert result["results"][0]["analyzed_version_ref"] == "unknown"
        assert result["results"][0]["version_match"] is None


def test_template_field_labels_are_not_searchable_content(tmp_path):
    _entry(tmp_path, "mastra", "Mastra Framework", "AI agent runtime")
    for query in ("relevance", "aliases", "analyzed", "revalidate",
                  "project relevance"):
        assert finder.find_contexts(tmp_path, query)["results"] == []
    for query in ("runtime", "Mastra"):
        results = finder.find_contexts(tmp_path, query)["results"]
        assert [row["path"] for row in results] == [
            "docs/research/external/mastra/context.md"]


def test_field_values_and_body_mentions_remain_searchable(tmp_path):
    context = _entry(tmp_path, "ai-sdk", "AI SDK", "model calls")
    context.write_text(context.read_text(encoding="utf-8") +
                       "\n- **Aliases:** sdk-alias\n\nAlternative to Mastra runtime.\n",
                       encoding="utf-8")
    assert finder.find_contexts(tmp_path, "sdk-alias")["results"][0]["path"] == \
        "docs/research/external/ai-sdk/context.md"
    assert finder.find_contexts(tmp_path, "Mastra")["results"][0]["path"] == \
        "docs/research/external/ai-sdk/context.md"


def test_missing_registry_is_a_valid_empty_result(tmp_path):
    result = finder.find_contexts(tmp_path, "Mastra")
    assert result["registry"] == "missing"
    assert result["results"] == []


def test_file_at_registry_path_is_reported_not_raised(tmp_path):
    registry = tmp_path / "docs/research/external"
    registry.parent.mkdir(parents=True)
    registry.write_text("not a directory", encoding="utf-8")
    result = finder.find_contexts(tmp_path, "Mastra")
    assert result["registry"] == "invalid"
    assert result["results"] == []
    assert any("not a directory" in warning for warning in result["warnings"])


def test_cli_reports_a_file_registry_as_json_without_failing(tmp_path):
    registry = tmp_path / "docs/research/external"
    registry.parent.mkdir(parents=True)
    registry.write_text("not a directory", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(tmp_path), "--query",
         "Mastra", "--json"], stdin=subprocess.DEVNULL, capture_output=True,
        text=True, check=False, timeout=10)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["registry"] == "invalid"
    assert payload["results"] == []


def test_unlistable_registry_is_reported_not_raised(tmp_path):
    if os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0):
        pytest.skip("permission bits cannot make a directory unlistable here")
    registry = tmp_path / "docs/research/external"
    registry.mkdir(parents=True)
    registry.chmod(0)
    try:
        result = finder.find_contexts(tmp_path, "Mastra")
    finally:
        registry.chmod(0o700)
    assert result["registry"] == "unreadable"
    assert result["results"] == []
    assert any("could not be listed" in warning for warning in result["warnings"])


def test_broken_registry_link_is_invalid_not_missing(tmp_path):
    registry = tmp_path / "docs/research/external"
    registry.parent.mkdir(parents=True)
    registry.symlink_to(tmp_path / "absent-registry", target_is_directory=True)
    result = finder.find_contexts(tmp_path, "Mastra")
    assert result["registry"] == "invalid"
    assert any("broken link" in warning for warning in result["warnings"])


def test_oversized_entry_warns_and_keeps_a_readable_sibling(tmp_path):
    _entry(tmp_path, "good", "Good Context", "agents")
    oversized = _entry(tmp_path, "oversized", "Oversized Context", "agents")
    oversized.write_text("x" * (finder.MAX_CONTEXT_BYTES + 1), encoding="utf-8")
    result = finder.find_contexts(tmp_path, "Context")
    assert [row["path"] for row in result["results"]] == [
        "docs/research/external/good/context.md"]
    assert any("oversized" in warning for warning in result["warnings"])


def test_unreadable_entry_warns_and_keeps_a_readable_sibling(tmp_path):
    if os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0):
        pytest.skip("permission bits cannot make a file unreadable in this environment")
    _entry(tmp_path, "good", "Good Context", "agents")
    unreadable = _entry(tmp_path, "unreadable", "Unreadable Context", "agents")
    unreadable.chmod(0)
    try:
        result = finder.find_contexts(tmp_path, "Context")
    finally:
        unreadable.chmod(0o600)
    assert [row["path"] for row in result["results"]] == [
        "docs/research/external/good/context.md"]
    assert any("unreadable" in warning for warning in result["warnings"])


def test_registry_symlink_cannot_escape_project_root(tmp_path):
    external = tmp_path.parent / (tmp_path.name + "-external")
    _entry(external, "mastra", "Mastra", "agents")
    registry_link = tmp_path / "docs/research/external"
    registry_link.parent.mkdir(parents=True)
    registry_link.symlink_to(external / "docs/research/external", target_is_directory=True)
    result = finder.find_contexts(tmp_path, "Mastra")
    assert result["registry"] == "unsafe"
    assert result["results"] == []


def test_symlink_cannot_escape_registry(tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("# Mastra\n- **Project relevance:** agents\n")
    registry = tmp_path / "docs/research/external"
    registry.mkdir(parents=True)
    (registry / "mastra").symlink_to(outside)
    assert finder.find_contexts(tmp_path, "Mastra")["results"] == []


def test_results_are_bounded_and_ties_are_sorted_by_relative_path(tmp_path):
    _entry(tmp_path, "zeta", "Agent runtime", "agent")
    _entry(tmp_path, "alpha", "Agent runtime", "agent")
    result = finder.find_contexts(tmp_path, "agent", limit=1)
    assert len(result["results"]) == 1
    assert result["results"][0]["path"].endswith("alpha/context.md")


def test_cli_json_reports_relative_path_and_version_mismatch(tmp_path):
    _entry(tmp_path, "mastra", "Mastra Framework", "AI agent runtime", "0.10.0")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(tmp_path), "--query",
         "Mastra agent", "--version", "0.11.0", "--json"],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False, timeout=10)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["results"][0]["path"] == "docs/research/external/mastra/context.md"
    assert payload["results"][0]["version_match"] is False


def test_cli_json_missing_registry_and_invalid_bounds(tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(tmp_path), "--query",
         "Mastra", "--json"], stdin=subprocess.DEVNULL, capture_output=True,
        text=True, check=False, timeout=10)
    assert result.returncode == 0
    assert json.loads(result.stdout)["registry"] == "missing"

    invalid = subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(tmp_path), "--query",
         "Mastra", "--max-results", "0", "--json"],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False, timeout=10)
    assert invalid.returncode != 0
    assert "--max-results" in invalid.stderr


def test_a_draft_is_reported_but_never_returned_as_registered_context(tmp_path):
    """E-088: discovery agrees with `check` about what is registered."""
    _entry(tmp_path, "reviewed-lib", "Reviewed Lib", "AI agent runtime",
           status="reviewed")
    _entry(tmp_path, "draft-lib", "Draft Lib", "AI agent runtime", status="draft")
    result = finder.find_contexts(tmp_path, "AI agent runtime")
    assert [row["path"] for row in result["results"]] == [
        "docs/research/external/reviewed-lib/context.md"]
    assert result["results"][0]["status"] == "reviewed"
    assert result["drafts"] == ["docs/research/external/draft-lib/context.md"]
    assert any("Draft entry" in warning and "draft-lib" in warning
               for warning in result["warnings"])


def test_a_statusless_entry_still_returns_with_unknown_status(tmp_path):
    """E-088: entries the contract pins without a Status field stay discoverable."""
    _entry(tmp_path, "mastra", "Mastra Framework", "AI agent runtime")
    result = finder.find_contexts(tmp_path, "Mastra")
    assert result["results"][0]["status"] == "unknown"
    assert result["drafts"] == [] and result["warnings"] == []


def test_a_draft_on_an_unrelated_query_is_not_reported(tmp_path):
    """E-088: drafts surface only when they match the query."""
    _entry(tmp_path, "mastra", "Mastra Framework", "AI agent runtime",
           status="reviewed")
    _entry(tmp_path, "migration-tool", "Migration Tool", "database migrations",
           status="draft")
    result = finder.find_contexts(tmp_path, "AI agent runtime")
    assert [row["path"] for row in result["results"]] == [
        "docs/research/external/mastra/context.md"]
    assert result["drafts"] == [] and result["warnings"] == []

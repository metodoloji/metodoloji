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

GOLDEN = json.loads(
    (Path(__file__).resolve().parent / "fixtures" /
     "external_context_golden.json").read_text(encoding="utf-8"))


def _entry(root: Path, slug: str, title: str, relevance: str, version: str = "1.2",
           status: str = "", body: str = "", aliases: str = "",
           revalidate_after: str = ""):
    path = root / "docs/research/external" / slug / "context.md"
    path.parent.mkdir(parents=True)
    status_line = f"- **Status:** {status}\n" if status else ""
    alias_line = f"- **Aliases:** {aliases}\n" if aliases else ""
    revalidate_line = (f"- **Revalidate after:** {revalidate_after}\n"
                       if revalidate_after else "")
    path.write_text(f"# {title}\n\n{status_line}- **Analyzed version/ref:** {version}\n"
                    f"{alias_line}{revalidate_line}"
                    f"- **Project relevance:** {relevance}\n"
                    + (f"\n{body}\n" if body else ""), encoding="utf-8")
    return path


def _golden_project(tmp_path: Path) -> Path:
    for entry in GOLDEN["entries"]:
        _entry(tmp_path, entry["slug"], entry["title"], entry["relevance"],
               status=entry["status"], body=entry["body"],
               aliases=entry["aliases"], revalidate_after="2099-01-01")
    return tmp_path


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


def test_version_match_understands_ranges_prefixes_and_compound_refs(tmp_path):
    """F-10: semver ranges, bare minor lines, commit prefixes and compound refs."""
    commit = "a" * 40
    context = _entry(tmp_path, "mastra", "Mastra Framework", "AI agent runtime",
                     "1.2.3")
    cases = (
        ("^1.2.0", True), ("~1.2", True), ("1.2", True), ("1.2.x", True),
        (">=1.0.0 <2.0.0", True), ("^2.0.0", False), ("11.2", False),
    )
    for requested, expected in cases:
        assert finder.find_contexts(tmp_path, "Mastra", version=requested)[
            "results"][0]["version_match"] is expected, requested
    context.write_text(context.read_text(encoding="utf-8").replace(
        "1.2.3", "3.0.0"), encoding="utf-8")
    assert finder.find_contexts(tmp_path, "Mastra", version=">=1.0.0 <2.0.0")[
        "results"][0]["version_match"] is False
    context.write_text(context.read_text(encoding="utf-8").replace(
        "3.0.0", "1.2.3"), encoding="utf-8")
    context.write_text(context.read_text(encoding="utf-8").replace(
        "1.2.3", commit), encoding="utf-8")
    assert finder.find_contexts(tmp_path, "Mastra", version=commit[:7])[
        "results"][0]["version_match"] is True
    context.write_text(context.read_text(encoding="utf-8").replace(
        commit, "v1.2.3 (commit " + commit[:12] + ")"), encoding="utf-8")
    assert finder.find_contexts(tmp_path, "Mastra", version="v1.2.3")[
        "results"][0]["version_match"] is True
    assert finder.find_contexts(tmp_path, "Mastra", version=commit[:8])[
        "results"][0]["version_match"] is True


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
                  "project relevance", "context", "reviewed"):
        assert finder.find_contexts(tmp_path, query)["results"] == [], query
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


def test_results_report_matched_terms_snippet_and_idf_weighting(tmp_path):
    """F-08: results explain themselves and shared terms cannot dominate."""
    _entry(tmp_path, "mastra", "Mastra Framework", "AI agent orchestration",
           body="Agents run workflows; agents call tools.")
    _entry(tmp_path, "drizzle", "Drizzle ORM", "SQL access",
           body="A database toolkit used by agents in tests.")
    result = finder.find_contexts(tmp_path, "agents orchestration")
    assert result["results"][0]["path"].endswith("mastra/context.md")
    assert "orchestration" in result["results"][0]["matched_terms"]
    assert result["results"][0]["snippet"]
    assert result["results"][0]["score"] > 0
    assert result["query_terms"] == ["agents", "orchestration"]


def test_missing_registry_is_a_valid_empty_result(tmp_path):
    result = finder.find_contexts(tmp_path, "Mastra")
    assert result["registry"] == "missing"
    assert result["results"] == []
    assert result["warnings"] == []


def test_cli_text_mode_handles_a_missing_registry_without_crashing(tmp_path):
    """F-02: the human-readable path must not index a missing `warnings` key."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(tmp_path),
         "--query", "Mastra"], stdin=subprocess.DEVNULL, capture_output=True,
        text=True, check=False, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "No external context registry is present." in result.stdout
    assert "Traceback" not in result.stderr


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
    try:
        registry.symlink_to(tmp_path / "absent-registry", target_is_directory=True)
    except OSError:
        pytest.skip("creating a symlink requires a privilege here")
    result = finder.find_contexts(tmp_path, "Mastra")
    assert result["registry"] == "invalid"
    assert any("broken link" in warning for warning in result["warnings"])


def test_oversized_entry_warns_and_keeps_a_readable_sibling(tmp_path):
    _entry(tmp_path, "good", "Good Context", "agents")
    oversized = _entry(tmp_path, "oversized", "Oversized Context", "agents")
    oversized.write_text("x" * (finder.MAX_CONTEXT_BYTES + 1), encoding="utf-8")
    result = finder.find_contexts(tmp_path, "agents")
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
        result = finder.find_contexts(tmp_path, "agents")
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
    try:
        registry_link.symlink_to(external / "docs/research/external",
                                 target_is_directory=True)
    except OSError:
        pytest.skip("creating a directory symlink requires a privilege here")
    result = finder.find_contexts(tmp_path, "Mastra")
    assert result["registry"] == "unsafe"
    assert result["results"] == []


def test_symlink_cannot_escape_registry(tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("# Mastra\n- **Project relevance:** agents\n")
    registry = tmp_path / "docs/research/external"
    registry.mkdir(parents=True)
    try:
        (registry / "mastra").symlink_to(outside)
    except OSError:
        pytest.skip("creating a symlink requires a privilege here")
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


def test_revalidating_and_stale_entries_stay_visible_but_are_flagged(tmp_path):
    """F-03/F-11/F-12: a known analysis is not hidden; it is marked not current."""
    _entry(tmp_path, "reviewed", "Reviewed Lib", "AI agent runtime",
           status="reviewed", revalidate_after="2099-01-01")
    _entry(tmp_path, "revalidating", "Revalidating Lib", "AI agent runtime",
           status="revalidating", revalidate_after="2099-01-01")
    _entry(tmp_path, "stale", "Stale Lib", "AI agent runtime",
           status="reviewed", revalidate_after="2020-01-01")
    _entry(tmp_path, "superseded", "Superseded Lib", "AI agent runtime",
           status="superseded")
    result = finder.find_contexts(tmp_path, "AI agent runtime", today="2026-10-30")
    by_slug = {row["path"].split("/")[-2]: row for row in result["results"]}
    assert set(by_slug) == {"reviewed", "revalidating", "stale"}
    assert by_slug["reviewed"]["freshness"] == "fresh"
    assert by_slug["reviewed"]["needs_revalidation"] is False
    assert by_slug["revalidating"]["status"] == "revalidating"
    assert by_slug["revalidating"]["needs_revalidation"] is True
    assert by_slug["stale"]["freshness"] == "stale"
    assert by_slug["stale"]["needs_revalidation"] is True
    assert result["superseded"] == [
        "docs/research/external/superseded/context.md"]


def test_turkish_dotted_and_dotless_i_fold_together(tmp_path):
    """F-25: `KİMLİK`, `kimlik` and `KIMLIK` are the same search term."""
    _entry(tmp_path, "kimlik", "Kimlik Servisi", "kimlik doğrulama ve oturum")
    for query in ("KİMLİK", "kimlik", "KIMLIK", "Kimlik"):
        assert [row["path"] for row in finder.find_contexts(tmp_path, query)["results"]] == [
            "docs/research/external/kimlik/context.md"], query


def test_invalid_today_is_rejected_not_silently_ignored(tmp_path):
    """E-092: an explicit date override is validated, not ignored."""
    _entry(tmp_path, "mastra", "Mastra", "agents")
    with pytest.raises(finder.ContextError):
        finder.find_contexts(tmp_path, "Mastra", today="not-a-date")


def test_cli_rejects_an_invalid_today(tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(tmp_path),
         "--query", "Mastra", "--today", "not-a-date", "--json"],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False,
        timeout=10)
    assert result.returncode == 2
    assert "--today" in result.stderr


def test_golden_retrieval_set_meets_precision_and_recall(tmp_path):
    """F-28: a fixed corpus/query set measures retrieval quality, not just shape."""
    _golden_project(tmp_path)
    hits = 0
    misses = []
    for case in GOLDEN["queries"]:
        results = finder.find_contexts(tmp_path, case["query"])["results"]
        top = results[0]["path"].split("/")[-2] if results else None
        if top == case["expected"]:
            hits += 1
        else:
            misses.append((case["query"], top, case["expected"]))
    assert hits / len(GOLDEN["queries"]) >= 0.9, misses

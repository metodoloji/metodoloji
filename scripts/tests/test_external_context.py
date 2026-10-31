import importlib.util
import json
import os
import re
from datetime import date
from pathlib import Path
import subprocess
import sys

import pytest

PLUGIN = Path(__file__).resolve().parents[2]
SCRIPT = PLUGIN / "scripts" / "external-context.py"
SPEC = importlib.util.spec_from_file_location("external_context", SCRIPT)
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)

TEMPLATE = (PLUGIN / "templates" / "external-context.md").read_text(encoding="utf-8")
README_TEMPLATE = (PLUGIN / "templates" / "external-context-README.md").read_text(
    encoding="utf-8")


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    registry = root / "docs/research/external"
    registry.mkdir(parents=True)
    (registry / "_template.md").write_text(TEMPLATE, encoding="utf-8")
    (registry / "README.md").write_text(README_TEMPLATE, encoding="utf-8")
    return root


def _dir_link(link: Path, target: Path) -> bool:
    """A directory symlink, or a Windows junction when symlinks need a privilege."""
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(target, target_is_directory=True)
        return True
    except OSError:
        pass
    if sys.platform == "win32":
        # Explicit DEVNULL handles: under pytest's capture the inherited
        # standard handles can be closed, and subprocess then fails before
        # `cmd` runs (WinError 6).
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        return result.returncode == 0
    return False


def _complete(root: Path, name: str = "Mastra", status: str = "reviewed") -> Path:
    entry = root / "docs/research/external" / tool.slugify(name) / "context.md"
    if not entry.exists():
        prepared = tool.prepare(root, "https://github.com/mastra-ai/mastra", name=name)
        entry = root / prepared["entry"]
    text = entry.read_text(encoding="utf-8")
    values = {
        "Status": status,
        "Analyzed version/ref": "v0.31.0",
        "Revalidate when": "a new minor release is published",
        "Revalidate after": "2099-01-01",
        "Last reviewed on": "2026-10-30",
        "Reviewer": "test-reviewer",
        "Project relevance": "AI agent runtime for the research workflow",
        "Aliases": "mastra-ai",
    }
    for label, value in values.items():
        text = tool._set_field(text, label, value)
    for label in tool.INTEGRATION_BULLETS:
        text = tool._set_field(text, label, "observed in the upstream documentation")
    text = tool._set_field(text, "Known gaps", "none observed")
    text = text.replace(
        "| <ref>; ", "| v0.31.0; ").replace(
        "| <what was inspected> |", "| README, docs and examples |")
    text = text.replace(
        "| YYYY-MM-DD | <ref> | initial analysis | <reviewer> |",
        "| 2026-10-30 | v0.31.0 | initial analysis | test-reviewer |")
    filler = ("Observed evidence recorded during the analysis pass; "
              "see docs/research/external for the registry.")
    text = re.sub(r"<[^<>\n]+>", filler, text)
    entry.write_text(text, encoding="utf-8")
    return entry


def test_slugify_derives_a_stable_registry_folder():
    assert tool.slugify("Mastra AI") == "mastra-ai"
    assert tool.slugify("  C++ Builder  ") == "c-builder"
    assert tool.slugify("!!!") == "external-context"


def test_prepare_repository_url_scaffolds_but_does_not_index(tmp_path):
    root = _project(tmp_path)
    result = tool.prepare(root, "https://github.com/mastra-ai/mastra",
                          name="Mastra Framework")
    assert result["ok"] is True
    assert result["slug"] == "mastra-framework"
    assert result["source_kind"] == "repository"
    entry = root / result["entry"]
    text = entry.read_text(encoding="utf-8")
    assert text.startswith("# Mastra Framework Context")
    assert "- **Status:** draft" in text
    assert "- **Schema:** 2" in text
    assert "| https://github.com/mastra-ai/mastra | repository |" in text
    assert "Analyzed on:** 20" in text
    assert "No external contexts registered yet." in (
        root / "docs/research/external/README.md").read_text(encoding="utf-8")


def test_prepare_classifies_docs_urls_and_local_sources(tmp_path):
    root = _project(tmp_path)
    docs = tool.prepare(root, "https://mastra.ai/docs")
    assert docs["source_kind"] == "official docs"
    assert docs["name"] == "mastra"
    pdf = tmp_path / "handbook.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    supplied = tool.prepare(root, str(pdf))
    assert supplied["source_kind"] == "supplied file"
    assert supplied["slug"] == "handbook"
    folder = tmp_path / "supplied-docs"
    folder.mkdir()
    assert tool.prepare(root, str(folder))["source_kind"] == "supplied path"


def test_prepare_derives_meaningful_names_for_issue_urls_domains_and_records(tmp_path):
    """F-19: GitHub issues, multi-label domains and record files never name an entry '3'."""
    root = _project(tmp_path)
    issue = tool.prepare(root, "https://github.com/mastra-ai/mastra/issues/3")
    assert issue["name"] == "mastra"
    assert issue["slug"] == "mastra"
    docs = tool.prepare(root, "https://example.co.uk/docs")
    assert docs["name"] == "example"
    record = tmp_path / "retrieval" / "E-001.md"
    record.parent.mkdir()
    record.write_text("# E-001\n", encoding="utf-8")
    derived = tool.prepare(root, str(record))
    assert derived["name"] == "retrieval"


def test_prepare_rejects_missing_and_unsupported_sources(tmp_path):
    root = _project(tmp_path)
    with pytest.raises(tool.ContextError):
        tool.prepare(root, str(tmp_path / "absent.md"))
    with pytest.raises(tool.ContextError):
        tool.prepare(root, "ftp://example.com/thing")
    with pytest.raises(tool.ContextError):
        tool.prepare(root, "   ")


def test_prepare_refuses_to_overwrite_an_existing_entry(tmp_path):
    root = _project(tmp_path)
    first = tool.prepare(root, "https://github.com/mastra-ai/mastra")
    entry = root / first["entry"]
    before = entry.read_text(encoding="utf-8")
    with pytest.raises(tool.ContextError, match="already registered"):
        tool.prepare(root, "https://github.com/mastra-ai/mastra")
    assert entry.read_text(encoding="utf-8") == before


def test_prepare_rejects_duplicate_alias_and_duplicate_source(tmp_path):
    """F-20: a second slug for the same name/source splits the evidence."""
    root = _project(tmp_path)
    tool.prepare(root, "https://github.com/mastra-ai/mastra", name="Mastra")
    with pytest.raises(tool.ContextError, match="already registered"):
        tool.prepare(root, "https://mastra.ai/docs", name="Mastra")
    with pytest.raises(tool.ContextError, match="already registered"):
        tool.prepare(root, "https://github.com/mastra-ai/mastra", name="Other")


def test_source_identity_ignores_spelling_that_names_the_same_resource(tmp_path):
    """E-094: one resource has one identity, whatever its harmless spelling."""
    root = _project(tmp_path)
    local = root / "notes.md"
    local.write_text("# notes\n", encoding="utf-8")
    assert tool.source_key(
        "https://github.com/mastra-ai/mastra", root
    ) == tool.source_key("https://GitHub.com/mastra-ai/mastra.git#readme", root)
    assert tool.source_key("https://mastra.ai/docs#top", root) == tool.source_key(
        "https://mastra.ai/docs/", root)
    assert tool.source_key("https://mastra.ai:443/docs", root) == tool.source_key(
        "https://mastra.ai/docs", root)
    assert tool.source_key("./notes.md", root) == tool.source_key(str(local), root)
    # Path and query case can name a different page, so it must stay distinct.
    assert tool.source_key("https://mastra.ai/docs/v1", root) != tool.source_key(
        "https://mastra.ai/docs/v2", root)


def test_prepare_refuses_the_same_source_written_differently(tmp_path):
    """E-094: a second entry would split the evidence for one source."""
    root = _project(tmp_path)
    tool.prepare(root, "https://github.com/mastra-ai/mastra")
    for spelling in (
            "https://github.com/mastra-ai/mastra.git",
            "https://github.com/mastra-ai/mastra/#readme",
            "https://GitHub.com/mastra-ai/mastra",
            "https://github.com:443/mastra-ai/mastra"):
        with pytest.raises(tool.ContextError, match="already registered"):
            tool.prepare(root, spelling, name="Second")
    local = tmp_path / "notes.md"
    local.write_text("# notes\n", encoding="utf-8")
    tool.prepare(root, str(local), name="Notes")
    with pytest.raises(tool.ContextError, match="already registered"):
        tool.prepare(root, str(Path(root) / ".." / "notes.md"), name="Other")


def test_prepare_stores_the_canonical_source_spelling(tmp_path):
    root = _project(tmp_path)
    result = tool.prepare(root, "https://mastra.ai/docs/#intro")
    text = (root / result["entry"]).read_text(encoding="utf-8")
    assert "| https://mastra.ai/docs | official docs |" in text
    assert "#intro" not in text


def test_check_reports_a_source_registered_twice(tmp_path):
    """E-094: an entry written before the canonical spelling is still caught."""
    root = _project(tmp_path)
    tool.prepare(root, "https://mastra.ai/docs", name="Mastra")
    second = tool.prepare(root, "https://example.com/guide", name="Guide")
    entry = root / second["entry"]
    entry.write_text(entry.read_text(encoding="utf-8").replace(
        "https://example.com/guide", "https://mastra.ai/docs/#intro"),
        encoding="utf-8")
    result = tool.check(root)
    assert result["ok"] is False
    duplicates = [finding for item in result["findings"]
                  for finding in item["findings"]
                  if "is also registered by" in finding]
    assert duplicates and any(name in duplicates[0] for name in
                              ("mastra/context.md", "guide/context.md"))


def test_prepare_is_safe_for_windows_paths_and_regex_escapes(tmp_path):
    """F-01: a Windows path must not reach re.sub as a replacement template."""
    root = _project(tmp_path)
    result = tool.prepare(root, "https://github.com/mastra-ai/mastra")
    text = (root / result["entry"]).read_text(encoding="utf-8")
    assert "\\U" not in text
    assert "C:\\Users\\ASUS" in tool._set_field(
        "- **Status:** draft\n", "Status", "C:\\Users\\ASUS\\project")
    if os.name == "nt":
        local = tmp_path / "local.md"
        local.write_text("# local\n", encoding="utf-8")
        prepared = tool.prepare(root, str(local))
        assert str(local.resolve()) in (root / prepared["entry"]).read_text(
            encoding="utf-8")


def test_validate_names_placeholders_and_empty_integration_bullets(tmp_path):
    root = _project(tmp_path)
    prepared = tool.prepare(root, "https://github.com/mastra-ai/mastra")
    findings = tool.validate(root, prepared["entry"])
    assert any("Analyzed version/ref" in finding for finding in findings)
    assert any("Integration guidance bullet is empty" in finding for finding in findings)
    assert any("template placeholders remain" in finding for finding in findings)
    assert any("Reviewer" in finding for finding in findings)


def test_finalize_requires_the_reviewed_status(tmp_path):
    root = _project(tmp_path)
    entry = _complete(root)
    entry.write_text(entry.read_text(encoding="utf-8").replace(
        "- **Status:** reviewed", "- **Status:** draft", 1), encoding="utf-8")
    result = tool.finalize(root, "mastra")
    assert result["ok"] is False
    assert any("reviewed" in finding for finding in result["findings"])


def test_finalize_records_the_review_and_revision_history(tmp_path):
    root = _project(tmp_path)
    entry = _complete(root)
    result = tool.finalize(root, "mastra")
    assert result["ok"] is True, result
    text = entry.read_text(encoding="utf-8")
    assert "- **Last reviewed on:** " in text
    assert text.count("| v0.31.0 |") >= 1
    assert "test-reviewer" in text


def test_finalize_registers_the_entry_and_is_idempotent(tmp_path):
    root = _project(tmp_path)
    _complete(root)
    first = tool.finalize(root, "mastra")
    assert first["ok"] is True and first["updated"] is True
    readme = (root / "docs/research/external/README.md").read_text(encoding="utf-8")
    assert "No external contexts registered yet." not in readme
    assert readme.count("[Mastra](mastra/context.md)") == 1
    second = tool.finalize(root, "mastra")
    assert second["ok"] is True and second["updated"] is False
    readme = (root / "docs/research/external/README.md").read_text(encoding="utf-8")
    assert readme.count("[Mastra](mastra/context.md)") == 1


def test_finalize_creates_a_missing_registry_index(tmp_path):
    root = _project(tmp_path)
    _complete(root)
    (root / "docs/research/external/README.md").unlink()
    result = tool.finalize(root, "mastra")
    assert result["ok"] is True
    assert "[Mastra](mastra/context.md)" in (
        root / "docs/research/external/README.md").read_text(encoding="utf-8")


def test_check_flags_drafts_and_dead_links(tmp_path):
    root = _project(tmp_path)
    tool.prepare(root, "https://github.com/mastra-ai/mastra")
    draft = tool.check(root)
    assert draft["ok"] is False
    assert draft["entries"] == 1
    assert any("still a draft" in finding
               for item in draft["findings"] for finding in item["findings"])

    _complete(root)
    tool.finalize(root, "mastra")
    readme = root / "docs/research/external/README.md"
    readme.write_text(readme.read_text(encoding="utf-8").replace(
        "<!-- END GENERATED REGISTRY -->",
        "- [Gone](gone/context.md) — removed (2026-10-29)\n"
        "<!-- END GENERATED REGISTRY -->"), encoding="utf-8")
    stale = tool.check(root)
    assert stale["ok"] is False
    assert any("gone" in finding for finding in stale["index_findings"])


def test_check_accepts_a_complete_registry(tmp_path):
    root = _project(tmp_path)
    _complete(root)
    assert tool.finalize(root, "mastra")["ok"] is True
    result = tool.check(root)
    assert result["ok"] is True
    assert result["entries"] == 1
    assert result["findings"] == [] and result["index_findings"] == []


def test_check_reports_a_stale_ref_and_date_in_the_index(tmp_path):
    """E-087: an index line that outlived its entry fails the audit."""
    root = _project(tmp_path)
    entry = _complete(root)
    assert tool.finalize(root, "mastra")["ok"] is True
    text = entry.read_text(encoding="utf-8")
    text = re.sub(r"(?m)^- \*\*Analyzed version/ref:\*\* .*$",
                  "- **Analyzed version/ref:** v0.32.0", text, count=1)
    text = re.sub(r"(?m)^- \*\*Analyzed on:\*\* .*$",
                  "- **Analyzed on:** 2020-01-01", text, count=1)
    entry.write_text(text, encoding="utf-8")
    result = tool.check(root)
    assert result["ok"] is False
    assert any("shows ref 'v0.31.0' but the entry analyzes 'v0.32.0'" in finding
               for finding in result["index_findings"])
    assert any("shows date" in finding and "2020-01-01" in finding
               for finding in result["index_findings"])


def test_check_reports_a_renamed_entry_in_the_index(tmp_path):
    """E-087: the index display name follows the entry heading."""
    root = _project(tmp_path)
    entry = _complete(root)
    assert tool.finalize(root, "mastra")["ok"] is True
    entry.write_text(entry.read_text(encoding="utf-8").replace(
        "# Mastra Context", "# Mastra Framework Context", 1), encoding="utf-8")
    result = tool.check(root)
    assert result["ok"] is False
    assert any("names 'Mastra' but the entry heading is 'Mastra Framework'" in finding
               for finding in result["index_findings"])


def test_finalize_clears_index_drift_and_the_registry_goes_green(tmp_path):
    """E-087: finalize is the repair for drift it reports."""
    root = _project(tmp_path)
    _complete(root)
    assert tool.finalize(root, "mastra")["ok"] is True
    entry = root / "docs/research/external/mastra/context.md"
    entry.write_text(entry.read_text(encoding="utf-8").replace(
        "- **Analyzed version/ref:** v0.31.0",
        "- **Analyzed version/ref:** v9.9.9", 1), encoding="utf-8")
    drifted = tool.check(root)
    assert drifted["ok"] is False
    assert tool.finalize(root, "mastra")["ok"] is True
    assert tool.check(root)["ok"] is True


def test_validate_rejects_an_entry_outside_the_registry(tmp_path):
    root = _project(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text(TEMPLATE, encoding="utf-8")
    assert tool.validate(root, outside) == [
        "entry is outside the external-context registry"]


def test_authoring_commands_refuse_a_registry_that_escapes_the_project(tmp_path):
    """F-05: finder, validate, finalize, refresh and discard share one boundary."""
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks are unavailable")
    root = _project(tmp_path)
    outside = tmp_path / "outside-registry"
    outside.mkdir()
    registry = root / "docs/research/external"
    import shutil
    shutil.rmtree(registry)
    try:
        registry.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("creating a directory symlink requires a privilege here")
    with pytest.raises(tool.ContextError, match="outside the project root"):
        tool.prepare(root, "https://github.com/mastra-ai/mastra")
    with pytest.raises(tool.ContextError, match="outside the project root"):
        tool.finalize(root, "mastra")
    with pytest.raises(tool.ContextError, match="outside the project root"):
        tool.refresh(root, "mastra")
    with pytest.raises(tool.ContextError, match="outside the project root"):
        tool.discard(root, "mastra")


def test_refresh_keeps_the_last_reviewed_analysis_visible(tmp_path):
    """F-03: revalidation must not turn a reviewed entry into an invisible draft."""
    root = _project(tmp_path)
    entry = _complete(root)
    assert tool.finalize(root, "mastra")["ok"] is True
    text = entry.read_text(encoding="utf-8")
    text += "\nanalysis-marker-keep\n"
    entry.write_text(text, encoding="utf-8")
    result = tool.refresh(root, "mastra")
    assert result["previous_status"] == "reviewed"
    refreshed = entry.read_text(encoding="utf-8")
    assert "- **Status:** revalidating" in refreshed
    assert "analysis-marker-keep" in refreshed
    assert "- **Analyzed on:** " in refreshed
    assert "- **Last reviewed on:** 2026-10-30" in refreshed
    assert f"- **Revalidation started on:** {date.today().isoformat()}" in refreshed
    checked = tool.check(root)
    assert checked["ok"] is True  # revalidation is a warning, not a broken entry
    assert any("revalidation is in progress" in warning for warning in checked["warnings"])
    assert tool.check(root, strict=True)["ok"] is False


def test_refresh_rejects_a_draft(tmp_path):
    root = _project(tmp_path)
    tool.prepare(root, "https://github.com/mastra-ai/mastra")
    with pytest.raises(tool.ContextError, match="still a draft"):
        tool.refresh(root, "mastra")


def test_refresh_missing_entry_raises(tmp_path):
    root = _project(tmp_path)
    with pytest.raises(tool.ContextError, match="no entry to refresh"):
        tool.refresh(root, "absent")


def test_cli_refresh_reports_the_previous_status(tmp_path):
    root = _project(tmp_path)
    _complete(root)
    assert tool.finalize(root, "mastra")["ok"] is True
    refreshed = _cli("refresh", "--project-root", str(root),
                     "--slug", "mastra", "--json")
    assert refreshed.returncode == 0, refreshed.stderr
    payload = json.loads(refreshed.stdout)
    assert payload["previous_status"] == "reviewed"
    assert payload["date"] == date.today().isoformat()


def test_discard_archives_an_abandoned_draft(tmp_path):
    root = _project(tmp_path)
    tool.prepare(root, "https://github.com/mastra-ai/mastra")
    result = tool.discard(root, "mastra", reason="superseded by a full analysis")
    assert result["archived"].startswith("docs/research/external/.archive/")
    assert not (root / "docs/research/external/mastra").exists()
    assert (root / result["archived"] / "context.md").is_file()
    assert (root / result["archived"] / "ARCHIVED.md").is_file()
    assert "No external contexts registered yet." in (
        root / "docs/research/external/README.md").read_text(encoding="utf-8")
    assert tool.check(root)["ok"] is True


def test_discard_requires_force_for_a_reviewed_entry(tmp_path):
    root = _project(tmp_path)
    _complete(root)
    assert tool.finalize(root, "mastra")["ok"] is True
    with pytest.raises(tool.ContextError, match="--force"):
        tool.discard(root, "mastra")
    result = tool.discard(root, "mastra", force=True)
    assert result["index_updated"] is True
    readme = (root / "docs/research/external/README.md").read_text(encoding="utf-8")
    assert "(mastra/context.md)" not in readme
    assert "No external contexts registered yet." in readme
    assert tool.check(root)["ok"] is True


def test_discard_requires_force_after_refresh(tmp_path):
    """F-04: refresh must not reset the 'was reviewed' protection."""
    root = _project(tmp_path)
    _complete(root)
    assert tool.finalize(root, "mastra")["ok"] is True
    tool.refresh(root, "mastra")
    with pytest.raises(tool.ContextError, match="--force"):
        tool.discard(root, "mastra")
    assert tool.discard(root, "mastra", force=True)["ok"] is True


def test_discard_missing_entry_raises(tmp_path):
    root = _project(tmp_path)
    with pytest.raises(tool.ContextError, match="no entry to discard"):
        tool.discard(root, "absent")


def test_cli_discard_archives_and_reports(tmp_path):
    root = _project(tmp_path)
    prepared = _cli("prepare", "--project-root", str(root),
                    "--source", "https://github.com/mastra-ai/mastra", "--json")
    assert prepared.returncode == 0
    discarded = _cli("discard", "--project-root", str(root),
                     "--slug", "mastra", "--json")
    assert discarded.returncode == 0, discarded.stderr
    payload = json.loads(discarded.stdout)
    assert payload["archived"].startswith("docs/research/external/.archive/")
    assert _cli("check", "--project-root", str(root), "--json").returncode == 0


def test_check_reports_a_missing_registry_instead_of_crashing(tmp_path):
    result = tool.check(tmp_path / "empty-project")
    assert result["ok"] is False
    assert result["registry"] == "missing"
    assert result["index_findings"]


def test_check_strict_turns_warnings_into_failures(tmp_path):
    """F-06/F-18: CI uses --strict; the default check still separates warnings."""
    root = _project(tmp_path)
    _complete(root)
    assert tool.finalize(root, "mastra")["ok"] is True
    entry = root / "docs/research/external/mastra/context.md"
    # `finalize` may append today's review row, so the row that must disagree
    # with the analyzed ref is the LAST one, wherever the calendar put it.
    lines = entry.read_text(encoding="utf-8").splitlines()
    for index in range(len(lines) - 1, -1, -1):
        if lines[index].startswith("| 20") and "| v0.31.0 |" in lines[index]:
            lines[index] = lines[index].replace("v0.31.0", "v0.30.0", 1)
            break
    else:
        raise AssertionError("the entry has no revision row naming the analyzed ref")
    entry.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert tool.check(root)["ok"] is True
    strict = tool.check(root, strict=True)
    assert strict["ok"] is False
    assert any("revision row" in warning for warning in strict["warnings"])


def test_table_cell_round_trip_preserves_escaped_pipes(tmp_path):
    """F-22: `_cell` escapes `|`; `_table_cells` must undo exactly that."""
    row = "| https://example.com/a?x=1\\|2 | official docs | v1; 2026-01-01 | docs |"
    cells = tool.table_cells(row)
    assert len(cells) == 4
    assert cells[0] == "https://example.com/a?x=1|2"
    assert tool.cell(cells[0]) == "https://example.com/a?x=1\\|2"


def test_migrate_upgrades_a_schema_one_entry(tmp_path):
    """F-26: legacy entries are upgraded deterministically, not rejected forever."""
    root = _project(tmp_path)
    registry = root / "docs/research/external"
    entry = registry / "legacy/context.md"
    entry.parent.mkdir()
    entry.write_text(
        "# Legacy Context\n\n- **Status:** reviewed\n"
        "- **Analyzed on:** 2024-01-02\n"
        "- **Analyzed version/ref:** 1.0\n"
        "- **Revalidate when:** 2024-07-01\n"
        "- **Project relevance:** agents\n\n"
        "## Summary\n\nObserved evidence recorded for the legacy entry.\n",
        encoding="utf-8")
    dry = tool.migrate(root, dry_run=True)
    assert dry["count"] == 1
    assert "Schema" not in entry.read_text(encoding="utf-8")
    result = tool.migrate(root)
    assert result["changed"] == ["docs/research/external/legacy/context.md"]
    text = entry.read_text(encoding="utf-8")
    assert "- **Schema:** 2" in text
    assert "- **Revalidate after:** 2024-07-01" in text
    assert "- **Last reviewed on:** 2024-01-02" in text
    assert "## Revision history" in text


def test_list_and_rebuild_index_are_management_commands(tmp_path):
    """F-27: list/rebuild-index expose registry state without hand-editing."""
    root = _project(tmp_path)
    _complete(root)
    assert tool.finalize(root, "mastra")["ok"] is True
    listed = tool.list_entries(root)
    assert listed["count"] == 1
    assert listed["entries"][0]["status"] == "reviewed"
    assert listed["entries"][0]["freshness"] == "fresh"
    readme = root / "docs/research/external/README.md"
    readme.write_text(readme.read_text(encoding="utf-8").replace(
        "[Mastra](mastra/context.md)", "[Broken](mastra/context.md)"),
        encoding="utf-8")
    assert tool.check(root)["ok"] is False
    rebuilt = tool.rebuild_index(root)
    assert rebuilt["updated"] is True
    assert tool.check(root)["ok"] is True


def test_atomic_write_leaves_no_temporary_file(tmp_path):
    """F-23: writers replace the target atomically and clean their sidecar."""
    target = tmp_path / "context.md"
    tool.atomic_write_text(target, "first\n")
    tool.atomic_write_text(target, "second\n")
    assert target.read_text(encoding="utf-8") == "second\n"
    assert not list(tmp_path.glob("*.tmp"))


def test_failed_finalize_leaves_the_entry_byte_identical(tmp_path):
    """E-092: validation happens on a candidate, never on a written file."""
    root = _project(tmp_path)
    tool.prepare(root, "https://github.com/mastra-ai/mastra")
    entry = root / "docs/research/external/mastra/context.md"
    entry.write_text(entry.read_text(encoding="utf-8").replace(
        "- **Status:** draft", "- **Status:** reviewed", 1), encoding="utf-8")
    before = entry.read_bytes()
    result = tool.finalize(root, "mastra")
    assert result["ok"] is False
    assert result["findings"]
    assert entry.read_bytes() == before
    assert "- **Last reviewed on:** pending" in entry.read_text(encoding="utf-8")
    assert "| reviewed |" not in entry.read_text(encoding="utf-8")


def test_check_and_list_accept_a_pinned_today(tmp_path):
    """E-092: freshness is injectable, not read from the wall clock."""
    root = _project(tmp_path)
    entry = _complete(root)
    assert tool.finalize(root, "mastra")["ok"] is True
    text = entry.read_text(encoding="utf-8")
    text = re.sub(r"(?m)^- \*\*Analyzed on:\*\* .*$",
                  "- **Analyzed on:** 2026-01-01", text, count=1)
    text = re.sub(r"(?m)^- \*\*Revalidate after:\*\* .*$",
                  "- **Revalidate after:** 2026-06-01", text, count=1)
    entry.write_text(text, encoding="utf-8")
    assert tool.rebuild_index(root)["ok"] is True

    early = tool.check(root, today="2026-05-31")
    assert early["ok"] is True
    assert early["warnings"] == []
    late = tool.check(root, today="2026-07-01")
    assert late["ok"] is True
    assert any("passed" in warning for warning in late["warnings"])
    assert tool.check(root, today="2026-07-01", strict=True)["ok"] is False
    assert tool.list_entries(root, today="2026-05-31")["entries"][0][
        "freshness"] == "fresh"
    assert tool.list_entries(root, today="2026-07-01")["entries"][0][
        "freshness"] == "stale"


def test_require_inside_refuses_a_path_outside_the_registry(tmp_path):
    """E-092: the per-entry guard is a pure path check; no symlink needed."""
    root = _project(tmp_path)
    registry = tool.registry_root(root)
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside\n", encoding="utf-8")
    with pytest.raises(tool.ContextError, match="outside"):
        tool.require_inside(outside, registry, "entry outside")
    missing = registry / "missing/context.md"
    with pytest.raises(tool.ContextError, match="cannot resolve"):
        tool.require_inside(missing, registry, "entry missing")
    inside = registry / "mastra/context.md"
    inside.parent.mkdir()
    inside.write_text("# Inside\n", encoding="utf-8")
    assert tool.require_inside(inside, registry, "entry mastra") == inside.resolve()


def test_entry_folder_symlink_escape_is_refused(tmp_path):
    """E-092: the registry-root guard is not enough; the entry path is checked."""
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks are unavailable")
    root = _project(tmp_path)
    outside = tmp_path / "outside-entry"
    outside.mkdir()
    external_entry = outside / "context.md"
    external_entry.write_text(
        "# Evil Context\n\n- **Status:** reviewed\n", encoding="utf-8")
    registry = root / "docs/research/external"
    try:
        (registry / "evil").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("creating a directory symlink requires a privilege here")
    before = external_entry.read_bytes()
    for call in (
            lambda: tool.finalize(root, "evil"),
            lambda: tool.refresh(root, "evil"),
            lambda: tool.discard(root, "evil", force=True),
            lambda: tool.migrate(root),
            lambda: tool.list_entries(root)):
        with pytest.raises(tool.ContextError, match="outside"):
            call()
    assert external_entry.read_bytes() == before
    checked = tool.check(root)
    assert checked["ok"] is False
    assert any("outside" in finding for finding in checked["index_findings"])


def test_require_inside_target_refuses_an_outside_ancestor(tmp_path):
    """E-093: a not-yet-created target is checked through its existing ancestor."""
    root = _project(tmp_path)
    registry = tool.registry_root(root)
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(tool.ContextError, match="outside"):
        tool.require_inside_target(
            outside / "new/context.md", registry, "entry new")
    safe = registry / "new" / "context.md"
    assert tool.require_inside_target(safe, registry, "entry new") == safe


def test_prepare_refuses_to_write_through_an_entry_folder_link(tmp_path):
    """E-093: a linked entry folder cannot route the scaffold outside."""
    root = _project(tmp_path)
    registry = root / "docs/research/external"
    outside = tmp_path / "outside-entry"
    outside.mkdir()
    if not _dir_link(registry / "escaped", outside):
        pytest.skip("creating a directory link requires a privilege here")
    with pytest.raises(tool.ContextError, match="outside"):
        tool.prepare(root, "https://github.com/mastra-ai/mastra", slug="escaped")
    assert not (outside / "context.md").exists()


def test_discard_refuses_to_archive_through_a_linked_archive_dir(tmp_path):
    """E-093: a linked `.archive` cannot move a discarded entry outside."""
    root = _project(tmp_path)
    registry = root / "docs/research/external"
    outside = tmp_path / "outside-archive"
    outside.mkdir()
    tool.prepare(root, "https://github.com/mastra-ai/mastra")
    if not _dir_link(registry / ".archive", outside):
        pytest.skip("creating a directory link requires a privilege here")
    with pytest.raises(tool.ContextError, match="outside"):
        tool.discard(root, "mastra")
    assert (registry / "mastra/context.md").is_file()
    assert list(outside.iterdir()) == []


def test_prepare_is_serialized_by_the_registry_lock(tmp_path):
    """E-092: collision detection and the scaffold write share one lock."""
    import threading
    import time

    root = _project(tmp_path)
    registry = root / "docs/research/external"
    results: list[dict] = []
    errors: list[BaseException] = []

    def run() -> None:
        try:
            results.append(tool.prepare(root, "https://github.com/mastra-ai/mastra"))
        except BaseException as exc:  # a thread failure must fail the test
            errors.append(exc)

    with tool.registry_lock(registry):
        thread = threading.Thread(target=run)
        thread.start()
        time.sleep(0.3)
        assert not (registry / "mastra").exists()
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert errors == []
    assert results and results[0]["ok"] is True
    assert (registry / "mastra/context.md").is_file()


def test_check_reports_a_folder_without_an_entry(tmp_path):
    """E-092: a silently skipped folder hides registry drift."""
    root = _project(tmp_path)
    (root / "docs/research/external/orphan").mkdir()
    result = tool.check(root)
    assert result["ok"] is True
    assert any("orphan" in warning and "context.md" in warning
               for warning in result["warnings"])
    assert tool.check(root, strict=True)["ok"] is False


def test_cli_rejects_an_invalid_today(tmp_path):
    root = _project(tmp_path)
    result = _cli("check", "--project-root", str(root),
                  "--today", "not-a-date", "--json")
    assert result.returncode == 2
    assert "--today" in result.stderr


def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          cwd=PLUGIN, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, check=False, timeout=30)


def test_cli_prepare_finalize_and_check_roundtrip(tmp_path):
    root = _project(tmp_path)
    prepared = _cli("prepare", "--project-root", str(root),
                    "--source", "https://github.com/mastra-ai/mastra", "--json")
    assert prepared.returncode == 0, prepared.stderr
    payload = json.loads(prepared.stdout)
    assert payload["entry"] == "docs/research/external/mastra/context.md"

    incomplete = _cli("finalize", "--project-root", str(root),
                      "--slug", "mastra", "--json")
    assert incomplete.returncode == 1
    assert json.loads(incomplete.stdout)["findings"]

    _complete(root)
    finalized = _cli("finalize", "--project-root", str(root),
                     "--slug", "mastra", "--json")
    assert finalized.returncode == 0, finalized.stderr
    assert json.loads(finalized.stdout)["ok"] is True

    checked = _cli("check", "--project-root", str(root), "--json")
    assert checked.returncode == 0, checked.stdout
    assert json.loads(checked.stdout)["entries"] == 1


def test_cli_reports_input_errors_with_exit_two(tmp_path):
    root = _project(tmp_path)
    result = _cli("prepare", "--project-root", str(root),
                  "--source", str(tmp_path / "absent.md"), "--json")
    assert result.returncode == 2
    assert "does not exist" in json.loads(result.stdout)["error"]

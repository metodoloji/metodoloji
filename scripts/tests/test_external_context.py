import importlib.util
import json
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


def _complete(root: Path, name: str = "Mastra") -> Path:
    entry = root / "docs/research/external" / tool.slugify(name) / "context.md"
    if not entry.exists():
        prepared = tool.prepare(root, "https://github.com/mastra-ai/mastra", name=name)
        entry = root / prepared["entry"]
    text = entry.read_text(encoding="utf-8")
    text = text.replace("- **Status:** draft", "- **Status:** reviewed", 1)
    text = re.sub(r"<[^<>\n]+>", "observed", text)
    text = re.sub(r"(?m)^(- \*\*[^*]+:\*\*)\s*$", r"\1 observed", text)
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
    with pytest.raises(tool.ContextError, match="already exists"):
        tool.prepare(root, "https://github.com/mastra-ai/mastra")
    assert entry.read_text(encoding="utf-8") == before


def test_validate_names_placeholders_and_empty_integration_bullets(tmp_path):
    root = _project(tmp_path)
    prepared = tool.prepare(root, "https://github.com/mastra-ai/mastra")
    findings = tool.validate(root, prepared["entry"])
    assert any("Analyzed version/ref" in finding for finding in findings)
    assert any("Integration guidance bullet is empty" in finding for finding in findings)
    assert any("template placeholders remain" in finding for finding in findings)


def test_finalize_requires_the_reviewed_status(tmp_path):
    root = _project(tmp_path)
    entry = _complete(root)
    entry.write_text(entry.read_text(encoding="utf-8").replace(
        "- **Status:** reviewed", "- **Status:** draft", 1), encoding="utf-8")
    result = tool.finalize(root, "mastra")
    assert result["ok"] is False
    assert any("reviewed" in finding for finding in result["findings"])


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


def test_check_flags_drafts_unindexed_entries_and_dead_links(tmp_path):
    root = _project(tmp_path)
    tool.prepare(root, "https://github.com/mastra-ai/mastra")
    draft = tool.check(root)
    assert draft["ok"] is False
    assert draft["entries"] == 1
    assert any("not 'reviewed'" in finding
               for item in draft["findings"] for finding in item["findings"])
    assert draft["index_findings"]

    _complete(root)
    tool.finalize(root, "mastra")
    readme = root / "docs/research/external/README.md"
    readme.write_text(readme.read_text(encoding="utf-8") +
                      "\n- [Gone](gone/context.md) — removed (2026-10-29)\n",
                      encoding="utf-8")
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
                  "- **Analyzed version/ref:** v0.31.0", text, count=1)
    text = re.sub(r"(?m)^- \*\*Analyzed on:\*\* .*$",
                  "- **Analyzed on:** 2020-01-01", text, count=1)
    entry.write_text(text, encoding="utf-8")
    result = tool.check(root)
    assert result["ok"] is False
    assert result["findings"] == []  # only the index is wrong
    assert any("shows ref 'observed' but the entry analyzes 'v0.31.0'" in finding
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
        "- **Analyzed version/ref:** observed",
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


def test_refresh_marks_an_entry_for_revalidation_and_keeps_the_analysis(tmp_path):
    root = _project(tmp_path)
    entry = _complete(root)
    assert tool.finalize(root, "mastra")["ok"] is True
    text = entry.read_text(encoding="utf-8")
    text = re.sub(r"(?m)^- \*\*Analyzed on:\*\* .*$",
                  "- **Analyzed on:** 2020-01-01", text, count=1)
    text += "\nanalysis-marker-keep\n"
    entry.write_text(text, encoding="utf-8")
    result = tool.refresh(root, "mastra")
    assert result["previous_status"] == "reviewed"
    refreshed = entry.read_text(encoding="utf-8")
    assert "- **Status:** draft" in refreshed
    assert "analysis-marker-keep" in refreshed
    assert "2020-01-01" not in refreshed
    assert f"- **Analyzed on:** {date.today().isoformat()}" in refreshed
    assert tool.check(root)["ok"] is False  # the revalidation is visible


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
    result = tool.discard(root, "mastra")
    assert result["archived"].startswith("docs/research/external/.archive/")
    assert not (root / "docs/research/external/mastra").exists()
    assert (root / result["archived"] / "context.md").is_file()
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

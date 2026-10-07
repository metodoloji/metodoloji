"""Tests for the QR-table coherence gate (SP-020 structural fix).

quality() must deny `git commit` when a done story's embedded QR table still
reads pending while its QR record is APPROVED — the exact rot SP-020
backfilled 24 times. The check is conservative: anything that is not
(unambiguously done + QR section present + pending rows + QR pointer +
readable QR + QR APPROVED) is skipped, never denied.
"""

import sys
import tempfile
from pathlib import Path

import pytest

_HOOKS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HOOKS))

from modules import config  # noqa: E402
from modules.guard import _check_qr_table_coherence, quality  # noqa: E402


def _s_record(status="done", qr_table="pending", qr_path="docs/quality/QR-900.md"):
    if qr_table == "pending":
        rows = "| DoD-001 | ⏳ pending | — | — |"
    elif qr_table == "passed":
        rows = "| DoD-001 | ✅ passed | fixture | 2026-09-19 |"
    else:
        rows = ""
    section = ""
    if qr_table != "absent":
        section = (
            "## Quality Record (QR)\n\n"
            "| DoD Item | Status | Evidence | Date |\n"
            "|----------|--------|----------|------|\n"
            f"{rows}\n"
            "### QR Summary\n- **Total DoD Items**: 1\n"
            "- **Passed**: 0\n- **Failed**: 0\n- **Pending**: 1\n"
            f"- **QR Record Path**: {qr_path}\n"
        )
    return (
        "# Methodology Record: S-900\n\n"
        "| Field | Value |\n|------|-------|\n"
        f"| Status | {status} |\n"
        "| Experiment Refs | E-900 (APPROVED) |\n"
        f"{section}"
    )


def _qr_record(status="APPROVED"):
    # `| Story | S-900 |` is the declaration the quality gate reads; the
    # `- **Status:**` field is what the coherence check below binds.
    return (
        "# Quality Record: QR-900\n\n"
        f"- **Status:** {status}\n\n"
        "| Story | S-900 |\n\n"
        "| DoD Item | Status | Evidence | Date |\n"
        "|----------|-------|-------|-------|\n"
        "| DoD-001 | ✅ passed | fixture | 2026-09-19 |\n"
    )


def _root(tmp_path, s_status="done", qr_table="pending", qr_status="APPROVED"):
    root = tmp_path / "proj"
    (root / "docs/development/stories").mkdir(parents=True)
    (root / "docs/development").mkdir(parents=True, exist_ok=True)
    (root / "docs/quality").mkdir(parents=True, exist_ok=True)
    (root / "docs/development/stories/S-900.md").write_text(
        _s_record(s_status, qr_table), encoding="utf-8")
    (root / "docs/quality/QR-900.md").write_text(
        _qr_record(qr_status), encoding="utf-8")
    (root / "docs/development/IR-900.md").write_text(
        "# IR\n- **Status:** READY\n", encoding="utf-8")
    return root


def _commit(root, monkeypatch, mode):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    monkeypatch.setattr(config, "hook_gate_mode", lambda key: mode)
    return quality({"tool_name": "terminal",
                    "tool_input": {"command": "git commit -m 'x'"}})


def test_coherent_tree_allows_hard(tmp_path, monkeypatch):
    root = _root(tmp_path, qr_table="passed")
    res = _commit(root, monkeypatch, "hard")
    assert res["decision"] == "allow", res.get("reason", "")


def test_incoherent_tree_denies_hard(tmp_path, monkeypatch):
    root = _root(tmp_path)
    res = _commit(root, monkeypatch, "hard")
    assert res["decision"] == "deny"
    assert "S-900.md" in res["reason"]
    assert "sync-story-qr.py --apply" in res["reason"]


def test_incoherent_tree_warns_soft(tmp_path, monkeypatch):
    root = _root(tmp_path)
    res = _commit(root, monkeypatch, "soft")
    assert res["decision"] == "allow"
    assert any("S-900.md" in w for w in res.get("methodology_warnings", []))


def test_in_progress_not_bound(tmp_path, monkeypatch):
    root = _root(tmp_path, s_status="in-progress")
    res = _commit(root, monkeypatch, "hard")
    assert res["decision"] == "allow", res.get("reason", "")


def test_unapproved_qr_not_bound(tmp_path, monkeypatch):
    root = _root(tmp_path, qr_status="in-review")
    res = _commit(root, monkeypatch, "hard")
    assert res["decision"] == "allow", res.get("reason", "")


def test_missing_qr_section_skipped(tmp_path, monkeypatch):
    root = _root(tmp_path, qr_table="absent")
    res = _commit(root, monkeypatch, "hard")
    assert res["decision"] == "allow", res.get("reason", "")


def test_direct_function_reports_file():
    # isolated unit check without gate-mode plumbing
    import tempfile as _tf
    td = _tf.TemporaryDirectory()
    r = Path(td.name)
    (r / "docs/development/stories").mkdir(parents=True)
    (r / "docs/quality").mkdir(parents=True)
    (r / "docs/development/stories/S-900.md").write_text(
        _s_record(), encoding="utf-8")
    (r / "docs/quality/QR-900.md").write_text(
        _qr_record(), encoding="utf-8")
    res = _check_qr_table_coherence(str(r), "blocked")
    assert res["decision"] == "deny"
    assert "S-900.md" in res["reason"]
    td.cleanup()

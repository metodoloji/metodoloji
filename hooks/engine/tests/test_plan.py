"""Tests for hooks/engine/modules/plan.py — Implementation Plan (GRP), E-065."""

import sys
from pathlib import Path

import pytest

_HOOKS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HOOKS))

from modules import plan as plan_mod  # noqa: E402


def _record(path: Path, planned: str = "src/a.py, src/b.py", tests: str = "",
            body: str = "", with_plan: bool = True) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    plan_block = ("\n## Implementation Plan (GRP)\n\n"
                  f"- **Planned Files:** {planned}\n"
                  f"- **Planned Tests:**{tests}\n"
                  "- **Known Gaps:** advisory only\n"
                  "- **Amendments:** none\n") if with_plan else ""
    path.write_text(
        "## Experiment: E-100 — plan tests\n- **Status:** planned\n- **Code Scope:** src/**\n"
        + body + plan_block,
        encoding="utf-8")
    return path


# --- parsing and compilation ---

def test_plan_section_parsed():
    text = "## Implementation Plan (GRP)\n- **Planned Files:** a.py\n"
    assert "**Planned Files:**" in plan_mod.plan_section(text)
    assert plan_mod.plan_section("no plan here") == ""


def test_parse_plan_fields_lists():
    fields = plan_mod.parse_plan_fields(
        "## Implementation Plan (GRP)\n- **Planned Files:** a.py, b.py\n"
        "- **Planned Tests:** t1.py t2.py\n- **Known Gaps:** none yet\n"
        "- **Amendments:** none\n")
    assert fields["planned_files"] == ["a.py", "b.py"]
    assert fields["planned_tests"] == ["t1.py", "t2.py"]
    assert fields["known_gaps"] == "none yet"
    assert fields["amendments_raw"] == "none"


def test_empty_planned_tests_field_is_empty_not_next_line():
    """`- **Planned Tests:**` with nothing after the colon must parse to an
    empty value — never swallow the next bullet (markup-leak regression)."""
    fields = plan_mod.parse_plan_fields(
        "## Implementation Plan (GRP)\n- **Planned Files:** a.py\n"
        "- **Planned Tests:**\n- **Known Gaps:** advisory only\n")
    assert fields["planned_tests"] == []
    assert fields["known_gaps"] == "advisory only"


def test_compile_plan_exact_and_patterns(tmp_path):
    rec = _record(tmp_path / "E-100.md", planned="src/a.py, src/dep/**")
    p = plan_mod.compile_plan(str(rec))
    assert plan_mod.plan_match(p, "src/a.py")
    assert plan_mod.plan_match(p, "src/dep/x/y.py")
    assert not plan_mod.plan_match(p, "src/aa.py")      # planned never leaks to siblings
    assert not plan_mod.plan_match(p, "src/c.py")       # planned narrows the frozen glob


def test_compile_plan_rejects_bare_glob_all(tmp_path):
    rec = _record(tmp_path / "E-100.md", planned="**")
    p = plan_mod.compile_plan(str(rec))
    assert p["planned_count"] == 0                       # anti-tiptoe: ** declares nothing


def test_compile_plan_missing_record_fails_open():
    assert plan_mod.compile_plan("Z:/definitely/missing/E-999.md")["planned_count"] == 0


def test_plan_match_backslash_normalization(tmp_path):
    rec = _record(tmp_path / "E-100.md", planned="src/a.py, src/b.py")
    p = plan_mod.compile_plan(str(rec))
    assert plan_mod.plan_match(p, "src\\a.py")           # Windows writer paths


def test_compile_plan_no_plan_section(tmp_path):
    rec = tmp_path / "E-100.md"
    rec.write_text("## Experiment: E-100 — no plan\n- **Code Scope:** src/**\n",
                   encoding="utf-8")
    assert plan_mod.compile_plan(str(rec))["planned_count"] == 0


# --- progress ---

def test_plan_progress_counts_done_and_pending(tmp_path):
    rec = _record(tmp_path / "docs" / "experiments" / "E-100.md")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    prog = plan_mod.plan_progress(str(rec), str(tmp_path))
    assert prog == {"done": 1, "pending": 1, "planned": 2}


# --- active_plan_for ---

def test_active_plan_for_missing_dir_returns_none(tmp_path):
    assert plan_mod.active_plan_for("src/a.py", str(tmp_path / "nope")) is None


def test_active_plan_for_prefers_verified_and_narrowest(tmp_path, monkeypatch):
    recs = tmp_path / "docs" / "experiments"
    wide = _record(recs / "E-101.md", planned="src/a.py, src/b.py")
    narrow = _record(recs / "E-102.md", planned="src/a.py")
    verified = {str(narrow)}

    def fake_verify(path):
        return path in verified

    winner = plan_mod.active_plan_for("src/a.py", str(recs), is_verified=fake_verify)
    assert winner == str(narrow)


def test_active_plan_for_no_cover_returns_none(tmp_path):
    recs = tmp_path / "docs" / "experiments"
    _record(recs / "E-101.md", planned="src/other.py")
    assert plan_mod.active_plan_for("src/a.py", str(recs)) is None


# --- amendment chain ---

def test_append_amendment_and_chain_roundtrip():
    secret = b"unit-test-key"
    base = ("## Experiment: E-100 — t\n\n## Implementation Plan (GRP)\n\n"
            "- **Planned Files:** src/a.py\n- **Amendments:** none\n")
    text, _hash = plan_mod.append_amendment(base, 1, "2026-10-05", ["src/c.py"],
                                            "discovered module", secret)
    text = plan_mod.bump_planned_files(text, ["src/c.py"])
    assert "- **Amendments:** 1" in text
    assert plan_mod.amendment_chain_issue(text) is None
    assert plan_mod.amendment_chain_issue(text, secret) is None
    p = plan_mod.compile_plan.__wrapped__ if hasattr(plan_mod.compile_plan, "__wrapped__") else None
    fields = plan_mod.parse_plan_fields(text)
    assert fields["planned_files"] == ["src/a.py", "src/c.py"]


def test_amendment_chain_detects_file_edit():
    secret = b"unit-test-key"
    base = ("## Experiment: E-100 — t\n\n## Implementation Plan (GRP)\n\n"
            "- **Planned Files:** src/a.py\n- **Amendments:** none\n")
    text, _ = plan_mod.append_amendment(base, 1, "2026-10-05", ["src/c.py"],
                                        "discovered module", secret)
    assert plan_mod.amendment_chain_issue(text.replace("src/c.py", "src/z.py")) is not None


def test_amendment_chain_detects_counter_mismatch():
    text = ("## Experiment: E-100 — t\n\n## Implementation Plan (GRP)\n\n"
            "- **Amendments:** 3\n")  # claims 3, zero entries
    assert plan_mod.amendment_chain_issue(text) is not None


def test_amendment_token_forgery_detected_with_secret():
    secret = b"unit-test-key"
    base = ("## Experiment: E-100 — t\n\n## Implementation Plan (GRP)\n\n"
            "- **Amendments:** none\n")
    text, _ = plan_mod.append_amendment(base, 1, "2026-10-05", ["src/c.py"],
                                        "discovered module", secret)
    payload = plan_mod.amend_payload("E-100", plan_mod._CHAIN_GENESIS, 1,
                                     ["src/c.py"], "discovered module")
    forged = text.replace(plan_mod.amend_token(payload, secret),
                          plan_mod.amend_token(payload, b"wrong-key"))
    # Key-free mode re-derives only the chain hash (the hash does not cover the
    # token), so the forged token is invisible there — by design, verify() owns
    # the key. Keyed mode must flag it.
    assert plan_mod.amendment_chain_issue(forged, secret) is not None


def test_final_plan_hash_deterministic_and_plan_sensitive():
    base = ("## Experiment: E-100 — t\n\n## Implementation Plan (GRP)\n\n"
            "- **Planned Files:** src/a.py\n- **Amendments:** none\n")
    h1 = plan_mod.final_plan_hash(base)
    assert h1 == plan_mod.final_plan_hash(base)
    assert h1 != plan_mod.final_plan_hash(
        plan_mod.bump_planned_files(base, ["src/new.py"]))


def test_plan_hash_for_record_missing_file_is_stable():
    a = plan_mod.plan_hash_for_record("Z:/missing/E-1.md")
    b = plan_mod.plan_hash_for_record("Z:/missing/E-1.md")
    assert a == b and len(a) == 64


# --- progress line ---

def test_progress_line_none_without_plan(tmp_path):
    rec = tmp_path / "E-100.md"
    rec.write_text("## Experiment: E-100 — no plan\n", encoding="utf-8")
    assert plan_mod.progress_line(str(rec), str(tmp_path)) is None


def test_progress_line_counts(tmp_path):
    rec = _record(tmp_path / "docs" / "experiments" / "E-100.md")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "src" / "b.py").write_text("y = 2\n", encoding="utf-8")
    line = plan_mod.progress_line(str(rec), str(tmp_path))
    assert line is not None and "2/2" in line


# --- surfacing: shared formatter + open plans (E-068) ---

def test_open_plan_line_shared_formatter(tmp_path):
    """One formatter (open_plan_line) names the record and its counts, and is
    empty/None once nothing is pending (a completed plan is silent)."""
    rec_dir = tmp_path / "docs" / "experiments"
    rec = _record(rec_dir / "E-100.md")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    assert plan_mod.open_plan_line(str(rec), str(tmp_path)) == \
        "E-100.md 1/2 planned files on disk (1 pending)"
    assert plan_mod.plan_progress_text(str(rec), str(tmp_path)) == \
        "1/2 planned files on disk (1 pending)"
    # completed plan → both formatters are empty
    (tmp_path / "src" / "b.py").write_text("y = 2\n", encoding="utf-8")
    assert plan_mod.plan_progress_text(str(rec), str(tmp_path)) == ""
    assert plan_mod.open_plan_line(str(rec), str(tmp_path)) is None


def test_plan_progress_text_empty_without_a_plan(tmp_path):
    rec = _record(tmp_path / "docs" / "experiments" / "E-100.md", with_plan=False)
    assert plan_mod.plan_progress_text(str(rec), str(tmp_path)) == ""
    assert plan_mod.open_plan_line(str(rec), str(tmp_path)) is None


def test_open_plans_sorted_capped_and_pending_only(tmp_path):
    """open_plans lists only records with a plan AND pending files, name-sorted,
    capped — one bounded row set every surfacing surface shares."""
    recs = tmp_path / "docs" / "experiments"
    _record(recs / "E-100.md", planned="src/a.py, src/b.py")
    _record(recs / "E-050.md", planned="src/z.py")
    _record(recs / "E-200.md", planned="src/c.py")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "c.py").write_text("x = 1\n", encoding="utf-8")  # completed
    rows = plan_mod.open_plans(str(recs), str(tmp_path))
    assert [row["name"] for row in rows] == ["E-050.md", "E-100.md"]  # sorted, done dropped
    assert rows[1]["done"] == 0 and rows[1]["pending"] == 2
    assert plan_mod.open_plans(str(recs), str(tmp_path), limit=1) == rows[:1]
    assert plan_mod.open_plans(str(tmp_path / "nope"), str(tmp_path)) == []
    assert "E-100.md 0/2 planned files on disk (2 pending)" in \
        plan_mod.open_plans_text(str(recs), str(tmp_path))

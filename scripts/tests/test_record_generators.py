"""Contract tests for the record generators' story parsing.

`scripts/create-methodology-record.py` (bridge #1: the S record) and
`scripts/create-qr-record.py` (bridge #3: the QR record) both parse the same
story fields: AC metadata, Technical Tasks, Definition of Done. The engine
parses those fields too, and its DoD scanner is the single source of truth
shared by guard and audit (`hooks/engine/modules/utils.py:scan_dod_items`).

The generators had drifted from it: they recognised only the checkbox bullet
(`- [ ] DoD-001: …`), while the shipped story template writes the token form
(`- [DoD-001] …`). A story written from that template therefore produced an S
record AND a QR record with ZERO DoD items — the record that is supposed to
carry the evidence silently dropped the items it evidences, and "0 DoD items"
then read as a clean result. That is the same defect class as the removed eval
suite and the Stop claim: a tool disagreeing with the shipped behaviour it
describes.

These tests pin both generators to the engine's own scanner — on synthetic
fragments, on the shipped template, and on the shipped story.
"""

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))

from hooks.engine.modules.utils import scan_dod_items  # noqa: E402


def _load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, PLUGIN / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


S_GEN = _load_module("create_methodology_record", "create-methodology-record.py")
QR_GEN = _load_module("create_qr_record", "create-qr-record.py")

TEMPLATE = PLUGIN / "docs" / "development" / "stories" / "_template_S.md"

# The reference story ships as a test fixture: the plugin repo no longer ships
# a native story tree (the record set was pruned 2026-10-06), so the same
# implemented example lives here and the generators keep a real story to
# round-trip.
FIXTURE_STORY = Path(__file__).resolve().parent / "fixtures" / "example-1-1-bench-in-ci.md"


def _shipped_story() -> Path:
    # Seçenek A dual-read: canonical docs/development/native/ first, legacy
    # fallback, then the fixture. The canonical copy carries the `_` prefix: it
    # is the plugin's reference example, so the story scanner skips it (an
    # example is not a story that can reach `done` without a record chain).
    canonical = (PLUGIN / "docs" / "development" / "native"
                 / "_example-1-1-bench-in-ci.md")
    if canonical.is_file():
        return canonical
    legacy = PLUGIN / "bmad-output" / "implementation-artifacts" / "1-1-bench-in-ci.md"
    if legacy.is_file():
        return legacy
    return FIXTURE_STORY


SHIPPED_STORY = _shipped_story()


def _unstarted_story() -> str:
    """The shipped story reset to its pre-implementation state.

    The shipped file is LIVING: S-001 has moved ready-for-dev → review, its
    DoD boxes record the implementation, and its Dev Agent Record is filled.
    The generator contracts below are about the UNSTARTED shape (a fresh
    record must report pending work, never failure) — so those tests read
    this controlled reset, not the live file. Built by replaying the exact
    edits implementation made; kept aligned with the template.
    """
    text = SHIPPED_STORY.read_text(encoding="utf-8")
    # reset the status the implementation advanced (status-agnostic: the story
    # keeps moving ready-for-dev → review → done as the chain progresses)
    text = re.sub(r"^Status: .*$", "Status: ready-for-dev", text, count=1,
                  flags=re.M)
    # reset every checkbox the implementation checked (tasks AND DoD)
    text = text.replace("- [x] ", "- [ ] ")
    # drop the Dev Agent Record body implementation filled
    text = re.sub(
        r"## Dev Agent Record\n.*?(?=\n## Quality Record \(QR\))",
        "## Dev Agent Record\n\n### Agent Model Used\n\n(not yet run — this record is `ready-for-dev`)\n\n### Debug Log References\n\n### Completion Notes List\n\n### File List\n\n### Change Log\n\n",
        text, flags=re.S)
    # reset the QR section the QR generator rewrote
    text = re.sub(
        r"## Quality Record \(QR\).*",
        "## Quality Record (QR)\n\n<!-- Filled in after implementation is complete. -->\n",
        text, flags=re.S)
    assert "- [x]" not in text, "fixture must be fully unstarted"
    assert "ready-for-dev" in text
    return text

# The two canonical bullet forms. The engine accepts both; so must the records.
CHECKBOX_DOD = """\
## Definition of Done

- [ ] DoD-001: All acceptance criteria met (AC: AC-001)
  - Verify: pytest
- [x] DoD-002: Code review done
  - Verify: QR-001 record exists
"""

TOKEN_DOD = """\
## Definition of Done

- [DoD-001] All acceptance criteria met (AC: AC-001)
  - Verify: pytest
- [DoD-002] Code review done
  - Verify: QR-001 record exists
"""


def _dod_section(text: str) -> str:
    m = re.search(
        r"##\s+Definition\s+of\s+Done\s*\n(.*?)(?=\n##\s|\Z)",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    return m.group(1) if m else ""


def _engine_dod_ids(text: str) -> list[str]:
    """The items the engine itself sees — the reference the generators must meet."""
    return [re.search(r"DoD-\d+", item["first"]).group(0)
            for item in scan_dod_items(_dod_section(text))]


def test_matrix_covers_the_two_canonical_forms():
    """Guard against a fix that only ever sees one form."""
    assert _engine_dod_ids(CHECKBOX_DOD) == ["DoD-001", "DoD-002"]
    assert _engine_dod_ids(TOKEN_DOD) == ["DoD-001", "DoD-002"]


def test_s_generator_reads_both_canonical_dod_forms():
    for text in (CHECKBOX_DOD, TOKEN_DOD):
        items = S_GEN.extract_story_metadata(text)["dod"]
        assert [i["id"] for i in items] == ["DoD-001", "DoD-002"], text
        assert items[0]["verify"] == "pytest", text
        assert items[0]["ac_refs"] == ["AC-001"], text
    # the checkbox is the ONLY place a done-state lives: the token form has
    # none, so it must read as not-yet-done rather than "passed"
    assert [i["checked"] for i in S_GEN.extract_story_metadata(CHECKBOX_DOD)["dod"]] == [False, True]
    assert [i["checked"] for i in S_GEN.extract_story_metadata(TOKEN_DOD)["dod"]] == [False, False]


def test_qr_generator_reads_both_canonical_dod_forms():
    for text in (CHECKBOX_DOD, TOKEN_DOD):
        items = QR_GEN.extract_dod_items(text)
        assert [i["id"] for i in items] == ["DoD-001", "DoD-002"], text
        assert items[0]["verify"] == "pytest", text
        assert items[0]["ac_refs"] == ["AC-001"], text
    assert [i["status"] for i in QR_GEN.extract_dod_items(CHECKBOX_DOD)] == ["pending", "passed"]
    assert [i["status"] for i in QR_GEN.extract_dod_items(TOKEN_DOD)] == ["pending", "pending"]


def test_generators_agree_with_the_engine_on_the_shipped_template():
    """The shipped template is the form every real story is written from."""
    text = TEMPLATE.read_text(encoding="utf-8")
    expected = _engine_dod_ids(text)
    assert len(expected) >= 5, expected
    assert [i["id"] for i in S_GEN.extract_story_metadata(text)["dod"]] == expected
    assert [i["id"] for i in QR_GEN.extract_dod_items(text)] == expected


def test_story_fields_lose_their_markdown_decoration():
    """`- **Epic:** epic-1` must not become `** epic-1` in the record."""
    text = _unstarted_story()
    meta = S_GEN.extract_story_metadata(text)
    assert meta["title"] == "Consistency bench runs in CI"
    assert meta["status"] == "ready-for-dev"
    assert meta["epic"] == "epic-1"
    assert meta["sprint"].startswith("SP-001")


def test_s_generator_keeps_every_ac_metadata_field():
    meta = S_GEN.extract_story_metadata(SHIPPED_STORY.read_text(encoding="utf-8"))
    acs = meta["acceptance_criteria"]
    assert [ac["id"] for ac in acs] == ["AC-001", "AC-002", "AC-003"]
    assert all(ac["experiment"] == "E-013" for ac in acs)
    assert all(ac["type"] == "agent-verifiable" for ac in acs)
    assert all(ac["measured"] == "true" for ac in acs)
    assert all(ac["verify"] for ac in acs)


def test_s_generator_keeps_tasks_verbatim():
    """No double bullet, and the AC binding survives into the record."""
    meta = S_GEN.extract_story_metadata(SHIPPED_STORY.read_text(encoding="utf-8"))
    assert meta["tasks"]
    # top-level lines keep column 0, subtasks keep their indentation
    assert all(t.lstrip().startswith("- [") for t in meta["tasks"])
    assert any(t.startswith("- [") for t in meta["tasks"])
    assert any(t[:1].isspace() for t in meta["tasks"])
    assert not any(t.lstrip().startswith("- - ") for t in meta["tasks"])
    assert any("AC: AC-001" in t for t in meta["tasks"])


def test_generators_agree_with_the_engine_on_the_shipped_story():
    text = SHIPPED_STORY.read_text(encoding="utf-8")
    expected = _engine_dod_ids(text)
    assert expected, "the shipped story must declare DoD items"
    assert [i["id"] for i in S_GEN.extract_story_metadata(text)["dod"]] == expected
    assert [i["id"] for i in QR_GEN.extract_dod_items(text)] == expected


def test_generated_record_carries_the_bridge_fields_and_the_structure(tmp_path):
    """Round-trip the shipped story through the real generator."""
    text = _unstarted_story()
    meta = S_GEN.extract_story_metadata(text)
    meta["native_story_path"] = SHIPPED_STORY.relative_to(PLUGIN).as_posix()
    record = S_GEN.create_methodology_record(meta, 1, tmp_path).read_text(encoding="utf-8")

    # bridge §2.3 field list
    for field in ("Date", "Status", "Story Title", "Epic", "Acceptance Criteria",
                  "Experiment Refs", "File List", "Sprint Ref", "Native Story"):
        assert re.search(rf"^\|\s*{field}\s*\|", record, re.MULTILINE), field

    assert "| Status | ready-for-dev |" in record
    assert "| Epic | epic-1 |" in record
    assert "| Acceptance Criteria | AC-001, AC-002, AC-003 |" in record
    assert "E-013" in record and "agent-verifiable" in record
    assert "|- - " not in record

    # one DoD row per item, with the item's own text
    rows = re.findall(r"^\|\s*(DoD-\d+)\b.*?\|", record, re.MULTILINE)
    assert len(rows) == 2 * len(_engine_dod_ids(text)), "DoD rows must appear in both tables"
    assert "DoD-001 — All acceptance criteria met" in record
    assert f"- **Total DoD Items**: {len(_engine_dod_ids(text))}" in record
    # An unstarted record reports PENDING work, never failed work.
    assert "- **Failed**: 0" in record
    assert f"- **Pending**: {len(_engine_dod_ids(text))}" in record


def test_story_reference_comment_is_posix_and_idempotent(tmp_path):
    """The pointer bridge §2.3 specifies, never a Windows path, written once."""
    story = tmp_path / "native.md"
    story.write_text("# Story 1.1: Title\n\nStatus: ready-for-dev\n", encoding="utf-8")
    record = tmp_path / "docs" / "development" / "stories" / "S-001.md"
    record.parent.mkdir(parents=True)
    record.write_text("# Methodology Record: S-001\n", encoding="utf-8")

    S_GEN.update_story_with_reference(story, record, tmp_path)
    S_GEN.update_story_with_reference(story, record, tmp_path)
    text = story.read_text(encoding="utf-8")
    assert "<!-- Methodology record: docs/development/stories/S-001.md -->" in text
    assert "docs\\development" not in text
    assert text.count("Methodology record:") == 1, text


def test_qr_story_section_reports_pending_not_failed(tmp_path):
    """The QR generator rewrites the story's QR section after implementation."""
    story = tmp_path / "1-1-bench-in-ci.md"
    story.write_text(_unstarted_story(), encoding="utf-8")
    qr = tmp_path / "docs" / "quality" / "QR-001.md"
    qr.parent.mkdir(parents=True)
    qr.write_text("# Quality Record: QR-001\n", encoding="utf-8")

    items = QR_GEN.extract_dod_items(story.read_text(encoding="utf-8"))
    QR_GEN.update_story_qr_section(story, qr, items, tmp_path)
    updated = story.read_text(encoding="utf-8")

    assert "| DoD Item | Status | Evidence | Date |" in updated
    assert "| Durum |" not in updated, "the record set is English"
    assert "- **Failed**: 0" in updated
    assert f"- **Pending**: {len(items)}" in updated
    assert "✅ passed" not in updated, "nothing is implemented yet"


# --- producer-side relay (2026-09-23 fix) ------------------------------------------
# The generators' mirror call used to be heartbeat-only: when a session created
# the record through the guard-approved script path, the downstream baton was
# never posted — the code-review signal was then forgotten one turn after the
# close-out. The script IS the close-out point now; mirror dedup keeps the
# skill-side close-out safe either way.


def _bb_pending(board, skill):
    from hooks.engine.modules import blackboard as bb
    return bb.pending_handoffs(str(board), skill)


def test_qr_mirror_posts_downstream_baton(tmp_path):
    """The QR heartbeat hands off to production-readiness (dedup on re-post)."""
    board = tmp_path / "proj"
    board.mkdir()
    QR_GEN._mirror_heartbeat(
        board, "QR-001", "QR record created — demo (3/3 DoD passed)",
        to="bmad-production-readiness", sender="bmad-quality-record",
        note="QR-001 ready for the production readiness check.")
    waiting = [a["text"] for a in _bb_pending(board, "bmad-production-readiness")]
    assert any(t.startswith("QR-001:") for t in waiting), waiting
    # idempotent: re-running the generator never floods the channel
    QR_GEN._mirror_heartbeat(
        board, "QR-001", "QR record created — demo (3/3 DoD passed)",
        to="bmad-production-readiness", sender="bmad-quality-record",
        note="QR-001 ready for the production readiness check.")
    assert len(_bb_pending(board, "bmad-production-readiness")) == 1


def test_story_mirror_posts_dev_story_baton(tmp_path):
    """The S heartbeat hands off to bmad-dev-story (the guard's pre() then
    re-announces it every turn until the dev run consumes it)."""
    board = tmp_path / "proj"
    board.mkdir()
    S_GEN._mirror_heartbeat(
        board, "S-001", "story record created — demo",
        to="bmad-dev-story", sender="bmad-create-story",
        note="story record S-001 ready-for-dev — open it with bmad-dev-story.")
    assert any(a["text"].startswith("S-001:")
               for a in _bb_pending(board, "bmad-dev-story"))


def test_qr_baton_gated_on_full_dod_pass():
    """The baton claims readiness — the call site must gate it on every DoD
    item passing (a pending QR must heartbeat, never hand off)."""
    src = (PLUGIN / "scripts" / "create-qr-record.py").read_text(encoding="utf-8")
    gate = src.find("passed == total")
    handoff = src.find('to="bmad-production-readiness"')
    assert gate != -1 and handoff != -1 and gate < handoff


def test_mirror_heartbeat_honors_kill_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("METODOLOJI_NO_BLACKBOARD", "1")
    board = tmp_path / "proj"
    board.mkdir()
    QR_GEN._mirror_heartbeat(
        board, "QR-001", "v", to="bmad-production-readiness",
        sender="bmad-quality-record", note="n")
    assert _bb_pending(board, "bmad-production-readiness") == []


# --- E-016: a non-UTF-8 record is an honest refusal, never a traceback ---------
# The record tooling reads a story/record that any editor or tool may corrupt;
# a UnicodeDecodeError (a ValueError, not OSError) used to escape as a crash.

def _run_script(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(PLUGIN / "scripts" / script), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60)


def test_qr_generator_refuses_a_non_utf8_story(tmp_path):
    story = tmp_path / "bin.md"
    story.write_bytes(b"\xff\xfe\x00 binary \x80 junk")
    r = _run_script("create-qr-record.py", "--story", str(story),
                    "--project-root", str(tmp_path))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "not valid UTF-8" in r.stderr and "Traceback" not in r.stderr


def test_s_generator_refuses_a_non_utf8_story(tmp_path):
    story = tmp_path / "bin.md"
    story.write_bytes(b"\xff\xfe\x00 binary \x80 junk")
    r = _run_script("create-methodology-record.py", "--story", str(story),
                    "--project-root", str(tmp_path))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "not valid UTF-8" in r.stderr and "Traceback" not in r.stderr


def test_sync_story_qr_reports_a_non_utf8_record_and_check_fails(tmp_path):
    (tmp_path / "docs" / "quality").mkdir(parents=True)
    (tmp_path / "docs" / "development" / "stories").mkdir(parents=True)
    (tmp_path / "docs" / "quality" / "QR-001.md").write_bytes(b"b \xff\xfe junk")
    (tmp_path / "docs" / "development" / "stories" / "S-001.md").write_bytes(
        b"b \xff\xfe junk")
    r = _run_script("sync-story-qr.py", "--check", "--project-root", str(tmp_path))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "UNREADABLE" in r.stdout and "Traceback" not in (r.stdout + r.stderr)

"""Engine-roundtrip scenarios for the record generators (epic-1 action item).

`scripts/create-methodology-record.py` (bridge: the S record) and
`scripts/create-qr-record.py` (bridge: the QR record) feed the record
chain, and the hook engine guards that chain at write time (guard) and
commit time (quality, hard). A generator whose output the engine rejects
is a bridge that leads nowhere — so these tests round-trip generator
output through the REAL engine entry points on throwaway roots
(tempfile — the repo is never touched):

- generated S record → guard(file_editor write) → allow
- generated S record + minimal SP/IR chain → quality(git commit, hard) → allow
- generated S record flipped to done + staged QR/IR/SP → quality(hard) → allow
- QR section rewrite → engine DoD scanner agreement on the rewritten story

No signed records are staged: generator output carries no frontmatter
(the skill adds it), and table-form AC linkage needs no HMAC — so the
scenarios repeat on any machine. Stdlib only, no network, no model calls.
"""

import importlib.util
import re
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))
sys.path.insert(0, str(PLUGIN / "hooks" / "engine"))

from hooks.engine.modules import config  # noqa: E402
from hooks.engine.modules.guard import guard, quality  # noqa: E402
from hooks.engine.modules.utils import scan_dod_items  # noqa: E402


def _load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, PLUGIN / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


S_GEN = _load_module("roundtrip_s_gen", "create-methodology-record.py")
QR_GEN = _load_module("roundtrip_qr_gen", "create-qr-record.py")

NATIVE = """---
experiment_refs:
  - id: E-900
    scope: "AC-001, AC-002 — roundtrip scope"
    status: APPROVED
---

# Story 9.1: Roundtrip fixture story

Status: ready-for-dev

- **Story key:** 9-1-roundtrip
- **Sprint:** SP-900
- **Priority:** High
- **Story points:** 1
- **Epic:** epic-9

## Acceptance Criteria

1. [AC-001] **Given** X **When** Y **Then** Z
   - Experiment: E-900
   - Type: agent-verifiable
   - Measured: true
   - Verify: run the thing

2. [AC-002] **Given** A **When** B **Then** C
   - Experiment: E-900
   - Type: agent-verifiable
   - Measured: true
   - Verify: run the other thing

## Technical Tasks

- [ ] Task 1: do the thing (AC: AC-001)
- [ ] Task 2: do the other thing (AC: AC-002)

## Definition of Done

- [DoD-001] All acceptance criteria met (AC: AC-001, AC-002)
  - Verify: run both things
  - Evidence: outputs
- [DoD-002] Change reviewed and recorded (code review)
  - Verify: QR record
  - Evidence: QR record
"""


def _throwaway_root(tmp_path: Path, monkeypatch) -> Path:
    """Stage template + native story + minimal SP/IR chain on a clean root."""
    root = tmp_path / "proj"
    stories = root / "docs" / "development" / "stories"
    stories.mkdir(parents=True)
    (stories / "_template_S.md").write_text(
        (PLUGIN / "docs" / "development" / "stories" / "_template_S.md"
         ).read_text(encoding="utf-8"), encoding="utf-8")
    native_dir = root / "docs" / "development" / "native"
    native_dir.mkdir(parents=True)
    (native_dir / "9-1-roundtrip.md").write_text(NATIVE, encoding="utf-8")
    (root / "docs" / "development" / "SP-900.md").write_text(
        "# Sprint: SP-900\n\n- **Status:** in-progress\n"
        "- **Readiness input:** IR-900\n"
        "- **Stories:**\n  - S-001: Roundtrip fixture (native 9-1-roundtrip)\n",
        encoding="utf-8")
    (root / "docs" / "development" / "IR-900.md").write_text(
        "# Implementation Readiness: IR-900\n\n- **Status:** READY\n"
        "- **Research inputs:**\n  - E-900 (roundtrip fixture)\n",
        encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)
    monkeypatch.setattr(config, "hook_gate_mode", lambda key: "hard")
    return root


def _generate(root: Path) -> Path:
    meta = S_GEN.extract_story_metadata(NATIVE)
    meta["native_story_path"] = "docs/development/native/9-1-roundtrip.md"
    return S_GEN.create_methodology_record(meta, 1, root)


def test_s_record_survives_guard_write(tmp_path, monkeypatch):
    """Generator output passes the story-write path (orphan via table linkage)."""
    root = _throwaway_root(tmp_path, monkeypatch)
    content = _generate(root).read_text(encoding="utf-8")
    res = guard({"tool_name": "file_editor",
                 "tool_input": {"path": "docs/development/stories/S-001.md",
                                "content": content}})
    assert res["decision"] == "allow", res.get("reason", "")


def test_s_record_survives_hard_quality_commit(tmp_path, monkeypatch):
    """Generator output commits clean under the hard quality gate."""
    root = _throwaway_root(tmp_path, monkeypatch)
    _generate(root)
    res = quality({"tool_name": "terminal",
                   "tool_input": {"command": "git commit -m 'roundtrip'"}})
    assert res["decision"] == "allow", res.get("reason", "")
    assert not res.get("methodology_warnings", ""), \
        res.get("methodology_warnings", "")


def test_done_record_with_qr_survives_hard_quality_commit(tmp_path, monkeypatch):
    """A finished generated record + staged QR/IR/SP commits under hard."""
    root = _throwaway_root(tmp_path, monkeypatch)
    out = _generate(root)
    content = out.read_text(encoding="utf-8")
    done = re.sub(r"\|\s*Status\s*\|\s*ready-for-dev\s*\|",
                  "| Status | done |", content, count=1)
    assert done != content, "fixture must flip to done"
    done = done.replace("⏳ pending", "✅ done")
    out.write_text(done, encoding="utf-8")
    (root / "docs" / "quality").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "quality" / "QR-001.md").write_text(
        "# Quality Record: QR-001\n\n- **Status:** APPROVED\n\n"
        # The declaration row create-qr-record.py writes — the gate reads this,
        # not a mention of the key in the record's body.
        "| Story | 9-1-roundtrip |\n\n"
        "| DoD Item | Status | Evidence | Date |\n"
        "|----------|-------|-------|-------|\n"
        "| DoD-001 — All acceptance criteria met | ✅ done | outputs | today |\n"
        "| DoD-002 — Change reviewed | ✅ done | QR record | today |\n",
        encoding="utf-8")
    res = guard({"tool_name": "file_editor",
                 "tool_input": {"path": "docs/development/stories/S-001.md",
                                "content": done}})
    assert res["decision"] == "allow", res.get("reason", "")
    res_q = quality({"tool_name": "terminal",
                     "tool_input": {"command": "git commit -m 'roundtrip done'"}})
    assert res_q["decision"] == "allow", res_q.get("reason", "")


def _dod_section(text: str) -> str:
    m = re.search(
        r"##\s+Definition\s+of\s+Done\s*\n(.*?)(?=\n##\s|\Z)",
        text, re.DOTALL | re.IGNORECASE)
    return m.group(1) if m else ""


def test_qr_rewrite_agrees_with_engine_scanner(tmp_path, monkeypatch):
    """The QR rewrite path and the engine see the same DoD items."""
    root = _throwaway_root(tmp_path, monkeypatch)
    story = root / "docs" / "development" / "native" / "9-1-roundtrip.md"
    qr = root / "docs" / "quality" / "QR-001.md"
    qr.parent.mkdir(parents=True, exist_ok=True)
    qr.write_text("# Quality Record: QR-001\n", encoding="utf-8")
    items = QR_GEN.extract_dod_items(story.read_text(encoding="utf-8"))
    assert [i["id"] for i in items] == ["DoD-001", "DoD-002"]
    QR_GEN.update_story_qr_section(story, qr, items, root)
    rewritten = story.read_text(encoding="utf-8")
    engine_ids = [re.search(r"DoD-\d+", i["first"]).group(0)
                  for i in scan_dod_items(_dod_section(rewritten))]
    assert engine_ids == ["DoD-001", "DoD-002"], engine_ids

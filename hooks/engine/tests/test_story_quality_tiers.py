"""Rigor-tier fast path: a story's declared risk controls upstream ceremony.

Tiers only decide which UPSTREAM planning records are required. They never
relax the QR requirement (evidence that the work was verified) and an absent or
malformed tier falls back to Tier 3.
"""

import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HOOKS))

from modules.config import story_rigor_tier  # noqa: E402
from modules import story_quality  # noqa: E402


def _story(root: Path, tier: str, *, status: str = "done", sprint: str = "") -> Path:
    stories = root / "docs" / "development" / "stories"
    stories.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Story: S-001 — tier fixture",
        "- **Status:** " + status,
        "- **Sprint:** " + (sprint or "SP-001"),
    ]
    if tier:
        lines.append("- **Rigor Tier:** " + tier)
    lines += [
        "## Acceptance Criteria",
        "- [AC-001] **Given** a case **When** run **Then** it passes",
        "  - Experiment: STRUCT-001",
        "  - Type: agent-verifiable",
        "  - Measured: false",
        "  - Verify: inspection",
        "",
    ]
    path = stories / "S-001.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def test_tier_defaults_to_full_chain_when_absent():
    assert story_rigor_tier("# Story\n- **Status:** done\n") == 3


def test_tier_parses_both_field_spellings():
    assert story_rigor_tier("- **Rigor Tier:** 1\n") == 1
    assert story_rigor_tier("Tier: 2\n") == 2
    assert story_rigor_tier("- **Rigor Tier:** 3\n") == 3


def test_tier_parses_generated_record_table_row():
    # create-methodology-record.py writes the tier as a bridge table row.
    assert story_rigor_tier("| Rigor Tier | 1 |\n") == 1
    assert story_rigor_tier("| Tier | 2 |\n") == 2


def test_invalid_tier_falls_back_to_strict():
    # A typo must never silently buy a fast path.
    assert story_rigor_tier("- **Rigor Tier:** 9\n") == 3
    assert story_rigor_tier("- **Rigor Tier:** two\n") == 3

def test_multi_digit_tier_is_not_read_as_its_first_digit():
    # `12` used to parse as Tier 1 and `2.5`/`2x` as Tier 2 — a malformed value
    # bought the fast path the fallback exists to deny (E-125).
    for bad in ("12", "13", "10", "2.5", "2x"):
        assert story_rigor_tier(f"- **Rigor Tier:** {bad}\n") == 3
        assert story_rigor_tier(f"| Rigor Tier | {bad} |\n") == 3
    assert story_rigor_tier("- **Rigor Tier:** 2 (feature)\n") == 2


def test_tier_one_and_two_skip_ir_requirement(tmp_path):
    _story(tmp_path, "2")
    assert story_quality._find_done_stories_without_ir(str(tmp_path)) == []


def test_tier_three_requires_ir(tmp_path):
    _story(tmp_path, "3")
    assert story_quality._find_done_stories_without_ir(str(tmp_path)) == ["S-001"]


def test_legacy_story_without_tier_keeps_ir_requirement(tmp_path):
    _story(tmp_path, "")
    assert story_quality._find_done_stories_without_ir(str(tmp_path)) == ["S-001"]


def test_tier_two_skips_sp_requirement(tmp_path):
    _story(tmp_path, "2", sprint="SP-001")
    assert story_quality._find_done_stories_without_sp(str(tmp_path)) == []


def test_tier_three_requires_sp(tmp_path):
    _story(tmp_path, "3", sprint="SP-001")
    assert story_quality._find_done_stories_without_sp(str(tmp_path)) == ["S-001"]


def test_tier_one_still_requires_qr(tmp_path):
    # The fast path removes planning ceremony, never the quality record.
    _story(tmp_path, "1")
    assert story_quality._find_done_stories_without_qr(str(tmp_path)) == ["S-001"]

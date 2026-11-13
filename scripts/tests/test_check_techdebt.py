"""Regression guards for ``scripts/check-techdebt.sh`` (E-015).

The tech-debt standard puts a debt marker in the CODE as
``# TODO: [TD-XXX]`` and the manifesto forbids hidden debt — yet the audit's
§5 scanned only ``scratch/`` and ``custom/``, so a marker in the shipped code
(``hooks/``, ``scripts/``, ``bmad/``, ``skills/``) was invisible. The same
blindness hid a malformed reference: ``hooks/engine/modules/blackboard.py``
carried ``(TD-14)`` while the inventory records ``TD-014``, and no check read
bare ``TD-NNN`` tokens at all.

These tests drive the real auditor against a throwaway fixture tree. The script
derives every path from its own location (``$0`` -> ``scripts/`` -> root), so a
copied tree is hermetic — the same fixture discipline TD-015 established for
the scope guards, and the reason these tests never touch the live inventory.
"""

import shutil
import subprocess
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
CHECK = PLUGIN / "scripts" / "check-techdebt.sh"
TEMPLATE = PLUGIN / "templates" / "tech-debt.md"
LIVE = PLUGIN / "docs" / "development" / "tech-debt.md"


def _fixture(tmp_path: Path) -> Path:
    """A minimal plugin tree with the real inventory pair + auditor.

    The pair is deliberately NOT identical since E-116: the template ships with
    placeholders while the live ledger is filled, and only the structure is
    shared.
    """
    root = tmp_path / "plugin"
    (root / "scripts").mkdir(parents=True)
    (root / "templates").mkdir(parents=True)
    (root / "docs" / "development").mkdir(parents=True)
    shutil.copy(CHECK, root / "scripts" / "check-techdebt.sh")
    shutil.copy(TEMPLATE, root / "templates" / "tech-debt.md")
    shutil.copy(LIVE, root / "docs" / "development" / "tech-debt.md")
    # §8 derives the metrics block from the sprint records, so the fixture needs
    # them: a ledger whose trend table names sprints the fixture cannot see is
    # indistinguishable from a stale one.
    for sp in sorted((PLUGIN / "docs" / "development").glob("SP-*.md")):
        shutil.copy(sp, root / "docs" / "development" / sp.name)
    return root


def _run(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["sh", str(root / "scripts" / "check-techdebt.sh")],
        cwd=str(root), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=60,
        stdin=subprocess.DEVNULL,  # pytest's fd capture leaves stdin unusable on Windows
    )


def test_clean_fixture_is_healthy(tmp_path):
    root = _fixture(tmp_path)
    (root / "hooks").mkdir()
    (root / "hooks" / "app.py").write_text("x = 1\n", encoding="utf-8")
    r = _run(root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "STATUS: HEALTHY" in r.stdout


def test_orphan_todo_in_code_is_caught(tmp_path):
    """A marker in the shipped code is the exact blind spot §5 used to have."""
    root = _fixture(tmp_path)
    (root / "hooks").mkdir()
    (root / "hooks" / "app.py").write_text(
        "# TODO: [TD-999] orphan marker\n", encoding="utf-8")
    r = _run(root)
    assert r.returncode == 1, r.stdout
    assert "TD-999" in r.stdout and "orphan TODO" in r.stdout


def test_malformed_td_reference_is_caught(tmp_path):
    """A bare `TD-14` where the inventory records `TD-014` is drift (§6)."""
    root = _fixture(tmp_path)
    (root / "hooks").mkdir()
    (root / "hooks" / "app.py").write_text("# bound by TD-14\n", encoding="utf-8")
    r = _run(root)
    assert r.returncode == 1, r.stdout
    assert "TD-14" in r.stdout and "not in the inventory" in r.stdout
    # §5 must stay silent on a bare token (only its heading names "orphan TODO")
    assert "orphan TODO (not in inventory)" not in r.stdout


def test_recorded_todo_passes(tmp_path):
    """A marker naming a real (paid) debt is not an orphan."""
    root = _fixture(tmp_path)
    (root / "hooks").mkdir()
    (root / "hooks" / "app.py").write_text(
        "# TODO: [TD-001] revisit after the next sprint\n", encoding="utf-8")
    r = _run(root)
    assert r.returncode == 0, r.stdout
    assert "all TODO [TD-XXX] are recorded in the inventory" in r.stdout


def test_inventory_example_does_not_false_positive(tmp_path):
    """The standard's own TD-042 example lives in the excluded inventory pair."""
    root = _fixture(tmp_path)
    r = _run(root)
    assert r.returncode == 0, r.stdout
    assert "TD-042" not in r.stdout


def test_template_and_live_may_differ(tmp_path):
    """E-116: §1 compares shape, not bytes — the pair is not a twin."""
    root = _fixture(tmp_path)
    live = root / "docs" / "development" / "tech-debt.md"
    live.write_text(
        live.read_text(encoding="utf-8").replace(
            "# Technical Debt Tracking\n",
            "# Technical Debt Tracking\n\nThis ledger is filled; the template is not.\n",
            1),
        encoding="utf-8")
    r = _run(root)
    assert r.returncode == 0, r.stdout
    assert "follows templates/tech-debt.md" in r.stdout


def test_live_placeholder_is_caught(tmp_path):
    """E-116 §7: a template slot left in the live ledger means it was never read."""
    root = _fixture(tmp_path)
    live = root / "docs" / "development" / "tech-debt.md"
    live.write_text(live.read_text(encoding="utf-8")
                    + "\n- **Total active debt:** [X items]\n", encoding="utf-8")
    r = _run(root)
    assert r.returncode == 1, r.stdout
    assert "placeholder" in r.stdout.lower()


def test_template_without_placeholder_is_caught(tmp_path):
    """E-116 §7: a template that lost its slots is a filled ledger, not a template."""
    root = _fixture(tmp_path)
    live = root / "docs" / "development" / "tech-debt.md"
    shutil.copy(live, root / "templates" / "tech-debt.md")
    r = _run(root)
    assert r.returncode == 1, r.stdout
    assert "not a template" in r.stdout


# --- §8: the metrics block is derived, not trusted --------------------------
# The reporting half used to be unread: §7 catches a template placeholder, but a
# WRONG number is a perfectly well-formed sentence. `Total active debt: 0` next
# to a full active table, or a trend table that stops at a sprint the record
# series has since passed, both passed every check.

def _ledger(root: Path) -> Path:
    return root / "docs" / "development" / "tech-debt.md"


def test_metrics_must_match_the_active_table(tmp_path):
    root = _fixture(tmp_path)
    live = _ledger(root)
    live.write_text(
        live.read_text(encoding="utf-8").replace(
            "- **Total active debt:** 0 items", "- **Total active debt:** 3 items", 1),
        encoding="utf-8")
    r = _run(root)
    assert r.returncode == 1, r.stdout
    assert "Total active debt says 3" in r.stdout


def test_metrics_priority_counts_must_match_their_subsections(tmp_path):
    root = _fixture(tmp_path)
    live = _ledger(root)
    live.write_text(
        live.read_text(encoding="utf-8").replace(
            "  - P1 (High): 0 items", "  - P1 (High): 2 items", 1),
        encoding="utf-8")
    r = _run(root)
    assert r.returncode == 1, r.stdout
    assert "P1 says 2" in r.stdout


def test_trend_table_must_carry_every_recorded_sprint(tmp_path):
    root = _fixture(tmp_path)
    live = _ledger(root)
    text = live.read_text(encoding="utf-8")
    # Drop the newest sprint's row: the record series has moved on.
    rows = [ln for ln in text.splitlines() if not ln.startswith("SP-017 (")]
    live.write_text("\n".join(rows) + "\n", encoding="utf-8")
    r = _run(root)
    assert r.returncode == 1, r.stdout
    assert "no row for" in r.stdout and "SP-017" in r.stdout


def test_trend_table_rejects_a_sprint_the_series_has_not_reached(tmp_path):
    root = _fixture(tmp_path)
    live = _ledger(root)
    text = live.read_text(encoding="utf-8").replace(
        "SP-017 (11-09)      | 0        | 0         | 0          | 0",
        "SP-017 (11-09)      | 0        | 0         | 0          | 0\n"
        "SP-077 (12-01)      | 0        | 0         | 0          | 0", 1)
    live.write_text(text, encoding="utf-8")
    r = _run(root)
    assert r.returncode == 1, r.stdout
    assert "SP-077" in r.stdout


def test_paid_in_last_three_sprints_is_derived(tmp_path):
    """The window is the last three RECORDED sprints, not a hand-typed number."""
    root = _fixture(tmp_path)
    live = _ledger(root)
    text = live.read_text(encoding="utf-8").replace(
        "- **Debt paid in the last 3 sprints:** 1 item", 
        "- **Debt paid in the last 3 sprints:** 4 items", 1)
    live.write_text(text, encoding="utf-8")
    r = _run(root)
    assert r.returncode == 1, r.stdout
    assert "Debt paid in the last 3 sprints says 4" in r.stdout


def test_clean_fixture_reports_the_derivation(tmp_path):
    root = _fixture(tmp_path)
    (root / "hooks").mkdir()
    (root / "hooks" / "app.py").write_text("x = 1\n", encoding="utf-8")
    r = _run(root)
    assert r.returncode == 0, r.stdout
    assert "metrics agree with the tables" in r.stdout

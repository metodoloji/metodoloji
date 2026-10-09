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
    """A minimal plugin tree with the real (identical) inventory pair + auditor."""
    root = tmp_path / "plugin"
    (root / "scripts").mkdir(parents=True)
    (root / "templates").mkdir(parents=True)
    (root / "docs" / "development").mkdir(parents=True)
    shutil.copy(CHECK, root / "scripts" / "check-techdebt.sh")
    shutil.copy(TEMPLATE, root / "templates" / "tech-debt.md")
    shutil.copy(LIVE, root / "docs" / "development" / "tech-debt.md")
    return root


def _run(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["sh", str(root / "scripts" / "check-techdebt.sh")],
        cwd=str(root), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=60,
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

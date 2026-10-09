"""Regression guards for ``scripts/check-triggers.py`` (E-017).

The trigger audit is one of the "check scripts" E-009 explicitly left
unprobed, and it was in the same class: ``_frontmatter`` read with
``open(path, encoding="utf-8").read()`` and no guard, so a skill file that is
not valid UTF-8 (written by another editor, a merge, or a damaged disk) crashed
the whole audit with a ``UnicodeDecodeError`` — a ValueError, not an OSError.

A crash here is the worst possible result for an audit: it reports nothing,
and a runner that ignores the traceback sees success. The contract now: an
unreadable skill file is NAMED as a problem and the audit still exits 1.
"""

import subprocess
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
SCRIPT = PLUGIN / "scripts" / "check-triggers.py"


def _run(project_root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(project_root)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60,
    )


def test_binary_skill_file_is_reported_not_crashed(tmp_path):
    skill = tmp_path / "skills" / "binary-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_bytes(b"name: binary\n\xff\xfe binary junk")

    r = _run(tmp_path)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "UNREADABLE" in r.stdout and "not valid UTF-8" in r.stdout
    assert "Traceback" not in (r.stdout + r.stderr)


def test_clean_skills_still_pass(tmp_path):
    """The report path must not make a healthy tree fail."""
    skill = tmp_path / "skills" / "one-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\ndescription: x (BMad Method)\ntriggers: []\n---\n# s\n",
        encoding="utf-8")

    r = _run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "checked skills: 1" in r.stdout
    assert "Traceback" not in (r.stdout + r.stderr)

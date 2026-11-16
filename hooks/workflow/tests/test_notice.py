"""The human channel: one bounded status line per workflow edge (E-175).

Before this module the workflow kernel spoke only to the model — `advisory.peek`
rode the session context and the CLI printed JSON to the agent — so a run could
advance with nobody at the keyboard knowing. These tests pin the user-facing
line: silent when nothing runs, bounded and naming the run when something does,
and in the project's own language.
"""

import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from hooks.workflow import notice  # noqa: E402

CLI = _ROOT / "hooks" / "scripts" / "workflow.py"


def _create_run(project: Path, slug: str = "demo") -> None:
    result = subprocess.run(
        [sys.executable, str(CLI), "create", "--builtin", "methodology-chain",
         "--slug", slug, "--project-root", str(project)],
        capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert result.returncode == 0, result.stderr


def test_no_line_when_nothing_is_running(tmp_path):
    assert notice.session_line(str(tmp_path)) == ""
    assert notice.turn_line(str(tmp_path)) == ""
    assert notice.stop_line(str(tmp_path)) == ""


def test_every_edge_names_the_run_and_stays_on_one_screen_line(tmp_path):
    _create_run(tmp_path)
    for line in (notice.session_line(str(tmp_path)),
                 notice.turn_line(str(tmp_path)),
                 notice.stop_line(str(tmp_path))):
        assert "demo" in line
        assert "\n" not in line
        assert len(line) <= notice.MAX_CHARS


def test_the_line_follows_the_project_language(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "config.toml").write_text(
        '[core]\ncommunication_language = "Turkish"\n', encoding="utf-8")
    _create_run(tmp_path)
    assert notice.session_line(str(tmp_path)).startswith("Metodoloji zinciri")

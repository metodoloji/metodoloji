#!/usr/bin/env python3
"""Tests for scripts/auto_iterate.py — the bounded auto-iterate harness.

Covers the pure helpers and the loop's three exits (PASS, ROUNDS EXHAUSTED,
regression revert) with fake eval/improve commands. Import-based, no subprocess
except the deliberately tiny shell commands the harness itself runs.
"""

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

import auto_iterate as ai  # noqa: E402


# --- _parse_score ------------------------------------------------------------

def test_parse_score_bare_float():
    assert ai._parse_score("0.75") == 0.75


def test_parse_score_json():
    assert ai._parse_score('{"score": 0.8, "modes": {}}') == 0.8


def test_parse_score_json_with_ws():
    assert ai._parse_score('  {"score": 0.9}  ') == 0.9


def test_parse_score_garbage():
    assert ai._parse_score("not a number") == 0.0


def test_parse_score_empty():
    assert ai._parse_score("") == 0.0


# --- _log --------------------------------------------------------------------

def test_log_appends_typed_entry(tmp_path):
    m = tmp_path / "trail.md"
    ai._log(m, "decision", "propose fix")
    ai._log(m, "event", "score 0.5")
    text = m.read_text(encoding="utf-8")
    assert "- (decision) propose fix" in text
    assert "- (event) score 0.5" in text
    lines = text.strip().splitlines()
    assert len(lines) == 2  # append-only, two entries


def test_log_creates_parent(tmp_path):
    m = tmp_path / "nested" / "deep" / "trail.md"
    ai._log(m, "note", "x")
    assert m.exists()


# --- _run --------------------------------------------------------------------

def test_run_substitutes_skill(tmp_path):
    skill = tmp_path / "s.md"
    skill.write_text("x", encoding="utf-8")
    # Echo the substituted path so Windows backslashes don't trip a nested
    # python -c string; we only verify the substitution happened.
    rc, out = ai._run("echo SUB {skill}", str(skill))
    assert rc == 0
    assert "SUB" in out
    assert "s.md" in out


# --- _apply_improvement ------------------------------------------------------

def test_apply_improvement_writes_stdout(tmp_path):
    skill = tmp_path / "s.md"
    skill.write_text("old", encoding="utf-8")
    ok, detail = ai._apply_improvement(skill, "echo new content")
    assert ok is True
    assert skill.read_text(encoding="utf-8") == "new content\n"
    assert "applied" in detail


def test_apply_improvement_failed(tmp_path):
    skill = tmp_path / "s.md"
    skill.write_text("old", encoding="utf-8")
    ok, detail = ai._apply_improvement(skill, "exit 3")
    assert ok is False
    assert skill.read_text(encoding="utf-8") == "old"  # untouched


def test_apply_improvement_empty_output(tmp_path):
    skill = tmp_path / "s.md"
    skill.write_text("old", encoding="utf-8")
    ok, _ = ai._apply_improvement(skill, "true")  # prints nothing
    assert ok is False


# --- main: loop exits ---------------------------------------------------------

def _write_skill(tmp_path, content="CLEAN"):
    skill = tmp_path / "skill.md"
    skill.write_text(content, encoding="utf-8")
    return skill


def test_main_pass_on_first_eval(tmp_path):
    skill = _write_skill(tmp_path)
    rc = ai.main(["--skill", str(skill), "--eval", "echo 0.95",
                  "--improve", "echo x", "--rounds", "3",
                  "--pass-threshold", "0.9", "--trail", str(tmp_path / "t.md")])
    assert rc == 0


def test_main_rounds_exhausted(tmp_path):
    skill = _write_skill(tmp_path)
    rc = ai.main(["--skill", str(skill), "--eval", "echo 0.5",
                  "--improve", "echo x", "--rounds", "2",
                  "--pass-threshold", "0.9", "--trail", str(tmp_path / "t.md")])
    assert rc == 1  # threshold never met


def test_main_revert_on_regression(tmp_path):
    skill = _write_skill(tmp_path, "CLEAN")
    # eval: BROKEN in skill → 0.3, else 0.8. improve writes BROKEN.
    eval_cmd = "python3 -c \"import sys; print('0.3' if 'BROKEN' in open(sys.argv[1]).read() else '0.8')\" {skill}"
    improve_cmd = "python3 -c \"print('BROKEN skill')\""
    rc = ai.main(["--skill", str(skill), "--eval", eval_cmd,
                  "--improve", improve_cmd, "--rounds", "1",
                  "--pass-threshold", "0.95", "--trail", str(tmp_path / "t.md")])
    assert rc == 1  # regression reverted, no pass
    assert skill.read_text(encoding="utf-8") == "CLEAN"  # reverted


def test_main_trail_written(tmp_path):
    skill = _write_skill(tmp_path)
    trail = tmp_path / "t.md"
    ai.main(["--skill", str(skill), "--eval", "echo 0.5",
             "--improve", "echo x", "--rounds", "1",
             "--pass-threshold", "0.9", "--trail", str(trail)])
    text = trail.read_text(encoding="utf-8")
    assert "auto-iterate start" in text
    assert "(event)" in text and "(decision)" in text


def test_main_missing_skill_usage_error(tmp_path):
    rc = ai.main(["--skill", str(tmp_path / "nope.md"), "--eval", "echo",
                  "--improve", "echo"])
    assert rc == 2

from __future__ import annotations

import os

import pytest

from optimization.env import find, load_dotenv, parse


def test_parse_accepts_comments_export_and_quotes():
    values = parse(
        "# training endpoint\n"
        "\n"
        "SKILL_EVAL_BASE_URL=https://example.test/v1\n"
        "export SKILL_EVAL_MODEL=\"beam 1\"\n"
        "SKILL_EVAL_API_KEY='sk-test'  # inline comment\n"
        "SKILL_EVAL_MAX_TOKENS=4096\n"
    )
    assert values == {
        "SKILL_EVAL_BASE_URL": "https://example.test/v1",
        "SKILL_EVAL_MODEL": "beam 1",
        "SKILL_EVAL_API_KEY": "sk-test",
        "SKILL_EVAL_MAX_TOKENS": "4096",
    }


def test_parse_rejects_a_line_without_a_value():
    with pytest.raises(ValueError, match="KEY=VALUE"):
        parse("SKILL_EVAL_API_KEY\n")


def test_load_dotenv_never_replaces_a_real_variable(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "SKILL_EVAL_MODEL=from-file\nSKILL_EVAL_BASE_URL=https://file.test\n",
        encoding="utf-8")
    monkeypatch.setenv("SKILL_EVAL_MODEL", "from-shell")
    monkeypatch.delenv("SKILL_EVAL_BASE_URL", raising=False)

    loaded = load_dotenv(tmp_path)

    assert loaded == [tmp_path / ".env"]
    assert os.environ["SKILL_EVAL_MODEL"] == "from-shell"
    assert os.environ["SKILL_EVAL_BASE_URL"] == "https://file.test"


def test_nearest_dotenv_wins_and_parents_fill_the_gaps(tmp_path, monkeypatch):
    inner = tmp_path / "skills" / "skill"
    inner.mkdir(parents=True)
    (tmp_path / ".env").write_text(
        "SKILL_EVAL_MODEL=outer\nSKILL_EVAL_MAX_TOKENS=1024\n", encoding="utf-8")
    (inner / ".env").write_text("SKILL_EVAL_MODEL=inner\n", encoding="utf-8")
    for key in ("SKILL_EVAL_MODEL", "SKILL_EVAL_MAX_TOKENS"):
        monkeypatch.delenv(key, raising=False)

    loaded = load_dotenv(inner)

    assert find(inner)[0] == inner / ".env"
    assert loaded[:2] == [inner / ".env", tmp_path / ".env"]
    assert os.environ["SKILL_EVAL_MODEL"] == "inner"
    assert os.environ["SKILL_EVAL_MAX_TOKENS"] == "1024"

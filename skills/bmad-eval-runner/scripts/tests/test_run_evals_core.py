#!/usr/bin/env python3
"""Tests for run_evals.py core helpers — adapter, staging, prompts, accounting.

Covers the pure helpers beyond the env-isolation contract already pinned in
test_env_isolation.py: adapter discovery, argv substitution, skill/fixture
staging, state_prefix composition, transcript/token accounting, and the
skip / adapter-missing / timeout result paths of run_case.
Run with: python3 -m pytest test_run_evals_core.py
"""

import json
import os
import sys
import time
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

import run_evals  # noqa: E402


# --- find_adapter -----------------------------------------------------------

def test_find_adapter_explicit(tmp_path):
    f = tmp_path / "adapter.json"
    f.write_text("{}", encoding="utf-8")
    cases = tmp_path / "cases.json"
    cases.write_text("[]", encoding="utf-8")
    assert run_evals.find_adapter(f, cases) == f


def test_find_adapter_explicit_missing(tmp_path):
    cases = tmp_path / "cases.json"
    cases.write_text("[]", encoding="utf-8")
    assert run_evals.find_adapter(tmp_path / "nope.json", cases) is None


def test_find_adapter_env(monkeypatch, tmp_path):
    f = tmp_path / "env-adapter.json"
    f.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("BMAD_EVAL_ADAPTER", str(f))
    cases = tmp_path / "cases.json"
    cases.write_text("[]", encoding="utf-8")
    assert run_evals.find_adapter(None, cases) == f


def test_find_adapter_sibling(tmp_path):
    cases = tmp_path / "cases.json"
    cases.write_text("[]", encoding="utf-8")
    (tmp_path / "adapter.json").write_text("{}", encoding="utf-8")
    assert run_evals.find_adapter(None, cases) == tmp_path / "adapter.json"


def test_find_adapter_9router(tmp_path):
    cases = tmp_path / "cases.json"
    cases.write_text("[]", encoding="utf-8")
    (tmp_path / "adapter-9router.json").write_text("{}", encoding="utf-8")
    assert run_evals.find_adapter(None, cases) == tmp_path / "adapter-9router.json"


def test_find_adapter_prefers_plain_over_9router(tmp_path):
    cases = tmp_path / "cases.json"
    cases.write_text("[]", encoding="utf-8")
    (tmp_path / "adapter.json").write_text("{}", encoding="utf-8")
    (tmp_path / "adapter-9router.json").write_text("{}", encoding="utf-8")
    assert run_evals.find_adapter(None, cases) == tmp_path / "adapter.json"


def test_find_adapter_none(tmp_path):
    cases = tmp_path / "cases.json"
    cases.write_text("[]", encoding="utf-8")
    assert run_evals.find_adapter(None, cases) is None


def test_load_adapter_requires_invocation(tmp_path):
    f = tmp_path / "bad.json"
    f.write_text('{"no": "invocation"}', encoding="utf-8")
    with pytest.raises(ValueError):
        run_evals.load_adapter(f)


# --- build_argv ------------------------------------------------------------

def test_build_argv_substitutes():
    argv = run_evals.build_argv(
        ["my-runner", "{prompt}", "{cwd}"], "the prompt", "/tmp/work")
    assert argv == ["my-runner", "the prompt", "/tmp/work"]


def test_build_argv_query_alias():
    argv = run_evals.build_argv(["{query}"], "q", "/w")
    assert argv == ["q"]


def test_build_argv_no_placeholders():
    argv = run_evals.build_argv(["ls", "-la"], "q", "/w")
    assert argv == ["ls", "-la"]


# --- compose_prompt ---------------------------------------------------------

def test_compose_prompt_no_prefix():
    assert run_evals.compose_prompt({"input": "hello"}) == "hello"


def test_compose_prompt_with_prefix():
    out = run_evals.compose_prompt(
        {"input": "continue", "state_prefix": "## State\nmid-work"})
    assert out == "## State\nmid-work\n\ncontinue"


def test_compose_prompt_missing_input():
    assert run_evals.compose_prompt({}) == ""


# --- stage_skill ------------------------------------------------------------

def test_stage_skill_symlink_or_copy(tmp_path):
    skill = tmp_path / "my-skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# S", encoding="utf-8")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    dest = run_evals.stage_skill(skill, cwd, ".claude/skills")
    assert (cwd / ".claude/skills/my-skill/SKILL.md").exists()
    assert dest.name == "my-skill"


# --- resolve_fixtures -------------------------------------------------------

def test_resolve_fixtures_project_root(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "fixture.txt").write_text("x", encoding="utf-8")
    cases = tmp_path / "cases"
    cases.mkdir()
    res = run_evals.resolve_fixtures(["fixture.txt"], root, cases)
    assert res == [(root / "fixture.txt", "fixture.txt")]


def test_resolve_fixtures_cases_dir(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    cases = tmp_path / "cases"
    cases.mkdir()
    (cases / "f.txt").write_text("x", encoding="utf-8")
    res = run_evals.resolve_fixtures(["f.txt"], root, cases)
    assert res == [(cases / "f.txt", "f.txt")]


def test_resolve_fixtures_missing_warns(tmp_path, capsys):
    root = tmp_path / "proj"
    root.mkdir()
    cases = tmp_path / "cases"
    cases.mkdir()
    res = run_evals.resolve_fixtures(["nope.txt"], root, cases)
    assert res == []
    assert "fixture not found" in capsys.readouterr().err


# --- account_transcript -----------------------------------------------------

def test_account_transcript_anthropic_shapes():
    transcript = "\n".join([
        json.dumps({"type": "assistant", "message": {
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "content": [{"type": "tool_use", "name": "Read", "id": "1"}]}}),
        json.dumps({"type": "result", "usage": {"input_tokens": 20, "output_tokens": 8}}),
    ])
    acc = run_evals.account_transcript(transcript)
    assert acc["input_tokens"] == 20  # result usage is authoritative
    assert acc["output_tokens"] == 8
    assert acc["total_tokens"] == 28
    assert acc["tokens_reported"] is True
    assert acc["total_steps"] == 1
    assert acc["tool_calls"].get("Read") == 1


def test_account_transcript_openhands_events():
    transcript = "\n".join([
        json.dumps({"kind": "ActionEvent", "tool_name": "terminal"}),
        json.dumps({"kind": "TokenEvent",
                    "prompt_token_ids": [1, 2, 3],
                    "response_token_ids": [4, 5]}),
    ])
    acc = run_evals.account_transcript(transcript)
    assert acc["total_steps"] == 1
    assert acc["input_tokens"] == 3
    assert acc["output_tokens"] == 2
    assert acc["tokens_reported"] is True


def test_account_transcript_garbage():
    acc = run_evals.account_transcript("not json\n\n[1,2,3]\n")
    assert acc["total_tokens"] == 0
    assert acc["tokens_reported"] is False


# --- read_transcript --------------------------------------------------------

def test_read_transcript_stdout_format():
    text, src = run_evals.read_transcript({}, b'{"a":1}\n', Path("."))
    assert text == '{"a":1}\n'
    assert src == "stdout"


def test_read_transcript_file_format(tmp_path):
    cfg = {"format": "file", "path": "transcript.jsonl"}
    (tmp_path / "transcript.jsonl").write_text("line1\n", encoding="utf-8")
    text, src = run_evals.read_transcript(cfg, b"", tmp_path)
    assert text == "line1\n"
    assert src == "file:transcript.jsonl"


def test_read_transcript_file_missing(tmp_path):
    cfg = {"format": "file", "path": "missing.jsonl"}
    text, src = run_evals.read_transcript(cfg, b"", tmp_path)
    assert text == ""
    assert src == "file:missing.jsonl (missing)"


# --- run_case: skip + adapter-missing ---------------------------------------

def test_run_case_no_adapter_skipped(tmp_path):
    case = {"id": "A1", "input": "do x"}
    case_dir = tmp_path / "run/skill/A1"
    case_dir.mkdir(parents=True)
    res = run_evals.run_case(
        case, case_dir, tmp_path / "run", None, 10, "skill", None, [])
    assert res["status"] == "skipped"
    assert "no runtime adapter" in res["reason"]
    assert (case_dir / "prompt.txt").exists()
    assert (case_dir / "case.json").exists()
    assert (case_dir / "timing.json").exists()


def test_run_case_adapter_missing_command(tmp_path, monkeypatch):
    case = {"id": "B2", "input": "do y"}
    case_dir = tmp_path / "run/skill/B2"
    case_dir.mkdir(parents=True)
    adapter = {"invocation": ["/nonexistent/runner", "{prompt}", "{cwd}"]}

    def _raise_not_found(*a, **k):
        raise FileNotFoundError(2, "No such file", "/nonexistent/runner")

    monkeypatch.setattr(run_evals.subprocess, "run", _raise_not_found)
    res = run_evals.run_case(
        case, case_dir, tmp_path / "run", adapter, 10, "skill",
        None, [])
    assert res["status"] == "adapter-missing"
    assert "not found" in res["reason"]


# --- load_cases -------------------------------------------------------------

def test_load_cases_list(tmp_path):
    f = tmp_path / "cases.json"
    f.write_text('[{"id": "A"}, {"id": "B"}]', encoding="utf-8")
    assert [c["id"] for c in run_evals.load_cases(f)] == ["A", "B"]


def test_load_cases_wrapped(tmp_path):
    f = tmp_path / "cases.json"
    f.write_text('{"cases": [{"id": "A"}]}', encoding="utf-8")
    assert [c["id"] for c in run_evals.load_cases(f)] == ["A"]


def test_load_cases_invalid(tmp_path):
    f = tmp_path / "cases.json"
    f.write_text('{"not": "cases"}', encoding="utf-8")
    with pytest.raises(ValueError):
        run_evals.load_cases(f)

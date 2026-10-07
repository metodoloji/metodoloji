#!/usr/bin/env python3
"""Guard trigger detection in run_triggers.py.

The stream-json init event lists every discovered skill by name, so any
detection that substring-matches the whole transcript reports a 100% trigger
rate. These tests pin the rule: only tool_use events (a Skill call naming the
synthetic skill, or a Read inside its directory) count as a load, and
substring-style load signals are rejected outright.
Run with: python3 -m pytest test_trigger_detection.py
(or plain `python3 test_trigger_detection.py` for a lightweight self-check).
"""
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from run_triggers import detect_load, validate_load_signal  # noqa: E402

NAME = "my-skill-trig-abc12345"


def line(obj) -> str:
    return json.dumps(obj)


def init_event() -> str:
    # Claude Code's init event advertises every discovered skill.
    return line({"type": "system", "subtype": "init",
                 "tools": ["Skill", "Read", "Bash"],
                 "slash_commands": [], "skills": [NAME, "other-skill"]})


def assistant(content) -> str:
    return line({"type": "assistant", "message": {"content": content}})


def test_init_event_alone_is_not_a_load():
    transcript = "\n".join([
        init_event(),
        assistant([{"type": "text", "text": "I can't help with that."}]),
        line({"type": "result", "usage": {}}),
    ])
    assert detect_load(transcript, {}, NAME) is False


def test_text_mention_is_not_a_load():
    transcript = assistant(
        [{"type": "text", "text": f"There is a skill called {NAME} available."}])
    assert detect_load(transcript, {}, NAME) is False


def test_skill_tool_call_is_a_load():
    transcript = "\n".join([
        init_event(),
        assistant([{"type": "tool_use", "name": "Skill",
                    "input": {"skill": NAME}}]),
    ])
    assert detect_load(transcript, {}, NAME) is True


def test_read_of_synthetic_skill_md_is_a_load():
    transcript = "\n".join([
        init_event(),
        assistant([{"type": "tool_use", "name": "Read",
                    "input": {"file_path":
                              f"/tmp/stage/.claude/skills/{NAME}/SKILL.md"}}]),
    ])
    assert detect_load(transcript, {}, NAME) is True


def test_unrelated_tool_calls_are_not_a_load():
    transcript = "\n".join([
        init_event(),
        assistant([{"type": "tool_use", "name": "Read",
                    "input": {"file_path": "/tmp/stage/notes.md"}}]),
        assistant([{"type": "tool_use", "name": "Skill",
                    "input": {"skill": "other-skill"}}]),
        assistant([{"type": "tool_use", "name": "Bash",
                    "input": {"command": f"echo {NAME}"}}]),
    ])
    assert detect_load(transcript, {}, NAME) is False


def test_custom_tool_names_from_load_signal():
    sig = {"skill_tool": "InvokeSkill", "read_tool": "OpenFile"}
    hit = assistant([{"type": "tool_use", "name": "InvokeSkill",
                      "input": {"name": NAME}}])
    miss = assistant([{"type": "tool_use", "name": "Skill",
                       "input": {"skill": NAME}}])
    assert detect_load(hit, sig, NAME) is True
    assert detect_load(miss, sig, NAME) is False, \
        "default tool name must not fire when the adapter renames it"


def test_garbage_lines_do_not_crash():
    transcript = "not json\n\n{\"type\": 42}\n[1,2,3]\n"
    assert detect_load(transcript, {}, NAME) is False


def oh_action(tool_name, action) -> str:
    # OpenHands CLI JSONL: SDK ActionEvent shape.
    return line({"kind": "ActionEvent", "tool_name": tool_name,
                 "action": action})


def test_openhands_invoke_skill_is_a_load():
    sig = {"skill_tool": "invoke_skill", "read_tool": "file_editor"}
    hit = oh_action("invoke_skill", {"name": NAME})
    miss = oh_action("terminal", {"command": f"echo {NAME}"})
    assert detect_load(hit, sig, NAME) is True
    assert detect_load(miss, sig, NAME) is False, \
        "OpenHands: unrelated tool call must not count as a load"


def test_openhands_file_editor_read_is_a_load():
    sig = {"skill_tool": "invoke_skill", "read_tool": "file_editor"}
    hit = oh_action("file_editor", {"command": "view",
                                    "path": f".agents/skills/{NAME}/SKILL.md"})
    miss = oh_action("file_editor", {"command": "view", "path": "README.md"})
    assert detect_load(hit, sig, NAME) is True
    assert detect_load(miss, sig, NAME) is False


def test_openhands_non_action_events_do_not_load():
    sig = {"skill_tool": "invoke_skill", "read_tool": "file_editor"}
    transcript = "\n".join([
        line({"kind": "MessageEvent", "content": f"try {NAME}"}),
        oh_action("unknown", "not-a-dict"),
    ])
    assert detect_load(transcript, sig, NAME) is False


def test_string_load_signal_rejected():
    for fn, args in ((validate_load_signal, ({"type": "string"},)),
                     (detect_load, ("", {"type": "string"}, NAME))):
        try:
            fn(*args)
        except ValueError:
            pass
        else:
            raise AssertionError(
                f"{fn.__name__} accepted a substring load_signal")


if __name__ == "__main__":
    test_init_event_alone_is_not_a_load()
    test_text_mention_is_not_a_load()
    test_skill_tool_call_is_a_load()
    test_read_of_synthetic_skill_md_is_a_load()
    test_unrelated_tool_calls_are_not_a_load()
    test_custom_tool_names_from_load_signal()
    test_garbage_lines_do_not_crash()
    test_openhands_invoke_skill_is_a_load()
    test_openhands_file_editor_read_is_a_load()
    test_openhands_non_action_events_do_not_load()
    test_string_load_signal_rejected()
    print("ok: trigger detection counts tool calls only; substring rejected")

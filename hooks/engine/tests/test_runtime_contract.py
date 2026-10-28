"""Shared runtime scenarios pin tool normalization and guard outcomes.

The JSON fixture is also consumed by the OpenCode bridge tests. These are
contract tests for the supported adapter paths, not a claim that unrecognized
or out-of-process tools are sandboxed.
"""

import json
import sys
from pathlib import Path

import pytest

ENGINE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE_ROOT))

from modules.guard import guard  # noqa: E402
from modules.utils import normalize_hook_input  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "runtime-contract.json"
SCENARIOS = json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda item: item["id"])
def test_runtime_scenario_has_expected_normalized_target_and_decision(
    scenario, tmp_path, monkeypatch
):
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "scratch").mkdir()
    (project / "docs").mkdir()
    monkeypatch.setenv("METODOLOJI_RUNTIME", scenario["runtime"])
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)

    payload = {
        "tool_name": scenario["tool_name"],
        "tool_input": scenario["tool_input"],
        "cwd": str(project),
    }
    normalized = normalize_hook_input(payload)
    assert normalized["tool_name"] == scenario["canonical_tool"]
    tool_input = normalized["tool_input"]
    actual_paths = []
    if normalized["tool_name"] in ("file_editor", "notebook_editor"):
        actual_paths = tool_input.get("paths") or (
            [tool_input["path"]] if tool_input.get("path") else []
        )
    assert actual_paths == scenario["canonical_paths"]

    result = guard(payload)
    assert result["decision"] == scenario["decision"], result
    assert bool(result.get("methodology_warnings")) is scenario["warning"], result


def test_fixture_covers_every_supported_runtime():
    assert {scenario["runtime"] for scenario in SCENARIOS} == {
        "claude", "openhands", "opencode", "hermes"
    }


@pytest.mark.parametrize(
    "scenario",
    [item for item in SCENARIOS if item["runtime"] == "hermes"],
    ids=lambda item: item["id"],
)
def test_hermes_adapter_payload_matches_shared_runtime_contract(scenario, tmp_path):
    sys.path.insert(0, str(ENGINE_ROOT.parent.parent))
    import hermes_adapter

    payload = hermes_adapter.build_payload(
        scenario["tool_name"], scenario["tool_input"], cwd=str(tmp_path)
    )
    assert payload["tool_name"] == scenario["tool_name"]
    assert payload["tool_input"] == scenario["tool_input"]
    assert payload["cwd"] == str(tmp_path)

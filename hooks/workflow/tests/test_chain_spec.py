"""The methodology chain spec: order, conditions, and a real gate behind it.

The v1 spec proved each stage with `contains: ["approved"]`, so six one-line
files completed the whole chain and a real APPROVED record was refused. These
tests pin the replacement: every stage's evidence is a command that asks the
gate which already owns that verdict, and the run is scoped to one story.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from hooks.workflow import engine, spec as spec_mod  # noqa: E402

CHAIN = _ROOT / "hooks" / "workflow" / "builtin" / "methodology-chain.json"
GATE = _ROOT / "hooks" / "scripts" / "chain-gate.py"


@pytest.fixture(scope="module")
def chain() -> dict:
    return spec_mod.load(CHAIN)


def test_every_stage_is_proven_by_the_chain_gate(chain):
    """No stage may complete on a keyword in a file (v1's defect)."""
    for stage in chain["stages"]:
        ev = stage.get("evidence")
        if ev is None:
            continue
        assert ev["kind"] == "command", f"{stage['id']} is not command evidence"
        argv = " ".join(ev["argv"])
        assert "chain-gate.py" in argv, f"{stage['id']} does not call the gate"
        assert "--stage" in ev["argv"] and stage["id"] in ev["argv"]
        assert "{slug}" in ev["argv"] and "{project-root}" in ev["argv"]


def test_the_chain_runs_in_order(chain):
    ids = [s["id"] for s in chain["stages"]]
    assert ids == ["experiment", "implementation-readiness", "sprint-planning",
                   "story", "quality-review", "production-readiness", "done"]
    for prev, nxt in zip(ids, ids[1:]):
        stage = spec_mod.stage_by_id(chain, prev)
        targets = [e.get("to") for e in stage.get("next", [])]
        assert nxt in targets, f"{prev} cannot reach {nxt} (edges: {targets})"


def test_tier_1_and_2_skip_the_upstream_planning_records(chain):
    edges = spec_mod.stage_by_id(chain, "experiment")["next"]
    by_tier = {e.get("when", {}).get("equals"): e["to"]
               for e in edges if e.get("when")}
    assert by_tier["1"] == "story"
    assert by_tier["2"] == "story"
    # The default (no tier, or Tier 3 by the config's fallback) is the full chain.
    assert edges[-1] == {"to": "implementation-readiness"}


def test_pr_runs_only_when_deploy_is_declared(chain):
    edges = spec_mod.stage_by_id(chain, "quality-review")["next"]
    assert edges[0]["when"] == {"key": "deploy", "equals": "true"}
    assert edges[0]["to"] == "production-readiness"
    assert edges[-1] == {"to": "done"}


def _run_gate(root: Path, stage: str, slug: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE), "--stage", stage, "--slug", slug,
         "--project-root", str(root)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, stdin=subprocess.DEVNULL)


def test_stub_files_do_not_satisfy_any_stage(tmp_path):
    """v1's defect, pinned: six files containing the word 'approved' passed."""
    (tmp_path / "docs" / "experiments").mkdir(parents=True)
    (tmp_path / "docs" / "development" / "stories").mkdir(parents=True)
    (tmp_path / "docs" / "quality").mkdir(parents=True)
    (tmp_path / "docs" / "experiments" / "E-001.md").write_text(
        "approved\n", encoding="utf-8")
    (tmp_path / "docs" / "development" / "stories" / "S-001.md").write_text(
        "approved\n", encoding="utf-8")
    (tmp_path / "docs" / "development" / "SP-001.md").write_text(
        "approved\n", encoding="utf-8")
    (tmp_path / "docs" / "development" / "IR-001.md").write_text(
        "approved\n", encoding="utf-8")
    (tmp_path / "docs" / "development" / "PR-001.md").write_text(
        "approved\n", encoding="utf-8")
    (tmp_path / "docs" / "quality" / "QR-001.md").write_text(
        "approved\n", encoding="utf-8")

    for stage in ("experiment", "implementation-readiness", "sprint-planning",
                  "story", "quality-review", "production-readiness"):
        proc = _run_gate(tmp_path, stage, "S-001")
        assert proc.returncode != 0, (
            f"stage '{stage}' accepted stub records: {proc.stdout}")


def test_completion_without_the_current_stage_is_refused(tmp_path):
    data = spec_mod.load(CHAIN)
    engine.create(str(tmp_path), data, slug="S-001", flags={"tier": "1"})
    # tier=1 goes experiment → story; asking for quality-review first is refused
    # before any evidence is read.
    res = engine.complete(str(tmp_path), "S-001", "quality-review")
    assert res["ok"] is False and "not the current stage" in res["error"]


def test_an_unreadable_run_is_scoped_to_its_story(tmp_path):
    """The gate resolves records from the slug, never from a project-wide glob."""
    (tmp_path / "docs" / "development" / "stories").mkdir(parents=True)
    (tmp_path / "docs" / "experiments").mkdir(parents=True)
    # A DIFFERENT story has a perfect-looking record; S-042 has none.
    (tmp_path / "docs" / "development" / "stories" / "S-013.md").write_text(
        "---\nstory_key: S-013\nexperiment_refs:\n  - id: E-108\n---\n",
        encoding="utf-8")
    proc = _run_gate(tmp_path, "experiment", "S-042")
    assert proc.returncode == 2
    assert "S-042" in proc.stdout

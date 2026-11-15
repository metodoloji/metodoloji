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


def _run_gate(root: Path, stage: str, slug: str,
              env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE), "--stage", stage, "--slug", slug,
         "--project-root", str(root)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, stdin=subprocess.DEVNULL, env=env)


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


def _write_minimal_chain(root: Path) -> dict:
    """A conforming project whose E record is minted by the real gate.

    The record is not hand-written: `run_experiment.py --run` signs it with a
    throw-away key (`BMAD_GATE_KEY`) and `chain-gate.py --verify` checks that
    signature under the same key. A hand-written "Decision: APPROVED" would be
    refused as FORGED, which is the point — the fixture proves the chain can
    close on evidence, not on a phrase.
    """
    import json as _json
    import os

    for rel in ("docs/experiments", "docs/development/stories", "docs/quality",
                "scripts/bench"):
        (root / rel).mkdir(parents=True, exist_ok=True)
    (root / "scripts/bench/probe.py").write_text(
        "import json, pathlib\n"
        "raw = pathlib.Path('docs/experiments/E-001/raw')\n"
        "raw.mkdir(parents=True, exist_ok=True)\n"
        "with (raw / 'samples.jsonl').open('w') as fh:\n"
        "    for i in range(20):\n"
        "        fh.write(json.dumps({'i': i, 'ok': True}) + '\\n')\n"
        "print('probe_rate=1.00 (20/20)')\n", encoding="utf-8")
    (root / "scripts/bench/probe_broken.py").write_text(
        "import json, pathlib\n"
        # The control arm must NOT write the measurement's evidence: the record
        # seals that file's verdicts, so a control that clobbers it makes the
        # seal describe the wrong run (E-170 caught exactly this in this fixture).
        "raw = pathlib.Path('docs/experiments/E-001/raw')\n"
        "raw.mkdir(parents=True, exist_ok=True)\n"
        "with (raw / 'control.jsonl').open('w') as fh:\n"
        "    for i in range(20):\n"
        "        fh.write(json.dumps({'i': i, 'ok': False}) + '\\n')\n"
        "print('probe_rate=0.00 (0/20)')\n", encoding="utf-8")
    (root / "docs/experiments/E-001.md").write_text(
        "## Experiment: E-001 — fixture\n\n"
        "- **Date:** 2026-01-01\n- **Status:** planned\n"
        "- **Record Kind:** contract\n"
        "- **Theory:** a conforming chain must be able to close.\n"
        "- **Hypothesis:** H-001: \"probe_rate == 1.0\"\n"
        "- **Measurement Metrics:** probe_rate == 1.0\n"
        "- **Experiment Design:** the fixture bench writes 20 samples.\n"
        "- **Census:** The closed input set is the twenty samples the bench "
        "writes; every case is enumerated, so no sampling inference applies.\n"
        "- **Sample Size n:** 20\n"
        "- **Code Scope:** scripts/bench/probe.py\n"
        "- **Control Command:** python3 scripts/bench/probe_broken.py\n"
        "- **Lineage:** none\n"
        "- **Raw Samples:** docs/experiments/E-001/raw/samples.jsonl\n",
        encoding="utf-8")

    env = dict(os.environ, BMAD_GATE_KEY="00" * 32)
    gate = _ROOT / "skills" / "bmad-research-experiment" / "scripts" / "run_experiment.py"
    proc = subprocess.run(
        [sys.executable, str(gate), "--record",
         str(root / "docs/experiments/E-001.md"),
         "--run", f"{sys.executable} scripts/bench/probe.py",
         "--control", f"{sys.executable} scripts/bench/probe_broken.py",
         "--project-root", str(root), "--no-blackboard"],
        cwd=str(root), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=300, stdin=subprocess.DEVNULL, env=env)
    assert "DECISION: APPROVED" in proc.stdout, proc.stdout + proc.stderr

    (root / "docs/development/IR-001.md").write_text(
        "- **Status:** READY\n- **Decision:** READY\n- **Date:** 2026-01-01\n"
        "- **Research inputs:** E-001\n"
        "- **Success criteria:**\n  - probe — Metric: x; Source: y; Target: 1\n"
        "- **Technical dependencies:**\n  - Libraries: none\n"
        "- **Risk assessment:**\n  - Risk 1: none -> Mitigation: n/a\n",
        encoding="utf-8")
    (root / "docs/development/SP-001.md").write_text(
        "- **Status:** planned\n- **Date:** 2026-01-01\n"
        "- **Readiness input:** IR-001\n- **Sprint goal:** probe\n"
        "- **Stories:**\n  - S-001: probe\n- **Technical debt:**\n  - none\n",
        encoding="utf-8")
    (root / "docs/development/stories/S-001.md").write_text(
        "---\nstory_key: S-001\nexperiment_refs:\n  - id: E-001\n---\n"
        "# Story: S-001 — probe\n\n"
        "- **Status:** review\n- **Sprint:** SP-001\n- **Story key:** S-001\n\n"
        "| Field | Value |\n|-------|-------|\n| Story | S-001 |\n"
        "| Native Story | none — probe |\n| Experiment | E-001 |\n",
        encoding="utf-8")
    (root / "docs/quality/QR-001.md").write_text(
        "| Field | Value |\n|-------|-------|\n| Story | S-001 |\n"
        "| Status | APPROVED |\n\n- **Story:** S-001\n- **Status:** APPROVED\n",
        encoding="utf-8")
    return env


@pytest.mark.parametrize("stage", ["experiment", "implementation-readiness",
                                  "sprint-planning", "story", "quality-review"])
def test_a_conforming_story_clears_its_stages(tmp_path, stage):
    """The refusal probes prove severity; this proves the chain can CLOSE.

    Six probes assert that bad records are refused. None of them would notice a
    gate that refuses everything — a kernel stuck at stage 1 looks exactly like
    a strict one. This fixture is the counterweight: a minimal project whose
    records each owner accepts must clear every stage the story needs.
    """
    env = _write_minimal_chain(tmp_path)
    proc = _run_gate(tmp_path, stage, "S-001", env)
    assert proc.returncode == 0, (
        f"stage '{stage}' refused a conforming record: {proc.stdout}")


def test_a_conforming_chain_completes_end_to_end(tmp_path, monkeypatch):
    """The kernel advances through every stage and reaches `completed`."""
    _write_minimal_chain(tmp_path)
    # The kernel spawns the dispatcher itself, so the throw-away key must be in
    # this process's environment for the child to see it.
    monkeypatch.setenv("BMAD_GATE_KEY", "00" * 32)
    data = spec_mod.load(CHAIN)
    engine.create(str(tmp_path), data, slug="S-001", flags={"tier": "3"})
    for stage in ("experiment", "implementation-readiness", "sprint-planning",
                  "story", "quality-review"):
        res = engine.complete(str(tmp_path), "S-001", stage)
        assert res["ok"], f"{stage} refused: {res.get('error')}"
    state = engine.status(str(tmp_path), "S-001")
    assert state["status"] == "completed"
    assert state["current"] is None

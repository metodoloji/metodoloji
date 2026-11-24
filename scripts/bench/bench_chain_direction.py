#!/usr/bin/env python3
"""A conforming chain must be able to CLOSE, not only to refuse.

    python3 scripts/bench/bench_chain_direction.py --samples docs/experiments/E-237/raw/samples.jsonl
    python3 scripts/bench/bench_chain_direction.py --broken   # rule-7 control

E-150's Known Gap 5 recorded this shape and left it open: every chain probe
asked whether a bad input is refused, so a kernel or gate that refused
everything would have scored 14/14. The direction — a Tier 3 story whose
records are real closes E → IR → SP → S → QR → done, and through
production-readiness when the deploy branch is declared before quality-review —
was pinned only by unit tests of the spec, never by a bench driving the shipped
gates.

This bench builds a throwaway project whose story is decided by a named test
(`Test: tests/test_fixture.py::test_fixture`), so the experiment gate needs no
gate key, and then drives the real `workflow.py` with the real
`chain-gate.py` evidence: no stubbed verdicts. Each branch is measured by the
stage order the run records and by the state it ends in.

`--broken` omits the records the gates read (IR/SP/QR/PR and the story's
test-decided basis), which is what "a gate that refuses everything" looks
like: the closure probes must fall below the claim while the spec-shape probes
still pass. The control fails for the reason the gap names, not for a crash.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / "hooks" / "scripts" / "workflow.py"
BROKEN = False
_THRESHOLD = "chain_direction_accuracy == 1.0"

STORY = """# Story: S-001 — Chain direction fixture

- **Date:** 2026-01-01
- **Status:** done
- **Rigor Tier:** 3
- **Sprint:** SP-001
- **Priority:** High
- **Story points:** 1
- **Assignee:** @fixture
- **Epic:** standalone
- **Story key:** S-001

---

## User Story

**As a** fixture project
**I want** the kernel to drive its records
**So that** the chain's forward direction is measured

---

## Acceptance Criteria

- [AC-001] **Given** a conforming fixture **When** the chain runs **Then** it closes
  - Test: tests/test_fixture.py::test_fixture
  - Type: agent-verifiable
  - Measured: true
  - Verify: python -m pytest tests/test_fixture.py -q
"""

PEER = """# Methodology Record: S-001

| Field | Value |
|-------|-------|
| Native Story | S-001 |
| Experiment | E-001 |

The peer record a `review`/`done` story is required to have. It declares the
story it covers and carries no status of its own, so it is not itself gated.
"""

IR = """# Implementation Readiness: IR-001 — Chain direction fixture

- **Date:** 2026-01-01
- **Status:** READY
- **Research inputs:** E-001 (fixture basis)
- **Success criteria:** the fixture closes — Metric: stage order; Source: workflow.py; Target: done
- **Decision:** READY → the fixture is planned
- **Next step:** proceed to sprint planning
- **Record Schema:** metodoloji.record/v1
"""

SP = """# Sprint: SP-001 — Chain direction fixture

- **Date:** 2026-01-01
- **Status:** active
- **Readiness input:** IR-001 (`docs/development/IR-001.md`)
- **Sprint goal:** Prove that a conforming chain closes
- **Stories:** S-001 — Chain direction fixture — Priority: High — 1 story point
- **Record Schema:** metodoloji.record/v1
"""

QR = """# Quality Review: QR-001 — S-001

- **Date:** 2026-01-01
- **Status:** APPROVED
- **Story:** S-001
- **PR/MR:** none — kernel fixture
- **Decision:** APPROVED — the fixture records are coherent
- **Next step:** plan deploy
"""

PR = """# Production Readiness: PR-001 — Chain direction fixture

- **Date:** 2026-01-01
- **Status:** ready
- **Release type:** minor
- **Version:** 0.0.1
- **Stories:** S-001
- **Record Schema:** metodoloji.record/v1
"""

TEST_FILE = """def test_fixture():
    assert True
"""


def _project(full: bool = True) -> pathlib.Path:
    """A throwaway project with the records the shipped gates read.

    `full=False` omits them: the closure probes must then fail because the
    gates refuse, which is the control arm's whole point.
    """
    root = pathlib.Path(tempfile.mkdtemp(prefix="e237-chain-"))
    dev = root / "docs" / "development"
    (dev / "stories").mkdir(parents=True)
    (root / "docs" / "quality").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "tests" / "test_fixture.py").write_text(TEST_FILE, encoding="utf-8")
    (dev / "stories" / "S-900.md").write_text(PEER, encoding="utf-8")
    (dev / "stories" / "S-001.md").write_text(
        STORY if full else "# Story: S-001 — no basis\n\n- **Status:** done\n",
        encoding="utf-8")
    if full:
        (dev / "IR-001.md").write_text(IR, encoding="utf-8")
        (dev / "SP-001.md").write_text(SP, encoding="utf-8")
        (root / "docs" / "quality" / "QR-001.md").write_text(QR, encoding="utf-8")
        (dev / "PR-001.md").write_text(PR, encoding="utf-8")
    return root


def _wf(root: pathlib.Path, *argv: str) -> dict:
    proc = subprocess.run(
        [sys.executable, str(WORKFLOW), *argv, "--project-root", str(root)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300, stdin=subprocess.DEVNULL)
    try:
        return json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return {"ok": False, "error": (proc.stdout or proc.stderr)[-400:]}


def _spec() -> dict:
    return json.loads((ROOT / "hooks" / "workflow" / "builtin"
                       / "methodology-chain.json").read_text(encoding="utf-8"))


def _drive(tier: str, deploy: bool) -> dict:
    """Create a run and advance it until the kernel stops it."""
    root = _project(full=not BROKEN)
    try:
        got = _wf(root, "create", "--builtin", "methodology-chain",
                  "--slug", "S-001", "--flag", f"tier={tier}")
        if not got.get("ok"):
            return {"error": got.get("error", "create failed")}
        completed: list[str] = []
        state = got["state"]
        guard = 0
        while state.get("current") and not state.get("terminal") and guard < 12:
            guard += 1
            stage = state["current"]
            if stage == "quality-review" and deploy:
                _wf(root, "flag", "--slug", "S-001", "--key", "deploy",
                    "--value", "true")
            got = _wf(root, "complete", "--slug", "S-001", "--stage", stage)
            if not got.get("ok"):
                return {"error": got.get("error", "complete failed"),
                        "completed": completed, "current": stage,
                        "status": state.get("status")}
            completed = got.get("completed", [])
            state = {"current": got.get("current"),
                     "status": got.get("status")}
        return {"completed": completed, "current": state.get("current"),
                "status": state.get("status"), "error": ""}
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _reaches(start: str, target: str) -> bool:
    stages = {s["id"]: s for s in _spec()["stages"]}
    seen: set[str] = set()
    stack = [start]
    while stack:
        sid = stack.pop()
        if sid == target:
            return True
        if sid in seen or sid not in stages:
            continue
        seen.add(sid)
        stack.extend(edge["to"] for edge in stages[sid].get("next", []))
    return False


# --- probes -------------------------------------------------------------------

def _case_tier3_closes_to_done():
    got = _drive("3", deploy=False)
    want = ["experiment", "implementation-readiness", "sprint-planning",
            "story", "quality-review", "done"]
    ok = got.get("status") == "completed" and got.get("completed") == want
    return ok, f"tier 3 without deploy → {got}"


def _case_deploy_branch_reaches_pr():
    got = _drive("3", deploy=True)
    want = ["experiment", "implementation-readiness", "sprint-planning",
            "story", "quality-review", "production-readiness", "done"]
    ok = got.get("status") == "completed" and got.get("completed") == want
    return ok, f"tier 3 with deploy before QR → {got}"


def _case_tier1_skips_planning():
    got = _drive("1", deploy=False)
    want = ["experiment", "story", "quality-review", "done"]
    ok = got.get("status") == "completed" and got.get("completed") == want
    return ok, f"tier 1 → {got}"


def _case_tier2_skips_planning():
    got = _drive("2", deploy=False)
    want = ["experiment", "story", "quality-review", "done"]
    ok = got.get("status") == "completed" and got.get("completed") == want
    return ok, f"tier 2 → {got}"


def _case_refused_gate_leaves_the_run_open():
    """A missing PR record must stop the deploy branch, not be walked past."""
    root = _project(full=not BROKEN)
    try:
        _wf(root, "create", "--builtin", "methodology-chain", "--slug", "S-001",
            "--flag", "tier=3")
        for stage in ("experiment", "implementation-readiness",
                      "sprint-planning", "story"):
            got = _wf(root, "complete", "--slug", "S-001", "--stage", stage)
            if not got.get("ok"):
                return False, f"{stage} refused on a conforming fixture: {got}"
        _wf(root, "flag", "--slug", "S-001", "--key", "deploy", "--value", "true")
        _wf(root, "complete", "--slug", "S-001", "--stage", "quality-review")
        (root / "docs" / "development" / "PR-001.md").unlink()
        got = _wf(root, "complete", "--slug", "S-001", "--stage",
                  "production-readiness")
        status = _wf(root, "status", "--slug", "S-001")
        ok = (not got.get("ok")
              and status.get("status") == "active"
              and status.get("current") == "production-readiness")
        return ok, f"refusal={got.get('error', '')[:120]!r} status={status.get('status')!r}"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _case_completed_run_refuses_a_late_deploy_flag():
    got = _drive("3", deploy=False)
    if got.get("status") != "completed":
        return False, f"the run did not close first: {got}"
    root = _project(full=not BROKEN)
    try:
        _wf(root, "create", "--builtin", "methodology-chain", "--slug", "S-001",
            "--flag", "tier=3")
        for stage in ("experiment", "implementation-readiness",
                      "sprint-planning", "story", "quality-review"):
            _wf(root, "complete", "--slug", "S-001", "--stage", stage)
        late = _wf(root, "flag", "--slug", "S-001", "--key", "deploy",
                   "--value", "true")
        ok = not late.get("ok") and "deploy" in late.get("error", "")
        return ok, f"late flag refused={not late.get('ok')}: {late.get('error', '')[:120]!r}"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _case_every_stage_reaches_the_terminal():
    spec = _spec()
    dead = [s["id"] for s in spec["stages"]
            if not s.get("terminal") and not _reaches(s["id"], "done")]
    return not dead, f"dead ends: {dead or 'none'}"


def _case_no_declared_stage_is_unreachable():
    spec = _spec()
    start = spec["start"]
    unreachable = [s["id"] for s in spec["stages"]
                   if s["id"] != start and not _reaches(start, s["id"])]
    return not unreachable, f"unreachable stages: {unreachable or 'none'}"


def _case_every_stage_evidence_names_its_own_gate():
    wrong = []
    for stage in _spec()["stages"]:
        if stage.get("terminal"):
            continue
        argv = stage.get("evidence", {}).get("argv", [])
        text = " ".join(argv)
        if "chain-gate.py" not in text or f"--stage {stage['id']}" not in text:
            wrong.append(stage["id"])
    return not wrong, f"stages without their own gate: {wrong or 'none'}"


PROBES = (
    ("tier3_closes_to_done_without_deploy", _case_tier3_closes_to_done),
    ("deploy_branch_reaches_production_readiness", _case_deploy_branch_reaches_pr),
    ("tier1_skips_planning_records", _case_tier1_skips_planning),
    ("tier2_skips_planning_records", _case_tier2_skips_planning),
    ("refused_gate_leaves_the_run_open", _case_refused_gate_leaves_the_run_open),
    ("completed_run_refuses_a_late_deploy_flag", _case_completed_run_refuses_a_late_deploy_flag),
    ("every_stage_reaches_the_terminal", _case_every_stage_reaches_the_terminal),
    ("no_declared_stage_is_unreachable", _case_no_declared_stage_is_unreachable),
    ("every_stage_evidence_names_its_own_gate", _case_every_stage_evidence_names_its_own_gate),
)


def run() -> list[dict]:
    results = []
    for name, probe in PROBES:
        try:
            ok, detail = probe()
        except Exception as exc:  # a crashed probe is a failed probe
            ok, detail = False, f"crashed: {exc}"
        results.append({"probe": name, "ok": bool(ok), "detail": str(detail)})
    return results


def main(argv=None) -> int:
    global BROKEN
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--broken", action="store_true", help="rule-7 control arm")
    ap.add_argument("--samples", help="write one JSON object per probe to this path")
    args = ap.parse_args(argv)
    BROKEN = args.broken

    results = run()
    if args.samples:
        path = pathlib.Path(args.samples)
        if not path.is_absolute():
            path = ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for r in results:
                fh.write(json.dumps({"probe": r["probe"], "ok": r["ok"]}) + "\n")

    passed = sum(1 for r in results if r["ok"])
    total = len(results)
    for r in results:
        print(f"[{'ok' if r['ok'] else 'FAIL'}] {r['probe']}: {r['detail']}")
    print(f"chain_direction_accuracy={passed / total:.2f} ({passed}/{total})")
    # The number is the result; exit status is not the verdict (the gate reads
    # the printed value, and the control must report a low score, not a crash).
    return 0


if __name__ == "__main__":
    sys.exit(main())

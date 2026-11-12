"""The optimization lane and the BMad/methodology lane cannot consume each
other's evidence.

The two lanes share a discipline (a claim needs a measurement, a control and a
record) but produce DIFFERENT artifacts:

  * optimization/gate.py  -> a GateDecision about an ARTIFACT (best_skill.md /
    ADAPTER.md). It writes `steps.jsonl`; it never writes an approval token and
    never names a code scope.
  * run_experiment.py     -> an approval token about CODE. It never reads an
    optimization run and never certifies an artifact improvement.

These tests pin the boundary mechanically instead of leaving it to prose.
"""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from optimization.gate import decide  # noqa: E402


def _gate_module():
    path = ROOT / "skills" / "bmad-research-experiment" / "scripts" / "run_experiment.py"
    spec = importlib.util.spec_from_file_location("run_experiment_lane", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_optimizer_decision_is_not_a_code_approval():
    base = {f"c{i}": False for i in range(6)}
    cand = {f"c{i}": True for i in range(6)}
    decision = decide(base, cand, min_cases=6)
    assert not hasattr(decision, "token")
    assert not hasattr(decision, "code_scope")


def test_optimizer_module_never_mints_an_approval_token():
    source = (ROOT / "optimization" / "gate.py").read_text(encoding="utf-8")
    assert "GATE-OK" not in source
    assert "HMAC" not in source


def test_experiment_gate_never_reads_the_optimization_run():
    gate = _gate_module()
    source = Path(gate.__file__).read_text(encoding="utf-8")
    # No import of the optimization kernel: the code gate cannot be satisfied
    # by an artifact run's steps.jsonl.
    assert "optimization.gate" not in source
    assert "from optimization" not in source
    assert "steps.jsonl" not in source

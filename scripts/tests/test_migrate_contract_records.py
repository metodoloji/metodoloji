"""Legacy record classification: contract vs experiment.

The migration must be conservative (no silent reclassification) and must never
touch gate-written lines, so every existing token keeps verifying.
"""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "migrate-contract-records.py"

_spec = importlib.util.spec_from_file_location("migrate_contract", SCRIPT)
migrate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate)


def _record(hypothesis: str, *, census: str = "", command: str = "",
            kind: str = "") -> str:
    lines = ["## Experiment: E-900 — fixture", "- **Status:** completed"]
    if kind:
        lines.append(f"- **Record Kind:** {kind}")
    lines += ["- **Theory:** t", f'- **Hypothesis:** {hypothesis}']
    if census:
        lines.append(f"- **Census:** {census}")
    if command:
        lines.append(f"- **Measurement Command:** {command}")
    return "\n".join(lines) + "\n"


_CENSUS = ("the twelve enumerated cases in the shipped regression suite are "
           "the complete closed population")


def test_full_claim_over_census_is_a_contract():
    kind, reason = migrate.classify(
        _record('H-1: "x == 1.0"', census=_CENSUS))
    assert kind == "contract"


def test_test_suite_command_is_a_contract():
    kind, _ = migrate.classify(
        _record('H-1: "x >= 0.90"', command="python -m pytest -q tests/"))
    assert kind == "contract"


def test_stochastic_claim_stays_an_experiment():
    kind, _ = migrate.classify(
        _record('H-1: "accuracy >= 0.90"', command="python bench.py"))
    assert kind == "experiment"


def test_declared_kind_is_never_overwritten():
    kind, reason = migrate.classify(_record('H-1: "x == 1.0"', kind="experiment"))
    assert kind == "experiment"
    assert reason == "already declared"


def test_insert_kind_keeps_gate_lines_and_is_idempotent():
    text = _record('H-1: "x >= 0.90"', command="python bench.py")
    once = migrate._insert_kind(text, "experiment")
    assert "- **Record Kind:** experiment" in once
    assert "- **Status:** completed" in once
    twice = migrate._insert_kind(once, "experiment")
    assert twice == once


def test_apply_writes_only_contracts(tmp_path):
    good = tmp_path / "E-001.md"
    good.write_text(_record('H-1: "x == 1.0"', census=_CENSUS), encoding="utf-8")
    plain = tmp_path / "E-002.md"
    plain.write_text(_record('H-1: "accuracy >= 0.9"'), encoding="utf-8")

    import sys
    sys.argv = ["migrate-contract-records.py", "--records-dir", str(tmp_path),
                "--apply"]
    assert migrate.main() == 0
    assert "Record Kind:** contract" in good.read_text(encoding="utf-8")
    assert "Record Kind" not in plain.read_text(encoding="utf-8")

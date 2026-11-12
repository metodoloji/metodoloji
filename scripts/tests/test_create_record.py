"""Schema-validated record CLI: generation refuses an incomplete draft.

Loaded in-process (not via subprocess): the Windows test host cannot always
inherit the harness's stdio handles, and the CLI's contract is its functions and
exit code, not a shell process.
"""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "create-record.py"

_spec = importlib.util.spec_from_file_location("create_record", SCRIPT)
create_record = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(create_record)


def _run(monkeypatch, *args):
    monkeypatch.setattr("sys.argv", ["create-record.py", *args])
    return create_record.main()


def test_experiment_draft_is_created_and_validates(tmp_path, monkeypatch, capsys):
    out = tmp_path / "proj"
    out.mkdir()
    rc = _run(
        monkeypatch, "--project-root", str(out), "--kind", "experiment",
        "--id", "E-001", "--title", "demo", "--theory", "because",
        "--hypothesis", 'H-001: "rate >= 0.90"', "--metrics", "rate >= 0.90",
        "--design", "run the bench", "--scope", "src/**", "--lineage", "none",
        "--raw-samples", "docs/experiments/E-001/raw/samples.jsonl")
    assert rc == 0, capsys.readouterr().err
    record = out / "docs" / "experiments" / "E-001.md"
    assert record.is_file()
    assert "metodoloji.record/v1" in record.read_text(encoding="utf-8")
    assert _run(monkeypatch, "--check", str(record)) == 0


def test_contract_draft_without_census_is_refused(tmp_path, monkeypatch, capsys):
    rc = _run(
        monkeypatch, "--project-root", str(tmp_path), "--kind", "contract",
        "--id", "E-002", "--title", "contract", "--theory", "closed set",
        "--hypothesis", 'H-002: "rate == 1.0"', "--metrics", "rate == 1.0",
        "--design", "run every case", "--scope", "src/**", "--lineage", "none",
        "--raw-samples", "raw/samples.jsonl")
    assert rc == 1
    assert "Census" in capsys.readouterr().err


def test_contract_draft_with_all_fields_is_accepted(tmp_path, monkeypatch, capsys):
    rc = _run(
        monkeypatch, "--project-root", str(tmp_path), "--kind", "contract",
        "--id", "E-003", "--title", "contract", "--theory", "closed set",
        "--hypothesis", 'H-003: "rate == 1.0"', "--metrics", "rate == 1.0",
        "--design", "run every case", "--scope", "src/**", "--lineage", "none",
        "--raw-samples", "raw/samples.jsonl",
        "--census", "the five enumerated cases in the shipped suite are the complete set")
    assert rc == 0, capsys.readouterr().err
    assert _run(monkeypatch, "--check",
                str(tmp_path / "docs" / "experiments" / "E-003.md")) == 0


def test_ir_record_requires_its_own_schema_fields(tmp_path, monkeypatch, capsys):
    rc = _run(
        monkeypatch, "--project-root", str(tmp_path), "--kind", "ir",
        "--id", "IR-001", "--title", "readiness",
        "--field", "Research inputs=E-001",
        "--field", "Success criteria=the gate is green",
        "--field", "Decision=READY",
        "--field", "Next step=sprint planning")
    assert rc == 0, capsys.readouterr().err
    assert _run(monkeypatch, "--check",
                str(tmp_path / "docs" / "development" / "IR-001.md")) == 0


def test_check_skips_a_legacy_record_without_the_marker(tmp_path, monkeypatch, capsys):
    legacy = tmp_path / "E-999.md"
    legacy.write_text("## Experiment: E-999 — legacy\n- **Status:** completed\n",
                      encoding="utf-8")
    assert _run(monkeypatch, "--check", str(legacy)) == 0
    assert "SKIP" in capsys.readouterr().out

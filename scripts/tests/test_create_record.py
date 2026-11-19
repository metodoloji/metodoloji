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


def test_auto_id_skips_a_claim_in_a_legacy_tree(tmp_path, monkeypatch, capsys):
    """Numbering reads the gate's whole id space, not just docs/experiments.

    A pre-migration `bmad-output/docs/experiments/E-001.md` made the gate
    refuse the auto id E-001, and the generator proposed it again on every
    run; the caller had to discover `--id` to get past it.
    """
    out = tmp_path / "proj"
    legacy = out / "bmad-output" / "docs" / "experiments" / "E-001.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("# Experiment: E-001\n", encoding="utf-8")
    rc = _run(
        monkeypatch, "--project-root", str(out), "--kind", "experiment",
        "--title", "demo", "--theory", "because",
        "--hypothesis", 'H-001: "rate >= 0.90"', "--metrics", "rate >= 0.90",
        "--design", "run the bench", "--scope", "src/**", "--lineage", "none",
        "--raw-samples", "docs/experiments/E-002/raw/samples.jsonl")
    assert rc == 0, capsys.readouterr().err
    assert (out / "docs" / "experiments" / "E-002.md").is_file()
    assert legacy.read_text(encoding="utf-8") == "# Experiment: E-001\n"


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


def test_explicit_field_overrides_the_seeded_default(tmp_path, monkeypatch, capsys):
    """One label, one value: --field wins, it is not appended after the seed."""
    rc = _run(
        monkeypatch, "--project-root", str(tmp_path), "--kind", "contract",
        "--id", "E-004", "--title", "contract", "--theory", "closed set",
        "--hypothesis", 'H-004: "rate == 1.0"', "--metrics", "rate == 1.0",
        "--design", "run every case", "--scope", "src/**",
        "--raw-samples", "raw/samples.jsonl",
        "--census", "the five enumerated cases in the shipped suite are the complete set",
        "--field", "Status=completed", "--field", "Lineage=E-001",
        "--field", "Date=2020-01-02")
    assert rc == 0, capsys.readouterr().err
    text = (tmp_path / "docs" / "experiments" / "E-004.md").read_text(encoding="utf-8")
    for label, value in (("Status", "completed"), ("Lineage", "E-001"),
                         ("Date", "2020-01-02")):
        assert text.count(f"- **{label}:**") == 1, label
        assert f"- **{label}:** {value}" in text, label


def test_a_second_explicit_value_is_refused_not_dropped(tmp_path, monkeypatch, capsys):
    """A conflict is an error: silently keeping either value is the ambiguity."""
    rc = _run(
        monkeypatch, "--project-root", str(tmp_path), "--kind", "experiment",
        "--id", "E-005", "--title", "dup", "--theory", "t",
        "--hypothesis", 'H-005: "x >= 0.90"', "--metrics", "x >= 0.90",
        "--design", "d", "--scope", "s", "--raw-samples", "raw/s.jsonl",
        "--date", "2020-01-02", "--field", "Date=2020-01-03")
    assert rc == 2
    assert "Date" in capsys.readouterr().err
    assert not (tmp_path / "docs" / "experiments" / "E-005.md").exists()


def test_validate_refuses_a_duplicate_schema_field(tmp_path, monkeypatch):
    text = (
        "## Experiment: E-950 — dup\n\n"
        "- **Date:** 2026-11-16\n- **Status:** planned\n- **Status:** completed\n"
        "- **Theory:** t\n- **Hypothesis:** H-950: \"x == 1.0\"\n"
        "- **Measurement Metrics:** x == 1.0\n- **Experiment Design:** d\n"
        "- **Code Scope:** s\n- **Lineage:** none\n- **Raw Samples:** raw/s.jsonl\n"
        "- **Record Schema:** metodoloji.record/v1\n")
    issues = create_record.validate_text("experiment", text)
    assert any("duplicate field 'Status'" in issue for issue in issues), issues


def test_validate_keeps_append_only_history_labels(tmp_path, monkeypatch):
    """E-128/E-158/E-159 repeat these on purpose; the rule must not police them."""
    text = (
        "## Experiment: E-951 — history\n\n"
        "- **Date:** 2026-11-16\n- **Status:** completed\n"
        "- **Theory:** t\n- **Hypothesis:** H-951: \"x == 1.0\"\n"
        "- **Measurement Metrics:** x == 1.0\n- **Experiment Design:** d\n"
        "- **Code Scope:** s\n- **Lineage:** none\n- **Raw Samples:** raw/s.jsonl\n"
        "- **Re-Measured-By:** E-952\n- **Re-Measured-By:** E-953\n"
        "- **Final Plan Hash:** aaaa\n- **Final Plan Hash:** bbbb\n"
        "- **Record Schema:** metodoloji.record/v1\n")
    assert create_record.validate_text("experiment", text) == []


def test_a_second_file_for_a_claimed_record_id_is_refused(tmp_path, monkeypatch,
                                                        capsys):
    """The generator writes the file itself, so the guard never sees the target:
    it must refuse the same fork the write gate refuses — a slug-named sibling
    and a differently padded spelling are the same record."""
    exp = tmp_path / "docs" / "experiments"
    exp.mkdir(parents=True)
    (exp / "E-001-gate.md").write_text("## Experiment: E-001\n", encoding="utf-8")
    rc = _run(
        monkeypatch, "--project-root", str(tmp_path), "--kind", "experiment",
        "--id", "E-001", "--title", "fork", "--theory", "t",
        "--hypothesis", 'H-001: "x >= 0.90"', "--metrics", "x >= 0.90",
        "--design", "d", "--scope", "s", "--raw-samples", "raw/s.jsonl",
        "--force")
    assert rc == 2
    err = capsys.readouterr().err
    assert "Duplicate record ID 'E-001'" in err
    assert not (exp / "E-001.md").exists()

    (exp / "E-002.md").write_text("## Experiment: E-002\n", encoding="utf-8")
    rc = _run(
        monkeypatch, "--project-root", str(tmp_path), "--kind", "experiment",
        "--id", "E-2", "--title", "padding", "--theory", "t",
        "--hypothesis", 'H-002: "x >= 0.90"', "--metrics", "x >= 0.90",
        "--design", "d", "--scope", "s", "--raw-samples", "raw/s.jsonl")
    assert rc == 2
    assert "Duplicate record ID 'E-2'" in capsys.readouterr().err
    assert not (exp / "E-2.md").exists()


def test_check_skips_a_legacy_record_without_the_marker(tmp_path, monkeypatch, capsys):
    legacy = tmp_path / "E-999.md"
    legacy.write_text("## Experiment: E-999 — legacy\n- **Status:** completed\n",
                      encoding="utf-8")
    assert _run(monkeypatch, "--check", str(legacy)) == 0
    assert "SKIP" in capsys.readouterr().out

"""Integrity of the measurement gate: a number must be attributable and derivable.

The gate used to read a printed aggregate and believe it, which made a `print`
statement indistinguishable from a measurement — a live session minted a real
token from `node -e "console.log('rate=1.00 (200/200)')"`. These tests pin the
two rules that close that:

  1. the command must execute a script FILE (no inline eval, no shell chain);
  2. the aggregate must be recomputable from per-sample raw data the run itself
     wrote, or the record must decline the samples in public.

They run the real gate as a subprocess against a real temporary project, so the
gate's own exit codes and record mutations are what is asserted — not a mock.
"""

import json
import os
import pathlib
import subprocess
import sys
import time

import pytest

PLUGIN = pathlib.Path(__file__).resolve().parents[2]
GATE = PLUGIN / "skills/bmad-research-experiment/scripts/run_experiment.py"

RECORD = """## Experiment: E-900 — integrity fixture

- **Date:** 21.10.2026
- **Status:** planned
- **Theory:** A fixture for the gate's integrity rules.
- **Hypothesis:** H-900: "demo_rate >= 0.95"
- **Measurement Metrics:** demo_rate >= 0.95
- **Experiment Design:** a bench prints an aggregate over 200 samples.
- **Sample Size n:** 200
- **Code Scope:** scripts/bench/bench.py
- **Lineage:** none
%s- **Status:** planned
"""

HONEST = '''
import json, pathlib
raw = pathlib.Path("docs/experiments/E-900/raw"); raw.mkdir(parents=True, exist_ok=True)
with (raw / "samples.jsonl").open("w") as fh:
    for i in range(200):
        fh.write(json.dumps({"i": i, "ok": True}) + "\\n")
print("demo_rate=1.00 (200/200)")
'''

MISMATCH = '''
import json, pathlib
raw = pathlib.Path("docs/experiments/E-900/raw"); raw.mkdir(parents=True, exist_ok=True)
with (raw / "samples.jsonl").open("w") as fh:
    for i in range(200):
        fh.write(json.dumps({"i": i, "ok": i < 100}) + "\\n")
print("demo_rate=1.00 (200/200)")
'''

NO_SAMPLES_WRITTEN = '''
print("demo_rate=1.00 (200/200)")
'''

NO_OK_FIELD = '''
import json, pathlib
raw = pathlib.Path("docs/experiments/E-900/raw"); raw.mkdir(parents=True, exist_ok=True)
with (raw / "samples.jsonl").open("w") as fh:
    for i in range(200):
        fh.write(json.dumps({"i": i}) + "\\n")
print("demo_rate=1.00 (200/200)")
'''


def _project(tmp_path, bench, raw_line=""):
    (tmp_path / "docs/experiments").mkdir(parents=True)
    (tmp_path / "scripts/bench").mkdir(parents=True)
    (tmp_path / "scripts/bench/bench.py").write_text(bench, encoding="utf-8")
    body = RECORD % (raw_line + "\n" if raw_line else "")
    (tmp_path / "docs/experiments/E-900.md").write_text(body, encoding="utf-8")
    return tmp_path


def _run(root, extra=()):
    return subprocess.run(
        [sys.executable, str(GATE), "--record", str(root / "docs/experiments/E-900.md"),
         "--project-root", str(root), "--no-blackboard", *extra],
        cwd=str(root), capture_output=True, text=True, timeout=300,
        stdin=subprocess.DEVNULL)


RAW_OK = "- **Raw Samples:** docs/experiments/E-900/raw/samples.jsonl"


def test_inline_eval_is_refused_before_it_can_measure(tmp_path):
    """A shell string is not an artifact: the number would come from nothing."""
    _project(tmp_path, HONEST, RAW_OK)
    res = _run(tmp_path, ["--run", "node -e \"console.log('demo_rate=1.00 (200/200)')\""])
    assert res.returncode == 2, res.stdout + res.stderr
    assert "must execute a script FILE" in res.stderr
    assert "DECISION: APPROVED" not in res.stdout


def test_missing_raw_samples_field_is_refused(tmp_path):
    """No samples declared and none declined: the aggregate is a bare claim."""
    _project(tmp_path, HONEST)
    res = _run(tmp_path, ["--run", "python3 scripts/bench/bench.py"])
    assert res.returncode == 4, res.stdout + res.stderr
    assert "no '- **Raw Samples:**' line" in res.stderr or "Raw Samples" in res.stdout + res.stderr
    record = (tmp_path / "docs/experiments/E-900.md").read_text(encoding="utf-8")
    assert "REFUSED" in record
    assert "**Decision:**" not in record


def test_declining_samples_requires_a_real_reason(tmp_path):
    """An unexplained exemption is the abuse case."""
    _project(tmp_path, HONEST, "- **Raw Samples:** none — too slow")
    res = _run(tmp_path, ["--run", "python3 scripts/bench/bench.py"])
    assert res.returncode == 4, res.stdout + res.stderr
    assert "at least 40 characters" in res.stderr


def test_honest_samples_are_recomputed_and_hashed(tmp_path):
    """The passing case: the gate derives x/n from the file and records its hash."""
    _project(tmp_path, HONEST, RAW_OK)
    res = _run(tmp_path, ["--run", "python3 scripts/bench/bench.py"])
    assert res.returncode == 0, res.stdout + res.stderr
    assert "DECISION: APPROVED" in res.stdout
    record = (tmp_path / "docs/experiments/E-900.md").read_text(encoding="utf-8")
    assert "200 samples, 200 ok" in record
    assert "sha256=" in record


def test_printed_aggregate_must_match_the_samples(tmp_path):
    """Half the samples fail but the output claims 200/200 — the lie is caught."""
    _project(tmp_path, MISMATCH, RAW_OK)
    res = _run(tmp_path, ["--run", "python3 scripts/bench/bench.py"])
    assert res.returncode == 4, res.stdout + res.stderr
    assert "success count disagrees" in res.stderr


def test_run_that_writes_no_samples_file_is_refused(tmp_path):
    """The record points at a sample file the run never produced."""
    _project(tmp_path, NO_SAMPLES_WRITTEN, RAW_OK)
    res = _run(tmp_path, ["--run", "python3 scripts/bench/bench.py"])
    assert res.returncode == 4, res.stdout + res.stderr
    assert "raw samples file does not exist" in res.stderr


def test_stale_sample_file_cannot_evidence_a_fresh_run(tmp_path):
    """A committed sample file from yesterday must not back today's measurement."""
    _project(tmp_path, NO_SAMPLES_WRITTEN, RAW_OK)
    raw = tmp_path / "docs/experiments/E-900/raw/samples.jsonl"
    raw.parent.mkdir(parents=True)
    raw.write_text("".join(json.dumps({"ok": True}) + "\n" for _ in range(200)),
                   encoding="utf-8")
    old = time.time() - 3600
    os.utime(raw, (old, old))
    res = _run(tmp_path, ["--run", "python3 scripts/bench/bench.py"])
    assert res.returncode == 4, res.stdout + res.stderr
    assert "was not written by this run" in res.stderr


def test_sample_without_a_verdict_is_refused(tmp_path):
    """Every sample must state its own verdict; a bare index proves nothing."""
    _project(tmp_path, NO_OK_FIELD, RAW_OK)
    res = _run(tmp_path, ["--run", "python3 scripts/bench/bench.py"])
    assert res.returncode == 4, res.stdout + res.stderr
    assert "no boolean 'ok' field" in res.stderr


def test_reproduce_confirms_a_real_measurement(tmp_path):
    """--reproduce re-runs the command: the one check that tests the number
    against the world rather than against itself."""
    _project(tmp_path, HONEST, RAW_OK)
    first = _run(tmp_path, ["--run", "python3 scripts/bench/bench.py"])
    assert first.returncode == 0, first.stdout + first.stderr
    res = _run(tmp_path, ["--reproduce"])
    assert res.returncode == 0, res.stdout + res.stderr
    assert "REPRODUCED" in res.stdout


def test_zero_of_six_is_not_zero_percent():
    """A '<= 0' claim on 0 of 6 trials bounds the true rate below ~39%, not 0.

    The gate used to answer "no small-sample risk" for every '<=' claim — the
    Wilson logic was applied to the wrong direction, so a record could assert an
    escape rate of exactly zero on six trials and be told it carried no risk.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("gate_unc", GATE)
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)

    note = gate.uncertainty_note(0, 6, 0.0, 0.0, "<=")
    assert "small sample" in note, note
    assert "0.39" in note, note
    assert abs(gate.wilson_upper(0, 6) - 0.39) < 0.01

    # An exact-zero claim is never statistically established, however large the
    # sample: the upper bound only shrinks towards zero. A claim of "at most 5%"
    # is reachable; "exactly 0%" is not — unless the n cases are the WHOLE
    # population rather than a sample, in which case this is a census and the
    # Wilson framing does not apply. The record must say which it is; that
    # ambiguity is logged in docs/bmad/experiment-integrity.md rather than
    # silently resolved here.
    assert "small sample" in gate.uncertainty_note(0, 500, 0.0, 0.0, "<=")
    assert "none" in gate.uncertainty_note(0, 500, 0.0, 0.05, "<=")


CENSUS_RECORD = """## Experiment: E-905 — census fixture

- **Date:** 21.10.2026
- **Status:** planned
- **Theory:** A claim over a complete population rather than a sample.
- **Hypothesis:** H-905: "escape_rate <= 0.0"
- **Measurement Metrics:** escape_rate <= 0.0
- **Experiment Design:** all six storage escape paths are exercised.
- **Sample Size n:** 6
- **Code Scope:** scripts/bench/census.py
- **Lineage:** none
- **Raw Samples:** raw/samples.jsonl
%s- **Status:** planned
"""

CENSUS_BENCH = '''
import json, pathlib
raw = pathlib.Path("raw"); raw.mkdir(exist_ok=True)
with (raw / "samples.jsonl").open("w") as f:
    for i in range(6):
        f.write(json.dumps({"i": i, "ok": True}) + chr(10))
print("escape_rate=0.00 (6/6)")
'''

CENSUS_OK = ("- **Census:** the six storage write paths enumerated in "
             "storage_adapters.py are the complete set; there is no seventh")


def _census_project(tmp_path, extra):
    (tmp_path / "docs/experiments").mkdir(parents=True)
    (tmp_path / "scripts/bench").mkdir(parents=True)
    (tmp_path / "scripts/bench/census.py").write_text(CENSUS_BENCH, encoding="utf-8")
    (tmp_path / "docs/experiments/E-905.md").write_text(
        CENSUS_RECORD % (extra + "\n" if extra else ""), encoding="utf-8")
    return tmp_path


def test_census_turns_an_exact_zero_into_a_defensible_claim(tmp_path):
    """0 of 6 over the COMPLETE population is the rate, not an estimate of it."""
    root = _census_project(tmp_path, CENSUS_OK)
    res = subprocess.run(
        [sys.executable, str(GATE), "--record", str(root / "docs/experiments/E-905.md"),
         "--project-root", str(root), "--no-blackboard",
         "--run", "python3 scripts/bench/census.py"],
        cwd=str(root), capture_output=True, text=True, timeout=300,
        stdin=subprocess.DEVNULL)
    assert res.returncode == 0, res.stdout + res.stderr
    record = (root / "docs/experiments/E-905.md").read_text(encoding="utf-8")
    assert "census —" in record
    assert "no sampling inference applies" in record


def test_a_short_census_is_ignored_and_says_so(tmp_path):
    """An unexplained exemption is the abuse case, exactly as with raw samples."""
    root = _census_project(tmp_path, "- **Census:** all six paths")
    res = subprocess.run(
        [sys.executable, str(GATE), "--record", str(root / "docs/experiments/E-905.md"),
         "--project-root", str(root), "--no-blackboard",
         "--run", "python3 scripts/bench/census.py"],
        cwd=str(root), capture_output=True, text=True, timeout=300,
        stdin=subprocess.DEVNULL)
    assert res.returncode == 0, res.stdout + res.stderr
    record = (root / "docs/experiments/E-905.md").read_text(encoding="utf-8")
    assert "[Census ignored:" in record


def test_corpus_report_surfaces_the_distribution():
    """The single-record gate cannot see its own corpus; this does."""
    res = subprocess.run(
        [sys.executable, str(GATE), "--corpus-report", str(PLUGIN / "docs/experiments")],
        capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "records" in res.stdout
    assert "exact-zero claims" in res.stdout
    assert "comparison claims" in res.stdout

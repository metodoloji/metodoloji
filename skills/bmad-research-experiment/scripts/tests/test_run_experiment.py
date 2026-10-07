"""Tests for skills/bmad-research-experiment/scripts/run_experiment.py.

Covers the pure helpers the module's --selfcheck does NOT exercise: record
parsing, hypothesis/claim extraction, uncertainty notes, metric naming, and
the advisory-block / legacy-token verify paths.
"""

import os
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

import run_experiment as gate  # noqa: E402


# --- record_fields ----------------------------------------------------------

def test_record_fields_parses_bold_lines():
    text = "- **Status:** planned\n- **Theory:** T\n- **Hypothesis:** H-001: \"x >= 0.9\"\n"
    fields = gate.record_fields(text)
    assert fields["Status"] == "planned"
    assert fields["Theory"] == "T"
    assert fields["Hypothesis"] == 'H-001: "x >= 0.9"'


def test_record_fields_ignores_non_bold():
    assert gate.record_fields("## Experiment: E-001\nplain line\n") == {}


def test_record_fields_multiline_value_only_first_line():
    fields = gate.record_fields("- **Theory:** first line\n  continuation\n")
    assert fields["Theory"] == "first line"


# --- deney_id / hypothesis_claim -------------------------------------------

def test_deney_id():
    assert gate.deney_id("## Experiment: E-042\n") == "E-042"
    assert gate.deney_id("no id") == "E-?"


def test_hypothesis_claim_quoted():
    hid, claim = gate.hypothesis_claim('H-001: "accuracy >= 0.90"')
    assert (hid, claim) == ("H-001", "accuracy >= 0.90")


def test_hypothesis_claim_unquoted_tail():
    hid, claim = gate.hypothesis_claim("H-007: accuracy >= 0.80")
    assert (hid, claim) == ("H-007", "accuracy >= 0.80")


def test_hypothesis_claim_no_colon_uses_tail():
    # A claim with whitespace but no H-id/colon falls back to the tail regex:
    # the text before the first separator is dropped, the rest is the claim.
    hid, claim = gate.hypothesis_claim("no id or claim")
    assert hid == "H-?"
    assert claim == "id or claim"


def test_hypothesis_claim_truly_empty_raises():
    with pytest.raises(ValueError):
        gate.hypothesis_claim("")


# --- parse_claim / evaluate ------------------------------------------------

def test_parse_claim_operators():
    assert gate.parse_claim("x >= 0.90") == (0.90, ">=")
    assert gate.parse_claim("x == 1") == (1.0, "==")
    assert gate.parse_claim("x < 5") == (5.0, "<")


def test_parse_claim_no_operator_raises():
    with pytest.raises(ValueError):
        gate.parse_claim("x is fine")


def test_parse_claim_bad_number_raises():
    with pytest.raises(ValueError):
        gate.parse_claim("x >= abc")


def test_evaluate_passes_and_fails():
    assert gate.evaluate("accuracy >= 0.90", 0.93)[0] is True
    assert gate.evaluate("accuracy >= 0.90", 0.87)[0] is False
    assert gate.evaluate("x == 1", 1)[0] is True


# --- uncertainty_note -------------------------------------------------------

def test_uncertainty_lower_bound_claim_no_note():
    # A lower-bound claim (<=, <) has no small-sample risk.
    note = gate.uncertainty_note(35, 40, 0.875, 0.8, "<=")
    assert "none" in note


def test_uncertainty_small_sample_flags():
    note = gate.uncertainty_note(3, 3, 1.0, 0.90, ">=")
    assert "small sample" in note


def test_uncertainty_adequate_sample_ok():
    # n=40 perfect (40/40) clears a 0.90 upper-threshold claim; 38/40 does not
    # (Wilson bound 0.83 < 0.90), so 38/40 is correctly flagged small-sample.
    assert "none" in gate.uncertainty_note(40, 40, 1.0, 0.90, ">=")
    assert "small sample" in gate.uncertainty_note(38, 40, 0.95, 0.90, ">=")


def test_uncertainty_unknown_n():
    assert "n unknown" in gate.uncertainty_note(None, None, 0.9, 0.8, ">=")


def test_uncertainty_inconsistent():
    note = gate.uncertainty_note(1, 10, 0.9, 0.5, ">=")
    assert "inconsistent" in note


# --- metric naming ----------------------------------------------------------

def test_metric_stem():
    assert gate.metric_stem("llm_overnight_accuracy") == "llm_overnight"
    assert gate.metric_stem("plain") == "plain"


def test_claim_metric_name():
    assert gate.claim_metric_name("llm_overnight_accuracy >= 0.90") == "llm_overnight"
    assert gate.claim_metric_name("no op here") == ""


# --- MEASURED_RE ------------------------------------------------------------

def test_measured_re_with_fraction():
    m = gate.MEASURED_RE.search("metric_accuracy=0.93 (14/15)")
    assert (m.group(2), m.group(3), m.group(4)) == ("0.93", "14", "15")


def test_measured_re_without_fraction():
    m = gate.MEASURED_RE.search("metric_score=0.80")
    assert (m.group(2), m.group(3), m.group(4)) == ("0.80", None, None)


def test_measured_re_negative():
    assert gate.MEASURED_RE.search("nothing here") is None


def _fake_run(monkeypatch, stdout):
    """Pin run_and_measure's stdout without spawning a process."""
    class _P:
        returncode = 0
        stderr = ""
    _P.stdout = stdout  # assigned here so the closure sees the parameter
    monkeypatch.setattr(gate.subprocess, "run",
                        lambda *a, **k: _P())


def test_run_and_measure_selects_claimed_metric(monkeypatch):
    """E-004: a multi-metric bench must bind the decision to the CLAIMED
    metric's line, not whichever metric_* line happened to print first."""
    _fake_run(monkeypatch,
              "metric_validity=0.00 (0/53)\n"
              "consistency_accuracy=1.00 (53/53)\n")
    val, x, y, stem = gate.run_and_measure("ignored", claim_metric="consistency")
    assert (val, x, y, stem) == (1.0, 53, 53, "consistency")


def test_run_and_measure_falls_back_to_first_line(monkeypatch):
    """Claim never printed: first line binds and the MISMATCH advisory fires
    downstream (backward-compatible behavior preserved)."""
    _fake_run(monkeypatch, "metric_validity=0.50 (1/2)\n")
    val, x, y, stem = gate.run_and_measure("ignored", claim_metric="consistency")
    # group(1) is 'metric_validity' -> stem strips _validity -> 'metric'
    assert (val, x, y, stem) == (0.5, 1, 2, "metric")


def test_run_and_measure_distinct_stems_may_differ(monkeypatch):
    """Different metrics printing different values is normal bench output —
    only the SAME stem with conflicting values fails closed."""
    _fake_run(monkeypatch,
              "other_validity=0.00 (0/53)\n"
              "consistency_accuracy=1.00 (53/53)\n")
    val, x, y, stem = gate.run_and_measure("ignored", claim_metric="consistency")
    assert (val, x, y, stem) == (1.0, 53, 53, "consistency")


def test_run_and_measure_rejects_conflicting_same_stem(monkeypatch):
    """Two lines, same stem, different values: self-contradicting output ->
    fail closed (ValueError), never silently pick one."""
    _fake_run(monkeypatch,
              "metric_accuracy=1.00 (53/53)\n"
              "metric_accuracy=0.00 (0/53)\n")
    try:
        gate.run_and_measure("ignored", claim_metric=None)
    except ValueError as exc:
        assert "conflicting" in str(exc)
    else:
        raise AssertionError("conflicting same-stem lines must raise")


def test_run_and_measure_identical_repeats_ok(monkeypatch):
    """The same metric line printed twice with identical values is not a
    conflict (banner + summary repetition)."""
    line = "consistency_accuracy=1.00 (53/53)\n"
    _fake_run(monkeypatch, line + line)
    val, x, y, stem = gate.run_and_measure("ignored", claim_metric="consistency")
    assert (val, x, y, stem) == (1.0, 53, 53, "consistency")


# --- gate_token -------------------------------------------------------------

def test_gate_token_deterministic():
    a = gate.gate_token("c >= 0.9", 1.0, "E-X", b"k")
    b = gate.gate_token("c >= 0.9", 1.0, "E-X", b"k")
    assert a == b


def test_gate_token_binds_cmd():
    a = gate.gate_token("c >= 0.9", 1.0, "E-X", b"k")
    b = gate.gate_token("c >= 0.9", 1.0, "E-X", b"k", "cmd")
    assert a != b


def test_gate_token_binds_scope():
    a = gate.gate_token("c >= 0.9", 1.0, "E-X", b"k", "cmd", "src/**")
    b = gate.gate_token("c >= 0.9", 1.0, "E-X", b"k", "cmd", "src/**,lib/**")
    assert a != b


def test_gate_token_binds_plan_hash():
    a = gate.gate_token("c >= 0.9", 1.0, "E-X", b"k", "cmd", "src/**")
    b = gate.gate_token("c >= 0.9", 1.0, "E-X", b"k", "cmd", "src/**", "a" * 64)
    c = gate.gate_token("c >= 0.9", 1.0, "E-X", b"k", "cmd", "src/**", "b" * 64)
    assert a != b and b != c


# --- amend-plan (E-065) ------------------------------------------------------

def _draft_with_plan(tmp_path, eid="E-201", planned="src/a.py"):
    rec = tmp_path / f"{eid}.md"
    rec.write_text(
        f"## Experiment: {eid}\n"
        "- **Status:** planned\n"
        "- **Theory:** T\n"
        '- **Hypothesis:** H-001: "fake_accuracy >= 0.90"\n'
        "- **Measurement Metrics:** fake_accuracy >= 0.90\n"
        "- **Experiment Design:** unit\n"
        "- **Code Scope:** none\n"
        "\n## Implementation Plan (GRP)\n\n"
        f"- **Planned Files:** {planned}\n"
        "- **Amendments:** none\n",
        encoding="utf-8")
    return rec


def test_amend_plan_refuses_missing_files_or_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("BMAD_GATE_KEY", "test-secret")
    rec = _draft_with_plan(tmp_path)
    assert gate.amend_plan_cli(str(rec), [], "reason") == 2
    assert gate.amend_plan_cli(str(rec), ["src/x.py"], "") == 2


def test_amend_plan_appends_chained_entry_and_widens_plan(tmp_path, monkeypatch):
    monkeypatch.setenv("BMAD_GATE_KEY", "test-secret")
    rec = _draft_with_plan(tmp_path)
    rc = gate.amend_plan_cli(str(rec), ["src/c.py"], "port discovered a third module")
    assert rc == 0
    text = rec.read_text(encoding="utf-8")
    assert "- **Amendments:** 1" in text
    assert "src/c.py" in text and "port discovered a third module" in text
    plan = _plan_module()
    assert plan.amendment_chain_issue(text) is None
    fields = plan.parse_plan_fields(text)
    assert fields["planned_files"] == ["src/a.py", "src/c.py"]


def test_amend_plan_refuses_decided_record(tmp_path, monkeypatch):
    monkeypatch.setenv("BMAD_GATE_KEY", "test-secret")
    rec = _draft_with_plan(tmp_path)
    lines = rec.read_text(encoding="utf-8").splitlines(keepends=False)
    lines = gate.upsert(lines, "- **Decision:**", "APPROVED — H-001: x")
    rec.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert gate.amend_plan_cli(str(rec), ["src/late.py"], "late file") == 2


def test_amend_plan_noop_when_all_files_present(tmp_path, monkeypatch):
    monkeypatch.setenv("BMAD_GATE_KEY", "test-secret")
    rec = _draft_with_plan(tmp_path)
    assert gate.amend_plan_cli(str(rec), ["src/a.py"], "already planned") == 0
    assert "- **Amendments:** none" in rec.read_text(encoding="utf-8")


def _plan_module():
    import importlib
    import modules.plan as plan  # the gate itself imports this via _ENGINE_DIR
    return importlib.reload(plan)


def test_verify_plan_stripping_forges(tmp_path, monkeypatch):
    """An APPROVED record's token binds to the final plan hash: rewriting the
    record WITHOUT its amendment chain (a post-approval plan strip) must
    re-derive a different hash and verify as FORGED."""
    monkeypatch.setenv("BMAD_GATE_KEY", "test-secret")
    plan = _plan_module()
    rec = _draft_with_plan(tmp_path, eid="E-202", planned="src/a.py, src/b.py")
    text = rec.read_text(encoding="utf-8")
    # Grow the plan mechanically (what --amend-plan does), then approve.
    text2, _chain = plan.append_amendment(text, 1, "2026-10-05", ["src/g.py"],
                                          "port helper", b"test-secret")
    text2 = plan.bump_planned_files(text2, ["src/g.py"])
    rec.write_text(text2, encoding="utf-8")
    lines = text2.splitlines(keepends=False)
    cmd = "run x"
    lines = gate.upsert(lines, "- **Raw Results:**", "measured=1.0; n=40")
    lines = gate.upsert(lines, "- **Uncertainty:**", "none (adequate)")
    lines = gate.upsert(lines, "- **Metric:**", "consistent")
    lines = gate.upsert(lines, "- **Measurement Command:**", cmd)
    tok = gate.gate_token("fake_accuracy >= 0.90", 1.0, "E-202", b"test-secret",
                          cmd, "none", plan.final_plan_hash(text2))
    lines = gate.upsert(lines, "- **Decision:**", "APPROVED — H-001: measured=1.0 >= threshold=0.90")
    lines = gate.upsert(lines, "- **Gate Evidence:**",
                        f'measured=1.0 claim="fake_accuracy >= 0.90" {tok}')
    lines = gate.upsert(lines, "- **Next Step:**", "Proceed to Code")
    lines = gate.upsert(lines, "- **Status:**", "completed")
    rec.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert gate.verify(str(rec)) == 0
    # Now strip the amendment chain + counter (post-approval plan tampering).
    stripped = "\n".join(l for l in rec.read_text(encoding="utf-8").splitlines()
                         if "- **Amendments:**" not in l and "AMEND-" not in l) + "\n"
    rec.write_text(stripped, encoding="utf-8")
    assert gate.verify(str(rec)) == 1


def test_verify_scope_widening_forges(tmp_path, monkeypatch):
    # Approved for "none", then scope widened post-approval → FORGED.
    monkeypatch.setenv("BMAD_GATE_KEY", "test-secret")
    rec = _write_approved(tmp_path)
    text = rec.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=False)
    lines = gate.upsert(lines, "- **Code Scope:**", "src/**")
    rec.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert gate.verify(str(rec)) == 1


def test_gate_token_differs_by_secret():
    a = gate.gate_token("c >= 0.9", 1.0, "E-X", b"k1")
    b = gate.gate_token("c >= 0.9", 1.0, "E-X", b"k2")
    assert a != b


# --- upsert ----------------------------------------------------------------

def test_upsert_replaces_existing():
    lines = ["- **Decision:** old", "- **Status:** planned"]
    new = gate.upsert(lines, "- **Decision:**", "APPROVED")
    assert new[0] == "- **Decision:** APPROVED"
    assert len(new) == 2


def test_upsert_appends_missing():
    lines = ["- **Status:** planned"]
    new = gate.upsert(lines, "- **Decision:**", "APPROVED")
    assert new[-1] == "- **Decision:** APPROVED"


# --- bench_in_free_zone -----------------------------------------------------

def test_bench_in_free_zone():
    assert gate.bench_in_free_zone("python3 scratch/bench.py") is True
    assert gate.bench_in_free_zone("python3 tmp/bench.py") is True
    # Guard-free surfaces are not measurement homes: graft/openhands/_bmad/
    # .metodoloji/explore_* benches must be refused exactly like scratch/.
    assert gate.bench_in_free_zone("python3 graft/bench.py") is True
    assert gate.bench_in_free_zone("python3 openhands/bench.py") is True
    assert gate.bench_in_free_zone("python3 _bmad/bench.py") is True
    assert gate.bench_in_free_zone("python3 .metodoloji/bench.py") is True
    assert gate.bench_in_free_zone("python3 explore_probe.py") is True
    assert gate.bench_in_free_zone("python3 ./explore_probe.py") is True  # guard norms ./ away
    assert gate.bench_in_free_zone("python3 explore_dir/bench.py") is False  # nested: guard gates it
    assert gate.bench_in_free_zone("python3 sub/explore_probe.py") is False  # not root-level
    assert gate.bench_in_free_zone("python3 src/bench.py data.csv") is False
    assert gate.bench_in_free_zone("sh scripts/bench.sh") is False
    assert gate.bench_in_free_zone("python3 -c 'print(1)'") is False


def test_bench_free_zone_guard_parity():
    """bench_in_free_zone must agree with guard.is_free on guard-free paths.

    A path the guard lets through unapproved must never host a measurement:
    the gate would APPROVE from a freely fabricable bench. The import follows
    the check-plugin.sh doctrine (engine is_free is the single free-surface
    source); a missing engine skips instead of asserting stale parity.
    """
    try:
        from modules.utils import is_free  # noqa: E402
    except Exception:
        import pytest as _pytest  # noqa: E402
        _pytest.skip("engine is_free unavailable")
    free_paths = [
        "scratch/bench.py",
        "tmp/bench.py",
        "temp/bench.py",
        "graft/bench.py",
        "openhands/bench_probe.py",
        "_bmad/bench.py",
        ".metodoloji/bench.py",
    ]  # .git/ is VCS-internal (git-managed); not an agent-writable surface.
    for path in free_paths:
        assert is_free(path), f"{path} should be guard-free"
        assert gate.bench_in_free_zone(f"python3 {path}") is True, (
            f"{path} is guard-free but hosts a gate-accepted bench")


# --- verify paths beyond selfcheck -----------------------------------------

def _draft_record(tmp_path, eid="E-001"):
    rec = tmp_path / f"{eid}.md"
    rec.write_text(
        f"## Experiment: {eid}\n"
        "- **Status:** planned\n"
        "- **Theory:** T\n"
        '- **Hypothesis:** H-001: "fake_accuracy >= 0.90"\n'
        "- **Measurement Metrics:** fake_accuracy >= 0.90\n"
        "- **Experiment Design:** unit\n"
        "- **Code Scope:** none\n",
        encoding="utf-8",
    )
    return rec


def _write_approved(tmp_path, secret=b"test-secret", cmd="run x"):
    """Create a record the gate has approved under `secret` and `cmd`."""
    rec = _draft_record(tmp_path)
    text = rec.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=False)
    lines = gate.upsert(lines, "- **Raw Results:**", "measured=1.0; n=40")
    lines = gate.upsert(lines, "- **Uncertainty:**", "none (adequate)")
    lines = gate.upsert(lines, "- **Metric:**", "consistent")
    lines = gate.upsert(lines, "- **Measurement Command:**", cmd)
    tok = gate.gate_token("fake_accuracy >= 0.90", 1.0, "E-001", secret, cmd, "none")
    lines = gate.upsert(lines, "- **Decision:**", "APPROVED — H-001: measured=1.0 >= threshold=0.90")
    lines = gate.upsert(
        lines, "- **Gate Evidence:**",
        f'measured=1.0 claim="fake_accuracy >= 0.90" {tok}')
    lines = gate.upsert(lines, "- **Next Step:**", "Proceed to Code")
    lines = gate.upsert(lines, "- **Status:**", "completed")
    rec.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return rec


def test_verify_missing_secret_returns_3(tmp_path, monkeypatch):
    monkeypatch.delenv("BMAD_GATE_KEY", raising=False)
    # Point SECRET_FILE and the trust-ring dir away so no key is found.
    monkeypatch.setattr(gate, "SECRET_FILE", str(tmp_path / "no-key"))
    monkeypatch.setattr(gate, "SECRETS_DIR", str(tmp_path / "no-ring"))
    rec = _write_approved(tmp_path)
    assert gate.verify(str(rec)) == 3


def test_verify_small_sample_advisory_block(tmp_path, monkeypatch):
    monkeypatch.setenv("BMAD_GATE_KEY", "test-secret")
    rec = _draft_record(tmp_path)
    text = rec.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=False)
    lines = gate.upsert(lines, "- **Raw Results:**", "measured=0.91; n=4")
    lines = gate.upsert(lines, "- **Uncertainty:**", "n=4 (small sample: 95% Wilson lower bound 0.55 < threshold 0.9)")
    lines = gate.upsert(lines, "- **Metric:**", "consistent")
    lines = gate.upsert(lines, "- **Measurement Command:**", "cmd")
    tok = gate.gate_token("fake_accuracy >= 0.90", 0.91, "E-001", b"test-secret", "cmd", "none")
    lines = gate.upsert(lines, "- **Decision:**", "APPROVED — H-001: measured=0.91 >= threshold=0.90")
    lines = gate.upsert(lines, "- **Gate Evidence:**", f'measured=0.91 claim="fake_accuracy >= 0.90" {tok}')
    lines = gate.upsert(lines, "- **Next Step:**", "Proceed to Code")
    lines = gate.upsert(lines, "- **Status:**", "completed")
    rec.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert gate.verify(str(rec)) == 2  # ADVISORY-BLOCK


def test_verify_metric_mismatch_advisory_block(tmp_path, monkeypatch):
    monkeypatch.setenv("BMAD_GATE_KEY", "test-secret")
    rec = _draft_record(tmp_path)
    text = rec.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=False)
    lines = gate.upsert(lines, "- **Raw Results:**", "measured=1.0; n=40")
    lines = gate.upsert(lines, "- **Uncertainty:**", "none (n=40)")
    lines = gate.upsert(lines, "- **Metric:**", "MISMATCH — measured other_metric, claimed fake_accuracy")
    lines = gate.upsert(lines, "- **Measurement Command:**", "cmd")
    tok = gate.gate_token("fake_accuracy >= 0.90", 1.0, "E-001", b"test-secret", "cmd", "none")
    lines = gate.upsert(lines, "- **Decision:**", "APPROVED — H-001: measured=1.0 >= threshold=0.90")
    lines = gate.upsert(lines, "- **Gate Evidence:**", f'measured=1.0 claim="fake_accuracy >= 0.90" {tok}')
    lines = gate.upsert(lines, "- **Next Step:**", "Proceed to Code")
    lines = gate.upsert(lines, "- **Status:**", "completed")
    rec.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert gate.verify(str(rec)) == 2


def test_verify_legacy_token_without_cmd(tmp_path, monkeypatch):
    # A record with no Measurement Command field must verify via the legacy
    # (non-command-bound) token.
    monkeypatch.setenv("BMAD_GATE_KEY", "test-secret")
    rec = _draft_record(tmp_path)
    text = rec.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=False)
    lines = gate.upsert(lines, "- **Raw Results:**", "measured=1.0; n=40")
    lines = gate.upsert(lines, "- **Uncertainty:**", "none (n=40)")
    lines = gate.upsert(lines, "- **Metric:**", "consistent")
    tok = gate.gate_token("fake_accuracy >= 0.90", 1.0, "E-001", b"test-secret")
    lines = gate.upsert(lines, "- **Decision:**", "APPROVED — H-001: measured=1.0 >= threshold=0.90")
    lines = gate.upsert(lines, "- **Gate Evidence:**", f'measured=1.0 claim="fake_accuracy >= 0.90" {tok}')
    lines = gate.upsert(lines, "- **Next Step:**", "Proceed to Code")
    lines = gate.upsert(lines, "- **Status:**", "completed")
    rec.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert gate.verify(str(rec)) == 0


def test_verify_rejected_returns_1(tmp_path, monkeypatch):
    monkeypatch.setenv("BMAD_GATE_KEY", "test-secret")
    rec = _draft_record(tmp_path)
    text = rec.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=False)
    lines = gate.upsert(lines, "- **Decision:**", "REJECTED — H-001: gate FAIL")
    lines = gate.upsert(lines, "- **Status:**", "REJECTED")
    rec.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert gate.verify(str(rec)) == 1


def test_verify_undecided_returns_1(tmp_path, monkeypatch):
    monkeypatch.setenv("BMAD_GATE_KEY", "test-secret")
    rec = _draft_record(tmp_path)
    assert gate.verify(str(rec)) == 1


def test_verify_template_placeholder_is_undecided(tmp_path, monkeypatch):
    monkeypatch.setenv("BMAD_GATE_KEY", "test-secret")
    rec = _draft_record(tmp_path)
    text = rec.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=False)
    lines = gate.upsert(lines, "- **Decision:**", "<gate writes: APPROVED | REJECTED — reason>")
    rec.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert gate.verify(str(rec)) == 1  # undecided, not forged-APPROVED


def test_verify_approved_without_evidence_forged(tmp_path, monkeypatch):
    monkeypatch.setenv("BMAD_GATE_KEY", "test-secret")
    rec = _draft_record(tmp_path)
    text = rec.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=False)
    lines = gate.upsert(lines, "- **Decision:**", "APPROVED — H-001: measured=1.0 >= threshold=0.90")
    lines = gate.upsert(lines, "- **Status:**", "completed")
    rec.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert gate.verify(str(rec)) == 1  # APPROVED without token -> forged


def test_verify_cross_machine_hint(tmp_path, monkeypatch, capsys):
    """A token that fails under every trusted key gets the two remedies:
    import the approving machine's key (--import-key) so it verifies here,
    or re-measure with a Re-Measured-By marker — provenance, not tampering."""
    # Record approved under b"test-secret" (see _write_approved), then verified
    # with a DIFFERENT key — exactly the second-machine situation.
    monkeypatch.setenv("BMAD_GATE_KEY", "other-machine-key")
    monkeypatch.setattr(gate, "SECRETS_DIR", str(tmp_path / "ring"))
    rec = _write_approved(tmp_path)
    assert gate.verify(str(rec)) == 1
    out = capsys.readouterr().out
    assert "--import-key" in out
    assert "Re-Measured-By" in out


def test_scope_matches():
    assert gate.scope_matches("src/**", "src/foo.py")
    assert gate.scope_matches("src/**", "src/engine/foo.py")
    assert not gate.scope_matches("src/**", "tests/foo.py")
    assert gate.scope_matches("src/*.py", "src/foo.py")
    assert not gate.scope_matches("src/*.py", "src/foo/bar.py")
    assert gate.scope_matches("src/**", "src\\engine\\foo.py")


def test_record_scope(tmp_path):
    rec = tmp_path / "E.md"
    rec.write_text("- **Code Scope:** src/**,tests/*\n", encoding="utf-8")
    assert gate.record_scope(str(rec)) == "src/**,tests/*"
    assert gate.record_scope(str(tmp_path / "missing.md")) == ""


# --- blackboard mirror (chain heartbeat) -------------------------------------

def test_derive_project_root_from_experiments_path(tmp_path):
    rec = tmp_path / "docs" / "experiments" / "E-001.md"
    assert gate._derive_project_root(str(rec), None) == str(tmp_path)


def test_derive_project_root_explicit_wins(tmp_path):
    assert gate._derive_project_root("anywhere.md", str(tmp_path)) == str(tmp_path)


def test_derive_project_root_temp_record_skips_mirror(tmp_path):
    # Temp-dir records (selfcheck/pytest) must never resolve a root — the
    # mirror must not pollute a real board from a test path.
    assert gate._derive_project_root(str(tmp_path / "E-001.md"), None) is None


def test_mirror_board_pass_writes_run_key_and_handoff(tmp_path):
    engine = Path(__file__).resolve().parents[4] / "hooks" / "engine"
    sys.path.insert(0, str(engine))
    from modules import blackboard as bb
    gate._mirror_board(str(tmp_path), "E-007", True, "fake_accuracy=1.0", "GATE-OK-x")
    board = bb.read_board(str(tmp_path))
    assert board["keys"]["E-007"]["value"].startswith("APPROVED")
    assert board["keys"]["methodology.last_experiment"]["value"].startswith("E-007")
    pending = bb.pending_handoffs(str(tmp_path), "bmad-check-implementation-readiness")
    assert len(pending) == 1 and pending[0]["text"].startswith("E-007")


def test_mirror_board_reject_writes_run_key_without_handoff(tmp_path):
    engine = Path(__file__).resolve().parents[4] / "hooks" / "engine"
    sys.path.insert(0, str(engine))
    from modules import blackboard as bb
    gate._mirror_board(str(tmp_path), "E-008", False, "fake_accuracy=0.1")
    board = bb.read_board(str(tmp_path))
    assert board["keys"]["E-008"]["value"].startswith("REJECTED")
    assert bb.pending_handoffs(str(tmp_path), "bmad-check-implementation-readiness") == []


def test_mirror_board_no_root_is_noop():
    # Fail-open: None root never raises, never writes anywhere.
    gate._mirror_board(None, "E-009", True, "x", "tok")


# --- trust ring (multi-machine development) --------------------------------
# The same repo is developed in parallel on several computers. Each machine
# signs with its own key, but --verify consults the developer's whole trust
# ring (own key + peer keys imported into ~/.bmad/gate-keys/): provenance
# across your own machines, not tampering.

PEER = "a" * 64


def test_trusted_secrets_own_key_only(tmp_path, monkeypatch):
    monkeypatch.delenv("BMAD_GATE_KEY", raising=False)
    monkeypatch.setattr(gate, "SECRET_FILE", str(tmp_path / "own-key"))
    monkeypatch.setattr(gate, "SECRETS_DIR", str(tmp_path / "ring"))
    (tmp_path / "own-key").write_text("b" * 64 + "\n", encoding="utf-8")
    ring = gate.trusted_secrets()
    assert ring == [b"b" * 64]


def test_trusted_secrets_ring_own_first_then_peers(tmp_path, monkeypatch):
    monkeypatch.delenv("BMAD_GATE_KEY", raising=False)
    monkeypatch.setattr(gate, "SECRET_FILE", str(tmp_path / "own-key"))
    monkeypatch.setattr(gate, "SECRETS_DIR", str(tmp_path / "ring"))
    (tmp_path / "own-key").write_text("b" * 64, encoding="utf-8")
    ring_dir = tmp_path / "ring"
    ring_dir.mkdir()
    (ring_dir / "laptop2.key").write_text(PEER, encoding="utf-8")
    (ring_dir / "desktop.key").write_text("c" * 64, encoding="utf-8")
    ring = gate.trusted_secrets()
    assert ring == [b"b" * 64, b"c" * 64, PEER.encode()]  # own first, peers sorted


def test_trusted_secrets_no_own_key_verify_still_works(tmp_path, monkeypatch):
    """A peer-only machine (e.g. fresh clone) verifies but cannot sign."""
    monkeypatch.delenv("BMAD_GATE_KEY", raising=False)
    monkeypatch.setattr(gate, "SECRET_FILE", str(tmp_path / "no-key"))
    monkeypatch.setattr(gate, "SECRETS_DIR", str(tmp_path / "ring"))
    ring_dir = tmp_path / "ring"
    ring_dir.mkdir()
    (ring_dir / "laptop.key").write_text(PEER, encoding="utf-8")
    assert gate.trusted_secrets() == [PEER.encode()]
    rec = _write_approved_under(tmp_path, PEER.encode())
    assert gate.verify(str(rec)) == 0


def _write_approved_under(tmp_path, secret):
    """A record the gate approved under a non-default secret."""
    rec = _draft_record(tmp_path)
    text = rec.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=False)
    lines = gate.upsert(lines, "- **Raw Results:**", "measured=1.0; n=40")
    lines = gate.upsert(lines, "- **Uncertainty:**", "none (adequate)")
    lines = gate.upsert(lines, "- **Metric:**", "consistent")
    lines = gate.upsert(lines, "- **Measurement Command:**", "run x")
    tok = gate.gate_token("fake_accuracy >= 0.90", 1.0, "E-001", secret, "run x", "none")
    lines = gate.upsert(lines, "- **Decision:**", "APPROVED — H-001: measured=1.0 >= threshold=0.90")
    lines = gate.upsert(
        lines, "- **Gate Evidence:**",
        f'measured=1.0 claim="fake_accuracy >= 0.90" {tok}')
    lines = gate.upsert(lines, "- **Next Step:**", "Proceed to Code")
    lines = gate.upsert(lines, "- **Status:**", "completed")
    rec.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return rec


def test_verify_matches_peer_ring_key(tmp_path, monkeypatch):
    """The contract: a record APPROVED on machine B verifies on machine A
    once B's key is imported — no re-measurement, no Re-Measured-By marker."""
    monkeypatch.setenv("BMAD_GATE_KEY", "own-key-material")
    monkeypatch.setattr(gate, "SECRETS_DIR", str(tmp_path / "ring"))
    rec = _write_approved_under(tmp_path, PEER.encode())
    assert gate.verify(str(rec)) == 1  # outside the ring -> FORGED
    ring_dir = tmp_path / "ring"
    ring_dir.mkdir(exist_ok=True)
    (ring_dir / "laptop2.key").write_text(PEER, encoding="utf-8")
    assert gate.verify(str(rec)) == 0  # import flips it to VERIFIED


def test_verify_still_rejects_genuinely_forged(tmp_path, monkeypatch):
    """Ring must not open the door: an unknown key's record stays FORGED."""
    monkeypatch.setenv("BMAD_GATE_KEY", "own-key-material")
    monkeypatch.setattr(gate, "SECRETS_DIR", str(tmp_path / "ring"))
    rec = _write_approved_under(tmp_path, b"attacker-key-012345678901234567890123456789012")
    ring_dir = tmp_path / "ring"
    ring_dir.mkdir(exist_ok=True)
    (ring_dir / "laptop2.key").write_text(PEER, encoding="utf-8")
    assert gate.verify(str(rec)) == 1


def test_verify_scope_widening_forges_with_ring(tmp_path, monkeypatch):
    """Ring acceptance still re-checks every binding: widening the Code Scope
    after approval breaks the token under every ring key too."""
    monkeypatch.setenv("BMAD_GATE_KEY", "test-secret")
    rec = _write_approved_under(tmp_path, b"test-secret")
    text = rec.read_text(encoding="utf-8").replace(
        "- **Code Scope:** none", "- **Code Scope:** none, extra/**")
    rec.write_text(text, encoding="utf-8")
    ring_dir = tmp_path / "ring"
    ring_dir.mkdir(exist_ok=True)
    (ring_dir / "laptop2.key").write_text("d" * 64, encoding="utf-8")
    assert gate.verify(str(rec)) == 1


def test_import_key_env_material_and_label(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(gate, "SECRETS_DIR", str(tmp_path / "ring"))
    monkeypatch.setenv("BMAD_PEER_GATE_KEY", PEER)
    monkeypatch.setenv("BMAD_PEER_LABEL", "laptop2")
    assert gate.import_key() == 0
    out = capsys.readouterr().out
    assert "laptop2.key" in out
    assert (tmp_path / "ring" / "laptop2.key").read_text(encoding="utf-8").strip() == PEER


def test_import_key_refuses_bad_material_and_missing_env(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "SECRETS_DIR", str(tmp_path / "ring"))
    monkeypatch.delenv("BMAD_PEER_GATE_KEY", raising=False)
    assert gate.import_key() == 2  # no material
    monkeypatch.setenv("BMAD_PEER_GATE_KEY", "zzz-not-hex")
    assert gate.import_key() == 2  # malformed
    assert not (tmp_path / "ring").exists() or list((tmp_path / "ring").glob("*.key")) == []


def test_check_secret_reports_ring(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("BMAD_GATE_KEY", raising=False)
    monkeypatch.setattr(gate, "SECRET_FILE", str(tmp_path / "own-key"))
    monkeypatch.setattr(gate, "SECRETS_DIR", str(tmp_path / "ring"))
    (tmp_path / "own-key").write_text("b" * 64, encoding="utf-8")
    assert gate.check_secret() == 0
    out = capsys.readouterr().out
    assert "own key only" in out
    ring_dir = tmp_path / "ring"
    ring_dir.mkdir()
    (ring_dir / "laptop2.key").write_text(PEER, encoding="utf-8")
    assert gate.check_secret() == 0
    out = capsys.readouterr().out
    assert "2 keys" in out and "1 peer" in out


def test_missing_field_hint_names_similar_heading():
    """`## Experiment Design` heading vs required bullet: the hint names both
    (2026-09-25 LIMX: agent read gate source 7× instead of fixing format)."""
    text = ("- **Theory:** t\n- **Hypothesis:** H-001: \"a >= 0.9\"\n"
            "## Experiment Design\nsome plan\n")
    hint = gate._missing_field_hint(text, "Experiment Design")
    assert "similar 'Experiment Design'" in hint
    assert "- **Experiment Design:**" in hint


def test_missing_field_hint_without_candidate_points_to_template():
    hint = gate._missing_field_hint("- **Theory:** t\n", "Code Scope")
    assert "_template.md" in hint and "similar" not in hint

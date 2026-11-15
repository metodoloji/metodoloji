"""E-170: `verify` re-reads the evidence a record seals.

The gap: the gate stamps `sha256=` into 'Raw Sample Evidence' at approval, but
`verify` only checked the HMAC token — editing or deleting `samples.jsonl`
after approval was invisible. A bytes-only seal cannot simply be made fatal:
24 benches embed a live corpus count in a sample's `detail`, so re-running one
changes the bytes without touching the claim (20 legacy records already drift).

The seal therefore gains `verdicts=` — a digest over each sample's identity and
its `ok`. Stable across prose edits, broken by a flipped verdict.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]
                       / "skills" / "bmad-research-experiment" / "scripts"))

import run_experiment as gate  # noqa: E402


def _write(tmp_path: pathlib.Path, lines: list[str]) -> pathlib.Path:
    p = tmp_path / "samples.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _seal_for(p: pathlib.Path, tmp_path: pathlib.Path, *, verdicts: bool) -> dict:
    _, n, _, digest, vdigest = gate._load_raw_samples(p)
    rel = p.relative_to(tmp_path).as_posix()
    line = f"`{rel}` — {n} samples, {n} ok, sha256={digest}"
    if verdicts:
        line += f", verdicts={vdigest}"
    return {"Raw Sample Evidence": line}


def test_a_flipped_verdict_is_detected(tmp_path):
    p = _write(tmp_path, ['{"check": "a", "ok": true}', '{"check": "b", "ok": true}'])
    fields = _seal_for(p, tmp_path, verdicts=True)
    assert gate.evidence_seal(fields, str(tmp_path)) == ("", "")

    # Flip one verdict: the count is unchanged, only the claim changed.
    _write(tmp_path, ['{"check": "a", "ok": false}', '{"check": "b", "ok": true}'])
    state, reason = gate.evidence_seal(fields, str(tmp_path))
    assert state == "VERDICT-DRIFT", reason


def test_prose_edits_do_not_break_a_verdict_seal(tmp_path):
    p = _write(tmp_path, ['{"check": "a", "ok": true}'])
    fields = _seal_for(p, tmp_path, verdicts=True)
    # A live corpus count in `detail` moves the bytes but not the verdict.
    _write(tmp_path, ['{"check": "a", "ok": true, "detail": "160 record(s)"}'])
    assert gate.evidence_seal(fields, str(tmp_path)) == ("", "")


def test_missing_evidence_is_reported(tmp_path):
    p = _write(tmp_path, ['{"check": "a", "ok": true}'])
    fields = _seal_for(p, tmp_path, verdicts=True)
    p.unlink()
    assert gate.evidence_seal(fields, str(tmp_path))[0] == "MISSING"


def test_a_legacy_seal_drifts_by_count(tmp_path):
    p = _write(tmp_path, ['{"check": "a", "ok": true}'])
    fields = _seal_for(p, tmp_path, verdicts=False)
    assert gate.evidence_seal(fields, str(tmp_path)) == ("", "")
    _write(tmp_path, ['{"check": "a", "ok": true}', '{"check": "b", "ok": true}'])
    assert gate.evidence_seal(fields, str(tmp_path))[0] == "COUNT-DRIFT"


def test_a_record_without_an_evidence_line_is_not_flagged(tmp_path):
    assert gate.evidence_seal({"Raw Sample Evidence": "none — no per-sample claim"},
                              str(tmp_path)) == ("", "")

"""chain-gate.py's fault excerpt keeps lines whole and counts what it hid (E-194).

The failure message used `out[:300]`, so a schema check with twelve defects
printed five and a half of them and stopped mid-word. These tests pin the
replacement: whole lines only, an explicit `(+N more lines)` count, and the
shared cutter as the fallback for a single over-long line.
"""

from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys

PLUGIN = pathlib.Path(__file__).resolve().parents[2]
HOOK_SCRIPTS = PLUGIN / "hooks" / "scripts"

spec = importlib.util.spec_from_file_location(
    "chain_gate", HOOK_SCRIPTS / "chain-gate.py")
chain_gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(chain_gate)


def _lines(count: int) -> str:
    return "\n".join(f"  - defect number {i} in the record" for i in range(count))


def test_short_output_is_printed_verbatim():
    text = "FAIL: record.md (contract)\n  - one defect"
    assert chain_gate._excerpt(text) == text


def test_output_is_cut_on_line_boundaries_and_counts_the_rest():
    text = _lines(40)
    excerpt = chain_gate._excerpt(text, limit=120)
    kept = excerpt.splitlines()
    marker = kept[-1]
    assert marker.startswith("… (+") and marker.endswith("more lines)")
    for line in kept[:-1]:
        assert line in text.splitlines()      # no line was sliced
    hidden = int(marker.split("(+", 1)[1].split(" ", 1)[0])
    # the count is the point: a reader must know how much was withheld
    assert len(kept) - 1 + hidden == 40


def test_one_line_hidden_is_named_in_the_singular():
    text = _lines(6)
    excerpt = chain_gate._excerpt(text, limit=len(text) - 5)
    assert excerpt.endswith("(+1 more line)")
    assert "more lines" not in excerpt


def test_a_single_over_long_line_is_marked_and_never_halves_a_token():
    token = "GATE-OK-E-192-02810bde1da438c02f9d1bafa129ba50"
    text = "prefix " * 40 + f"({token})"
    excerpt = chain_gate._excerpt(text, limit=90)
    assert excerpt.endswith("…")
    assert token[:20] not in excerpt
    assert len(excerpt) <= 91


def test_schema_failure_hides_nothing_silently(tmp_path):
    """The real checker's fault list names its remaining defects."""
    source = PLUGIN / "docs" / "experiments" / "E-193.md"
    keep = [line for line in source.read_text(encoding="utf-8").splitlines()
            if line.startswith(("## Experiment:", "- **Record Schema:",
                                "- **Date:", "- **Record Kind:"))]
    record = tmp_path / "E-900.md"
    record.write_text("\n".join(keep) + "\n", encoding="utf-8")

    rc, out = chain_gate._run([sys.executable,
                               str(PLUGIN / "scripts" / "create-record.py"),
                               "--check", str(record)])
    assert rc == 1 and out.count("missing or placeholder") > 6

    message = chain_gate._excerpt(out)
    lines = message.splitlines()
    assert lines[-1].startswith("… (+") and lines[-1].endswith("more lines)")
    for line in lines[:-1]:
        assert line in out.splitlines()          # every shown line is verbatim
    assert "'Experime" not in message           # no mid-word fragment


def test_check_still_fails_closed_with_the_counted_excerpt(tmp_path, capsys):
    record = tmp_path / "E-900.md"
    record.write_text("## Experiment: E-900\n- **Record Schema:** "
                      "metodoloji.record/v1\n- **Record Kind:** contract\n",
                      encoding="utf-8")
    assert chain_gate._check(tmp_path, "experiment", [record]) == 1
    printed = capsys.readouterr().out
    assert "schema check failed" in printed
    assert "more line" in printed  # nothing is dropped without a count

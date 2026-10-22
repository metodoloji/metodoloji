"""Comparison primitive: can the gate decide "A beats B"?

Every record before this could say "X meets T". None could say "X beats Y",
because the gate had no comparison claim form, no pairing and no test for
whether a difference exceeds noise. Those are the questions architecture is
actually made of, so the gap made the methodology unable to adjudicate a design
choice it had just measured.

The contract tested here:
  * the hypothesis names two metrics and no threshold ("a_rate > b_rate");
  * both are printed by ONE run (two runs could differ for unrelated reasons);
  * the samples are paired per case (`{"a": true, "b": false}` per line);
  * PASS requires the direction to favour the left condition AND an exact
    McNemar test on the discordant pairs to clear p < 0.05.
"""

import json
import pathlib
import subprocess
import sys

PLUGIN = pathlib.Path(__file__).resolve().parents[2]
GATE = PLUGIN / "skills/bmad-research-experiment/scripts/run_experiment.py"

RECORD = """## Experiment: E-901 — comparison fixture

- **Date:** 21.10.2026
- **Status:** planned
- **Theory:** A paired comparison fixture.
- **Hypothesis:** H-901: "variant_a_rate > variant_b_rate"
- **Measurement Metrics:** variant_a_rate > variant_b_rate
- **Experiment Design:** one bench prints both conditions over the same cases.
- **Sample Size n:** %(n)d
- **Code Scope:** scripts/bench/cmp.py
- **Lineage:** none
- **Raw Samples:** raw/paired.jsonl
- **Status:** planned
"""

BENCH = '''
import json, pathlib
CASES = %(cases)r
raw = pathlib.Path("raw"); raw.mkdir(exist_ok=True)
with (raw / "paired.jsonl").open("w") as f:
    for i, (a, b) in enumerate(CASES):
        f.write(json.dumps({"i": i, "a": a, "b": b}) + chr(10))
na = sum(1 for a, _ in CASES if a); nb = sum(1 for _, b in CASES if b); n = len(CASES)
print("variant_a_rate={:.2f} ({}/{})".format(na/n, na, n))
print("variant_b_rate={:.2f} ({}/{})".format(nb/n, nb, n))
'''


def _project(tmp_path, cases, record=RECORD):
    (tmp_path / "docs/experiments").mkdir(parents=True)
    (tmp_path / "scripts/bench").mkdir(parents=True)
    (tmp_path / "scripts/bench/cmp.py").write_text(BENCH % {"cases": cases}, encoding="utf-8")
    (tmp_path / "docs/experiments/E-901.md").write_text(
        record % {"n": len(cases)}, encoding="utf-8")
    return tmp_path


def _run(root):
    return subprocess.run(
        [sys.executable, str(GATE), "--record", str(root / "docs/experiments/E-901.md"),
         "--project-root", str(root), "--no-blackboard",
         "--run", "python3 scripts/bench/cmp.py"],
        cwd=str(root), capture_output=True, text=True, timeout=300,
        stdin=subprocess.DEVNULL)


def _cases(a_ok, b_ok, n, a_only=0):
    """n cases: a_ok/b_ok total passes, of which `a_only` are A-only wins."""
    cases = []
    for i in range(n):
        a = i < a_ok
        b = i < b_ok
        cases.append((a, b))
    return cases


def test_a_clear_winner_is_approved_with_an_effect_size(tmp_path):
    """A beats B by 25 points on 100 paired cases — that is a finding."""
    root = _project(tmp_path, _cases(95, 70, 100))
    res = _run(root)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "DECISION: APPROVED" in res.stdout
    record = (root / "docs/experiments/E-901.md").read_text(encoding="utf-8")
    assert "**Comparison:**" in record
    assert "delta=+0.2500" in record
    assert "McNemar exact p=" in record
    assert "discordant +25/-0" in record


def test_a_tie_is_rejected(tmp_path):
    """Equal rates cannot support 'A is better', however large n is."""
    root = _project(tmp_path, _cases(80, 80, 100))
    res = _run(root)
    assert res.returncode == 1, res.stdout + res.stderr
    assert "DECISION: REJECTED" in res.stdout


def test_a_head_start_inside_the_noise_is_rejected(tmp_path):
    """A came out ahead, but the discordant pairs do not support the claim.

    This is the case the whole primitive exists for: without McNemar, every
    corpus run that happens to end 22-vs-20 would be recorded as a win.
    """
    cases = []
    for i in range(40):
        cases.append((i < 22, i < 20))
    root = _project(tmp_path, cases)
    res = _run(root)
    assert res.returncode == 1, res.stdout + res.stderr
    assert "not distinguishable from noise" in res.stdout


def test_comparison_without_paired_samples_is_refused(tmp_path):
    """Two unpaired rates are two samples of the same noise."""
    record = RECORD.replace("- **Raw Samples:** raw/paired.jsonl", "- **Raw Samples:** none")
    root = _project(tmp_path, _cases(95, 70, 100), record=record)
    res = _run(root)
    assert res.returncode == 2, res.stdout + res.stderr
    assert "paired samples" in res.stderr


def test_paired_file_must_carry_both_verdicts(tmp_path):
    """A file with only one verdict per line is not a pairing."""
    root = _project(tmp_path, _cases(95, 70, 100))
    raw = root / "raw/paired.jsonl"
    raw.parent.mkdir(parents=True)
    raw.write_text("".join(json.dumps({"ok": True}) + "\n" for _ in range(100)),
                   encoding="utf-8")
    # the bench rewrites the file, so run the gate against a bench that does not
    (root / "scripts/bench/cmp.py").write_text(
        'print("variant_a_rate=0.95 (95/100)")\nprint("variant_b_rate=0.70 (70/100)")\n',
        encoding="utf-8")
    res = _run(root)
    assert res.returncode == 2, res.stdout + res.stderr
    assert "must carry boolean 'a' and 'b'" in res.stderr


def test_reproduce_re_derives_the_delta(tmp_path):
    """--reproduce on a comparison must re-run the pairs, not read a rate line.

    The recorded value of a comparison is the delta; a naive re-run would compare
    a rate against a delta and report NOT REPRODUCED for a perfectly good record.
    """
    root = _project(tmp_path, _cases(95, 70, 100))
    first = _run(root)
    assert first.returncode == 0, first.stdout + first.stderr
    res = subprocess.run(
        [sys.executable, str(GATE), "--record", str(root / "docs/experiments/E-901.md"),
         "--project-root", str(root), "--reproduce"],
        cwd=str(root), capture_output=True, text=True, timeout=300,
        stdin=subprocess.DEVNULL)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "REPRODUCED" in res.stdout

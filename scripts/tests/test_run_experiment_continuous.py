"""Continuous comparisons: "A is 1.2 ms faster" is a claim about numbers.

The categorical primitive decides "A passes the case, B does not". Most
architecture questions are not like that — they are about a distribution: this
codec's p95 versus that one's, over the same inputs. Without a numeric pairing
the corpus could only ever say "A met 50 ms" and "B met 50 ms", which is not a
comparison at all.

Contract: `- **Measure:** continuous`, a paired JSONL of NUMBERS (`{"a": 0.84,
"b": 1.31}` per case), and a hypothesis naming the two conditions. PASS requires
the median difference to favour the left condition AND a two-sided Wilcoxon
signed-rank test at p < 0.05.
"""

import json
import pathlib
import subprocess
import sys

PLUGIN = pathlib.Path(__file__).resolve().parents[2]
GATE = PLUGIN / "skills/bmad-research-experiment/scripts/run_experiment.py"

RECORD = """## Experiment: E-902 — continuous comparison

- **Date:** 21.10.2026
- **Status:** planned
- **Theory:** A paired numeric comparison fixture.
- **Hypothesis:** H-902: "int8_ms < float16_ms"
- **Measurement Metrics:** int8_ms < float16_ms
- **Experiment Design:** one bench measures both codecs on the same inputs.
- **Sample Size n:** %(n)d
- **Measure:** continuous
- **Code Scope:** scripts/bench/cmp.py
- **Lineage:** none
- **Raw Samples:** raw/paired.jsonl
- **Status:** planned
"""

BENCH = '''
import json, pathlib
PAIRS = %(pairs)r
raw = pathlib.Path("raw"); raw.mkdir(exist_ok=True)
with (raw / "paired.jsonl").open("w") as f:
    for i, (a, b) in enumerate(PAIRS):
        f.write(json.dumps({"i": i, "a": a, "b": b}) + chr(10))
'''


def _project(tmp_path, pairs, record=RECORD):
    (tmp_path / "docs/experiments").mkdir(parents=True)
    (tmp_path / "scripts/bench").mkdir(parents=True)
    (tmp_path / "scripts/bench/cmp.py").write_text(BENCH % {"pairs": pairs}, encoding="utf-8")
    (tmp_path / "docs/experiments/E-902.md").write_text(
        record % {"n": len(pairs)}, encoding="utf-8")
    return tmp_path


def _run(root, *extra):
    return subprocess.run(
        [sys.executable, str(GATE), "--record", str(root / "docs/experiments/E-902.md"),
         "--project-root", str(root), "--no-blackboard",
         "--run", "python3 scripts/bench/cmp.py", *extra],
        cwd=str(root), capture_output=True, text=True, timeout=300,
        stdin=subprocess.DEVNULL)


def test_consistently_faster_is_approved(tmp_path):
    """A is 0.9 ms faster on every one of 20 inputs — that is a finding."""
    pairs = [(9.0 + i * 0.01, 9.9 + i * 0.01) for i in range(20)]
    root = _project(tmp_path, pairs)
    res = _run(root)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "DECISION: APPROVED" in res.stdout
    record = (root / "docs/experiments/E-902.md").read_text(encoding="utf-8")
    assert "**Comparison:**" in record
    assert "Wilcoxon signed-rank" in record
    assert "median difference -0.9" in record


def test_faster_on_average_but_noisy_is_rejected(tmp_path):
    """The median is ahead, but the differences straddle zero.

    This is the case the numeric primitive exists for: a benchmark that wins on
    average by luck would otherwise become an architectural decision.
    """
    pairs = [(10.0, 10.1)] * 7 + [(12.0, 10.0)] * 5
    root = _project(tmp_path, pairs)
    res = _run(root)
    assert res.returncode == 1, res.stdout + res.stderr
    assert "DECISION: REJECTED" in res.stdout
    assert "not distinguishable from noise" in res.stdout


def test_slower_than_the_alternative_is_rejected(tmp_path):
    """Direction matters: 'int8 < float16' fails when int8 is slower."""
    pairs = [(20.0 + i, 10.0 + i) for i in range(12)]
    root = _project(tmp_path, pairs)
    res = _run(root)
    assert res.returncode == 1, res.stdout + res.stderr


def test_too_few_pairs_is_refused_not_approved(tmp_path):
    """Below n=6 the test cannot reach p<0.05; pretending otherwise is a lie."""
    pairs = [(1.0, 2.0), (1.1, 2.1), (1.2, 2.2), (1.3, 2.3)]
    root = _project(tmp_path, pairs)
    res = _run(root)
    assert res.returncode == 2, res.stdout + res.stderr
    assert "cannot be decided" in res.stderr


def test_verdicts_in_a_continuous_file_are_refused(tmp_path):
    """Booleans are a categorical file wearing a numeric label."""
    root = _project(tmp_path, [(1.0, 2.0)] * 20)
    raw = root / "raw/paired.jsonl"
    raw.parent.mkdir(parents=True)
    raw.write_text("".join(json.dumps({"a": True, "b": False}) + "\n" for _ in range(20)),
                   encoding="utf-8")
    (root / "scripts/bench/cmp.py").write_text("pass\n", encoding="utf-8")
    res = _run(root)
    assert res.returncode == 2, res.stdout + res.stderr
    assert "NUMERIC" in res.stderr


def test_reproduce_re_derives_the_numeric_delta(tmp_path):
    root = _project(tmp_path, [(9.0 + i * 0.01, 9.9 + i * 0.01) for i in range(20)])
    first = _run(root)
    assert first.returncode == 0, first.stdout + first.stderr
    res = subprocess.run(
        [sys.executable, str(GATE), "--record", str(root / "docs/experiments/E-902.md"),
         "--project-root", str(root), "--reproduce"],
        cwd=str(root), capture_output=True, text=True, timeout=300,
        stdin=subprocess.DEVNULL)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "REPRODUCED" in res.stdout

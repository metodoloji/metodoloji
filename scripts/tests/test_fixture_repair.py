"""E-166: six shipped benches measured a spelling that had legitimately moved.

The defect these tests pin: at e18a677 six benches read red while the code they
measure was correct — two baseline fixtures captured before the E-148/E-149
moves (so later records' legitimate edits read as drift) and four probes pinned
to an exact spelling that E-139/E-143 changed.

The benches themselves are bound to CI by E-162's `run_all.py` (a red bench
fails the job), so this file asserts the cheap invariants only: the population
is intact and the probes are wired to real scripts. The expensive red→green
proof lives in the bench's own `--control` arm, which the gate runs.
"""

from __future__ import annotations

import pathlib
import sys

BENCH_DIR = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "bench"
if str(BENCH_DIR) not in sys.path:
    sys.path.insert(0, str(BENCH_DIR))

import bench_fixture_repair as bench  # noqa: E402


def test_the_population_is_intact():
    """Every member still ships and still differs from the base commit."""
    ok, detail = bench._case_the_population_is_intact()
    assert ok, detail


def test_the_population_names_only_shipped_benches():
    for name in bench._POPULATION:
        assert (BENCH_DIR / name).is_file(), f"{name} does not ship"


def test_the_control_reaches_the_base_tree():
    """The control must measure a real pre-repair tree, not a hardcoded failure."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="fixture-reach-") as td:
        base = bench._base_tree(pathlib.Path(td))
        assert (base / "scripts" / "bench" / bench._POPULATION[0]).is_file()
        assert not (base / "docs" / "experiments" / "E-165.md").exists()

"""Tests for the release-number rule itself (E-013).

The rule answers "when do we move to v0.2.0?" with arithmetic, so it has to be
exact: the mapping must be total, its inverse must refuse every number the
mapping cannot produce (`0.1.150`, `0.9.3`, `1.0.0`), and the boundary must
land where the docs say it lands. A rule that is one off here silently
misnumbers every future release, which is the failure E-012 repaired.
"""

import importlib.util
import pathlib
import subprocess
import sys

PLUGIN = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = PLUGIN / "scripts" / "versioning.py"

_spec = importlib.util.spec_from_file_location("versioning", SCRIPT)
versioning = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(versioning)


def _cli(*args: str):
    proc = subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          stdin=subprocess.DEVNULL,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def test_blocks_open_every_hundred_positions():
    """The boundary the docs promise: position 100 IS 0.2.0, position 200 0.3.0."""
    assert versioning.AUTO_BLOCK == 100
    assert versioning.auto_version(0) == "0.1.0"
    assert versioning.auto_version(1) == "0.1.1"
    assert versioning.auto_version(14) == "0.1.14"
    assert versioning.auto_version(99) == "0.1.99"
    assert versioning.auto_version(100) == "0.2.0"
    assert versioning.auto_version(101) == "0.2.1"
    assert versioning.auto_version(199) == "0.2.99"
    assert versioning.auto_version(200) == "0.3.0"
    assert versioning.auto_version(1000) == "0.11.0"


def test_position_is_the_exact_inverse():
    """Round-trip both ways: no number is reachable twice, none is orphaned."""
    for position in list(range(0, 250)) + [400, 999, 1234]:
        assert versioning.auto_position(versioning.auto_version(position)) == position
    for version in ("0.1.0", "0.1.99", "0.2.0", "0.2.50", "0.3.7", "0.11.0"):
        assert versioning.auto_version(versioning.auto_position(version)) == version


def test_impossible_numbers_are_refused():
    """Everything the mapping cannot produce must fail loudly, never be written."""
    for version in ("0.1.100",       # a third digit inside a block
                    "0.1.150",
                    "0.2.250",
                    "0.0.0",         # no block zero
                    "1.0.0",         # a declared line, not a counted number
                    "1.2.3",
                    "0.1",           # not plain X.Y.Z
                    "0.1.x"):
        try:
            versioning.auto_position(version)
        except versioning.VersionError:
            continue
        raise AssertionError(f"{version} should have been refused")
    try:
        versioning.auto_version(-1)
    except versioning.VersionError:
        pass
    else:
        raise AssertionError("a negative position should have been refused")


def test_line_helpers():
    """The line helpers the scripts and the CI branch on."""
    assert versioning.line("0.2.7") == (0, 2)
    assert versioning.opening("0.2.7") == "0.2.0"
    assert versioning.is_opening("0.2.0") is True
    assert versioning.is_opening("0.2.1") is False
    assert versioning.same_line("0.2.0", "0.2.9") is True
    assert versioning.same_line("0.2.0", "0.3.0") is False
    assert versioning.is_auto("0.9.9") is True     # on the automatic line
    assert versioning.is_auto("1.0.0") is False    # declared from here


def test_cli_answers_the_pipeline_questions():
    """The shell side of the rule: the CI composes tags from exactly these."""
    assert _cli("--block")[1] == "100"
    assert _cli("--position", "100")[1] == "0.2.0"
    assert _cli("--position-of", "0.2.0")[1] == "100"
    assert _cli("--opening", "0.2.7")[1] == "0.2.0"
    assert _cli("--is-auto", "0.1.14")[0] == 0
    assert _cli("--is-auto", "1.0.0")[0] == 1
    assert _cli("--check", "0.1.99")[0] == 0
    code, out, err = _cli("--check", "0.1.150")
    assert code == 1 and "0.2.50" in err, (code, out, err)
    assert _cli("--position", "-3")[0] == 1

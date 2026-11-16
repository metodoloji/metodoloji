"""The chain-handoff bench's fixture must speak the dialect the parser reads.

E-171: two refusal probes in ``bench_chain_handoff.py`` scored a *parser miss*
as a gate decision. The fixture wrote ``experiment_refs:`` as bare body text,
while ``story_records._parse_experiment_refs`` reads the YAML frontmatter block
only — so the gate saw a story with no measured basis and, since the fixture
declares Tier 1, opened the run. Both probes were green for the wrong reason
until the tier exemption made the vacuous path visible as a red bench.

These pins read the parser the gate reads and the fixture the bench writes, in
one process: no gate spawn, so the check costs a fraction of a second.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.engine.modules import story_records  # noqa: E402


def _bench():
    spec = importlib.util.spec_from_file_location(
        "bench_chain_handoff", ROOT / "scripts" / "bench" / "bench_chain_handoff.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_fixture_writes_refs_the_parser_reads(tmp_path):
    path = _bench()._story(tmp_path, "S-007", refs=("E-171", "E-172"))
    ids = [r.get("id") for r in story_records._parse_experiment_refs(
        path.read_text(encoding="utf-8"))]
    assert ids == ["E-171", "E-172"], (
        "the bench fixture's refs do not survive the parser the gate uses; "
        "every probe built on this helper would measure an empty ref block")


def test_the_fixture_declares_no_refs_when_none_are_given(tmp_path):
    path = _bench()._story(tmp_path, "S-008")
    assert story_records._parse_experiment_refs(
        path.read_text(encoding="utf-8")) == []

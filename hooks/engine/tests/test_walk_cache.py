"""The record walk is one per batch, and never outlives it.

A project with 29 records walked its documentation tree 29 times per hook call -
166k scandir calls, ~14s of a 15s budget - because every record lookup re-walked
the tree. The walk is now shared through the batch cache, which means two
things must stay true and are pinned here:

1. one batch, many lookups, one walk (the cost this fixes);
2. the walk is dropped when the batch closes (the correctness this must not
   lose: a record written afterwards has to be visible).
"""
import pathlib

from hooks.engine.modules import freshness as fr
from hooks.engine.modules import state


def _project(tmp_path, ids):
    exp = tmp_path / "docs" / "experiments"
    exp.mkdir(parents=True)
    for rid in ids:
        (exp / f"{rid}.md").write_text(f"# {rid}\n", encoding="utf-8")
    return tmp_path


def _count_walks(monkeypatch):
    calls = []
    original = pathlib.Path.rglob

    def counting(self, pattern):
        calls.append(str(self))
        return original(self, pattern)

    monkeypatch.setattr(pathlib.Path, "rglob", counting)
    return calls


def test_one_batch_walks_the_tree_once(tmp_path, monkeypatch):
    root = _project(tmp_path, ["E-001", "E-002", "E-003"])
    walks = _count_walks(monkeypatch)

    with fr.batch():
        for rid in ("E-001", "E-002", "E-003", "E-004"):
            state.record_files(root, rid, ("docs/experiments",), fr._WALK_CACHE)

    assert len(walks) == 1, f"walked {len(walks)} times inside one batch"


def test_the_walk_is_dropped_when_the_batch_closes(tmp_path, monkeypatch):
    root = _project(tmp_path, ["E-001"])
    walks = _count_walks(monkeypatch)

    with fr.batch():
        assert state.record_files(root, "E-001", ("docs/experiments",), fr._WALK_CACHE)
    with fr.batch():
        assert state.record_files(root, "E-001", ("docs/experiments",), fr._WALK_CACHE)

    assert len(walks) == 2, "each batch must take its own snapshot"


def test_a_record_written_after_the_batch_is_seen(tmp_path):
    root = _project(tmp_path, ["E-001"])

    with fr.batch():
        assert state.record_files(root, "E-001", ("docs/experiments",), fr._WALK_CACHE)
        assert not state.record_files(root, "E-002", ("docs/experiments",), fr._WALK_CACHE)

    (root / "docs" / "experiments" / "E-002.md").write_text("# E-002\n", encoding="utf-8")

    with fr.batch():
        assert state.record_files(root, "E-002", ("docs/experiments",), fr._WALK_CACHE), (
            "a record written between batches must not be answered from the older snapshot")


def test_without_a_cache_every_lookup_walks(tmp_path, monkeypatch):
    """A caller with no batch (a writer, a test) must never get a snapshot."""
    root = _project(tmp_path, ["E-001"])
    walks = _count_walks(monkeypatch)

    state.record_files(root, "E-001", ("docs/experiments",))
    state.record_files(root, "E-001", ("docs/experiments",))

    assert len(walks) == 2, "an uncached lookup must read the tree as it is now"

"""SQLite WAL blackboard backend: transactional state + audit.

The JSON backend stays the default. These tests pin that opting into SQLite
stores the same board in one database, keeps the event stream, migrates an
existing JSON board once, and never leaves the snapshot and audit log under
separate failure domains.
"""

import json
import os
import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HOOKS))

from modules import blackboard as bb  # noqa: E402


def _sqlite(monkeypatch):
    monkeypatch.setenv("METODOLOJI_BLACKBOARD_BACKEND", "sqlite")


def test_sqlite_is_the_shipped_default(tmp_path, monkeypatch):
    """With no env override, the shipped config selects SQLite for a new board."""
    monkeypatch.delenv("METODOLOJI_BLACKBOARD_BACKEND", raising=False)
    from modules import config
    assert config.blackboard_backend() == "sqlite"
    root = str(tmp_path)
    bb.write_key(root, "default-board", "v")
    assert Path(bb.board_paths(root)["db"]).is_file()
    assert not Path(bb.board_paths(root)["snapshot"]).exists()


def test_sqlite_backend_writes_one_database(tmp_path, monkeypatch):
    _sqlite(monkeypatch)
    root = str(tmp_path)
    ack = bb.write_key(root, "decision", "ship it")
    assert ack["ok"] is True
    assert Path(bb.board_paths(root)["db"]).is_file()
    # The JSON snapshot is not written in SQLite mode.
    assert not Path(bb.board_paths(root)["snapshot"]).exists()
    board = bb.read_board(root)
    assert board["keys"]["decision"]["value"] == "ship it"


def test_sqlite_backend_keeps_the_event_stream(tmp_path, monkeypatch):
    _sqlite(monkeypatch)
    root = str(tmp_path)
    bb.write_key(root, "a", "1")
    bb.list_add(root, "items", "x")
    conn = __import__("sqlite3").connect(bb.board_paths(root)["db"])
    try:
        events = conn.execute("SELECT data FROM events ORDER BY seq").fetchall()
    finally:
        conn.close()
    kinds = [json.loads(row[0])["event"] for row in events]
    assert "write" in kinds
    assert "list_add" in kinds


def test_sqlite_backend_migrates_an_existing_json_board(tmp_path, monkeypatch):
    root = str(tmp_path)
    paths = bb.board_paths(root)
    Path(paths["dir"]).mkdir(parents=True, exist_ok=True)
    board = bb._empty_board()
    board["keys"]["legacy"] = {"value": "kept", "type": "note", "ts": 1.0}
    Path(paths["snapshot"]).write_text(json.dumps(board), encoding="utf-8")
    Path(paths["events"]).parent.mkdir(parents=True, exist_ok=True)
    Path(paths["events"]).write_text(
        json.dumps({"event": "write", "key": "from-log", "value": "v",
                    "type": "note", "hot": False, "ts": 2.0}) + "\n",
        encoding="utf-8")

    _sqlite(monkeypatch)
    migrated = bb.read_board(root)
    assert migrated["keys"]["legacy"]["value"] == "kept"
    # The event stream is imported for audit even though the snapshot stays
    # authoritative for state (the same rule the JSON backend applies).
    conn = __import__("sqlite3").connect(bb.board_paths(root)["db"])
    try:
        events = [json.loads(r[0]) for r in
                  conn.execute("SELECT data FROM events ORDER BY seq")]
    finally:
        conn.close()
    assert any(e.get("key") == "from-log" for e in events)


def test_sqlite_transaction_rolls_back_a_failed_mutation(tmp_path, monkeypatch):
    _sqlite(monkeypatch)
    root = str(tmp_path)
    bb.write_key(root, "stable", "before")
    # A mutator that appends an event and then raises must not half-write.
    def boom(board):
        bb._append_event(bb.board_paths(root), {"event": "write", "key": "ghost",
                                                 "value": "no", "ts": 1.0})
        raise RuntimeError("boom")

    try:
        bb._mutate(root, boom)
    except RuntimeError:
        pass
    board = bb.read_board(root)
    assert board["keys"]["stable"]["value"] == "before"
    assert "ghost" not in board["keys"]


def test_sqlite_read_does_not_create_a_board(tmp_path, monkeypatch):
    """A read must not leave a runtime database in a project it only looked at."""
    _sqlite(monkeypatch)
    root = str(tmp_path)
    assert bb.read_board(root)["keys"] == {}
    assert bb.event_stats(bb.board_paths(root)) == ([], 0)
    assert not Path(bb.board_paths(root)["db"]).exists()
    assert not Path(bb.board_paths(root)["dir"]).exists()


def test_sqlite_event_stats_reads_the_json_log_before_the_first_import(
        tmp_path, monkeypatch):
    """A legacy board is not 'silent' just because nothing migrated it yet."""
    _sqlite(monkeypatch)
    root = str(tmp_path)
    paths = bb.board_paths(root)
    Path(paths["events"]).parent.mkdir(parents=True, exist_ok=True)
    Path(paths["events"]).write_text(
        json.dumps({"event": "write", "key": "E-001", "value": "v"}) + "\n",
        encoding="utf-8")
    events, garbage = bb.event_stats(paths)
    assert [e["key"] for e in events] == ["E-001"]
    assert garbage == 0


def test_sqlite_event_stats_does_not_resurrect_a_rotated_stream(
        tmp_path, monkeypatch):
    """After a rotate the database is authoritative even if the file lingers."""
    _sqlite(monkeypatch)
    root = str(tmp_path)
    bb.write_key(root, "live", "v")
    paths = bb.board_paths(root)
    Path(paths["events"]).parent.mkdir(parents=True, exist_ok=True)
    Path(paths["events"]).write_text(
        json.dumps({"event": "write", "key": "stale", "value": "v"}) + "\n",
        encoding="utf-8")
    with bb._board_lock(paths):
        bb.clear_events(paths)
    assert bb.event_stats(paths) == ([], 0)


def test_sqlite_event_cap_counts_rows_not_seq_arithmetic(tmp_path, monkeypatch):
    """A burned AUTOINCREMENT id must not make the cap delete extra rows."""
    _sqlite(monkeypatch)
    root = str(tmp_path)
    paths = bb.board_paths(root)
    conn = bb._sqlite_conn(paths)
    try:
        conn.execute("INSERT INTO board (id, data, updated) "
                     "VALUES (1, ?, 0)", (json.dumps(bb._empty_board()),))
        for i in range(3):
            conn.execute("INSERT INTO events (ts, data) VALUES (0, ?)",
                         (json.dumps({"event": "write", "key": f"k{i}"}),))
        # A rolled-back insert consumed ids: the next row lands far away.
        conn.execute("UPDATE sqlite_sequence SET seq = 99 WHERE name = 'events'")
        conn.execute("INSERT INTO events (ts, data) VALUES (0, ?)",
                     (json.dumps({"event": "write", "key": "newest"}),))
    finally:
        conn.close()
    old = bb.MAX_EVENTS
    bb.MAX_EVENTS = 2
    try:
        with bb._board_lock(paths):
            bb._truncate_events_if_needed(paths)
    finally:
        bb.MAX_EVENTS = old
    keys = [e["key"] for e in bb.event_stats(paths)[0]]
    assert keys == ["k2", "newest"]


def test_rotate_imports_before_archiving_and_touches_no_boardless_project(
        tmp_path, monkeypatch):
    """`rotate` must archive the history it has, and create nothing.

    It used to read an empty SQLite events table before the one-time JSON
    import, so a legacy board rotated to an archive of zero events; and the
    lock opens the database with create=True, so rotating a project with no
    board left a runtime database behind (E-125).
    """
    _sqlite(monkeypatch)
    legacy = tmp_path / "legacy"
    paths = bb.board_paths(str(legacy))
    Path(paths["events"]).parent.mkdir(parents=True, exist_ok=True)
    Path(paths["events"]).write_text(
        json.dumps({"event": "write", "key": "E-001", "value": "v"}) + "\n",
        encoding="utf-8")
    ack = bb.rotate_event_log(str(legacy), max_lines=1)
    assert ack["ok"] is True and ack["lines_archived"] == 1
    empty = tmp_path / "empty"
    assert bb.rotate_event_log(str(empty), max_lines=1)["ok"] is False
    assert not Path(bb.board_paths(str(empty))["db"]).exists()

def test_rotate_is_not_drift_and_rebuild_repairs_real_drift(tmp_path, monkeypatch):
    """`rotate` resets the stream; the doctor must not call that drift.

    The remedy the drift warning names (`blackboard.py rebuild`) must also
    exist, and must refuse a stream that cannot testify about the board —
    replaying a rotated or capped log would DROP live state (E-125).
    """
    _sqlite(monkeypatch)
    root = str(tmp_path)
    for i in range(3):
        bb.write_key(root, f"k{i}", "v")
    assert bb.doctor(root)["checks"]["drift"]["in_sync"] is True
    assert bb.rotate_event_log(root, max_lines=1)["ok"] is True
    d = bb.doctor(root)
    assert d["checks"]["drift"]["in_sync"] is True
    assert not any("drift" in w for w in d["warnings"])
    assert sorted(bb.read_board(root)["keys"]) == ["k0", "k1", "k2"]
    # A rotated stream cannot testify: a rebuild would write an empty board.
    assert bb.rebuild(root)["ok"] is False
    assert sorted(bb.read_board(root)["keys"]) == ["k0", "k1", "k2"]


def test_rebuild_repairs_a_drifted_snapshot(tmp_path, monkeypatch):
    _sqlite(monkeypatch)
    root = str(tmp_path)
    bb.write_key(root, "kept", "v")
    paths = bb.board_paths(root)
    conn = bb._sqlite_conn(paths)
    try:
        data = json.loads(conn.execute(
            "SELECT data FROM board WHERE id = 1").fetchone()[0])
        data["keys"]["ghost"] = {"value": "x", "type": "note", "updated": 1.0}
        conn.execute("UPDATE board SET data = ? WHERE id = 1",
                     (json.dumps(data),))
    finally:
        conn.close()
    assert bb.doctor(root)["checks"]["drift"]["in_sync"] is False
    assert bb.rebuild(root)["ok"] is True
    assert bb.doctor(root)["checks"]["drift"]["in_sync"] is True
    assert sorted(bb.read_board(root)["keys"]) == ["kept"]

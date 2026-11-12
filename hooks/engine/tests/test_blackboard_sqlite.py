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

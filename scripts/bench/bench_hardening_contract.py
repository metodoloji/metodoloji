"""Closed-set contract bench for the E-125 hardening batch.

    python scripts/bench/bench_hardening_contract.py --samples docs/experiments/E-125/raw/samples.jsonl
    python scripts/bench/bench_hardening_contract.py --broken   # rule-7 control

Every case is a defect the batch closed, exercised through the SHIPPED code:
a read that created a board, event diagnostics that called a live legacy board
silent, an event cap that cut past N once AUTOINCREMENT had burned ids, a tier
parser that read `12` as Tier 1 (in the engine AND in the S-record generator),
a `sh` script that only ran under bash, and a drift verdict whose named remedy
(`blackboard.py rebuild`) did not exist and would have wiped the board had it.
`--broken` runs the same predicates against the pre-fix rule, so rule 7 can see
the bench distinguish them.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sqlite3
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hooks" / "engine"))

from modules import blackboard as bb  # noqa: E402
from modules import blackboard_diagnostics as diag  # noqa: E402
from modules.config import story_rigor_tier  # noqa: E402

CLI = ROOT / "bmad" / "scripts" / "blackboard.py"

BROKEN = False

_OLD_TIER_RE = re.compile(
    r"^(?:[-*]\s+)?\*{0,2}(?:Rigor\s+Tier|Tier)\*{0,2}\s*:\s*\*{0,2}\s*(\d)",
    re.IGNORECASE | re.MULTILINE)
_OLD_TIER_TABLE_RE = re.compile(
    r"^\s*\|\s*(?:Rigor\s+)?Tier\s*\|\s*(\d)", re.IGNORECASE | re.MULTILINE)

_BAD_TIERS = ("12", "13", "10", "2.5", "2x", "two")
_GOOD_TIERS = ("1", "2", "3")


def _old_tier(text: str) -> int:
    """The pre-fix engine rule: one DIGIT of either dialect."""
    m = _OLD_TIER_RE.search(text) or _OLD_TIER_TABLE_RE.search(text)
    if not m:
        return 3
    return int(m.group(1)) if m.group(1) in ("1", "2", "3") else 3


def _backend(name: str) -> None:
    os.environ["METODOLOJI_BLACKBOARD_BACKEND"] = name


def _fresh() -> str:
    return tempfile.mkdtemp()


def _dash_parses_the_checker() -> bool:
    script = ROOT / "scripts" / "check-methodology.sh"
    if not BROKEN:
        return subprocess.run(["sh", "-n", str(script)], capture_output=True,
                              stdin=subprocess.DEVNULL).returncode == 0
    # The pre-fix shape: a herestring, which dash refuses to parse at all.
    probe = pathlib.Path(_fresh()) / "probe.sh"
    probe.write_text('read x <<< "a"\necho "$x"\n', encoding="utf-8")
    return subprocess.run(["sh", "-n", str(probe)], capture_output=True,
                          stdin=subprocess.DEVNULL).returncode == 0


def _tier_rejects_malformed() -> bool:
    return all((_old_tier if BROKEN else story_rigor_tier)(
        f"- **Rigor Tier:** {bad}\n") == 3 for bad in _BAD_TIERS)


def _tier_accepts_declared() -> bool:
    read = _old_tier if BROKEN else story_rigor_tier
    for good in _GOOD_TIERS:
        if read(f"- **Rigor Tier:** {good}\n") != int(good):
            return False
        if read(f"| Rigor Tier | {good} |\n") != int(good):
            return False
    return True


def _generator_agrees_with_the_engine() -> bool:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "gen", ROOT / "scripts" / "create-methodology-record.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    for value in _BAD_TIERS + _GOOD_TIERS:
        text = f"- **Rigor Tier:** {value}\n"
        expected = _old_tier(text) if BROKEN else story_rigor_tier(text)
        if gen.extract_story_metadata(text)["rigor_tier"] != str(expected):
            return False
    return True


def _read_creates_no_board() -> bool:
    _backend("sqlite")
    root = _fresh()
    if BROKEN:
        # The pre-fix read path: opening the connection creates the database.
        bb._sqlite_conn(bb.board_paths(root)).close()
    else:
        bb.read_board(root)
    paths = bb.board_paths(root)
    return not any(os.path.isfile(paths[k])
                   for k in ("db", "snapshot", "events"))


def _pre_import_log_is_read() -> bool:
    _backend("sqlite")
    root = _fresh()
    paths = bb.board_paths(root)
    os.makedirs(os.path.dirname(paths["events"]), exist_ok=True)
    with open(paths["events"], "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"event": "write", "key": "E-001"}) + "\n")
    if BROKEN:
        # The pre-fix diagnostic read: SQLite only, so with no database the
        # legacy board reads as silent even though its log is right there.
        return os.path.isfile(paths["db"]) and bb.event_stats(paths)[0] != []
    return [e["key"] for e in bb.event_stats(paths)[0]] == ["E-001"]


def _rotated_stream_is_not_resurrected() -> bool:
    _backend("sqlite")
    root = _fresh()
    bb.write_key(root, "live", "v")
    paths = bb.board_paths(root)
    os.makedirs(os.path.dirname(paths["events"]), exist_ok=True)
    with open(paths["events"], "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"event": "write", "key": "stale"}) + "\n")
    with bb._board_lock(paths):
        bb.clear_events(paths)
    if BROKEN:
        # The pre-fix fallback: an empty table fell through to the stale file.
        with open(paths["events"], encoding="utf-8") as fh:
            return not any(line.strip() for line in fh)
    return bb.event_stats(paths) == ([], 0)


def _rotate_is_not_drift() -> bool:
    """`rotate` resets the stream; a suffix can never testify about the board."""
    _backend("sqlite")
    root = _fresh()
    for i in range(3):
        bb.write_key(root, f"k{i}", "v")
    assert bb.rotate_event_log(root, max_lines=1)["ok"] is True
    # Post-rotate writes make the retained window non-empty but still partial.
    bb.write_key(root, "after", "v")
    if BROKEN:
        # The pre-fix comparator replayed the suffix against the full snapshot
        # and called the board's own maintenance DRIFTED.
        paths = bb.board_paths(root)
        return bb._replay_events(paths) == bb._read_snapshot(paths)
    return diag._doctor_drift(bb.board_paths(root), bb.read_board(root))["in_sync"]


def _drift_remedy_exists_and_repairs() -> bool:
    """The doctor names a command the CLI actually accepts, and it repairs."""
    _backend("sqlite")
    root = _fresh()
    bb.write_key(root, "kept", "v")
    if BROKEN:
        # The pre-fix warning named `blackboard.py rebuild`, an entry point the
        # CLI never shipped, so the forged drift below was unrepairable.
        return False
    paths = bb.board_paths(root)
    conn = bb._sqlite_conn(paths)
    try:  # forge a snapshot the event stream does not fold to
        data = json.loads(conn.execute(
            "SELECT data FROM board WHERE id = 1").fetchone()[0])
        data["keys"]["ghost"] = {"value": "x", "type": "note", "updated": 1.0}
        conn.execute("UPDATE board SET data = ? WHERE id = 1", (json.dumps(data),))
    finally:
        conn.close()
    m = re.search(r"`blackboard\.py (\w+)`", " ".join(bb.doctor(root)["warnings"]))
    if not m:
        return False
    run = subprocess.run([sys.executable, str(CLI), m.group(1), "--project-root",
                          root], capture_output=True, text=True,
                         stdin=subprocess.DEVNULL)
    if run.returncode != 0:
        return False
    return (bb.doctor(root)["checks"]["drift"]["in_sync"]
            and sorted(bb.read_board(root)["keys"]) == ["kept"])


def _rebuild_refuses_a_partial_stream() -> bool:
    """A rebuild from a rotated stream would DROP live state, so it refuses."""
    _backend("sqlite")
    root = _fresh()
    for i in range(3):
        bb.write_key(root, f"k{i}", "v")
    bb.rotate_event_log(root, max_lines=1)
    paths = bb.board_paths(root)
    if BROKEN:
        # The pre-fix remedy had no such guard: a rebuild replayed whatever
        # stream it found and wrote it over the snapshot, deleting the keys the
        # rotated-away events carried. Model exactly that and ask whether the
        # board survived.
        with bb._board_lock(paths):
            bb._write_snapshot_atomic(paths, bb._replay_events(paths))
        return sorted(bb.read_board(root)["keys"]) == ["k0", "k1", "k2"]
    refused = not bb.rebuild(root)["ok"]
    # ... and a stream capped by the truncation guard is a suffix too.
    old = bb.MAX_EVENTS
    bb.MAX_EVENTS = 2
    try:
        for i in range(4):
            bb.write_key(root, f"c{i}", "v")
        refused = refused and not bb.rebuild(root)["ok"]
    finally:
        bb.MAX_EVENTS = old
    return refused and sorted(bb.read_board(root)["keys"]) == [
        "c0", "c1", "c2", "c3", "k0", "k1", "k2"]


def _event_cap_keeps_newest_rows() -> bool:
    _backend("sqlite")
    root = _fresh()
    paths = bb.board_paths(root)
    conn = bb._sqlite_conn(paths)
    try:
        conn.execute("INSERT INTO board (id, data, updated) VALUES (1, ?, 0)",
                     (json.dumps(bb._empty_board()),))
        for i in range(3):
            conn.execute("INSERT INTO events (ts, data) VALUES (0, ?)",
                         (json.dumps({"event": "write", "key": f"k{i}"}),))
        conn.execute("UPDATE sqlite_sequence SET seq = 99 WHERE name = 'events'")
        conn.execute("INSERT INTO events (ts, data) VALUES (0, ?)",
                     (json.dumps({"event": "write", "key": "newest"}),))
    finally:
        conn.close()
    old = bb.MAX_EVENTS
    bb.MAX_EVENTS = 2
    try:
        with bb._board_lock(paths):
            if BROKEN:
                # The pre-fix bound: seq arithmetic cuts past the cap.
                bb._sqlite_active(paths).execute(
                    "DELETE FROM events WHERE seq <= "
                    "(SELECT MAX(seq) FROM events) - ?", (bb.MAX_EVENTS,))
            else:
                bb._truncate_events_if_needed(paths)
    finally:
        bb.MAX_EVENTS = old
    keys = [e["key"] for e in bb.event_stats(paths)[0]]
    return keys == ["k2", "newest"]


CASES = (
    ("dash_parses_the_checker", _dash_parses_the_checker,
     "the checker parses under sh/dash (no herestring)"),
    ("tier_rejects_malformed", _tier_rejects_malformed,
     "12 / 2.5 / 2x / two fall back to Tier 3"),
    ("tier_accepts_declared", _tier_accepts_declared,
     "1|2|3 parse in both the bullet and the table dialect"),
    ("generator_agrees_with_the_engine", _generator_agrees_with_the_engine,
     "the S-record generator reads the tier with the engine's rule"),
    ("read_creates_no_board", _read_creates_no_board,
     "reading a project with no board leaves no database behind"),
    ("pre_import_log_is_read", _pre_import_log_is_read,
     "a legacy JSON log is not reported as a silent board"),
    ("rotated_stream_is_not_resurrected", _rotated_stream_is_not_resurrected,
     "an empty events table after rotate does not fall back to the stale file"),
    ("event_cap_keeps_newest_rows", _event_cap_keeps_newest_rows,
     "the cap keeps the newest N rows despite burned AUTOINCREMENT ids"),
    ("rotate_is_not_drift", _rotate_is_not_drift,
     "a rotated stream is a suffix, not a snapshot divergence"),
    ("drift_remedy_exists_and_repairs", _drift_remedy_exists_and_repairs,
     "the named remedy is a real CLI command that repairs the board"),
    ("rebuild_refuses_a_partial_stream", _rebuild_refuses_a_partial_stream,
     "a rebuild over a rotated/capped window refuses instead of dropping state"),
)


def main() -> int:
    global BROKEN
    ap = argparse.ArgumentParser()
    ap.add_argument("--broken", action="store_true",
                    help="run the pre-fix rules (the rule-7 control)")
    ap.add_argument("--samples", default=None,
                    help="JSONL path for per-case evidence")
    args = ap.parse_args()
    BROKEN = args.broken

    results = []
    for name, probe, detail in CASES:
        try:
            ok = bool(probe())
        except Exception as exc:  # a raising probe is a failed case, not a crash
            ok, detail = False, f"{detail} — raised {exc!r}"
        results.append({"check": name, "ok": ok, "detail": detail})

    if args.samples:
        path = pathlib.Path(args.samples)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for r in results:
                fh.write(json.dumps(r) + "\n")

    x = sum(1 for r in results if r["ok"])
    n = len(results)
    for r in results:
        print(f"[{'OK' if r['ok'] else 'MISS'}] {r['check']}: {r['detail']}")
    print(f"hardening_contract_accuracy={x / n:.2f} ({x}/{n})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

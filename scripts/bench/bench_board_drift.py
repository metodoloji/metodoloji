"""Board drift classification bench — E-117.

docs/experiments/E-117.md (H-117: board_drift_classification_accuracy >= 0.95).

The board's doctor compares the event-log replay with the stored snapshot. Both
representations carry the 24-hour handoff TTL, but only the replay applies it
while reading: the snapshot is cleaned on the next mutation, because reads are
deliberately side-effect free (E-040). A board that has simply been idle for a
day therefore reads DRIFTED — the replay has dropped the expired signal and the
snapshot still carries it — and the audit tells the operator to delete a
healthy snapshot.

This census asks the comparator the question it exists to answer: did the two
representations diverge, or did time pass? The stale-handoff fixtures are the
discriminator; the snapshot-only key/alert/link fixtures prove the fix does not
turn into a blanket "in sync" by ignoring every difference.

`--broken` reproduces the pre-fix comparator (the same normalization without
the TTL filter). Rule 7: it must score below the threshold.

Deterministic, offline, stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hooks" / "engine"))

# The fixtures ARE the JSON representation: they read `blackboard.json` by hand
# and age the handoff in place, so this census cannot run on the shipped SQLite
# default (it did not — E-117/E-118's measurement command raised FileNotFound
# after the backend flip, E-125). Pinned, not defaulted: a census must not
# change shape with the operator's environment.
os.environ["METODOLOJI_BLACKBOARD_BACKEND"] = "json"

from modules import blackboard as bb  # noqa: E402
from modules import blackboard_diagnostics as diag  # noqa: E402

TTL = bb.HANDOFF_SIGNAL_TTL_SECONDS
OLD = TTL + 3600  # one hour past the handoff TTL

_BROKEN = False


def _paths(root: str) -> dict:
    return bb.board_paths(root)


def _read_json(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: pathlib.Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def _post_handoff(root: str) -> str:
    """Create one handoff signal through the shipped write path."""
    ack = bb.post_alert(root, "handoff.bmad-test", "handoff",
                        "E-999: fixture signal")
    board = _read_json(pathlib.Path(_paths(root)["snapshot"]))
    return str(board["alerts"][0]["id"]) if board.get("alerts") else ""


def _age_handoff(root: str, seconds: int) -> None:
    """Age the handoff signal in BOTH representations, then let the TTL differ.

    The snapshot keeps the alert; the replay prunes it because the fold applies
    the same TTL. This is the state a board reaches by being idle, not by being
    corrupted.
    """
    p = _paths(root)
    old_ts = time.time() - seconds
    snap = _read_json(pathlib.Path(p["snapshot"]))
    for alert in snap.get("alerts", []):
        if alert.get("kind") == "handoff":
            alert["ts"] = old_ts
    # The attribution ledger carries the same ts as the alert it came from;
    # age it too, so the fixture ages the signal rather than manufacturing a
    # second, unrelated divergence in the ledger.
    for entries in snap.get("open_handoffs", {}).values():
        for entry in entries:
            if isinstance(entry, list) and len(entry) > 1:
                entry[-1] = old_ts
    _write_json(pathlib.Path(p["snapshot"]), snap)

    events = pathlib.Path(p["events"])
    rewritten = []
    for line in events.read_text(encoding="utf-8").splitlines():
        ev = json.loads(line)
        if ev.get("event") == "alert" and ev.get("kind") == "handoff":
            ev["ts"] = old_ts
        rewritten.append(json.dumps(ev))
    events.write_text("\n".join(rewritten) + "\n", encoding="utf-8")


def _snapshot_only(root: str, mutate) -> None:
    """Apply a mutation to the snapshot only (a true divergence)."""
    path = pathlib.Path(_paths(root)["snapshot"])
    snap = _read_json(path)
    mutate(snap)
    _write_json(path, snap)


def _add_snapshot_key(snap: dict) -> None:
    snap["keys"]["fixture.snapshot-only"] = {
        "value": "snapshot", "type": "state", "updated": time.time(),
    }


def _add_snapshot_session_alert(snap: dict) -> None:
    snap["alerts"].append({
        "id": "m-fixture-snapshot", "channel": "session", "kind": "info",
        "text": "snapshot-only alert", "ts": time.time(),
    })


def _add_snapshot_stale_session_alert(snap: dict) -> None:
    snap["alerts"].append({
        "id": "m-fixture-old-session", "channel": "session", "kind": "info",
        "text": "old but not a handoff", "ts": time.time() - OLD,
    })


def _add_snapshot_link(snap: dict) -> None:
    snap["links"].append({
        "a": "fixture.a", "b": "fixture.b", "relation": "related",
        "ts": time.time(),
    })


def _drop_snapshot_alerts(snap: dict) -> None:
    snap["alerts"] = []


def _remove_replay_handoff_alert(root: str) -> None:
    p = _paths(root)
    events = pathlib.Path(p["events"])
    kept = []
    for line in events.read_text(encoding="utf-8").splitlines():
        ev = json.loads(line)
        if ev.get("event") == "alert" and ev.get("kind") == "handoff":
            continue
        kept.append(json.dumps(ev))
    events.write_text("\n".join(kept) + "\n", encoding="utf-8")


def _write_replay_only_key(root: str) -> None:
    p = _paths(root)
    with pathlib.Path(p["events"]).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "event": "write", "key": "fixture.replay-only", "value": "replay",
            "type": "state", "ts": time.time(),
        }) + "\n")


def _norm(board: dict) -> dict:
    """The doctor's drift normalization, without the TTL rule (pre-fix shape)."""
    keys = {k: v for k, v in board["keys"].items() if v.get("type") != "tool"}
    return {
        "keys": keys,
        "tags": board["tags"],
        "canvases": board["canvases"],
        "consumed": board.get("consumed", {}),
        "open_handoffs": board.get("open_handoffs", {}),
        "links": board["links"],
        "subscriptions": board["subscriptions"],
        "alerts": board["alerts"],
        "hot": board["hot"],
        "hot_canvas": board["hot_canvas"],
    }


def _pre_fix_in_sync(root: str) -> bool:
    paths = _paths(root)
    board = bb._read_snapshot(paths)
    replay = bb._replay_events(paths)
    return _norm(replay) == _norm(board)


def _ttl_norm(board: dict, now: float) -> dict:
    """The candidate contract: normalize the snapshot and the replay with the
    same 24-hour handoff TTL before asking whether they diverged."""
    data = _norm(board)
    data["alerts"] = [
        alert for alert in data["alerts"]
        if not (alert.get("kind") == "handoff"
                and now - float(alert.get("ts", 0.0) or 0.0) > TTL)
    ]
    return data


def _candidate_in_sync(root: str) -> bool:
    paths = _paths(root)
    now = time.time()
    return (_ttl_norm(bb._replay_events(paths), now)
            == _ttl_norm(bb._read_snapshot(paths), now))


def _in_sync(root: str) -> bool:
    if _BROKEN:
        return _pre_fix_in_sync(root)
    # Before the fix lands the shipped comparator is the pre-fix one; measure
    # the candidate contract so the gate decides on the proposed behaviour, and
    # once the module carries the marker measure the shipped code itself.
    if getattr(diag, "_DRIFT_TTL_NORMALIZED", False):
        paths = _paths(root)
        return diag._doctor_drift(paths, bb._read_snapshot(paths))["in_sync"]
    return _candidate_in_sync(root)


def _case(name: str, expected: bool, setup, detail: str) -> dict:
    with tempfile.TemporaryDirectory() as td:
        setup(td)
        actual = _in_sync(td)
    return {
        "check": name,
        "expected": expected,
        "actual": actual,
        "ok": actual == expected,
        "detail": detail,
    }


def _empty(_root: str) -> None:
    return None


def _fresh(root: str) -> None:
    _post_handoff(root)


def _stale(root: str) -> None:
    _post_handoff(root)
    _age_handoff(root, OLD)


def _stale_plus_shared_key(root: str) -> None:
    _post_handoff(root)
    # Mutate before aging: a write after the TTL would itself clean the
    # snapshot and turn the fixture into the fresh case.
    bb.write_key(root, "fixture.shared", "both sides")
    _age_handoff(root, OLD)


def _stale_plus_snapshot_key(root: str) -> None:
    _stale(root)
    _snapshot_only(root, _add_snapshot_key)


def _stale_plus_snapshot_alert(root: str) -> None:
    _stale(root)
    _snapshot_only(root, _add_snapshot_session_alert)


def _fresh_plus_snapshot_key(root: str) -> None:
    _fresh(root)
    _snapshot_only(root, _add_snapshot_key)


def _snapshot_stale_session_alert(root: str) -> None:
    _fresh(root)
    _snapshot_only(root, _add_snapshot_stale_session_alert)


def _snapshot_link(root: str) -> None:
    _fresh(root)
    _snapshot_only(root, _add_snapshot_link)


def _fresh_missing_from_snapshot(root: str) -> None:
    _fresh(root)
    _snapshot_only(root, _drop_snapshot_alerts)


def _retired_stale(root: str) -> None:
    alert_id = _post_handoff(root)
    # Retire before aging, for the same reason as the shared-key case: the
    # retirement is a mutation and would otherwise prune the signal first.
    bb.retire(root, alert_id, "fixture retirement")
    _age_handoff(root, OLD)


def _stale_plus_replay_key(root: str) -> None:
    _stale(root)
    _write_replay_only_key(root)


CASES = [
    ("empty_board_in_sync", True, _empty,
     "no snapshot, no log — both representations are empty"),
    ("fresh_handoff_in_sync", True, _fresh,
     "a fresh handoff is present in both representations"),
    ("stale_handoff_is_not_drift", True, _stale,
     "the replay pruned the expired signal; the snapshot still carries it"),
    ("stale_handoff_plus_shared_key_is_not_drift", True, _stale_plus_shared_key,
     "an expired signal beside a key both representations carry"),
    ("stale_handoff_plus_snapshot_key_is_drift", False, _stale_plus_snapshot_key,
     "a snapshot-only key survives TTL normalization and must still be drift"),
    ("stale_handoff_plus_snapshot_alert_is_drift", False,
     _stale_plus_snapshot_alert,
     "a snapshot-only fresh alert must still be drift"),
    ("fresh_handoff_plus_snapshot_key_is_drift", False, _fresh_plus_snapshot_key,
     "a fresh handoff does not excuse a snapshot-only key"),
    ("snapshot_stale_session_alert_is_drift", False,
     _snapshot_stale_session_alert,
     "the TTL governs handoffs only; an old session alert is still divergence"),
    ("snapshot_only_link_is_drift", False, _snapshot_link,
     "a snapshot-only graph edge is divergence"),
    ("fresh_handoff_missing_from_snapshot_is_drift", False,
     _fresh_missing_from_snapshot,
     "a fresh signal absent from the snapshot is divergence"),
    ("retired_stale_handoff_is_not_drift", True, _retired_stale,
     "retirement leaves the expired alert in the snapshot; both TTL-prune it"),
    ("stale_handoff_plus_replay_key_is_drift", False, _stale_plus_replay_key,
     "a replay-only key is divergence even beside an expired signal"),
]


def main() -> int:
    global _BROKEN
    ap = argparse.ArgumentParser()
    ap.add_argument("--broken", action="store_true",
                    help="report the pre-fix comparator (the rule-7 control)")
    ap.add_argument("--samples", default=None,
                    help="JSONL path for per-check evidence")
    args = ap.parse_args()
    _BROKEN = args.broken

    results = [_case(name, expected, setup, detail)
               for name, expected, setup, detail in CASES]

    x = sum(1 for r in results if r["ok"])
    n = len(results)
    if args.samples:
        path = pathlib.Path(args.samples)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for r in results:
                fh.write(json.dumps(r) + "\n")
    for r in results:
        print(f"[{'OK' if r['ok'] else 'MISS'}] {r['check']}: "
              f"expected={r['expected']} actual={r['actual']} — {r['detail']}")
    print(f"board_drift_classification_accuracy={x / n:.2f} ({x}/{n})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

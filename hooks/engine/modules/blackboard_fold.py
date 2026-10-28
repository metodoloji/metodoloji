"""Event-fold and bounded-plane helpers for the blackboard.

These functions operate on the canonical blackboard module's constants and
helpers through a late-bound module reference. That keeps replay behavior and
existing monkeypatchable public seams in one shared namespace.
"""

from __future__ import annotations

import time

from . import blackboard as _bb


def _apply_event(board: dict, ev: dict) -> None:
    kind = ev.get("event")
    if kind == "write":
        key = str(ev.get("key", ""))[:200]
        if key:
            board["keys"][key] = {
                "value": ev.get("value"),
                "type": ev.get("type", "note"),
                "updated": ev.get("ts", 0.0),
            }
            if ev.get("hot"):
                board["hot"] = key
            _bb._cap_keys(board)
    elif kind == "key-forget":
        # Retire one key (event-sourced): replay folds the removal, so a
        # rebuilt snapshot stays identical (no drift) — the cap is no longer
        # the only way a key leaves the board.
        _fkey = str(ev.get("key", ""))[:200]
        if _fkey:
            board["keys"].pop(_fkey, None)
    elif kind == "tool":
        # Audit tool-touch stamp (event-sourced): replay folds last_tool.*
        # back after a snapshot rebuild — no drift, unlike the old
        # snapshot-only fold.
        tool = str(ev.get("tool", ""))[:100]
        if tool:
            entry = {
                "value": str(ev.get("target", "")), "type": "tool",
                "updated": ev.get("ts", 0.0),
            }
            # PreToolUse/PostToolUse phase stamp rides the event (HIGH #7):
            # folded onto the key so the touch sequence survives replay.
            if ev.get("hook_event"):
                entry["hook_event"] = str(ev["hook_event"])[:40]
            board["keys"][f"last_tool.{tool}"] = entry
            _bb._cap_keys(board)
    elif kind == "hot":
        if ev.get("key"):
            board["hot"] = str(ev["key"])[:200]
        elif ev.get("clear"):
            board["hot"] = None
    elif kind == "tag":
        tag = str(ev.get("tag", ""))[:100]
        if tag and tag not in board["tags"]:
            board["tags"].append(tag)
            while len(board["tags"]) > _bb.MAX_TAGS:
                board["tags"].pop(0)
    elif kind == "untag":
        tag = str(ev.get("tag", ""))
        if tag in board["tags"]:
            board["tags"].remove(tag)
    elif kind == "contribute":
        board["contributions"].append({
            "who": str(ev.get("who", ""))[:200],
            "what": str(ev.get("what", ""))[:_bb.MAX_TEXT_LEN],
            "ts": ev.get("ts", 0.0),
        })
        while len(board["contributions"]) > _bb.MAX_CONTRIBUTIONS:
            board["contributions"].pop(0)
    elif kind == "watch":
        w = str(ev.get("watcher", ""))[:100]
        if w:
            board["watchers"][w] = ev.get("ts", 0.0)
            while len(board["watchers"]) > _bb.MAX_WATCHERS:
                oldest = min(board["watchers"], key=lambda k: board["watchers"][k])
                board["watchers"].pop(oldest)
    elif kind == "list_add":
        key = str(ev.get("key", ""))[:200]
        if key:
            entry = board["keys"].setdefault(
                key, {"value": [], "type": "list", "updated": 0.0})
            if not isinstance(entry.get("value"), list):
                entry["value"] = []
            entry["type"] = "list"
            entry["value"].append(str(ev.get("item", ""))[:_bb.MAX_LIST_ITEM_LEN])
            entry["updated"] = ev.get("ts", 0.0)
            while len(entry["value"]) > _bb.MAX_LIST_ITEMS:
                entry["value"].pop(0)
            _bb._cap_keys(board)
    elif kind == "list_remove":
        key = str(ev.get("key", ""))[:200]
        entry = board["keys"].get(key)
        if entry and isinstance(entry.get("value"), list):
            if ev.get("index") is not None:
                try:
                    entry["value"].pop(int(ev["index"]))
                except (IndexError, ValueError, TypeError):
                    pass
            elif ev.get("item") is not None:
                item = str(ev["item"])
                if item in entry["value"]:
                    entry["value"].remove(item)
            entry["updated"] = ev.get("ts", 0.0)
    elif kind == "list_clear":
        key = str(ev.get("key", ""))[:200]
        entry = board["keys"].get(key)
        if entry and isinstance(entry.get("value"), list):
            entry["value"] = []
            entry["updated"] = ev.get("ts", 0.0)
    elif kind == "link":
        a, b = str(ev.get("a", ""))[:200], str(ev.get("b", ""))[:200]
        rel = str(ev.get("relation", "related"))[:50]
        if a and b and not any(
                l["a"] == a and l["b"] == b and l["relation"] == rel
                for l in board["links"]):
            board["links"].append(
                {"a": a, "b": b, "relation": rel, "ts": ev.get("ts", 0.0)})
            while len(board["links"]) > _bb.MAX_LINKS:
                board["links"].pop(0)
    elif kind == "unlink":
        a, b = str(ev.get("a", "")), str(ev.get("b", ""))
        unlink_relation = ev.get("relation")
        board["links"] = [
            l for l in board["links"]
            if not (l["a"] == a and l["b"] == b
                    and (unlink_relation is None or l["relation"] == unlink_relation))]
    elif kind == "canvas_create":
        name = str(ev.get("name", ""))[:_bb.MAX_CANVAS_NAME]
        if name and name not in board["canvases"]:
            board["canvases"][name] = {
                "grid": ev.get("grid"), "cells": {}, "watch": [], "updated": ev.get("ts", 0.0)}
            if ev.get("focus"):
                board["hot_canvas"] = name
            _bb._cap_canvases(board)
    elif kind == "canvas_cell":
        cv = board["canvases"].get(str(ev.get("name", ""))[:_bb.MAX_CANVAS_NAME])
        if cv is not None:
            cell = str(ev.get("cell", ""))[:_bb.MAX_CELL_ID]
            if cell:
                cv["cells"][cell] = {
                    "content": str(ev.get("content", ""))[:_bb.MAX_CELL_LEN],
                    "kind": str(ev.get("kind", "note"))[:20],
                    "x": ev.get("x"), "y": ev.get("y"),
                    "updated": ev.get("ts", 0.0),
                }
                if cv["cells"][cell]["kind"] == "auto":
                    _bb._cap_auto_cells(cv)
                while len(cv["cells"]) > _bb.MAX_CELLS:
                    oldest = min(cv["cells"], key=lambda c: cv["cells"][c].get("updated", 0.0))
                    cv["cells"].pop(oldest)
                cv["updated"] = ev.get("ts", 0.0)
    elif kind == "canvas_remove":
        cv = board["canvases"].get(str(ev.get("name", ""))[:_bb.MAX_CANVAS_NAME])
        if cv is not None:
            cv["cells"].pop(str(ev.get("cell", ""))[:_bb.MAX_CELL_ID], None)
            cv["updated"] = ev.get("ts", 0.0)
    elif kind == "canvas_move":
        cv = board["canvases"].get(str(ev.get("name", ""))[:_bb.MAX_CANVAS_NAME])
        if cv is not None:
            src, dst = str(ev.get("cell", ""))[:_bb.MAX_CELL_ID], str(ev.get("to", ""))[:_bb.MAX_CELL_ID]
            if src in cv["cells"] and dst and dst != src:
                moved = dict(cv["cells"].pop(src))
                moved["updated"] = ev.get("ts", 0.0)
                if ev.get("x") is not None:
                    moved["x"] = ev.get("x")
                if ev.get("y") is not None:
                    moved["y"] = ev.get("y")
                cv["cells"][dst] = moved
                cv["updated"] = moved["updated"]
    elif kind == "canvas_resize":
        cv = board["canvases"].get(str(ev.get("name", ""))[:_bb.MAX_CANVAS_NAME])
        if cv is not None:
            grid = ev.get("grid")
            cv["grid"] = grid if isinstance(grid, list) and len(grid) == 2 else None
            cv["updated"] = ev.get("ts", 0.0)
    elif kind == "canvas_clear":
        cv = board["canvases"].get(str(ev.get("name", ""))[:_bb.MAX_CANVAS_NAME])
        if cv is not None:
            cv["cells"] = {}
            cv["updated"] = ev.get("ts", 0.0)
    elif kind == "canvas_focus":
        if ev.get("name"):
            board["hot_canvas"] = str(ev["name"])[:_bb.MAX_CANVAS_NAME]
        elif ev.get("clear"):
            board["hot_canvas"] = None
    elif kind == "canvas_watch":
        cv = board["canvases"].get(str(ev.get("name", ""))[:_bb.MAX_CANVAS_NAME])
        if cv is not None:
            path = str(ev.get("path", ""))[:300]
            if path and path not in cv["watch"]:
                cv["watch"].append(path)
                while len(cv["watch"]) > _bb.MAX_WATCH_PATHS:
                    cv["watch"].pop(0)
    elif kind == "canvas_unwatch":
        cv = board["canvases"].get(str(ev.get("name", ""))[:_bb.MAX_CANVAS_NAME])
        if cv is not None:
            path = str(ev.get("path", ""))
            if path in cv["watch"]:
                cv["watch"].remove(path)
    elif kind == "canvas_touch":
        cv = board["canvases"].get(str(ev.get("name", ""))[:_bb.MAX_CANVAS_NAME])
        if cv is not None:
            path = str(ev.get("path", ""))[:_bb.MAX_CELL_ID]
            if path:
                cv["cells"][path] = {
                    "content": str(ev.get("content", ""))[:_bb.MAX_CELL_LEN],
                    "kind": "auto",
                    "x": None, "y": None,
                    "updated": ev.get("ts", 0.0),
                }
                _bb._cap_auto_cells(cv)
                while len(cv["cells"]) > _bb.MAX_CELLS:
                    oldest = min(cv["cells"], key=lambda c: cv["cells"][c].get("updated", 0.0))
                    cv["cells"].pop(oldest)
                cv["updated"] = ev.get("ts", 0.0)
    elif kind == "subscribe":
        watcher = str(ev.get("watcher", ""))[:100]
        pattern = str(ev.get("pattern", ""))[:200]
        channel = str(ev.get("channel", "session"))[:_bb.MAX_CHANNEL_LEN]
        if watcher and pattern and not any(
                s["watcher"] == watcher and s["pattern"] == pattern
                for s in board["subscriptions"]):
            board["subscriptions"].append(
                {"watcher": watcher, "pattern": pattern, "channel": channel,
                 "ts": ev.get("ts", 0.0)})
            while len(board["subscriptions"]) > _bb.MAX_SUBSCRIPTIONS:
                board["subscriptions"].pop(0)
    elif kind == "unsubscribe":
        watcher, pattern = str(ev.get("watcher", "")), str(ev.get("pattern", ""))
        board["subscriptions"] = [
            s for s in board["subscriptions"]
            if not (s["watcher"] == watcher and s["pattern"] == pattern)]
    elif kind == "consume":
        chan = str(ev.get("channel", ""))
        # Fold the finished handshake into the board before dropping the
        # waiting signal, so the consumed tally lives in the snapshot too
        # (chain_health reads it there; no event-log replay). Deterministic
        # and order-preserving, so a snapshot rebuild derives the same tally.
        if chan.startswith("handoff."):
            _receiver = chan[len("handoff."):]
            _tally = board.get("consumed")
            if not isinstance(_tally, dict):
                _tally = board["consumed"] = {}
            # Attribution reads the open-handoff LEDGER, not the (TTL-pruned)
            # alert list: a handshake that completed days ago must still count
            # after a rebuild even though its waiting alert has aged out. The
            # ledger is event-sourced like every plane, so live and replay
            # derive the same tally.
            _ledger = board.get("open_handoffs")
            _open = _ledger.pop(_receiver, []) if isinstance(_ledger, dict) else []
            for _entry in _open:
                _sender = (_entry[0] if isinstance(_entry, (list, tuple))
                           and _entry else "unknown")
                _tally.setdefault(_sender, {})
                _tally[_sender][_receiver] = _tally[_sender].get(_receiver, 0) + 1
            _bb._cap_consumed(board)
        board["alerts"] = [a for a in board["alerts"] if a["channel"] != chan]
    elif kind == "alert":
        alert = {
            "id": ev.get("id", ""),
            "channel": str(ev.get("channel", "session"))[:_bb.MAX_CHANNEL_LEN],
            "kind": str(ev.get("kind", "info"))[:20],
            "text": str(ev.get("text", ""))[:_bb.MAX_ALERT_TEXT],
            "ts": ev.get("ts", 0.0),
        }
        # Persist the poster-attributed sender so snapshot readers keep the
        # shared-namespace attribution the event log carries (TD-014: the
        # diagnostic waiting set moved to the folded snapshot).
        if ev.get("sender") and _bb._SENDER_RE.match(str(ev.get("sender")).strip()):
            alert["sender"] = str(ev.get("sender")).strip()
        board["alerts"].append(alert)
        # Open-handoff ledger: the attribution record a later consume folds
        # into the persisted 'consumed' plane. Kept TTL-independent (only the
        # alerts list ages out) so a rebuild still counts old handshakes.
        if alert.get("kind") == "handoff":
            _chan = str(alert.get("channel", ""))
            if _chan.startswith("handoff."):
                _recv = _chan[len("handoff."):]
                _ledger = board.get("open_handoffs")
                if not isinstance(_ledger, dict):
                    _ledger = board["open_handoffs"] = {}
                _ledger.setdefault(_recv, []).append([
                    _bb._signal_sender(str(alert.get("text", "")),
                                   explicit=alert.get("sender")),
                    float(alert.get("ts", 0.0) or 0.0)])
                _bb._cap_open_handoffs(board)
        while len(board["alerts"]) > _bb.MAX_ALERTS:
            board["alerts"].pop(0)
        # NEW: Clean up stale handoff signals (CRITICAL #4)
        _bb._cleanup_stale_handoffs(board)
    elif kind == "alert-edit":
        # Correct an alert's text in place, by id (replace_handoff): the
        # signal never disappears — replay folds the edit onto the original
        # alert, preserving its id and original ts (age/staleness stays
        # truthful). Sender is refreshed when the edit carries one.
        target_id = str(ev.get("id", ""))
        if target_id:
            for a in board["alerts"]:
                if a.get("id") == target_id:
                    a["text"] = str(ev.get("text", ""))[:_bb.MAX_ALERT_TEXT]
                    if ev.get("sender") and _bb._SENDER_RE.match(str(ev.get("sender")).strip()):
                        a["sender"] = str(ev.get("sender")).strip()
                    break
    elif kind == "witness":
        # The claim's code-state anchor, written once by the write path. The
        # board stores the ANCHOR, never a verdict: freshness is recomputed on
        # every read (all a stored "fresh" flag could do is go stale silently).
        target = str(ev.get("target", ""))[:200]
        if target:
            board["witness"][target] = {
                "commit": str(ev.get("commit", "none"))[:64],
                "tree": str(ev.get("tree", "none"))[:32],
                "scope": str(ev.get("scope", "none"))[:32],
                "ts": float(ev.get("ts", 0.0) or 0.0),
            }
            _bb._cap_witness(board)
    elif kind == "retire":
        # "This no longer applies", event-sourced: the claim keeps its own ts
        # (age stays truthful) but stops being delivered as live work.
        target = str(ev.get("target", ""))[:200]
        if target:
            board["retired"][target] = {
                "reason": str(ev.get("reason", ""))[:_bb.MAX_ALERT_TEXT],
                "ts": float(ev.get("ts", 0.0) or 0.0),
            }
            _bb._cap_retired(board)


def _cap_witness(board: dict) -> None:
    """Bound the witness plane, oldest anchor first (deterministic: replay
    derives the same plane, so a rebuild cannot diverge from the live fold)."""
    plane = board.get("witness")
    if not isinstance(plane, dict):
        board["witness"] = {}
        return
    while len(plane) > _bb.MAX_WITNESSES:
        plane.pop(min(plane, key=lambda k: plane[k].get("ts", 0.0)))


def _cap_retired(board: dict) -> None:
    """Bound the retirement plane, oldest marker first."""
    plane = board.get("retired")
    if not isinstance(plane, dict):
        board["retired"] = {}
        return
    while len(plane) > _bb.MAX_RETIRED:
        plane.pop(min(plane, key=lambda k: plane[k].get("ts", 0.0)))


def _cap_auto_cells(cv: dict) -> None:
    autos = [c for c, v in cv["cells"].items() if v.get("kind") == "auto"]
    while len(autos) > _bb.MAX_AUTO_CELLS:
        oldest = min(autos, key=lambda c: cv["cells"][c].get("updated", 0.0))
        cv["cells"].pop(oldest)
        autos.remove(oldest)


def _cap_open_handoffs(board: dict) -> None:
    """Bound the open-handoff ledger (FIFO across receivers, insertion order).
    Deterministic, so live mutation and replay derive the same ledger."""
    ledger = board.get("open_handoffs")
    if not isinstance(ledger, dict):
        board["open_handoffs"] = {}
        return
    total = sum(len(v) for v in ledger.values() if isinstance(v, list))
    while total > _bb.MAX_OPEN_HANDOFFS:
        for _recv in list(ledger.keys()):
            _lst = ledger[_recv]
            if isinstance(_lst, list) and _lst:
                _lst.pop(0)
                total -= 1
                if not _lst:
                    ledger.pop(_recv, None)
                break
        else:
            break


def _cap_consumed(board: dict) -> None:
    """Bound the folded 'consumed' tally (distinct sender names). Counts are
    deterministic and order-preserving, so a rebuild derives the same plane."""
    tally = board.get("consumed")
    if not isinstance(tally, dict):
        board["consumed"] = {}
        return
    while len(tally) > _bb.MAX_CONSUMED_SENDERS:
        tally.pop(next(iter(tally)))


def _cleanup_stale_handoffs(board: dict) -> None:
    """Remove handoff signals older than TTL (24 hours).

    Prevents hung skills from blocking the chain indefinitely.
    Called during every mutation to keep stale signals from accumulating.
    (CRITICAL #4 / ISSUE #25: Skill timeout/crash handling)
    """
    now = time.time()
    initial_count = len(board["alerts"])
    board["alerts"] = [
        a for a in board["alerts"]
        if not (a.get("kind") == "handoff" and
                (now - (a.get("ts", 0.0))) > _bb.HANDOFF_SIGNAL_TTL_SECONDS)
    ]
    stale_removed = initial_count - len(board["alerts"])
    if stale_removed > 0:
        # Log that stale signals were cleaned up (for diagnostics)
        # Could write to a stale-signal counter if needed
        pass


def _cap_canvases(board: dict) -> None:
    while len(board["canvases"]) > _bb.MAX_CANVASES:
        protected = {board.get("hot_canvas")}
        candidates = [n for n in board["canvases"] if n not in protected]
        if not candidates:
            return  # never evict the focused canvas
        oldest = min(candidates, key=lambda n: board["canvases"][n].get("updated", 0.0))
        board["canvases"].pop(oldest)


def _cap_keys(board: dict) -> None:
    while len(board["keys"]) > _bb.MAX_KEYS:
        protected = set(_bb._BRIDGE_KEYS)
        if board.get("hot"):
            protected.add(board["hot"])
        oldest = min(
            (k for k in board["keys"] if k not in protected),
            key=lambda k: board["keys"][k].get("updated", 0.0),
            default=None,
        )
        if oldest is None:
            return  # everything left is protected — never evict the bridge/focus
        board["keys"].pop(oldest)


def _clear_hot_if_dangling(board: dict) -> None:
    """After a write flood, the hot key/canvas may have been expired in a
    previous snapshot generation while later 'hot' events re-set it. Never
    surface a hot key or canvas that is not on the board."""
    if board.get("hot") and board["hot"] not in board["keys"]:
        board["hot"] = None
    if board.get("hot_canvas") and board["hot_canvas"] not in board["canvases"]:
        board["hot_canvas"] = None

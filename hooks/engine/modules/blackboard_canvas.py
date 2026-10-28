"""Canvas state operations for the blackboard.

The public delegates remain in ``blackboard`` and bind its current helpers on
each call, retaining compatibility for consumers and focused test seams.
"""

from __future__ import annotations

def _parse_grid(spec) -> list | None:
    """Accept [w, h], "WxH" or None; returns a sane grid or None (free canvas)."""
    from . import blackboard as _board_api
    MAX_GRID = _board_api.MAX_GRID
    if spec is None:
        return None
    if isinstance(spec, str):
        try:
            w, h = spec.lower().split("x", 1)
            grid = [max(1, min(MAX_GRID, int(w))), max(1, min(MAX_GRID, int(h)))]
        except (ValueError, AttributeError):
            return None
    elif isinstance(spec, (list, tuple)) and len(spec) == 2:
        try:
            grid = [max(1, min(MAX_GRID, int(spec[0]))), max(1, min(MAX_GRID, int(spec[1])))]
        except (ValueError, TypeError):
            return None
    else:
        return None
    return grid

def canvas_create(project_root: str, name: str, *, grid=None,
                  focus: bool = False) -> dict:
    """Create a new canvas or retrieve existing.

    NEW: Bounds enforcement visible - checks MAX_CANVASES limit (MEDIUM #4 / ISSUE #63)
    Oldest canvases are expired when limit is reached (except hot_canvas).
    """
    from . import blackboard as _board_api
    MAX_CANVASES = _board_api.MAX_CANVASES
    MAX_CANVAS_NAME = _board_api.MAX_CANVAS_NAME
    _append_event = _board_api._append_event
    _apply_event = _board_api._apply_event
    _clear_hot_if_dangling = _board_api._clear_hot_if_dangling
    _mutate = _board_api._mutate
    _parse_grid = _board_api._parse_grid
    board_paths = _board_api.board_paths
    sys = _board_api.sys
    time = _board_api.time
    name = str(name).strip()[:MAX_CANVAS_NAME]
    if not name:
        return {"ok": False, "error": "empty canvas name"}
    event = {"event": "canvas_create", "name": name, "grid": _parse_grid(grid),
             "focus": bool(focus), "ts": time.time()}

    def mut(board):
        existed = name in board["canvases"]
        paths = board_paths(project_root)
        _append_event(paths, event)
        _apply_event(board, event)

        # NEW: Explicit bounds check - enforce MAX_CANVASES (MEDIUM #4 / ISSUE #63)
        canvas_count = len(board["canvases"])
        if canvas_count > MAX_CANVASES:
            sys.stderr.write(f"metodoloji: bounds enforced — canvases {canvas_count}/{MAX_CANVASES}, "
                           f"oldest expired\n")

        # Graph-plane derived edge (same scope, no nesting): a canvas focused
        # at creation sits under a run — bind it to the focused run key with
        # the `owns` relation so the graph plane carries the canvas↔run wiring
        # neighbors() reads. One edge per canvas: an owner re-run under a new
        # run key first unlinks the stale edge (event-sourced), then links the
        # new owner; the same owner re-linking is a fold-dedup no-op. An
        # unnamed run leaves no edge. The canvas namespace cannot collide with
        # key namespaces (`canvas:` prefix on the graph), so the edge can
        # never alias a run key.
        if event["focus"]:
            owner_key = board.get("hot")
            if owner_key and owner_key != name:
                canvas_node = f"canvas:{name}"
                for edge in [l for l in board["links"]
                             if l["relation"] == "owns" and l["b"] == canvas_node
                             and l["a"] != owner_key]:
                    _append_event(paths, {"event": "unlink", "a": edge["a"],
                                          "b": canvas_node, "relation": "owns",
                                          "ts": event["ts"]})
                    _apply_event(board, {"event": "unlink", "a": edge["a"],
                                         "b": canvas_node, "relation": "owns"})
                if not any(l["a"] == owner_key and l["b"] == canvas_node
                           and l["relation"] == "owns" for l in board["links"]):
                    link_event = {"event": "link", "a": owner_key, "b": canvas_node,
                                  "relation": "owns", "ts": event["ts"]}
                    _append_event(paths, link_event)
                    _apply_event(board, link_event)
        _clear_hot_if_dangling(board)
        return board, {"ok": True, "canvas": name, "grid": event["grid"],
                       "existed": existed, "focused": board.get("hot_canvas") == name}

    return _mutate(project_root, mut)

def canvas_set(project_root: str, name: str, cell: str, content: str, *,
               kind: str = "note", x: int | None = None,
               y: int | None = None) -> dict:
    """Write one cell in real time — the board's live mutation primitive."""
    from . import blackboard as _board_api
    MAX_CANVAS_NAME = _board_api.MAX_CANVAS_NAME
    MAX_CELL_ID = _board_api.MAX_CELL_ID
    MAX_CELL_LEN = _board_api.MAX_CELL_LEN
    _append_event = _board_api._append_event
    _apply_event = _board_api._apply_event
    _mutate = _board_api._mutate
    _route_alerts = _board_api._route_alerts
    board_paths = _board_api.board_paths
    time = _board_api.time
    name = str(name).strip()[:MAX_CANVAS_NAME]
    cell = str(cell).strip()[:MAX_CELL_ID]
    if not name or not cell:
        return {"ok": False, "error": "canvas-set needs --name and --cell"}
    event = {"event": "canvas_cell", "name": name, "cell": cell,
             "content": str(content)[:MAX_CELL_LEN], "kind": str(kind).strip()[:20] or "note",
             "x": x, "y": y, "ts": time.time()}

    def mut(board):
        paths = board_paths(project_root)
        _append_event(paths, event)
        _apply_event(board, event)
        _route_alerts(paths, board, f"canvas:{name}", "canvas",
                      f"canvas '{name}' cell '{cell}' set ({event['kind']})")
        return board, {"ok": True, "canvas": name, "cell": cell,
                       "cells": len(board["canvases"].get(name, {}).get("cells", {}))}

    return _mutate(project_root, mut)

def canvas_remove(project_root: str, name: str, cell: str) -> dict:
    from . import blackboard as _board_api
    MAX_CANVAS_NAME = _board_api.MAX_CANVAS_NAME
    MAX_CELL_ID = _board_api.MAX_CELL_ID
    _append_event = _board_api._append_event
    _apply_event = _board_api._apply_event
    _mutate = _board_api._mutate
    _route_alerts = _board_api._route_alerts
    board_paths = _board_api.board_paths
    time = _board_api.time
    event = {"event": "canvas_remove", "name": str(name).strip()[:MAX_CANVAS_NAME],
             "cell": str(cell).strip()[:MAX_CELL_ID], "ts": time.time()}

    def mut(board):
        paths = board_paths(project_root)
        _append_event(paths, event)
        _apply_event(board, event)
        _route_alerts(paths, board, f"canvas:{name}", "canvas",
                      f"canvas '{name}' cell '{event['cell']}' removed")
        return board, {"ok": True, "canvas": event["name"], "cell": event["cell"]}

    return _mutate(project_root, mut)

def canvas_move(project_root: str, name: str, cell: str, to: str, *,
                x: int | None = None, y: int | None = None) -> dict:
    """Rename/reposition a cell (moves content, kind and watch state)."""
    from . import blackboard as _board_api
    MAX_CANVAS_NAME = _board_api.MAX_CANVAS_NAME
    MAX_CELL_ID = _board_api.MAX_CELL_ID
    _append_event = _board_api._append_event
    _apply_event = _board_api._apply_event
    _mutate = _board_api._mutate
    board_paths = _board_api.board_paths
    time = _board_api.time
    event = {"event": "canvas_move", "name": str(name).strip()[:MAX_CANVAS_NAME],
             "cell": str(cell).strip()[:MAX_CELL_ID], "to": str(to).strip()[:MAX_CELL_ID],
             "x": x, "y": y, "ts": time.time()}
    if not event["name"] or not event["cell"] or not event["to"]:
        return {"ok": False, "error": "canvas-move needs --name, --cell and --to"}

    def mut(board):
        cv = board["canvases"].get(event["name"])
        moved = event["cell"] in (cv or {}).get("cells", {})
        _append_event(board_paths(project_root), event)
        _apply_event(board, event)
        return board, {"ok": True, "canvas": event["name"], "from": event["cell"],
                       "to": event["to"], "moved": moved}

    return _mutate(project_root, mut)

def canvas_resize(project_root: str, name: str, grid) -> dict:
    from . import blackboard as _board_api
    MAX_CANVAS_NAME = _board_api.MAX_CANVAS_NAME
    _append_event = _board_api._append_event
    _apply_event = _board_api._apply_event
    _mutate = _board_api._mutate
    _parse_grid = _board_api._parse_grid
    board_paths = _board_api.board_paths
    time = _board_api.time
    event = {"event": "canvas_resize", "name": str(name).strip()[:MAX_CANVAS_NAME],
             "grid": _parse_grid(grid), "ts": time.time()}

    def mut(board):
        _append_event(board_paths(project_root), event)
        _apply_event(board, event)
        return board, {"ok": True, "canvas": event["name"], "grid": event["grid"]}

    return _mutate(project_root, mut)

def canvas_clear(project_root: str, name: str) -> dict:
    from . import blackboard as _board_api
    MAX_CANVAS_NAME = _board_api.MAX_CANVAS_NAME
    _append_event = _board_api._append_event
    _apply_event = _board_api._apply_event
    _mutate = _board_api._mutate
    board_paths = _board_api.board_paths
    time = _board_api.time
    event = {"event": "canvas_clear", "name": str(name).strip()[:MAX_CANVAS_NAME],
             "ts": time.time()}

    def mut(board):
        cv = board["canvases"].get(event["name"])
        had = len((cv or {}).get("cells", {}))
        _append_event(board_paths(project_root), event)
        _apply_event(board, event)
        return board, {"ok": True, "canvas": event["name"], "cleared": had}

    return _mutate(project_root, mut)

def canvas_focus(project_root: str, name: str | None) -> dict:
    """Focus one canvas (the engine surfaces it) or clear (name=None)."""
    from . import blackboard as _board_api
    MAX_CANVAS_NAME = _board_api.MAX_CANVAS_NAME
    _append_event = _board_api._append_event
    _apply_event = _board_api._apply_event
    _clear_hot_if_dangling = _board_api._clear_hot_if_dangling
    _mutate = _board_api._mutate
    board_paths = _board_api.board_paths
    time = _board_api.time
    if name:
        event = {"event": "canvas_focus", "name": str(name).strip()[:MAX_CANVAS_NAME],
                 "ts": time.time()}
    else:
        event = {"event": "canvas_focus", "clear": True, "ts": time.time()}

    def mut(board):
        _append_event(board_paths(project_root), event)
        _apply_event(board, event)
        _clear_hot_if_dangling(board)
        return board, {"ok": True, "hot_canvas": board["hot_canvas"]}

    return _mutate(project_root, mut)

def canvas_watch(project_root: str, name: str, path: str, *, remove: bool = False) -> dict:
    """Register a filesystem path prefix that feeds this canvas in real time:
    every touch reported under the path lands as an auto cell.

    The feed is writer-driven (see canvas_touch): the engine hot path is
    blackboard-free, so a producer reports its own touches instead of the
    PostToolUse hook pushing them."""
    from . import blackboard as _board_api
    MAX_CANVAS_NAME = _board_api.MAX_CANVAS_NAME
    _append_event = _board_api._append_event
    _apply_event = _board_api._apply_event
    _mutate = _board_api._mutate
    board_paths = _board_api.board_paths
    time = _board_api.time
    event = {"event": "canvas_unwatch" if remove else "canvas_watch",
             "name": str(name).strip()[:MAX_CANVAS_NAME],
             "path": str(path).strip()[:300], "ts": time.time()}
    if not event["name"] or not event["path"]:
        return {"ok": False, "error": "canvas-watch needs --name and --path"}

    def mut(board):
        _append_event(board_paths(project_root), event)
        _apply_event(board, event)
        cv = board["canvases"].get(event["name"], {})
        return board, {"ok": True, "canvas": event["name"],
                       "watch": list(cv.get("watch", []))}

    return _mutate(project_root, mut)

def read_canvas(project_root: str, name: str) -> dict:
    """Read one canvas whole (cells + watch paths + grid)."""
    from . import blackboard as _board_api
    MAX_CANVAS_NAME = _board_api.MAX_CANVAS_NAME
    read_board = _board_api.read_board
    name = str(name).strip()[:MAX_CANVAS_NAME]
    board = read_board(project_root)
    cv = board["canvases"].get(name)
    if cv is None:
        return {"ok": True, "canvas": name, "exists": False, "cells": {}}
    return {"ok": True, "canvas": name, "exists": True, "grid": cv.get("grid"),
            "cells": cv.get("cells", {}), "watch": cv.get("watch", []),
            "focused": board.get("hot_canvas") == name}

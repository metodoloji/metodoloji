"""Archive and rotate the blackboard's append-only event log."""

from __future__ import annotations

import json
import pathlib


def rotate_event_log(project_root: str, max_lines: int = 10000) -> dict:
    """Archive old event entries and start a fresh log once the threshold is met."""
    from . import blackboard

    paths = blackboard.board_paths(project_root)
    events_path = pathlib.Path(paths["events"])
    # No board at all: return before the lock, which opens the SQLite file with
    # create=True and would leave a database in a project that has none.
    if not any(pathlib.Path(paths[k]).is_file() for k in ("db", "snapshot", "events")):
        return {"ok": False, "error": "no event log to rotate"}

    try:
        with blackboard._board_lock(paths):
            # Read, archive and reset under ONE lock: an append between the
            # read and the clear would be archived nowhere and then deleted.
            # `_read_snapshot` first performs the one-time JSON import, so a
            # legacy board archives the events it actually has (and the reset
            # then clears the authoritative store, not a stale migration file).
            blackboard._read_snapshot(paths)
            all_events, _garbage = blackboard.event_stats(paths)

            if not all_events or len(all_events) < max_lines:
                return {
                    "ok": False,
                    "message": f"log has {len(all_events)} events (threshold: {max_lines})",
                }

            import datetime
            import gzip

            now = datetime.datetime.now()
            archive_name = f"events-{now.strftime('%Y-%m-%d-%H%M%S')}.log.gz"
            archive_path = events_path.parent / archive_name
            archive_path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(archive_path, "wt", encoding="utf-8") as stream:
                for event in all_events:
                    stream.write(json.dumps(event, ensure_ascii=False) + "\n")

            # Every mutation already wrote the snapshot as the fold of the full
            # history, so the reset drops no state — only audit trail, which the
            # archive now holds. (The old code folded the stream and DISCARDED
            # the result; folding a capped window would have REGRESSED the
            # snapshot instead — E-125.)
            blackboard.clear_events(paths)

        return {
            "ok": True,
            "archived_to": str(archive_path),
            "lines_archived": len(all_events),
            "message": f"Archived {len(all_events)} events to {archive_name}; event log rotated",
        }
    except Exception as exc:
        return {"ok": False, "error": f"rotation failed: {str(exc)[:200]}"}

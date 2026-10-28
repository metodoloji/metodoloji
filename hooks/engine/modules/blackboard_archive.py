"""Archive and rotate the blackboard's append-only event log."""

from __future__ import annotations

import json
import pathlib


def rotate_event_log(project_root: str, max_lines: int = 10000) -> dict:
    """Archive old event entries and start a fresh log once the threshold is met."""
    from . import blackboard

    paths = blackboard.board_paths(project_root)
    events_path = pathlib.Path(paths["events"])
    if not events_path.exists():
        return {"ok": False, "error": "no event log to rotate"}

    try:
        all_events = []
        with open(events_path, "r", encoding="utf-8") as stream:
            for line in stream:
                line = line.strip()
                if not line:
                    continue
                try:
                    all_events.append(json.loads(line))
                except (json.JSONDecodeError, ValueError):
                    pass  # Skip corrupted lines, matching the reader's recovery policy.

        if not all_events or len(all_events) < max_lines:
            return {
                "ok": False,
                "message": f"log has {len(all_events)} lines (threshold: {max_lines})",
            }

        # Rebuild before archiving so the snapshot represents the full event
        # history even though the active log is about to be reset.
        blackboard._replay_events(paths)

        import datetime
        import gzip

        now = datetime.datetime.now()
        archive_name = f"events-{now.strftime('%Y-%m-%d-%H%M%S')}.log.gz"
        archive_path = events_path.parent / archive_name
        with gzip.open(archive_path, "wt", encoding="utf-8") as stream:
            for event in all_events:
                stream.write(json.dumps(event, ensure_ascii=False) + "\n")

        with open(events_path, "w", encoding="utf-8"):
            pass

        return {
            "ok": True,
            "archived_to": str(archive_path),
            "lines_archived": len(all_events),
            "message": f"Archived {len(all_events)} events to {archive_name}; event log rotated",
        }
    except Exception as exc:
        return {"ok": False, "error": f"rotation failed: {str(exc)[:200]}"}

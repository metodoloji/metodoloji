"""Blackboard — dynamic, event-sourced working context for the methodology.

One JSON snapshot (``.metodoloji/blackboard.json``) plus an append-only event
log (``.metodoloji/logs/blackboard-events.log``). Every mutation is written as
an event first; the snapshot is a cache rebuilt from events when missing or
corrupt. All file operations are atomic (temp file + rename) and guarded by an
exclusive lock file so concurrent hook processes never interleave.

The board is a project-wide network, not a flat notebook:
- text keys  — single values (notes/decisions/state)
- lists      — ordered items under one key
- canvases   — dynamic surfaces (grid or free) whose cells any project actor
               mutates in real time; a canvas may watch filesystem paths so
               engine hooks push touches into it as they happen
- links      — a graph between any nodes (keys or canvases) with relations;
               mutations ripple alerts along links
- subscriptions — watchers route matching mutations into alert channels
- alerts     — bounded, channel-addressed notifications consumed by the
               engine (session_start injects, stop surfaces pending counts)
- witness    — one code-state anchor per claim (alert id / key): the commit,
               dirty-tree digest and scope digest the claim was true against.
               Written by the write path, never by a reader; the freshness
               VERDICT is derived from it (modules/freshness.py), never stored
- retired    — claims explicitly withdrawn ("this no longer applies"): the
               first-class sibling of consume/forget, keeping the original ts
               (age stays honest) while changing what the claim MEANS

Design invariants (this module is the canonical canvas home — the old canvas
doc was pruned; the code is the contract):
- Read/report APIs fail open to safe defaults; mutation storage failures surface
  to callers and must never be mistaken for successful writes.
- Bounded: keys/lists/canvases/cells/links/alerts/events expire oldest-first.
- No auto content generation: the board only holds what a writer (or an
  explicit watch registration) put there.
"""

from __future__ import annotations

import fnmatch
import json
import os
import pathlib
import re
import sys
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from enum import Enum  # NEW: For AlertKind (PHASE 4 #7)
from typing import Any, TypeVar

from . import freshness as fr

# NEW: Structured alert taxonomy (PHASE 4 #7)
class AlertKind(Enum):
    """Enumeration of valid alert kinds for type-safe alert posting.

    Not a decorative list: `post_alert` validates every kind against it, and
    each kind ANNOTATED "written by" is WRITTEN through this enum by that
    module (`retire`/`write_key` → RETIRED/CASCADE_INVALIDATION,
    `doctor --post` → WITNESS_STALE/STALE_SESSION), so a rename here is a
    rename at the writer too — the failure E-053 F-06 measured was a taxonomy
    no code referenced. `bench_consistency.py` derives that annotation set and
    fails if an annotated kind has no writer; the remaining members are the
    operator vocabulary of `notify --kind`, validated by `post_alert` below.
    """
    INFO = "info"
    WARN = "warn"
    ERROR = "error"
    RISK = "risk"
    HANDOFF = "handoff"
    CASCADE_INVALIDATION = "cascade_invalidation"   # written by blackboard.py
    STALE_SESSION = "stale_session"                 # written by blackboard.py
    VERIFICATION_FAILURE = "verification_failure"
    HOOK_VIOLATION = "hook_violation"
    ESCALATION = "escalation"
    RETIRED = "retired"                             # written by blackboard.py
    WITNESS_STALE = "witness_stale"                 # written by blackboard.py
    
    def __str__(self):
        return self.value

# --- Limits (oldest expire first) -------------------------------------------
MAX_KEYS = 128
MAX_TAGS = 32
MAX_CONTRIBUTIONS = 64
MAX_EVENTS = 10_000
MAX_VALUE_LEN = 4_000
MAX_TEXT_LEN = 500

MAX_LIST_ITEMS = 100          # per list key
MAX_LIST_ITEM_LEN = 300       # per item

MAX_CANVASES = 8              # canvases on the board
MAX_CELLS = 256               # cells per canvas
MAX_AUTO_CELLS = 32           # auto (watch-fed) cells per canvas
MAX_CANVAS_NAME = 80
MAX_CELL_ID = 60
MAX_CELL_LEN = 500
MAX_WATCH_PATHS = 4           # watched paths per canvas
MAX_GRID = 64                 # max grid dimension (w or h)
MAX_CHANNEL_LEN = 80          # alert/subscription channel name; handoff channels
                              # carry `handoff.<skill>` and the longest skill name
                              # is 35 chars (bmad-check-implementation-readiness),
                              # so the generic 40-char cap silently truncated those
                              # signals out of their own receiver's reach

MAX_LINKS = 128
MAX_SUBSCRIPTIONS = 16
MAX_ALERTS = 32               # global, oldest expire
MAX_ALERT_TEXT = 200
MAX_CONSUMED_SENDERS = 256    # distinct sender names in the folded 'consumed' tally
MAX_OPEN_HANDOFFS = 512       # entries in the folded open-handoff attribution ledger
MAX_WITNESSES = 512           # code-state anchors (claims share a target's slot)
MAX_RETIRED = 256             # explicit retirements (oldest retire marker expires)

# Handoff signal TTL (24 hours) and stale cleanup (CRITICAL #4 / ISSUE #25)
HANDOFF_SIGNAL_TTL_SECONDS = 24 * 3600

# Watcher stamps recorded by the engine, bounded.
MAX_WATCHERS = 16

class BoardError(Exception):
    """Raised when a supported cross-process board lock cannot be acquired."""


_LOCK_FAILED = object()
_MutationResult = TypeVar("_MutationResult")

# --- Locking -----------------------------------------------------------------
def _acquire_lock(lock_path: str):
    """Acquire an exclusive advisory lock.

    Returns None only when this platform has no supported lock primitive, and
    ``_LOCK_FAILED`` when a supported lock could not be acquired. Callers must
    never treat lock failure as permission to perform an unlocked mutation.

    Windows: msvcrt.locking is byte-range based and non-blocking LK_NBLCK
    raises on contention — retry a bounded number of times with exponential backoff.
    If the lock still cannot be acquired, the caller fails safely: reads return
    an empty board and mutations surface the lock error. A unique temp filename
    protects atomic replacement, but cannot protect an unlocked read-modify-write.
    (HIGH #2 / ISSUE #6: Windows lock retry with exponential backoff)
    """
    try:
        f = open(lock_path, "a+")
    except OSError:
        return _LOCK_FAILED
    try:
        import fcntl  # POSIX
        fcntl.flock(f, fcntl.LOCK_EX)  # type: ignore[attr-defined]
        return f
    except ImportError:
        pass
    except OSError:
        f.close()
        return _LOCK_FAILED
    try:
        import msvcrt  # Windows
    except ImportError:
        f.close()
        return None
    f.seek(0)
    
    # NEW: Exponential backoff strategy (HIGH #2)
    # Retry with exponential backoff: 10ms, 50ms, 100ms, then steady 100ms
    base_backoff = 0.01  # 10ms
    max_backoff = 0.1    # 100ms
    max_retries = 100    # ~10 seconds total with exponential backoff
    
    for attempt in range(max_retries):
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            return f  # Lock acquired
        except OSError:
            # Lock contention — backoff and retry
            if attempt < max_retries - 1:
                # Exponential backoff: 10ms * 2^attempt, capped at max_backoff
                sleep_time = min(base_backoff * (2 ** attempt), max_backoff)
                time.sleep(sleep_time)
    # The lock primitive exists, but contention did not clear within the bound.
    
    # A unique temp filename makes atomic replacement safe, but cannot make an
    # unlocked read-modify-write safe; signal failure to the caller instead.
    f.close()
    return _LOCK_FAILED

def _release_lock(f) -> None:
    if f is None:
        return
    try:
        if os.name == "nt":
            try:
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            except (ImportError, OSError):
                pass
        else:
            try:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_UN)  # type: ignore[attr-defined]
            except (ImportError, OSError):
                pass
    except Exception:
        pass
    finally:
        try:
            f.close()
        except OSError:
            pass

# In-process serialization: one threading.Lock per board path. Windows
# msvcrt byte-range locks proved unreliable as a same-process thread mutex
# (transient double-acquire), so threads serialize here first, and the OS
# lock only guards cross-process races.
_THREAD_LOCKS: dict = {}
_THREAD_LOCKS_GUARD = threading.Lock()

def _thread_lock(lock_path: str) -> threading.Lock:
    with _THREAD_LOCKS_GUARD:
        if lock_path not in _THREAD_LOCKS:
            _THREAD_LOCKS[lock_path] = threading.Lock()
        return _THREAD_LOCKS[lock_path]

@contextmanager
def _board_lock(paths: dict):
    """Hold an exclusive advisory lock for the duration of the block."""
    tl = _thread_lock(paths["lock"])
    with tl:
        f = _acquire_lock(paths["lock"])
        if f is _LOCK_FAILED:
            raise BoardError("could not acquire blackboard lock")
        try:
            yield
        finally:
            _release_lock(f)

# --- Paths -------------------------------------------------------------------
def board_paths(project_root: str) -> dict:
    root = os.path.abspath(project_root or os.getcwd())
    bb_dir = os.path.join(root, ".metodoloji")
    return {
        "root": root,
        "dir": bb_dir,
        "snapshot": os.path.join(bb_dir, "blackboard.json"),
        "events": os.path.join(bb_dir, "logs", "blackboard-events.log"),
        "lock": os.path.join(bb_dir, "blackboard.json.lock"),
    }

# --- Snapshot handling --------------------------------------------------------
def _empty_board() -> dict:
    # v4 adds the freshness planes (witness / retired). Both are empty by
    # default, so a v3 snapshot upgrades in place — no replay is needed and no
    # old reader can be surprised by a populated plane it does not know.
    return {"version": 4, "hot": None, "hot_canvas": None, "keys": {},
            "tags": [], "contributions": [], "links": [], "canvases": {},
            "subscriptions": [], "alerts": [], "watchers": {},
            "consumed": {}, "open_handoffs": {}, "witness": {},
            "retired": {},
            "updated": 0.0}

def _read_snapshot(paths: dict) -> dict:
    try:
        with open(paths["snapshot"], encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return _empty_board()
    except (OSError, ValueError):
        return _empty_board()
    board = _empty_board()
    board.update({k: v for k, v in data.items() if k in board})
    if not isinstance(board.get("consumed"), dict):
        board["consumed"] = {}
    if not isinstance(board.get("open_handoffs"), dict):
        board["open_handoffs"] = {}
    if not isinstance(board.get("witness"), dict):
        board["witness"] = {}
    if not isinstance(board.get("retired"), dict):
        board["retired"] = {}
    # v1 snapshots had no graph sections — defaults already cover them.
    if not isinstance(board.get("links"), list):
        board["links"] = []
    if not isinstance(board.get("canvases"), dict):
        board["canvases"] = {}
    if not isinstance(board.get("subscriptions"), list):
        board["subscriptions"] = []
    if not isinstance(board.get("alerts"), list):
        board["alerts"] = []
    return board

def _write_snapshot_atomic(paths: dict, board: dict) -> None:
    import tempfile
    os.makedirs(paths["dir"], exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                       dir=paths["dir"],
                                       prefix=os.path.basename(paths["snapshot"]) + ".",
                                       delete=False) as f:
        tmp = f.name
        json.dump(board, f, ensure_ascii=False, separators=(",", ":"))
        f.flush()
        os.fsync(f.fileno())
    _replace_atomic(tmp, paths["snapshot"])

def _replace_atomic(src: str, dst: str, attempts: int = 5) -> None:
    """os.replace with a bounded retry: Windows can transiently deny the
    rename (WinError 32) when an indexer/AV briefly holds the target."""
    for attempt in range(attempts):
        try:
            os.replace(src, dst)
            return
        except FileNotFoundError:
            # src already consumed by this same call's earlier successful
            # replace (retry-after-rename) — the dst now exists or will.
            if os.path.exists(dst):
                return
            raise
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.02 * (attempt + 1))

# --- Event log ----------------------------------------------------------------
def _append_event(paths: dict, event: dict) -> None:
    """Append one JSON line. Called under the board lock from mutations."""
    os.makedirs(os.path.dirname(paths["events"]), exist_ok=True)
    with open(paths["events"], "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")

def _truncate_events_if_needed(paths: dict) -> None:
    """Bound the event log. Runs under the board lock (from _mutate); only
    rewrites the file when it actually exceeds the cap, so the common path
    never races with concurrent appends."""
    try:
        with open(paths["events"], "rb") as f:
            lines = f.readlines()
        if len(lines) <= MAX_EVENTS:
            return
        keep = lines[-MAX_EVENTS:]
        tmp = paths["events"] + f".{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp, "wb") as f:
            f.writelines(keep)
            f.flush()
            os.fsync(f.fileno())
        _replace_atomic(tmp, paths["events"])
    except OSError:
        pass

# --- Public API ----------------------------------------------------------------
def _read_board_unchecked(project_root: str) -> dict:
    """Load and migrate the board; the public wrapper handles read failures."""
    paths = board_paths(project_root)
    with _board_lock(paths):
        board = _read_snapshot(paths)
        # Rebuild from event log when the snapshot is empty but events exist.
        if not any((board["keys"], board["tags"], board["contributions"],
                    board["canvases"], board["links"], board["alerts"],
                    board["subscriptions"], board["consumed"],
                    board["open_handoffs"])):
            replay = _replay_events(paths)
            if any((replay["keys"], replay["tags"], replay["contributions"],
                    replay["canvases"], replay["links"], replay["alerts"],
                    replay["subscriptions"], replay["consumed"],
                    replay["open_handoffs"])):
                _write_snapshot_atomic(paths, replay)
                board = replay
        # One-time migration (v2 -> v3): the folded board gained a persisted
        # 'consumed' plane so chain_health can read a single source. Rebuild
        # once from the event log to derive it from existing history (only
        # when the log actually has something to fold — never wipe a board
        # whose log was lost).
        if board.get("version", 2) < 3:
            replay = _replay_events(paths)
            if any((replay["keys"], replay["tags"], replay["contributions"],
                    replay["canvases"], replay["links"], replay["alerts"],
                    replay["subscriptions"])) or replay.get("consumed"):
                _write_snapshot_atomic(paths, replay)
                board = replay
        # v3 -> v4: the freshness planes default to empty and are event-sourced
        # like every other plane, so the version marker needs no rebuild.
        if board.get("version", 2) < 4:
            board["version"] = 4
    if board.get("hot") and board["hot"] not in board["keys"]:
        board["hot"] = None
    if board.get("hot_canvas") and board["hot_canvas"] not in board["canvases"]:
        board["hot_canvas"] = None
    return board


def read_board(project_root: str) -> dict:
    """Load the board, returning an empty safe state when reading fails."""
    try:
        return _read_board_unchecked(project_root)
    except Exception:
        return _empty_board()

def _replay_events(paths: dict) -> dict:
    """Fold the event log into a board (used to rebuild a lost snapshot).
    
    Skips malformed JSON lines gracefully. (HIGH #1 / ISSUE #45) An
    undecodable log (not valid UTF-8) folds to an empty board — same policy
    as the malformed-line skip and as `_read_snapshot`'s fail-open, and the
    same rule E-009 set for the workflow store: the caller must never see a
    traceback on a file another tool could have damaged (E-017).
    """
    board = _empty_board()
    skipped_lines = 0
    try:
        with open(paths["events"], encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    # NEW: Skip malformed JSON but log for diagnostics
                    skipped_lines += 1
                    # Could emit warning, but silently skip for now (fail-open)
                    # In production, operator would see these in doctor output
                    continue
                except Exception:
                    # Catch other errors (e.g., encoding, memory) and skip
                    skipped_lines += 1
                    continue
                
                try:
                    _apply_event(board, ev)
                except Exception:
                    # NEW: If applying an event crashes, skip it but keep going
                    # (HIGH #1: Corruption recovery - keep as much state as possible)
                    skipped_lines += 1
                    continue
    except (OSError, UnicodeDecodeError):
        # A missing log is empty (OSError); a log that is not valid UTF-8 is
        # undecodable, so it too folds to an empty board instead of raising.
        # Discard any prefix already folded: returning a partial replay would
        # make recovery depend on where corruption happened in the file.
        board = _empty_board()
        skipped_lines = 0
    
    # Store skipped line count for diagnostics (can be exposed in doctor())
    # For now, it's available but not used in output
    if skipped_lines > 0:
        board["_metadata"] = board.get("_metadata", {})
        board["_metadata"]["replay_skipped_lines"] = skipped_lines
    
    return board

# Event-fold helpers live in blackboard_fold.py; their names are bound after
# this module finishes loading, when the canonical constants are available.

# Focus keys that must survive key-cap eviction: they carry the session
# scope/status the hook engine and bmad-help route on. A busy board
# must never evict the very keys the methodology is steering from.
# methodology.last_* keys are also protected: they mirror the E→IR→SP→S→QR→PR
# chain position (write_key stamps them from each stage's run key).
# (The bridge.last_* entries were ghosts — no reader or writer ever existed —
# and are removed; re-add a key here only when both sides of its contract ship.)
_BRIDGE_KEYS = frozenset({
    "scope", "status",
    "methodology.last_experiment", "methodology.last_ir", "methodology.last_sp",
    "methodology.last_story", "methodology.last_qr", "methodology.last_pr"
})
# A methodology stage's run-key prefix → the methodology.last_* mirror key
# write_key stamps from it (the only writers: each close-out writes its run
# key, e.g. `write --key E-001 --value "APPROVED …"` mirrors
# methodology.last_experiment; the S stage's bridge close-out writes
# `story.{story_key}`, which mirrors methodology.last_story). Read back by
# compact_context() in chain order.
_METHODOLOGY_MIRROR_KEYS = (
    ("E-", "methodology.last_experiment"),
    ("IR-", "methodology.last_ir"),
    ("SP-", "methodology.last_sp"),
    ("S-", "methodology.last_story"),
    ("story.", "methodology.last_story"),
    ("QR-", "methodology.last_qr"),
    ("PR-", "methodology.last_pr"),
)

# --- alert routing ---------------------------------------------------------------
def _route_alerts(paths: dict, board: dict, match: str, kind: str, text: str) -> None:
    """Route one alert to every subscription whose pattern matches `match`.
    Dedup identical (channel, kind, text) alerts; append as real events so
    replay reproduces them."""
    text = str(text)[:MAX_ALERT_TEXT]
    for sub in board["subscriptions"]:
        try:
            hit = fnmatch.fnmatchcase(match, sub["pattern"])
        except Exception:
            hit = False
        if not hit:
            continue
        channel = sub.get("channel", "session")
        if any(al.get("channel") == channel and al.get("kind") == kind
               and al.get("text") == text for al in board["alerts"]):
            continue
        ev = {"event": "alert", "channel": channel, "kind": kind, "text": text,
              "id": f"a{int(time.time() * 1000):013d}{len(board['alerts']) % 1000:03d}",
              "ts": time.time()}
        _append_event(paths, ev)
        _apply_event(board, ev)

def _neighbor_channels(board: dict, node: str) -> list:
    """Channels implied by graph links: mutations on `node` may concern its
    neighbors — surfaced via the subscription of any watcher matching the
    neighbor name (pattern match on the neighbor, not the source)."""
    chans = []
    for l in board["links"]:
        other = l["b"] if l["a"] == node else (l["a"] if l["b"] == node else None)
        if other is None:
            continue
        for sub in board["subscriptions"]:
            try:
                if fnmatch.fnmatchcase(other, sub["pattern"]):
                    chans.append(sub["channel"])
            except Exception:
                continue
    return chans

def _mutate(
    project_root: str,
    mutator: Callable[[dict[str, Any]], tuple[dict[str, Any], _MutationResult]],
) -> _MutationResult:
    """Run mutator(board)->(board, result) under one lock scope.

    The whole read-modify-write is one lock scope, so concurrent writers
    serialize and no update is lost. Lock acquisition performs its own bounded
    retry; if it still fails, no mutation runs and the error surfaces. This
    operation is never retried after entering the mutator: a storage failure
    after event append could otherwise append the same event a second time.

    Handoff TTL is enforced HERE, on the shared mutation path, for every
    event kind (E-040): the old alert-branch-only placement let a snapshot
    that already carried a stale handoff survive write/list/canvas/tool
    mutations forever — the live board accumulated 10 signals aged 2-3 days
    while daily work ran hundreds of mutations. The condition is unchanged
    (kind == "handoff" AND age > TTL); only the placement moved, so fresh
    signals still survive and the event-sourced consumption contract (TD-014)
    is untouched.
    """
    paths = board_paths(project_root)
    # The first mutation needs a directory in which to create its lock file.
    # Reads remain side-effect free for projects with no board yet.
    os.makedirs(paths["dir"], exist_ok=True)
    with _board_lock(paths):
        board = _read_snapshot(paths)
        _cleanup_stale_handoffs(board)
        board, result = mutator(board)
        board["updated"] = time.time()
        _write_snapshot_atomic(paths, board)
        _truncate_events_if_needed(paths)
        return result

# --- freshness planes (witness / retired) ---------------------------------------
# The board stores ANCHORS, the verdict is derived (modules/freshness.py). Write
# paths stamp a witness in the SAME mutation scope as the claim itself (event
# first, one lock, one snapshot) — a claim whose anchor lands in a later write
# could be read in the gap between the two, which is exactly the shape the
# freshness plane exists to remove.
def _witness_event(project_root: str, target: str, scope: str = ""):
    """A witness event for `target`, or None when there is nothing to anchor.

    Fail-open: git unavailable / freshness switched off → no event (the claim
    then reads UNKNOWN, which is the honest answer, not a fabricated CURRENT).
    """
    if not fr.enabled() or not str(target or "").strip():
        return None
    try:
        if fr.head(project_root) is None:
            return None
        anchor = fr.witness_of(project_root, scope=scope)
    except Exception:
        return None
    return {"event": "witness", "target": str(target)[:200],
            "commit": anchor["commit"], "tree": anchor["tree"],
            "scope": anchor["scope"], "ts": anchor["ts"]}

def _retire_targets(board: dict, target: str) -> list:
    """Alert ids a retirement of `target` withdraws.

    `handoff.<skill>` withdraws every waiting signal on the channel; an alert
    id withdraws that signal; anything else is read as a run key and withdraws
    every signal that cites it (`E-048`, `story.1-1-x`, …) — the mechanical
    form of "yesterday's decision no longer applies".
    """
    t = str(target or "").strip()
    if not t:
        return []
    ids = []
    if t.startswith("handoff."):
        for a in board["alerts"]:
            if a.get("channel") == t and a.get("kind") == "handoff" and a.get("id"):
                ids.append(a["id"])
        return ids
    for a in board["alerts"]:
        if a.get("id") == t:
            ids.append(t)
            return ids
    for a in board["alerts"]:
        if (a.get("kind") == "handoff" and a.get("id")
                and str(a.get("text", "")).startswith(f"{t}:")):
            ids.append(a["id"])
    return ids

def _retire_events(board: dict, target: str, reason: str, ts: float | None = None) -> list:
    """Retire events withdrawing `target` plus every signal that carries it."""
    t = str(target or "").strip()[:200]
    if not t:
        return []
    now = float(ts if ts is not None else time.time())
    events = [{"event": "retire", "target": t,
               "reason": str(reason)[:MAX_ALERT_TEXT], "ts": now}]
    for aid in _retire_targets(board, t):
        events.append({"event": "retire", "target": aid,
                       "reason": str(f"retired with {t}")[:MAX_ALERT_TEXT],
                       "ts": now})
    return events

def retire(project_root: str, target: str, reason: str = "") -> dict:
    """Withdraw a claim: "this no longer applies" as a first-class event.

    The sibling of `consume` (complete the handshake) and `forget` (drop a
    key): a retirement keeps the claim's own ts — the age stays honest — and
    changes what the claim MEANS. Retired signals stop being delivered as live
    work; retired keys keep their value but read RETIRED.
    """
    t = str(target or "").strip()[:200]
    if not t:
        return {"ok": False, "error": "retire needs a target (key, alert id or handoff.<skill>)"}

    def mut(board):
        paths = board_paths(project_root)
        events = _retire_events(board, t, reason)
        for ev in events:
            _append_event(paths, ev)
            _apply_event(board, ev)
        return board, {"ok": True, "target": t, "signals": len(events) - 1,
                       "reason": str(reason)[:MAX_ALERT_TEXT],
                       "root": paths["root"]}

    ack = _mutate(project_root, mut)
    # A retirement is an operator-visible state change, so it is announced on
    # the session channel (announce-only, like every other lifecycle signal).
    try:
        post_alert(project_root, "session", AlertKind.RETIRED.value,
                   f"retired '{t}' — {ack.get('signals', 0)} waiting signal(s) "
                   f"no longer apply{f': {reason}' if reason else ''}",
                   sender="freshness")
    except Exception:
        pass
    return ack

def witness_map(project_root: str) -> dict:
    """Every stored witness anchor: {target: {commit, tree, scope, ts}}."""
    return dict(read_board(project_root).get("witness") or {})

def retired_map(project_root: str) -> dict:
    """Every retirement marker: {target: {reason, ts}}."""
    return dict(read_board(project_root).get("retired") or {})

# --- text keys -------------------------------------------------------------------
def forget_key(project_root: str, key: str) -> dict:
    """Remove one namespaced key from the board (event-sourced).

    The board only ever retired a key through the cap (oldest-first, above
    MAX_KEYS); there was no way to drop a key a run no longer needs, so old
    per-record run-keys accumulated until every board log read a capacity
    warning. This appends a `key-forget` event; replay folds it, so the removal
    survives a snapshot rebuild and stays drift-free. Bridge keys (`scope`,
    `status`, `methodology.last_*`) and the current hot key are protected — a
    forget that would blind the relay or the focus is refused, never silently
    dropped.
    """
    key = str(key or "").strip()[:200]
    if not key:
        return {"ok": False, "error": "forget needs --key"}
    if key in _BRIDGE_KEYS:
        return {"ok": False, "error": f"key '{key}' is a protected bridge key"}

    def mut(board):
        if board.get("hot") == key:
            return board, {"ok": False,
                           "error": f"key '{key}' is the board's hot focus"}
        removed = key in board["keys"]
        ev = {"event": "key-forget", "key": key, "ts": time.time()}
        _append_event(board_paths(project_root), ev)
        _apply_event(board, ev)
        _clear_hot_if_dangling(board)
        return board, {"ok": True, "key": key, "removed": removed}

    return _mutate(project_root, mut)

def write_key(project_root: str, key: str, value, *, type_: str = "note",
              hot: bool = False) -> dict:
    """Write (create or overwrite) a namespaced key. Returns the ack.
    
    NEW: Bounds enforcement visible - checks MAX_KEYS limit before write (MEDIUM #4 / ISSUE #63)
    Oldest keys are expired when limit is reached.
    """
    if not key or not str(key).strip():
        return {"ok": False, "error": "empty key"}
    value = str(value)[:MAX_VALUE_LEN]
    event = {"event": "write", "key": key.strip()[:200], "value": value,
             "type": str(type_)[:50], "hot": bool(hot), "ts": time.time()}
    superseded: list = []  # records this write re-measures (cascade source)

    def mut(board):
        paths = board_paths(project_root)
        _append_event(paths, event)
        _apply_event(board, event)
        
        # NEW: Explicit bounds check - enforce MAX_KEYS (MEDIUM #4 / ISSUE #63)
        keys_count = len(board["keys"])
        if keys_count > MAX_KEYS:
            # Log warning about bounds enforcement
            sys.stderr.write(f"metodoloji: bounds enforced — keys {keys_count}/{MAX_KEYS}, "
                           f"oldest expired\n")
        
        _clear_hot_if_dangling(board)
        _route_alerts(paths, board, event["key"], "update",
                      f"key '{event['key']}' updated ({event['type']})")
        # Methodology relay mirror (no nesting — the mirror rides this
        # mutation): a stage's run-key write is the chain heartbeat, so
        # stamp methodology.last_* from it (E-/IR-/SP-/S-/QR-/PR-). Any
        # other key just leaves the mirror untouched.
        for prefix, mirror in _METHODOLOGY_MIRROR_KEYS:
            if event["key"].startswith(prefix):
                # Stamp the identity once. A caller whose value already opens
                # with the run key (the IR close-out writes
                # `IR-2026-09-28 READY — …`, its value *is* the verdict line)
                # must not become `IR-2026-09-28: IR-2026-09-28 READY — …` on
                # the relay: the chain line is where a router reads the
                # identity, and a doubled one reads like two runs (dogfood
                # board, 2026-09-28).
                raw = str(event["value"])
                opens_with_key = (raw.startswith(f"{event['key']}:")
                                  or raw.startswith(f"{event['key']} "))
                mirror_event = {
                    "event": "write", "key": mirror,
                    "value": (raw if opens_with_key
                              else f"{event['key']}: {raw}")[:MAX_VALUE_LEN],
                    "type": "state", "hot": False, "ts": event["ts"],
                }
                _append_event(paths, mirror_event)
                _apply_event(board, mirror_event)
                break
        # Run-open heartbeat (same derived-event idiom as the relay mirror):
        # marking a key hot means a run started, so a bridge `status` left at
        # the PREVIOUS run's `complete` must stop claiming the work is done —
        # that stale `complete` is what made bmad-help route "work is done,
        # here is the next step" while a UX run was mid-discovery (real
        # session, 2026-09-21: read --context returned status=complete with
        # hot=ux.mailjs). Only corrects a finished status; an unset status is
        # no claim at all, and an explicit `write --key status --value …`
        # always wins. A write to the bridge keys themselves never re-stamps.
        opened_status = None
        if hot and event["key"] not in _BRIDGE_KEYS:
            current = board["keys"].get("status")
            current_val = str(current.get("value", "")).strip().lower() \
                if isinstance(current, dict) else ""
            if current_val in ("complete", "done"):
                status_event = {"event": "write", "key": "status",
                                "value": "in-progress", "type": "state",
                                "hot": False, "ts": event["ts"]}
                _append_event(paths, status_event)
                _apply_event(board, status_event)
                opened_status = "in-progress"
        ack = {"ok": True, "key": event["key"], "hot": event["hot"],
               "root": board_paths(project_root)["root"]}
        if opened_status:
            ack["status"] = opened_status
        if board.get("hot") == event["key"] and not hot:
            ack["dirty_notice"] = f"key '{event['key']}' is hot on the board"
        # Watch-plane stamp (same scope, no nesting): a hot run-key write is
        # the run's opening heartbeat, so its owning stage registers as the
        # live watcher — the `watchers` plane has a writer for every stage
        # the skill contract touches, and compact_context's watcher list
        # reflects who is actually alive mid-run (bounded to MAX_WATCHERS).
        # Bridge keys (scope/status/purpose…) are focus plumbing, not stage
        # runs — they stamp no watcher. Mirrors _skill_for_run_key exactly.
        if hot and event["key"] not in _BRIDGE_KEYS:
            owner = _skill_for_run_key(event["key"])
            if owner:
                watch_event = {"event": "watch", "watcher": owner,
                               "ts": event["ts"]}
                _append_event(paths, watch_event)
                _apply_event(board, watch_event)
                ack["watchers"] = sorted(board["watchers"].keys())
        # Freshness anchor: the write is where a claim begins, so its witness
        # is stamped in this same mutation (event-first, one lock, one
        # snapshot) — never in a second write a reader could interleave with.
        w_ev = _witness_event(project_root, event["key"],
                              fr.record_scope(project_root, event["key"]))
        if w_ev:
            _append_event(paths, w_ev)
            _apply_event(board, w_ev)
            ack["witness"] = {"commit": w_ev["commit"], "tree": w_ev["tree"]}
        # Cascade invalidation (the plane that used to be declared but dead):
        # writing a NEW record id that older records declare themselves
        # re-measured by retires those older claims in the same breath —
        # "E-019 superseded by E-053" is now a mechanic, not a convention.
        if _RECORD_KEY_RE.match(event["key"]):
            for old in fr.superseded_records(project_root, event["key"]):
                superseded.append(old)
                for r_ev in _retire_events(board, old,
                                           f"superseded by {event['key']}",
                                           event["ts"]):
                    _append_event(paths, r_ev)
                    _apply_event(board, r_ev)
        return board, ack

    ack = _mutate(project_root, mut)
    for old in dict.fromkeys(superseded):
        try:
            post_alert(project_root, "session",
                       AlertKind.CASCADE_INVALIDATION.value,
                       f"{old} superseded by {event['key']} — waiting claims "
                       f"citing it are cascade-invalidated",
                       sender="freshness")
        except Exception:
            pass
    return ack

def set_hot(project_root: str, key: str | None) -> dict:
    """Focus one key (hot) or clear focus (key=None)."""
    if key:
        event = {"event": "hot", "key": str(key).strip()[:200], "ts": time.time()}
    else:
        event = {"event": "hot", "clear": True, "ts": time.time()}

    def mut(board):
        _append_event(board_paths(project_root), event)
        _apply_event(board, event)
        _clear_hot_if_dangling(board)
        return board, {"ok": True, "hot": board["hot"]}

    return _mutate(project_root, mut)

# --- lists -----------------------------------------------------------------------
def list_add(project_root: str, key: str, item: str) -> dict:
    """Append one item to an ordered list key (creates the list on first add)."""
    if not key or not str(key).strip():
        return {"ok": False, "error": "empty key"}
    if not str(item).strip():
        return {"ok": False, "error": "empty item"}
    event = {"event": "list_add", "key": key.strip()[:200],
             "item": str(item).strip()[:MAX_LIST_ITEM_LEN], "ts": time.time()}

    def mut(board):
        paths = board_paths(project_root)
        _append_event(paths, event)
        _apply_event(board, event)
        _clear_hot_if_dangling(board)
        _route_alerts(paths, board, event["key"], "update",
                      f"list '{event['key']}' +1 item (n="
                      f"{len(board['keys'].get(event['key'], {}).get('value', []))})")
        return board, {"ok": True, "key": event["key"],
                       "count": len(board["keys"].get(event["key"], {}).get("value", []))}

    return _mutate(project_root, mut)

def list_clear(project_root: str, key: str) -> dict:
    """Remove every item from a list key (keeps the key, now an empty list).
    One-command close-out for run lists (pending/branches/failures)."""
    if not key or not str(key).strip():
        return {"ok": False, "error": "empty key"}
    event = {"event": "list_clear", "key": key.strip()[:200], "ts": time.time()}

    def mut(board):
        entry = board["keys"].get(event["key"])
        had = len(entry["value"]) if entry and isinstance(entry.get("value"), list) else 0
        _append_event(board_paths(project_root), event)
        _apply_event(board, event)
        return board, {"ok": True, "key": event["key"], "cleared": had}

    return _mutate(project_root, mut)

def list_remove(project_root: str, key: str, *, item: str | None = None,
                index: int | None = None) -> dict:
    """Remove by exact item text or by index (0-based, oldest first)."""
    if not key or not str(key).strip():
        return {"ok": False, "error": "empty key"}
    event = {"event": "list_remove", "key": key.strip()[:200], "ts": time.time()}
    if item is not None:
        event["item"] = str(item)[:MAX_LIST_ITEM_LEN]
    elif index is not None:
        event["index"] = int(index)
    else:
        return {"ok": False, "error": "list-remove needs --item or --index"}

    def mut(board):
        entry = board["keys"].get(event["key"])
        before = len(entry["value"]) if entry and isinstance(entry.get("value"), list) else 0
        _append_event(board_paths(project_root), event)
        _apply_event(board, event)
        entry = board["keys"].get(event["key"])
        after = len(entry["value"]) if entry and isinstance(entry.get("value"), list) else 0
        return board, {"ok": True, "key": event["key"], "removed": before - after,
                       "count": after}

    return _mutate(project_root, mut)

# --- graph links -------------------------------------------------------------------
def link(project_root: str, a: str, b: str, relation: str = "related") -> dict:
    """Connect two nodes (key or canvas names) with a directed relation."""
    a, b = str(a).strip()[:200], str(b).strip()[:200]
    if not a or not b or a == b:
        return {"ok": False, "error": "link needs two distinct nodes"}
    event = {"event": "link", "a": a, "b": b,
             "relation": str(relation).strip()[:50] or "related", "ts": time.time()}

    def mut(board):
        paths = board_paths(project_root)
        _append_event(paths, event)
        _apply_event(board, event)
        for chan in _neighbor_channels(board, event["a"]):
            _route_alerts(paths, board, event["b"], "link",
                          f"'{event['b']}' linked to '{event['a']}' ({event['relation']})")
        return board, {"ok": True, "a": event["a"], "b": event["b"],
                       "relation": event["relation"]}

    return _mutate(project_root, mut)

def unlink(project_root: str, a: str, b: str, relation: str | None = None) -> dict:
    event = {"event": "unlink", "a": str(a).strip()[:200], "b": str(b).strip()[:200],
             "ts": time.time()}
    if relation:
        event["relation"] = str(relation).strip()[:50]

    def mut(board):
        before = len(board["links"])
        _append_event(board_paths(project_root), event)
        _apply_event(board, event)
        return board, {"ok": True, "removed": before - len(board["links"])}

    return _mutate(project_root, mut)

def neighbors(project_root: str, node: str, relation: str | None = None) -> list:
    """One-hop neighborhood (both link directions), optionally filtered."""
    node = str(node).strip()[:200]
    out, seen = [], set()
    for l in read_board(project_root)["links"]:
        if relation and l["relation"] != relation:
            continue
        if l["a"] == node:
            other, direction = l["b"], "out"
        elif l["b"] == node:
            other, direction = l["a"], "in"
        else:
            continue
        if other not in seen:
            seen.add(other)
            out.append({"node": other, "relation": l["relation"], "direction": direction})
    return out

# --- canvases (dynamic surfaces) -----------------------------------------------------
def _parse_grid(*args, **kwargs):
    """Compatibility delegate to the blackboard canvas service."""
    from . import blackboard_canvas as service
    return service._parse_grid(*args, **kwargs)

def canvas_create(*args, **kwargs):
    """Compatibility delegate to the blackboard canvas service."""
    from . import blackboard_canvas as service
    return service.canvas_create(*args, **kwargs)

def canvas_set(*args, **kwargs):
    """Compatibility delegate to the blackboard canvas service."""
    from . import blackboard_canvas as service
    return service.canvas_set(*args, **kwargs)

def canvas_remove(*args, **kwargs):
    """Compatibility delegate to the blackboard canvas service."""
    from . import blackboard_canvas as service
    return service.canvas_remove(*args, **kwargs)

def canvas_move(*args, **kwargs):
    """Compatibility delegate to the blackboard canvas service."""
    from . import blackboard_canvas as service
    return service.canvas_move(*args, **kwargs)

def canvas_resize(*args, **kwargs):
    """Compatibility delegate to the blackboard canvas service."""
    from . import blackboard_canvas as service
    return service.canvas_resize(*args, **kwargs)

def canvas_clear(*args, **kwargs):
    """Compatibility delegate to the blackboard canvas service."""
    from . import blackboard_canvas as service
    return service.canvas_clear(*args, **kwargs)

def canvas_focus(*args, **kwargs):
    """Compatibility delegate to the blackboard canvas service."""
    from . import blackboard_canvas as service
    return service.canvas_focus(*args, **kwargs)

def canvas_watch(*args, **kwargs):
    """Compatibility delegate to the blackboard canvas service."""
    from . import blackboard_canvas as service
    return service.canvas_watch(*args, **kwargs)

def read_canvas(*args, **kwargs):
    """Compatibility delegate to the blackboard canvas service."""
    from . import blackboard_canvas as service
    return service.read_canvas(*args, **kwargs)

def _touch_canvases(board: dict, paths: dict, tool: str, target: str) -> list:
    """Push one audited touch into every canvas watching a matching path.

    Called by stamp_tool_event in the same lock scope (no nesting).
    Returns touched canvas names."""
    touched: list[str] = []
    if not target:
        return touched
    for name, cv in board["canvases"].items():
        for w in cv.get("watch", []):
            if _watch_matches(w, target):
                ev = {"event": "canvas_touch", "name": name,
                      "path": target[:MAX_CELL_ID],
                      "content": f"{tool}: {os.path.basename(target)}"[:MAX_CELL_LEN],
                      "ts": time.time()}
                _append_event(paths, ev)
                _apply_event(board, ev)
                _route_alerts(paths, board, f"canvas:{name}", "canvas",
                              f"canvas '{name}' live: {tool} touched {target}")
                touched.append(name)
                break
    return touched

def stamp_tool_event(project_root: str, tool_name: str, target: str, hook_event: str = "") -> dict:
    """Fold one tool touch into the board in a single lock scope.

    Skill-side entry point (CLI: `canvas touch`): appends the
    `last_tool.<tool>` key as a `tool` event plus a `canvas_touch` push to
    watching canvases in the same scope. The audit hook deliberately does
    NOT call this (the engine write path is blackboard-free). One `_mutate`,
    so no writer can interleave; all event-sourced, so `last_tool.*`
    survives a snapshot rebuild. Never nest inside another _mutate scope.
    
    NEW: Optional hook_event parameter to track PreToolUse/PostToolUse sequence (HIGH #7)
    NEW: Session ID inclusion for multi-session isolation (PHASE 2 #2)
    """
    tool_name = str(tool_name or "")[:100]
    target = str(target or "")[:MAX_TEXT_LEN]
    if not tool_name:
        return {"ok": True, "touched": 0}
    
    # NEW: Extract session_id from latest SessionStart marker (PHASE 2 #2)
    session_id = ""
    try:
        paths = board_paths(project_root)
        with open(paths["events"], "r", encoding="utf-8") as f:
            lines = f.readlines()
        # Scan backwards to find last session_start
        for line in reversed(lines[-1000:]):  # Only check last 1000 lines for efficiency
            try:
                entry = json.loads(line)
                if entry.get("type") == "session_marker" and entry.get("hook_event") == "SessionStart":
                    session_id = entry.get("session_id", "")
                    break
            except (json.JSONDecodeError, ValueError):
                pass
    except (OSError, IOError):
        pass  # Can't read events file, continue without session_id
    
    event = {"event": "tool", "tool": tool_name, "target": target, "ts": time.time()}
    if hook_event:
        event["hook_event"] = str(hook_event)[:40]  # NEW: PreToolUse or PostToolUse
    if session_id:
        event["session_id"] = session_id  # NEW: Multi-session isolation (PHASE 2 #2)

    def mut(board):
        paths = board_paths(project_root)
        _append_event(paths, event)
        _apply_event(board, event)
        # Real-time canvas push: watched canvases record the touch (same scope).
        touched = _touch_canvases(board, paths, tool_name, target)
        return board, {"ok": True, "touched": len(touched), "canvases": touched}

    return _mutate(project_root, mut)

def canvas_touch(project_root: str, path: str, *, tool: str = "watch") -> dict:
    """Report one path touch to every canvas watching a matching prefix.

    The engine write path is blackboard-free, so a watched canvas is fed by
    its producers rather than by the PostToolUse hook: after touching a path,
    a skill calls this (CLI: `canvas touch --path P [--tool T]`) and every
    canvas whose `watch` prefix matches records an `auto` cell — the same
    event a filesystem watcher would have produced. Reports only; a board
    with no matching canvas is a no-op success.
    """
    target = str(path or "").strip()
    if not target:
        return {"ok": False, "error": "canvas-touch needs --path"}
    return stamp_tool_event(project_root, str(tool or "watch").strip() or "watch", target)

def _watch_matches(watch_path: str, target: str) -> bool:
    """Normalize a watch-prefix match (agnostic to rel/abs, backslash, ./).

    A raw string `startswith` misfired here — a rel path never matched the
    abs path a skill wrote to the watch, or vice versa."""
    import re as _re
    w = _re.sub(r"(?i)^[a-z]:", "", str(watch_path or "").replace("\\", "/"))
    t = _re.sub(r"(?i)^[a-z]:", "", str(target or "").replace("\\", "/"))
    w = _re.sub(r"/{2,}", "/", w).rstrip("/")
    t = _re.sub(r"/{2,}", "/", t).rstrip("/")
    while t.startswith("./"):
        t = t[2:]
    while w.startswith("./"):
        w = w[2:]
    if not w or not t:
        return False
    return t == w or t.startswith(w + "/") or w.startswith(t + "/")

# --- subscriptions & alerts ----------------------------------------------------------
def subscribe(project_root: str, watcher: str, pattern: str, *,
              channel: str = "session") -> dict:
    """Watch keys/canvases by glob: matching mutations route an alert into
    `channel` ('session' → injected at session start, 'stop' → surfaced at
    stop, or any custom channel addressable via consume_alerts)."""
    event = {"event": "subscribe", "watcher": str(watcher).strip()[:100],
             "pattern": str(pattern).strip()[:200],
             "channel": str(channel).strip()[:MAX_CHANNEL_LEN] or "session", "ts": time.time()}
    if not event["watcher"] or not event["pattern"]:
        return {"ok": False, "error": "subscribe needs --watcher and --pattern"}

    def mut(board):
        _append_event(board_paths(project_root), event)
        _apply_event(board, event)
        return board, {"ok": True, "watcher": event["watcher"],
                       "pattern": event["pattern"], "channel": event["channel"]}

    return _mutate(project_root, mut)

def unsubscribe(project_root: str, watcher: str, pattern: str) -> dict:
    event = {"event": "unsubscribe", "watcher": str(watcher).strip()[:100],
             "pattern": str(pattern).strip()[:200], "ts": time.time()}

    def mut(board):
        before = len(board["subscriptions"])
        _append_event(board_paths(project_root), event)
        _apply_event(board, event)
        return board, {"ok": True, "removed": before - len(board["subscriptions"])}

    return _mutate(project_root, mut)

def pending_alerts(project_root: str, channel: str | None = None) -> list:
    """Read-only: alerts awaiting a channel (all channels when None)."""
    alerts = read_board(project_root)["alerts"]
    if channel:
        return [a for a in alerts if a["channel"] == channel]
    return list(alerts)

def consume_alerts(project_root: str, channel: str) -> list:
    """Take and clear one channel's alerts (engine consumption point).
    Consumption is evented: replay folds consume after alert, so delivered
    alerts never resurrect from the event log."""
    def mut(board):
        take = [a for a in board["alerts"] if a["channel"] == channel]
        if take:
            ev = {"event": "consume", "channel": channel, "ts": time.time()}
            _append_event(board_paths(project_root), ev)
            _apply_event(board, ev)
        return board, take

    try:
        return _mutate(project_root, mut)
    except Exception:
        return []

# --- hand-off signals (the skill chain handshake) ------------------------------------
from .blackboard_handoff import (  # noqa: F401 — stable compatibility exports
    CHAIN, CHAIN_TERMINAL, METHODOLOGY_CHAIN, SUB_CHAINS, CHAIN_ADJACENT)

def post_handoff(*args, **kwargs):
    """Compatibility delegate to the handoff and signal delivery service."""
    from . import blackboard_handoff as service
    return service.post_handoff(*args, **kwargs)

def replace_handoff(*args, **kwargs):
    """Compatibility delegate to the handoff and signal delivery service."""
    from . import blackboard_handoff as service
    return service.replace_handoff(*args, **kwargs)

def _routed_skills(*args, **kwargs):
    """Compatibility delegate to the handoff and signal delivery service."""
    from . import blackboard_handoff as service
    return service._routed_skills(*args, **kwargs)

def _annotate_alert(*args, **kwargs):
    """Compatibility delegate to the handoff and signal delivery service."""
    from . import blackboard_handoff as service
    return service._annotate_alert(*args, **kwargs)

def _waiting_signals(*args, **kwargs):
    """Compatibility delegate to the handoff and signal delivery service."""
    from . import blackboard_handoff as service
    return service._waiting_signals(*args, **kwargs)

def pending_handoffs(*args, **kwargs):
    """Compatibility delegate to the handoff and signal delivery service."""
    from . import blackboard_handoff as service
    return service.pending_handoffs(*args, **kwargs)

def pending_handoff_channels(*args, **kwargs):
    """Compatibility delegate to the handoff and signal delivery service."""
    from . import blackboard_handoff as service
    return service.pending_handoff_channels(*args, **kwargs)

def pending_handoff_freshness(*args, **kwargs):
    """Compatibility delegate to the handoff and signal delivery service."""
    from . import blackboard_handoff as service
    return service.pending_handoff_freshness(*args, **kwargs)

def recent_handoffs(*args, **kwargs):
    """Compatibility delegate to the handoff and signal delivery service."""
    from . import blackboard_handoff as service
    return service.recent_handoffs(*args, **kwargs)

from .blackboard_chain import (  # noqa: F401 — stable compatibility exports
    _KEY_PREFIX_TO_SKILL, _SENDER_RE, _RECORD_KEY_RE)

def sub_chain_skills(*args, **kwargs):
    """Compatibility delegate to the handoff-chain health analysis service."""
    from . import blackboard_chain as service
    return service.sub_chain_skills(*args, **kwargs)

def _skill_for_run_key(*args, **kwargs):
    """Compatibility delegate to the handoff-chain health analysis service."""
    from . import blackboard_chain as service
    return service._skill_for_run_key(*args, **kwargs)

def _signal_sender(*args, **kwargs):
    """Compatibility delegate to the handoff-chain health analysis service."""
    from . import blackboard_chain as service
    return service._signal_sender(*args, **kwargs)

def chain_health(*args, **kwargs):
    """Compatibility delegate to the handoff-chain health analysis service."""
    from . import blackboard_chain as service
    return service.chain_health(*args, **kwargs)

def waiting_hop_rows(*args, **kwargs):
    """Compatibility delegate to the handoff-chain health analysis service."""
    from . import blackboard_chain as service
    return service.waiting_hop_rows(*args, **kwargs)

# --- doctor (one-glance diagnostic) ----------------------------------------
_DOCTOR_CAP_WARN_PCT = 90  # compatibility constant for existing readers

def _doctor_age(ts: float) -> str:
    from .blackboard_diagnostics import _doctor_age as impl
    return impl(ts)

def _doctor_drift(paths: dict, board: dict) -> dict:
    from .blackboard_diagnostics import _doctor_drift as impl
    return impl(paths, board)

def rotate_event_log(project_root: str, max_lines: int = 10000) -> dict:
    """Stable board API for the separate event-log archive service."""
    from .blackboard_archive import rotate_event_log as rotate
    return rotate(project_root, max_lines=max_lines)

def doctor(project_root: str, *, post: bool = False) -> dict:
    from .blackboard_diagnostics import doctor as impl
    return impl(project_root, post=post)

def _post_lifecycle_alerts(project_root: str, unclear: list,
                           stale_session_hours: int | None) -> int:
    from .blackboard_diagnostics import _post_lifecycle_alerts as impl
    return impl(project_root, unclear, stale_session_hours)

# Announcement-state kinds: re-posting the identical sentence is not a new
# fact. These dedup because MAX_ALERTS is a SHARED pool (32) that also holds
# waiting hand-off signals — a lifecycle announcement repeated per turn would
# evict real batons. Hand-off signals deliberately keep their exact previous
# semantics: every post is a distinct bridge between two runs.
_DEDUP_ALERT_KINDS = frozenset({AlertKind.RETIRED.value,
                                AlertKind.CASCADE_INVALIDATION.value,
                                AlertKind.STALE_SESSION.value,
                                AlertKind.WITNESS_STALE.value})

def post_alert(project_root: str, channel: str, kind: str, text: str,
               *, sender: str | None = None) -> dict:
    """Manual alert injection (skills can notify the session/stop channels).
    
    NEW: Bounds enforcement visible - checks MAX_ALERTS limit (MEDIUM #4 / ISSUE #63)
    NEW: Kind validation against AlertKind enum (PHASE 4 #7)
    Oldest alerts are expired when limit is reached.
    """
    # NEW: Validate kind against AlertKind enum (PHASE 4 #7)
    kind_str = str(kind).strip()[:20] or "info"
    valid_kinds = {k.value for k in AlertKind}
    if kind_str not in valid_kinds:
        # Log warning but allow (fail-open) — map unknown kinds to "info"
        sys.stderr.write(f"metodoloji: unknown alert kind '{kind_str}' (expected one of {valid_kinds}), "
                        f"mapping to 'info'\n")
        kind_str = "info"
    
    event = {"event": "alert", "channel": str(channel).strip()[:MAX_CHANNEL_LEN] or "session",
             "kind": kind_str,
             "text": str(text).strip()[:MAX_ALERT_TEXT],
             "id": f"m{int(time.time() * 1000):013d}", "ts": time.time()}
    # Explicit sender rides with the event (event-sourced, additive): a signal
    # from a shared run-key namespace is attributed to the stage that posted it
    # instead of the prefix's default owner. Never trusted verbatim — the
    # reader validates the token shape and falls back to the prefix.
    if sender and _SENDER_RE.match(str(sender).strip()):
        event["sender"] = str(sender).strip()
    if not event["text"]:
        return {"ok": False, "error": "empty alert text"}

    def mut(board):
        paths = board_paths(project_root)
        if kind_str in _DEDUP_ALERT_KINDS and any(
                a.get("channel") == event["channel"] and a.get("kind") == kind_str
                and a.get("text") == event["text"] for a in board["alerts"]):
            return board, {"ok": True, "channel": event["channel"],
                           "duplicate": True, "alerts": len(board["alerts"]),
                           "root": paths["root"]}
        _append_event(paths, event)
        _apply_event(board, event)
        # A hand-off signal IS a claim about the code state, so it is anchored
        # to that state in this same mutation (the freshness plane's writer).
        if kind_str == "handoff":
            w_ev = _witness_event(
                project_root, event["id"],
                fr.record_scope(project_root, fr.key_of(event["text"])))
            if w_ev:
                _append_event(paths, w_ev)
                _apply_event(board, w_ev)

        # NEW: Explicit bounds check - enforce MAX_ALERTS (MEDIUM #4 / ISSUE #63)
        alert_count = len(board["alerts"])
        if alert_count > MAX_ALERTS:
            sys.stderr.write(f"metodoloji: bounds enforced — alerts {alert_count}/{MAX_ALERTS}, "
                            f"oldest expired\n")

        return board, {"ok": True, "channel": event["channel"],
                       "id": event["id"], "alerts": alert_count,
                       "root": paths["root"]}

    return _mutate(project_root, mut)

# --- contributions / watchers (unchanged surface) --------------------------------------
def add_contribution(project_root: str, who: str, what: str) -> dict:
    if not str(who).strip():
        return {"ok": False, "error": "empty who"}
    event = {"event": "contribute", "who": str(who).strip()[:200],
             "what": str(what).strip()[:MAX_TEXT_LEN], "ts": time.time()}

    def mut(board):
        _append_event(board_paths(project_root), event)
        _apply_event(board, event)
        return board, {"ok": True, "who": event["who"]}

    return _mutate(project_root, mut)

def add_tag(project_root: str, tag: str) -> dict:
    tag = str(tag).strip()[:100]
    if not tag:
        return {"ok": False, "error": "empty tag"}

    def mut(board):
        if tag in board["tags"]:
            return board, {"ok": True, "tag": tag, "duplicate": True}
        event = {"event": "tag", "tag": tag, "ts": time.time()}
        _append_event(board_paths(project_root), event)
        _apply_event(board, event)
        return board, {"ok": True, "tag": tag}

    return _mutate(project_root, mut)

def remove_tag(project_root: str, tag: str) -> dict:
    tag = str(tag).strip()[:100]

    def mut(board):
        if tag not in board["tags"]:
            return board, {"ok": True, "tag": tag, "absent": True}
        event = {"event": "untag", "tag": tag, "ts": time.time()}
        _append_event(board_paths(project_root), event)
        _apply_event(board, event)
        return board, {"ok": True, "tag": tag}

    return _mutate(project_root, mut)

def record_watcher(project_root: str, watcher: str) -> dict:
    event = {"event": "watch", "watcher": str(watcher).strip()[:100],
             "ts": time.time()}

    def mut(board):
        _append_event(board_paths(project_root), event)
        _apply_event(board, event)
        return board, {"ok": True, "watcher": event["watcher"]}

    return _mutate(project_root, mut)

# --- context & stats --------------------------------------------------------------------
def compact_context(project_root: str) -> dict:
    """The bounded-context summary hooks inject (never the whole board)."""
    board = read_board(project_root)
    hot_key = board.get("hot")
    hot_value = None
    if hot_key and hot_key in board["keys"]:
        entry = board["keys"][hot_key]
        value = entry.get("value")
        if isinstance(value, list):
            preview = f"list[{len(value)}]"
        else:
            preview = str(value or "")[:120]
        hot_value = {"type": entry.get("type"), "updated": entry.get("updated"),
                     "preview": preview}
    hot_canvas = board.get("hot_canvas")
    canvas_summary = None
    if hot_canvas and hot_canvas in board["canvases"]:
        cv = board["canvases"][hot_canvas]
        cells = cv.get("cells", {})
        autos = sum(1 for c in cells.values() if c.get("kind") == "auto")
        latest = None
        if cells:
            latest = max(cells.items(), key=lambda kv: kv[1].get("updated", 0.0))[0]
        canvas_summary = {"name": hot_canvas, "cells": len(cells),
                          "auto": autos, "grid": cv.get("grid"),
                          "watch": cv.get("watch", []), "latest": latest}
    nb = [n["node"] for n in neighbors(project_root, hot_key)] if hot_key else []
    # Focus summary: the same keys bootstrap exports as env. Included so
    # a session-start inject (or read --context consumer) sees the live
    # scope/status even when bootstrap's env snapshot predates a mid-session
    # skill write (see utils board-first ordering).
    focus = {}
    for k in ("scope", "status", "priority", "focus_story"):  # NEW: Expanded context (PHASE 3 #6)
        entry = board["keys"].get(k)
        if entry and isinstance(entry, dict) and str(entry.get("value", "")).strip():
            focus[k] = str(entry.get("value"))[:120]

    # Methodology chain progress: writing a stage's run key (the close-out
    # contract: `write --key E-001 --value "APPROVED …"`) mirrors
    # methodology.last_* — see write_key. Surface them in chain order so
    # the injected context shows how far the E→IR→SP→S→QR→PR relay has
    # actually progressed (empty = not started).
    methodology = {}
    methodology_freshness = {}
    with fr.batch():
        for stage, key in (("E", "methodology.last_experiment"),
                           ("IR", "methodology.last_ir"),
                           ("SP", "methodology.last_sp"),
                           ("S", "methodology.last_story"),
                           ("QR", "methodology.last_qr"),
                           ("PR", "methodology.last_pr")):
            entry = board["keys"].get(key)
            if not (entry and isinstance(entry, dict)
                    and str(entry.get("value", "")).strip()):
                continue
            methodology[stage] = str(entry.get("value"))[:80]
            # The relay line is a CLAIM about a record, so it is dated too: the
            # cited run key's own witness (write_key stamped it) or its record's
            # commit. Without this the chain-progress sentence repeated history
            # as if it were present tense (F-02/F-03).
            cited = fr.key_of(str(entry.get("value", "")))
            try:
                ann = fr.annotate(
                    project_root, cited,
                    stored=(board.get("witness") or {}).get(cited) if cited else None)
            except Exception:
                continue
            methodology_freshness[stage] = {
                "verdict": ann["verdict"], "age_s": ann["age_s"],
                "reason": ann["reason"], "run_key": ann["run_key"],
                "label": fr.label(ann["verdict"], ann["age_s"])}

    # NEW: Operator preferences and urgency (PHASE 3 #6)
    tags_list = list(board["tags"])
    priority = "normal"
    if "critical" in tags_list:
        priority = "critical"
    elif "urgent" in tags_list:
        priority = "urgent"
    elif "low-priority" in tags_list:
        priority = "low"
    
    return {
        "hot": hot_key,
        "hot_meta": hot_value,
        "hot_canvas": canvas_summary,
        "focus": focus,
        "methodology": methodology,
        "methodology_freshness": methodology_freshness,
        "priority": priority,  # NEW (PHASE 3 #6)
        "tags": tags_list,
        "watchers": sorted(board["watchers"].keys()),
        "contributions": board["contributions"][-5:],
        "key_count": len(board["keys"]),
        "canvas_count": len(board["canvases"]),
        "links": len(board["links"]),
        "neighbors": nb[:5],
        "subscriptions": len(board["subscriptions"]),
        "alerts": len(board["alerts"]),
    }

def stats(project_root: str) -> dict:
    board = read_board(project_root)
    paths = board_paths(project_root)
    event_count = 0
    try:
        with open(paths["events"], "rb") as f:
            event_count = sum(1 for _ in f)
    except OSError:
        pass
    lists = sum(1 for e in board["keys"].values() if isinstance(e.get("value"), list))
    cells = sum(len(cv.get("cells", {})) for cv in board["canvases"].values())
    return {
        "keys": len(board["keys"]),
        "lists": lists,
        "canvases": len(board["canvases"]),
        "cells": cells,
        "links": len(board["links"]),
        "subscriptions": len(board["subscriptions"]),
        "alerts": len(board["alerts"]),
        "tags": len(board["tags"]),
        "contributions": len(board["contributions"]),
        "watchers": len(board["watchers"]),
        "events": event_count,
        "hot": board.get("hot"),
        "hot_canvas": board.get("hot_canvas"),
        "snapshot": paths["snapshot"],
    }


# Import after the canonical module state is defined so the fold helpers can
# read its constants and public test seams without a partial-import cycle.
from .blackboard_fold import (  # noqa: E402,F401 — canonical fold helpers and compatibility exports
    _apply_event, _cap_auto_cells, _cap_canvases, _cap_consumed, _cap_keys,
    _cap_open_handoffs, _cap_retired, _cap_witness, _cleanup_stale_handoffs,
    _clear_hot_if_dangling,
)

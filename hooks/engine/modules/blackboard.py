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
- Fail-open: every public function returns a usable default on any error.
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
from contextlib import contextmanager
from enum import Enum  # NEW: For AlertKind (PHASE 4 #7)

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
    """Raised internally; public API converts to fail-open defaults."""

# --- Locking -----------------------------------------------------------------
def _acquire_lock(lock_path: str):
    """Acquire an exclusive advisory lock; None only when locking is unsupported.

    Windows: msvcrt.locking is byte-range based and non-blocking LK_NBLCK
    raises on contention — retry a bounded number of times with exponential backoff.
    When the lock cannot be taken at all the caller proceeds unguarded (fail-open)
    and the per-process tmp filename keeps the snapshot rename safe.
    (HIGH #2 / ISSUE #6: Windows lock retry with exponential backoff)
    """
    try:
        f = open(lock_path, "a+")
    except OSError:
        return None
    try:
        import fcntl  # POSIX
        fcntl.flock(f, fcntl.LOCK_EX)
        return f
    except ImportError:
        pass
    except OSError:
        f.close()
        return None
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
            # else: last attempt failed, fall through to fail-open
    
    # FIXED: Fail-open (return None) instead of raising exception (HIGH #2 / ISSUE #6)
    # The per-process tmp filename in _replace_atomic() keeps the snapshot rename safe
    f.close()
    return None

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
                fcntl.flock(f, fcntl.LOCK_UN)
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
def read_board(project_root: str) -> dict:
    """Load the board snapshot (fail-open: empty board on any error)."""
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
                except ValueError as e:
                    # NEW: Skip malformed JSON but log for diagnostics
                    skipped_lines += 1
                    # Could emit warning, but silently skip for now (fail-open)
                    # In production, operator would see these in doctor output
                    continue
                except Exception as e:
                    # Catch other errors (e.g., encoding, memory) and skip
                    skipped_lines += 1
                    continue
                
                try:
                    _apply_event(board, ev)
                except Exception as e:
                    # NEW: If applying an event crashes, skip it but keep going
                    # (HIGH #1: Corruption recovery - keep as much state as possible)
                    skipped_lines += 1
                    continue
    except (OSError, UnicodeDecodeError):
        # A missing log is empty (OSError); a log that is not valid UTF-8 is
        # undecodable, so it too folds to an empty board instead of raising.
        pass
    
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

def _mutate(project_root: str, mutator) -> dict:
    """Run mutator(board)->(board, result) under lock with bounded retries.

    The whole read-modify-write is one lock scope, so concurrent writers
    serialize and no update is lost. On lock exhaustion the mutation is
    retried from a fresh read; only a persistent failure surfaces.

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
    last_exc = None
    for _ in range(3):
        try:
            with _board_lock(paths):
                board = _read_snapshot(paths)
                _cleanup_stale_handoffs(board)
                board, result = mutator(board)
                board["updated"] = time.time()
                _write_snapshot_atomic(paths, board)
                _truncate_events_if_needed(paths)
                return result
        except BoardError as exc:
            last_exc = exc
            time.sleep(0.1)
    raise last_exc

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
def _parse_grid(spec) -> list | None:
    """Accept [w, h], "WxH" or None; returns a sane grid or None (free canvas)."""
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
    event = {"event": "canvas_resize", "name": str(name).strip()[:MAX_CANVAS_NAME],
             "grid": _parse_grid(grid), "ts": time.time()}

    def mut(board):
        _append_event(board_paths(project_root), event)
        _apply_event(board, event)
        return board, {"ok": True, "canvas": event["name"], "grid": event["grid"]}

    return _mutate(project_root, mut)

def canvas_clear(project_root: str, name: str) -> dict:
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
    name = str(name).strip()[:MAX_CANVAS_NAME]
    board = read_board(project_root)
    cv = board["canvases"].get(name)
    if cv is None:
        return {"ok": True, "canvas": name, "exists": False, "cells": {}}
    return {"ok": True, "canvas": name, "exists": True, "grid": cv.get("grid"),
            "cells": cv.get("cells", {}), "watch": cv.get("watch", []),
            "focused": board.get("hot_canvas") == name}

def _touch_canvases(board: dict, paths: dict, tool: str, target: str) -> list:
    """Push one audited touch into every canvas watching a matching path.

    Called by stamp_tool_event in the same lock scope (no nesting).
    Returns touched canvas names."""
    touched = []
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
def post_handoff(project_root: str, to_skill: str, from_key: str, note: str = "",
                 *, sender: str | None = None) -> dict:
    """Upstream→downstream hand-off signal: route an alert into the
    `handoff.<to_skill>` channel (kind `handoff`, text `<from_key>: <note>`).
    The signal waits there until the downstream skill consumes it — that
    consumption completes the handshake.

    `sender` is optional but authoritative: pass it whenever the run key's
    namespace is shared by two stages (`story.` is written by BOTH
    create-story and dev-story), so the diagnostic attributes the baton to the
    stage that actually posted it instead of guessing from the prefix."""
    to_skill = str(to_skill).strip()[:MAX_CHANNEL_LEN - len("handoff.")]  # channel budget
    if not to_skill:
        return {"ok": False, "error": "empty --to"}
    from_key = str(from_key).strip()[:200]
    if not from_key:
        return {"ok": False, "error": "empty --from-key"}
    return post_alert(project_root, f"handoff.{to_skill}", "handoff",
                      f"{from_key}: {str(note).strip()}", sender=sender)

def replace_handoff(project_root: str, to_skill: str, from_key: str, note: str = "",
                    *, sender: str | None = None) -> dict:
    """Correct the note on a WAITING hand-off signal without dropping it.

    A hand-off is a bridge, not a message: consuming it to "fix a typo in the
    note" clears the waiting signal, and downstream readers that peeked in the
    gap saw a relay that no longer exists (real opencode session, 2026-09-30:
    a close-out note with a drafting typo was corrected by consume → repost,
    which silently dropped the signal for everyone reading between the two
    calls). This replaces the alert text in place — the signal keeps its
    original ts (so staleness/age stays truthful), stays in its channel, and
    never disappears. The replaced alert id is stable (same id, new text), so
    snapshot folds and dedup stay consistent.

    No waiting signal from `from_key` → posts a fresh one (first-write
    semantics, identical to post_handoff), so a correction to an already-
    consumed handshake cannot resurrect the consumed bridge. Returns the same
    ack shape as post_handoff plus `"replaced": True|False`.
    """
    to_skill = str(to_skill).strip()[:MAX_CHANNEL_LEN - len("handoff.")]  # channel budget
    if not to_skill:
        return {"ok": False, "error": "empty --to"}
    from_key = str(from_key).strip()[:200]
    if not from_key:
        return {"ok": False, "error": "empty --from-key"}
    channel = f"handoff.{to_skill}"
    new_text = f"{from_key}: {str(note).strip()}"
    waiting = [a for a in read_board(project_root)["alerts"]
               if a.get("channel") == channel and a.get("kind") == "handoff"
               and str(a.get("text", "")).startswith(f"{from_key}:")]
    if not waiting:
        # No waiting signal from this key: a correction is a first write.
        # (A consumed handshake stays consumed — never resurrect it.)
        ack = post_alert(project_root, channel, "handoff", new_text, sender=sender)
        ack["replaced"] = False
        return ack

    def mut(board):
        replaced = 0
        for a in board["alerts"]:
            if (a.get("channel") == channel and a.get("kind") == "handoff"
                    and str(a.get("text", "")).startswith(f"{from_key}:") and a.get("id")):
                ev = {"event": "alert-edit", "id": a["id"], "channel": channel,
                      "kind": "handoff", "text": new_text, "ts": time.time()}
                if sender and _SENDER_RE.match(str(sender).strip()):
                    ev["sender"] = str(sender).strip()
                _append_event(board_paths(project_root), ev)
                _apply_event(board, ev)
                replaced += 1
        return board, {"ok": replaced > 0, "replaced": True, "count": replaced,
                       "channel": channel,
                       "root": board_paths(project_root)["root"]}

    return _mutate(project_root, mut)

# --- freshness annotation of waiting signals -------------------------------------
# ONE scan, ONE derivation. pending_handoffs, pending_handoff_channels,
# pending_handoff_freshness, recent_handoffs, chain_health, doctor and the
# session-start inject all read this, because a second parser is a second
# opinion about whether a claim still holds (the project's own doctrine).
def _routed_skills() -> set:
    """Every known receiving skill (delivery + methodology + sub-chains)."""
    return (set(CHAIN) | set(METHODOLOGY_CHAIN) | sub_chain_skills()
            | CHAIN_ADJACENT
            | {s for hop in CHAIN_TERMINAL for s in hop})

def _annotate_alert(project_root: str, alert: dict, board: dict) -> dict:
    """Derived freshness for one waiting signal — read-only, never raises.

    A stored witness (written with the signal) wins; otherwise the claim is
    dated by the commit that last touched the record its run key names. A
    retirement (of the signal, its channel, or its run key) overrides both.
    """
    aid = str(alert.get("id", ""))
    channel = str(alert.get("channel", ""))
    key = fr.key_of(alert.get("text", ""))
    markers = board.get("retired") or {}
    retired = (aid in markers or channel in markers or bool(key and key in markers))
    stored = (board.get("witness") or {}).get(aid) if aid else None
    try:
        ann = fr.annotate(project_root, key, stored=stored, retired=retired)
    except Exception:
        ann = {"verdict": fr.UNKNOWN, "reason": "freshness derivation failed",
               "age_s": None, "changed": [], "changed_count": 0,
               "run_key": key, "anchor": "none", "superseded_by": None}
    if ann.get("age_s") is None:
        try:
            ann["age_s"] = max(0.0, time.time() - float(alert.get("ts", 0.0) or 0.0))
        except (TypeError, ValueError):
            ann["age_s"] = None
    if retired and ann.get("verdict") != fr.RETIRED:
        marker = markers.get(aid) or markers.get(channel) or markers.get(key) or {}
        ann = {**ann, "verdict": fr.RETIRED,
               "reason": f"retired — {marker.get('reason') or 'no longer applies'}"}
    return ann

def _waiting_signals(project_root: str, *, board: dict | None = None,
                     include_retired: bool = False) -> list:
    """Every waiting hand-off signal, annotated with its derived freshness.

    Shaped for every reader: the alert's own fields (ts/text/id/channel/sender)
    plus `skill`, `verdict`, `reason`, `age_s`, `changed_count`, `anchor`,
    `run_key`. Retired signals are dropped unless explicitly asked for — a
    withdrawn claim is not pending work.
    """
    board = board if board is not None else read_board(project_root)
    out = []
    # One batch: N signals on one board cost ONE head/diff/status read instead
    # of N, and the cache dies with the scan so the next reader sees git as it
    # is then (a wall-clock TTL here was measured hiding a commit — see
    # freshness._BATCH).
    with fr.batch():
        for alert in board.get("alerts", []):
            if not isinstance(alert, dict) or alert.get("kind") != "handoff":
                continue
            channel = str(alert.get("channel", ""))
            if not channel.startswith("handoff."):
                continue
            ann = _annotate_alert(project_root, alert, board)
            if ann["verdict"] == fr.RETIRED and not include_retired:
                continue
            out.append({**alert, "skill": channel[len("handoff."):],
                        "verdict": ann["verdict"], "reason": ann["reason"],
                        "age_s": ann["age_s"],
                        "changed_count": ann["changed_count"],
                        "anchor": ann["anchor"], "run_key": ann["run_key"]})
    return out

def pending_handoffs(project_root: str, skill: str, *,
                     only_current: bool = False) -> list:
    """Un-consumed hand-off signals waiting for `skill` (read-only peek).

    Each entry carries the DERIVED freshness verdict (`CURRENT` / `STALE` /
    `UNKNOWN`) and the reason behind it, so a reader can say "this no longer
    applies" instead of repeating the past. `only_current=True` drops the
    claims the code state has moved past.
    """
    skill = str(skill).strip()[:MAX_CHANNEL_LEN - len("handoff.")]  # mirror post_handoff's budget
    return [sig for sig in _waiting_signals(project_root)
            if sig["skill"] == skill
            and (not only_current or sig["verdict"] == fr.CURRENT)]

def pending_handoff_channels(project_root: str) -> dict:
    """Skills with waiting hand-off signals: {skill: count} (engine peek —
    session_start announces, never consumes; the skill completes the shake).

    Routed skills only: the delivery relay (CHAIN) together with its DECLARED
    terminal hops (CHAIN_TERMINAL — both ends, so the gate is routable without
    naming it here), the methodology relay (METHODOLOGY_CHAIN), every sub-chain
    member (SUB_CHAINS — discovery, alt_dev, …), the chain-adjacent surfaces
    (CHAIN_ADJACENT — help, infrastructure, the loop orchestrator's automation
    layer, the WDS backends), so a waiting signal addressed to any known skill
    surfaces instead of being silently dropped. Off-relay channels stay out of
    the session-start nudge — the skill-side CLI lists every channel: run the
    plugin's ``bmad/scripts/blackboard.py handoffs`` (without `--skill`) from
    the plugin root.

    Counts the SAME scan `pending_handoff_freshness` breaks down by verdict, so
    the count and the breakdown can never disagree. Retired signals are not
    waiting work and are excluded from both.
    """
    relay = _routed_skills()
    counts = {}
    for sig in _waiting_signals(project_root):
        if sig["skill"] in relay:
            counts[sig["skill"]] = counts.get(sig["skill"], 0) + 1
    return counts

def pending_handoff_freshness(project_root: str) -> dict:
    """Per-routed-channel freshness breakdown of the waiting signals.

    `{skill: {total, current, stale, unknown, oldest_age_s, stale_keys,
    unknown_keys}}` — the data the session-start inject and doctor render. The
    read that gives the delivery plane a third answer (E-052/E-053): a baton
    the code moved past is reported as such instead of as live work.
    """
    relay = _routed_skills()
    out: dict = {}
    for sig in _waiting_signals(project_root):
        skill = sig["skill"]
        if skill not in relay:
            continue
        entry = out.setdefault(skill, {"total": 0, "current": 0, "stale": 0,
                                       "unknown": 0, "oldest_age_s": None,
                                       "stale_keys": [], "unknown_keys": [],
                                       "freshness": []})
        entry["total"] += 1
        verdict = str(sig["verdict"]).lower()
        entry[verdict if verdict in ("current", "stale", "unknown") else "unknown"] += 1
        age = sig.get("age_s")
        if isinstance(age, (int, float)):
            if entry["oldest_age_s"] is None or age > entry["oldest_age_s"]:
                entry["oldest_age_s"] = float(age)
        key = sig.get("run_key")
        if key and sig["verdict"] in (fr.STALE, fr.UNKNOWN):
            bucket = ("stale_keys" if sig["verdict"] == fr.STALE
                      else "unknown_keys")
            if key not in entry[bucket]:
                entry[bucket].append(key)
            entry["freshness"].append(
                {"key": key, "verdict": sig["verdict"],
                 "label": fr.label(sig["verdict"], age),
                 "age_s": age, "reason": str(sig.get("reason", ""))})
    return out

def recent_handoffs(project_root: str, limit: int = 3, *,
                    only_current: bool = False,
                    with_freshness: bool = False) -> list:
    """Most recent un-consumed hand-off signals, relay-routed only (read-only).

    The engine-side carrier for the close-out relay: skills post their
    downstream baton via `mirror --to` and a later session can only see it if
    some surface re-announces it — stop/session_start announce counts at the
    edges, but mid-session every turn was silent (the 2026-09-23 mailjs
    session forgot dev-story's baton to code-review one turn after the
    close-out). Hook layer: guard/pre peeks this list and re-injects it via
    PostToolUse warnings (throttled in audit.py); orient.py folds it into the
    digest. Shaped for injection: newest first, at most `limit`, each entry
    `{skill, from, note, age_s}`. Fail-open caller side; off-relay channels
    stay out (same routing rule as pending_handoff_channels).
    """
    limit = max(1, min(int(limit), 10))
    relay = _routed_skills()
    now = time.time()
    hits = []
    for sig in _waiting_signals(project_root):
        skill = sig["skill"]
        if skill not in relay:
            continue
        if only_current and sig["verdict"] != fr.CURRENT:
            continue
        text = str(sig.get("text", ""))
        age = sig.get("age_s")
        if not isinstance(age, (int, float)):
            age = max(0.0, now - float(sig.get("ts", 0.0) or 0.0))
        hit = {
            "skill": skill,
            "from": text.split(":", 1)[0] if ":" in text else "",
            "note": text.split(":", 1)[1].strip() if ":" in text else text,
            "age_s": age,
        }
        if with_freshness:
            # The verdict rides the note too (the inject reads `note`), so a
            # stale baton cannot be re-announced as fresh work by a surface
            # that only renders the table.
            hit["verdict"] = sig["verdict"]
            hit["reason"] = sig["reason"]
        hits.append(hit)
    # Freshest baton first. Sort on the raw float — same-second batons keep
    # their post order stable (an int-rounded key would tie them and flip the
    # order); the shape rounds age_s for display.
    hits.sort(key=lambda h: h["age_s"])
    for h in hits:
        h["age_s"] = int(h["age_s"])
    return hits[:limit]

# --- chain health (hand-off diagnostics) ----------------------------------------------
# The canonical delivery relay: each hop is one skill handing off to the next.
# TOOL CHAIN: PRD → UX → Architecture → Spec → Epics → Story → Dev (7 skills,
# 6 stage-to-stage hops) — the ordered hop list chain_health reports.
CHAIN = [
    "bmad-prd", "bmad-ux", "bmad-architecture", "bmad-spec",
    "bmad-create-epics-and-stories", "bmad-create-story", "bmad-dev-story"
]

# TERMINAL HOPS: receivers that consume a relay signal and hand nothing on.
# They ARE hops (a signal waits there and is consumed there), so they must be
# declared — otherwise the terminal baton reports as off-chain 'extra' noise
# and every consumer has to bolt the receiver on by hand. Declaring them here
# (rather than appending to CHAIN) keeps the ordered relay's stage list exact
# while chain_health, the session-start router and doctor pick them up as
# first-class hops. Such chain rows carry `terminal: true`.
CHAIN_TERMINAL = [
    ("bmad-dev-story", "bmad-code-review"),
]

# METHODOLOGY CHAIN: Experiment → IR → Sprint Planning → Story → Quality Record →
# Production Readiness. Not part of the ordered hop relay — stages receive
# hand-offs by prefix attribution (table below) and surface under
# chain_health's 'extra' when they hold waiting signals. stop.py consults
# this set for chain-completion checks (single source of truth).
METHODOLOGY_CHAIN = [
    "bmad-research-experiment",                 # E (Experiment)
    "bmad-check-implementation-readiness",      # IR (Implementation Readiness)
    "bmad-sprint-planning",                     # SP (Sprint Planning)
    "bmad-create-story",                        # S (Story)
    "bmad-quality-record",                      # QR (Quality Record)
    "bmad-production-readiness",                # PR (Production Readiness)
]

# SUB-CHAINS — the phase-based modular relays.
#
# The two canonical ordered relays (CHAIN, METHODOLOGY_CHAIN) are not the whole
# hand-off contract: ideation, the alternative development branches, the QA
# feeders and the governance/retro loops each relay work between stages too.
# Every hop listed here is part of the contract, so its waiting signals MUST
# surface in chain_health/doctor and must never be reported as off-chain
# 'extra' noise (that bucket is reserved for genuinely unknown receivers).
#
# Adding a phase = adding one entry here; chain_health, doctor and the
# session-start/stop relays all follow automatically.
SUB_CHAINS: dict[str, list[tuple[str, str]]] = {
    # Discovery — raw idea to a brief/PRD input (ideation + research fan-in).
    "discovery": [
        ("bmad-forge-idea", "bmad-product-brief"),
        ("bmad-brainstorming", "bmad-product-brief"),
        ("bmad-prfaq", "bmad-prd"),
        ("bmad-product-brief", "bmad-prd"),
        ("bmad-market-research", "bmad-product-brief"),
        ("bmad-domain-research", "bmad-product-brief"),
        ("bmad-technical-research", "bmad-product-brief"),
        # CIS creative/strategy facilitators — brainstorming's siblings: they
        # shape the raw problem, so a session that produced a direction worth
        # building on feeds the brief.
        ("bmad-cis-design-thinking", "bmad-product-brief"),
        ("bmad-cis-innovation-strategy", "bmad-product-brief"),
        ("bmad-cis-problem-solving", "bmad-product-brief"),
        ("bmad-cis-storytelling", "bmad-product-brief"),
    ],
    # Alt-dev — shortcut (quick-dev) and unattended (dev-auto) implementations
    # fan in on the same formal review gate the delivery relay terminates at.
    "alt_dev": [
        ("bmad-quick-dev", "bmad-code-review"),
        ("bmad-dev-auto", "bmad-code-review"),
        # The dev persona implements too (its BRIDGE carries S→QR→PR), so it is
        # the third branch converging on the same formal review gate.
        ("bmad-agent-dev", "bmad-code-review"),
    ],
    # Testing — the TEA toolbox feeds the quality record's mechanical checks
    # (every one of these skills is a registered QR feeder: it adds evidence to
    # an existing QR, it never opens a record of its own), plus TEA Academy
    # routing the learner into the workflow chain it teaches.
    "testing": [
        ("bmad-testarch-atdd", "bmad-quality-record"),
        ("bmad-testarch-automate", "bmad-quality-record"),
        ("bmad-testarch-ci", "bmad-quality-record"),
        ("bmad-testarch-framework", "bmad-quality-record"),
        ("bmad-testarch-nfr", "bmad-quality-record"),
        ("bmad-testarch-test-design", "bmad-quality-record"),
        ("bmad-testarch-test-review", "bmad-quality-record"),
        ("bmad-testarch-trace", "bmad-quality-record"),
        ("bmad-qa-generate-e2e-tests", "bmad-quality-record"),
        # Academy close-out recommends "start with Framework setup": the
        # learner lands in the chain it just studied.
        ("bmad-teach-me-testing", "bmad-testarch-framework"),
    ],
    # Governance — the hops that CLOSE the methodology loop: a retrospective's
    # lessons become the next hypothesis, and a course correction lands on the
    # planning surface that owns the backlog.
    "governance": [
        ("bmad-retrospective", "bmad-research-experiment"),
        ("bmad-correct-course", "bmad-sprint-planning"),
    ],
    # Game Dev Suite (GDS) — the game vertical mirrors the canonical relay
    # shape (ideation fan-in → design docs → readiness → planning → dev →
    # review) with game-native artifacts, and re-uses the same patterns: QA
    # tools feed the playtest record, retro/correct-course re-enter planning,
    # the dev branches converge on the review gate.
    "gds": [
        # ideation & research fan-in on the brief (the discovery pattern)
        ("gds-domain-research", "gds-create-game-brief"),
        ("gds-brainstorm-game", "gds-create-game-brief"),
        ("gds-create-game-brief", "gds-gdd"),
        ("gds-create-narrative", "gds-gdd"),
        # design gates — GDD is primary; a formal PRD is the optional
        # alternative gate; both converge on architecture via UX.
        ("gds-gdd", "gds-ux"),
        ("gds-ux", "gds-game-architecture"),
        ("gds-prd", "gds-game-architecture"),
        # readiness → planning → dev (the IR twin and the planning relay)
        ("gds-game-architecture", "gds-check-implementation-readiness"),
        ("gds-check-implementation-readiness", "gds-sprint-planning"),
        ("gds-sprint-planning", "gds-create-story"),
        ("gds-create-story", "gds-dev-story"),
        ("gds-dev-story", "gds-code-review"),
        # QA feeders — their BRIDGE (already in the team TOMLs) adds evidence
        # to the QR record the review session owns, never a record of their
        # own: the bmad TEA feeder pattern, game-flavored. gds-playtest-plan is
        # the seventh: it plans the playtest and its findings land in the same
        # QR (its BRIDGE says so explicitly), so it signals the review too.
        ("gds-playtest-plan", "gds-code-review"),
        ("gds-test-design", "gds-code-review"),
        ("gds-test-framework", "gds-code-review"),
        ("gds-test-automate", "gds-code-review"),
        ("gds-test-review", "gds-code-review"),
        ("gds-e2e-scaffold", "gds-code-review"),
        ("gds-performance-test", "gds-code-review"),
        # governance — retro/correct-course re-enter planning (the loop,
        # game-flavored; the review gate itself is terminal like its bmad twin)
        ("gds-retrospective", "gds-sprint-planning"),
        ("gds-correct-course", "gds-sprint-planning"),
        # dev-branch variants converge on the same review gate
        ("gds-quick-dev", "gds-code-review"),
        ("gds-agent-game-dev", "gds-code-review"),
        ("gds-agent-game-solo-dev", "gds-code-review"),
        # reporter — live status next to the file-backed plan
        ("gds-sprint-status", "gds-sprint-planning"),
        # a forensic investigation's defect feeds course correction
        ("gds-investigate", "gds-correct-course"),
    ],
    # Web Design Suite (WDS) — Freya's design pipeline: setup seeds the
    # alignment sign-off, the brief flows through trigger mapping and
    # scenarios into UX design, then the numbered build phases run in order;
    # brownfield evolution re-enters at the brief (the WDS loop).
    "wds": [
        ("wds-0-project-setup", "wds-0-alignment-signoff"),
        ("wds-0-alignment-signoff", "wds-1-project-brief"),
        ("wds-1-project-brief", "wds-2-trigger-mapping"),
        ("wds-2-trigger-mapping", "wds-3-scenarios"),
        ("wds-3-scenarios", "wds-4-ux-design"),
        ("wds-4-ux-design", "wds-5-agentic-development"),
        ("wds-5-agentic-development", "wds-6-asset-generation"),
        ("wds-6-asset-generation", "wds-7-design-system"),
        # brownfield re-entry: evolution cycles inform the next brief
        ("wds-8-product-evolution", "wds-1-project-brief"),
    ],
}

# Skills that appear in no hop but are known chain-adjacent surfaces (the help
# router, the infrastructure tools, the loop orchestrator's automation layer,
# the WDS session backends). They post and relay nothing, so the hop relays
# cannot carry them — but they must NOT surface as off-chain 'extra' noise
# when a signal is (mis)addressed to them, and the lint still checks them.
CHAIN_ADJACENT = {
    "bmad-help",           # routes the user; never produces
    "bmad-loop-setup", "bmad-loop-sweep", "bmad-loop-resolve",
    "bmad-bmb-setup", "bmad-module-builder", "bmad-workflow-builder",
    "bmad-customize", "bmad-checkpoint-preview", "bmad-eval-runner",
    "memory", "sync",
    # Vertical tooling — the GDS/WDS personas that only advise (architect,
    # designer, tech-writer, freya/mimir/saga) and the documentation tools;
    # the producing GDS personas (game-dev, solo-dev) are hop members above, as
    # is the playtest-plan feeder (gds.playtest.<slug> → gds-code-review).
    "gds-agent-game-architect", "gds-agent-game-designer",
    "gds-agent-tech-writer", "gds-document-project",
    "gds-generate-project-context",
    "wds-agent-freya-ux", "wds-agent-mimir-builder", "wds-agent-saga-analyst",
}

def sub_chain_skills() -> set:
    """Every skill named by any sub-chain hop (senders and receivers)."""
    return {s for hops in SUB_CHAINS.values() for hop in hops for s in hop}

# Run-key namespace prefix → the skill that owns the hand-off (sender attribution).
_KEY_PREFIX_TO_SKILL = [
    # METHODOLOGY CHAIN prefixes
    ("E-", "bmad-research-experiment"),
    ("IR-", "bmad-check-implementation-readiness"),
    ("SP-", "bmad-sprint-planning"),
    ("S-", "bmad-create-story"),
    ("QR-", "bmad-quality-record"),
    ("PR-", "bmad-production-readiness"),
    # DISCOVERY CHAIN prefixes
    ("forge.", "bmad-forge-idea"),
    ("brainstorm.", "bmad-brainstorming"),
    ("brief.", "bmad-product-brief"),
    ("prfaq.", "bmad-prfaq"),
    ("research.market.", "bmad-market-research"),
    ("research.domain.", "bmad-domain-research"),
    ("research.tech.", "bmad-technical-research"),
    ("cis.design.", "bmad-cis-design-thinking"),
    ("cis.innovation.", "bmad-cis-innovation-strategy"),
    ("cis.solving.", "bmad-cis-problem-solving"),
    ("cis.story.", "bmad-cis-storytelling"),
    ("retro.", "bmad-retrospective"),
    ("change.", "bmad-correct-course"),
    # ALT DEV BRANCH prefixes (alternative implementations of the dev stage)
    ("quickdev.", "bmad-quick-dev"),
    ("devauto.", "bmad-dev-auto"),
    ("agentdev.", "bmad-agent-dev"),
    # TESTING CHAIN prefixes (TEA toolbox + academy — specific prefixes first)
    ("test.atdd.", "bmad-testarch-atdd"),
    ("test.automate.", "bmad-testarch-automate"),
    ("test.ci.", "bmad-testarch-ci"),
    ("test.framework.", "bmad-testarch-framework"),
    ("test.nfr.", "bmad-testarch-nfr"),
    ("test.design.", "bmad-testarch-test-design"),
    ("test.review.", "bmad-testarch-test-review"),
    ("test.trace.", "bmad-testarch-trace"),
    ("test.e2e.", "bmad-qa-generate-e2e-tests"),
    ("teach.", "bmad-teach-me-testing"),
    # GDS prefixes (game vertical — specific before generic)
    ("gds.quickdev.", "gds-quick-dev"),
    ("gds.agentdev.", "gds-agent-game-dev"),
    ("gds.solodev.", "gds-agent-game-solo-dev"),
    ("gds.research.", "gds-domain-research"),
    ("gds.brainstorm.", "gds-brainstorm-game"),
    ("gds.brief.", "gds-create-game-brief"),
    ("gds.narrative.", "gds-create-narrative"),
    ("gds.gdd.", "gds-gdd"),
    ("gds.ux.", "gds-ux"),
    ("gds.prd.", "gds-prd"),
    ("gds.architecture.", "gds-game-architecture"),
    ("gds.ir.", "gds-check-implementation-readiness"),
    ("gds.sp.", "gds-sprint-planning"),
    ("gds.story.", "gds-create-story"),
    ("gds.dev.", "gds-dev-story"),
    ("gds.review.", "gds-code-review"),
    ("gds.test.design.", "gds-test-design"),
    ("gds.test.framework.", "gds-test-framework"),
    ("gds.test.automate.", "gds-test-automate"),
    ("gds.test.review.", "gds-test-review"),
    ("gds.test.e2e.", "gds-e2e-scaffold"),
    ("gds.test.perf.", "gds-performance-test"),
    ("gds.playtest.", "gds-playtest-plan"),
    ("gds.retro.", "gds-retrospective"),
    ("gds.change.", "gds-correct-course"),
    ("gds.status.", "gds-sprint-status"),
    ("gds.investigate.", "gds-investigate"),
    ("gds.", "gds-generic"),
    # WDS prefixes (web design vertical — numbered phases before generic)
    ("wds.setup.", "wds-0-project-setup"),
    ("wds.signoff.", "wds-0-alignment-signoff"),
    ("wds.brief.", "wds-1-project-brief"),
    ("wds.trigger.", "wds-2-trigger-mapping"),
    ("wds.scenario.", "wds-3-scenarios"),
    ("wds.ux.", "wds-4-ux-design"),
    ("wds.dev.", "wds-5-agentic-development"),
    ("wds.asset.", "wds-6-asset-generation"),
    ("wds.designsystem.", "wds-7-design-system"),
    ("wds.evolve.", "wds-8-product-evolution"),
    ("wds.", "wds-generic"),
    ("prd.", "bmad-prd"),
    ("ux.", "bmad-ux"),
    ("architecture.", "bmad-architecture"),
    ("spec.", "bmad-spec"),
    ("epics.", "bmad-create-epics-and-stories"),
    ("story.", "bmad-create-story"),
]

def _skill_for_run_key(key: str) -> str | None:
    """Owning skill for a run-key namespace — the producer side of the same
    table the hand-off diagnostic attributes with (watch-plane stamps and
    canvas owner links must name stages the way chain_health names senders).
    None for keys outside every known namespace (bridge keys, ad-hoc notes)."""
    key = str(key or "").strip()
    for prefix, skill in _KEY_PREFIX_TO_SKILL:
        if key.startswith(prefix):
            return skill
    return None

# A skill token accepted as an explicit signal sender (same shape as the
# channel budget allows). Anything else is ignored — attribution falls back to
# the prefix rather than trusting a free-form string.
_SENDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")

# A run key that names a record (`E-048`, `QR-001`): only these can supersede or
# be superseded, so only these pay the cascade scan on the write path.
_RECORD_KEY_RE = re.compile(r"^[A-Z]+-\d+$")

def _signal_sender(text: str, explicit: str | None = None) -> str:
    """Attribute a hand-off signal to a sender skill.

    An explicit sender (recorded by the poster) wins over the from-key prefix:
    run-key namespaces are shared (`story.` is written by BOTH create-story and
    dev-story), so the prefix alone cannot name the stage that posted. Without
    one, the from-key prefix decides (signal text is '<from-key>: <note>');
    keys outside every known namespace (e.g. a QR record key) attribute to
    'unknown' — they still surface under chain_health's 'extra', just unnamed."""
    if explicit:
        candidate = str(explicit).strip()
        if _SENDER_RE.match(candidate):
            return candidate
    key = text.split(":", 1)[0].strip()
    for prefix, skill in _KEY_PREFIX_TO_SKILL:
        if key.startswith(prefix):
            return skill
    return "unknown"

def chain_health(project_root: str, *, now: float | None = None) -> dict:
    """Per-hop hand-off diagnostics for the delivery relay AND the methodology
    relay: how many signals are waiting (downstream has not picked up) and how
    many were consumed (handshake completed) on every hop — waiting
    sender-attributed from the folded snapshot (poster sender persisted),
    consumed folded into that same board.

    - ``chain``: the ordered delivery relay (CHAIN, 7 skills → 6 hops) plus
      its declared terminal hops (CHAIN_TERMINAL — the review gate that
      consumes the last stage and signals nothing onward; such rows carry
      ``terminal: true``).
    - ``methodology_chain``: the ordered methodology relay (METHODOLOGY_CHAIN,
      6 stages → 5 hops).
    - ``sub_chains``: the phase-based modular relays (SUB_CHAINS), keyed by
      phase name — 'discovery', 'alt_dev', …
    - ``extra``: signals outside every declared relay (unknown receivers only).
      The protocol is extensible, the diagnostic follows.
    - ``stale``: waiting signals older than HANDOFF_SIGNAL_TTL_SECONDS —
      the escalation path stop/session_start surface. Pass ``now`` in tests
      for deterministic age computation.

    One source, one contract (TD-014): ``waiting``, ``stale`` AND ``consumed``
    all fold from the same snapshot skills read via ``pending_handoffs`` /
    ``consume`` — the diagnostic can only see a board the readers can see.
    Snapshot evictions (stale-cleanup, alert caps) and event-log rotation can
    therefore never stage a divergence between the two views.
    """
    ref = now if now is not None else time.time()
    board = read_board(project_root)
    consumed: dict = {}  # (sender, receiver) -> count, from the folded board
    for _sender, _receivers in (board.get("consumed") or {}).items():
        if not isinstance(_receivers, dict):
            continue
        for _receiver, _count in _receivers.items():
            try:
                _n = int(_count)
            except (TypeError, ValueError):
                continue
            if _n:
                consumed[(_sender, _receiver)] = (
                    consumed.get((_sender, _receiver), 0) + _n)
    # Waiting + stale + consumed ALL read the folded board (single source):
    # the same live truth pending_handoffs/consume see. Replay-only
    # diagnostics are gone, so the diagnostic can only see a board the
    # readers can see — evicted or log-rotated history cannot stage a
    # divergence between the two views.
    waiting: dict = {}   # (sender, receiver) -> count
    stale: list = []     # waiting signals past TTL (age-based escalation)
    verdicts: dict = {}  # (sender, receiver) -> {STALE: n, UNKNOWN: n}
    unclear: list = []   # waiting signals the CODE STATE moved past (E-053)
    for sig in _waiting_signals(project_root, board=board):
        receiver = sig["skill"]
        sender = _signal_sender(str(sig.get("text", "")), explicit=sig.get("sender"))
        key = (sender, receiver)
        waiting[key] = waiting.get(key, 0) + 1
        verdict = str(sig.get("verdict") or fr.CURRENT).upper()
        if verdict in (fr.STALE, fr.UNKNOWN):
            verdicts.setdefault(key, {})
            verdicts[key][verdict] = verdicts[key].get(verdict, 0) + 1
            unclear.append({"from": sender, "to": receiver,
                            "text": str(sig.get("text", "")),
                            "verdict": verdict,
                            "reason": str(sig.get("reason", "")),
                            "run_key": str(sig.get("run_key", "")),
                            "age_seconds": int(sig.get("age_s") or 0)})
        try:
            ts = float(sig.get("ts", 0.0) or 0.0)
        except (TypeError, ValueError):
            ts = 0.0
        if ref - ts > HANDOFF_SIGNAL_TTL_SECONDS:
            stale.append({"from": sender, "to": receiver,
                          "text": str(sig.get("text", "")),
                          "age_seconds": int(ref - ts), "verdict": verdict})

    def hop_rows(sender_skill: str, receiver_skill: str) -> dict:
        w = sum(v for (s, r), v in waiting.items() if s == sender_skill and r == receiver_skill)
        c = sum(v for (s, r), v in consumed.items() if s == sender_skill and r == receiver_skill)
        v = verdicts.get((sender_skill, receiver_skill), {})
        if w:
            status = "waiting"
        elif w + c:
            status = "clear"
        else:
            status = "idle"
        # Age and CODE STATE are different questions: `stale` counts the clock,
        # `stale_claims`/`unknown_claims` count the claims the code moved past
        # (and those the anchor cannot settle). A hop can be perfectly fresh by
        # age and already false by state — that was the whole defect (E-052).
        return {"from": sender_skill, "to": receiver_skill,
                "waiting": w, "consumed": c, "status": status,
                "stale_claims": v.get(fr.STALE, 0),
                "unknown_claims": v.get(fr.UNKNOWN, 0)}

    chain = [hop_rows(a, b) for a, b in zip(CHAIN, CHAIN[1:])]
    for a, b in CHAIN_TERMINAL:
        chain.append({**hop_rows(a, b), "terminal": True})
    methodology_chain = [hop_rows(a, b) for a, b in zip(METHODOLOGY_CHAIN, METHODOLOGY_CHAIN[1:])]
    sub_chains = {name: [hop_rows(a, b) for a, b in hops]
                  for name, hops in SUB_CHAINS.items()}
    # NOTE: bmad-create-story is the bridge (present in both ordered relays);
    # its fan-out to quality-record is a METHODOLOGY_CHAIN hop (S->QR). Every
    # declared relay feeds 'seen', so only genuinely unknown receivers land in
    # 'extra'.
    seen = ({(h["from"], h["to"]) for h in chain} |
            {(h["from"], h["to"]) for h in methodology_chain} |
            {(h["from"], h["to"]) for hops in sub_chains.values() for h in hops})
    # Chain-adjacent surfaces (help, infrastructure, the loop orchestrator's
    # automation layer, the WDS backends) declare no hop, but a signal
    # addressed to a KNOWN skill is still first-class diagnostic data — only
    # genuinely unknown receivers land in 'extra'. They report as a single
    # self-hop so their waiting count stays visible without inventing a relay.
    extra: list = []
    for r in sorted(CHAIN_ADJACENT - sub_chain_skills()):
        w = sum(v for (s, rc), v in waiting.items() if rc == r)
        c = sum(v for (s, rc), v in consumed.items() if rc == r)
        if w or c:
            extra.append({"from": "chain-adjacent", "to": r, "waiting": w,
                          "consumed": c, "status": "waiting" if w else "clear"})
    for (s, r) in sorted(set(list(waiting) + list(consumed))):
        if (s, r) in seen:
            continue
        if r in CHAIN_ADJACENT:
            continue  # already reported as a chain-adjacent row above
        w, c = waiting.get((s, r), 0), consumed.get((s, r), 0)
        extra.append({"from": s, "to": r, "waiting": w, "consumed": c,
                      "status": "waiting" if w else "clear"})
    total_waiting = sum(waiting.values())
    return {"ok": True, "chain": chain, "methodology_chain": methodology_chain,
            "sub_chains": sub_chains,
            "extra": extra, "total_waiting": total_waiting, "stale": stale,
            # Freshness dimension (E-053): derived from the same scan, keyed by
            # claim, never stored. `stale` above is the clock; `unclear` is the
            # code state. `retired` counts the claims explicitly withdrawn.
            "unclear": unclear,
            "freshness": fr.tally(unclear),
            "retired": len(board.get("retired") or {}),
            "freshness_mode": fr.mode()}

def waiting_hop_rows(health: dict) -> list:
    """Every hop holding a waiting signal, across ALL declared relays
    (delivery, methodology, every sub-chain) plus the unknown 'extra' bucket.
    One place, so a new sub-chain is picked up by every consumer."""
    rows = [h for h in health.get("chain", []) if h.get("waiting")]
    rows += [h for h in health.get("methodology_chain", []) if h.get("waiting")]
    for hops in (health.get("sub_chains") or {}).values():
        rows += [h for h in hops if h.get("waiting")]
    rows += [h for h in health.get("extra", []) if h.get("waiting")]
    return rows

# --- doctor (one-glance diagnostic) ---------------------------------------------------
_DOCTOR_CAP_WARN_PCT = 90  # warn when a plane is this close to its cap

def _doctor_age(ts: float) -> str:
    """Human age of a timestamp ('2s', '5m', '3h', '6d')."""
    age = int(max(0, time.time() - (ts or 0)))
    if age < 60:
        return f"{age}s"
    if age < 3600:
        return f"{age // 60}m"
    if age < 86400:
        return f"{age // 3600}h"
    return f"{age // 86400}d"

def _doctor_drift(paths: dict, board: dict) -> dict:
    """Snapshot vs event-log replay comparison (tool-stamped keys excluded:
    last_tool.* are event-sourced like everything else; excluded here because
    they are high-churn skill-side touch stamps, not meaningful state for
    drift)."""
    replay = _replay_events(paths)

    def norm(b: dict) -> dict:
        keys = {k: v for k, v in b["keys"].items() if v.get("type") != "tool"}
        return {"keys": keys, "tags": b["tags"], "canvases": b["canvases"],
                "consumed": b.get("consumed", {}),
                "open_handoffs": b.get("open_handoffs", {}),
                "links": b["links"], "subscriptions": b["subscriptions"],
                "alerts": b["alerts"], "hot": b["hot"],
                "hot_canvas": b["hot_canvas"]}

    in_sync = norm(replay) == norm(board)
    return {"in_sync": in_sync, "status": "ok" if in_sync else "warn"}

def rotate_event_log(project_root: str, max_lines: int = 10000) -> dict:
    """Archive old event log entries and start fresh (MEDIUM #1 / ISSUE #60).
    
    Strategy:
    1. Read all events from current log
    2. Rebuild snapshot from all events (to capture current state)
    3. Archive old events to logs/events-YYYY-MM-DD-HHmmss.log.gz
    4. Truncate current event log (fresh start)
    5. Append session_marker to new log
    
    Returns (ok, rotated_file, lines_archived, message).
    """
    paths = board_paths(project_root)
    events_path = pathlib.Path(paths["events"])
    
    if not events_path.exists():
        return {"ok": False, "error": "no event log to rotate"}
    
    try:
        # 1. Read all events
        all_events = []
        with open(events_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        all_events.append(json.loads(line))
                    except (json.JSONDecodeError, ValueError):
                        pass  # Skip corrupted lines
        
        if not all_events or len(all_events) < max_lines:
            return {"ok": False, "message": f"log has {len(all_events)} lines (threshold: {max_lines})"}
        
        # 2. Rebuild snapshot from all events to capture current state
        board_rebuilt = _replay_events(paths)
        
        # 3. Archive old events to timestamped file
        import gzip
        import datetime
        now = datetime.datetime.now()
        archive_name = f"events-{now.strftime('%Y-%m-%d-%H%M%S')}.log.gz"
        archive_path = events_path.parent / archive_name
        
        # Write compressed archive
        with gzip.open(archive_path, "wt", encoding="utf-8") as f:
            for event in all_events:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        
        # 4. Truncate current event log
        with open(events_path, "w", encoding="utf-8") as f:
            pass  # Empty file
        
        # 5. Append session marker to new log (if not already there)
        # This will be done by the next record_session_start()
        
        return {
            "ok": True,
            "archived_to": str(archive_path),
            "lines_archived": len(all_events),
            "message": f"Archived {len(all_events)} events to {archive_name}; event log rotated"
        }
    
    except Exception as e:
        return {
            "ok": False,
            "error": f"rotation failed: {str(e)[:200]}"
        }

def doctor(project_root: str, *, post: bool = False) -> dict:
    """One-glance diagnostic for the whole board: gate, snapshot, event log,
    state counts, cap usage, focus, chain health, FRESHNESS, integrity
    (snapshot/event drift, tmp residue) and watch paths. Verdict is HEALTHY iff
    no warnings.
    
    NEW: Supports --rotate flag to archive old event logs (MEDIUM #1)
    NEW: Includes record ID uniqueness check (MEDIUM #2 / ISSUE #61)
    NEW: Detects stale sessions (SessionStart without Stop) (MEDIUM #5 / ISSUE #64)
    NEW (E-053): reports the derived freshness of every waiting claim, and — when
    `post=True` — PRODUCES the lifecycle alerts the board's taxonomy declares
    (`witness_stale`, `cascade_invalidation`, `stale_session`). The default stays
    read-only: a diagnostic must not mutate the thing it measures.
    """
    stale_session_hours: int | None = None
    paths = board_paths(project_root)
    warnings = []
    board = read_board(project_root)

    # gate
    gate_known, gate_on = True, True
    try:
        from .config import blackboard_enabled
        gate_on = bool(blackboard_enabled())
    except Exception:
        gate_known = False

    # event log
    lines = garbage = 0
    try:
        with open(paths["events"], encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                lines += 1
                try:
                    json.loads(line)
                except ValueError:
                    garbage += 1
        events_exist = lines > 0
    except OSError:
        events_exist = False
    if garbage:
        warnings.append(f"event log carries {garbage} unparseable line(s) "
                        "(tolerated, but investigate the writer)")
    # Rotation hint (MEDIUM #1 / ISSUE #60 surface): _truncate_events_if_needed
    # silently keeps the newest MAX_EVENTS lines at every mutation; the
    # operator-facing surface for "the log hit its bound and history was
    # dropped" is this warning, pointing at `rotate` (archive + fresh log).
    if events_exist and lines >= MAX_EVENTS:
        from .utils import plugin_script_cmd as _plugin_cmd
        warnings.append(f"event log at bound ({lines}/{MAX_EVENTS} lines) — "
                        "oldest events were already dropped by the truncation "
                        f"guard; archive history with "
                        f"`{_plugin_cmd('bmad/scripts/blackboard.py', 'rotate')}`")

    # caps
    cells_per_canvas = {name: len(cv.get("cells", {}))
                        for name, cv in board["canvases"].items()}
    caps = {
        "keys": (len(board["keys"]), MAX_KEYS),
        "tags": (len(board["tags"]), MAX_TAGS),
        "contributions": (len(board["contributions"]), MAX_CONTRIBUTIONS),
        "canvases": (len(board["canvases"]), MAX_CANVASES),
        "cells": (sum(cells_per_canvas.values()), MAX_CELLS * MAX_CANVASES),
        "links": (len(board["links"]), MAX_LINKS),
        "subscriptions": (len(board["subscriptions"]), MAX_SUBSCRIPTIONS),
        "alerts": (len(board["alerts"]), MAX_ALERTS),
    }
    cap_warnings = []
    for name, (used, cap) in caps.items():
        if used * 100 >= _DOCTOR_CAP_WARN_PCT * cap:
            cap_warnings.append(f"{name} {used}/{cap} (≥{_DOCTOR_CAP_WARN_PCT}% — "
                                "oldest will expire soon)")
    warnings.extend(cap_warnings)

    # NEW: Check if snapshot rebuild from events works (corruption recovery)
    replay_for_check = _replay_events(paths)
    skipped = replay_for_check.get("_metadata", {}).get("replay_skipped_lines", 0)
    if skipped > 0:
        warnings.append(f"event log replay skipped {skipped} line(s) during rebuild "
                        "(corruption detected, but auto-recovered by skipping bad lines)")
    
    # NEW: Check for duplicate record IDs (MEDIUM #2 / ISSUE #61)
    # Record IDs live in filenames (E-001.md, IR-002.md, …) scattered across
    # the canonical layout (check-methodology.sh CHECK 2):
    #   E → docs/experiments;  IR/SP/S/PR → docs/development;
    #   S → docs/development/stories;  QR → docs/quality.
    # The old per-type table pointed at directories that don't exist
    # (docs/stories, docs/quality-records, …), so this scan silently never
    # ran. Scanning the real layout catches a same-ID copy under the wrong
    # directory (an archive subdir, a second artifacts tree, the legacy
    # docs/stories location) — the duplicate shapes that actually occur.
    root = os.path.abspath(project_root or os.getcwd())
    seen_ids = {}  # record ID → first directory it was seen in
    scan_dirs = [
        "docs/experiments", "docs/development", "docs/development/stories",
        "docs/quality",
    ]

    for rec_dir in scan_dirs:
        rec_path = pathlib.Path(root) / rec_dir
        if not rec_path.exists():
            continue

        for record_file in rec_path.glob("*.md"):
            match = re.match(r"^([A-Z]+-\d+)\.md$", record_file.name)
            if not match:
                continue
            record_id = match.group(1)

            if record_id in seen_ids:
                warnings.append(
                    f"Duplicate record ID '{record_id}' detected in {rec_dir} "
                    f"(first seen in {seen_ids[record_id]}; record IDs must be "
                    f"globally unique per type)"
                )
            else:
                seen_ids[record_id] = rec_dir
    
    # NEW: Detect stale sessions (MEDIUM #5 / ISSUE #64)
    # Look for SessionStart without corresponding Stop in recent history
    try:
        last_session_start = None
        last_session_stop = None
        last_session_id = ""
        session_count = 0
        current_session_id = ""
        
        with open(paths["events"], "r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                
                entry_type = entry.get("type", "")
                if entry_type == "session_marker":
                    if entry.get("hook_event") == "SessionStart":
                        last_session_start = entry.get("ts", 0.0)
                        last_session_id = entry.get("session_id", "")
                        current_session_id = last_session_id
                        session_count += 1
                
                elif entry_type == "session_stop":
                    last_session_stop = entry.get("ts", 0.0)
        
        # Check if there's an unclosed session
        if last_session_start and (last_session_stop is None or last_session_start > last_session_stop):
            session_age = time.time() - last_session_start
            stale_threshold = 3600  # 1 hour
            
            if session_age > stale_threshold:
                hours = int(session_age / 3600)
                stale_session_hours = hours
                warnings.append(
                    f"Stale session detected: SessionStart {hours}h+ ago without Stop "
                    f"(hung session? agent crash?)"
                )
        
        # NEW: Check multi-session isolation (MEDIUM #6 / ISSUE #65)
        # If more than one session, track session boundaries
        if session_count > 1:
            warnings.append(
                f"Multi-session log detected: {session_count} sessions tracked "
                f"(use session_id for isolation). Current: {current_session_id[:12]}..."
            )
    except Exception:
        pass  # Can't check session history, skip

    # integrity: snapshot presence + drift
    snapshot_exists = os.path.exists(paths["snapshot"])
    drift = _doctor_drift(paths, board)
    if not drift["in_sync"]:
        warnings.append("snapshot drifts from the event log — delete "
                        "blackboard.json to force a rebuild")

    # tmp residue (crash leftovers, snapshot dir + event-log dir)
    tmp_files = []
    try:
        for d in (paths["dir"], os.path.dirname(paths["events"])):
            if os.path.isdir(d):
                tmp_files.extend(os.path.join(d, f) for f in os.listdir(d)
                                 if f.endswith(".tmp"))
    except OSError:
        pass
    if tmp_files:
        warnings.append(f"{len(tmp_files)} leftover .tmp file(s) under "
                        f"{paths['dir']} — safe to delete")

    # chain (delivery + methodology relays + every sub-chain).
    # A waiting baton is the designed post-close state, NOT a defect: a close
    # that hands off downstream always leaves one unclaimed signal, so counting
    # it into the verdict made every healthy close read NEEDS ATTENTION (real
    # opencode session, 2026-09-30). It is surfaced three ways instead: the
    # informational warnings list (read-only report), checks.chain (status:
    # "signal" with its own verdict), and the skill close-step text that names
    # unclaimed signals explicitly. Waiting-signal warnings therefore do NOT
    # poison the verdict — only genuine health findings do. Stale (> TTL) stays
    # a health warning: a >24h baton means a crashed/hung skill.
    chain = chain_health(project_root)
    chain_warnings: list = []  # signal-state warnings — informational, not health
    if chain["total_waiting"] > 0:
        waiting_hops = [f"{h['from']}->{h['to']}" for h in waiting_hop_rows(chain)]
        chain_warnings.append(f"{chain['total_waiting']} unclaimed hand-off signal(s) "
                              f"({', '.join(waiting_hops)}) -- PROACTIVE, see chain-health")
    
    # Stale handoff escalation: folded-snapshot ages (TD-014 — the snapshot
    # is the live truth skills can claim; a log-only age stages a
    # divergence, not staleness). Oldest-first so operators triage the
    # longest-waiting baton first. The diagnosis is split by DERIVED verdict
    # (E-053): a >24h baton whose claim still holds is a crashed/hung skill,
    # while one whose scope changed is obsolete WORK — the engine used to call
    # both "hung skill" and so mis-prescribed the remedy for the second.
    stale = chain.get("stale", [])
    if stale:
        stale_sorted = sorted(stale, key=lambda s: -s.get("age_seconds", 0))
        oldest = stale_sorted[0]
        obsolete = [s for s in stale
                    if str(s.get("verdict", "")).upper() == fr.STALE]
        if obsolete:
            warnings.append(
                f"{len(stale)} STALE hand-off signal(s) (> 24h old) — "
                f"{len(obsolete)} name decisions the code has already moved "
                f"past (obsolete work, not a hung skill); oldest: "
                f"{oldest['from']}->{oldest['to']} — retire them "
                f"(`handoff --retire <key> --reason \"…\"`) or re-post, then "
                f"consume the channel")
        else:
            warnings.append(f"{len(stale)} STALE hand-off signal(s) "
                            f"(> 24h old, claims still current) -- skill "
                            f"crashed/hung? oldest: {oldest['from']}->"
                            f"{oldest['to']} -- claim via handoffs --skill "
                            f"<downstream>, then consume")

    # Focus liveness: a hot key with a finished bridge `status` is the stale
    # hand-off this engine used to hand bmad-help (2026-09-21 real session:
    # status="complete" while hot="ux.mailjs" and discovery in flight).
    # write_key now corrects exactly this on the hot write, so the warning
    # catches boards written before that (or by a hand-edited event log).
    # An unset status is no claim at all — never warn on it.
    bridge_entry = board["keys"].get("status")
    bridge_status = str(bridge_entry.get("value", "")).strip() \
        if isinstance(bridge_entry, dict) else ""
    focus_stale = bool(board.get("hot")) and bridge_status.lower() in (
        "complete", "done")
    if focus_stale:
        warnings.append(
            f"focus says '{bridge_status}' but '{board['hot']}' is hot — the "
            f"live run never marked itself in-progress (write `--key status "
            f"--value in-progress`); readers route on status"
        )

    # Blocked-run tag (blackboard full-integration P1): `blocked` is the
    # stalls-the-run marker from the tag vocabulary (GUIDE §14). A board
    # carrying it names its hot run so the operator triages the stall
    # instead of re-deriving state. Informational severity stays with the
    # verdict logic (any warning → NEEDS ATTENTION).
    if "blocked" in (board.get("tags") or []):
        _hot_name = board.get("hot") or "(no hot focus)"
        warnings.append(
            f"board tagged 'blocked' — run '{_hot_name}' is stalled; "
            f"clear with `untag --tag blocked` once it moves"
        )

    # Graph orphans (blackboard full-integration, informational only): a link
    # endpoint with no key or canvas on the board. A forward edge legitimately
    # precedes its downstream run key (the run lands at the next close-out),
    # so — like waiting batons (SIGNAL) — this never touches the verdict; it
    # names the endpoints so the operator can tell a pending downstream apart
    # from a mis-slugged key (e.g. a literal `<slug>` pasted from a template).
    # Bounded to keep doctor bounded.
    _nodes = set(board["keys"])
    _nodes.update(board["canvases"])
    _nodes.update(f"canvas:{n}" for n in board["canvases"])
    _orphans: list = []
    for _l in board["links"]:
        for _end in (_l.get("a"), _l.get("b")):
            if _end and _end not in _nodes and _end not in _orphans:
                _orphans.append(_end)

    # watch paths (informational: missing prefixes may be created later)
    watch = [{"canvas": name, "path": w, "exists": os.path.exists(w)}
             for name, cv in board["canvases"].items()
             for w in cv.get("watch", [])]

    # methodology relay progress (informational: which E→IR→SP→S→QR→PR
    # stages stamped their run key — empty = relay not started on this board)
    methodology = {}
    for stage, key in (("E", "methodology.last_experiment"),
                       ("IR", "methodology.last_ir"),
                       ("SP", "methodology.last_sp"),
                       ("S", "methodology.last_story"),
                       ("QR", "methodology.last_qr"),
                       ("PR", "methodology.last_pr")):
        entry = board["keys"].get(key)
        if entry and isinstance(entry, dict) and str(entry.get("value", "")).strip():
            methodology[stage] = str(entry.get("value"))[:80]

    # Freshness plane (E-053): the DERIVED verdict of every waiting claim, plus
    # the claims explicitly withdrawn. Informational by itself — a signal whose
    # scope moved is report-only noise until it also ages out (the health case
    # above). This is also the reader that turns the declared-but-dead alert
    # taxonomy into produced data: doctor --post emits the lifecycle kinds.
    freshness_report = {}
    unclear = chain.get("unclear", [])
    retired_count = len(board.get("retired") or {})
    try:
        freshness_report = pending_handoff_freshness(project_root)
    except Exception:
        freshness_report = {}
    fresh_tally = fr.tally(unclear)
    # `[freshness] unknown_policy = "silent"` keeps "unverifiable" out of the
    # warnings (a legacy corpus has nothing else to report) while the tally
    # keeps counting it — the fact is never lost, only not repeated.
    report_unknown = fr.unknown_policy() != "silent"
    if fresh_tally[fr.STALE] or (fresh_tally[fr.UNKNOWN] and report_unknown):
        counts = f"{fresh_tally[fr.STALE]} stale"
        tail = ""
        if report_unknown:
            counts += f", {fresh_tally[fr.UNKNOWN]} unknown"
            tail = " or cannot be anchored"
        chain_warnings.append(
            f"{len(unclear)} waiting signal(s) name decisions the code has "
            f"moved past{tail} ({counts}) — verify, then retire or re-post "
            f"(checks.freshness.inject)")
    stale_verdicts = [s for s in stale
                      if str(s.get("verdict", "")).upper() == fr.STALE]

    checks = {
        "gate": {"known": gate_known, "on": gate_on,
                 "status": "ok" if gate_known else "info"},
        "snapshot": {"exists": snapshot_exists,
                     "version": board.get("version"),
                     "age": _doctor_age(board.get("updated", 0.0)),
                     "status": "ok"},
        "events": {"exists": events_exist, "lines": lines, "garbage": garbage,
                   "status": "ok" if not garbage else "warn"},
        "caps": {"usage": {k: {"used": u, "cap": c}
                           for k, (u, c) in caps.items()},
                 "status": "ok" if not cap_warnings else "warn"},
        "focus": {"hot": board.get("hot"), "hot_canvas": board.get("hot_canvas"),
                  "bridge_status": bridge_status,
                  "status": "warn" if focus_stale else "ok"},
        "drift": drift,
        "residue": {"tmp_files": tmp_files,
                    "status": "ok" if not tmp_files else "warn"},
        "chain": {"total_waiting": chain["total_waiting"],
                  "waiting_hops": waiting_hop_rows(chain),
                  "stale": chain.get("stale", []),
                  # "signal" = waiting batons exist (designed post-close state,
                  # announce-only); "warn" = STALE ones exist (crashed skill).
                  "verdict": ("HEALTHY" if not chain_warnings and not stale
                              else "NEEDS ATTENTION" if stale else "SIGNAL"),
                  "status": ("ok" if not chain_warnings and not stale
                             else "warn" if stale else "signal")},
        "watch": {"paths": watch, "status": "info" if watch else "ok"},
        "graph": {"edges": len(board["links"]), "orphans": _orphans[:10],
                  "status": "info"},
        "methodology": {"progress": methodology,
                        "status": "info" if methodology else "ok"},
        "freshness": {"mode": fr.mode(),
                      "witnesses": len(board.get("witness") or {}),
                      "retired": retired_count,
                      "report": freshness_report,
                      "unclear": unclear[:10],
                      "tally": fresh_tally,
                      # A >24h signal that is ALSO stale by code state is a
                      # health finding (its remedy is retire/re-post, not
                      # "restart the skill"); otherwise freshness is info.
                      "status": ("warn" if stale_verdicts
                                 else "info" if unclear else "ok")},
    }
    report = {"ok": True, "root": paths["root"],
              "verdict": "HEALTHY" if not warnings else "NEEDS ATTENTION",
              "checks": checks,
              "warnings": warnings,
              # Signal-state findings (waiting batons): report-only, never part
              # of the health verdict. The close-step contract surfaces these
              # alongside the verdict instead of reading NEEDS ATTENTION as
              # "the close failed".
              "signal_warnings": chain_warnings}
    if post:
        report["posted"] = _post_lifecycle_alerts(project_root, unclear,
                                                  stale_session_hours)
    return report

def _post_lifecycle_alerts(project_root: str, unclear: list,
                           stale_session_hours: int | None) -> int:
    """Produce the lifecycle alerts doctor detected (bounded, deduped, fail-open).

    Split out so the default diagnostic stays read-only: only an explicit
    `doctor --post` writes. Every kind emitted here already has a consumer —
    the session-start inject and `alerts --channel session` — which is what
    makes the board's declared taxonomy a working plane instead of dead names
    (E-053, F-06). Bounded to 5 signals so a flooded board cannot evict real
    batons from the shared 32-slot alert pool.
    """
    posted = 0
    try:
        for sig in (unclear or [])[:5]:
            if str(sig.get("verdict", "")).upper() != fr.STALE:
                continue
            kind = (AlertKind.CASCADE_INVALIDATION.value
                    if "superseded by" in str(sig.get("reason", ""))
                    else AlertKind.WITNESS_STALE.value)
            post_alert(
                project_root, "session", kind,
                f"{sig.get('run_key') or sig.get('from')}: {sig.get('reason')} "
                f"(waiting for {sig.get('to')}, "
                f"{int(sig.get('age_seconds') or 0) // 3600}h) — retire or re-post",
                sender="doctor")
            posted += 1
        if stale_session_hours:
            post_alert(
                project_root, "session", AlertKind.STALE_SESSION.value,
                f"stale session: SessionStart {stale_session_hours}h+ ago "
                f"without Stop (hung session? agent crash?)", sender="doctor")
            posted += 1
    except Exception:
        pass
    return posted

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
from .blackboard_fold import (  # noqa: E402
    _apply_event, _cap_auto_cells, _cap_canvases, _cap_consumed, _cap_keys,
    _cap_open_handoffs, _cap_retired, _cap_witness, _cleanup_stale_handoffs,
    _clear_hot_if_dangling,
)

"""Workflow state store — self-contained, atomic, event-sourced, fail-open.

The kernel owns its storage so it does not depend on any legacy subsystem:
one JSON state file per run, one append-only event log per project, an
exclusive advisory lock around every mutation, and atomic replace on write.
A crashed or concurrent writer can never truncate state, and a missing or
corrupt file degrades to "empty" rather than raising into the caller.

Layout (project-relative)::

    .metodoloji/workflow/
        specs/<slug>.json      # the workflow definition, versioned with the project
        <slug>.state.json      # current run state (single source of truth)
        events.jsonl           # append-only audit of every transition
        workflow.lock          # advisory lock (best-effort)
"""

from __future__ import annotations

import json
import os
import time

_DEFAULT_DIR = os.path.join(".metodoloji", "workflow")


def paths(project_root: str) -> dict:
    base = os.path.join(project_root, _DEFAULT_DIR)
    return {
        "base": base,
        "specs": os.path.join(base, "specs"),
        "events": os.path.join(base, "events.jsonl"),
        "lock": os.path.join(base, "workflow.lock"),
    }


def state_path(project_root: str, slug: str) -> str:
    """The run's state file — THE one path every reader must agree on (E-008).

    The slug is sanitized to [alnum._-] (everything else becomes `_`) so a
    slug with a space or slash cannot escape the base dir. The corrupt-state
    detector must build its probe path with THIS function: a raw-slug probe
    checks a file that never exists, so a corrupt run is reported as missing
    (`no workflow ...`) while `list` names it corrupt.
    """
    safe = "".join(ch if (ch.isalnum() or ch in "._-") else "_" for ch in slug)
    return os.path.join(paths(project_root)["base"], f"{safe}.state.json")


# Internal name kept: read_state/write_state and any existing readers use it.
_state_path = state_path


# --- locking (best-effort, cross-platform, fail-open) -------------------------

def _acquire_lock(lock_path: str):
    try:
        os.makedirs(os.path.dirname(lock_path), exist_ok=True)
        f = open(lock_path, "a+")
    except OSError:
        return None
    try:
        import fcntl
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        return f
    except ImportError:
        pass
    except OSError:
        f.close()
        return None
    try:
        import msvcrt
    except ImportError:
        f.close()
        return None
    f.seek(0)
    for attempt in range(100):
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            return f
        except OSError:
            time.sleep(min(0.01 * (2 ** attempt), 0.1))
    f.close()
    return None  # fail-open: proceed unguarded (atomic replace still protects)


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
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except (ImportError, OSError):
                pass
    finally:
        try:
            f.close()
        except OSError:
            pass


# --- atomic IO ----------------------------------------------------------------

def _atomic_write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)


def _append_event(project_root: str, event: dict) -> None:
    p = paths(project_root)
    os.makedirs(p["base"], exist_ok=True)
    try:
        with open(p["events"], "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError:
        pass  # fail-open: the audit trail must never block a transition


# --- public API ---------------------------------------------------------------

def read_state(project_root: str, slug: str) -> dict:
    """The run's state, or {} when missing/corrupt (fail-open)."""
    try:
        with open(_state_path(project_root, slug), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_state(project_root: str, slug: str, state: dict) -> dict:
    """Persist the run state atomically and append one audit event."""
    lock = _acquire_lock(paths(project_root)["lock"])
    try:
        state = dict(state)
        state["updated_at"] = time.time()
        _atomic_write(_state_path(project_root, slug), json.dumps(state, ensure_ascii=False, indent=1))
        _append_event(project_root, {
            "ts": time.time(), "event": "state", "slug": slug,
            "status": state.get("status"), "current": state.get("current"),
            "completed": state.get("completed", []),
        })
    finally:
        _release_lock(lock)
    return state


def list_slugs(project_root: str) -> list[str]:
    base = paths(project_root)["base"]
    try:
        names = os.listdir(base)
    except OSError:
        return []
    return sorted(n[: -len(".state.json")] for n in names if n.endswith(".state.json"))


def save_spec(project_root: str, spec: dict) -> str:
    """Copy a workflow definition into the project so the run is reproducible."""
    dest = os.path.join(paths(project_root)["specs"], f"{spec['id']}.json")
    lock = _acquire_lock(paths(project_root)["lock"])
    try:
        _atomic_write(dest, json.dumps(spec, ensure_ascii=False, indent=2))
    finally:
        _release_lock(lock)
    return dest


def load_spec(project_root: str, slug: str) -> dict:
    """Read the project-local spec copy from {slug} or from the state's spec id."""
    base = paths(project_root)["specs"]
    for candidate in (os.path.join(base, f"{slug}.json"),):
        try:
            with open(candidate, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
    state = read_state(project_root, slug)
    spec_file = state.get("spec_file")
    if spec_file:
        try:
            with open(spec_file, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def read_events(project_root: str, slug: str | None = None, limit: int = 100) -> list[dict]:
    try:
        with open(paths(project_root)["events"], encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return []
    out: list[dict] = []
    for line in lines[-max(1, limit):]:
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if slug and ev.get("slug") != slug:
            continue
        out.append(ev)
    return out

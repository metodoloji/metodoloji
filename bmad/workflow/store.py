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

from .paths import outside, root_of

_DEFAULT_DIR = os.path.join(".metodoloji", "workflow")


def safe_slug(slug: str) -> str:
    """The ONE sanitizer every filename built from a slug must go through.

    A slug reaches the kernel from the CLI (or from the spec id), so it must be
    treated as a string the operator wrote, not as a path. Letters, digits and
    `.` `_` `-` survive; every separator or drive marker becomes `_` — which is
    what makes a traversal impossible rather than merely unlikely, since a `..`
    is then left with no separator to follow.

    E-008 extracted this rule for the STATE file; E-062 found the spec path and
    the stored `spec_file` still building filenames from raw input, so the rule
    now has one name and every caller uses it (a second copy is how the two
    paths disagreed in the first place).
    """
    return "".join(ch if (ch.isalnum() or ch in "._-") else "_" for ch in str(slug))


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

    The slug is sanitized through :func:`safe_slug` so a slug with a space or
    slash cannot escape the base dir. The corrupt-state detector must build its
    probe path with THIS function: a raw-slug probe checks a file that never
    exists, so a corrupt run is reported as missing (`no workflow ...`) while
    `list` names it corrupt.
    """
    return os.path.join(paths(project_root)["base"], f"{safe_slug(slug)}.state.json")


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
    """The run's state, or {} when missing/corrupt (fail-open).

    Undecodable bytes count as corrupt (E-009): UnicodeDecodeError is a
    ValueError but NOT a JSONDecodeError, so a binary state file used to escape
    this handler and traceback — `state_is_corrupt` (the honest detector)
    reports such a file as CORRUPT, never as "no such workflow".
    """
    try:
        with open(_state_path(project_root, slug), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
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
    """Copy a workflow definition into the project so the run is reproducible.

    The id is already refused outside `[a-z0-9._-]` by `spec.validate`, so this
    sanitization is the same belt-and-braces E-008 asked of the state path: the
    file this writes must be reachable by the sanitized name every reader uses.
    """
    dest = os.path.join(paths(project_root)["specs"], f"{safe_slug(spec['id'])}.json")
    lock = _acquire_lock(paths(project_root)["lock"])
    try:
        _atomic_write(dest, json.dumps(spec, ensure_ascii=False, indent=2))
    finally:
        _release_lock(lock)
    return dest


def load_spec(project_root: str, slug: str) -> dict:
    """Read the run's spec — from the project's own copy, and only from inside it.

    Two candidates, both confined to the project root (E-062): the spec file the
    slug names, and the `spec_file` the state recorded. The slug goes through
    :func:`safe_slug` first — E-008 sanitized the state path but this one joined
    the RAW slug, so `../../../../loaded` used to read a JSON file beside the
    project and hand it to every read surface as the run's workflow definition.
    The stored path is checked, not sanitized: it is a real path the kernel wrote
    itself, so what matters is that it still resolves inside the root (an older
    tree, or a hand edit, can leave one that does not).

    Fail-open stays fail-open: an unresolvable spec is `{}`, which the callers
    already report as "no spec" — the refusal names the missing spec, and nothing
    outside the project is ever read.
    """
    base = paths(project_root)["specs"]
    root_real = root_of(project_root)
    for candidate in (os.path.join(base, f"{safe_slug(slug)}.json"),):
        if outside(root_real, candidate):
            continue
        try:
            with open(candidate, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
    state = read_state(project_root, slug)
    spec_file = state.get("spec_file")
    if spec_file and not outside(root_real, str(spec_file)):
        try:
            with open(spec_file, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            pass
    return {}


def read_events(project_root: str, slug: str | None = None, limit: int = 100) -> list[dict]:
    try:
        with open(paths(project_root)["events"], encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except (OSError, UnicodeDecodeError):
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

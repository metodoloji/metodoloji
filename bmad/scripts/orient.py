#!/usr/bin/env python3
"""One-command orientation digest — the small, stable context skills start from.

Why this exists: in a real session (2026-09-21, /root/mailjs, OpenHands) the
agent spent most of a run re-deriving state instead of working. It read
`skills/bmad-help/SKILL.md` 7× (the activation body arrived truncated), called
`resolve_config.py` 10× (the full dump arrived truncated/garbled, so it re-ran
"clean"), read the board 8×, the catalog 5×, catted a python binary while
looking for the interpreter, and searched the filesystem for
{metodoloji-root} — which the SessionStart line had already printed. Every
answer it needed was in three separate large dumps.

This script is the single small answer: plugin root, resolved paths, board
focus + liveness, waiting batons with age, record inventory, skeleton/gate
state and the catalog's ambiguous menu codes — one call, bounded output, no
filesystem hunting. bmad-help reads this first; `--json` serves tooling.

Read-only by construction: it never writes board events, never consumes a
hand-off.
"""

import argparse
import csv
import datetime as _dt
import importlib.util
import json
import os
import pathlib
import re
import sys
import time

_SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent
_PLUGIN_ROOT = _SCRIPTS_DIR.parent.parent


def _load(name: str, path: pathlib.Path):
    """Load a sibling script by path (the repo's own import convention)."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_skeleton = _load("orient_skeleton", _SCRIPTS_DIR / "skeleton.py")
_resolve_config = _load("orient_resolve_config", _SCRIPTS_DIR / "resolve_config.py")


def _state():
    """The engine's state module — the one source for records + sprint facts.

    Loaded lazily by path (same convention as _load above) so orient.py keeps
    working standalone. record_inventory() here used to duplicate that parser;
    two parsers of the same tree is how "the digest said X, the session line
    said Y" starts.
    """
    return _load("orient_state", _PLUGIN_ROOT / "hooks" / "engine" / "modules" / "state.py")


def _plan():
    """The engine's plan module (GRP) — None when it cannot be imported.

    Read-only surfacing (E-068): open Implementation Plans feed the board peek
    and build() so a resumed multi-file operation sees what its plan has left.
    The module owns the formatter, so this digest, the SessionStart context and
    the Stop report print the same sentence by construction.
    """
    try:
        sys.path.insert(0, str(_PLUGIN_ROOT / "hooks" / "engine"))
        from modules import plan as plan_mod  # noqa: PLC0415
        return plan_mod
    except Exception:
        return None

# Canonical record homes (check-methodology.sh CHECK 2 layout): one place to
# count what actually exists, instead of the `ls`/`find` hunts the session did.
RECORD_DIRS = {
    "E": ("docs/experiments",),
    "IR": ("docs/development",),
    "SP": ("docs/development",),
    "S": ("docs/development/stories",),
    "QR": ("docs/quality", "docs/development"),
    "PR": ("docs/development",),
}
_MAX_LISTED = 6
_MAX_ARTIFACT_LINES = 6
_PREVIEW_CHARS = 90
# docs_overview bounds (2026-09-25 LIMX session: the agent ran ~70 manual
# ls/find/wc/git probes because the digest said nothing about docs/ — the
# task's actual work area was docs/arge, a non-record tree orient.py never
# mentioned, so the agent concluded "no state" and re-derived everything by
# hand, then re-read README 10x and v0.2 9x because no sizes planned the
# reads). One bounded, read-only scan answers all of it: subdir inventory +
# largest text files with sizes, so windowed reads can be planned upfront.
_MAX_DOCS_SUBDIRS = 12
_MAX_DOCS_FILES = 12
_MAX_DOCS_WALK_FILES = 3000


def _age(seconds: float) -> str:
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _resolve_tokens(value: str, project_root: pathlib.Path, output_folder: str) -> str:
    """Expand the two ${...} tokens the catalog/config use in path values."""
    text = str(value)
    text = text.replace("{project-root}", str(project_root))
    text = text.replace("{output_folder}", output_folder)
    return text


def _engine():
    """The blackboard engine (None when it cannot be imported — fail-open)."""
    try:
        sys.path.insert(0, str(_PLUGIN_ROOT / "hooks" / "engine"))
        from modules import blackboard as bb  # noqa: PLC0415
        return bb
    except Exception:
        return None


def catalog_digest() -> dict:
    """Catalog facts a router needs without reading the whole CSV."""
    path = _PLUGIN_ROOT / "bmad" / "_config" / "bmad-help.csv"
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
    except OSError:
        return {"path": str(path), "available": False}
    header, data = rows[0], rows[1:]
    by_code: dict = {}
    skills = []
    required = []
    modules = []
    for row in data:
        if len(row) != len(header):
            continue
        module, skill, display, code = row[0], row[1], row[2], row[3]
        if module not in modules:
            modules.append(module)
        if skill == "_meta":
            continue
        if code:
            by_code.setdefault(code, []).append(f"{module}/{skill}")
        entry = {"module": module, "skill": skill, "display": display,
                 "menu_code": code, "phase": row[7], "required": row[10] == "true",
                 "output_location": row[11], "outputs": row[12]}
        skills.append(entry)
        if entry["required"]:
            required.append(entry)
    # A menu code shared by two skills is the collision that makes a bare
    # "[SP]"/"[CS]" ambiguous (SP = bmad-spec AND bmad-sprint-planning): the
    # router must qualify those with the module.
    ambiguous = {code: owners for code, owners in sorted(by_code.items())
                 if len(owners) > 1}
    return {"path": str(path), "available": True, "modules": modules,
            "skill_count": len(skills), "ambiguous_menu_codes": ambiguous,
            "required": [{"skill": e["skill"], "display": e["display"],
                          "menu_code": e["menu_code"], "phase": e["phase"],
                          "output_location": e["output_location"],
                          "outputs": e["outputs"]} for e in required],
            "skills": skills}


def record_inventory(project_root: pathlib.Path) -> dict:
    """Count the E→IR→SP→S→QR→PR records that exist (newest first per type).

    Delegates to the engine's state module — the single parser shared with the
    session-start inject and stop's story scan. The fallback here keeps the
    digest alive when the engine tree is missing (standalone CLI use).
    """
    try:
        return _state().record_inventory(project_root)
    except Exception:
        inventory: dict = {}
        for kind, dirs in RECORD_DIRS.items():
            # Per-kind pattern: a shared `^(E|IR|…)-N.md$` would count every
            # SP-026.md once per kind (the first draft reported IR=52, QR=85).
            # The slug suffix is accepted: methodology records carry
            # title-derived names (`E-056-gate-heading-guard.md`).
            record_re = re.compile(rf"^{kind}-\d+(?:-[A-Za-z0-9-]+)?\.md$")
            found = []
            for rel in dirs:
                base = project_root / rel
                if not base.is_dir():
                    continue
                for candidate in base.glob("*.md"):
                    if record_re.match(candidate.name):
                        found.append((candidate.stat().st_mtime, rel, candidate.name))
            found.sort(reverse=True)
            if found:
                inventory[kind] = {
                    "count": len(found),
                    "newest": found[0][2],
                    "dirs": sorted({rel for _ts, rel, _n in found}),
                }
        return inventory


def artifact_dirs(project_root: pathlib.Path, output_folder: str, config: dict) -> dict:
    """Top-level entries of the resolved output folders (no deep recursion)."""
    wanted = {"output_folder": output_folder}
    for module, section in (config.get("modules") or {}).items():
        if not isinstance(section, dict):
            continue
        for key in ("planning_artifacts", "implementation_artifacts",
                    "project_knowledge", "test_artifacts", "design_artifacts"):
            if isinstance(section.get(key), str):
                wanted[f"{module}.{key}"] = _resolve_tokens(
                    section[key], project_root, output_folder)
    out = {}
    seen_paths: dict = {}
    for label, raw in wanted.items():
        path = pathlib.Path(_resolve_tokens(raw, project_root, output_folder))
        resolved = str(path)
        # Every module inherits core's paths, so the same folder arrives under
        # several labels — print each folder once, naming the first label.
        if resolved in seen_paths:
            out[label] = dict(out[seen_paths[resolved]], alias_of=seen_paths[resolved])
            continue
        seen_paths[resolved] = label
        if not path.is_dir():
            out[label] = {"path": resolved, "exists": False}
            continue
        names = sorted(p.name for p in path.iterdir())
        out[label] = {"path": resolved, "exists": True,
                      "entries": names[:_MAX_LISTED], "entry_count": len(names)}
    return out


def docs_overview(project_root: pathlib.Path) -> dict:
    """Bounded docs/ inventory: subdir file counts + largest text files.

    Read-only and fail-open. Covers NON-record trees (docs/arge, docs/design,
    …) the record inventory never mentions — the exact blind spot that sent
    the LIMX session into ~70 manual ls/find/wc probes. Sizes let the agent
    plan windowed reads (offset/limit) instead of re-catting a 15 MB file.
    """
    docs = project_root / "docs"
    if not docs.is_dir():
        return {"available": False}
    try:
        subdirs: list[dict] = []
        for entry in sorted(docs.iterdir(), key=lambda p: p.name):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            count = 0
            truncated = False
            try:
                for _root, _dirs, files in os.walk(entry):
                    _dirs[:] = [d for d in _dirs
                                if d not in (".git", "node_modules", "__pycache__")]
                    count += len(files)
                    if count >= _MAX_DOCS_WALK_FILES:
                        truncated = True
                        break
            except OSError:
                continue
            subdirs.append({"name": entry.name, "files": count,
                            "truncated": truncated})
            if len(subdirs) >= _MAX_DOCS_SUBDIRS:
                break
        biggest: list[tuple[int, str]] = []
        try:
            for cand in docs.rglob("*"):
                if len(biggest) > 4000:
                    break
                try:
                    if cand.is_file() and cand.suffix.lower() in (
                            ".md", ".txt", ".mmd", ".yaml", ".yml"):
                        biggest.append((cand.stat().st_size,
                                        str(cand.relative_to(project_root))))
                except OSError:
                    continue
        except OSError:
            pass
        biggest.sort(reverse=True)
        files = [{"path": rel, "bytes": size} for size, rel in biggest[:_MAX_DOCS_FILES]]
        return {"available": True, "path": str(docs),
                "subdirs": subdirs, "largest": files}
    except Exception:
        return {"available": False}


def handoff_digest(project_root: pathlib.Path) -> dict:
    """Waiting batons with age + preview — read-only (never consumed)."""
    bb = _engine()
    if bb is None:
        return {"available": False}
    try:
        counts = bb.pending_handoff_channels(str(project_root))
    except Exception:
        return {"available": False}
    now = time.time()
    waiting = {}
    for skill, count in sorted(counts.items()):
        try:
            signals = bb.pending_handoffs(str(project_root), skill)
        except Exception:
            signals = []
        oldest = min((float(s.get("ts", 0.0) or 0.0) for s in signals), default=0.0)
        text = str(signals[0].get("text", ""))[:_PREVIEW_CHARS] if signals else ""
        waiting[skill] = {"count": count, "oldest_age": _age(now - oldest) if oldest else "?",
                          "preview": text,
                          "peek": f"blackboard.py handoffs --skill {skill}"}
    return {"available": True, "waiting": waiting, "total": sum(counts.values())}


def install_digest() -> dict:
    """Installed copies of the plugin besides this checkout (duplicate hunt).

    Read-only and fail-open. A real OpenHands session (2026-09-25, LIMX) met
    TWO installs — ``~/.openhands/plugins/installed/metodoloji`` and a
    versioned ``~/.openhands/cache/extensions/metodoloji-*`` copy — and burned
    turns catting SKILL.md from each plus ``find / -name metodoloji`` hunts to
    decide which was canonical. One bounded scan answers it: the canonical
    root is always this checkout (``_PLUGIN_ROOT``); anything else found is a
    shadow candidate the agent must NOT read from.
    """
    canonical = str(_PLUGIN_ROOT)
    found: list[str] = []
    try:
        # Path.home() (not os.path.expanduser): the test seam patches
        # Path.home, and both raise on a missing home — caught below.
        home = pathlib.Path.home()
    except Exception:
        return {"available": False, "canonical": canonical,
                "found": found, "duplicate": False}
    try:
        fixed = home / ".openhands" / "plugins" / "installed" / "metodoloji"
        if fixed.is_dir():
            found.append(str(fixed))
        cache_bases = (
            home / ".claude" / "plugins" / "cache" / "yunusgungor" / "metodoloji",
            home / ".openhands" / "cache" / "extensions",
        )
        for base in cache_bases:
            try:
                if not base.is_dir():
                    continue
                for entry in sorted(base.iterdir(), key=lambda q: q.name)[:5]:
                    try:
                        if entry.is_dir():
                            found.append(str(entry))
                    except OSError:
                        continue
            except OSError:
                continue
    except Exception:
        return {"available": False, "canonical": canonical,
                "found": found, "duplicate": False}
    try:
        canon_resolved = str(pathlib.Path(canonical).resolve())
    except Exception:
        canon_resolved = canonical
    shadows = []
    for f in found:
        try:
            same = str(pathlib.Path(f).resolve()) == canon_resolved
        except Exception:
            same = (f == canonical)
        if not same:
            shadows.append(f)
    return {"available": True, "canonical": canonical,
            "found": found, "duplicate": bool(shadows), "shadows": shadows}


_HOT_ID_RE = re.compile(r"^([A-Z]+)-(\d+)$")


def hot_record_dangling(project_root: pathlib.Path, hot) -> bool:
    """True when the hot key names a chain record id with no file on disk.

    Catches the reset-session shape: the board still claims E-001
    in-progress while docs/experiments/ holds no E-001*.md (record deleted
    or never created, board kept the claim). A new session reading the
    digest would otherwise believe the run is live.
    """
    try:
        m = _HOT_ID_RE.match(str(hot or ""))
        if not m or m.group(1) not in RECORD_DIRS:
            return False
        kind, num = m.group(1), m.group(2)
        for rel in RECORD_DIRS[kind]:
            base = pathlib.Path(project_root) / rel
            if not base.is_dir():
                continue
            for candidate in base.glob(f"{kind}-{num}*.md"):
                stem = candidate.name[:-3]  # exact id or slug suffix grounds it
                if stem == f"{kind}-{num}" or stem.startswith(f"{kind}-{num}-"):
                    return False
        return True
    except (OSError, ValueError):
        return False


# --- Chain verdict freshness -------------------------------------------------
# A negative gate verdict ("NOT READY", "INCOMPLETE", …) is only as good as the
# artifacts it judged. The 2026-09-30 graph-engineering-arge session showed the
# cost of believing one blindly: the board's `IR-2026-09-30` key said
# "readiness: NOT READY" because `docs/design/prds/` did not exist yet, the PRD
# landed ~30 minutes later, and the next session's digest still presented the
# old verdict as current — bmad-help had to read the readiness report and reason
# the caveat by hand (exactly the re-derivation this digest exists to prevent).
# Same family as status_stale/hot_dangling: name the stale claim and its cure
# instead of surfacing it as live fact.
_STAGE_PREFIXES = (("E-", "E"), ("IR-", "IR"), ("SP-", "SP"), ("S-", "S"),
                   ("QR-", "QR"), ("PR-", "PR"))
# The mirrored relay keys compact_context reads (same order/values it prints).
_STAGE_MIRROR_KEYS = {"E": "methodology.last_experiment",
                      "IR": "methodology.last_ir",
                      "SP": "methodology.last_sp",
                      "S": "methodology.last_story",
                      "QR": "methodology.last_qr",
                      "PR": "methodology.last_pr"}
_NEGATIVE_VERDICT_RE = re.compile(
    r"(INPUTS\s+MISSING|PROVISIONAL|NOT\s+READY|NEEDS\s+WORK|INCOMPLETE|REJECTED)",
    re.IGNORECASE)
# Which relative artifact trees a gate *judges*: IR consumes the planning set
# (PRD/UX/architecture/epics/spec), PR consumes the built set. Keeping the map
# here — not inferred from the verdict text — is what makes the check cheap and
# honest: it can only say "something it gates on changed after the verdict".
_GATE_INPUT_DIRS = {
    "IR": ("docs/design/prds", "docs/design/ux-designs",
           "docs/design/architecture", "docs/planning", "docs/specs"),
    "PR": ("docs/development/stories", "docs/quality", "docs/development"),
}
_STAGE_RERUN_SKILL = {"IR": "bmad-check-implementation-readiness",
                      "PR": "bmad-production-readiness"}
_MAX_STALE_WALK = 2000
_REPORT_REF_RE = re.compile(r"report:\s*([^\s—]+)")


def _clip(text: str, limit: int = _PREVIEW_CHARS) -> str:
    """Bound a value for the text panel, marking the cut (never a silent chop)."""
    text = str(text)
    return text if len(text) <= limit else text[:limit - 1] + "…"


def chain_runs(project_root: pathlib.Path) -> dict:
    """Newest run key per methodology stage, with its full value + timestamp.

    `compact_context` clips methodology values at 80 chars (the injected-context
    budget) — enough for a hook nudge, not enough to keep the readiness report
    path a router must name (the real digest showed
    `…report: docs/planning/implementation-readi`). Read the snapshot directly
    for the untruncated value and the key's own `updated` stamp, which is what
    the freshness check compares against.
    """
    bb = _engine()
    if bb is None:
        return {}
    try:
        board = bb.read_board(str(project_root))
    except Exception:
        return {}
    runs: dict = {}
    for name, entry in (board.get("keys") or {}).items():
        if name.startswith("methodology.") or not isinstance(entry, dict):
            continue
        for prefix, stage in _STAGE_PREFIXES:
            if not name.startswith(prefix):
                continue
            value = entry.get("value")
            if isinstance(value, list):
                value = f"list[{len(value)}]"
            candidate = {"key": name, "value": str(value or ""),
                         "updated": float(entry.get("updated") or 0.0)}
            current = runs.get(stage)
            if current is None or candidate["updated"] > current["updated"]:
                runs[stage] = candidate
            break
    # Prefer the mirrored relay value for display: `write_key` stamps it as
    # `<run-key>: <value>` (the identity a router must name), while the raw run
    # key holds the bare value. The mirror is written by the same call, so the
    # run key's own stamp stays the freshness reference.
    for stage, mirror_key in _STAGE_MIRROR_KEYS.items():
        entry = (board.get("keys") or {}).get(mirror_key)
        if not isinstance(entry, dict) or stage not in runs:
            continue
        mirrored = str(entry.get("value") or "").strip()
        if mirrored:
            runs[stage]["value"] = mirrored
    return runs


def _newest_input(project_root: pathlib.Path, dirs, exclude: set):
    """(relpath, mtime) of the newest file under `dirs`, or None.

    Bounded walk (same spirit as docs_overview's cap) so a huge tree cannot turn
    the digest into a crawl. `exclude` holds the verdict's own report — a gate
    writes its report and *then* its run key, so that file is never evidence
    that the verdict is out of date.
    """
    newest = None
    walked = 0
    for rel in dirs:
        base = pathlib.Path(project_root) / rel
        if not base.is_dir():
            continue
        for root_dir, _dirnames, filenames in os.walk(base):
            for fn in filenames:
                walked += 1
                if walked > _MAX_STALE_WALK:
                    return newest
                p = pathlib.Path(root_dir) / fn
                try:
                    if str(p.resolve()).replace("\\", "/").lower() in exclude:
                        continue
                    mtime = p.stat().st_mtime
                except OSError:
                    continue
                if newest is None or mtime > newest[1]:
                    try:
                        relp = p.relative_to(project_root).as_posix()
                    except ValueError:
                        relp = str(p)
                    newest = (relp, mtime)
    return newest


def gate_verdict_staleness(project_root: pathlib.Path, runs: dict) -> list:
    """Negative gate verdicts whose input artifacts changed after they were issued."""
    stale = []
    for stage, dirs in _GATE_INPUT_DIRS.items():
        run = runs.get(stage)
        if not run or not run["updated"]:
            continue
        verdict = _NEGATIVE_VERDICT_RE.search(run["value"])
        if not verdict:
            continue  # a positive verdict is superseded by a re-run, not by inputs
        exclude = set()
        for m in _REPORT_REF_RE.finditer(run["value"]):
            try:
                exclude.add(str((pathlib.Path(project_root) / m.group(1))
                                .resolve()).replace("\\", "/").lower())
            except OSError:
                continue
        newest = _newest_input(project_root, dirs, exclude)
        if not newest or newest[1] <= run["updated"]:
            continue
        stale.append({
            "stage": stage,
            "key": run["key"],
            "verdict": verdict.group(1).upper(),
            "newer_path": newest[0],
            "newer_by": _age(newest[1] - run["updated"]),
            "verdict_age": _age(time.time() - run["updated"]),
            "rerun": _STAGE_RERUN_SKILL.get(stage, ""),
        })
    return stale


def plan_digest(project_root: pathlib.Path) -> dict:
    """Open Implementation Plans (GRP) — read-only, bounded, fail-open.

    Feed of the same derived progress the guard's plan layer computes. The peek
    reads record files only and NEVER writes the board (a board export cannot
    carry plan progress; only the live digest and the SessionStart context do).
    """
    empty = {"open": [], "count": 0, "line": ""}
    mod = _plan()
    if mod is None:
        return empty
    try:
        rows = mod.open_plans(str(project_root / "docs" / "experiments"),
                              str(project_root))
    except Exception:
        return empty
    return {"open": rows, "count": len(rows),
            "line": "; ".join(row["line"] for row in rows)}


def board_digest(project_root: pathlib.Path) -> dict:
    bb = _engine()
    if bb is None:
        return {"available": False, "chain_values": {}, "verdict_stale": [],
                "plan_progress": []}
    try:
        ctx = bb.compact_context(str(project_root))
    except Exception:
        return {"available": False, "chain_values": {}, "verdict_stale": [],
                "plan_progress": []}
    status = str((ctx.get("focus") or {}).get("status", "")).strip()
    hot = ctx.get("hot")
    # The untruncated run-key values + their stamps: compact_context's 80-char
    # clip is right for the hook nudge and wrong for a router (see chain_runs).
    runs = chain_runs(project_root)
    return {
        "available": True,
        "hot": hot,
        "hot_preview": (ctx.get("hot_meta") or {}).get("preview"),
        "hot_updated_age": _age(time.time() - (ctx.get("hot_meta") or {}).get("updated", 0.0))
        if (ctx.get("hot_meta") or {}).get("updated") else None,
        "hot_canvas": (ctx.get("hot_canvas") or {}).get("name"),
        "focus": ctx.get("focus") or {},
        "priority": ctx.get("priority"),
        "chain_progress": ctx.get("methodology") or {},
        # Open Implementation Plans (E-068): the board peek carries the same
        # derived progress the session edge does. Read-only — the peek writes
        # NOTHING to the board it peeks at.
        "plan_progress": plan_digest(project_root)["open"],
        "chain_values": {stage: run["value"] for stage, run in runs.items()},
        # The stage's actual run key (`IR-2026-09-30`) — a router must name the
        # key the board holds, not guess the record file's id (`IR-001.md`).
        "chain_keys": {stage: run["key"] for stage, run in runs.items()},
        "key_count": ctx.get("key_count"),
        "canvas_count": ctx.get("canvas_count"),
        "alerts": ctx.get("alerts"),
        # A hot run whose status still says complete is the stale hand-off that
        # misrouted bmad-help in the real session: the run is live, the claim
        # says finished.
        "status_stale": bool(hot) and status.lower() in ("complete", "done"),
        # A hot run naming a record id with no file on disk (reset session:
        # board kept E-001 in-progress, docs/experiments/ lost E-001*.md).
        "hot_dangling": hot_record_dangling(project_root, hot),
        # A negative gate verdict the artifacts have outgrown — the 2026-09-30
        # IR-2026-09-30 shape (see gate_verdict_staleness).
        "verdict_stale": gate_verdict_staleness(project_root, runs),
    }


def build(project_root: pathlib.Path) -> dict:
    """Everything an activating skill needs, in one read-only dict."""
    try:
        config = _resolve_config.resolve(project_root)
    except Exception as exc:  # config resolution must never kill orientation
        config = {"_error": f"{type(exc).__name__}: {exc}"}
    core = config.get("core") or {}
    output_folder = _resolve_tokens(core.get("output_folder", "{project-root}/docs"),
                                    project_root, str(project_root))
    skeleton_state = _skeleton.status(project_root)
    gate_key = pathlib.Path(os.path.expanduser("~")) / ".bmad" / "gate-key"
    board = board_digest(project_root)
    plan = plan_digest(project_root)
    handoffs = handoff_digest(project_root)
    catalog = catalog_digest()
    try:
        sprint = _state().sprint_summary(project_root)
    except Exception:
        sprint = {"found": False, "reason": "sprint summary unavailable"}
    try:
        mcp = _state().mcp_inventory(project_root)
        mcp["steering_line"] = _state().mcp_steering_line(mcp)
    except Exception:  # tool discovery must never kill orientation (fail-open)
        mcp = {"servers": [], "sources_checked": [], "count": 0, "steering_line": ""}

    try:
        docs = docs_overview(project_root)
    except Exception:  # docs peek must never kill orientation (fail-open)
        docs = {"available": False}

    try:
        installs = install_digest()
    except Exception:  # install peek must never kill orientation (fail-open)
        installs = {"available": False, "canonical": str(_PLUGIN_ROOT),
                    "found": [], "duplicate": False}

    next_steps = []
    # Identity, not cosmetics (2026-10-01 graph-engineering-arge session): the
    # run had to work out which name governed its record paths — the config
    # project_name (the plugin base default, itself a leaked project name) or
    # the product it was naming PRD folders after. Run folders are built from
    # {project_name}, so an unset/neutral name silently names artifacts after
    # the plugin default. Name the layer that fixes it.
    project_name = str(core.get("project_name") or "").strip()
    if project_name.lower() in ("", "unconfigured", "bmad-project", "project"):
        next_steps.append(
            f"project_name is unset (resolves to '{project_name or '(empty)'}' — the "
            f"plugin's neutral base default) — set core.project_name in "
            f"{{project-root}}/docs/config.toml so run folders and record headers "
            f"name YOUR project, not the plugin default")
    if installs.get("duplicate"):
        shadows = ", ".join((installs.get("shadows") or [])[:3])
        next_steps.append(
            f"duplicate plugin installs shadow this checkout ({shadows}) — "
            f"use canonical {installs.get('canonical')} verbatim; never "
            f"cat skills/docs from cache paths and never `find /` for the root")
    if handoffs.get("waiting"):
        for skill, info in handoffs["waiting"].items():
            next_steps.append(
                f"unclaimed hand-off for {skill} (waiting {info['oldest_age']}) — "
                f"peek `{info['peek']}`, then consume it")
    if not skeleton_state["installed"] and not skeleton_state["problems"]:
        next_steps.append(
            f"record skeleton not installed — run: python3 {_PLUGIN_ROOT}/bmad/"
            f"scripts/skeleton.py --install")
    for problem in skeleton_state["problems"]:
        next_steps.append(f"skeleton/marker drift: {problem}")
    if board.get("status_stale"):
        next_steps.append(
            f"board focus says '{board['focus'].get('status')}' while "
            f"'{board['hot']}' is hot — the run is live; write "
            f"`blackboard.py write --key status --value in-progress`")
    if board.get("hot_dangling"):
        next_steps.append(
            f"board hot run '{board['hot']}' names no record file — the run "
            f"was reset or its record deleted while the board kept the "
            f"claim. Either re-create the record or clear the stale focus: "
            f"`python3 {_PLUGIN_ROOT}/bmad/scripts/blackboard.py hot --clear "
            f"--project-root {project_root}`")
    for sv in board.get("verdict_stale") or []:
        # A gate verdict is a snapshot verdict, not a permanent one: name the
        # superseding artifact and the re-run so no session (and no help turn)
        # has to read the gate report to work out whether it still holds.
        next_steps.append(
            f"{sv['stage']} verdict stale — '{sv['key']}' says {sv['verdict']} "
            f"({sv['verdict_age']} ago) but {sv['newer_path']} changed "
            f"{sv['newer_by']} after it; re-run {sv['rerun']} before trusting "
            f"the {sv['stage']} verdict")

    # Sprint-derived next steps — measured signal, not guesswork: the mailjs
    # session (2026-09-23) re-derived its whole state from a stale memory
    # because nothing surfaced "stories done but epic/retro open".
    if sprint.get("found"):
        stories = sprint.get("stories") or {}
        in_review = sorted(k for k, s in stories.items() if s == "review")
        in_prog = sorted(k for k, s in stories.items() if s == "in-progress")
        for k in in_review[:3]:
            next_steps.append(
                f"story {k} sits in 'review' — run bmad-code-review (its "
                f"verdict is the only thing that moves it to done)")
        for k in in_prog[:3]:
            next_steps.append(
                f"story {k} is in-progress — resume it or mark it honestly "
                f"before opening anything new")
        for epic_key, prog in sorted((sprint.get("epic_progress") or {}).items()):
            epic_status = (sprint.get("epics") or {}).get(epic_key, "")
            if prog["total"] and epic_status == "in-progress" and prog["done"] == prog["total"]:
                next_steps.append(
                    f"{epic_key}: every story done — roll the epic up to done "
                    f"in sprint-status and run bmad-retrospective")
            elif prog["total"] and epic_status == "done" and prog["done"] < prog["total"]:
                next_steps.append(
                    f"{epic_key}: marked done but {prog['total'] - prog['done']} "
                    f"story/stories are not — fix the status before planning on")
        if not any(s.startswith("story ") or s.startswith("epic-")
                   for s in next_steps):
            ready = sorted(k for k, s in stories.items() if s == "ready-for-dev")
            if ready:
                next_steps.append(
                    f"next ready story: {ready[0]} — open it with bmad-dev-story")

    # MCP steering (2026-09-24 request: "metodoloji must be willing to discover
    # and route to installed, active MCP servers"). One line per case, never a
    # dump: name what exists so the run reaches for it; when nothing is
    # configured, offer to add one only if the coming work plausibly fits —
    # never as a mandatory step. Healthiness stays the harness's answer; the
    # digest only proves what is configured and enabled.
    enabled = [s for s in (mcp.get("servers") or []) if s.get("enabled")]
    disabled_count = len(mcp.get("servers") or []) - len(enabled)
    if enabled:
        names = ", ".join(f"`{s['name']}`" for s in enabled[:4])
        more = len(enabled) - 4
        next_steps.append(
            f"mcp servers reachable this session: {names}"
            + (f", … (+{more})" if more > 0 else "")
            + " — prefer them for the work they serve (repo search, docs lookup, "
              "browser, issue trackers); do not re-derive by hand what a server "
              "already provides")
    elif disabled_count:
        # All configured servers are disabled: "none configured" would be false
        # and push the user into re-adding what already exists — the honest
        # signal is the enable step.
        next_steps.append(
            f"{disabled_count} MCP server(s) configured but all disabled — enable "
            "the ones the coming work needs in their harness config; they surface "
            "here once active")
    elif mcp.get("sources_checked"):
        next_steps.append(
            "no MCP servers configured — if the coming work would clearly benefit "
            "from one (docs lookup, browser automation, repo indexing), offer to "
            "add it to .mcp.json (project) or the harness's user config; otherwise "
            "proceed tool-free")

    return {
        "plugin_root": str(_PLUGIN_ROOT),
        "plugin_version": _skeleton.plugin_version(_PLUGIN_ROOT),
        "project_root": str(project_root),
        "core": core,
        # The skills' `{date}` token (frontmatter, record headers) — one source
        # for it, so a run never needs a second config read just for today.
        "today": _dt.date.today().isoformat(),
        "modules": config.get("modules") or {},
        "module_codes": sorted((config.get("modules") or {}).keys()),
        "artifacts": artifact_dirs(project_root, output_folder, config),
        "records": record_inventory(project_root),
        "chain_runs": chain_runs(project_root),
        "sprint": sprint,
        "mcp": mcp,
        "board": board,
        "plan": {"open": plan["open"], "count": plan["count"],
                 "line": plan["line"]},
        "handoffs": handoffs,
        "docs": docs,
        "installs": installs,
        "skeleton": skeleton_state,
        "gate_key": {"path": str(gate_key), "present": gate_key.is_file()},
        "catalog": catalog,
        "next_steps": next_steps,
    }


def _print_text(digest: dict) -> None:
    print(f"METODOLOJI orient — plugin v{digest['plugin_version']}")
    print(f"  {{metodoloji-root}}: {digest['plugin_root']}")
    print(f"  {{project-root}}  : {digest['project_root']}")
    core = digest["core"]
    if core:
        print(f"  core            : name={core.get('project_name', '?')} "
              f"lang={core.get('communication_language', '?')} "
              f"doc_lang={core.get('document_output_language', '?')} "
              f"user={core.get('user_name', '?')} date={digest['today']}")
        print(f"                    output_folder={core.get('output_folder', '?')}")
    print(f"  modules         : {', '.join(digest['module_codes']) or '(none)'}")

    printed = 0
    for label, info in digest["artifacts"].items():
        if info.get("alias_of"):
            continue  # same folder as an earlier label — already printed
        if printed >= _MAX_ARTIFACT_LINES:
            print(f"  artifacts       : … ({len(digest['artifacts'])} resolved paths; "
                  "--json for the rest)")
            break
        printed += 1
        if not info["exists"]:
            print(f"  {label:24}: (missing) {info['path']}")
            continue
        shown = ", ".join(info["entries"])
        more = "" if info["entry_count"] <= _MAX_LISTED else \
            f" … +{info['entry_count'] - _MAX_LISTED}"
        print(f"  {label:24}: {info['path']} — {shown}{more}")

    if digest["records"]:
        line = " ".join(f"{kind}={info['count']} (newest {info['newest']})"
                        for kind, info in sorted(digest["records"].items()))
        print(f"  records         : {line}")
    # SP has TWO concrete forms (the 2026-10-01 fikir session flagged the gap:
    # the agent read "SP yok" as "sprint planning skipped", then mis-read the
    # catalog's required column as a hard CS prerequisite to compensate):
    # 1. the SP-*.md gate record (quality gate 2 looks for it, §6 formats it,
    #    _template_SP.md is its template — written by the sprint-planning close
    #    since this surface's fix), and
    # 2. the sprint heartbeat `SP-<sequence>` on the board (methodology.last_sp).
    # Sprint planning legitimately produces ONLY form 2 mid-implementation when
    # it refreshes an existing sprint. Name the split so a router never has to
    # re-derive it — an SP heartbeat without a record file is a REFRESH, not a
    # skipped stage.
    sp_runs = (digest.get("chain_runs") or {}).get("SP") or {}
    sp_record = (digest.get("records") or {}).get("SP") or {}
    if sp_runs and not sp_record and digest["records"]:
        print(f"  sprint          : SP heartbeat {sp_runs.get('key', '?')} on board, "
              "no SP-*.md gate record — an existing sprint was refreshed "
              "(sprint-status.yaml is the artifact; the record is written by "
              "the sprint-planning close when scope is first cut)")
    elif not sp_record and digest["records"]:
        print("  sprint          : (no SP gate record and no SP heartbeat on the "
              "board — bmad-sprint-planning has not run yet; catalog `required` "
              "is a PHASE gate for closing phase 4, not a create-story "
              "prerequisite — CS offers a specific-story path without it)")

    sprint = digest.get("sprint") or {}
    if sprint.get("found"):
        line = _state().format_sprint_line(sprint)
        if line:
            print(f"  {line}")
    elif not digest["records"]:
        print("  records         : none yet (docs/experiments, docs/development, "
              "docs/quality)")

    board = digest["board"]
    if board.get("available"):
        focus = board.get("focus") or {}
        flag = "  ← STALE (hot run must claim in-progress)" if board["status_stale"] else ""
        if board.get("hot_dangling"):
            flag += "  ← DANGLING (hot run names no record file)"
        if board.get("verdict_stale"):
            flag += "  ← STALE VERDICT (" + ", ".join(
                sv["stage"] for sv in board["verdict_stale"]) + ")"
        print(f"  board           : hot={board['hot'] or '(none)'} "
              f"status={focus.get('status') or '(unset)'} "
              f"scope={focus.get('scope') or '(unset)'} "
              f"keys={board['key_count']} canvases={board['canvas_count']} "
              f"alerts={board['alerts']}{flag}")
        if board.get("hot_preview"):
            print(f"                    hot preview: {board['hot_preview']}")
        if board.get("hot_canvas"):
            print(f"                    hot canvas: {board['hot_canvas']}")
        # Prefer the untruncated run-key values (chain_values); fall back to the
        # 80-char compact_context view when no run key exists. Each value is
        # clipped with an explicit `…` so a mangled path is never mistaken for
        # the report path a router is asked to name.
        chain_source = board.get("chain_values") or board.get("chain_progress") or {}
        if chain_source:
            chain = " | ".join(f"{k}: {_clip(v)}" for k, v in chain_source.items())
            print(f"                    chain: {chain}")
        for sv in board.get("verdict_stale") or []:
            print(f"                    stale verdict: {sv['key']} = {sv['verdict']} "
                  f"({sv['verdict_age']} ago) but {sv['newer_path']} changed "
                  f"{sv['newer_by']} after — re-run {sv['rerun']}")
        # Open plans (E-068): the same line the session edge injects — a
        # resumed multi-file operation sees what its plan has left.
        for row in board.get("plan_progress") or []:
            print(f"                    plan: {row['line']}")
    else:
        print("  board           : (engine unavailable — no blackboard peek)")

    mcp = digest.get("mcp") or {}
    if mcp.get("steering_line"):
        print(f"  {mcp['steering_line']}")
        enabled = [s for s in (mcp.get("servers") or []) if s.get("enabled")][:3]
        print("                    sources: "
              + ", ".join(s["source"] for s in enabled))
    elif mcp.get("servers"):
        print(f"  mcp             : {len(mcp['servers'])} configured (all disabled) "
              "— enable what the work needs in the harness config")
    elif mcp.get("sources_checked"):
        print("  mcp             : none configured — offer to add one if the "
              "work fits (.mcp.json / harness user config)")

    handoffs = digest["handoffs"]
    if handoffs.get("available"):
        if handoffs["waiting"]:
            for skill, info in handoffs["waiting"].items():
                print(f"  handoff         : {skill} ({info['count']}, oldest "
                      f"{info['oldest_age']}) — {info['preview']}")
                print(f"                    peek: {info['peek']}")
        else:
            print("  handoff         : none waiting")
    docs = digest.get("docs") or {}
    if docs.get("available"):
        subs = ", ".join(
            f"{s['name']} ({s['files']}{'+' if s['truncated'] else ''})"
            for s in docs.get("subdirs") or [])
        print(f"  docs            : {docs['path']} — {subs or '(empty)'}")
        for f in (docs.get("largest") or [])[:6]:
            kb = f["bytes"] / 1024
            size = f"{kb:.0f}KB" if kb < 1024 else f"{kb / 1024:.1f}MB"
            print(f"                    {size:>8}  {f['path']}")
        print("                    → read large files windowed (offset/limit), "
              "never re-cat a file you already read")
    installs = digest.get("installs") or {}
    if installs.get("duplicate"):
        print(f"  installs        : DUPLICATE — shadows: "
              f"{', '.join((installs.get('shadows') or [])[:3])}")
        print(f"                    → canonical is {installs.get('canonical')}; "
              f"never read skills/docs from cache paths, never `find /`")
    skeleton = digest["skeleton"]
    print(f"  skeleton        : {'installed' if skeleton['installed'] else 'NOT installed'}"
          f" (marker {'present' if skeleton['marker_present'] else 'absent'})"
          + ("" if skeleton['installed'] else
             f" — run: python3 {digest['plugin_root']}/bmad/scripts/skeleton.py --install"))
    print(f"  gate_key        : {'present' if digest['gate_key']['present'] else 'MISSING'}"
          f" ({digest['gate_key']['path']})")
    catalog = digest["catalog"]
    if catalog.get("available"):
        print(f"  catalog         : {catalog['skill_count']} skills / "
              f"{len(catalog['modules'])} modules — "
              f"{catalog['path']}")
        if catalog["ambiguous_menu_codes"]:
            pairs = ", ".join(
                f"{code} ({' vs '.join(owners)})"
                for code, owners in list(catalog["ambiguous_menu_codes"].items())[:_MAX_LISTED])
            print(f"  ambiguous codes : {pairs}")
            print("                    → qualify a menu code with its module when "
                  "two skills share it")
    for step in digest["next_steps"]:
        print(f"  NEXT            : {step}")
    if not digest["next_steps"]:
        print("  NEXT            : no live signal — route from the board + catalog above")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only orientation digest: plugin root, resolved paths, "
                    "board focus, waiting batons, records, skeleton, catalog.",
    )
    parser.add_argument("--project-root", "-p", default=None,
                        help="Target project root (default: CLAUDE_PROJECT_DIR / "
                             "OPENHANDS_PROJECT_DIR / cwd)")
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    args = parser.parse_args()

    project_root = _skeleton.resolve_project_root(args.project_root)
    digest = build(project_root)
    if args.json:
        print(json.dumps(digest, indent=2, ensure_ascii=False))
    else:
        _print_text(digest)
    return 0


if __name__ == "__main__":
    sys.exit(main())

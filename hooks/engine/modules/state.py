"""State — the single read-only source for project progress facts.

Two consumers made the same three reads in different shapes: the session-start
inject (audit.session_start) said nothing about where the project stands, while
orient.py counted records and stop.py parsed sprint-status separately — so
three answers to "where are we" could disagree.

This module is the one place that answers it, READ-ONLY:

- ``record_inventory()``  — E/IR/SP/S/QR/PR record counts + newest id per kind.
- ``sprint_summary()``    — the parsed sprint-status.yaml (canonical-first), or
  a precise absence reason. Story/epic keys are separated, so callers can
  report both ``3-4-foo: done`` and ``epic-3: in-progress`` without confusing
  one for the other.

Neither function reads anything else and neither mutates anything. orient.py
delegates to record_inventory; stop.py's story scan delegates to
sprint_summary; the session-start context sentence composes both. Keep every
new consumer here — a second parser is how the disagreement starts.

``mcp_inventory()`` (2026-09-24) extends the same doctrine to tools: the
methodology should know which MCP servers the session can reach and steer
toward them when the work fits (the user's request: "metodoloji must be
willing to discover and route to installed, active MCP servers"). Read-only,
fail-open, never spawns a server; healthiness stays the harness's answer.

Format rationale (2026-09-23, mailjs OpenHands transcript): the session began
from memory ("Epic 6 in-progress, contacts CRUD tested") instead of the board
and burned its first minutes re-deriving state — the answers existed in
docs/planning + sprint-status.yaml but arrived in no single bounded read.
"""

from __future__ import annotations

import json
import pathlib
import re
from collections import Counter

# Importing the package's config for the audit-log helper would be circular at
# module level for some entry points; keep this module import-light instead:
# sprint_summary() does not need the log, record_inventory() needs no engine.


# --- record inventory ---------------------------------------------------------

# Per-kind pattern (orient.py's lesson: a shared `^(E|IR|…)-N.md$` counted
# SP-026.md once per kind). Keys follow the E → IR → SP → S → QR → PR chain.
# QR keeps its legacy secondary home (docs/development) — pre-migration
# projects wrote QRs there and the digest must keep seeing them.
_RECORD_KINDS = (
    ("E", ("docs/experiments",)),
    ("IR", ("docs/development",)),
    ("SP", ("docs/development",)),
    ("S", ("docs/development/stories",)),
    ("QR", ("docs/quality", "docs/development")),
    ("PR", ("docs/development",)),
)


def record_inventory(project_root: str | pathlib.Path) -> dict:
    """Count the E→IR→SP→S→QR→PR records (newest first per kind).

    Returns {kind: {count, newest, dir}} for kinds with at least one record.
    Read-only: glob + stat only.
    """
    root = pathlib.Path(project_root)
    inventory: dict = {}
    for kind, rels in _RECORD_KINDS:
        # Title-derived slug suffix counts: the gate's own records are named
        # `E-056-gate-heading-guard.md`, and the mirror note names that real
        # path — a bare `^E-N.md$` pattern made every slugged record
        # invisible to routing (E-054..E-058 reported as "none yet").
        record_re = re.compile(rf"^{kind}-\d+(?:-[A-Za-z0-9-]+)?\.md$")
        found = []
        for rel in rels:
            base = root / rel
            if not base.is_dir():
                continue
            for candidate in base.glob("*.md"):
                if record_re.match(candidate.name):
                    try:
                        found.append((candidate.stat().st_mtime, rel, candidate.name))
                    except OSError:
                        continue
        found.sort(reverse=True)
        if found:
            inventory[kind] = {"count": len(found), "newest": found[0][2],
                               "dirs": sorted({rel for _ts, rel, _n in found})}
    return inventory


# --- MCP server inventory -------------------------------------------------------

# Where harnesses configure MCP servers (read-only peek, never executed):
# project-level files make servers shareable per-repo, user-level files are
# the harness's global config. Kept as a tuple so the none-case can name
# exactly what was checked ("no MCP servers configured" must be provable).
_MCP_SOURCES = (
    ("project", ".mcp.json", "claude"),
    ("project", ".vscode/mcp.json", "vscode"),
    ("project", ".cursor/mcp.json", "cursor"),
    ("user", ".claude.json", "claude"),
    ("user", ".codex/config.toml", "codex"),
    # OpenHands is a first-class runtime here (run-hook.sh dispatches it) and
    # keeps its servers in ~/.openhands/mcp.json; Cursor also takes a
    # user-level file at ~/.cursor/mcp.json. VS Code's user-level config lives
    # in profile/versioned dirs with no stable home — skipped on purpose.
    ("user", ".openhands/mcp.json", "openhands"),
    ("user", ".cursor/mcp.json", "cursor"),
)


def _mcp_entries_from_json(path: pathlib.Path) -> dict:
    """{name: entry} from the three JSON shapes harnesses use.

    Claude/Cursor nest under "mcpServers"; VS Code nests under "servers".
    """
    data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    if not isinstance(data, dict):
        return {}
    servers = data.get("mcpServers") or data.get("servers") or {}
    return servers if isinstance(servers, dict) else {}


def _claude_local_entries(data: dict, root: pathlib.Path) -> dict:
    """THIS project's local-scope servers from ~/.claude.json's 'projects'.

    `claude mcp add` writes its default (local) scope under
    "projects".{project-path}.mcpServers — the most common way servers get
    attached to a repo, and they never touch the repo's own .mcp.json.
    Only the entry matching this project root is reachable here; every other
    project's servers stay out. Keys are stored with forward slashes even on
    Windows, so comparison normalizes separators and case.
    """
    projects = data.get("projects")
    if not isinstance(projects, dict):
        return {}

    def norm(value: str) -> str:
        return str(value).replace("\\", "/").rstrip("/").lower()

    me = {norm(root), norm(pathlib.Path(str(root)).resolve())}
    for key, value in projects.items():
        if isinstance(value, dict) and norm(key) in me:
            servers = value.get("mcpServers")
            return servers if isinstance(servers, dict) else {}
    return {}


def _mcp_entries_from_toml(path: pathlib.Path) -> dict:
    """{name: entry} from Codex's [mcp_servers.<name>] TOML tables."""
    try:
        import tomllib
    except ImportError:  # pre-3.11 — the methodology requires 3.11+, stay fail-open
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:  # tomllib.TomlError and friends — a broken config is not fatal
        return {}
    servers = data.get("mcp_servers") or {}
    return servers if isinstance(servers, dict) else {}


def mcp_inventory(project_root: str | pathlib.Path) -> dict:
    """Discover the MCP servers this session can reach (READ-ONLY, fail-open).

    Reads the standard harness config locations (project + user level) and
    normalizes them to ``[{name, source, harness, transport, target, enabled}]``.
    Healthiness is deliberately NOT probed — spawning a server is a side
    effect this module never takes; callers steer with names and let the
    harness/runtime answer availability. ``enabled`` mirrors the harness's
    own disabled flag when present, never invented otherwise.

    Dedup: the same server name may be declared at several scopes (project
    .mcp.json AND a user file; `claude mcp add`'s default local scope under
    ~/.claude.json "projects" — and again at that file's user-global level).
    The closest declaration wins, mirroring the harnesses' own precedence:
    project files (rank 0) > this project's local entries (rank 1) >
    user-global files (rank 2) — so the list shows what this session
    actually runs, once.
    """
    root = pathlib.Path(project_root)
    home = pathlib.Path.home()
    winners: dict[str, tuple[int, dict]] = {}  # name -> (rank, record)
    sources_seen: list[str] = []

    def offer(entries: dict, scope: str, rank: int, display: str,
              harness: str) -> None:
        if not isinstance(entries, dict):
            return  # a wrong-shaped source hides only itself
        for name, entry in entries.items():
            if not isinstance(entry, dict):
                entry = {}
            record = {
                "name": str(name),
                "harness": harness,
                "source": f"{scope}:{display}",
                "transport": str(entry.get("type") or entry.get("transport") or ""),
                "target": str(entry.get("command") or entry.get("url") or "")[:80],
                "enabled": entry.get("disabled") not in (True, "true"),
            }
            prev = winners.get(str(name))
            if prev is None or rank < prev[0]:
                winners[str(name)] = (rank, record)

    for scope, rel, harness in _MCP_SOURCES:
        base = root if scope == "project" else home
        path = base / rel
        # User-level rels already start with '.' — join with '~/' (not '~')
        # so the display reads ~/.claude.json, not ~.claude.json.
        display = ("./" if scope == "project" else "~/") + rel
        sources_seen.append(display)
        try:
            if not path.is_file():
                continue
            if path.suffix == ".toml":
                offer(_mcp_entries_from_toml(path), scope, 0, display, harness)
            elif rel == ".claude.json":
                data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
                offer(data.get("mcpServers") or {}, scope, 2, display, harness)
                # This project's local-scope servers (`claude mcp add` default)
                # live under "projects" — the most common way a server gets
                # attached to a repo; they never touch the repo's .mcp.json and
                # they outrank the same name at user-global level.
                offer(_claude_local_entries(data, root),
                      scope, 1, display + " (this project)", harness)
            else:
                offer(_mcp_entries_from_json(path), scope, 0, display, harness)
        except Exception:  # malformed JSON/OSError — one broken config hides no others
            continue

    servers = [record for _rank, record in winners.values()]
    servers.sort(key=lambda s: (not s["enabled"], s["name"].lower()))
    return {"servers": servers, "sources_checked": sources_seen,
            "count": len(servers)}


def mcp_steering_line(inventory: dict) -> str:
    """One bounded line naming the reachable servers (empty when none).

    Same contract as format_sprint_line: a bounded inject line a caller can
    drop into orientation output without a second read.
    """
    servers = [s for s in (inventory.get("servers") or []) if s.get("enabled")]
    if not servers:
        return ""
    names = ", ".join(s["name"] for s in servers[:4])
    more = len(servers) - 4
    line = f"mcp: {len(servers)} active server(s) — {names}" + (", …" if more > 0 else "")
    return line[:200]


# Canonical path first (Seçenek A: skills compose it as
# {implementation_artifacts}/sprint-status.yaml); the rest are legacy fallbacks.
_SPRINT_STATUS_CANDIDATES = (
    "docs/development/native/sprint-status.yaml",
    "bmad-output/implementation-artifacts/sprint-status.yaml",
    "_bmad-output/implementation-artifacts/sprint-status.yaml",
    ".metodoloji/sprint-status.yaml",
)

# Story key: `N-N-slug` (the shape the engine's stop hook already parses and
# check-methodology.sh greps). An epic key (`epic-N`) looks identical to a
# loose pattern but is NOT a story — the real session read "epic-6: in-progress"
# as "one story done, rest untracked" and re-derived its whole state from that.
_STORY_ENTRY_RE = re.compile(
    r"^[ \t]+(\d+-\d+-[a-z][a-z0-9-]*):[ \t]*([A-Za-z][A-Za-z0-9_-]*)",
    re.MULTILINE,
)
_EPIC_ENTRY_RE = re.compile(
    r"^[ \t]+(epic-\d+):[ \t]*([A-Za-z][A-Za-z0-9_-]*)", re.MULTILINE,
)

_STORY_STATUSES = ("backlog", "ready-for-dev", "in-progress", "review", "done")


def sprint_status_path(project_root: str | pathlib.Path) -> str | None:
    """The canonical-first existing sprint-status.yaml path (None = absent)."""
    root = pathlib.Path(project_root)
    for rel in _SPRINT_STATUS_CANDIDATES:
        p = root / rel
        try:
            if p.is_file():
                return str(p)
        except OSError:
            continue
    return None


def sprint_summary(project_root: str | pathlib.Path) -> dict:
    """Parse the sprint-status file into story + epic entries (read-only).

    Returns one of:

    - ``{"found": False, "reason": "no sprint-status file", "paths_checked": […]}``
    - ``{"found": False, "reason": "sprint-status file has no development_status entries", "path": …}``

    or, with ``found: True``:

    - ``path``            — the file used
    - ``stories``         — {key: status} for story keys (`N-N-slug`)
    - ``epics``           — {key: status} for epic keys (`epic-N`)
    - ``counts``          — {status: n} over stories only
    - ``epic_progress``   — per epic: {done, total, started} (stories under its
      number; `started` = members past `backlog`)

    Status vocabulary is the shipped template's (check-methodology.sh §6
    conventions); unknown statuses are kept verbatim, never invented.
    """
    root = pathlib.Path(project_root)
    path = sprint_status_path(root)
    if path is None:
        return {"found": False, "reason": "no sprint-status file",
                "paths_checked": list(_SPRINT_STATUS_CANDIDATES)}
    try:
        content = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"found": False, "reason": f"sprint-status unreadable: {exc}",
                "path": path}

    # The shipped template documents its vocabulary in comment legend lines
    # (check-methodology.sh §6 drift bug #1); comment lines carry no entries.
    body = "\n".join(line for line in content.splitlines()
                     if not line.lstrip().startswith("#"))
    stories: dict = {}
    for key, status in _STORY_ENTRY_RE.findall(body):
        if key not in stories:
            stories[key] = status
    epics: dict = {}
    for key, status in _EPIC_ENTRY_RE.findall(body):
        if key not in epics:
            epics[key] = status
    if not stories and not epics:
        return {"found": False,
                "reason": "sprint-status file has no development_status entries",
                "path": path}

    counts = dict(Counter(stories.values()))

    epic_progress: dict = {}
    for epic_key in epics:
        try:
            num = int(epic_key[len("epic-"):])
        except ValueError:
            continue
        # Digit boundary: members are `{num}-<any>-<slug>`, so `1-6-x` joins
        # epic 1 but `10-x` never can (it is epic 10's).
        member_re = re.compile(rf"^{num}-\d+-")
        members = [(k, s) for k, s in stories.items() if member_re.match(k)]
        epic_progress[epic_key] = {
            "done": sum(1 for _, s in members if s == "done"),
            "total": len(members),
            # Members that have left `backlog`. The epic-lag warning below needs
            # it: a freshly planned sprint lists stories under every epic while
            # every epic still reads `backlog` (the template default, correct
            # until create-story writes the first story), and warning on that
            # state made every correctly planned project look broken.
            "started": sum(1 for _, s in members if s != "backlog"),
        }

    return {"found": True, "path": path, "stories": stories, "epics": epics,
            "counts": counts, "epic_progress": epic_progress}


def format_sprint_line(summary: dict) -> str:
    """One bounded line for a session-start inject (max ~200 chars).

    Leads with actionable states (in-progress, review), counts the rest;
    epics appear only when their state disagrees with their stories (the
    `epic-N: in-progress` vs all-stories-done lag the real session tripped on).
    """
    if not summary.get("found"):
        return ""
    stories = summary.get("stories") or {}
    epics = summary.get("epics") or {}
    counts = summary.get("counts") or {}
    epic_progress = summary.get("epic_progress") or {}

    parts: list[str] = []
    # Order the vocabulary so the important states lead.
    for status in ("in-progress", "review", "ready-for-dev", "done", "backlog"):
        keys = [k for k, s in sorted(stories.items()) if s == status]
        if not keys:
            continue
        if status in ("done", "backlog") or len(keys) > 3:
            shown = ", ".join(keys[:3])
            more = len(keys) - 3
            parts.append(f"{len(keys)} {status} ({shown}{', …' if more > 0 else ''})")
        else:
            parts.append(f"{status}: {', '.join(keys)}")
    leftover = {s: n for s, n in counts.items()
                if s not in _STORY_STATUSES}
    for status, n in sorted(leftover.items()):
        parts.append(f"{n} {status}")

    for epic_key in sorted(epic_progress):
        prog = epic_progress[epic_key]
        status = epics.get(epic_key, "")
        # Show the epic only on disagreement: claimed in-progress with all
        # member stories done (completion lag), or done with gaps.
        if prog["total"] and status == "in-progress" and prog["done"] == prog["total"]:
            parts.append(f"⚠ {epic_key}: stories all done — epic still "
                         f"in-progress, roll up to done + run the retro")
        elif prog["total"] and status == "done" and prog["done"] < prog["total"]:
            parts.append(f"⚠ {epic_key}: marked done but "
                         f"{prog['total'] - prog['done']} story/stories not done")
        elif status in ("backlog", "") and prog.get("started"):
            parts.append(f"⚠ {epic_key}: backlog but {prog['started']} of "
                         f"{prog['total']} story/stories started — bump the epic")
    if not parts:
        return ""
    line = "sprint: " + "; ".join(parts)
    if len(line) > 200:
        line = line[:197] + "…"
    return line

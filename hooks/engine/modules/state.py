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
- ``canonical_record_id()`` / ``record_files()`` — an id back to its file(s).
  One id, one record: a title-slug file and a zero-padded spelling are the
  record they name, so a citation or a freshness anchor resolves instead of
  reading as "no record".

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

# The id space, owned here because every reader asks this module: the duplicate
# gate, the number allocator, the doctor and the digest all count "records"
# over these roots. Keys follow the E → IR → SP → S → QR → PR chain.
RECORD_SCAN_ROOTS = ("docs", "bmad-output", "_bmad-output", "design-artifacts")
_RECORD_KINDS = ("E", "IR", "SP", "S", "QR", "PR")


def record_scan_files(project_root: str | pathlib.Path) -> list[pathlib.Path]:
    """Every `*.md` under `RECORD_SCAN_ROOTS`, recursively, in scan order.

    A record home is any file the write gate would treat as an id claim: the
    canonical directories, but also an archive subdir, `docs/development/native`
    and a pre-migration `bmad-output/` tree. A reader that walks only the
    canonical directories answers "no records" for a project the gate already
    refuses duplicates in. Unreadable roots are skipped (the gate fails open the
    same way); identity is still decided by `record_id_of_stem`, not the path.
    """
    root = pathlib.Path(project_root)
    found: list[pathlib.Path] = []
    for rel_root in RECORD_SCAN_ROOTS:
        base = root / rel_root
        try:
            if not base.is_dir():
                continue
            found.extend(sorted(base.rglob("*.md")))
        except OSError:
            continue
    return found


def record_inventory(project_root: str | pathlib.Path) -> dict:
    """Count the E→IR→SP→S→QR→PR records (newest first per kind).

    Returns {kind: {count, newest, dirs}} for kinds with at least one record,
    counted over the id space the write gate enforces (`RECORD_SCAN_ROOTS`), so
    a legacy or archived record is not "no records" to the digest while the
    gate already refuses a second file for its id. Read-only: glob + stat only.
    """
    root = pathlib.Path(project_root)
    by_kind: dict[str, list[tuple[float, str, str]]] = {k: [] for k in _RECORD_KINDS}
    for candidate in record_scan_files(root):
        # Title-derived slug suffix counts: the gate's own records are named
        # `E-056-gate-heading-guard.md`, and the mirror note names that real
        # path — a bare `^E-N.md$` pattern made every slugged record
        # invisible to routing (E-054..E-058 reported as "none yet"). The
        # filename grammar is the gate's, so a stray `notes-S-001.md` or a
        # lowercase `e-001.md` is not a record for either reader.
        record_id = record_id_of_stem(candidate.stem)
        if not record_id:
            continue
        kind = record_id.rpartition("-")[0]
        if kind not in by_kind:
            continue
        try:
            mtime = candidate.stat().st_mtime
        except OSError:
            continue
        try:
            rel = candidate.parent.relative_to(root).as_posix()
        except ValueError:
            rel = candidate.parent.as_posix()
        by_kind[kind].append((mtime, rel, candidate.name))

    inventory: dict = {}
    for kind, found in by_kind.items():
        found.sort(reverse=True)
        if found:
            inventory[kind] = {"count": len(found), "newest": found[0][2],
                               "dirs": sorted({rel for _ts, rel, _n in found})}
    # The deferred-work ledger is the one surface outside the E→PR chain that
    # routing still needs to see: without this key an orphaned ledger looks
    # exactly like no ledger (E-108). `count` is OPEN entries — the number a
    # reader acts on; `total`/`done`/`malformed` ride the same entry for JSON.
    dw = deferred_work_inventory(root)
    if dw["path"] and (dw["total"] or dw["legacy"] or dw["legacy_items"]):
        inventory["DW"] = {
            # The number a reader acts on: canonical open entries PLUS
            # un-migrated legacy bullets, which are just as outstanding.
            "count": dw["open_total"],
            "newest": ("legacy" if dw["legacy"]
                       else f"DW-{dw['newest_seq']}" if dw.get("newest_seq")
                       else "deferred-work.md"),
            "dirs": [dw["path"]],
            "total": dw["total"], "done": dw["done"],
            "malformed": dw["malformed"], "legacy": dw["legacy"],
            "legacy_items": dw["legacy_items"],
        }
    return inventory


# --- record identity ----------------------------------------------------------

# One record id, one file. `record_inventory` already accepts a title-derived
# slug (`E-056-gate-heading-guard.md`), but every reader that turned an id back
# into a path spelled `docs/experiments/<id>.md` literally: a story citing the
# approved `E-056` was told "no record exists", and the freshness plane read the
# same record as UNKNOWN. The record existed; the lookup could not recall it.
# Zero padding is the other half of the same mistake: `E-9` and `E-009` are one
# id, not two.
_RECORD_STEM_RE = re.compile(r"^(?P<kind>[A-Za-z]+)-(?P<num>\d+)(?:-(?P<slug>.+))?$")


def canonical_record_id(value: str) -> str:
    """`E-009`, `E-9` and `E-009-title` name one id: `E-9`."""
    raw = str(value or "").strip()
    m = _RECORD_STEM_RE.fullmatch(raw)
    if not m:
        return raw
    return f"{m.group('kind').upper()}-{int(m.group('num'))}"


#: A record STEM, as opposed to an id a caller typed: the kind is the uppercase
#: prefix every record kind uses (E, IR, SP, S, QR, PR), and a title slug may
#: follow the number. Kept separate from `_RECORD_STEM_RE` because a filename is
#: untrusted input: `note-1.md` is a note, and `notes-S-001.md` is neither a note
#: nor a record, while `E-9`, `E-009` and `E-009-title` are one record.
_RECORD_STEM_FILE_RE = re.compile(
    r"^(?P<kind>[A-Z]+)-(?P<num>\d+)(?:-(?P<slug>.+))?$")


def record_id_of_stem(stem: str) -> str:
    """Canonical id of a record filename stem, or '' when it names no record.

    Every reader that decides "is this a duplicate record?" must first agree on
    "is this a record at all?". Spelling a filename check as `E-001.md` made a
    title-slug record (`E-056-gate-heading-guard.md`) invisible to the guard and
    to the board's duplicate scan, so the second file for one id was admitted
    and every later lookup had to call the id ambiguous. Identity is the id, not
    the name.
    """
    m = _RECORD_STEM_FILE_RE.fullmatch(str(stem or "").strip())
    if not m:
        return ""
    return f"{m.group('kind')}-{int(m.group('num'))}"


# The .md walk behind record_files is shared through the CALLER's batch cache:
# a project with 29 records walked its documentation tree 29 times per call,
# 166k scandir calls and ~14s of a 15s hook budget. The cache belongs to
# freshness.batch(), which clears it when the operation closes, so a record
# written afterwards is never answered from an older snapshot.


def _md_files(root, rel_dirs, cache=None):
    """Every .md under rel_dirs, walked once per cache scope.

    `cache` is the caller's batch cache: inside one read operation the tree
    cannot change, so the walk is shared; the caller clears it when the
    operation closes, so a record written later is seen. With no cache the walk
    is not cached at all - a writer or a test that creates a record mid-run
    must never be answered from a snapshot taken before the write.
    """
    key = (str(root), tuple(rel_dirs))
    if cache is not None:
        hit = cache.get(key)
        if hit is not None:
            return hit
    files = []
    for rel in rel_dirs:
        try:
            files.extend(sorted((root / rel).rglob("*.md")))
        except OSError:
            continue
    if cache is not None:
        cache[key] = files
    return files


def record_files(project_root, record_id, rel_dirs, cache=None):
    """Every file under `rel_dirs` that claims `record_id` (sorted, unique).

    The walk is recursive: a home is a tree, and a superseded/archived record
    lives in a subdirectory of it (`stories/archive/S-004.md`). A flat glob read
    that record as "no record", so a hot key pointing at it was reported
    dangling and a chain gate could not find its own story — while the write
    gate, which scans recursively, already treated the id as claimed.

    Callers report zero matches as "no record" and more than one as an
    ambiguous id; neither may be decided by a guessed path.
    """
    wanted = canonical_record_id(record_id)
    root = pathlib.Path(project_root)
    found = []
    seen = set()
    for candidate in _md_files(root, rel_dirs, cache):
        try:
            if not candidate.is_file():
                continue
        except OSError:
            continue
        # A FILE is a record only under the filename grammar
        # (`record_id_of_stem`): the uppercase kind prefix is what the write
        # gate sees, so recall must not answer "record" for a stem the gate
        # reads as a plain note (`notes-1.md`, a lowercase `e-001.md`). The
        # permissive `canonical_record_id` stays for ids a caller TYPES.
        if record_id_of_stem(candidate.stem) != wanted:
            continue
        key = str(candidate)
        if key not in seen:
            seen.add(key)
            found.append(candidate)
    return found


# --- deferred-work ledger ------------------------------------------------------

# The ledger's writers resolve `{implementation_artifacts}` from module config;
# the core reader walks the canonical default first and the legacy homes older
# projects still carry, so a project on either layout is seen (E-108).
_DEFERRED_WORK_CANDIDATES = (
    "docs/development/native/deferred-work.md",
    "docs/development/deferred-work.md",
    "bmad-output/implementation-artifacts/deferred-work.md",
    "_bmad-output/implementation-artifacts/deferred-work.md",
)
_DW_ENTRY_RE = re.compile(r"^###\s+DW-(\d+)\s*:", re.MULTILINE)
_DW_STATUS_RE = re.compile(r"^status:[ \t]*(\S+)", re.MULTILINE | re.IGNORECASE)
_DW_SEVERITY_RE = re.compile(r"^severity:[ \t]*(\S+)", re.MULTILINE | re.IGNORECASE)
_DW_NEAR_MISS_RE = re.compile(r"^#{2,4}\s*DW-\d+\s*:", re.MULTILINE)
_LEGACY_SECTION_RE = re.compile(r"^##\s+Deferred", re.MULTILINE)
# Any heading of level 1-2 ends a legacy section: counting bullets to EOF made a
# mixed ledger over-report (a later section's bullets, or a canonical entry's own
# sub-list, were charged as un-migrated legacy work).
_SECTION_END_RE = re.compile(r"^#{1,2}\s", re.MULTILINE)
_LEGACY_BULLET_RE = re.compile(r"^\s*-\s+\S", re.MULTILINE)
_DW_SEVERITIES = frozenset({"critical", "high", "medium", "low"})


def _strip_fences(text: str) -> tuple[str, bool]:
    """Drop fenced documentation (the installed template shows the shape inside
    one) and report whether a fence was left open."""
    body_lines: list[str] = []
    fenced = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced:
            body_lines.append(line)
    return "\n".join(body_lines), fenced


def _ledger_has_entries(text: str) -> bool:
    body, _ = _strip_fences(text)
    return bool(_DW_ENTRY_RE.search(body) or _LEGACY_SECTION_RE.search(body))


def deferred_work_path(project_root: str | pathlib.Path) -> pathlib.Path | None:
    """The ledger this project owns, preferring a candidate with content.

    A fresh init installs an EMPTY canonical ledger. If a project also carries a
    populated legacy-path ledger, returning the canonical first would hide every
    real entry (E-108 review), so a populated candidate wins over an empty one.
    """
    root = pathlib.Path(project_root)
    existing = [root / rel for rel in _DEFERRED_WORK_CANDIDATES
                if (root / rel).is_file()]
    for candidate in existing:
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _ledger_has_entries(text):
            return candidate
    return existing[0] if existing else None


def deferred_work_inventory(project_root: str | pathlib.Path) -> dict:
    """Parse the deferred-work ledger (read-only, fail-open; E-108).

    Returns {path, total, open, done, malformed, legacy, legacy_items,
    open_total, newest_seq, duplicate_seq, severity_unspecified, error}.

    An entry is a `### DW-N:` block whose first `status:` line decides
    open/done; a block with no status (or an unrecognized one) is malformed.
    Fenced blocks are documentation (the installed template shows the shape
    inside a fence), not entries. A legacy `## Deferred from:` section is
    un-migrated work: its bullets are counted inside that section only, so a
    mixed ledger neither loses the leftovers nor charges a later section's
    bullets to them. `open_total` is the number a reader acts on — canonical
    open entries plus un-migrated legacy items — because a legacy bullet is
    exactly as much outstanding work as a `status: open` entry.

    `duplicate_seq` counts reused DW numbers (two `### DW-3:` blocks): the
    format says entries are never renumbered, so a duplicate means two
    different findings share one id and every cross-reference is ambiguous.
    `severity_unspecified` counts entries with no severity or an unrecognized
    one; the format makes the field optional, so this is a signal for writers,
    never a malformation.

    A missing ledger is zeros, never an error.
    """
    info = {"path": "", "total": 0, "open": 0, "done": 0,
            "malformed": 0, "legacy": False, "legacy_items": 0,
            "open_total": 0, "newest_seq": 0, "duplicate_seq": 0,
            "severity_unspecified": 0, "error": ""}
    path = deferred_work_path(project_root)
    if path is None:
        return info
    info["path"] = str(path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        info["error"] = str(exc)
        return info

    body, unterminated = _strip_fences(text)
    if unterminated:
        # A stray fence would hide every later entry: parse the raw text and
        # report the malformation instead of silently reporting zero.
        info["malformed"] += 1
        body = text

    starts = list(_DW_ENTRY_RE.finditer(body))
    for section in _LEGACY_SECTION_RE.finditer(body):
        end = _SECTION_END_RE.search(body, section.end())
        chunk = body[section.start():end.start() if end else len(body)]
        info["legacy_items"] += len(_LEGACY_BULLET_RE.findall(chunk))
    if info["legacy_items"] and not starts:
        info["legacy"] = True

    if not starts:
        info["malformed"] += len(_DW_NEAR_MISS_RE.findall(body))
        info["open_total"] = info["open"] + info["legacy_items"]
        return info
    info["total"] = len(starts)
    seen: set[int] = set()
    for index, match in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(body)
        block = body[match.end():end]
        status = _DW_STATUS_RE.search(block)
        if not status:
            info["malformed"] += 1
        elif status.group(1).lower().startswith("done"):
            info["done"] += 1
        elif status.group(1).lower().startswith("open"):
            info["open"] += 1
        else:
            info["malformed"] += 1
        severity = _DW_SEVERITY_RE.search(block)
        if not severity or severity.group(1).lower() not in _DW_SEVERITIES:
            info["severity_unspecified"] += 1
        try:
            sequence = int(match.group(1))
        except ValueError:  # >4300 digits: Python's int-string limit
            info["malformed"] += 1
            continue
        if sequence in seen:
            info["duplicate_seq"] += 1
        seen.add(sequence)
        info["newest_seq"] = max(info["newest_seq"], sequence)
    info["open_total"] = info["open"] + info["legacy_items"]
    return info


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

    def norm(value: str | pathlib.Path) -> str:
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
    # E-200: this line named four servers and then `, …` — no count, so seven
    # enabled servers read as four, and the trailing `line[:200]` could cut a
    # name in half AND eat the marker that admitted it (the same defect E-198
    # removed from the sprint line, from which this one was modelled).
    head = f"mcp: {len(servers)} active server(s) — "
    return _bounded_parts_line(head, [s["name"] for s in servers],
                               limit=200, sep=", ")


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

# The epic PLAN and the sprint file are two different ledgers, and only the
# second one is a subset: a sprint-status file lists the stories that already
# have a draft (the shipped template's SCOPE NOTE says exactly that) while epic
# status is computed over the full plan. Counting membership alone therefore
# cannot answer "is this epic finished?" — the digest told a project with three
# planned-but-unwritten slices to "roll the epic up to done" because the two
# stories it could see were done (m-tesil EP-1, 2026-11-06). Read the plan when
# it is on disk so the advice can name what the sprint file cannot see.
_PLAN_CANDIDATES = (
    "docs/planning/epics.md",
    "docs/planning/*epic*.md",
    "bmad-output/planning-artifacts/epics.md",
    "_bmad-output/planning-artifacts/epics.md",
    "bmad-output/planning-artifacts/*epic*.md",
)
_EPIC_SECTION_RE = re.compile(r"^##\s+(?:EP|Epic)[\s\-]*(\d+)\b", re.MULTILINE)
_PLAN_SLICE_RE = re.compile(r"^###\s+(S-\d+)\b", re.MULTILINE)


def epic_plan_path(project_root: str | pathlib.Path,
                   planning_dir: str | None = None) -> str | None:
    """The readable epic plan document, canonical path first (None = absent)."""
    root = pathlib.Path(project_root)
    candidates = ([f"{planning_dir.rstrip('/')}/epics.md"] if planning_dir else [])
    candidates.extend(_PLAN_CANDIDATES)
    for rel in candidates:
        for hit in sorted(root.glob(rel)):
            if hit.is_file():
                return str(hit)
    return None


def epic_plan_slices(project_root: str | pathlib.Path,
                     planning_dir: str | None = None) -> dict:
    """{epic_key: {"slices": n, "path": …}} — planned slice count per epic.

    A slice is a ``### S-NNN`` heading under a ``## EP-N`` section. Only that
    shape is counted: a plan written differently yields ``{}`` and the caller
    must then say it could not check the plan rather than claim coverage.
    Absence is "unknown", never "zero".
    """
    path = epic_plan_path(project_root, planning_dir)
    if path is None:
        return {}
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    sections = [(m.start(), int(m.group(1)))
                for m in _EPIC_SECTION_RE.finditer(text)]
    if not sections:
        return {}
    out: dict = {}
    for idx, (pos, num) in enumerate(sections):
        end = sections[idx + 1][0] if idx + 1 < len(sections) else len(text)
        slices = _PLAN_SLICE_RE.findall(text[pos:end])
        if slices:
            out[f"epic-{num}"] = {"slices": len(slices), "path": path}
    return out


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
    - ``epic_plan``       — per epic: {slices, path} counted from the epic plan
      document; ``{}`` when no plan is readable. Membership above is the subset
      that has a story draft, so an epic is only finished when the plan agrees:
      compare ``epic_progress[k]["total"]`` against ``epic_plan[k]["slices"]``
      and resolve the plan→record mapping before claiming completion.

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
            "counts": counts, "epic_progress": epic_progress,
            # The plan is the authority on whether an epic is finished; the
            # sprint file only knows the slices that have a draft. Empty dict
            # means "no readable plan" — callers must say that, not assume 0.
            "epic_plan": epic_plan_slices(root)}


_SPRINT_LINE_LIMIT = 200


def _counted_list(items: list, limit: int, *, noun: str) -> str:
    """The shared reminder-counting join, however this module was loaded.

    `modules.counting` is a sibling in the package, but orient.py loads this
    file by path with no package context (so the shipped digest keeps working
    standalone), and a bare `from .counting import ...` raises there. Prefer
    the package import; fall back to loading the sibling by path.
    """
    try:
        from .counting import counted_list  # noqa: PLC0415
        return counted_list(items, limit, noun=noun)
    except ImportError:
        import importlib.util  # noqa: PLC0415
        spec = importlib.util.spec_from_file_location(
            "state_counting", pathlib.Path(__file__).resolve().with_name("counting.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        return module.counted_list(items, limit, noun=noun)


def format_sprint_line(summary: dict) -> str:
    """One bounded line for a session-start inject (max 200 chars).

    Leads with the epic mismatches (the only ⚠ parts: an epic claimed
    in-progress with all its stories done, or done with gaps), then the
    actionable states (in-progress, review), counting the rest. Warnings lead
    because the bound can drop trailing parts: a board big enough to overflow
    still shows its discrepancies, and only the counts fall off the end.
    """
    if not summary.get("found"):
        return ""
    stories = summary.get("stories") or {}
    epics = summary.get("epics") or {}
    counts = summary.get("counts") or {}
    epic_progress = summary.get("epic_progress") or {}
    epic_plan = summary.get("epic_plan") or {}

    parts: list[str] = []
    warnings: list[str] = []
    # Order the vocabulary so the important states lead.
    for status in ("in-progress", "review", "ready-for-dev", "done", "backlog"):
        keys = [k for k, s in sorted(stories.items()) if s == status]
        if not keys:
            continue
        if status in ("done", "backlog") or len(keys) > 3:
            # E-211: this group read `16 done (a, b, c, …)` — three names and a
            # bare ellipsis, so the reader could not tell thirteen from one.
            parts.append(f"{len(keys)} {status} "
                         f"({_counted_list(keys, 3, noun='story')})")
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
            planned = (epic_plan.get(epic_key) or {}).get("slices")
            if planned:
                # The sprint file lists only drafted stories; the plan is the
                # authority. Never say "all done" while the plan has slices the
                # board cannot account for.
                warnings.append(f"⚠ {epic_key}: {prog['done']}/{prog['total']} listed "
                             f"done, plan has {planned} slices — verify mapping "
                             f"before roll-up")
            else:
                warnings.append(f"⚠ {epic_key}: stories all done — epic still "
                             f"in-progress, roll up to done + run the retro")
        elif prog["total"] and status == "done" and prog["done"] < prog["total"]:
            warnings.append(f"⚠ {epic_key}: marked done but "
                         f"{prog['total'] - prog['done']} story/stories not done")
        elif status in ("backlog", "") and prog.get("started"):
            warnings.append(f"⚠ {epic_key}: backlog but {prog['started']} of "
                         f"{prog['total']} story/stories started — bump the epic")
    if not parts and not warnings:
        return ""
    return _bounded_sprint_line(warnings + parts)


def _bounded_parts_line(head: str, parts: list[str],
                        limit: int = _SPRINT_LINE_LIMIT,
                        sep: str = "; ") -> str:
    """Join whole parts after `head` inside `limit`, counting the rest.

    The bound used to slice the finished line at 197 characters, so a realistic
    board (8 in-progress, 5 review, one epic lag) ended
    `⚠ epic-2: stories all done — epic …`: the finding survived and its action
    ("roll up to done + run the retro") did not, and every ⚠ after the cut
    vanished behind one silent `…`. Parts are the semantic units, so the line
    now drops whole parts and names how many it withheld (E-198).

    `sep` is the separator between parts; the withheld count uses it too, so an
    ``", … (+N more)"`` list reads as one list. A part wider than the whole line
    is shortened by `_elide_part` instead of being dropped, and its successor is
    still counted.
    """
    marker = lambda hidden: f"{sep}… (+{hidden} more)"
    out = head
    for index, part in enumerate(parts):
        between = "" if index == 0 else sep
        # Reserve the marker this break would need, so the count always fits.
        hidden = len(parts) - index
        reserve = len(marker(hidden)) if index != len(parts) - 1 else 0
        if len(out) + len(between) + len(part) + reserve > limit:
            if index == 0:
                # One part wider than the whole line: shorten it, keeping its
                # leading identity (a status group's "status:" and the first
                # key), and count the parts still to come.
                tail = len(parts) - 1
                room = limit - len(head) - (len(marker(tail)) if tail else 0)
                out = head + _elide_part(part, max(room, 20))
                return out + (marker(tail) if tail else "")
            return out + marker(hidden)
        out += between + part
    return out


def _bounded_sprint_line(parts: list[str], limit: int = _SPRINT_LINE_LIMIT) -> str:
    """The sprint line: the shared part-bounded join under its own head."""
    return _bounded_parts_line("sprint: ", parts, limit)


def _elide_part(part: str, room: int) -> str:
    """Shorten one oversized part, preferring a boundary that keeps its head.

    A word-boundary cut is the nicer shape, but only when it leaves most of the
    room in use: a group whose key is one giant token has its last space right
    after `in-progress:`, and cutting there dropped the only story the part was
    about (`sprint: in-progress…`). When the boundary sits in the first half,
    keep the token's prefix instead — the elision is marked either way.
    """
    cut = part[:max(room - 1, 1)]  # `room` includes the `…` this adds
    space = max(cut.rfind(" "), cut.rfind(";"))
    if space >= room // 2:
        cut = cut[:space].rstrip(";,:—–- ")
    else:
        cut = cut.rstrip(";,:—–- ")
    return cut + "…"

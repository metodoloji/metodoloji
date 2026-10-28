"""Story record, chain, and embedded quality validation.

The public compatibility functions remain in ``guard``; implementations here
bind shared dependencies from that module per call so existing callers and
focused monkeypatch seams keep their behavior.
"""

from __future__ import annotations

import pathlib
import re

_STORY_READ_ROOTS = (
    ("docs/development/stories", "S-*.md"),
    ("docs/development/native", "*.md"),
)

# Native story drafts name their key explicitly (`- **Story key:** 1-1-slug`);
# the title line is prose (`# Story 1.1: …`) and key extraction from it yields
# junk, so the explicit field wins when present.
_STORY_KEY_FIELD_RE = re.compile(
    r"\*\*Story key:\*\*\s*([A-Za-z0-9][A-Za-z0-9._-]*)", re.IGNORECASE)

def _iter_story_files(root: str):
    """Story files across the methodology + Seçenek A native roots (sorted)."""
    from . import guard as _guard
    _STORY_READ_ROOTS = _guard._STORY_READ_ROOTS
    pathlib = _guard.pathlib
    for rel, pattern in _STORY_READ_ROOTS:
        base = pathlib.Path(root) / rel
        if not base.is_dir():
            continue
        for f in sorted(base.glob(pattern)):
            if f.name.startswith("_") or not f.is_file():
                continue
            yield f

# A story names the sprint it belongs to (`- **Sprint:** SP-001`); an SP record
# names its IR, an IR its experiment. The id may carry further numeric segments
# (a date-named record, `SP-2026-01-15` / `IR-2026-09-28`).
_SP_REF_RE = re.compile(r"\bSP-(\d+(?:-\d+)*)\b", re.IGNORECASE)
_IR_REF_RE = re.compile(r"\bIR-(\d+(?:-\d+)*)\b", re.IGNORECASE)
_E_REF_RE = re.compile(r"\bE-(\d+(?:-\d+)*)\b", re.IGNORECASE)

def _first_heading(text: str) -> str:
    """A record's identity line (`# Sprint: SP-001 — …`) or "" when it has none."""
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            return line
    return ""

def _record_id_token(match: "re.Match[str]") -> "re.Pattern[str]":
    """Regex matching the SAME record id a document references, zero-pad tolerant.

    Segment-wise on purpose. `SP-9` must still find `SP-009.md` (the record
    convention zero-pads, story rows often don't), but `SP-2026-01-15` must NOT
    find `SP-2026-11-02` — a leading-number-only match let ANY sprint of the
    same year satisfy a story's reference, so the SP gate could pass with the
    wrong record and no January record at all (found by the 2026-10-01 scratch
    run of bmad-sprint-planning, whose SKILL named the record `SP-{date}`).
    """
    from . import guard as _guard
    re = _guard.re
    kind = match.group(0).split("-", 1)[0]
    segs = [s.lstrip("0") or "0" for s in match.group(1).split("-")]
    body = "-".join(rf"0*{re.escape(s)}" for s in segs)
    return re.compile(rf"\b{re.escape(kind)}-{body}\b", re.IGNORECASE)

# A record DECLARES its subject in a labelled field or table row, never in prose:
# `scripts/sync-story-qr.py` calls the `| Story |` row "the fact" it trusts, and
# `scripts/create-qr-record.py` writes exactly that row; the QR/PR templates write
# `- **Story:**` / `- **Stories:**`. A declaration's indented continuation lines
# belong to it — the SP template lists its story keys as sub-bullets under
# `- **Stories:**`.
_SUBJECT_LABEL_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?:[-*][ \t]+)?\|?[ \t]*\*{0,2}"
    r"(?P<label>stor(?:y|ies)|story key|story keys|native story|target)"
    r"\*{0,2}[ \t]*[:|]",
    re.IGNORECASE)

# Story identifiers a declaration may use: the methodology key (`S-001`,
# `S-001-slug`) or the native key (`1-1-slug`). `.md` paths are reduced to stems.
_DECLARED_KEY_RE = re.compile(
    r"\b(?:S-\d+(?:-[a-z0-9-]+)?|\d+-\d+-[a-z][a-z0-9-]*)\b", re.IGNORECASE)
_DECLARED_PATH_RE = re.compile(r"[\w./\\-]*[A-Za-z0-9_-]+\.md")

def _names_in(text: str) -> set[str]:
    """Story keys / path stems appearing in a bare text (heading, id line)."""
    from . import guard as _guard
    _DECLARED_KEY_RE = _guard._DECLARED_KEY_RE
    _DECLARED_PATH_RE = _guard._DECLARED_PATH_RE
    pathlib = _guard.pathlib
    names = {k.lower() for k in _DECLARED_KEY_RE.findall(text)}
    names |= {pathlib.PurePosixPath(p.replace("\\", "/")).stem.lower()
              for p in _DECLARED_PATH_RE.findall(text)}
    return names

def _declared_story_names(text: str) -> set[str]:
    """Story keys/paths a record DECLARES as its subject (never a prose mention)."""
    from . import guard as _guard
    _SUBJECT_LABEL_RE = _guard._SUBJECT_LABEL_RE
    _names_in = _guard._names_in
    names: set[str] = set()
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = _SUBJECT_LABEL_RE.match(line)
        if not m:
            continue
        names |= _names_in(line[m.end():])
        indent = len(m.group("indent"))
        for nxt in lines[i + 1:]:
            if not nxt.strip() or len(nxt) - len(nxt.lstrip()) <= indent:
                break
            names |= _names_in(nxt)
    return names

def _declaration_covers(names: set[str], story_key: str, story_stem: str) -> bool:
    """True when a declaration names this story (its key or its file stem)."""
    if story_key.lower() in names or story_stem.lower() in names:
        return True
    # A bare `S-060` declaration covers `S-060-scope-guard-hermeticity.md`.
    return any(story_stem.lower().startswith(f"{n}-") for n in names)

def _record_covers_story(text: str, path: pathlib.Path, story_key: str,
                         story_stem: str) -> bool:
    """A record covers a story when it DECLARES it or its identity names it.

    Never when the story key merely appears somewhere in the body: the shipped
    QR-015 named 1-1-bench-in-ci only inside a regression note about a moved
    test path, and that mention alone counted as the story's quality record
    (2026-10-01 audit of the same looseness fixed for SP ids).
    """
    from . import guard as _guard
    _declaration_covers = _guard._declaration_covers
    _declared_story_names = _guard._declared_story_names
    _first_heading = _guard._first_heading
    _names_in = _guard._names_in
    names = _declared_story_names(text) | _names_in(_first_heading(text)) \
        | _names_in(path.name)
    return _declaration_covers(names, story_key, story_stem)

def _story_key_of(content: str, path: pathlib.Path) -> str:
    """The story key for a story file: explicit field, then title, then slug,
    then filename stem. Every caller needs the same precedence."""
    from . import guard as _guard
    _STORY_KEY_FIELD_RE = _guard._STORY_KEY_FIELD_RE
    extract_story_key_from_content = _guard.extract_story_key_from_content
    re = _guard.re
    m = _STORY_KEY_FIELD_RE.search(content)
    if m:
        return m.group(1).strip()
    key = extract_story_key_from_content(content)
    if key:
        return key
    m = re.search(r"(\d+-\d+-[a-z][a-z0-9-]+)", content, re.IGNORECASE)
    if m:
        return m.group(1)
    return path.stem

def _story_paths_for_keys(root: str, keys: "list[str]") -> dict[str, str]:
    """Map each missing story key to its project-relative file path.

    A remedy the gate prints must name the file the operator actually has, not
    a literal ``S-XXX`` placeholder — the observed E-028 session hand-substituted
    the path (and then the script path failed too). Reuses ``_iter_story_files``
    and ``_story_key_of`` so the mapping is the SAME precedence the gate itself
    matched the story by; a key that resolves to nothing is left out (the caller
    falls back to the key).
    """
    from . import guard as _guard
    _iter_story_files = _guard._iter_story_files
    _story_key_of = _guard._story_key_of
    pathlib = _guard.pathlib
    wanted = set(keys)
    found: dict[str, str] = {}
    if not wanted:
        return found
    base = pathlib.Path(root)
    for story_file in _iter_story_files(root):
        try:
            content = story_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        key = _story_key_of(content, story_file)
        if key in wanted and key not in found:
            try:
                found[key] = story_file.relative_to(base).as_posix()
            except ValueError:
                found[key] = story_file.as_posix()
    return found

def _methodology_mirror_map(root: str) -> dict[str, str]:
    """Native story name -> the methodology record key that mirrors it.

    A generated record names its native story in its own ``| Native Story |``
    row (read by ``_declared_story_names``), so the link is readable without
    guessing. Keys are both the declared key and its path stem, lowercased.
    E-032: the commit gate read this link only native -> record; a QR covering
    the record left the mirrored native story looking unrecorded.
    """
    from . import guard as _guard
    _declared_story_names = _guard._declared_story_names
    _is_methodology_record = _guard._is_methodology_record
    pathlib = _guard.pathlib
    out: dict[str, str] = {}
    stories_dir = pathlib.Path(root) / "docs" / "development" / "stories"
    if not stories_dir.is_dir():
        return out
    for rec in sorted(stories_dir.glob("S-*.md")):
        if rec.name.startswith("_"):
            continue
        try:
            text = rec.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not _is_methodology_record(text):
            continue
        # The record's own FILE STEM is the key a record of this kind is
        # matched by (`_declaration_covers` compares against `story_stem`), so
        # a QR declaring `| Story | S-002 |` names S-002.md. `_story_key_of`
        # deliberately returns the NATIVE slug for a generated record, which
        # is not what such a declaration carries.
        rec_key = rec.stem
        for name in _declared_story_names(text):
            out.setdefault(name.lower(), rec_key)
            out.setdefault(
                pathlib.PurePosixPath(name.replace("\\", "/")).stem.lower(),
                rec_key)
    return out

def _find_done_stories_without_record(root: str, record_glob: str, record_dir: str,
                                      require_sp_ref: bool = False) -> list[str]:
    """Find stories with Status: done that lack a record of the given type.

    Args:
        record_glob: glob for record files (e.g. 'QR-*.md', 'SP-*.md').
        record_dir: directory to scan (project-relative, e.g. 'docs/quality').
        require_sp_ref: only check stories that reference an SP record.

    Returns list of story keys (e.g. '1-2-user-auth' or 'S-001') missing the record.
    """
    from . import guard as _guard
    _SP_REF_RE = _guard._SP_REF_RE
    _declaration_covers = _guard._declaration_covers
    _declared_story_names = _guard._declared_story_names
    _first_heading = _guard._first_heading
    _iter_story_files = _guard._iter_story_files
    _methodology_mirror_map = _guard._methodology_mirror_map
    _record_id_token = _guard._record_id_token
    _story_key_of = _guard._story_key_of
    pathlib = _guard.pathlib
    story_status = _guard.story_status
    rec_dir = pathlib.Path(root) / record_dir

    # Collect all record content to search for story references.
    # rec_id_texts holds what counts as a record's OWN identity — its filename
    # plus its heading line, never its body — for the SP numeric-id match;
    # rec_content is the plain concatenation the story-key search reads.
    rec_id_texts: list[str] = []
    rec_names: set[str] = set()
    if rec_dir.is_dir():
        for rec_file in rec_dir.glob(record_glob):
            try:
                text = rec_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rec_id_texts.append(rec_file.name)
            rec_id_texts.append(_first_heading(text))
            rec_names |= _declared_story_names(text)

    missing: list[str] = []
    mirror: dict[str, str] | None = None
    for story_file in _iter_story_files(root):
        try:
            content = story_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if story_status(content) != "done":
            continue
        sp_ref = None
        if require_sp_ref:
            # Only check stories that reference an SP record
            sp_ref = _SP_REF_RE.search(content)
            if not sp_ref:
                continue
        story_key = _story_key_of(content, story_file)
        # Identity, not prose: some record of this kind must DECLARE the story
        # (a `Story`/`Stories` field or table row, or the SP template's Stories
        # list). A body mention used to be enough — see _record_covers_story.
        if _declaration_covers(rec_names, story_key, story_file.stem):
            continue
        # A generated methodology record is the SAME work unit as the story it
        # mirrors: its `| Native Story |` field names that story, and
        # scripts/sync-story-qr.py resolves its QR through exactly that hop
        # ("a methodology record owns its QR through the native story it
        # names"). Read the same link here, or a QR that declares the native
        # story (QR-014 -> S-056) leaves the generated record (S-057) looking
        # unrecorded while the reconciler already calls it owned.
        if any(_declaration_covers(rec_names, n, n)
               for n in _declared_story_names(content)):
            continue
        # …and the SAME hop read BACKWARDS (E-032): a QR that covers the
        # generated methodology record also covers the native story that record
        # mirrors — they are ONE work unit. Without this, a QR declaring
        # `| Story | S-002 |` left the done native story `1-2-…` looking
        # unrecorded, and the observed session's `git commit` was denied on it
        # although the reconciler already called the pair owned.
        if mirror is None:
            mirror = _methodology_mirror_map(root)
        mirror_key = (mirror.get(story_key.lower())
                      or mirror.get(story_file.stem.lower()))
        if mirror_key and _declaration_covers(
                rec_names, mirror_key,
                pathlib.PurePosixPath(mirror_key).stem):
            continue
        # SP records may be referenced by ID even when the story key isn't in
        # content. ID equality is NUMERIC and SEGMENT-WISE (SP-9 == SP-009 —
        # the record-name convention zero-pads while the story row often
        # doesn't; a string match false-denied every padded record, fikir
        # deep-check 2026-10-01) and the id must appear in some record's own
        # IDENTITY — its filename or heading — never merely in a body: a bare
        # substring check let SP-12 match SP-123's name (different sprint!), a
        # leading-number-only check let SP-2026-01-15 match SP-2026-11-02 (same
        # year), and a body-wide check let record N's "SP-002 opens next …"
        # mention satisfy sprint 2's stories before their record existed
        # (2026-10-01 second-sprint run; the shipped records really do carry
        # forward references like "carried to SP-004").
        if sp_ref is not None:
            sp_token = _record_id_token(sp_ref)
            if any(sp_token.search(entry) for entry in rec_id_texts):
                continue
        missing.append(story_key)
    return missing

def _find_done_stories_without_qr(root: str) -> list[str]:
    """Find stories with Status: done that lack a QR record."""
    from . import guard as _guard
    _find_done_stories_without_record = _guard._find_done_stories_without_record
    return _find_done_stories_without_record(root, "QR-*.md", "docs/quality")

def _find_done_stories_without_pr(root: str) -> list[str]:
    """Find stories with Status: done that lack a PR record.

    Restored: the deploy gate (Gate 4) calls this when include_pr is set;
    commit 48ed1f2 dropped the definition while the call site survived,
    raising NameError at deploy time.
    """
    from . import guard as _guard
    _find_done_stories_without_record = _guard._find_done_stories_without_record
    return _find_done_stories_without_record(root, "PR-*.md", "docs/development")

def _find_done_stories_without_sp(root: str) -> list[str]:
    """Find stories with Status: done that reference an SP but lack SP record."""
    from . import guard as _guard
    _find_done_stories_without_record = _guard._find_done_stories_without_record
    return _find_done_stories_without_record(root, "SP-*.md", "docs/development",
                                             require_sp_ref=True)

def _find_done_stories_without_ir(root: str) -> list[str]:
    """Find done stories when no IR record exists (Kapi 1 gate bypassed).

    IR is a project-level readiness record — if ANY done stories exist but
    NO IR records exist in docs/development/, the readiness gate was skipped.
    Returns list of story keys if IR is missing, empty list if IR exists.
    """
    from . import guard as _guard
    _iter_story_files = _guard._iter_story_files
    _story_key_of = _guard._story_key_of
    pathlib = _guard.pathlib
    story_status = _guard.story_status
    dev_dir = pathlib.Path(root) / "docs" / "development"

    # Check if ANY IR record exists
    has_ir = False
    if dev_dir.is_dir():
        for ir_file in dev_dir.glob("IR-*.md"):
            if ir_file.name.startswith("_"):
                continue
            has_ir = True
            break
    if has_ir:
        return []  # IR gate was evaluated — OK

    # No IR records exist — check if there are done stories
    missing: list[str] = []
    for story_file in _iter_story_files(root):
        try:
            content = story_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if story_status(content) != "done":
            continue
        missing.append(_story_key_of(content, story_file))
    return missing

def _board_key_identity(content: str, path: pathlib.Path) -> str:
    """A story file's own identity for board-key resolution.

    Deliberately NOT ``_story_key_of``'s last fallback (an ``N-N-slug`` anywhere
    in the body): a METHODOLOGY record names its subject by label, so a slug in
    its prose is a mention, not an identity. Measured while closing E-046 — a
    record whose regression note named a since-renamed story resolved that
    board key by itself, and the phantom read as covered. The story gate keeps
    the wider precedence because it validates story files, where a draft may
    carry its key only in the body.
    """
    from . import guard as _guard
    _STORY_KEY_FIELD_RE = _guard._STORY_KEY_FIELD_RE
    extract_story_key_from_content = _guard.extract_story_key_from_content
    m = _STORY_KEY_FIELD_RE.search(content)
    if m:
        return m.group(1).strip()
    return extract_story_key_from_content(content) or path.stem

def board_story_keys_without_records(root: str) -> list[str]:
    """Sprint-board story keys that resolve to no story record.

    Every chain rule in this module starts from a story FILE
    (``_iter_story_files``) and looks outward, so a key the board carries with
    no story behind it is invisible to all of them. Measured 2026-10-15 on a
    board carrying ``9-9-ghost-story-that-does-not-exist``: the gates returned
    nothing, ``check-methodology.sh`` never printed the key, and the only trace
    was an in-progress count one too high — a nudge to go verify records for a
    story that does not exist. CHECK 6's opening comment had promised this hunt
    ("stories in sprint-status that lack methodology records") since the
    counting was written; nothing implemented it.

    A key resolves when a story file is NAMED by it (``_board_key_identity``: the
    explicit field, the title, else the filename) or when a record DECLARES it —
    the vocabulary the write-time gates use, where a prose mention deliberately
    does not count (``_record_covers_story``).
    """
    from . import guard as _guard
    _board_key_identity = _guard._board_key_identity
    _chain_text = _guard._chain_text
    _iter_story_files = _guard._iter_story_files
    _record_covers_story = _guard._record_covers_story
    state_mod = _guard.state_mod
    from .config import MAX_STORY_COUNT  # noqa: PLC0415 — same bound as the gate

    summary = state_mod.sprint_summary(root)
    if not summary.get("found"):
        return []  # no board: there are no keys to resolve
    keys: list[str] = sorted(summary.get("stories", {}))
    if not keys:
        return []
    wanted = {k.lower() for k in keys}
    resolved: set[str] = set()
    seen = 0
    for story_file in _iter_story_files(root):
        seen += 1
        if seen > MAX_STORY_COUNT:  # Bounds check (MEDIUM #11)
            break
        try:
            content = _chain_text(story_file)
        except OSError:
            continue
        identity = _board_key_identity(content, story_file).lower()
        if identity in wanted:
            resolved.add(identity)
        if story_file.stem.lower() in wanted:
            resolved.add(story_file.stem.lower())
        for candidate in keys:
            if candidate.lower() in resolved:
                continue
            if _record_covers_story(content, story_file, candidate, candidate):
                resolved.add(candidate.lower())
    return [k for k in keys if k.lower() not in resolved]

def _validate_methodology_chain(content: str, rel_path: str, root: str = "") -> tuple[bool, str]:
    """Validate that the methodology chain is intact for a story file.

    Checks:
    - If story status is 'done', QR record must exist
    - If story status is 'review', methodology record must exist
    - If story references SP-XXX, SP record must exist
    - If story references SP-XXX, SP must reference IR which must reference E (backreference chain)
    - Every AC must reference an approved experiment E-NNN

    Returns (is_valid, reason).
    """

    # Bind guard dependencies at call time to preserve existing patch seams.
    from . import guard as _guard
    _E_REF_RE = _guard._E_REF_RE
    _IR_REF_RE = _guard._IR_REF_RE
    _SP_REF_RE = _guard._SP_REF_RE
    _STORY_KEY_FIELD_RE = _guard._STORY_KEY_FIELD_RE
    _chain_text = _guard._chain_text
    _cited_experiment_problem = _guard._cited_experiment_problem
    _dangling_test_problem = _guard._dangling_test_problem
    _first_heading = _guard._first_heading
    _is_methodology_record = _guard._is_methodology_record
    _parse_ac_metadata = _guard._parse_ac_metadata
    _plugin_script_cmd = _guard._plugin_script_cmd
    _record_covers_story = _guard._record_covers_story
    _record_id_token = _guard._record_id_token
    extract_story_key_from_content = _guard.extract_story_key_from_content
    os = _guard.os
    pathlib = _guard.pathlib
    re = _guard.re
    repo_root = _guard.repo_root
    story_status = _guard.story_status
    from .config import MAX_DUPLICATE_CHECK_RECORDS, MAX_STORY_COUNT

    issues = []
    if not root:
        root = repo_root({})
    root = os.path.abspath(root)

    # Extract story status via the shared reader (field form or bridge
    # table row). Line-start anchored so indented YAML 'status:' keys never
    # match (same rationale as _validate_story_metadata).
    status = story_status(content)
    if not status:
        return True, ""  # No status = not a story file

    # Story key, the shared precedence MINUS the filename fallback: the explicit
    # `- **Story key:** …` field first (a native draft's `# Story 1.1: …` title
    # used to be read as the key "1.1:", so the gate then hunted a QR record for
    # `1.1:` and a correctly-declared native story could never satisfy it), then
    # the title, then an N-N-slug anywhere, then the path. An S-record generated
    # by create-methodology-record.py has none of those, and skipping it here is
    # deliberate: this function validates STORY files, and widening it to the
    # generated records is a separate decision (they carry a `| Sprint Ref |`
    # claim, not a story's own `Sprint:` field).
    story_key = ""
    m_key = _STORY_KEY_FIELD_RE.search(content)
    if m_key:
        story_key = m_key.group(1).strip()
    if not story_key:
        story_key = extract_story_key_from_content(content)
    if not story_key:
        key_match = re.search(r"(\d+-\d+-[a-z][a-z0-9-]+)", content, re.IGNORECASE)
        if key_match:
            story_key = key_match.group(1)
    if not story_key:
        key_match = re.search(r"(\d+-\d+-[a-z][a-z0-9-]+)", rel_path, re.IGNORECASE)
        if key_match:
            story_key = key_match.group(1)
    story_stem = pathlib.Path(rel_path).stem
    if not story_key:
        return True, ""

    # Check 1: If status is 'done', QR record must exist.
    # ponytail: read each QR file at most once per story write — the chain
    # cache below keeps (mtime_ns, size) so repeated writes don't re-read
    # unchanged records.
    if status == "done":
        qr_dir = pathlib.Path(root) / "docs" / "quality"
        if qr_dir.is_dir():
            # Look for QR record that references this story
            found_qr = False
            count = 0
            for qr_file in qr_dir.glob("QR-*.md"):
                count += 1
                if count > MAX_DUPLICATE_CHECK_RECORDS:  # Bounds check (MEDIUM #11)
                    break
                try:
                    qr_content = _chain_text(qr_file)
                    if _record_covers_story(qr_content, qr_file, story_key,
                                            story_stem):
                        found_qr = True
                        break
                except OSError:
                    pass
            if not found_qr:
                issues.append(
                    f"Story status is 'done' but no QR record found for {story_key}. "
                    f"A QR covers a story only when it DECLARES it (a `| Story | … |` "
                    f"row or a `- **Story:** …` field) — a mention in the body is "
                    f"not a record. Run: "
                    f"{_plugin_script_cmd('scripts/create-qr-record.py', '--story', rel_path)}"
                )

    # Check 2: If status is 'review', methodology record must exist.
    # Self-evident records skip the peer hunt: a file that IS a generated
    # methodology record (title `# Methodology Record:` or a `| Native
    # Story |` field row) cannot lack one — hunting peers for it only
    # matched on coincidence (a debug log quoting the slug). Native-shaped
    # drafts without record markers are still hunted exactly as before.
    if status in ("review", "done") and not _is_methodology_record(content):
        meth_dir = pathlib.Path(root) / "docs" / "development" / "stories"
        if meth_dir.is_dir():
            # S-014 fix (E-010, GATE-OK-E-010-44abfab68a12b8b4f46ba8984dfa3f89):
            # exclude the story file itself from the methodology search. The
            # story mentions its own key, so without this check, the glob
            # trivially matched and the methodology check false-positived.
            target_name = pathlib.Path(rel_path).name
            found_meth = False
            count = 0
            for meth_file in meth_dir.glob("S-*.md"):
                count += 1
                if count > MAX_STORY_COUNT:  # Bounds check (MEDIUM #11)
                    break
                if meth_file.name == target_name:
                    continue
                try:
                    meth_content = _chain_text(meth_file)
                    if _record_covers_story(meth_content, meth_file, story_key,
                                            story_stem):
                        found_meth = True
                        break
                except OSError:
                    pass
            if not found_meth:
                issues.append(
                    f"Story status is '{status}' but no methodology record found for {story_key}. "
                    f"A record covers a story when it declares it (`| Native Story | … |` "
                    f"or a Story field) or its own title/filename names it. "
                    f"Run: {_plugin_script_cmd('scripts/create-methodology-record.py', '--story', rel_path)}"
                )

    # Check 3: If story references SP-XXX, SP record must exist AND backreference chain S→SP→IR→E
    sprint_match = _SP_REF_RE.search(content)
    if sprint_match:
        sp_id = sprint_match.group(0)  # e.g. SP-001
        # Same segment-wise id match the done-story gate uses: the record may
        # be named by the story key OR by the sprint id, and the id comparison
        # must not let a different sprint of the same year stand in.
        sp_token = _record_id_token(sprint_match)
        dev_dir = pathlib.Path(root) / "docs" / "development"
        if dev_dir.is_dir():
            found_sp = False
            sp_file_found = None
            for sp_file in dev_dir.glob("SP-*.md"):
                try:
                    sp_content = _chain_text(sp_file)
                    # The id must be the record's identity (filename or heading),
                    # not a passing body mention — see _find_done_stories_without_sp.
                    if (story_key in sp_content
                            or sp_token.search(sp_file.name)
                            or sp_token.search(_first_heading(sp_content))):
                        found_sp = True
                        sp_file_found = sp_file
                        break
                except OSError:
                    pass
            if not found_sp:
                issues.append(
                    f"Story references {sp_id} but no SP record found for {story_key}. "
                    f"Run bmad-sprint-planning to create SP record."
                )
            else:
                # NEW: Check backreference chain S→SP→IR→E (HIGH #4 / HIGH #8)
                # SP must reference IR, IR must reference E
                if sp_file_found:
                    sp_content = _chain_text(sp_file_found)
                    # Look for IR-NNN reference in SP
                    ir_match = _IR_REF_RE.search(sp_content)
                    if ir_match:
                        ir_id = ir_match.group(0)
                        # The id must be the IR record's OWN identity (filename or
                        # heading): an IR record that merely names IR-001 ("what
                        # IR-001 left open") is not the record the SP points at.
                        ir_token = _record_id_token(ir_match)
                        found_ir = False
                        ir_file_found = None
                        for ir_file in dev_dir.glob("IR-*.md"):
                            if ir_file.name.startswith("_"):
                                continue
                            try:
                                ir_content = _chain_text(ir_file)
                            except OSError:
                                continue
                            if (ir_token.search(ir_file.name)
                                    or ir_token.search(_first_heading(ir_content))):
                                found_ir = True
                                ir_file_found = ir_file
                                break
                        if not found_ir:
                            issues.append(
                                f"SP {sp_id} references {ir_id} but IR record not found. "
                                f"Create IR record before sprint planning."
                            )
                        else:
                            # Check if IR references E
                            if ir_file_found:
                                ir_content = _chain_text(ir_file_found)
                                # Look for E-NNN reference in IR. The IR body IS the
                                # declaration of its research inputs (`- **Research
                                # inputs:** E-001, D-005`), so a labelled id is the
                                # link here — unlike the story→record links above,
                                # there is no separate identity channel to prefer.
                                e_match = _E_REF_RE.search(ir_content)
                                if not e_match:
                                    issues.append(
                                        f"IR record {ir_id} does not reference any Experiment (E-NNN). "
                                        f"IR must trace back to an approved experiment."
                                    )
                    else:
                        # NEW: SP exists but does NOT reference any IR (HIGH #4 - orphaned detection)
                        issues.append(
                            f"SP record {sp_id} does not reference any Implementation Readiness (IR) record. "
                            f"Stories cannot be planned without IR. Create IR record first."
                        )

    # Check 4: a cited experiment must still be able to back a criterion.
    # Without this the "approved experiment" requirement was a docstring: an AC
    # citing a withdrawn or rejected record read as valid everywhere.
    for ac in _parse_ac_metadata(content):
        if ac.get("test"):
            continue  # decided by a named test — Check 5 resolves that instead
        ref = ac.get("experiment", "")
        if not re.fullmatch(r"E-\d+", ref or ""):
            continue
        problem = _cited_experiment_problem(ref, root)
        if problem:
            issues.append(problem)

    # Check 5: a test-decided criterion must name a test that exists.
    dangling_test = _dangling_test_problem(content, root)
    if dangling_test:
        issues.append(dangling_test)

    if issues:
        return False, "; ".join(issues[:3])
    return True, ""


def _check_all_stories_quality(root: str, blocked_action: str) -> dict:
    """Validate all stories in docs/development/stories for metadata, orphan links, and chain."""
    from . import guard as _guard
    _dangling_structural_reason = _guard._dangling_structural_reason
    _orphan_story_reason = _guard._orphan_story_reason
    _validate_methodology_chain = _guard._validate_methodology_chain
    _validate_story_metadata = _guard._validate_story_metadata
    pathlib = _guard.pathlib
    rel_to_root = _guard.rel_to_root
    stories_dir = pathlib.Path(root) / "docs" / "development" / "stories"
    if not stories_dir.is_dir():
        return {"decision": "allow"}
    for s_file in sorted(stories_dir.glob("S-*.md")):
        if s_file.name.startswith("_"):
            continue
        try:
            content = s_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        valid, reason = _validate_story_metadata(content)
        if not valid:
            return {
                "decision": "deny",
                "reason": f"{blocked_action}: Story metadata validation failed for {s_file.name}: {reason}",
            }
        orphan = _orphan_story_reason(content)
        if orphan:
            return {
                "decision": "deny",
                "reason": f"{blocked_action}: Story experiment validation failed for {s_file.name}: {orphan}",
            }
        dangling = _dangling_structural_reason(content, root)
        if dangling:
            return {
                "decision": "deny",
                "reason": f"{blocked_action}: Structural reference failed for {s_file.name}: {dangling}",
            }
        rel = rel_to_root(root, str(s_file))
        valid, reason = _validate_methodology_chain(content, rel, root)
        if not valid:
            return {
                "decision": "deny",
                "reason": f"{blocked_action}: Methodology chain validation failed for {s_file.name}: {reason}",
            }
    return {"decision": "allow"}

_QR_SECTION_RE = re.compile(
    r"##\s+Quality\s+Record\s*\(QR\).*?(?=\n##\s|\Z)",
    re.DOTALL | re.IGNORECASE)
_QR_PATH_RE = re.compile(r"QR Record Path\*\*:\s*(docs/quality/QR-\d+\.md)")
_QR_STATUS_RE = re.compile(r"^\s*-\s*\*\*Status:\*\*\s*(.+?)\s*$", re.MULTILINE)

def _qr_table_has_pending(section: str) -> bool:
    """True if any DoD row in a QR table lacks a pass/fail mark."""
    from . import guard as _guard
    re = _guard.re
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or not re.match(r"DoD-\d+", cells[0]):
            continue
        if "✅" not in cells[1] and "❌" not in cells[1]:
            return True
    return False

def _check_qr_table_coherence(root: str, blocked_action: str) -> dict:
    """Deny commit when a done story's embedded QR table still reads pending
    while its QR record is APPROVED (SP-020 structural gap: approval never
    propagated back, and nothing alarmed because no reader checked the
    embedded copy).

    Conservative by design: files without a QR section, without a QR
    pointer, with an unreadable QR file, or whose QR is not APPROVED are
    skipped, never denied — other checks own those cases."""
    from . import guard as _guard
    _QR_PATH_RE = _guard._QR_PATH_RE
    _QR_SECTION_RE = _guard._QR_SECTION_RE
    _QR_STATUS_RE = _guard._QR_STATUS_RE
    _plugin_script_cmd = _guard._plugin_script_cmd
    _qr_table_has_pending = _guard._qr_table_has_pending
    pathlib = _guard.pathlib
    story_status = _guard.story_status
    stories_dir = pathlib.Path(root) / "docs" / "development" / "stories"
    if not stories_dir.is_dir():
        return {"decision": "allow"}
    for s_file in sorted(stories_dir.glob("S-*.md")):
        if s_file.name.startswith("_"):
            continue
        try:
            content = s_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if story_status(content) != "done":
            continue
        m = _QR_SECTION_RE.search(content)
        if not m or not _qr_table_has_pending(m.group(0)):
            continue
        pm = _QR_PATH_RE.search(content)
        if not pm:
            continue
        try:
            qr_text = (pathlib.Path(root) / pm.group(1)).read_text(
                encoding="utf-8", errors="replace")
        except OSError:
            continue
        qm = _QR_STATUS_RE.search(qr_text)
        if not qm or qm.group(1).strip() != "APPROVED":
            continue
        return {
            "decision": "deny",
            "reason": (
                f"{blocked_action}: {s_file.name} is done and its QR record "
                f"is APPROVED, but its embedded QR table still reads "
                f"pending. Run: {_plugin_script_cmd('scripts/sync-story-qr.py', '--apply')}"
            ),
        }
    return {"decision": "allow"}

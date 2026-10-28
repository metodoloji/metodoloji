"""Pure story-record parsing helpers used by the guard and quality checks."""

import os
import re

from .utils import _VERIFY_FIELD_RE


_STORY_BASENAME_RE = re.compile(r"^(?:\d+-\d+-[a-z][a-z0-9-]*\.md|S-\d+(?:-[a-z0-9-]+)?\.md)$", re.IGNORECASE)


def _is_story_file(rel: str) -> bool:
    """True when the path's basename is exactly a story filename."""
    base = rel.replace("\\", "/").rsplit("/", 1)[-1]
    return bool(_STORY_BASENAME_RE.match(base))


def _unchecked_story_write_warning(rel: str) -> str:
    """Warn-only notice when a terminal story write bypasses content checks."""
    return (
        f"{rel}: story write via terminal — its content is not visible to the "
        f"guard before the write, so AC/chain validation did not run here "
        f"(the PostToolUse audit enforces it after the fact)."
    )


def _story_heredoc_body(command: str, target: str) -> str | None:
    """Return the literal heredoc payload a terminal command writes to `target`.

    Best-effort parse of the canonical shell story-creation pattern
    (`cat <<'EOF' > docs/.../S-002.md` or `cat > docs/.../S-002.md <<'EOF'`).
    Returns None when the command does not write a plain heredoc to the story
    target — in that case the payload is unknowable at guard time.

    Note: callers only reach this for commands without `$` (variable targets
    are dropped before story detection), so no shell expansion is needed.
    """
    m = re.search(r"<<\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1", command)
    if not m:
        return None
    marker = m.group(2)
    want = os.path.basename(target.replace("\\", "/"))
    matched = None
    for rm in re.finditer(r">\s*([^\s;|&'\"]+)", command):
        if os.path.basename(rm.group(1)) == want:
            matched = rm
            break
    if matched is None:
        return None
    # Everything after the heredoc marker's line, up to the bare terminator
    # line, is the literal payload. The tail of the opening line (which may
    # carry the `> file` redirect) is not payload.
    body: list[str] = []
    for line in command[m.end():].splitlines()[1:]:
        if line.strip() == marker:
            break
        body.append(line)
    return "\n".join(body) if body else None


def _frontmatter_block(content: str) -> str:
    """Return the YAML frontmatter body ('' when absent).

    The closing fence must sit alone on its line: a body rule (`---`) never
    ends the block early.
    """
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[1:i])
    return ""


def _parse_experiment_refs(content: str) -> list[dict]:
    """Extract experiment_refs from YAML frontmatter of a story file.

    Returns a list of dicts with keys: id, scope, status.
    Returns empty list if no experiment_refs found or parsing fails.
    """
    frontmatter = _frontmatter_block(content)
    if not frontmatter:
        return []

    # Find experiment_refs block — indentation-aware line parse. Only lines
    # indented DEEPER than the experiment_refs key belong to the block; a
    # top-level key (status: draft) ends it instead of merging into the ref.
    refs: list[dict] = []
    refs_indent: int | None = None
    current: dict = {}
    current_indent = 0
    for line in frontmatter.splitlines():
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        if refs_indent is None:
            if stripped.startswith("experiment_refs"):
                refs_indent = indent
            continue
        if indent <= refs_indent:
            break  # back at top level — block is over
        if stripped.startswith("- "):
            if current:
                refs.append(current)
            current = {}
            current_indent = indent
            inner = stripped[2:].strip()
            # Handle inline: - id: E-001
            kv = inner.split(":", 1)
            if len(kv) == 2:
                current[kv[0].strip()] = kv[1].strip()
        elif ":" in stripped and current and indent > current_indent:
            kv = stripped.split(":", 1)
            current[kv[0].strip()] = kv[1].strip()
    if current:
        refs.append(current)
    return refs


_TABLE_REFS_ROW_RE = re.compile(
    r"^\s*\|\s*Experiment\s+Refs\s*\|([^|\n]*)\|", re.IGNORECASE | re.MULTILINE)


def _table_experiment_linked(content: str) -> bool:
    """Bridge table linkage: a field-table ``Experiment Refs`` row naming
    E-NNN, or an AC-table Experiment column carrying E-NNN (same parser
    the metadata check uses, so linkage and fields can never disagree
    about what the table says)."""
    refs_match = _TABLE_REFS_ROW_RE.search(content)
    if refs_match and re.search(r"E-\d+", refs_match.group(1)):
        return True
    for ac in _parse_ac_metadata(content):
        if re.fullmatch(r"E-\d+|STRUCT-\d{3}", ac.get("experiment", "")):
            return True
    return False


_AC_ID_RE = re.compile(r"\[AC-(\d+)\]")


_TASK_AC_RE = re.compile(r"AC:\s*(AC-\d+)")


_HYPOTHESIS_RE = re.compile(r"\[HYPOTHESIS\]")


_EXPERIMENT_FIELD_RE = re.compile(r"Experiment:\s*(E-\d+|STRUCT-\d{3}|—|-)")


_TEST_FIELD_RE = re.compile(r"^\s*-?\s*Test:\s*([^\n]+)", re.M)


_MEASURED_FIELD_RE = re.compile(r"Measured:\s*(true|false)", re.IGNORECASE)


_TYPE_FIELD_RE = re.compile(r"Type:\s*(agent-verifiable|user-evaluable|hybrid)", re.IGNORECASE)


_TABLE_AC_ID_RE = re.compile(r"AC-(\d+)")


_TABLE_AC_KNOWN_TYPES = ("agent-verifiable", "user-evaluable", "hybrid")


_TABLE_AC_COLUMNS = ("ac", "status", "experiment", "test", "type", "measured", "verify")


def _is_ac_table_header(cells: list[str]) -> bool:
    """A header names the bridge columns (in any order) and carries no AC id."""
    names = [c.strip().lower() for c in cells]
    if _TABLE_AC_ID_RE.search(" ".join(names)):
        return False
    return sum(1 for c in names if c in _TABLE_AC_COLUMNS) >= 3


def _table_column_map(header_cells: list[str]) -> dict[str, int]:
    """Map bridge table columns (AC | Status | Experiment | Type | Measured | Verify)
    to indices by header name; -1 when a column is absent."""
    mapping: dict[str, int] = {}
    lowered = [c.strip().lower() for c in header_cells]
    for name in _TABLE_AC_COLUMNS:
        try:
            mapping[name] = lowered.index(name)
        except ValueError:
            mapping[name] = -1
    if mapping["ac"] == -1 and lowered:
        mapping["ac"] = 0
    return mapping


def _parse_table_ac_row(cells: list[str], col: dict[str, int], line: str) -> dict | None:
    """Parse one bridge table AC row (| AC-001 | … |); None when not a data row.

    Column positions come from the section's header row, so reordered
    tables resolve identically. Values mirror the bracket dialect's
    validity contract: unknown Type / non-boolean Measured normalize to
    empty (the validator flags them), dash Verify is accepted exactly as
    the bracket ``Verify:`` capture accepts it.
    """
    if len(cells) < 6:
        return None
    id_match = _TABLE_AC_ID_RE.search(cells[col["ac"]] if 0 <= col["ac"] < len(cells) else "")
    if not id_match:
        return None
    ac_id = f"AC-{id_match.group(1)}"

    def cell(name: str) -> str:
        idx = col.get(name, -1)
        return cells[idx].strip() if 0 <= idx < len(cells) else ""

    experiment_raw = cell("experiment")
    experiment_m = re.search(r"E-\d+|STRUCT-\d{3}", experiment_raw)
    experiment = experiment_m.group(0) if experiment_m else (
        experiment_raw if experiment_raw in ("—", "-") else "")
    type_raw = cell("type").lower()
    type_value = type_raw if type_raw in _TABLE_AC_KNOWN_TYPES else ""
    measured_raw = cell("measured").lower()
    measured = measured_raw if measured_raw in ("true", "false") else ""
    verify = " ".join(cell("verify").split())
    if len(cells) > 6 and col.get("verify", -1) >= 0:
        extra = " ".join(c.strip() for c in cells[col["verify"] + 1:] if c.strip())
        if extra:
            verify = f"{verify} {extra}".strip() if verify else extra
    return {
        "id": ac_id,
        "experiment": experiment,
        "test": cell("test"),
        "type": type_value,
        "measured": measured,
        "verify": verify,
        "is_hypothesis": bool(_HYPOTHESIS_RE.search(line)),
    }


def _parse_ac_metadata(content: str) -> list[dict]:
    """Parse Acceptance Criteria section and extract AC metadata.

    Two dialects, one contract: bracket blocks (``[AC-001]`` + labeled
    fields — the native-draft shape) and bridge table rows
    (``| AC-001 | … |`` — the methodology-record shape per bridge §2.5b).
    Table columns resolve through the section's own header row. An id
    parsed from brackets wins on collision (first occurrence stands).

    Returns list of dicts with keys: id, experiment, type, measured, verify, is_hypothesis.
    """
    acs = []
    # Find Acceptance Criteria section. Methodology records title it
    # "## Acceptance Criteria Details" while native drafts use the bare
    # "## Acceptance Criteria" — both are the same section (bridge §2.5b).
    ac_match = re.search(r"##\s+Acceptance\s+Criteria[^\n]*\n(.*?)(?=\n##\s|\Z)",
                         content, re.DOTALL | re.IGNORECASE)
    if not ac_match:
        return acs
    ac_section = ac_match.group(1)

    # Table dialect first so bracket blocks keep first-occurrence priority
    # on id collision (dedup below skips table ids already seen).
    table_seen: dict[str, dict] = {}
    col: dict[str, int] = {}
    header_found = False
    for line in ac_section.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if not header_found and _is_ac_table_header(cells):
            col = _table_column_map(cells)
            header_found = True
            continue
        if not header_found:
            col = _table_column_map(
                ["ac", "status", "experiment", "test", "type", "measured", "verify"])
            header_found = True
        parsed = _parse_table_ac_row(cells, col, line)
        if parsed and parsed["id"] not in table_seen:
            table_seen[parsed["id"]] = parsed

    # Split by AC identifiers
    ac_blocks = re.split(r"(?=\[AC-\d+\])", ac_section)
    for block in ac_blocks:
        id_match = _AC_ID_RE.search(block)
        if not id_match:
            continue
        ac_id = f"AC-{id_match.group(1)}"
        experiment_m = _EXPERIMENT_FIELD_RE.search(block)
        test_m = _TEST_FIELD_RE.search(block)
        type_m = _TYPE_FIELD_RE.search(block)
        measured_m = _MEASURED_FIELD_RE.search(block)
        verify_m = _VERIFY_FIELD_RE.search(block)
        is_hypothesis = bool(_HYPOTHESIS_RE.search(block))

        acs.append({
            "id": ac_id,
            "experiment": experiment_m.group(1) if experiment_m else "",
            "test": test_m.group(1).strip() if test_m else "",
            "type": type_m.group(1) if type_m else "",
            "measured": measured_m.group(1).lower() if measured_m else "",
            "verify": verify_m.group(1).strip() if verify_m else "",
            "is_hypothesis": is_hypothesis,
        })
    seen = {ac["id"] for ac in acs}
    for ac_id, parsed in table_seen.items():
        if ac_id not in seen:
            acs.append(parsed)
            seen.add(ac_id)
    return acs


def _parse_task_ac_refs(content: str) -> list[dict]:
    """Parse Technical Tasks section and extract AC references.

    Returns list of dicts with keys: task_text, ac_refs (list of AC IDs).
    """
    tasks = []
    # Find Technical Tasks section
    tt_match = re.search(r"##\s+Technical\s+Tasks\s*\n(.*?)(?=\n##\s|\Z)", content, re.DOTALL | re.IGNORECASE)
    if not tt_match:
        return tasks
    tt_section = tt_match.group(1)

    for line in tt_section.splitlines():
        # Only capture top-level tasks (not indented subtasks)
        if line.startswith("- [ ]") or line.startswith("- [x]"):
            ac_refs = _TASK_AC_RE.findall(line)
            tasks.append({
                "task_text": line.strip(),
                "ac_refs": ac_refs,
            })
    return tasks

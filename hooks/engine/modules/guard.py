"""Guard logic for PreToolUse hook."""

import contextlib
import io
import os
import pathlib
import re
import subprocess
import sys
import time
from functools import lru_cache

from .config import GATE_DIR, SCOPE_GIT_TIMEOUT_SECONDS, _BMD_DIR, _KEY_ACCESS_IN_CONTENT, story_status
from .utils import (is_code_target, is_free, norm_path, normalize_hook_input,
                    rel_to_root, repo_root, extract_story_key_from_content,
                    dod_issues, _VERIFY_FIELD_RE)
from .bash_targets import extract_bash_targets, extract_bash_targets_ex
from . import plan as plan_mod
from . import state as state_mod

# Import gate script — deferred: sys.exit at module level kills the entire process
# (including audit which doesn't need the gate). Instead, gate is loaded lazily
# and guard/quality/deploy fail-closed at call time if it's missing.
gate = None

def _load_gate():
    global gate
    if gate is not None:
        return True
    if GATE_DIR is None:
        sys.stderr.write("metodoloji-hooks: gate script not found — fail-closed\n")
        return False
    if str(GATE_DIR) not in sys.path:
        sys.path.insert(0, str(GATE_DIR))
    try:
        import run_experiment as _gate  # noqa: E402
        gate = _gate
        return True
    except Exception as exc:
        sys.stderr.write(f"metodoloji-hooks: gate import failed — {exc}\n")
        return False


_LISTING_SEG_RE = re.compile(r"^\s*(ls|dir)(\s|$)", re.IGNORECASE)
_ECHO_SEG_RE = re.compile(r"^\s*echo(\s|$)", re.IGNORECASE)


def _secret_ref(s: str) -> bool:
    """True if s reaches for key material (fail-closed), False for a bare
    directory listing (fail-open).

    The key filename (`gate-key`, incl. the `gate-keys/` trust ring) and the
    env identifier are always denied — `ls ~/.bmad/gate-key` included. A bare
    `.bmad` mention without the key filename denies too, EXCEPT pure `ls`/`dir`
    segments and plain `echo` separators: listing the directory reveals only
    presence, which the orient digest already publishes (`gate_key: present`),
    while `ls .bmad/` in an exploration chain must not kill the whole command
    (real-session false positive). Reads, copies, greps and chained consumers
    of the dir (`cat ~/.bmad/*`, `cp -r ~/.bmad …`, `cd ~/.bmad`) stay denied.
    """
    low = s.lower()
    if "gate-key" in low or "bmad_gate_key" in low:
        return True
    if not _BMD_DIR.search(s):
        return False
    for seg in re.split(r"[;&|\n]+", s):
        if not _BMD_DIR.search(seg):
            continue
        seg = seg.strip()
        if _LISTING_SEG_RE.match(seg):
            continue
        if _ECHO_SEG_RE.match(seg) and "$(" not in seg and "`" not in seg:
            continue
        return True
    return False


def _plan_record_verified(rec_path: str) -> bool:
    """HMAC-verified approval check for the plan layer (E-065).

    A plan unlocks code ONLY under a genuinely VERIFIED record — the plan is
    a working-set optimization, never an approval substitute. Uses the cached
    verify (mtime+size keyed, 5-min TTL) so repeated writes in one session
    don't re-run the HMAC over unchanged records.
    """
    rc, _scope = _cached_verify(rec_path)
    return rc == 0


@lru_cache(maxsize=256)
def _cached_scope(path: str, mtime_ns: int, size: int) -> str:
    if not _load_gate():
        return ""
    try:
        return gate.record_scope(path)
    except Exception:
        return ""


def record_scope_cached(path: str) -> str:
    """gate.record_scope with (mtime_ns, size)-based LRU cache."""
    try:
        st = pathlib.Path(path).stat()
        return _cached_scope(path, st.st_mtime_ns, st.st_size)
    except OSError:
        if not _load_gate():
            return ""
        try:
            return gate.record_scope(path)
        except Exception:
            return ""


def verify_record(rec: str) -> tuple[int, str]:
    """Run gate verify on a record; return (rc, scope)."""
    if not _load_gate():
        return 1, ""
    try:
        rec_path = pathlib.Path(rec)
        if not rec_path.exists():
            return 1, ""
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = gate.verify(rec)
        return rc, record_scope_cached(rec)
    except (AttributeError, TypeError, ValueError):
        return 1, ""
    except Exception as exc:
        try:
            sys.stderr.write(f"metodoloji: verify_record({rec}) unexpected error: {exc}\n")
        except Exception:
            pass
        return 1, ""


# Matches native story files (1-2-user-auth.md) AND methodology story records
# (S-001.md, S-056-gate-id-guard.md). Basename-anchored: notes-S-001.md or
# a/b-S-001.md/notes.md must NOT count as story files (substring match would
# drag ordinary files into the story metadata chain).
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
        if re.fullmatch(r"E-\d+", ac.get("experiment", "")):
            return True
    return False


def _orphan_story_reason(content: str) -> str:
    """Reason text when a story has no link to any experiment (orphan);
    empty string when the story is experiment-linked.

    A story is an orphan when it has neither frontmatter ``experiment_refs``
    nor any AC-level ``Experiment: E-NNN`` field nor bridge table linkage
    (field-table refs row or AC-table column) — with the documented
    ``[HYPOTHESIS]`` opt-out honoured, so an AC that is explicitly a hypothesis
    is not counted as missing its Experiment (E-031; the write-time validator
    and this probe share one parser). Strictness for this case is decided by
    the gate branch in the story-write path (soft → warn, hard → deny); this
    helper only detects it.
    """
    if _parse_experiment_refs(content):
        return ""
    if _table_experiment_linked(content):
        return ""
    acs = _parse_ac_metadata(content)
    if acs:
        # One parser, both gates. `_validate_story_metadata` (write time)
        # honours the documented [HYPOTHESIS] opt-out — "The [HYPOTHESIS] tag
        # is an explicit opt-out from the Experiment field requirement" — but
        # this probe read the raw text and filtered `Experiment: —` out, so a
        # story whose ACs are all hypotheses PASSED the write gate and was
        # denied at COMMIT as an orphan (observed session 2026-10-1x: S-002
        # stuck on "the methodology's approved hypothesis exception"). A story
        # is an orphan only when some AC is neither experiment-linked nor a
        # hypothesis.
        if all(ac["experiment"] not in ("", "—", "-") or ac["is_hypothesis"]
               for ac in acs):
            return ""
    else:
        # No AC metadata parsed (legacy/table-less shape): keep the text probe.
        ac_exp_pattern = re.compile(r"\[AC-\d+\].*?Experiment:\s*(E-\d+|—|-)", re.DOTALL | re.IGNORECASE)
        actual_ac_exp_refs = [e for e in ac_exp_pattern.findall(content) if e not in ("—", "-")]
        if actual_ac_exp_refs:
            return ""
    return (
        "Story has no experiment reference: every acceptance criterion must reference "
        "an Experiment (E-NNN). Add 'Experiment: E-XXX' field to each AC to link this story "
        "to the experiment that validates it."
    )


def _superseded_by(root: str, exp_id: str) -> str:
    """The record that re-measured `exp_id` (`Re-Measured-By`), or ''.

    Read through modules/freshness.py — the plane that already owns this
    question for the board — so a story and a baton can never disagree about
    whether a decision was superseded.
    """
    try:
        from . import freshness as fr
        return fr.superseded_by(root, exp_id) or ""
    except Exception:
        return ""


def _cascade_warning(exp_id: str, superseded_by: str) -> str:
    """One advisory line: this story cites a decision a newer record replaced."""
    return (f"CASCADE INVALIDATION: {exp_id} was re-measured by "
            f"{superseded_by} — the decision is no longer the operative one; "
            f"re-point the AC at {superseded_by} or retire {exp_id} "
            f"explicitly.")


def _validate_story_experiment_refs(content: str, root: str = "") -> tuple[bool, str, list]:
    """Validate that all experiment_refs in a story file point to approved records.

    Also validates that a story file has at least 1 AC with an experiment reference.

    Cascade invalidation (E-053): a story AC citing an APPROVED record that a
    newer record has re-measured (`- **Re-Measured-By:** E-NNN`) is citing a
    decision that is no longer the operative one. That is not a validity
    failure — the record was genuine — so it comes back as a WARNING beside the
    verdict, derived from modules/freshness.py (the same single source every
    other surface reads), never as a second opinion computed here.

    Returns (is_valid, reason, cascade_warnings).
    """
    if not root:
        root = repo_root({})
    refs = _parse_experiment_refs(content)
    
    # NEW: Check for AC experiment references using regex to find AC-NNN with Experiment: E-NNN
    ac_exp_pattern = re.compile(r"\[AC-\d+\].*?Experiment:\s*(E-\d+|—|-)", re.DOTALL | re.IGNORECASE)
    ac_experiment_refs = ac_exp_pattern.findall(content)
    
    # Filter out dashes (—, -) to get actual experiment IDs
    actual_ac_exp_refs = [e for e in ac_experiment_refs if e not in ("—", "-")]
    
    # NEW: A story must have at least 1 AC with an experiment reference (not mandatory, but recommended)
    # For now, warn but don't block. Can be made mandatory later.
    if not actual_ac_exp_refs and not refs:
        # No experiment references found anywhere in story
        # This is a warning case (story can still proceed but should be linked to an experiment)
        pass  # Allow it to proceed (soft enforcement)
    
    if not refs:
        # No experiment_refs in frontmatter: the story may still be linked
        # via AC-level 'Experiment:' fields; when it is not, it is an ORPHAN
        # story. Orphan strictness is decided by the gate branch in the
        # story-write path (_orphan_story_reason) — soft gate warns, hard
        # gate denies. Refs that DO exist are always strictly validated
        # below, regardless of gate mode (an unapproved/missing experiment
        # reference must never pass).
        return True, "", []

    recs_dir = pathlib.Path(root) / "docs" / "experiments"
    if not recs_dir.is_dir():
        return False, "experiment_refs found but docs/experiments/ directory missing", []

    cascade_warnings: list[str] = []

    for ref in refs:
        exp_id = ref.get("id", "")
        status = ref.get("status", "")
        if not exp_id:
            continue
        if status in ("PENDING", "REJECTED"):
            return False, (
                f"Experiment {exp_id} has status '{status}' — "
                f"ACs linked to this experiment cannot be implemented. "
                f"Mark linked ACs as [HYPOTHESIS] or get experiment approval first."
            ), []
        # Check if the experiment record exists and is verified
        exp_file = recs_dir / f"{exp_id}.md"
        if not exp_file.exists():
            return False, (
                f"Experiment record {exp_id}.md not found in docs/experiments/. "
                f"Create the experiment record before implementing linked ACs."
            ), []
        rc, _ = verify_record(str(exp_file))
        if rc != 0:
            return False, (
                f"Experiment record {exp_id} is not verified (rc={rc}). "
                f"Run: {_plugin_script_cmd(
                    'skills/bmad-research-experiment/scripts/run_experiment.py',
                    '--verify', '--record', str(exp_file))}"
            ), []
        superseded = _superseded_by(root, exp_id)
        if superseded:
            cascade_warnings.append(_cascade_warning(exp_id, superseded))

    # Also validate AC experiment references
    for ac_exp_ref in actual_ac_exp_refs:
        exp_id = ac_exp_ref
        exp_file = recs_dir / f"{exp_id}.md"
        if not exp_file.exists():
            return False, (
                f"AC references Experiment {exp_id} but record not found in docs/experiments/. "
                f"Create the experiment record before implementing this AC."
            ), []
        rc, _ = verify_record(str(exp_file))
        if rc != 0:
            return False, (
                f"AC references Experiment {exp_id} which is not verified (rc={rc}). "
                f"Get experiment approval first."
            ), []
        superseded = _superseded_by(root, exp_id)
        if superseded:
            cascade_warnings.append(_cascade_warning(exp_id, superseded))

    # One line per superseded record, whatever cited it how many times.
    return True, "", list(dict.fromkeys(cascade_warnings))


# --- AC Metadata Validation ---

_AC_ID_RE = re.compile(r"\[AC-(\d+)\]")
_TASK_AC_RE = re.compile(r"AC:\s*(AC-\d+)")
_HYPOTHESIS_RE = re.compile(r"\[HYPOTHESIS\]")
_EXPERIMENT_FIELD_RE = re.compile(r"Experiment:\s*(E-\d+|—|-)")
_MEASURED_FIELD_RE = re.compile(r"Measured:\s*(true|false)", re.IGNORECASE)
_TYPE_FIELD_RE = re.compile(r"Type:\s*(agent-verifiable|user-evaluable|hybrid)", re.IGNORECASE)
# _VERIFY_FIELD_RE and the DoD rules are shared with the audit's QR DoD check
# (single source of truth in .utils).


_TABLE_AC_ID_RE = re.compile(r"AC-(\d+)")
_TABLE_AC_KNOWN_TYPES = ("agent-verifiable", "user-evaluable", "hybrid")
_TABLE_AC_COLUMNS = ("ac", "status", "experiment", "type", "measured", "verify")


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
    experiment_m = re.search(r"E-\d+", experiment_raw)
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
                ["ac", "status", "experiment", "type", "measured", "verify"])
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
        type_m = _TYPE_FIELD_RE.search(block)
        measured_m = _MEASURED_FIELD_RE.search(block)
        verify_m = _VERIFY_FIELD_RE.search(block)
        is_hypothesis = bool(_HYPOTHESIS_RE.search(block))

        acs.append({
            "id": ac_id,
            "experiment": experiment_m.group(1) if experiment_m else "",
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


# Directories that can hold methodology records, relative to the project root.
# `docs/` covers the canonical layout (docs/experiments, docs/development,
# docs/development/stories, docs/quality, plus Seçenek A native outputs
# docs/planning + docs/development/native) plus the legacy per-type dirs;
# `bmad-output/` is the pre-migration artifacts tree, kept as legacy fallback.
# A project on either layout gets the check.
_RECORD_SCAN_ROOTS = ("docs", "bmad-output", "_bmad-output", "design-artifacts")

_RECORD_ID_RE = re.compile(r"[A-Z]+-\d+")


def _check_duplicate_record_ids(root: str, rel_path: str,
                               record_type: str | None = None) -> tuple[bool, str]:
    """Is this record ID unique across the project? (MEDIUM #2 / ISSUE #61)

    The target file itself is excluded by resolved path, so:
    * editing an existing record → unique (no false block);
    * a copy under an archive subdir, a second artifacts tree, or the wrong
      directory → a real duplicate.

    Fast path (Faz 2e): a duplicate is a creation-time defect. When the target
    already exists on disk the write is an edit, and the project-wide scan that
    globs 4 roots is skipped entirely — a record deleted then re-created with a
    colliding ID is a rare, pre-broken state the set of projects tolerates.
    Only a genuinely new file pays the scan.

    `record_type` is accepted for backward compatibility and ignored: the record
    ID prefix already carries the type (IR-001 vs QR-001 are different IDs).

    Returns (is_unique, reason). An unreadable tree fails open.
    """
    try:
        root = str(root)  # callers pass str; Path is accepted for convenience
        name = pathlib.Path(rel_path).name
        record_id = name[:-3] if name.endswith(".md") else name
        if not _RECORD_ID_RE.fullmatch(record_id):
            return True, ""  # not a record file — nothing to check

        try:
            if (pathlib.Path(root) / rel_path).exists():
                return True, ""  # edit path: duplicates can't arise
        except OSError:
            pass

        try:
            target_abs = (pathlib.Path(root) / rel_path).resolve()
        except OSError:
            target_abs = None  # unresolvable: fall back to textual comparison

        from .config import MAX_DUPLICATE_CHECK_RECORDS
        others: list[str] = []
        budget = 0
        for rel_root in _RECORD_SCAN_ROOTS:
            base = pathlib.Path(root) / rel_root
            if not base.is_dir():
                continue
            for found in base.rglob(f"{record_id}.md"):
                budget += 1
                if budget > MAX_DUPLICATE_CHECK_RECORDS:
                    break
                try:
                    same = target_abs is not None and found.resolve() == target_abs
                except OSError:
                    same = False
                if not same:
                    others.append(rel_to_root(root, str(found)))

        if others:
            shown = ", ".join(sorted(others)[:3])
            if len(others) > 3:
                shown += f", … (+{len(others) - 3} more)"
            return False, (
                f"Duplicate record ID '{record_id}' detected: also at {shown}. "
                f"A record ID must identify exactly one file."
            )

        return True, ""

    except Exception as e:
        # Can't check, allow write (fail-open)
        return True, f"(duplicate check skipped: {str(e)[:100]})"


def _validate_story_metadata(content: str) -> tuple[bool, str]:
    """Validate AC metadata, Task↔AC mapping, DoD structure, and STATUS field state machine.

    Returns (is_valid, reason).

    S-008 fix (D2 root cause):
    - (a) If story has no experiment_refs in frontmatter (empty refs list
          or no frontmatter), skip the AC 'missing Experiment field' check
          entirely. Per bench invariant: 'not a story with metadata' implies
          the AC's Experiment field is optional (the AC is testing the
          ref validation itself, not a real experiment).
    - (b) If AC is marked [HYPOTHESIS], skip both the 'missing Experiment
          field' and 'Experiment=— but no [HYPOTHESIS] tag' checks. The
          [HYPOTHESIS] tag is an explicit opt-out from the Experiment
          field requirement.
          
    NEW: Status field state machine validation (HIGH #3 / ISSUE #11)
    - Valid states: backlog, ready-for-dev, in-progress, review, done, blocked
    - Valid transitions: backlog → ready-for-dev → in-progress → review → done
    - blocked can transition from/to any state (exception for emergencies)
    """
    issues = []

    # NEW: Validate status field state machine (HIGH #3 / ISSUE #11)
    # Single shared reader (config.story_status): field form or bridge table
    # row. Indented YAML keys (e.g. 'status: APPROVED' under experiment_refs
    # frontmatter) must never match — the vocabulary check would flag
    # 'approved' as an invalid story status.
    current_status = story_status(content)
    if current_status:
        # Valid states in the state machine
        valid_states = {"backlog", "ready-for-dev", "in-progress", "review", "done", "blocked"}
        if current_status not in valid_states:
            issues.append(f"Invalid status '{current_status}'. Valid: backlog, ready-for-dev, in-progress, review, done, blocked")
        # Note: Full transition validation (e.g., backlog→done not allowed) would require
        # knowing previous status. For now, we validate only that current status is legal.

    refs = _parse_experiment_refs(content)
    has_refs = bool(refs)

    # 1. Validate AC metadata
    acs = _parse_ac_metadata(content)
    for ac in acs:
        if has_refs and not ac["is_hypothesis"]:
            if not ac["experiment"]:
                issues.append(f"{ac['id']}: missing Experiment field")
            elif ac["experiment"] in ("—", "-"):
                issues.append(f"{ac['id']}: Experiment=— but no [HYPOTHESIS] tag")
        # When has_refs is False OR ac is HYPOTHESIS, Experiment field is optional.
        if not ac["type"]:
            issues.append(f"{ac['id']}: missing Type field")
        if not ac["measured"]:
            issues.append(f"{ac['id']}: missing Measured field")
        if not ac["verify"]:
            issues.append(f"{ac['id']}: missing Verify field")

    # 2. Validate Task↔AC mapping
    tasks = _parse_task_ac_refs(content)
    ac_ids = {ac["id"] for ac in acs}
    for task in tasks:
        if not task["ac_refs"]:
            issues.append(f"Task without AC reference: {task['task_text'][:60]}...")
        else:
            for ref in task["ac_refs"]:
                if ref not in ac_ids:
                    issues.append(f"Task references non-existent {ref}: {task['task_text'][:60]}...")

    # 3. Validate DoD structure
    # Shared with the audit's QR DoD check (.utils.dod_issues) so guard and
    # audit enforce the identical DoD rules (identifier + Verify per item).
    dod_match = re.search(r"##\s+Definition\s+of\s+Done\s*\n(.*?)(?=\n##\s|\Z)", content, re.DOTALL | re.IGNORECASE)
    if dod_match:
        issues.extend(dod_issues(dod_match.group(1)))

    if issues:
        return False, "; ".join(issues[:5])  # Limit to 5 issues
    return True, ""


# --- Methodology Chain Validation ---

@lru_cache(maxsize=256)
def _cached_text(path: str, mtime_ns: int, size: int) -> str:
    """Read a chain record, cached by (mtime_ns, size)."""
    return pathlib.Path(path).read_text(encoding="utf-8", errors="replace")


def _chain_text(path: "pathlib.Path") -> str:
    try:
        st = path.stat()
    except OSError:
        return path.read_text(encoding="utf-8", errors="replace")
    return _cached_text(str(path), st.st_mtime_ns, st.st_size)


_RECORD_TITLE_RE = re.compile(r"^#\s+Methodology\s+Record\s*:", re.IGNORECASE | re.MULTILINE)
_NATIVE_STORY_ROW_RE = re.compile(r"^\s*\|\s*Native\s+Story\s*\|", re.IGNORECASE | re.MULTILINE)


def _is_methodology_record(content: str) -> bool:
    """True when the file under scan IS a generated methodology record
    (generator title or Native Story field row) rather than a draft
    awaiting one."""
    return bool(_RECORD_TITLE_RE.search(content) or _NATIVE_STORY_ROW_RE.search(content))


def _plugin_script_cmd(script: str, *args: str) -> str:
    """A runnable command for one of the PLUGIN's own scripts.

    The gate runs in a TARGET project, which does not ship the plugin's
    ``scripts/`` tree — so a remedy that names the script relative to the cwd
    cannot be run where it is printed (E-029, the E-028 Known Gap: the observed
    session's project had no ``scripts/`` dir). Anchor the command at the
    engine's OWN plugin root (``config._METHODOLOGY_ROOT``, the same root
    ``_check_scope_coverage`` derives), so the remedy runs as written.
    """
    from .utils import plugin_script_cmd
    return plugin_script_cmd(script, *args)


def _qr_remedy(root: str, missing_keys: "list[str]") -> str:
    """One `create-qr-record.py --story <real file>` command per missing story.

    Names the story file the operator actually has (resolved with the gate's
    own key precedence); a key that resolves to nothing falls back to the
    conventional `docs/development/stories/<key>.md` path so the command is
    still shaped as a runnable example (E-029).
    """
    paths = _story_paths_for_keys(root, missing_keys)
    cmds = [
        _plugin_script_cmd("scripts/create-qr-record.py", "--story",
                           paths.get(key, f"docs/development/stories/{key}.md"))
        for key in missing_keys
    ]
    return "; ".join(cmds)


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

    if issues:
        return False, "; ".join(issues[:3])
    return True, ""


# Verified-scope cache: find_approved runs gate.verify (HMAC) over every
# record per call. Cache per (mtime_ns, size) so the SAME call does not
# re-verify one record for every write target it tests. Bounded (128 entries);
# rc=3 (key missing) is never cached — it would stick after --init-secret.
#
# SCOPE, honestly stated (E-053 / F-07): the hook engine is COLD-STARTED per
# call (see mirror.py — "the hook engine is cold-started per call, so an
# in-process throttle never fires"), so this dict never survives past the
# decision that filled it: it collapses the N record verifications inside ONE
# guard call, nothing more. It is not a cross-call cache, and it does not need
# external invalidation — the key IS the invalidation (a deleted, re-created
# or edited record has a different (mtime_ns, size)). The former
# `invalidate_verify_cache()` version counter and the "5-minute TTL" claim
# described a cross-call lifetime this process cannot have; both are gone
# rather than left as documentation of a mechanism that never runs.
_VERIFY_CACHE: dict[str, tuple[tuple[int, int], int, str, float]] = {}


def _cached_verify(rec: str) -> tuple[int, str]:
    """verify_record, memoized by (mtime_ns, size) for THIS call only.

    The key is the invalidation: an edited, re-created or deleted record
    changes it, so a hit can only ever describe the file as it is now. See the
    cache block above for why there is no TTL and no external invalidator.
    """
    import time

    try:
        st = pathlib.Path(rec).stat()
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        return verify_record(rec)

    hit = _VERIFY_CACHE.get(rec)
    if hit is not None and hit[0] == key:
        return hit[1], hit[2]

    rc, scope = verify_record(rec)
    if rc != 3:
        if len(_VERIFY_CACHE) >= 128:
            _VERIFY_CACHE.pop(next(iter(_VERIFY_CACHE)))
        _VERIFY_CACHE[rec] = (key, rc, scope, time.time())
    return rc, scope


def _record_freshness(root: str, rec_path: str) -> dict | None:
    """Derived freshness of an APPROVED record (E-053), or None when unavailable.

    A VERIFIED token proves the record was not tampered with; it says nothing
    about whether the decision still applies to the code. This derives that
    second answer from the record's own Witness commit + Code Scope (and from a
    Re-Measured-By declaration, which is a supersede). Returns None when the
    plane is switched off or the gate is unreachable — fail-open, never block.
    """
    try:
        from . import freshness as fr
        if not fr.enabled() or not _load_gate():
            return None
        path = pathlib.Path(rec_path)
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            commit = gate.witness_commit(text)
        except Exception:
            commit = ""
        verdict, detail = fr.classify(root, commit=commit,
                                      scope=record_scope_cached(rec_path),
                                      run_key=path.stem)
        age_s = None
        try:
            age_s = max(0.0, time.time() - path.stat().st_mtime)
        except OSError:
            pass
        return {"verdict": verdict, "reason": detail.get("reason", ""),
                "changed": detail.get("changed", []), "age_s": age_s,
                "commit": commit, "record": path.name}
    except Exception:
        return None


# Cap on the records a commit/deploy freshness row reads: the row must stay
# cheap on the hot path (init-time commit), and a project past this many
# experiment records has bigger inputs than a warning line.
_FRESHNESS_ROW_MAX_RECORDS = 500


def _freshness_chain_row(root: str, blocked_action: str) -> dict:
    """Derived freshness of every APPROVED record: report, or block on request.

    The write path already refuses to open code through a claim the code moved
    past (find_approved). This asks the SAME question at commit/deploy time,
    where the answer can have changed since the write: an approval goes stale
    while its work sits uncommitted in the tree.

    Report-only under the shipped defaults; it blocks only when the operator
    asked for it — `[hooks] freshness = deny` for STALE, `[freshness]
    unknown_policy = deny` for UNKNOWN (both decided by `freshness.blocks`, the
    single place that answers "does this verdict block?"). Fail-open on every
    error: a diagnostic must not wedge a commit.
    """
    try:
        from . import freshness as fr
        if not fr.enabled():
            return {"decision": "allow"}
        # `_load_gate()` returns a BOOL and binds the module to `gate` — the
        # record parser comes from that module (same single source the
        # verification path uses), never from a second parser here.
        if not _load_gate() or globals().get("gate") is None:
            return {"decision": "allow"}
        record_fields = gate.record_fields
    except Exception:
        return {"decision": "allow"}

    stale: list[str] = []
    unknown: list[str] = []
    try:
        base = pathlib.Path(root) / "docs" / "experiments"
        with fr.batch():
            for rec in sorted(base.glob("E-*.md"))[:_FRESHNESS_ROW_MAX_RECORDS]:
                if rec.name == "_template.md":
                    continue
                try:
                    text = rec.read_text(encoding="utf-8", errors="replace")
                    decision = str(record_fields(text).get("Decision", ""))
                except Exception:
                    continue
                if not decision.startswith("APPROVED"):
                    continue
                info = _record_freshness(root, str(rec))
                if not info:
                    continue
                if info["verdict"] == fr.STALE:
                    stale.append(f"{rec.stem} ({info['reason']})")
                elif info["verdict"] == fr.UNKNOWN:
                    unknown.append(f"{rec.stem} ({info['reason']})")
    except Exception:
        return {"decision": "allow"}

    if not stale and not unknown:
        return {"decision": "allow"}
    try:
        from . import freshness as fr
        stale_blocks = bool(stale) and fr.blocks(fr.STALE)
        unknown_blocks = bool(unknown) and fr.blocks(fr.UNKNOWN)
    except Exception:
        stale_blocks = unknown_blocks = False
    if stale_blocks or unknown_blocks:
        parts = []
        if stale_blocks:
            parts.append(f"{len(stale)} approval(s) the code moved past: "
                         + ", ".join(stale[:3]))
        if unknown_blocks:
            parts.append(f"{len(unknown)} unverifiable approval(s): "
                         + ", ".join(unknown[:3]))
        return {"decision": "deny",
                "reason": (f"{blocked_action}: freshness denies — "
                           + "; ".join(parts)
                           + ". Re-measure in a new E record, or retire the "
                             "old one (blackboard.py handoff-retire) so the "
                             "claim stops unlocking code.")}
    shown = (stale + unknown)[:3]
    return {"decision": "allow", "methodology_warnings": [
        f"FRESHNESS: {len(stale)} approval(s) the code moved past, "
        f"{len(unknown)} unverifiable — " + ", ".join(shown)
        + (". This is a report, not a block: `[hooks] freshness = deny` / "
           "`[freshness] unknown_policy = deny` make it one.")]}


def find_approved(target: str, recs_dir: str | None = None,
                  root: str = "") -> tuple[bool, str, dict | None]:
    """Find a VERIFIED record whose scope matches target.

    Returns ``(approved, detail, freshness)``. `freshness` is the derived
    verdict of the record that opened the door (None when the plane is off):
    under `[hooks] freshness = "deny"` a STALE approval does NOT unlock code,
    and under the default `"warn"` it unlocks with the reason attached so the
    caller can surface it — the write is never silently blessed by an approval
    the code has moved past (E-053). Whether a verdict blocks is decided in ONE
    place (`freshness.blocks`), so the write path, the commit gate and the
    deploy gate cannot disagree — UNKNOWN joins STALE there only when
    `[freshness] unknown_policy = "deny"`.
    """
    if not _load_gate():
        return False, "gate script not available", None
    target_rel = norm_path(target).lstrip("/")
    if not root:
        root = repo_root({})
    recs_dir = recs_dir or "docs/experiments"
    if pathlib.PurePosixPath(recs_dir).is_absolute():
        return False, "absolute recs_dir rejected", None
    base = pathlib.Path(root) / recs_dir
    if not base.is_dir():
        return False, "docs/experiments/ not found", None
    key_missing = False
    best = None
    advisory = None
    advisory_msg = (f"record {{rec}} is ADVISORY-BLOCKED (genuine token, "
                    f"code stays closed: small sample, n unknown, or metric "
                    f"mismatch — re-measure in a new record)")

    deferred = []
    scope_error = set()
    for rec in sorted(base.glob("*.md")):
        if rec.name == "_template.md":
            continue
        try:
            # Cheap scope pre-filter: cached file read, no gate key needed.
            candidate = gate.scope_matches(record_scope_cached(str(rec)), target_rel)
        except Exception:
            candidate = False
            scope_error.add(rec)
        if not candidate:
            deferred.append(rec)
            continue
        rc, _scope = _cached_verify(str(rec))
        if rc == 3:
            key_missing = True
        elif rc == 2:
            if advisory is None:
                advisory = advisory_msg.format(rec=rec)
        elif rc == 0:
            fresh = _record_freshness(root, str(rec))
            # Local import (the engine imports freshness lazily): without it the
            # verdict comparison below raised NameError on every scope-matched
            # APPROVED record, so the write path crashed instead of warning.
            try:
                from . import freshness as fr
            except Exception:
                fr = None
            if fr and fresh and fresh["verdict"] in (fr.STALE, fr.UNKNOWN):
                try:
                    blocks = fr.blocks(fresh["verdict"])
                except Exception:
                    blocks = False
                if fresh["verdict"] == fr.STALE:
                    trouble = (
                        f"but the code has moved past it: {fresh['reason']} "
                        f"({fresh['changed'][:3] if fresh['changed'] else 'code state'} "
                        f"changed after approval"
                        f"{', witness ' + fresh['commit'][:12] if fresh['commit'] else ''}). ")
                else:
                    trouble = (f"but its code state cannot be established "
                               f"here: {fresh['reason']}. ")
                stale_msg = (
                    f"record {rec} is APPROVED and scope-matched, "
                    f"{trouble}"
                    f"Re-measure in a new E record (add a plain "
                    f"`- **Re-Measured-By:** E-NNN (date)` line to {rec.name}), or "
                    f"declare this file in the current record's plan "
                    f"(--amend-plan). "
                    f"[hooks] freshness = deny blocks stale; "
                    f"[freshness] unknown_policy = deny blocks unverifiable.")
                if blocks:
                    return False, stale_msg, fresh
                return True, f"record {rec} (scope matched)", fresh
            return True, f"record {rec} (scope matched)", fresh

    for rec in deferred:
        rc, _scope = _cached_verify(str(rec))
        if rc == 3:
            key_missing = True
        elif rc == 2:
            if advisory is None:
                advisory = advisory_msg.format(rec=rec)
        elif rc == 0 and rec not in scope_error and best is None:
            best = f"record {rec} scope not matched"

    if key_missing:
        return False, ("gate key not configured (" + _plugin_script_cmd(
            'skills/bmad-research-experiment/scripts/run_experiment.py',
            '--init-secret') + ")"), None
    # A VERIFIED record that simply does not cover this target is the ACCURATE
    # reason, and it must win over an advisory seeded by an unrelated record:
    # with E-001 ADVISORY-BLOCKED and E-002 VERIFIED, a write to `lib/x.py`
    # (outside BOTH scopes) was reported as "E-001 is ADVISORY-BLOCKED", which
    # sends the agent off to re-measure E-001 instead of widening the scope it
    # is actually missing. `advisory` still wins when no scope-mismatch exists,
    # i.e. for the write the advisory record DOES cover.
    if best:
        return False, best, None
    return False, advisory or "no approved experiment record", None


# Instrument paths stage BEFORE approval: the gate refuses to measure a bench
# in a free zone (scratch/tmp) and --run is the only measurement path, so a
# bench that could only be written post-approval would deadlock every first
# experiment under code_guard=hard. Staging is narrow: only scripts/bench/**
# targets, only under a well-formed UNDECIDED record whose Code Scope covers
# the bench path. Product code still requires a VERIFIED record — staging
# never unlocks apps/**, src/** or any other code target.
_INSTRUMENT_PREFIXES = ("scripts/bench/",)


def _is_instrument_path(rel: str) -> bool:
    """True when the project-relative path is a measurement bench target."""
    p = norm_path(rel or "").lstrip("/")
    return any(p.startswith(prefix) for prefix in _INSTRUMENT_PREFIXES)


def find_staged_instrument(target: str, root: str = "") -> tuple[bool, str]:
    """True when target is a bench staged by a well-formed undecided record.

    Well-formed = all REQUIRED_DRAFT fields present, Experiment id names a
    run, hypothesis claim parses, and no Decision written yet (placeholder
    or empty). The record's Code Scope must cover the bench path. Any
    unreadable record, parse failure or missing gate fails CLOSED (no allow).
    """
    if not _load_gate():
        return False, "gate script not available"
    try:
        target_rel = norm_path(target).lstrip("/")
        if not root:
            root = repo_root({})
        base = pathlib.Path(root) / "docs" / "experiments"
        if not base.is_dir():
            return False, "docs/experiments/ not found"
        for rec in sorted(base.glob("*.md")):
            if rec.name == "_template.md":
                continue
            try:
                text = rec.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            try:
                fields = gate.record_fields(text)
                if any(not fields.get(need) for need in gate.REQUIRED_DRAFT):
                    continue
                if gate.experiment_id_issue(text) is not None:
                    continue
                gate.hypothesis_claim(fields.get("Hypothesis", ""))
                decision = (fields.get("Decision", "") or "").strip()
                if decision and not gate._is_placeholder(decision):
                    continue  # decided — the VERIFIED path owns it, not staging
                if not gate.scope_matches(gate.record_scope(str(rec)), target_rel):
                    continue
            except (ValueError, AttributeError, TypeError):
                continue
            except Exception:
                continue
            return True, f"staged instrument for undecided {rec.name} (scope matched)"
    except Exception:
        return False, "staged-instrument check failed"
    return False, "no undecided record stages this bench"


_STRUCTURAL_DIR = ("docs", "development", "structural")
_STRUCTURAL_MIN_JUSTIFICATION = 40


def _struct_field(text: str, name: str) -> str:
    m = re.search(rf"^-\s*\*\*{re.escape(name)}:\*\*\s*(.+)$", text, re.M)
    return m.group(1).strip() if m else ""


def find_structural_declaration(target: str, root: str = "") -> tuple[bool, str]:
    """True when a Structural Scope record declares this path.

    The honest third category. The guard knew two kinds of code: product code
    (needs a VERIFIED experiment) and measurement benches (stage under an
    undecided record). Code that carries no empirical claim had neither — a
    scaffold, a CI workflow, a layout shell, a wiring module. The only way past
    was to invent a hypothesis, and a fabricated experiment is a worse outcome
    than an admitted exemption: a subagent in a consuming project did exactly
    that, writing a fake E-001 to obtain permission to save an HTML file.

    A structural record says, in public: this code makes no claim that could be
    measured, here is why, and a QR must ratify it. Deliberately narrow:

      - never covers scripts/bench/** — a bench needs a real experiment, and
        this path must not become a way around the instrument rule;
      - Status must be 'declared' or 'ratified'; anything else is ignored;
      - a Scope glob and a justification of at least 40 characters are both
        required, because an unexplained exemption is the abuse case;
      - an approved record wins: this check is only reached when no approval
        covered the path, so it can never downgrade a measured claim;
      - every write warns, so the exemption is never invisible in a session.
    """
    try:
        if not root:
            root = repo_root({})
        base = pathlib.Path(root).joinpath(*_STRUCTURAL_DIR)
        if not base.is_dir():
            return False, "no structural declarations"
        target_rel = norm_path(target).lstrip("/")
        if not target_rel:
            return False, "empty target"
        for rec in sorted(base.glob("*.md")):
            if rec.name.startswith("_"):
                continue
            try:
                text = rec.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            status = _struct_field(text, "Status").lower()
            if status not in ("declared", "ratified"):
                continue
            if len(_struct_field(text, "Justification")) < _STRUCTURAL_MIN_JUSTIFICATION:
                continue
            scope = _struct_field(text, "Scope").replace("`", "")
            patterns = [p for p in re.split(r"[,\s]+", scope) if p]
            for pat in patterns:
                try:
                    if gate.scope_matches(pat, target_rel):
                        return True, (f"structural scope declared in {rec.name} "
                                      f"(status: {status}, unmeasured)")
                except Exception:
                    continue
    except Exception:
        return False, "structural check failed"
    return False, "no structural declaration covers this path"


def guard(json_in: dict) -> dict:
    """PreToolUse guard: block code writes without approved experiment record."""
    norm = normalize_hook_input(json_in)
    tool_name = norm["tool_name"]
    tool_input = norm["tool_input"]
    root = repo_root(json_in)
    _soft_warnings: list[str] = []  # warn-only findings when code_guard=soft

    # Hot-path fast exits FIRST:
    if tool_name == "unknown":
        return {
            "decision": "allow",
            "methodology_warnings": [
                f"Unrecognized tool '{norm['raw_tool_name']}' — guard skipped; "
                f"verify this write manually."
            ],
        }

    if tool_name == "terminal":
        _cmd = str(tool_input.get("command", "") or "")
        if not _cmd.strip():
            return {"decision": "allow"}
    elif tool_name in ("file_editor", "notebook_editor"):
        _p = str(tool_input.get("path", "") or "")
        _multi = tool_input.get("paths")
        _has_multi = isinstance(_multi, list) and any(str(x).strip() for x in _multi)
        if not _p.strip() and not _has_multi:
            return {"decision": "allow"}
    elif tool_name not in ("terminal", "file_editor", "notebook_editor"):
        return {"decision": "allow"}

    # Determine targets based on tool
    targets: list[str] = []

    if tool_name == "terminal":
        command = tool_input.get("command", "")
        targets, _var_drop = extract_bash_targets_ex(command)

        # Check for secret references in command
        if _secret_ref(command):
            return {
                "decision": "deny",
                "reason": "Gate key reference detected in command — blocked. "
                          "To check key presence see the orient digest gate_key line or run "
                          "/metodoloji:verify; never print or cat the key file itself."
            }

    elif tool_name in ("file_editor", "notebook_editor"):
        _paths = tool_input.get("paths")
        if isinstance(_paths, list) and any(str(x).strip() for x in _paths):
            targets = [str(x) for x in _paths if str(x).strip()]
        else:
            path = tool_input.get("path", "")
            if path:
                targets = [path]

    # Fast exit: shell/file write with no resolvable targets
    if not targets:
        # F1/T-03: a dropped $destination means the write went SOMEWHERE the
        # guard cannot see — allow (never deny on a guess) but say so, so the
        # blind spot is visible instead of silent. No write operator at all
        # (e.g. `echo $HOME`) stays a plain allow.
        if tool_name == "terminal" and _var_drop:
            return {
                "decision": "allow",
                "methodology_warnings": [
                    "Unresolved variable write target — guard could not verify "
                    "the destination; confirm it is not an unapproved code write."
                ],
            }
        return {"decision": "allow"}

    # Check each target
    for target in targets:
        rel = rel_to_root(root, target)

        # --- Story file validation (runs BEFORE free/code checks) ---
        if _is_story_file(rel):
            try:
                story_content = ""
                if tool_name in ("file_editor", "notebook_editor"):
                    story_content = str(tool_input.get("content", ""))
                    if not story_content.strip():
                        target_path = pathlib.Path(target)
                        if target_path.is_file():
                            story_content = target_path.read_text(encoding="utf-8", errors="replace")
                elif tool_name == "terminal":
                    target_path = pathlib.Path(target)
                    if target_path.is_file():
                        try:
                            story_content = target_path.read_text(encoding="utf-8", errors="replace")
                        except OSError:
                            story_content = ""
                            _soft_warnings.append(_unchecked_story_write_warning(rel))
                    else:
                        heredoc = _story_heredoc_body(command, target)
                        if heredoc is not None:
                            story_content = heredoc
                        else:
                            _soft_warnings.append(_unchecked_story_write_warning(rel))

                if story_content:
                    # Duplicate record IDs
                    unique, dup_reason = _check_duplicate_record_ids(root, rel)
                    if not unique:
                        return {
                            "decision": "deny",
                            "reason": f"Duplicate record ID detected: {dup_reason}"
                        }

                    # Validate experiment_refs in frontmatter — ALWAYS deny:
                    valid, reason, cascade = _validate_story_experiment_refs(story_content, root)
                    if not valid:
                        return {
                            "decision": "deny",
                            "reason": f"Story experiment validation failed for {rel}: {reason}"
                        }
                    _soft_warnings.extend(cascade)
            except Exception as exc:
                sys.stderr.write(f"metodoloji: story validation error for {rel}: {exc}\n")
            # Story validation passed — continue to next target
            continue

        # --- Non-story files: free zone and code target checks ---
        if tool_name in ("file_editor", "notebook_editor"):
            try:
                content = str(tool_input.get("content", "") or "")
                if content and _KEY_ACCESS_IN_CONTENT.search(content):
                    return {
                        "decision": "deny",
                        "reason": f"Secret access pattern detected in {rel} — blocked."
                    }
            except Exception as exc:
                sys.stderr.write(f"metodoloji: secret check error for {rel}: {exc}\n")

        # Free zone — no approval needed
        if is_free(rel):
            continue

        # Check if it's a code target
        if not is_code_target(rel):
            continue

        # E-065 — plan-first check: a declared Implementation Plan (GRP) on a
        # VERIFIED record is the operation's working set. Set membership after
        # one parse (O(1) per target) — the multi-file operation path that
        # file-by-file scope hunting drowned (10/36 repeated denies in a real
        # session). The plan can only ADD a legitimate working set on top of
        # the record: an in-plan write STILL requires the owning record to be
        # genuinely VERIFIED, and plan-external targets fall through to the
        # Code Scope path below exactly as before.
        try:
            recs_base = pathlib.Path(root) / "docs" / "experiments"
            if recs_base.is_dir():
                plan_rec = plan_mod.active_plan_for(
                    rel, str(recs_base), is_verified=_plan_record_verified)
                # The plan only EVER unlocks under a genuinely VERIFIED record.
                # active_plan_for falls back to an ANY-cover record when nothing
                # is verified (the stop report wants that informational view),
                # so the guard re-checks verification here — otherwise an
                # undecided record's plan would unlock writes and the plan layer
                # would become an approval substitute.
                if plan_rec is not None and _plan_record_verified(plan_rec):
                    continue  # in-plan write under a VERIFIED record — allowed
        except Exception:
            pass  # plan layer must never wedge the guard — Code Scope decides below

        # Find approved record
        approved, detail, fresh = find_approved(rel, root=root)
        if approved:
            # Freshness vocabulary (CURRENT/STALE/UNKNOWN) lives in one module;
            # import it HERE — `fr` was referenced without ever being bound in
            # this scope, so every scope-matched APPROVED record raised
            # NameError, the handler crashed, and the fail-closed wrapper turned
            # the crash into "received no input" on the FIRST write an approval
            # was supposed to unlock (found in a live Hermes session: E-002 was
            # APPROVED and src/app.py still could not be written).
            try:
                from . import freshness as fr
            except Exception:
                fr = None
            # The approval opened the door, but a door is not a promise: when
            # the code moved past the record's own scope, say so — the write
            # proceeds under `freshness = warn` and the reason rides the
            # warning channel instead of being silently forgotten (E-053).
            if fr and fresh and fresh.get("verdict") != fr.CURRENT:
                quiet = False
                if fresh["verdict"] == fr.UNKNOWN:
                    # `[freshness] unknown_policy = "silent"`: a legacy corpus
                    # with no witnesses is not lectured on every write.
                    try:
                        from . import freshness as _fr
                        quiet = _fr.unknown_policy() == "silent"
                    except Exception:
                        quiet = False
                if not quiet:
                    _soft_warnings.append(
                        f"{rel}: {fresh['record']} is {fresh['verdict']} — "
                        f"{fresh['reason']}; re-measure it, add a "
                        f"`- **Re-Measured-By:**` line, or plan this file in a "
                        f"current record (--amend-plan).")
            continue
        if not approved:
            # Staged instrument: a bench under a matching undecided record
            # stages BEFORE approval (otherwise the first experiment under
            # code_guard=hard deadlocks: no bench → no measurement → no
            # approval → no bench). Product code has no such path.
            if _is_instrument_path(rel):
                staged, staged_msg = find_staged_instrument(rel, root=root)
                if staged:
                    _soft_warnings.append(
                        f"{rel}: {staged_msg} — instrument staging only; the "
                        f"decision still requires a gate measurement run: "
                        f"{_plugin_script_cmd(
                            'skills/bmad-research-experiment/scripts/run_experiment.py',
                            '--record', 'docs/experiments/E-XXX.md',
                            '--run', '<cmd>')}, "
                        f"and product code stays closed until VERIFIED")
                    continue
            if not _is_instrument_path(rel):
                declared, smsg = find_structural_declaration(rel, root=root)
                if declared:
                    _soft_warnings.append(
                        f"{rel}: {smsg} — this write is NOT backed by a measurement; "
                        f"a QR must ratify the declaration before the story closes.")
                    continue
            from .config import hook_gate_mode
            msg = (f"No approved experiment record for {rel}: {detail}. "
                   f"Create a hypothesis, measure, and get approval with "
                   f"{_plugin_script_cmd(
                       'skills/bmad-research-experiment/scripts/run_experiment.py',
                       '--record', 'docs/experiments/E-XXX.md',
                       '--run', '<cmd>')}")
            msg += _out_of_root_note(rel, target)
            if _is_instrument_path(rel):
                msg += (f" Note: '{rel}' is a measurement bench — benches "
                        f"stage BEFORE approval: write docs/experiments/E-XXX.md "
                        f"first with '{rel}' in its Code Scope, then write the bench.")
            try:
                if (not pathlib.PurePosixPath(rel).suffix
                        and (pathlib.Path(root) / rel).is_dir()):
                    msg += (f" Note: '{rel}' is a directory, not a file — the guard gates "
                            f"file writes; address the full file path inside it.")
            except OSError:
                pass
            # E-065: the actionable remediation for plan-external targets —
            # the operation's plan can absorb the file WITHOUT re-measurement
            # (mechanically chained, HMAC-signed). Replaces the retry loop the
            # old static deny text fed (_prior_attempts history).
            msg += (f" Plan path (E-065): if '{rel}' belongs to this operation's "
                    f"declared plan, add it without re-measuring: "
                    f"{_plugin_script_cmd(
                        'skills/bmad-research-experiment/scripts/run_experiment.py',
                        '--amend-plan', 'docs/experiments/E-XXX.md',
                        '--files', rel, '--reason', '\"<why>\"')}. "
                    f"A decided record refuses amendment — open a new E record instead.")
            try:
                tries = _prior_attempts(root, rel)
                if tries >= 3:
                    msg += (f" This is attempt #{tries + 1} on the same target — retrying "
                            f"the write cannot succeed. Next: create docs/experiments/E-XXX.md "
                            f"from docs/experiments/_template.md with this target in Code Scope, "
                            f"then run {_plugin_script_cmd(
                                'skills/bmad-research-experiment/scripts/run_experiment.py',
                                '--record', '<rec>', '--run', '<cmd>')}. ")
            except Exception:
                pass

            if hook_gate_mode("code_guard") == "soft":
                _soft_warnings.append(msg)
                continue
            return {"decision": "deny", "reason": msg}

    if _soft_warnings:
        return {"decision": "allow", "methodology_warnings": _soft_warnings}

    return {"decision": "allow"}


# --- Quality Gate (PreToolUse, terminal) ---

def _is_absolute_target(rel: str) -> bool:
    """True when the (normalized) target path is not project-relative."""
    return bool(re.match(r"^[a-zA-Z]:[/\\]", rel) or rel[:1] == "/")


def _out_of_root_note(rel: str, target: str = "") -> str:
    """Honest remediation text when the denied target lies outside the project.

    Real opencode session (2026-09-30): an edit of the INSTALLED plugin's own
    source was denied with the generic "create E-XXX" advice, illustrated by a
    record from an unrelated project — implying a project-relative approval
    path that a path outside the project does not have. Scope matching runs on
    the full path, so only an absolute Code Scope entry can cover such a
    target; and plugin source trees in a consuming project stay behind the
    gate BY DESIGN (see PLUGIN_FREE_PREFIXES — self-modification is released
    only when the guarded project IS the methodology repository).

    The plugin-tree check runs on the ORIGINAL target, not the project-relative
    rel: when the plugin install sits under the project root rel_to_root()
    strips the root, so rel no longer carries the absolute path the membership
    test needs.
    """
    if not rel:
        return ""
    try:
        from .config import _METHODOLOGY_ROOT
        metho = norm_path(str(_METHODOLOGY_ROOT)).rstrip("/")
        abs_n = norm_path(target) if target else norm_path(rel)
    except Exception:
        return ""
    if metho and (abs_n == metho or abs_n.startswith(metho + "/")):
        return (f" Note: '{rel}' is inside the installed metodoloji plugin tree, "
                f"outside this project — consuming projects keep plugin source "
                f"(hooks/, scripts/, skills/, custom/) behind the gate by design. "
                f"The supported change path is the metodoloji repository itself, "
                f"where those trees are a self-modification free zone; a record "
                f"in this project can only cover this path via an absolute Code "
                f"Scope entry.")
    if not _is_absolute_target(rel):
        return ""
    return (f" Note: '{rel}' is outside the project root — scope matching runs "
            f"on this full path, so a record in this project can only cover it "
            f"via an absolute Code Scope entry, never a project-relative glob.")


def _prior_attempts(root: str, rel: str) -> int:
    """Prior write attempts at the same target in the audit trail (bounded, fail-open).

    2026-09-25 LIMX session: 10 identical denies for scripts/bench/gemv_bench.c
    and 36 for docs/arge — the deny text never changed, so neither did the
    agent's behavior. The count lets the deny escalate instead of repeating
    itself (see guard()). Only runs on the deny path, so the hot path pays
    nothing; the trail holds writes only (read-only calls are skipped), so
    every hit is a genuine attempt.
    """
    if not rel:
        return 0
    try:
        from .config import log_file
        log_path = pathlib.Path(root).absolute() / log_file()
        with open(log_path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 65536))
            tail = f.read().decode("utf-8", errors="replace")
    except OSError:
        return 0
    n = 0
    needle = rel.replace("\\", "/").lstrip("/")
    for line in tail.splitlines():
        if needle and needle in line.replace("\\", "/"):
            n += 1
    return n


def _is_git_commit(command: str) -> bool:
    """True if command is a git commit (or git commit -am, etc.)."""
    return bool(re.search(r"\bgit\b.*\bcommit\b", command))


# Roots that can hold a story file, project-relative with the glob that names
# a story in that root. Methodology records live in docs/development/stories
# (`S-*.md`), but the plugin config's `implementation_artifacts` default points
# at docs/development/native — a project on the config layout writes its story
# drafts (`{story_key}.md`, e.g. `1-1-bench-in-ci.md`) there. Scanning only the
# stories root let such a project reach `done` with the QR/SP/IR gates silently
# not firing (fikir deep-check 2026-10-01: "sprint vs sprint-status" confusion
# was, underneath, a done story the gate could not see). Templates (`_*.md`)
# and the sprint-status board (`.yaml`) are not stories and are skipped.
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
    names = {k.lower() for k in _DECLARED_KEY_RE.findall(text)}
    names |= {pathlib.PurePosixPath(p.replace("\\", "/")).stem.lower()
              for p in _DECLARED_PATH_RE.findall(text)}
    return names


def _declared_story_names(text: str) -> set[str]:
    """Story keys/paths a record DECLARES as its subject (never a prose mention)."""
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
    names = _declared_story_names(text) | _names_in(_first_heading(text)) \
        | _names_in(path.name)
    return _declaration_covers(names, story_key, story_stem)


def _story_key_of(content: str, path: pathlib.Path) -> str:
    """The story key for a story file: explicit field, then title, then slug,
    then filename stem. Every caller needs the same precedence."""
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
    return _find_done_stories_without_record(root, "QR-*.md", "docs/quality")


def _find_done_stories_without_pr(root: str) -> list[str]:
    """Find stories with Status: done that lack a PR record.

    Restored: the deploy gate (Gate 4) calls this when include_pr is set;
    commit 48ed1f2 dropped the definition while the call site survived,
    raising NameError at deploy time.
    """
    return _find_done_stories_without_record(root, "PR-*.md", "docs/development")


def _find_done_stories_without_sp(root: str) -> list[str]:
    """Find stories with Status: done that reference an SP but lack SP record."""
    return _find_done_stories_without_record(root, "SP-*.md", "docs/development",
                                             require_sp_ref=True)


def _find_done_stories_without_ir(root: str) -> list[str]:
    """Find done stories when no IR record exists (Kapi 1 gate bypassed).

    IR is a project-level readiness record — if ANY done stories exist but
    NO IR records exist in docs/development/, the readiness gate was skipped.
    Returns list of story keys if IR is missing, empty list if IR exists.
    """
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


def _check_all_stories_quality(root: str, blocked_action: str) -> dict:
    """Validate all stories in docs/development/stories for metadata, orphan links, and chain."""
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


def _check_scope_coverage(root: str) -> list[str]:
    """Edited files (work-tree + untracked) sitting outside every approved scope.

    Mirror of check-plugin §3b at commit time (warn-only here — the write-time
    gate is guard itself; this surfaces the gap when the commit is about to
    happen). Reads each APPROVED record's Code Scope with the gate's own
    matcher (single source), verifies tokens like §3b, and exempts the same
    free surfaces utils.is_free exempts. The gate module resolves from the
    ENGINE's own plugin root (never from the project — user projects don't
    carry skills/ at their root).
    """
    plugin_root = pathlib.Path(__file__).resolve().parents[3]
    gate_scripts = plugin_root / "skills" / "bmad-research-experiment" / "scripts"
    if not (gate_scripts / "run_experiment.py").is_file():
        return []  # engine without its gate companion — fail-open
    sys.path.insert(0, str(gate_scripts))
    try:
        import run_experiment as gate_mod  # type: ignore
    except Exception:
        return []  # fail-open: the mirror must never block commits on its own
    # Drop the insert so repeated calls don't pile the same path onto sys.path.
    try:
        sys.path.remove(str(gate_scripts))
    except (ValueError, NameError):
        pass
    edited: list[str] = []
    ran = True
    # F1/T-09: envelope over the 10s PreToolUse budget — each git call gets
    # the REMAINING time, so two hanging gits can never jointly exceed it.
    deadline = time.monotonic() + SCOPE_GIT_TIMEOUT_SECONDS
    for args in (["git", "-C", root, "diff", "--name-only", "HEAD"],
                 ["git", "-C", root, "ls-files", "--others", "--exclude-standard"]):
        # stdin=DEVNULL: under pytest capture on Windows the inherited std
        # handles can be invalid and git intermittently dies with WinError 6
        # ("The handle is invalid"); a fresh stdin sidesteps the race, and the
        # short retry covers the residual flake. Guard must never raise here:
        # the mirror is advisory and its failure is silent by design.
        for attempt in range(3):
            remaining = deadline - time.monotonic()
            if remaining < 1:
                return [], False  # budget spent — fail-open, not ran
            try:
                out = subprocess.run(
                    args,
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=min(SCOPE_GIT_TIMEOUT_SECONDS, remaining),
                    stdin=subprocess.DEVNULL)
                if out.returncode != 0:
                    # git itself rejected the invocation (e.g. rc=129 "Not a
                    # git repository"): not a clean run — never treat its
                    # empty output as evidence of a clean tree.
                    return [], False
                break
            except OSError:
                if attempt == 2:
                    return [], False  # git unavailable — fail-open, not ran
        edited += [l.strip() for l in out.stdout.splitlines() if l.strip()]

    scopes = []
    exp_dir = pathlib.Path(root) / "docs" / "experiments"
    try:
        records = sorted(exp_dir.glob("E-*.md"))
    except OSError:
        records = []
    for rec in records:
        if rec.name == "_template.md":
            continue
        try:
            fields = gate_mod.record_fields(
                rec.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        if "APPROVED" not in fields.get("Decision", ""):
            continue
        # In-process verify (HMAC is milliseconds; a subprocess per record —
        # 40+ cold interpreters — would tax every real commit). verify() also
        # prints; keep the hook output clean.
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                rc = gate_mod.verify(str(rec))
        except Exception:
            continue
        if rc not in (0, 2):
            continue
        sv = fields.get("Code Scope", "")
        if sv and sv.strip().lower() != "none":
            scopes.append(sv)

    misses = []
    for f in edited:
        norm = f.replace("\\", "/")
        if any(gate_mod.scope_matches(sv, norm) for sv in scopes):
            continue
        if norm.endswith((".md",)) and norm.startswith(("docs/",)):
            continue
        if is_free(norm):
            continue
        misses.append(norm)
    return misses, ran


def quality(json_in: dict) -> dict:
    """Quality gate: block git commit if done stories lack IR, QR, or SP records,
    or if stories fail metadata/chain validation, or if a done story's
    embedded QR table contradicts its APPROVED QR record (coherence)."""
    norm = normalize_hook_input(json_in)
    tool_name = norm["tool_name"]
    if tool_name != "terminal":
        return {"decision": "allow"}

    command = norm["tool_input"].get("command", "")
    if not _is_git_commit(command):
        return {"decision": "allow"}

    root = repo_root(json_in)
    root = os.path.abspath(root)

    # 0. Freshness row (E-053): the commit is the last place the question "does
    #    this approval still apply to this code?" can be asked before the work
    #    leaves the tree. Report-only at the shipped modes — see
    #    _freshness_chain_row.
    res_fresh = _freshness_chain_row(root, "git commit blocked")
    if res_fresh.get("decision") == "deny":
        return _apply_gate_strictness(res_fresh, "quality_gate")
    fresh_warnings = list(res_fresh.get("methodology_warnings") or [])

    # 1. Gate record checks (IR → QR → SP)
    res_gate = _check_gate_records(root, "git commit blocked")
    if res_gate.get("decision") == "deny":
        return _apply_gate_strictness(res_gate, "quality_gate")

    # 2. Story metadata and methodology chain validation
    res_stories = _check_all_stories_quality(root, "git commit blocked")
    if res_stories.get("decision") == "deny":
        return _apply_gate_strictness(res_stories, "quality_gate")

    # 3. Embedded QR-table coherence (SP-020 structural fix: approval must
    #    propagate back into done stories' own tables)
    res_coherence = _check_qr_table_coherence(root, "git commit blocked")
    if res_coherence.get("decision") == "deny":
        return _apply_gate_strictness(res_coherence, "quality_gate")

    # 4. Scope-coverage mirror (check-plugin §3b, warn-only at commit time):
    #    edited files outside every approved Code Scope surface here so the
    #    gap is visible when the commit happens, not in a later audit. The
    #    warning is also posted to the board's "scope" channel (replace
    #    semantics: consume-then-post — MAX_ALERTS=32 is a shared pool and
    #    one alert per uncovered state must not evict waiting hand-offs).
    #    A commit warning scrolls away with the transcript; the board is
    #    project-persistent, so the next session's startup peek still sees
    #    the debt. A verified-clean commit CONSUMES the channel: remediation
    #    (the last uncovered edit fixed or scoped) is the only path that
    #    clears the debt. A git-outage run (empty result with ran=False)
    #    deliberately touches nothing — an infrastructure failure must not
    #    be able to silently forgive a real coverage gap. Fail-open twice
    #    over: a missing/corrupt/disabled board changes nothing here, and
    #    the hook decision is allow regardless.
    try:
        misses, ran = _check_scope_coverage(root)
    except Exception:
        misses, ran = [], False
    if misses:
        warn = ("SCOPE COVERAGE: " + ", ".join(misses[:3])
                + (f" (+{len(misses) - 3} more)" if len(misses) > 3 else "")
                + " — outside every approved Code Scope; open an E record with "
                  "this file in its scope before relying on these edits")
        try:
            from .config import blackboard_enabled
            if blackboard_enabled():
                from . import blackboard as bb
                bb.consume_alerts(root, "scope")
                bb.post_alert(root, "scope", "warn", warn, sender="quality-gate")
        except Exception:
            pass  # board unavailable — the transcript warning already fired
        return {"decision": "allow", "methodology_warnings": [warn] + fresh_warnings}
    if ran:
        # Verified clean (git answered, records evaluated) — clear the debt.
        try:
            from .config import blackboard_enabled
            if blackboard_enabled():
                from . import blackboard as bb
                bb.consume_alerts(root, "scope")
        except Exception:
            pass  # board unavailable — nothing to clear, nothing to block

    if fresh_warnings:
        return {"decision": "allow", "methodology_warnings": fresh_warnings}
    return {"decision": "allow"}


# --- Deploy Gate (PreToolUse, terminal) ---

_DEPLOY_CMD_RE = re.compile(
    r"(?i)(?:"
    r"\bterraform\s+(?:apply|destroy|plan)\b|"
    r"\bkubectl\s+(?:apply|rollout|deploy)\b|"
    r"\bdocker\s+(?:compose\s+)?(?:up|deploy)\b|"
    r"\bansible\s+(?:playbook|deploy)\b|"
    r"\bgit\s+push\s+(?:origin|upstream)\s+(?:main|master|production|prod)\b|"
    r"\b部署\b|"
    r"\bdeploy\b"
    r")"
)


def _apply_gate_strictness(result: dict, gate_key: str) -> dict:
    """Soft mode → a gate deny becomes warn-only allow.

    gate_key is the config key that controls this gate (quality_gate or
    deploy_guard). Read live (per-call) so config changes apply without reload.
    """
    if result.get("decision") != "deny":
        return result
    from .config import hook_gate_mode
    if hook_gate_mode(gate_key) != "hard":
        return {"decision": "allow", "methodology_warnings": [result["reason"]]}
    return result


def _check_gate_records(root: str, blocked_action: str, include_pr: bool = False) -> dict:
    """Run the record-chain gate checks (IR → QR → SP → [PR]).

    Shared by quality() and deploy(). Returns a deny dict with reason, or
    {"decision": "allow"} when all required records exist.
    """
    # Check IR (Gate 1 — project-level readiness)
    missing_ir = _find_done_stories_without_ir(root)
    if missing_ir:
        return {
            "decision": "deny",
            "reason": (
                f"{blocked_action}: {len(missing_ir)} done story(s) exist but no Implementation Readiness (IR) record. "
                f"Stories: {', '.join(missing_ir)}. "
                f"Run bmad-check-implementation-readiness to create IR record."
            ),
        }

    # Check QR (Gate 3)
    missing_qr = _find_done_stories_without_qr(root)
    if missing_qr:
        return {
            "decision": "deny",
            "reason": (
                f"{blocked_action}: {len(missing_qr)} story(s) marked 'done' lack Quality Record (QR). "
                f"Stories: {', '.join(missing_qr)}. "
                f"A QR covers a story only when it DECLARES it (a `| Story | … |` row "
                f"or a `- **Story:** …` field; a mention in the body is not a record). "
                f"Create QR with: {_qr_remedy(root, missing_qr)}"
            ),
        }

    # Check SP (Gate 2)
    missing_sp = _find_done_stories_without_sp(root)
    if missing_sp:
        return {
            "decision": "deny",
            "reason": (
                f"{blocked_action}: {len(missing_sp)} story(s) reference SP but lack Sprint Planning record. "
                f"Stories: {', '.join(missing_sp)}. "
                f"Run bmad-sprint-planning to create SP record."
            ),
        }

    # Check PR (Gate 4 — deploy only)
    if include_pr:
        missing_pr = _find_done_stories_without_pr(root)
        if missing_pr:
            return {
                "decision": "deny",
                "reason": (
                    f"{blocked_action}: {len(missing_pr)} story(s) lack Production Readiness (PR) record. "
                    f"Stories: {', '.join(missing_pr)}. "
                    f"A PR record covers a release's stories when it DECLARES them "
                    f"(`- **Stories:** S-001, S-002` or a `| Story | … |` row). "
                    f"Create PR record before deploying."
                ),
            }

    return {"decision": "allow"}


def pre(json_in: dict) -> dict:
    """Combined PreToolUse gate: guard + quality + deploy in ONE process.

    hooks/hooks.json used to fire three separate hook processes per tool
    call (guard for Write/Edit/terminal, quality + deploy for Bash/terminal
    — a `terminal` call paid 3x sh-dispatch + 3x python cold-start). This
    runs all three checks sequentially in a single interpreter:

      1. guard()   — experiment approval for code writes (fail-closed)
      2. quality() — git-commit record gate (soft/hard per config)
      3. deploy()  — deploy-command record gate (soft/hard per config)

    Deny wins: the first deny short-circuits (later gates add nothing
    once the call is already blocked). Soft-gate warnings accumulate
    across all three. Non-gated tools exit after guard()'s own fast
    path, so quality()/deploy() only pay normalize() + a regex on the
    hot path — and only git-commit/deploy-shaped commands reach the
    record scans.
    """
    res = guard(json_in)
    if res.get("decision") == "deny":
        return res
    warnings: list[str] = list(res.get("methodology_warnings") or [])

    for gate_fn in (quality, deploy):
        try:
            r = gate_fn(json_in)
        except Exception:
            continue  # fail-open: one gate crashing must not wedge the call
        if r.get("decision") == "deny":
            out = dict(r)
            if warnings:
                prior = list(out.get("methodology_warnings") or [])
                out["methodology_warnings"] = warnings + prior
            return out
        warnings.extend(r.get("methodology_warnings") or [])

    if warnings:
        return {"decision": "allow", "methodology_warnings": warnings}

    # Sync carrier for the close-out relay (H1): batons are posted by skills'
    # `mirror --to` close-outs, but between session edges nothing re-announced
    # them — the mailjs session (2026-09-23) forgot dev-story's code-review
    # hand-off one turn after the close-out posted it. pre() is the only hook
    # on the every-turn path: ship mirror's pending_note through PostToolUse
    # (audit.py throttles the repeat and mirrors the warning shape). Mirrors
    # the gate contract: announce-only, never consumes, never blocks,
    # fail-open.
    try:
        from .mirror import pending_note
        note = pending_note(os.path.abspath(repo_root(json_in)))
        if note:
            return {"decision": "allow", "methodology_warnings": [note]}
    except Exception:
        pass
    return {"decision": "allow"}


def deploy(json_in: dict) -> dict:
    """Deploy gate: block deployment if done stories lack IR, QR, SP, or PR records.

    This is the Gate 1+2+3+4 enforcement — stories marked 'done' must have:
    - An Implementation Readiness record (IR) in docs/development/ (Gate 1)
    - A Sprint Planning record (SP) in docs/development/ (if story references SP, Gate 2)
    - A Quality Record (QR) in docs/quality/ (Gate 3)
    - A Production Readiness (PR) record in docs/development/ (Gate 4)
    
    Also verifies the methodology chain E→IR→SP→S→QR→PR is complete for production.
    """
    norm = normalize_hook_input(json_in)
    tool_name = norm["tool_name"]
    if tool_name != "terminal":
        return {"decision": "allow"}

    command = norm["tool_input"].get("command", "")
    if not command or not _DEPLOY_CMD_RE.search(command):
        return {"decision": "allow"}

    root = repo_root(json_in)
    root = os.path.abspath(root)

    # Gate record checks (IR → QR → SP → PR), same rationale as quality():
    # _check_gate_records carries the gate-specific messages; PR (Gate 4) is
    # deploy-only via include_pr=True. The freshness row rides the same
    # decision (E-053): a deploy is the most expensive place to obey a claim
    # the code moved past, so it is asked here too — report-only at the
    # shipped modes, a block under the deny modes.
    res = _check_gate_records(root, "Deploy blocked", include_pr=True)
    if res.get("decision") != "deny":
        fresh = _freshness_chain_row(root, "Deploy blocked")
        if fresh.get("decision") == "deny":
            res = fresh
        elif fresh.get("methodology_warnings"):
            res = dict(fresh)
    return _apply_gate_strictness(res, "deploy_guard")

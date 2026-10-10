"""Utility functions for BMAD hooks engine."""

import os
import pathlib
import re

from .config import (
    CODE_BASENAMES,
    CODE_DIRS,
    EXEC_CONFIG_NAME,
    FREE_DOC_MD,
    FREE_DOC_RAW,
    FREE_PREFIXES,
    INFRA_FILES,
    NON_CODE_BASENAMES,
    NON_CODE_CONFIG_RES,
    NON_CODE_EXTS,
    PLUGIN_FREE_PREFIXES,
)


def _coerce_json_scalar(v, _depth: int = 0) -> str:
    """Str-coerce one hook payload value, defensively.

    Hook input is a wire format from another process: a schema like
    ``{"command": ["cat", "f.txt"]}`` or ``{"path": 123}`` is malformed but
    arrives through the same JSON channel as good input, and the engine must
    never crash on it. Strings pass through untouched; the rest get
    deterministic, shell-parseable string forms (list -> joined words,
    mapping -> k=v words, scalars -> str()). None normalizes to "".

    Recursion is BOUNDED at ``_COERCE_MAX_DEPTH`` (E-007): a depth-500 nested
    list on a routing key used to recurse with two frames per level (function
    + genexpr) and raise RecursionError at main.py's handler call — guard, pre,
    quality, deploy and audit then exited with NO decision (fail-open). Past
    the bound the subtree renders as a deterministic truncation marker: the
    value is preserved as a string (deny direction for a bogus path — it
    resolves outside the root), never a crash.
    """
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, (list, tuple)):
        if _depth >= _COERCE_MAX_DEPTH:
            return f"<list truncated at depth {_COERCE_MAX_DEPTH}>"
        return " ".join(_coerce_json_scalar(x, _depth + 1) for x in v)
    if isinstance(v, dict):
        if _depth >= _COERCE_MAX_DEPTH:
            return f"<dict truncated at depth {_COERCE_MAX_DEPTH}>"
        return " ".join(f"{k}={_coerce_json_scalar(x, _depth + 1)}"
                        for k, x in sorted(v.items()))
    return str(v)


# Wire values deeper than this render as a truncation marker instead of
# recursing (E-007). Legitimate tool payloads are a handful of levels deep;
# 32 leaves every real shape byte-identical while bounding the stack.
_COERCE_MAX_DEPTH = 32


_COERCE_TOOL_KEYS = ("command", "cmd", "path", "file_path", "content")


def _coerce_tool_input(raw) -> dict:
    """Normalize the tool_input surface to a dict with stringified routing keys.

    A non-object tool_input kept no {path,command,content} contract for any
    caller — but it is still one opaque "stdin word/user typed a thing" value,
    so it is carried as the command of a synthetic terminal payload instead of
    crashing on dict() coercion (E-003) or being dropped silently. None/empty
    normalizes to {}; malformed values inside a real object never crash —
    every crash-susceptible value (path/file_path/command/cmd/content) is
    coerced, keys the engine only reads for metadata stay native.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        rendered = _coerce_json_scalar(raw)
        return {"command": rendered} if rendered else {}
    out = {}
    for k, x in raw.items():
        if k in _COERCE_TOOL_KEYS:
            out[k] = _coerce_json_scalar(x)
        else:
            out[k] = x
    return out


def normalize_hook_input(json_in: dict) -> dict:
    """Normalize hook input from either Claude Code or OpenHands to a common schema.

    Claude Code sends: tool_name=Write|Edit|MultiEdit|Bash|PowerShell,
      tool_input={file_path,content,command,...}
    OpenHands sends: tool_name=file_editor|terminal,
      tool_input={path,content,command,...}

    `PowerShell` is Claude Code's shell tool on Windows (a real session emitted
    `PowerShell(...)` calls on the win32 host). Before this mapping it fell
    through to the catch-all "unknown" branch, so the guard WARNED and allowed
    every PowerShell call — `Set-Content src/a.py …` bypassed the experiment
    gate entirely. It normalizes to the same `terminal` schema as `Bash`.

    A name outside both vocabularies normalizes to "unknown" on EVERY path
    (E-010), whatever METODOLOJI_RUNTIME says: guard() then warns instead of
    silently allowing a call it cannot judge.

    Returns a normalized dict with keys: tool_name, tool_input (with
    file_path/content/command), raw_tool_name.
    """
    runtime = os.environ.get("METODOLOJI_RUNTIME", "")
    tool_name = json_in.get("tool_name", "")
    tool_input = _coerce_tool_input(json_in.get("tool_input", {}))

    raw_name = tool_name

    if runtime == "claude" or tool_name in ("Write", "Edit", "MultiEdit",
                                                  "NotebookEdit", "Bash",
                                                  "PowerShell"):
        # Claude Code → normalize to OpenHands convention
        if tool_name in ("Write", "Edit", "MultiEdit"):
            if "file_path" in tool_input and "path" not in tool_input:
                tool_input["path"] = tool_input["file_path"]
            tool_name = "file_editor"
        elif tool_name == "NotebookEdit":
            if "file_path" in tool_input and "path" not in tool_input:
                tool_input["path"] = tool_input["file_path"]
            tool_name = "notebook_editor"
        elif tool_name in ("Bash", "PowerShell"):
            if "command" not in tool_input and "cmd" in tool_input:
                tool_input["command"] = tool_input["cmd"]
            tool_name = "terminal"
    elif runtime == "openhands" or tool_name in ("file_editor", "terminal",
                                                        "notebook_editor"):
        pass  # already normalized
    elif tool_name in ("", None):
        pass  # no tool info (e.g. Stop/SessionStart payloads) — nothing to map
    else:
        tool_name = "unknown"

    # E-010 — unified catch-all. hook-entry.sh ALWAYS passes a runtime
    # (`RUNTIME="${2:-${METODOLOJI_RUNTIME:-openhands}}"`), and each runtime
    # branch above returns early for its own known vocabulary, so an
    # unrecognized name used to reach guard()'s "non-gated tool" fast path
    # unmarked and be allowed silently — the exact outcome the comment above
    # promises to prevent. Anything that is not one of the engine's tools is
    # marked here, on every path (guard warns; it never denies, so a new tool
    # that is simply unknown to us cannot hard-block a call).
    if tool_name not in ("file_editor", "terminal", "notebook_editor", "", None):
        tool_name = "unknown"

    return {
        "tool_name": tool_name,
        "tool_input": tool_input,
        "raw_tool_name": raw_name,
        **{k: v for k, v in json_in.items() if k not in ("tool_name", "tool_input")},
    }


# MSYS/Cygwin drive form: '/c/Users/x' or '/cygdrive/c/Users/x'.
_MSYS_DRIVE_RE = re.compile(r"^/(?:cygdrive/)?([a-zA-Z])(?=[/]|$)")

# A token carrying shell punctuation is not a filename. Redirect destinations
# arrive glued to their terminator (`> /dev/null;.` → "dev/null;."), and the
# fail-closed tail of is_code_target would then read the junk as an unlisted
# extension and report it as code — the Stop report announced "code written
# this session: /dev/null;." in a real session where no code was written.
_SHELL_JUNK_RE = re.compile(r"[\s;&|<>(){}`$'\"*?!\\]")


def _msys_to_native(p: str) -> str:
    """Map an MSYS/Cygwin drive path to the native Windows form (Windows only).

    Git Bash hands native processes paths like '/c/Users/x'. Native Python
    treats them as merely rooted, so os.path.abspath() prefixes the current
    drive ('C:\\c\\Users\\x') and relativizing against a real 'C:\\' project
    root silently fails: every target looks outside the project, which breaks
    free-zone matching and the code-target gate for that call. Converting to
    'c:/Users/x' first makes abspath/norm_path agree with the native form.

    On POSIX the input is a legitimate absolute path and is returned unchanged,
    so this can only ever affect Windows (where '/c/...' has no other meaning).
    """
    if os.name != "nt" or not p:
        return p
    q = p.replace("\\", "/")
    m = _MSYS_DRIVE_RE.match(q)
    return f"{m.group(1)}:" + q[m.end():] if m else p


def norm_path(p: str) -> str:
    """Normalize path to project-relative, forward-slash, no leading './'."""
    p = _msys_to_native((p or "").strip()).replace("\\", "/")
    p = re.sub(r"^[a-zA-Z]:", "", p)  # Remove drive letter
    p = re.sub(r"/{2,}", "/", p)  # collapse duplicate slashes
    while p.startswith("./"):
        p = p[2:]
    # Resolve .. and . lexically without touching the filesystem.
    parts: list[str] = []
    absolute = p.startswith("/")
    for seg in p.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if parts and parts[-1] != "..":
                parts.pop()
            elif not absolute:
                parts.append(seg)
            continue
        parts.append(seg)
    out = "/".join(parts)
    return ("/" + out) if absolute else out


def _project_is_methodology_root() -> bool:
    """True when the project being guarded IS this plugin's own repository.

    The plugin root resolved from env always equals the engine's methodology
    root (the engine lives inside the install dir), so the meaningful signal is
    the project root: CLAUDE_PROJECT_DIR / OPENHANDS_PROJECT_DIR / cwd. When it
    resolves to the methodology root, the plugin source trees (hooks/,
    scripts/, skills/, custom/) are released as a
    self-modification free zone — the methodology working on itself. In any
    ordinary project those trees stay behind the experiment gate.
    """
    try:
        from .config import _METHODOLOGY_ROOT
        project_root = repo_root({})
        return pathlib.Path(project_root).resolve() == pathlib.Path(_METHODOLOGY_ROOT).resolve()
    except Exception:
        return False


def is_free(path: str) -> bool:
    """True if the project-relative path is inside a free zone (no approval needed)."""
    p = norm_path(path).lstrip("/")
    if not p:
        return True
    if FREE_DOC_MD.match(p) or FREE_DOC_RAW.match(p):
        return True
    if p in INFRA_FILES:
        return True
    if p.startswith("explore_") and "/" not in p:
        # Root-level exploration files only: without the slash guard an
        # `explore_*` DIRECTORY releases its whole subtree
        # (explore_evil/payload.py) with no approval — a code-gate bypass.
        # Nested paths stay gated; scratch/ is the wide-open prototyping area.
        return True
    if any(p.startswith(prefix) for prefix in FREE_PREFIXES):
        return True
    # Plugin source trees: free ONLY when the project is this repo itself
    # (self-modification). Otherwise they are protected by the experiment gate.
    if any(p.startswith(prefix) for prefix in PLUGIN_FREE_PREFIXES):
        return _project_is_methodology_root()
    return False


def is_code_target(path: str) -> bool:
    """True if the path is a code target (whitelist: everything except data/markup/asset).

    Order is deliberate: non-code signals (extension/basename, toolchain
    config) win over the CODE_DIRS directory shortcut, so `src/*.md` is
    never code just for living under src/.
    """
    p = norm_path(path).lstrip("/")
    base = pathlib.PurePosixPath(p).name
    first = p.split("/", 1)[0].lower()
    ext = pathlib.PurePosixPath(base).suffix.lower()
    if p == "dev/null" or p.startswith("dev/null/"):
        return False
    # Not a path at all (shell punctuation glued to a redirect target): a
    # phantom can never be a code target — never let it reach the fail-closed
    # tail that treats unknown extensions as code.
    if _SHELL_JUNK_RE.search(base):
        return False
    # Executable CI/config (workflows, compose, package.json) is always code,
    # even with a data extension like .yml — the pipeline runs it.
    if EXEC_CONFIG_NAME.search(p):
        return True
    if ext in NON_CODE_EXTS or base.lower() in NON_CODE_BASENAMES:
        return False
    # Toolchain config (prisma.config.ts, tsconfig.json, lockfiles): not
    # application code — never a code target, even under CODE_DIRS.
    if any(rx.search(p) for rx in NON_CODE_CONFIG_RES):
        return False
    if base.lower() in CODE_BASENAMES or first in CODE_DIRS:
        return True
    # Intentionally fail-closed: an unlisted extension is treated as code, so
    # a new data format never silently bypasses the experiment gate. Projects
    # with exotic data trees should add them to NON_CODE_* instead.
    return True


def extract_story_key_from_content(content: str) -> str:
    """Extract story key from file content — the `# Story: <key>` title forms.

    The key is a single token, never one that swallowed a trailing colon: the
    old ``\\S+`` matched the `:` separator itself, so `# Story S-002: Title`
    yielded the key `S-002:` — a token no declaration can ever equal, which
    silently hid the story from its own record check. ``[^\\s:]+`` stops at
    the colon.
    """
    # Try '# Story: S-XXX' header first (handles space variations around colon)
    m = re.search(r"#\s+Story\s*:\s*([^\s:]+)", content, re.IGNORECASE)
    if m:
        return m.group(1)
    # Try '# Story S-XXX' (key before the colon)
    m = re.search(r"#\s+Story\s+([^\s:]+)", content, re.IGNORECASE)
    if m:
        return m.group(1)
    return ""


def repo_root(json_in: dict) -> str:
    """Get repository root from environment variables.

    Priority:
    1. CLAUDE_PROJECT_DIR (Claude Code standard)
    2. OPENHANDS_PROJECT_DIR (OpenHands standard)
    3. json_in["cwd"] (hook input fallback) — STRING values only (E-006)
    4. os.getcwd() (last resort)

    Step 3 honors the payload cwd only when it is a string (E-006): the
    payload is a wire format from another process, and a mistyped cwd (123,
    4.5, ["x"], {"a": 1}) used to reach _msys_to_native() and raise — guard,
    quality, audit, stop and session_start then exited with a traceback and NO
    decision, the fail-open turn the engine exists to prevent (E-002's known
    shape gap, at the top-level keys). A non-string cwd is not a root signal:
    skip it and fall through to the documented next priority instead of
    crashing, and never coerce one into a root (a fabricated "123" root under
    the process cwd would be a silently wrong tree — worse than no signal).
    """
    cwd = json_in.get("cwd")
    root = (
        os.environ.get("CLAUDE_PROJECT_DIR")
        or os.environ.get("OPENHANDS_PROJECT_DIR")
        or (cwd if isinstance(cwd, str) else None)
        or os.getcwd()
    )
    # Normalize an MSYS drive path before abspath, so the root and the targets
    # relativized against it stay in the same (native) form — see _msys_to_native.
    return os.path.abspath(_msys_to_native(root))


def rel_to_root(root: str, p: str, cwd: str | None = None) -> str:
    """Resolve a possibly-relative path against cwd (or root) and relativize to root.

    Only a slash-boundary prefix match strips the root: /repo-evil/x under
    root /repo stays absolute (never rebased as repo-internal).
    """
    if not p:
        return ""
    p = _msys_to_native(p.strip().strip("\"'"))
    base = cwd or root
    full = (
        p
        if os.path.isabs(p) or re.match(r"^[a-zA-Z]:[/\\]", p)
        else os.path.join(base, p)
    )
    r = norm_path(root).rstrip("/")
    f = norm_path(full)
    if r and (f == r or f.startswith(r + "/")):
        return f[len(r):].lstrip("/")
    return f


# --- Definition-of-Done validation (shared: guard story + audit QR checks) ---
# Single source of truth for "what is a valid DoD item", so the guard's story
# DoD validation and the audit's QR DoD warning can never disagree about the
# rules: every DoD item needs a DoD-NNN identifier AND a recorded verification
# (a Verify: field for story definitions; a Verify:/Evidence/result marker for
# QR records, which report the verification outcome).

_DOD_ID_RE = re.compile(r"[\[\(]?DoD-(\d+)[\]\)]?")
_VERIFY_FIELD_RE = re.compile(r"Verify:\s*(.+)")
# QR result markers: status symbols, an arrowed verdict, or an Evidence field.
_QR_RESULT_RE = re.compile(
    r"[✓✅❌⚠️]|->\s*(?:PASS|FAIL|pass|fail|passed|failed|pending|blocked)|Evidence:\s*\S")
# Presence signals for "does this text carry DoD content at all".
_DOD_SIGNAL_RE = re.compile(
    r"DoD\s*Item|DoD-\s*\d+|Definition\s+of\s+Done|##\s+[^\n]*\bDoD\b",
    re.IGNORECASE)
_DOD_EMPTY_CELL = {"", "—", "-"}


def scan_dod_items(text: str, *, qr: bool = False) -> list[dict]:
    """Scan text for DoD items and their structural facts.

    Bullet items (checkbox "- [ ] DoD-001 …" or token "- [DoD-001] …" — the
    record-template style) are recognised together with their indented
    continuation lines, where a ``Verify:`` field (and, in QR mode, an
    ``Evidence:`` line or result marker) may live. In QR mode, markdown-table
    rows ("| DoD-001 | … |") are recognised as items too.

    Returns a list of dicts with keys: kind ("bullet" | "row"), first (the
    item's first line), has_identifier, has_verification.
    """
    lines = text.splitlines()
    items: list[dict] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i].strip()

        # QR DoD verification tables: | DoD-001 | ✅ passed | evidence | date |
        if qr and line.startswith("|") and _DOD_ID_RE.search(line):
            cells = [c.strip() for c in line.strip("|").split("|")]
            items.append({
                "kind": "row",
                "first": line,
                "has_identifier": bool(cells and _DOD_ID_RE.search(cells[0])),
                # A recorded result = any non-empty status/evidence cell.
                "has_verification": any(
                    c and c not in _DOD_EMPTY_CELL for c in cells[1:]),
            })
            i += 1
            continue

        is_checkbox = line.startswith(("- [ ]", "- [x]", "- [X]"))
        is_token = line.startswith("- ") and bool(_DOD_ID_RE.search(line))
        if not (is_checkbox or is_token):
            i += 1
            continue

        # Item block: the bullet plus its indented sub-lines (where Verify: /
        # Evidence: fields are written, template style).
        block = [line]
        j = i + 1
        while j < n and lines[j][:1].isspace():
            block.append(lines[j].strip())
            j += 1
        block_text = "\n".join(block)
        has_verify = bool(_VERIFY_FIELD_RE.search(block_text)) or "Verify:" in block_text
        has_result = _QR_RESULT_RE.search(block_text) if qr else None
        items.append({
            "kind": "bullet",
            "first": line,
            "has_identifier": bool(_DOD_ID_RE.search(line)),
            "has_verification": bool(has_verify or has_result),
        })
        i = j
    return items


def dod_issues(text: str, *, qr: bool = False) -> list[str]:
    """Structural DoD issues in *text* — identical rules for guard and audit.

    Story mode (qr=False): bullet items only; each item needs a DoD-NNN
    identifier and a Verify: field (inline or on an indented sub-line).
    QR mode (qr=True): bullet items and DoD table rows; each item needs an
    identifier and a recorded verification (Verify:, Evidence:, or a result
    marker such as "✓ PASS").

    Returns guard-style messages so both layers report the same defects.
    """
    issues: list[str] = []
    for item in scan_dod_items(text, qr=qr):
        first = item["first"]
        if not item["has_identifier"]:
            issues.append(f"DoD item without identifier: {first[:60]}...")
        if not item["has_verification"]:
            issues.append(f"DoD item missing Verify field: {first[:60]}...")
    return issues


def has_dod_content(text: str) -> bool:
    """True when *text* carries any DoD signal (section, table, or item)."""
    return bool(_DOD_SIGNAL_RE.search(text))

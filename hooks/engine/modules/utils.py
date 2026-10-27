"""Utility functions for BMAD hooks engine."""

import ast
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
    PLUGIN_FREE_FILES,
    PLUGIN_FREE_PREFIXES,
)

# Free-zone case key (E-035): membership is compared against a case-folded
# path ONLY where the filesystem itself folds (os.name == "nt" — Windows).
# On a case-sensitive filesystem the fold would free `DOCS/e/raw/`, a
# directory distinct from `docs/` (fail-open); without the fold on nt,
# `SCRATCH/` is gated although it IS the documented gapless zone
# (fail-closed). `os.path.normcase` is the OS's own answer but also swaps
# separators; norm_path() already normalized those, so plain lower() carries
# exactly the fold half we need.
_FREE_ZONE_FOLD = os.name == "nt"
INFRA_FILES_KEY = frozenset(f.lower() for f in INFRA_FILES) if _FREE_ZONE_FOLD else INFRA_FILES
_FREE_PREFIXES_KEY = tuple(x.lower() for x in FREE_PREFIXES) if _FREE_ZONE_FOLD else FREE_PREFIXES
_PLUGIN_FREE_PREFIXES_KEY = (
    tuple(x.lower() for x in PLUGIN_FREE_PREFIXES) if _FREE_ZONE_FOLD else PLUGIN_FREE_PREFIXES
)
_PLUGIN_FREE_FILES_KEY = (
    frozenset(x.lower() for x in PLUGIN_FREE_FILES) if _FREE_ZONE_FOLD else PLUGIN_FREE_FILES
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


# --- OpenCode tool vocabulary (V2) ------------------------------------------
# OpenCode (V2) exposes lower-case tool names. They are normalized onto the same
# internal contract the other runtimes use, so the gate logic stays shared:
#   write / edit / multiedit        → file_editor  (single `filePath`)
#   patch (alias `apply_patch`)      → file_editor  (many paths inside `patchText`)
#   shell | bash                     → terminal     (the shell tool name differs
#     between OpenCode builds; both spellings are accepted, same as
#     Write/PowerShell are both file/terminal tools in the Claude branch)
# Non-write tools (read/glob/grep/webfetch/websearch/question/skill/task/…)
# normalize to "" so guard's non-gated fast path allows them WITHOUT a warning —
# a warn on every read would drown the real signal. A name outside this
# vocabulary keeps the E-010 catch-all ("unknown" → warn), while the shared
# path + mutation inference below can route conventional future write payloads
# through the file gate instead of merely warning.
_OPENCODE_WRITE_TOOLS = {
    "write": "file_editor", "edit": "file_editor", "multiedit": "file_editor",
    "patch": "file_editor", "apply_patch": "file_editor",
}
_OPENCODE_PATCH_TOOLS = frozenset({"patch", "apply_patch"})
_OPENCODE_SHELL_TOOLS = {"bash": "terminal", "shell": "terminal"}
_OPENCODE_READ_TOOLS = {
    "read", "glob", "grep", "list", "ls", "lsp", "webfetch", "websearch",
    "question", "skill", "subagent", "task", "execute", "todowrite", "todoread",
    "revert", "view", "metodoloji_verify", "metodoloji_audit",
}
# Key an OpenCode file tool carries its path under. Documented builds use
# `filePath`; the local `path` spelling is accepted too so a vocabulary shift
# never silently drops the guard's target (an empty target = allow).
_OPENCODE_PATH_KEYS = ("path", "filePath", "file_path")
_OPENCODE_ALL_TOOLS = (
    frozenset(_OPENCODE_WRITE_TOOLS) | frozenset(_OPENCODE_SHELL_TOOLS)
    | frozenset(_OPENCODE_READ_TOOLS)
)

# --- Hermes Agent tool vocabulary -------------------------------------------
# Hermes (hermes-agent.nousresearch.com) names one tool per ACTION where Claude
# reuses Write/Edit, and its gateway runs the plugin's hooks in-process. The
# bridge (hermes_adapter.py) always passes --runtime=hermes, so this branch is
# runtime-pinned rather than name-sniffed: `patch` exists in BOTH vocabularies
# (OpenCode's applies a diff, Hermes' replaces a string in one file) and a name
# heuristic would resolve it by whichever branch happened to be tested first.
#   write_file / patch / multi_edit   → file_editor  (`path` + body keys)
#   terminal                          → terminal
#   execute_code                      → terminal     (runs arbitrary Python in
#     the session cwd, so it can write files — the shell-target extractor and
#     the gate-key reference check still see the payload; without this mapping
#     it would normalize to "" and silently bypass the code gate)
# Non-gated tools are listed so guard's fast path allows them WITHOUT a warning
# — Hermes calls web_search/read_file on nearly every turn and a warn per call
# would drown the real signal in the injected context.
_HERMES_WRITE_TOOLS = {
    "write_file": "file_editor",
    "patch": "file_editor",
    "multi_edit": "file_editor",
    "notebook_edit": "notebook_editor",
}
_HERMES_SHELL_TOOLS = {
    "terminal": "terminal",
    "execute_code": "terminal",
}
_HERMES_READ_TOOLS = {
    "read_file", "search_files", "web_search", "web_extract", "browser_navigate",
    "browser_snapshot", "browser_click", "browser_type", "browser_console",
    "browser_vision", "browser_scroll", "browser_press", "browser_back",
    "browser_get_images", "vision_analyze", "todo_list", "skills_list",
    "skill_view", "skill_manage", "delegate_task", "cronjob_manage",
    "text_to_speech", "clarify", "computer_use", "tool_search", "tool_describe",
    "tool_call", "session_search", "hindsight_recall", "hindsight_reflect",
    "hindsight_retain", "process_manage", "manage_connections",
    "metodoloji_orient", "metodoloji_init", "metodoloji_verify",
    "metodoloji_audit",
}
_HERMES_PATH_KEYS = ("path", "file_path", "filename")

# `execute_code` runs arbitrary Python in the session cwd, so "which files does
# this touch" has to be derived from the source before the guard can judge it.
# Two layers, in this order:
#
# 1. AST (primary): resolve the write idioms the language actually has —
#    `open(p, 'w')`, `Path(p).write_text(...)`, `shutil.copy/move` destinations,
#    `os.rename/replace`, dataframe/figure savers — with a tiny constant
#    evaluator so the COMMON shape `p = 'src/a.py'; open(p, 'w')` resolves
#    instead of falling into the blind spot. It also folds `os.path.join('a','b')`,
#    `'a' + 'b'` and placeholder-free f-strings.
# 2. Regex (supplement): a shell redirect living INSIDE a Python string
#    (`os.system("... > src/a.py")`), which extract_bash_targets() gives up on
#    at the surrounding quote. Deliberately not the general shell extractor:
#    Python is full of `>` that is not a redirect (`if x > 3`), and a fake
#    target there turns a computation into a denied write.
#
# What still cannot be resolved — a path read from argv/env/input() — is
# reported as an `unknown` tool call, so the blind spot is visible. The Hermes
# bridge turns that into a warning by default, or a denial under
# `execute_code_guard: deny`.
# A redirect target inside a shell string. Applied to STRING CONSTANTS (the
# AST path hands us the literal body), so it must not require the surrounding
# quote to still be present: `os.system("echo x > src/a.py")` reaches us as
# `echo x > src/a.py`. The path-shape filter at the call site keeps `a > b`
# comparisons out.
_HERMES_PY_REDIRECT_RE = re.compile(r""">{1,2}\s*(?P<p>[^\s'";|&)]+)""")
# Does the snippet write at all? Used only when no literal target was found, to
# tell "pure computation" (silent allow) from "writes somewhere I cannot name"
# (warn) — the difference between noise and a visible blind spot.
_HERMES_PY_WRITE_SIGNAL_RE = re.compile(
    r"""open\(|write_text|write_bytes|os\.system|os\.popen|subprocess\.|shutil\."""
    r"""|os\.rename|os\.replace|to_csv|to_excel|to_parquet|savefig"""
)
_HERMES_SAVER_ATTRS = frozenset({
    "to_csv", "to_excel", "to_parquet", "to_json", "savefig", "save",
})


def _hermes_const_value(node, consts: dict) -> str | None:
    """Constant-fold one expression to a string, or None when not constant.

    Deliberately small: string literals, names bound to a constant earlier,
    `'a' + 'b'`, placeholder-free f-strings, and `os.path.join(...)` of
    constants. Everything else (input(), env lookups, function results) stays
    unresolved — guessing there would deny real work.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return consts.get(node.id)
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            else:
                return None  # a formatted placeholder is not a constant
        return "".join(parts) if parts else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _hermes_const_value(node.left, consts)
        right = _hermes_const_value(node.right, consts)
        if left is not None and right is not None:
            return left + right
        return None
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        func = node.func
        if (func.attr == "join" and isinstance(func.value, ast.Attribute)
                and func.value.attr == "path"):
            parts = [_hermes_const_value(arg, consts) for arg in node.args]
            if parts and all(part is not None for part in parts):
                return "/".join(p for p in parts if p)  # type: ignore[arg-type]
    return None


def _hermes_const_map(tree: ast.AST) -> dict:
    """Name → constant string for simple `p = 'src/a.py'` assignments."""
    consts: dict[str, str] = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            value = _hermes_const_value(node.value, consts)
            if value:
                consts[node.targets[0].id] = value
    return consts


def _hermes_call_target(call: ast.Call, consts: dict) -> str | None:
    """The file one write-shaped call targets, when it is constant."""
    func = call.func
    if isinstance(func, ast.Name) and func.id == "open" and call.args:
        mode = ""
        if len(call.args) > 1:
            mode = _hermes_const_value(call.args[1], consts) or ""
        for keyword in call.keywords:
            if keyword.arg == "mode":
                mode = _hermes_const_value(keyword.value, consts) or ""
        if not any(flag in mode for flag in "wax"):
            return None
        return _hermes_const_value(call.args[0], consts)
    if not isinstance(func, ast.Attribute):
        return None

    attr = func.attr
    if attr in ("write_text", "write_bytes"):
        base = func.value
        if (isinstance(base, ast.Call) and isinstance(base.func, ast.Name)
                and base.func.id == "Path" and base.args):
            return _hermes_const_value(base.args[0], consts)
        if isinstance(base, ast.Name):
            return consts.get(base.id)
        return None
    if attr in ("copy", "copy2", "copyfile", "move") and len(call.args) >= 2:
        return _hermes_const_value(call.args[1], consts)
    if attr in ("rename", "replace") and len(call.args) >= 2:
        return _hermes_const_value(call.args[1], consts)
    if attr in _HERMES_SAVER_ATTRS and call.args:
        return _hermes_const_value(call.args[0], consts)
    return None


def _hermes_execute_code_targets(code: str) -> list[str]:
    """Literal files an `execute_code` payload may write, in order, de-duplicated."""
    if not isinstance(code, str) or not code:
        return []
    seen: list[str] = []

    def add(path: str | None) -> None:
        if not path:
            return
        path = path.strip()
        if not path or path in seen or path.startswith(("http://", "https://")):
            return
        seen.append(path)

    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError):
        tree = None

    if tree is not None:
        consts = _hermes_const_map(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                add(_hermes_call_target(node, consts))
            # A file path can also arrive as a plain constant in a shell
            # string: `os.system("echo x > src/a.py")`.
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for match in _HERMES_PY_REDIRECT_RE.finditer(node.value):
                    path = (match.group("p") or "").strip()
                    if "/" in path or "." in path:
                        add(path)
        return seen

    # Unparseable snippet (a fragment, or a different language): fall back to
    # the shape scan so a redirect is still seen.
    for match in _HERMES_PY_REDIRECT_RE.finditer(code):
        path = (match.group("p") or "").strip()
        if "/" in path or "." in path:
            add(path)
    return seen


def _normalize_hermes_tool(raw_name, tool_input: dict) -> str:
    """Map one Hermes tool name onto the engine contract (or ""/passthrough)."""
    name = str(raw_name or "")
    low = name.lower()
    if low in _HERMES_WRITE_TOOLS:
        if not tool_input.get("path"):
            for _k in _HERMES_PATH_KEYS:
                if tool_input.get(_k):
                    tool_input["path"] = tool_input[_k]
                    break
        return _HERMES_WRITE_TOOLS[low]
    if low == "execute_code":
        code = str(tool_input.get("code") or tool_input.get("command") or "")
        targets = _hermes_execute_code_targets(code)
        if targets:
            # Gate it as a file write: every target must sit in an approved
            # Code Scope, exactly like a write_file on the same path.
            tool_input["paths"] = targets
            tool_input.setdefault("path", targets[0])
            tool_input["content"] = code
            return "file_editor"
        if _HERMES_PY_WRITE_SIGNAL_RE.search(code):
            return "execute_code"  # no literal target → catch-all warns, allows
        return ""  # pure computation: non-gated, no warning
    if low == "process_manage":
        # Feeding a PTY's stdin is a shell channel (`submit` types a command,
        # `write` sends raw bytes): both go through the terminal gate. The
        # read-only actions (list/poll/log/wait/kill) carry no write target.
        action = str(tool_input.get("action") or "").lower()
        if action in ("write", "submit"):
            payload_text = (tool_input.get("data") or tool_input.get("input")
                            or tool_input.get("text") or "")
            tool_input["command"] = str(payload_text)
            return "terminal"
        return ""
    if low in _HERMES_SHELL_TOOLS:
        if not tool_input.get("command"):
            for _k in ("cmd", "code"):
                if tool_input.get(_k):
                    tool_input["command"] = tool_input[_k]
                    break
        return _HERMES_SHELL_TOOLS[low]
    if low in _HERMES_READ_TOOLS:
        return ""  # non-gated: guard allows without a warning
    return name  # unknown → the catch-all below marks it "unknown"


# Patch payload path lines: `*** Add File: p`, `*** Update File: p`,
# `*** Delete File: p`, and the `*** Move to: p` half of an update/rename.
_OPENCODE_PATCH_PATH_RE = re.compile(
    r"(?m)^\*\*\*\s+(?:Add|Update|Delete)\s+File:\s*(.+?)\s*$")
_OPENCODE_PATCH_MOVE_RE = re.compile(r"(?m)^\*\*\*\s+Move\s+to:\s*(.+?)\s*$")


def _opencode_patch_paths(patch_text) -> list[str]:
    """Every file a `patch` payload touches, in order, de-duplicated.

    A multi-file patch must gate on ALL its targets: taking only the first would
    open the rest as an unapproved write — the exact class the guard exists to
    close. `Move to:` contributes the destination of a rename.
    """
    if not isinstance(patch_text, str) or not patch_text:
        return []
    seen: list[str] = []
    for m in (*_OPENCODE_PATCH_PATH_RE.finditer(patch_text),
              *_OPENCODE_PATCH_MOVE_RE.finditer(patch_text)):
        p = m.group(1).strip()
        if p and p not in seen:
            seen.append(p)
    return seen


def _normalize_opencode_tool(raw_name, tool_input: dict) -> str:
    """Map one OpenCode tool name onto the engine contract (or ""/passthrough).

    Returns the internal tool name: `file_editor`/`terminal` for gated tools,
    `""` for a known non-gated tool (guard allows, no warning), and the original
    name for anything else so the E-010 catch-all below marks it `unknown`.
    """
    name = str(raw_name or "").lower()
    if name in _OPENCODE_WRITE_TOOLS:
        if "path" not in tool_input:
            for _k in _OPENCODE_PATH_KEYS:
                if _k in tool_input and tool_input[_k]:
                    tool_input["path"] = tool_input[_k]
                    break
        if name in _OPENCODE_PATCH_TOOLS:
            paths = _opencode_patch_paths(
                tool_input.get("patchText") or tool_input.get("patch") or "")
            if paths:
                tool_input["paths"] = paths
                tool_input.setdefault("path", paths[0])
        return _OPENCODE_WRITE_TOOLS[name]
    if name in _OPENCODE_SHELL_TOOLS:
        if "command" not in tool_input and "cmd" in tool_input:
            tool_input["command"] = tool_input["cmd"]
        return _OPENCODE_SHELL_TOOLS[name]
    if name in _OPENCODE_READ_TOOLS:
        return ""  # non-gated: guard allows without a warning
    return str(raw_name)  # unknown → the catch-all below marks it "unknown"


def normalize_hook_input(json_in: dict) -> dict:
    """Normalize hook input from either Claude Code or OpenHands to a common schema.

    Claude Code sends: tool_name=Write|Edit|MultiEdit|Bash|PowerShell,
      tool_input={file_path,content,command,...}
    OpenHands sends: tool_name=file_editor|terminal,
      tool_input={path,content,command,...}
    OpenCode sends: tool_name=write|edit|patch|shell|read|glob|…,
      tool_input={path,content,command,patchText,...} — lower-case names (V2).
    Hermes sends: tool_name=write_file|patch|terminal|execute_code|read_file|…,
      tool_input={path,content,old_string,new_string,command,code,...} — one
      name per action, normalized through the runtime-pinned hermes branch.

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
    elif runtime == "hermes":
        # Runtime-pinned: see the Hermes vocabulary block above for why a
        # name heuristic cannot be used here (`patch` is shared with OpenCode).
        tool_name = _normalize_hermes_tool(tool_name, tool_input)
    elif runtime == "opencode" or tool_name in _OPENCODE_ALL_TOOLS:
        tool_name = _normalize_opencode_tool(tool_name, tool_input)
    elif tool_name in ("", None):
        pass  # no tool info (e.g. Stop/SessionStart payloads) — nothing to map
    else:
        tool_name = "unknown"

    # Canonicalize common field aliases for every runtime branch, including
    # already-normalized tool names. Otherwise `terminal` + `cmd` or
    # `file_editor` + `filePath` looks like an empty/no-op call to the guard.
    if tool_name == "terminal" and not str(tool_input.get("command") or "").strip():
        command_alias = tool_input.get("cmd")
        if command_alias is not None:
            tool_input["command"] = str(command_alias)
    elif tool_name in ("file_editor", "notebook_editor") and not str(
        tool_input.get("path") or ""
    ).strip():
        for path_key in ("file_path", "filePath"):
            path_alias = tool_input.get(path_key)
            if path_alias is not None and str(path_alias).strip():
                tool_input["path"] = str(path_alias)
                break

    # Infer common mutation payloads even for future tool names. If a single
    # call carries both shell and file-write signatures, a single normalized
    # tool cannot safely represent both; preserve that fact for guard().
    candidate_command = tool_input.get("command") or tool_input.get("cmd")
    path_keys = ("path", "file_path", "filePath")
    candidate_path = next(
        (str(tool_input.get(key) or "").strip() for key in path_keys
         if str(tool_input.get(key) or "").strip()),
        "",
    )
    candidate_paths = tool_input.get("paths")
    has_paths = isinstance(candidate_paths, list) and any(
        str(path).strip() for path in candidate_paths
    )
    mutation_keys = ("content", "new_string", "replacement", "patchText", "patch")
    has_mutation = any(
        key in tool_input and tool_input.get(key) is not None
        for key in mutation_keys
    )
    action = str(tool_input.get("action") or "").strip().lower()
    has_mutation = has_mutation or action in {
        "write", "create", "edit", "replace", "patch", "delete", "remove"
    }
    patch_text = tool_input.get("patchText") or tool_input.get("patch") or ""
    patch_paths = _opencode_patch_paths(patch_text)
    has_file_write = ((candidate_path or has_paths) and has_mutation) or bool(patch_paths)
    ambiguous_write_payload = bool(
        candidate_command and str(candidate_command).strip() and has_file_write
    )

    if tool_name not in ("file_editor", "terminal", "notebook_editor"):
        if candidate_command and str(candidate_command).strip():
            tool_name = "terminal"
            tool_input["command"] = str(candidate_command)
        elif has_file_write:
            tool_name = "file_editor"
            if candidate_path:
                tool_input.setdefault("path", candidate_path)
            if patch_paths:
                tool_input["paths"] = patch_paths
                tool_input.setdefault("path", patch_paths[0])

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

    normalized = {
        "tool_name": tool_name,
        "tool_input": tool_input,
        "raw_tool_name": raw_name,
        **{k: v for k, v in json_in.items() if k not in ("tool_name", "tool_input")},
    }
    if ambiguous_write_payload:
        normalized["ambiguous_write_payload"] = True
    return normalized


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


def _resolved_project_rel(p: str) -> str | None:
    """Project-relative path with every symlink resolved (E-036), or None.

    norm_path is lexical by design (E-006), so a free-zone path can hide a
    protected target behind a symlink (`scratch/link -> ../src`): the guard
    would release every write THROUGH the link into the protected tree with
    no approval. Resolution runs only on the release direction, against the
    project root repo_root() already derives (env → cwd); a resolution
    failure, a different drive, or a link that escapes the project root
    returns None — the caller then fails CLOSED (gated), never open.
    """
    try:
        base = os.path.realpath(repo_root({}))
        real = os.path.realpath(os.path.join(base, p))
        rel = os.path.relpath(real, base)
    except Exception:
        return None
    if rel.startswith("..") or os.path.isabs(rel):
        return None
    return norm_path(rel)


def _lexically_free(key: str) -> bool:
    """The pure string form of the free-zone decision (no filesystem touch).

    `key` is the norm_path'd, project-relative spelling already case-folded
    the way the filesystem folds (see _FREE_ZONE_FOLD).
    """
    if FREE_DOC_MD.match(key) or FREE_DOC_RAW.match(key):
        return True
    if key in INFRA_FILES_KEY:
        return True
    if key.startswith("explore_") and "/" not in key:
        # Root-level exploration files only: without the slash guard an
        # `explore_*` DIRECTORY releases its whole subtree
        # (explore_evil/payload.py) with no approval — a code-gate bypass.
        # Nested paths stay gated; scratch/ is the wide-open prototyping area.
        return True
    if any(key.startswith(prefix) for prefix in _FREE_PREFIXES_KEY):
        return True
    # Plugin source trees: free ONLY when the project is this repo itself
    # (self-modification). Otherwise they are protected by the experiment gate.
    if key in _PLUGIN_FREE_FILES_KEY:
        return _project_is_methodology_root()
    if any(key.startswith(prefix) for prefix in _PLUGIN_FREE_PREFIXES_KEY):
        return _project_is_methodology_root()
    return False


def is_free(path: str) -> bool:
    """True if the project-relative path is inside a free zone (no approval needed).

    Case rule (E-035): zone membership follows the filesystem's own case
    semantics. On a case-insensitive filesystem two spellings are the SAME
    file, so `SCRATCH/b.py` must be free exactly like `scratch/b.py` (the
    documented gapless free zone); on a case-sensitive one a differently-cased
    name is a different, ungoverned path, so `DOCS/e/raw/p.py` must stay gated
    — freeing it would release a directory the doctrine never listed. The
    fold happens only on os.name == "nt" and only for these membership tests;
    the normalized path itself keeps its original spelling.

    Symlink rule (E-036): a lexical free surface keeps the free verdict only
    when the FILESYSTEM-RESOLVED path is free too — `scratch/link -> ../src`
    must not release src/ through the link.
    """
    p = norm_path(path).lstrip("/")
    if not p:
        return True
    key = p.lower() if _FREE_ZONE_FOLD else p
    if not _lexically_free(key):
        return False
    # E-036 symlink closure: only a target that is itself free keeps the
    # free verdict. Only the RELEASE direction pays the resolution (two
    # realpath calls); a failed or root-escaping resolution is fail-closed.
    resolved = _resolved_project_rel(p)
    if resolved is None:
        return False
    rkey = resolved.lower() if _FREE_ZONE_FOLD else resolved
    if rkey == key:
        return True
    return _lexically_free(rkey)


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
    # A bare version token (0.1.0, v1.2.3, 3.11) carries version semantics,
    # never file semantics (E-037): without this the fail-closed tail below
    # reads its `.0` suffix as an unknown code extension and gates every
    # command that merely MENTIONS a version — a real session was denied
    # with "No approved experiment record for 0.1.0 … attempt #19" for an
    # inspection command. A name with a REAL extension keeps its verdict.
    if re.fullmatch(r"v?\d+(?:\.\d+)+", base):
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


def plugin_script_cmd(script: str, *args: str) -> str:
    """A runnable command for one of the PLUGIN's own scripts (E-029/E-033).

    The engine prints remedies into a TARGET project, which does not ship the
    plugin's ``scripts/``, ``bmad/scripts/`` or ``skills/`` trees — so a
    cwd-relative script path or a bare subcommand cannot be run where it is
    printed (the observed session ran the bare handoffs subcommand and got
    "command not found: handoffs"). Anchor every printed command at the
    engine's OWN plugin root; ``script`` is plugin-relative.
    """
    from .config import _METHODOLOGY_ROOT
    return "python3 " + " ".join([str(_METHODOLOGY_ROOT / script), *args])


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

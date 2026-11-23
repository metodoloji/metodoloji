"""Audit logic for PostToolUse hook.

Log-only (Faz 3): every invocation appends ONE redacted JSON line to the
audit trail and returns. No blackboard reads/writes, no canvas/bridge
mutation, no per-call board I/O — record-graph tracking belongs to the
skill-side CLI (blackboard.py), not to the per-write hot path.
"""

import json
import pathlib
import re
import sys
import time
from typing import Any

from .config import log_file
from .utils import (brief_text, counted_list, dod_issues, has_dod_content,
                    repo_root, scan_dod_items)


# Read-only call detection (2026-09-25, LIMX OpenHands session): the audit
# trail logged EVERY PostToolUse — including `ls`, `find`, `git status`,
# `wc -l` and file_editor `view` calls. In that session 423/652 tool blocks
# (65%) were hook allow echoes and the log filled with read-only noise the
# stop report then had to wade through. Read-only calls carry no
# methodology signal (guard already allows them; kopru checks need content),
# so audit() skips them: no log line, no I/O, immediate allow.
#
# The one exception is outcome, not input: a read-only command whose OUTPUT
# matches a known cross-platform failure signature IS logged (command_failure
# below). Noise is "ls succeeded"; a failure is the signal the trail exists
# to show — B-003's zsh nomatch abort and B-004's path collapse both died
# unrecorded behind this skip.
#
# Kept local (not imported from guard.py) so the audit hot path stays
# blackboard/gate-free per the Faz-1 lazy-import contract. The two patterns
# below mirror guard._is_git_commit / guard._DEPLOY_CMD_RE — commit/deploy
# commands are ALWAYS logged even when they resolve no file target.
_AUDIT_COMMIT_RE = re.compile(r"\bgit\b.*\bcommit\b")
# A kernel mutation writes workflow state, so it is never a "read-only" call —
# the fast exit would swallow the one turn the human channel exists to show
# (E-175). The verb must follow the script as a shell token, not sit in prose.
_AUDIT_WORKFLOW_RE = re.compile(
    r"workflow\.py\s+(?:complete|block|resume|flag|create)\b")
_AUDIT_DEPLOY_RE = re.compile(
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

# file_editor verbs that never mutate a file (OpenHands view/open/search).
_READONLY_EDITOR_COMMANDS = frozenset({"view", "open", "search", "list", "show"})
# file_editor verbs that DO mutate the file. A write verb is a write even when
# the body is empty (a create/truncate still changes the tree).
_WRITE_EDITOR_COMMANDS = frozenset({
    "create", "write", "write_file", "edit", "replace", "str_replace",
    "insert", "append", "undo_edit",
})
# Keys an edit carries its body under. `content` is only the whole-file shape:
# Claude Code's Edit normalizes to old_string/new_string, OpenHands uses
# old_str/new_str on str_replace/insert, a notebook edit uses new_str. Deciding
# "write" from `content` alone read every one of them as a read-only call, so
# incremental edits never reached the trail (2026-10-03, E-064).
_WRITE_PAYLOAD_KEYS = (
    "content", "new_str", "new_string", "old_str", "old_string",
    "insert_line", "edits", "diff", "patch",
)


def _has_write_payload(tool_input: dict) -> bool:
    """True when an edit body is present, under any of the write-payload keys.

    `content` keeps its historical rule (non-empty to count); the edit keys
    count on presence, since an edit always carries a replacement body.
    """
    for key in _WRITE_PAYLOAD_KEYS:
        if key not in tool_input:
            continue
        value = tool_input[key]
        if key == "content":
            if value is not None and str(value).strip():
                return True
            continue
        if value not in (None, "", [], {}):
            return True
    return False


# --- cross-platform command-failure signatures ------------------------------
#
# The agent sees its own shell error; the TRAIL is what lets a later session
# count failures per class — and every output_summary in the shipped trail is
# null (the audit read `tool_output`, a key no harness sends; Claude Code's
# PostToolUse payload carries the outcome as `tool_response`), so no command
# failure, on either OS, was ever recorded. Each signature below is a failure
# class a research record MEASURED, paired with the one bounded correction
# that fixes it; unknown output yields no signature and no hint (silence
# beats a wrong hint, and one hint per failing call keeps the edge bounded):
#   B-002 S1  `$M` set in an earlier call expands empty → command not found
#   B-003     zsh (macOS) cancels the WHOLE command on an unmatched glob
#   B-004 S2  a native `C:\…` path collapses inside a POSIX shell
#   B-004 S1  PowerShell is a different vocabulary (no grep/sed; PS 5.1 no `&&`)
#   macOS     GNU-style `sed -i 's/…/'` under BSD sed → invalid command code
# The signature below is a SHELL diagnostic, not the words "no matches".
# E-177: the pre-fix pattern matched the harness's own label
# (`returnCodeInterpretation: "No matches found"`) on a grep that simply
# found nothing, so the agent was told a glob abort had happened.
_COMMAND_FAILURES = (
    ("glob_nomatch",
     re.compile(r"(?im)(?:^|[\n;])\s*(?:zsh|bash|dash|sh|fish):\s*no match(?:es)?\b"),
     "Command aborted by an unmatched glob — zsh (macOS) cancels the whole "
     "line when a pattern matches nothing: confirm the target exists before "
     "a `path/*` command, and never let a glob decide whether the rest runs."),
    # E-181: the FILESYSTEM TOOL prints this, with its name at line start. A
    # bare phrase matched any file naming it — the module's own tests and the
    # shipped bench both did, so reading them bought a forward-slash correction.
    # Only the COLLAPSED drive form is a backslash problem: `C:/Users/x` reached
    # the tool intact, and `'notes:draft.md'` is a filename, not a drive.
    ("backslash_path",
     re.compile(r"(?im)^\s*(?:ls|cat|cp|mv|rm|stat|file|find|cd|dir|type|test|"
                r"touch|mkdir|chmod)\s*:\s*cannot access\s+['\"]?[a-z]:[^/\\]"),
     "A native backslash path collapsed inside the POSIX shell — rewrite it "
     "with forward slashes (`C:/Users/...` works in Bash, PowerShell and "
     "Python alike)."),
    # E-180: these are PROGRAM diagnostics, not phrases. This module's own
    # failure table names them, so reading the table must stay silent. PowerShell
    # quotes the term it could not resolve and names the error id; cmd.exe quotes
    # the command it could not find; the token message quotes the token.
    ("powershell_dialect",
     re.compile(r"(?im)(?:^\s*\S+\s*:\s*The term ['\"][^'\"\n]+['\"] is not "
                r"recognized as the name of a cmdlet"
                r"|\bFullyQualifiedErrorId\s*:\s*CommandNotFoundException\b"
                r"|['\"][^'\"\n]+['\"] is not recognized as an internal or "
                r"external command"
                r"|\bThe token ['\"][^'\"\n]+['\"] is not a valid statement "
                r"separator\b)"),
     "Shell dialect mismatch — PowerShell has no grep/sed/ls flags and PS 5.1 "
     "has no `&&`: run POSIX pipelines in the Bash tool or use the cmdlet "
     "equivalents (Bash = POSIX sh; PowerShell = PowerShell)."),
    # E-180: `sed` prefixes its own diagnostic (`sed: 1: "file": invalid command
    # code f`). The bare phrase also appears in this module's table and in docs.
    ("bsd_sed",
     re.compile(r"(?im)^\s*sed:\s*[^\n]{0,80}?\binvalid command code\b"),
     "GNU-style `sed -i 's/…/'` fails under BSD sed (macOS) — use "
     "`sed -i.bak 's/…/' file`, which works on both, or `sed -i '' …` on "
     "macOS only."),
    # E-179: the phrase alone is not a diagnostic — this module's own source
    # and any `grep -rn "command not found"` contain it. A shell names the
    # word that failed and then says the phrase on the SAME line:
    #   bash: line 1: rg: command not found      zsh: command not found: rg
    #   sh: 1: foo: command not found            /bin/bash: line 7: x: ...
    # fish uses different words (`fish: Unknown command: rg`) and is left
    # silent rather than guessed at.
    ("command_not_found",
     re.compile(r"(?im)^\s*(?:\S*/)?(?:zsh|bash|dash|sh|fish|ksh)\s*:\s*"
                r"(?:(?:line\s+)?\d+\s*:\s*)?(?:[^\n:]{1,60}:\s*)?"
                r"command not found\b"),
     "The command never resolved — check the tool exists in THIS shell and "
     "that nothing relies on a shell variable from an earlier tool call "
     "(every call is a fresh shell: run commands self-contained with literal "
     "paths)."),
)


# `tool_response` is a MAPPING in every harness that sends one, and the mapping
# is not always flat: Claude Code carries `stdout`, `stderr`, `interrupted` and
# `returnCodeInterpretation` at the top level, while another adapter wraps the
# same thing one level down (`{'output': {'exit': 0, 'output': '...'}}`,
# observed in this checkout's trail 2026-11-17). Only output-bearing values are
# process output; an interpretation of the exit code is the harness's summary
# of the CALL. Stringifying the envelope let that label be scanned as command
# text (E-177), and a flat extractor then read `status: ok` while dropping the
# nested command-not-found (E-178).
_OUTPUT_FIELDS = frozenset({
    "stdout", "stderr", "output", "content", "text", "result", "log", "logs",
})
_NON_OUTPUT_FIELDS = frozenset({
    "returncodeinterpretation", "returncode", "exitcode", "exit", "code",
    "status", "success", "ok", "interrupted", "isimage",
    "nooutputexpected", "truncated", "duration", "elapsed", "cwd", "command",
    "metadata",
})
# Past this depth the envelope stops yielding output. A harness nesting deeper
# than this gets silence, never a guessed failure class (one bounded walk).
_MAX_OUTPUT_DEPTH = 4


def _collect_output(value, depth: int = 0) -> list[str]:
    """Text values that are command output, walking a harness envelope."""
    if isinstance(value, str):
        return [value] if value else []
    if depth >= _MAX_OUTPUT_DEPTH:
        return []
    if isinstance(value, dict):
        keys = [key for key in value if str(key).lower() in _OUTPUT_FIELDS]
        if not keys:
            # No named output field at this level: descend only into nested
            # containers. A bare string under an unknown key (a status word)
            # is not output, and must not be read as it.
            keys = [key for key, item in value.items()
                    if str(key).lower() not in _NON_OUTPUT_FIELDS
                    and isinstance(item, (dict, list, tuple))]
        parts: list[str] = []
        for key in keys:
            parts.extend(_collect_output(value[key], depth + 1))
        return parts
    if isinstance(value, (list, tuple)):
        parts = []
        for item in value:
            parts.extend(_collect_output(item, depth + 1))
        return parts
    return []


def command_output_text(tool_output) -> str:
    """The command's own output from a harness envelope, never its metadata.

    Walks nested mappings and lists, reads only output-bearing fields (or the
    containers they live in), and stops at a bounded depth. Metadata fields and
    scalars (`status`, `exit`, flags) never contribute.
    """
    return "\n".join(_collect_output(tool_output))


# The one view of an output that BOTH the classifier and the trail use (E-182).
# Every signature is anchored to a line start, so a repr (escaped `\n`) has no
# anchor and a head-only cut loses a tail traceback: either one left a stored
# row unable to reproduce the class it recorded.
_OUTPUT_WINDOW = 4000


def _output_window(text: str, limit: int = _OUTPUT_WINDOW) -> str:
    """Head+tail of `text`, so a diagnostic at either end survives the cut."""
    if len(text) <= limit:
        return text
    half = limit // 2
    return (text[:half] + f"\n…[{len(text) - limit} chars omitted]…\n"
            + text[-half:])


def command_failure(tool_name: str, tool_output) -> tuple[str, str] | None:
    """(kind, bounded correction) when a terminal output shows a known
    cross-platform failure; None otherwise.

    Only terminal output is inspected, only against the fixed table above.
    The match window keeps the hot path off multi-MB outputs: these failures
    print at the head (nomatch, command-not-found) or the tail (tracebacks).
    """
    if tool_name != "terminal" or not tool_output:
        return None
    s = command_output_text(tool_output)
    if not s:
        return None
    hay = _output_window(s)
    for kind, rx, hint in _COMMAND_FAILURES:
        if rx.search(hay):
            return kind, hint
    return None


def is_readonly_call(
    tool_name: str, tool_input: dict, *, powershell: bool = False
) -> bool:
    """True when the call cannot mutate project state (safe to skip logging)."""
    if tool_name == "terminal":
        cmd = str((tool_input or {}).get("command", "") or "")
        if not cmd.strip():
            return True
        if (_AUDIT_COMMIT_RE.search(cmd) or _AUDIT_DEPLOY_RE.search(cmd)
                or _AUDIT_WORKFLOW_RE.search(cmd)):
            return False
        try:
            from .bash_targets import extract_bash_targets_ex
            targets, dropped = extract_bash_targets_ex(cmd, powershell=powershell)
            # A dropped destination is still a write (the guard treats it as
            # one and warns); skip only when there is no static target AND
            # nothing was dropped, so the guard's blind spot is recorded in
            # the trail instead of vanishing from it.
            return not targets and not dropped
        except Exception:
            return False
    if tool_name in ("file_editor", "notebook_editor"):
        ti = tool_input or {}
        verb = str(ti.get("command", "") or "").strip().lower()
        if verb in _READONLY_EDITOR_COMMANDS:
            return True
        if verb in _WRITE_EDITOR_COMMANDS:
            return False
        # No/unknown verb: a write iff an edit payload is present.
        return not _has_write_payload(ti)
    return False


# The fixed first sentence every session-start inject carries. Exported so the
# tests pin the contract in one place instead of re-typing it (and so a change
# here is visibly a contract change, not an incidental string edit).
#
# The path-form clause exists because the guard's target detection and every
# shell probe share one dialect problem: on Windows a native `C:\Users\…` path
# collapses inside a POSIX shell (`ls C:\a\b` reads as `ls C:ab`), which a real
# win32 Claude Code session hit repeatedly. Forward slashes are the one path
# form Bash, PowerShell and Python all accept.
#
# The second clause is shell-state discipline (2026-10-01 graph-engineering-arge
# session): the agent set `M=<metodoloji-root>` in one tool call and used `$M`
# in later ones — each Bash/hook call is a fresh shell, so the variable was gone
# and the command ran as an empty expansion (the agent hit it twice in one
# session and called it "yine $M sorunu"). The session edge already prints the
# literal roots, so it is where the rule belongs: one bounded sentence, never a
# filesystem hunt.
#
# The glob clause is the third measured dialect abort (B-003: an unmatched
# glob cancelled the whole zsh command in a real session; the steps that warn
# about it are documentation only). The session edge is where the other two
# rules already live, so the third joins them.
FIXED_SESSION_CONTEXT = (
    "METODOLOJI session started. Record chain: E → IR → SP → S → QR → PR. "
    "Run every command self-contained — a shell variable set in one tool "
    "call does not exist in the next (`M=...` then `$M` expands empty); "
    "use the literal {metodoloji-root}/{project-root} roots named in this "
    "context. Write every path with forward slashes on every OS — "
    "`C:/Users/...` works in Bash, PowerShell and Python, while a `C:\\...` "
    "path collapses inside a POSIX shell. Match the dialect to the shell tool "
    "you call (Bash = POSIX sh; PowerShell = PowerShell). Guard globs — an "
    "unmatched pattern cancels the whole command in zsh (macOS), so confirm "
    "the target exists before running a `path/*` command."
)

# The configured languages, on the session edge (E-173). `communication_language`
# and `document_output_language` were reachable only through orient.py, which
# runs when a SKILL loads — a plain conversation runs no skill, so a project
# configured Turkish answered in English until the first skill opened
# (2026-11-16 session: "the config has no effect"). Every runtime injects THIS
# handler's context (bootstrap.sh, Hermes session_context, OpenCode), so the
# sentence belongs here, once.
#
# Both values are PROJECT data that lands in a prompt: a language name is a few
# words, so anything longer or carrying a newline is not one and is dropped —
# docs/config.toml must not be able to add a context line.
_LANGUAGE_VALUE_MAX = 40
_LANGUAGE_VALUE_WORDS = 3
_LANGUAGE_VALUE_RE = re.compile(r"[\w ()-]+", re.UNICODE)
def _core_config(root: str) -> dict:
    """The project's resolved `core` table, through the ONE merge.

    resolve_config.py owns the layer order (plugin team/user < project
    docs/team/user). Reading a config file here directly would be a second
    opinion about what the user configured — the defect E-146/E-147 closed.
    Loaded lazily: the audit hot path never pays for this import.
    """
    import importlib.util
    path = (pathlib.Path(__file__).resolve().parents[2]
            / "scripts" / "resolve_config.py")  # <root>/hooks/scripts/
    spec = importlib.util.spec_from_file_location("audit_resolve_config", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return (module.resolve(root) or {}).get("core") or {}

def _language_sentence(root: str) -> str:
    """`Language: answer the user in X; write every document in Y.` or "".

    Fail-open: a config that cannot be read leaves the fixed sentence alone,
    exactly as an absent one does. Bounded: see the caps above.
    """
    try:
        core = _core_config(root)
    except Exception:
        return ""
    clauses = []
    for label, key in (("answer the user in", "communication_language"),
                       ("write every document in", "document_output_language")):
        value = " ".join(str(core.get(key) or "").split())
        if (value and len(value) <= _LANGUAGE_VALUE_MAX
                and len(value.split()) <= _LANGUAGE_VALUE_WORDS
                and _LANGUAGE_VALUE_RE.fullmatch(value)):
            clauses.append(f"{label} {value}")
    return "Language: " + "; ".join(clauses) + "." if clauses else ""

# Preview caps for the audit trail: bodies never land whole in the log.
_INPUT_PREVIEW_LEN = 300
_OUTPUT_PREVIEW_LEN = 500
# Keys whose values are file/command bodies, not metadata — preview only.
# The edit-body keys (new_str/old_str/…) MUST be here or the very content the
# module promises never to log whole lands in the trail at full length.
_BODY_KEYS = frozenset({
    "content", "code", "source", "text", "body", "output",
    "old_str", "new_str", "old_string", "new_string", "diff", "patch",
    "edits",
})


def _redacted_input(tool_input: dict) -> dict:
    """Copy tool input with body values reduced to preview + length.

    Paths, commands and flags stay whole (stop/guard need them); only
    potentially large or sensitive bodies are cut.
    """
    redacted: dict[str, Any] = {}
    for key, value in tool_input.items():
        if key in _BODY_KEYS and isinstance(value, str) and len(value) > _INPUT_PREVIEW_LEN:
            redacted[key] = value[:_INPUT_PREVIEW_LEN] + f"... [truncated {len(value)} chars]"
        elif key in _BODY_KEYS and isinstance(value, list):
            joined = "\n".join(str(v) for v in value)
            if len(joined) > _INPUT_PREVIEW_LEN:
                redacted[key] = joined[:_INPUT_PREVIEW_LEN] + f"... [truncated {len(joined)} chars]"
            else:
                redacted[key] = value
        else:
            redacted[key] = value
    return redacted


def _check_kopru_consumption(tool_name: str, tool_input: dict,
                             root: str | None = None) -> list[str]:
    """Check if bridge outputs exist for recently modified files.

    Scoped to the modified file only (never a directory scan): a QR edit
    without structurally sound DoD items warns; the done-story→QR chain check
    lives in check-plugin.sh (static audit), not on the per-write hot path.
    QR DoD content is validated with the SAME rules and parser the guard
    applies to a story's Definition of Done (.utils.dod_issues) — identifier
    on every item plus a recorded verification — so the two layers can never
    disagree about what "valid DoD" means.

    Neither path shape nor payload shape may skip the check: a native
    Windows path (`C:\\…\\QR-001.md`) matches like a forward-slash one, and
    an incremental edit that carries no `content` body (Claude Code Edit
    old_string/new_string, OpenHands str_replace — the payload diversity
    E-064 documented for writes) is validated against the file on disk,
    which PostToolUse already reflects. Fail-open: an unreadable file
    returns no warning.
    """
    warnings: list[str] = []

    if tool_name == "file_editor":
        path = tool_input.get("path", "")
        if not path:
            return warnings

        # Check: QR-NNN.md modified → should carry DoD verification items.
        # Separator-agnostic (the pinned backslash BUG: the slash-anchored
        # regex never matched a native Windows path, so the whole check was
        # silently skipped on that platform).
        normed = path.replace("\\", "/")
        if re.search(r"/QR-\d+\.md$", normed, re.IGNORECASE):
            content = str(tool_input.get("content", "") or "")
            if not content:
                # No whole-file body in the payload → the tool already ran
                # (PostToolUse), so the file on disk IS the current content.
                # Fail-open: a missing/unreadable file never turns this hot-
                # path check into noise (the old silent skip stays for it).
                try:
                    p = pathlib.Path(normed)
                    if not p.is_absolute():
                        p = pathlib.Path(root or repo_root({})) / p
                    content = p.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    return warnings
            if not has_dod_content(content):
                warnings.append(
                    f"Bridge inconsistency: {path} does not contain DoD items. "
                    f"The QR record may be missing or incorrectly created."
                )
                return warnings
            # DoD content is referenced but no bullet/table item parsed → the
            # QR only repeats the words, it does not record the verification.
            if not scan_dod_items(content, qr=True):
                warnings.append(
                    f"Bridge inconsistency: {path} mentions DoD but has no DoD items "
                    f"(expected '- DoD-NNN …' bullets or a '| DoD-NNN | … |' table)."
                )
            # Structural defects — the guard's DoD rules applied to QR content.
            for issue in dod_issues(content, qr=True):
                warnings.append(f"Bridge inconsistency: {path} — {issue}")

    return warnings


def _workflow_peek(root: str) -> str:
    """One read-only sentence about unfinished workflow runs (fail-open → "").

    The kernel lives in the plugin tree next to hooks/, not in a module of this
    engine — the engine deliberately owns no workflow logic, so this imports the
    kernel lazily (path derived from __file__, never guessed) and returns ""
    whenever the tree or the state is absent. Fail-open is the contract: a
    session must open even if the kernel cannot be reached.
    """
    try:
        plugin_root = str(pathlib.Path(__file__).resolve().parents[3])
        if plugin_root not in sys.path:
            sys.path.insert(0, plugin_root)
        from hooks.workflow import advisory
        return advisory.peek(root)
    except Exception:
        return ""


def _workflow_notice(root: str, kind: str) -> str:
    """One user-facing workflow line ("session" | "turn" | "stop"), or "".

    `_workflow_peek` feeds the MODEL; this feeds the HUMAN through the
    harness's `systemMessage`, which is the only channel the person at the
    keyboard reads (E-175). Same lazy kernel import, same fail-open contract.
    """
    try:
        plugin_root = str(pathlib.Path(__file__).resolve().parents[3])
        if plugin_root not in sys.path:
            sys.path.insert(0, plugin_root)
        from hooks.workflow import notice
        return getattr(notice, f"{kind}_line")(root)
    except Exception:
        return ""


# Proactive workflow offer: the states where implementation is already in
# flight, so a story with no declared run is work the kernel could be carrying.
# `review` is included because the chain's later stages (QR/PR) are still ahead.
_OFFER_STATUSES = ("in-progress", "review")
_OFFER_LIMIT = 2


def _workflow_offer(root: str) -> str:
    """One bounded sentence offering a run for an in-flight story (E-186).

    The kernel only ever reported runs that already existed, so a story being
    implemented without one was silent: creation depended on the agent
    remembering `create`, and nothing in the project said which run it meant.
    The session edge now names the stories whose own record says work is under
    way and whose id no run claims, plus the ONE command that derives the run
    (slug + Rigor Tier) from that record. Read-only and fail-open: no state is
    written, no run is created, and any unreadable input yields "".
    """
    try:
        from hooks.workflow import store
        from . import state as _state
        from .config import story_status
        from .story_quality import _iter_story_files
    except Exception:
        return ""
    try:
        runs = {str(s).lower() for s in store.list_slugs(root)}
    except Exception:
        return ""
    rows: list[tuple[str, str]] = []
    try:
        for path in _iter_story_files(root):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            status = story_status(text)
            if status not in _OFFER_STATUSES:
                continue
            sid = _state.record_id_of_stem(path.stem)
            if not sid or sid.lower() in runs:
                continue
            rows.append((sid, status))
            # E-213: the scan used to stop one row past the bound, so the tail
            # could only ever say `(+1 more)` — five in-flight stories with no
            # run read as three, and the marker named no unit. Count the whole
            # set (the files are read anyway) and let counted_list state it.
    except Exception:
        return ""
    if not rows:
        return ""
    from .utils import plugin_script_cmd
    shown = counted_list([f"{sid} ({status})" for sid, status in rows],
                         _OFFER_LIMIT, noun="story")
    commands = "; ".join(
        f"`{plugin_script_cmd('hooks/scripts/workflow.py', 'adopt', '--story', sid)}`"
        for sid, _status in rows[:_OFFER_LIMIT])
    return (f"PROACTIVE — no declared workflow run for {shown}: "
            f"the story record already declares the run, so open it with "
            f"{commands}.")


def _scope_debt_is_paid(root: str) -> bool:
    """True only when the commit gate's own coverage check RUNS and finds no
    edited file outside an approved scope.

    The "scope" channel holds an observation from commit time, and the session
    edge used to replay it as current debt forever. The answer is computed by
    the SAME check the commit gate uses (never a second matcher): when it
    answers and finds nothing, the debt it described no longer exists. A check
    that could not run (no git, no gate companion, timeout) is NOT clean — an
    unverifiable tree cannot pay a debt (E-037's rule, one channel over).
    """
    try:
        from . import guard
        misses, ran = guard._check_scope_coverage(root)
    except Exception:
        return False
    return bool(ran) and not misses


# A kernel mutation is the only turn worth announcing: reads (`status`, `next`,
# `list`, `history`) and enquiries must not spam the human channel (E-175).
_WORKFLOW_MUTATION_RE = re.compile(
    r"workflow\.py\s+(?:complete|block|resume|flag|create)\b")


def session_start(json_in: dict) -> dict:
    """SessionStart: stamp a session_start marker into the audit trail.

    Marker + relay announcement (Faz 5): stop counts touched files only
    after the newest marker, so previous sessions' leftovers never wedge a
    new session. The context stays one fixed sentence, plus a read-only peek
    at the handoff channels (pending_handoff_channels): waiting relay signals
    surface as PROACTIVE nudges — announce-only, never consumed; the
    addressed skill completes the shake. When methodology run keys exist,
    the chain progress (E→IR→SP→S→QR→PR values from compact_context) is
    appended read-only so the session grounds in live state. Fail-open (never
    blocks startup) and blackboard-mutation-free: a missing or corrupt board
    leaves just the fixed sentence. Returns additionalContext so SessionStart
    can inject it.
    """
    from .utils import repo_root, plugin_script_cmd as _plugin_cmd
    root = repo_root(json_in)
    try:
        from .stop import record_session_start
        record_session_start(root)
    except Exception:
        pass

    context = FIXED_SESSION_CONTEXT
    # The configured languages ride the same edge (E-173): the sentence is
    # derived from the resolved config, so a project that says Turkish is
    # answered in Turkish from the first turn, before any skill loads.
    language = _language_sentence(root)
    if language:
        context += " " + language
    # Project state, one bounded sentence (state.py — the same source orient.py
    # reports from): the mailjs session (2026-09-23, OpenHands) opened with zero
    # progress signal, grounded in the model's memory instead of the board
    # ("Epic 6 in-progress" from last week), and spent its first minutes
    # re-deriving state the record tree + sprint-status already held. Fail-open:
    # an empty project contributes nothing here.
    try:
        from . import state as _state
        inv = _state.record_inventory(root)
        if inv:
            inv_s = ", ".join(f"{kind}={info['count']} (newest {info['newest']})"
                              for kind, info in sorted(inv.items()))
            context += f" Records: {inv_s}."
        sprint_line = _state.format_sprint_line(_state.sprint_summary(root))
        if sprint_line:
            context += f" {sprint_line}."
        # Tool steering at the session edge (2026-09-24): the digest names the
        # reachable MCP servers on every run opener; the inject carries the same
        # one bounded line so even a session that never runs a digest-first
        # opener knows which servers exist. Empty when none configured.
        mcp_line = _state.mcp_steering_line(_state.mcp_inventory(root))
        if mcp_line:
            context += f" {mcp_line}."
    except Exception:
        pass
    # Open Implementation Plans (E-068): an operation that spans sessions must
    # not start blind. The session edge injects the same derived progress the
    # Stop report names (one shared formatter — plan.open_plans_text), so a
    # resumed multi-file run resumes its declared set instead of re-deriving it
    # from chat. Read-only (record files only, mtime+size cached), capped, and
    # silent when nothing is pending or the records dir is absent. Fail-open.
    try:
        from . import plan as _plan
        opens = _plan.open_plans_text(
            str(pathlib.Path(root) / "docs" / "experiments"), root)
        if opens:
            context += (f" Plan progress (GRP): {opens} — extend an operation's "
                        f"plan with "
                        f"{_plugin_cmd('skills/bmad-research-experiment/scripts/run_experiment.py', '--amend-plan')}.")
    except Exception:
        pass
    # Both peeks below are read-only (never consume, never mutate) and
    # fail-open: a missing or corrupt board leaves just the fixed sentence.
    try:
        from . import blackboard as bb
        from . import freshness as fr
        # `[freshness] unknown_policy = "silent"` counts UNKNOWNs without
        # announcing them: a legacy corpus with no witnesses would otherwise
        # repeat "unverifiable" on every surface of every session. STALE is
        # never silenced — that is the defect itself.
        announce_unknown = fr.unknown_policy() != "silent"
        waiting = bb.pending_handoff_channels(root)
        if waiting:
            # The count is the routing seam; the DERIVED verdict is what makes
            # the nudge honest (E-053). A signal the code moved past is no
            # longer "nobody picked up the baton" — it may be work that no
            # longer applies, and the sentence has to say which it is instead
            # of repeating yesterday's decision in the present tense (F-01/F-02).
            try:
                detail = bb.pending_handoff_freshness(root)
            except Exception:
                detail = {}
            parts = []
            for s, n in waiting.items():
                info = detail.get(s) or {}
                stale = list(info.get("stale_keys") or [])
                unknown = list(info.get("unknown_keys") or []) if announce_unknown else []
                peek_cmd = _plugin_cmd('hooks/scripts/blackboard.py', 'handoffs', '--skill', s)
                consume_cmd = _plugin_cmd('hooks/scripts/blackboard.py', 'consume',
                                          '--channel', 'handoff.' + s)
                if stale:
                    # Only a STALE verdict rewrites the sentence: the code has
                    # moved past these claims, so announcing them as "work
                    # nobody picked up" is the exact defect E-052 measured.
                    labels = {str(item.get("key")): str(item.get("label"))
                              for item in (info.get("freshness") or [])}
                    # E-196: the illustrate list was sliced at five with no
                    # marker, so a channel with eight dated claims named five
                    # and read as complete. Bound it once, count the rest.
                    shown = counted_list(
                        [f"{k} ({labels.get(k, fr.STALE)})" for k in stale + unknown],
                        5, noun="signal", sep=" ")
                    unknown_count = len(info.get("unknown_keys") or [])
                    parts.append(
                        f"{s} ({n}: {info.get('current', 0)} current, "
                        f"{len(stale)} stale, {unknown_count} unknown) — NO LONGER "
                        f"VALID WORK: {shown}; verify with `{peek_cmd}`, then "
                        f"retire it "
                        f"(`{_plugin_cmd('hooks/scripts/blackboard.py', 'handoff-retire', '--retire', '<key>', '--reason', '<why>')}`) "
                        f"or consume the channel with `{consume_cmd}`")
                    continue
                legacy = (
                    f"{s} ({n}): {n} unclaimed signal(s) from completed "
                    f"upstream runs — a run finished its work but nobody "
                    f"picked up the baton; peek `{peek_cmd}`, then consume its "
                    f"handoff channel with `{consume_cmd}`")
                if unknown:
                    # UNKNOWN is not stale: the claim simply cannot be dated
                    # here (no record, no git). It stays delivered as work and
                    # is FLAGGED, so a session knows the anchor is missing
                    # instead of mistaking silence for verification.
                    # E-196: the same slice said "4 of them" and named three
                    # (live inject, 2026-11-22). Count the remainder instead.
                    legacy += (f" NOTE: {len(unknown)} of them carry no "
                               f"code-state anchor (UNKNOWN: "
                               f"{counted_list(unknown, 3, noun='key')}) "
                               f"— verify before obeying ("
                               f"`{_plugin_cmd('hooks/scripts/blackboard.py', 'witness', '--target', '<key>')}`)")
                parts.append(legacy)
            context += f" PROACTIVE — hand-off waiting: {'; '.join(parts)}."
        # Chain progress: when methodology run keys exist on the board,
        # surface how far the relay progressed so the session grounds in
        # live state instead of a static chain reminder. Empty board →
        # nothing appended (keeps the fixed sentence for fresh projects).
        # Each stage carries its DERIVED freshness label, so a relay position
        # whose scope moved on reads as such (F-03: the most durable surface
        # used to be the least checked one).
        compact = bb.compact_context(root)
        progress = compact.get("methodology", {})
        if progress:
            verdicts = compact.get("methodology_freshness", {}) or {}
            chain_s = "; ".join(
                f"{stage}: {brief_text(val, 60)}"
                + (f" [{verdicts[stage]['label']}]"
                   if verdicts.get(stage, {}).get("verdict") not in (None, fr.CURRENT)
                   and (verdicts[stage]['verdict'] != fr.UNKNOWN
                        or announce_unknown)
                   else "")
                for stage, val in progress.items())
            # An elided value already ends in its own marker; a second period
            # would read as `… .` and make the bound look like a typo.
            context += (f" Chain progress — {chain_s}"
                        + ("" if chain_s.endswith("…") else "."))
        # Graph peek (blackboard full-integration P1): the hot run's
        # neighborhood grounds a resumed session in what the run is wired
        # to (decision map, upstream spec, downstream consumer). Read-only,
        # bounded to 5, fail-open — a hot-less or link-less board adds
        # nothing. `neighbors` already rides `compact_context`; this only
        # renders it into the injected sentence.
        try:
            _hot = bb.read_board(root).get("hot")
            if _hot:
                _nbs = bb.neighbors(root, _hot)[:5]
                if _nbs:
                    _nb_s = ", ".join(
                        f"{n.get('node')} ({n.get('relation')},{n.get('direction')})"
                        for n in _nbs)
                    context += f" Graph — {_hot} ↔ [{_nb_s}]."
        except Exception:
            pass
        # Workflow-kernel peek (2026-10-02): the run's position is COMPUTED
        # state, not memory — a resumed session continues the declared workflow
        # instead of re-deriving the plan from chat, and the sentence names
        # where the kernel will go next so ordering is never re-negotiated.
        # Read-only, bounded, and silent when nothing is running.
        wf_line = _workflow_peek(root)
        if wf_line:
            context += f" {wf_line}"
        else:
            # E-186: no unfinished run is exactly when creation is the missing
            # step, so the offer rides the same edge instead of a second hook.
            offer = _workflow_offer(root)
            if offer:
                context += f" {offer}"
        # Scope-debt peek (2026-09-25): the commit-time mirror posts uncovered
        # files to the "scope" channel; a later session should still see the
        # debt even though the commit warning has scrolled away. Read-only —
        # consuming here would silence the debt for everyone else; only the
        # remediation (E record approval / next commit without misses, which
        # replaces the alert) may clear it.
        scope_alerts = bb.pending_alerts(root, "scope")
        if scope_alerts:
            # E-199: this was the last raw `[:160]` on a routed alert. Live on
            # this tree, 2026-11-22, a missed-scope commit injected
            # `… blackboard_diag.` — cut mid-path and with no marker, so the
            # reader could not tell the sentence had been shortened (E-192
            # fixed the same slice on the stop edge; this edge kept it).
            first = brief_text(str(scope_alerts[0].get("text", "")), 160)
            more = f" (+{len(scope_alerts) - 1} older)" if len(scope_alerts) > 1 else ""
            if _scope_debt_is_paid(root):
                # The persisted claim is replayed in the present tense forever;
                # this tree kept telling every session to open an E record for
                # `uv.lock` after the file had gone back to matching HEAD
                # (observed 2026-11-20). Label it, never consume it — the
                # session edge is announce-only.
                context += (f" SCOPE DEBT [STALE — no edited file sits outside "
                            f"an approved scope now; clear with "
                            f"`{_plugin_cmd('hooks/scripts/blackboard.py', 'consume', '--channel', 'scope')}`]"
                            f" — {first}{more}.")
            else:
                context += f" SCOPE DEBT — {first}{more}."
        # Subscription-plane delivery announcement (the engine's contract: "session
        # → injected at session start"). The engine invariants bar the session
        # edge from consuming (announce-only: never consume, never mutate), so
        # the edge surfaces the routed alerts and the addressed watcher clears
        # them by hand with `consume --channel session` — deliver-once stays
        # with the consumer, exactly like the handoff handshake. Silent when
        # nothing was ever routed (fresh boards contribute nothing).
        session_alerts = bb.pending_alerts(root, "session")
        if session_alerts:
            first = brief_text(str(session_alerts[0].get("text", "")), 160)
            # E-223: the session-channel alert list surfaced only the first
            # and a bare `(+N more routed)`, so five routed alerts read as one
            # and the reader could not tell withheld alerts from withheld paths.
            # Name the unit (alert) and the true count.
            _n = len(session_alerts) - 1
            more = (f" (+{_n} more routed alert{'s' if _n != 1 else ''})"
                    if _n > 0 else "")
            context += (f" Blackboard — {len(session_alerts)} routed alert(s) on the "
                        f"session channel: {first}{more}; clear with "
                        f"`{_plugin_cmd('hooks/scripts/blackboard.py', 'consume', '--channel', 'session')}`.")
    except Exception:
        pass

    result: dict[str, Any] = {
        "decision": "allow",
        "additionalContext": context,
    }
    # The human channel (E-175): the same position `_workflow_peek` gives the
    # model, rendered for the person at the keyboard. Silent when idle.
    user_message = _workflow_notice(root, "session")
    if user_message:
        result["user_message"] = user_message
    return result


def _trail_output(tool_name: str, tool_output, failed: bool) -> str | None:
    """The row's own text: real output, in the window the classifier read.

    A failing terminal call keeps the whole window, so a later session can
    re-derive the class from the row and nothing else; a silent one keeps a
    short preview, because a subset of text that matched nothing cannot match
    anything. Other tools keep the bounded preview of the response: their
    payload is an edit body, not command output.
    """
    if not tool_output:
        return None
    if tool_name == "terminal":
        text = command_output_text(tool_output)
        if text:
            return _output_window(
                text, _OUTPUT_WINDOW if failed else _OUTPUT_PREVIEW_LEN)
    return str(tool_output)[:_OUTPUT_PREVIEW_LEN]


def audit(json_in: dict) -> dict:
    """PostToolUse audit: append one redacted JSON line, return.

    Fail-open: a log write failure never blocks the tool call. The QR DoD
    consumption check is content-only (no I/O) and stays as the single
    warn-only signal; story-file AC / experiment_refs validation lives in
    the guard — auditing it again here would double-report the same defect.
    """
    from .utils import normalize_hook_input
    norm = normalize_hook_input(json_in)
    tool_name = norm["tool_name"]
    tool_input = norm["tool_input"]
    # Claude Code's PostToolUse payload carries the tool's outcome under
    # `tool_response`; `tool_output` is this engine's own (test) spelling.
    # Reading only the latter left output_summary null on every real record —
    # no command outcome, on either OS, ever reached the trail.
    tool_output = (json_in.get("tool_response")
                   or json_in.get("tool_output") or {})

    # A FAILED command is signal even when the command itself only reads: the
    # fast exit below must not swallow the one outcome the trail exists to
    # show (B-003's nomatch abort and B-004's path collapse died behind it).
    failure = command_failure(tool_name, tool_output)

    # Read-only fast exit FIRST: ls/find/cat/git-status/file views carry no
    # methodology signal — skip the log write entirely (no I/O). Commit,
    # deploy, real file writes and known-failure outputs fall through below.
    if failure is None and is_readonly_call(
            tool_name, tool_input,
            powershell=norm["raw_tool_name"] == "PowerShell"):
        return {"decision": "allow"}

    # Project root anchored like guard/quality/deploy (cwd may differ under
    # OpenHands; repo_root resolves via OPENHANDS_PROJECT_DIR).
    from .utils import repo_root
    root = repo_root(json_in)

    # Build audit record. File content is NEVER logged whole: large or
    # sensitive bodies stay out of the trail (preview + length only).
    record = {
        "timestamp": time.time(),
        "tool": tool_name,
        "input": _redacted_input(tool_input),
        "output_summary": _trail_output(tool_name, tool_output, failure is not None),
    }
    if failure:
        # Machine-countable class for the trail (failures per session per OS
        # is the baseline the research records' Next Steps ask to measure).
        record["command_failed"] = failure[0]

    warnings = _check_kopru_consumption(tool_name, tool_input, root)
    if failure:
        # One bounded correction per failing call; main.py feeds warnings to
        # the model as additionalContext — the fact arrives where the session
        # is, right after the error the agent just saw.
        warnings = [failure[1]] + warnings

    if warnings:
        record["methodology_warnings"] = warnings

    log_path = pathlib.Path(root).absolute() / log_file()

    try:
        # Ensure log directory exists
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # Append record
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        import sys
        print(f"audit log write failed: {exc}", file=sys.stderr)

    result: dict[str, Any] = {"decision": "allow"}
    if warnings:
        result["methodology_warnings"] = warnings
    # Announce the advance to the HUMAN, not only to the trail: a run that
    # moves stage by stage in silence is the complaint this answers (E-175).
    # A refused `complete` did not advance, so `failure` keeps it quiet.
    if failure is None and _WORKFLOW_MUTATION_RE.search(
            str(tool_input.get("command") or "")):
        user_message = _workflow_notice(root, "turn")
        if user_message:
            result["user_message"] = user_message
    return result

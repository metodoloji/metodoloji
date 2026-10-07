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

from .config import log_file
from .utils import dod_issues, has_dod_content, repo_root, scan_dod_items


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
_COMMAND_FAILURES = (
    ("glob_nomatch",
     re.compile(r"(?i)no matches found|\bnomatch\b"),
     "Command aborted by an unmatched glob — zsh (macOS) cancels the whole "
     "line when a pattern matches nothing: confirm the target exists before "
     "a `path/*` command, and never let a glob decide whether the rest runs."),
    ("backslash_path",
     re.compile(r"(?i)cannot access ['\"]?[a-z]:[^/\\]"),
     "A native backslash path collapsed inside the POSIX shell — rewrite it "
     "with forward slashes (`C:/Users/...` works in Bash, PowerShell and "
     "Python alike)."),
    ("powershell_dialect",
     re.compile(r"(?i)CommandNotFoundException|not recognized as the name of "
                r"a cmdlet|is not recognized as an internal or external "
                r"command|is not a valid statement separator"),
     "Shell dialect mismatch — PowerShell has no grep/sed/ls flags and PS 5.1 "
     "has no `&&`: run POSIX pipelines in the Bash tool or use the cmdlet "
     "equivalents (Bash = POSIX sh; PowerShell = PowerShell)."),
    ("bsd_sed",
     re.compile(r"(?i)invalid command code"),
     "GNU-style `sed -i 's/…/'` fails under BSD sed (macOS) — use "
     "`sed -i.bak 's/…/' file`, which works on both, or `sed -i '' …` on "
     "macOS only."),
    ("command_not_found",
     re.compile(r"(?i)command not found"),
     "The command never resolved — check the tool exists in THIS shell and "
     "that nothing relies on a shell variable from an earlier tool call "
     "(every call is a fresh shell: run commands self-contained with literal "
     "paths)."),
)


def command_failure(tool_name: str, tool_output) -> tuple[str, str] | None:
    """(kind, bounded correction) when a terminal output shows a known
    cross-platform failure; None otherwise.

    Only terminal output is inspected, only against the fixed table above.
    The match window keeps the hot path off multi-MB outputs: these failures
    print at the head (nomatch, command-not-found) or the tail (tracebacks).
    """
    if tool_name != "terminal" or not tool_output:
        return None
    s = str(tool_output)
    hay = s if len(s) <= 4000 else s[:2000] + "\n" + s[-2000:]
    for kind, rx, hint in _COMMAND_FAILURES:
        if rx.search(hay):
            return kind, hint
    return None


def is_readonly_call(tool_name: str, tool_input: dict) -> bool:
    """True when the call cannot mutate project state (safe to skip logging)."""
    if tool_name == "terminal":
        cmd = str((tool_input or {}).get("command", "") or "")
        if not cmd.strip():
            return True
        if _AUDIT_COMMIT_RE.search(cmd) or _AUDIT_DEPLOY_RE.search(cmd):
            return False
        try:
            from .bash_targets import extract_bash_targets_ex
            targets, dropped = extract_bash_targets_ex(cmd)
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
    redacted = {}
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
    warnings = []

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
        from bmad.workflow import advisory
        return advisory.peek(root)
    except Exception:
        return ""


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
    from .utils import repo_root
    root = repo_root(json_in)
    try:
        from .stop import record_session_start
        record_session_start(root)
    except Exception:
        pass

    context = FIXED_SESSION_CONTEXT
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
                        f"plan with run_experiment.py --amend-plan.")
    except Exception:
        pass
    # Both peeks below are read-only (never consume, never mutate) and
    # fail-open: a missing or corrupt board leaves just the fixed sentence.
    try:
        from . import blackboard as bb
        waiting = bb.pending_handoff_channels(root)
        if waiting:
            waiting_s = "; ".join(
                f"{s} ({n}): {n} unclaimed signal(s) from completed upstream "
                f"runs — a run finished its work but nobody picked up the "
                f"baton; peek `handoffs --skill {s}`, then consume its "
                f"handoff channel"
                for s, n in waiting.items())
            context += f" PROACTIVE — hand-off waiting: {waiting_s}."
        # Chain progress: when methodology run keys exist on the board,
        # surface how far the relay progressed so the session grounds in
        # live state instead of a static chain reminder. Empty board →
        # nothing appended (keeps the fixed sentence for fresh projects).
        progress = bb.compact_context(root).get("methodology", {})
        if progress:
            chain_s = "; ".join(f"{stage}: {val[:60]}"
                                for stage, val in progress.items())
            context += f" Chain progress — {chain_s}."
        # Workflow-kernel peek (2026-10-02): the run's position is COMPUTED
        # state, not memory — a resumed session continues the declared workflow
        # instead of re-deriving the plan from chat, and the sentence names
        # where the kernel will go next so ordering is never re-negotiated.
        # Read-only, bounded, and silent when nothing is running.
        wf_line = _workflow_peek(root)
        if wf_line:
            context += f" {wf_line}"
        # Scope-debt peek (2026-09-25): the commit-time mirror posts uncovered
        # files to the "scope" channel; a later session should still see the
        # debt even though the commit warning has scrolled away. Read-only —
        # consuming here would silence the debt for everyone else; only the
        # remediation (E record approval / next commit without misses, which
        # replaces the alert) may clear it.
        scope_alerts = bb.pending_alerts(root, "scope")
        if scope_alerts:
            first = str(scope_alerts[0].get("text", ""))[:160]
            more = f" (+{len(scope_alerts) - 1} older)" if len(scope_alerts) > 1 else ""
            context += f" SCOPE DEBT — {first}{more}."
        # Subscription-plane delivery announcement (BLACKBOARD.md §5: "session
        # → injected at session start"). The engine invariants bar the session
        # edge from consuming (announce-only: never consume, never mutate), so
        # the edge surfaces the routed alerts and the addressed watcher clears
        # them by hand with `consume --channel session` — deliver-once stays
        # with the consumer, exactly like the handoff handshake. Silent when
        # nothing was ever routed (fresh boards contribute nothing).
        session_alerts = bb.pending_alerts(root, "session")
        if session_alerts:
            first = str(session_alerts[0].get("text", ""))[:160]
            more = (f" (+{len(session_alerts) - 1} more routed)"
                    if len(session_alerts) > 1 else "")
            context += (f" Blackboard — {len(session_alerts)} routed alert(s) on the "
                        f"session channel: {first}{more}; clear with "
                        f"`blackboard.py consume --channel session`.")
    except Exception:
        pass

    return {
        "decision": "allow",
        "additionalContext": context,
    }


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
    if failure is None and is_readonly_call(tool_name, tool_input):
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
        "output_summary": str(tool_output)[:_OUTPUT_PREVIEW_LEN] if tool_output else None,
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

    result = {"decision": "allow"}
    if warnings:
        result["methodology_warnings"] = warnings
    return result

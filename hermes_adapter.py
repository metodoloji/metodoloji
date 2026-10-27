"""Hermes Agent bridge for the Metodoloji plugin.

Hermes runs plugin hooks **in-process in the gateway** and speaks its own
dialect (``tool_name`` + ``args``, one tool name per action: ``write_file``,
``patch``, ``terminal``, ``execute_code``). The methodology engine speaks the
Claude Code wire format (``tool_name`` + ``tool_input``, ``Write``/``Edit``/
``Bash``) and answers with ``hookSpecificOutput.permissionDecision``.

This module is the only place that knows both sides:

    hermes tool call ──► engine stdin payload ──► engine decision
                                                     │
        {"action": "block"} / None  ◄────────────────┘

Two design rules, both load-bearing:

1. **The engine is never imported.** Every call is a subprocess. A gate module
   that calls ``sys.exit``, a corrupted record that trips a bug, or a hung
   ``git`` cannot take the gateway down, and the engine keeps its own
   fail-closed policy: a missing engine or unreadable output BLOCKS a gated
   tool call rather than silently allowing it.
2. **Fail closed only where the engine does.** ``pre`` (guard + quality +
   deploy) denies on error; audit/stop/session_start are report-only and fail
   open, because a logging failure must never wedge a session.

The payload's ``cwd`` carries the session working directory: Hermes hooks run
in the gateway process, whose own cwd is NOT the project (see
``agent/runtime_cwd.py``), so the engine's ``repo_root()`` fallback would
resolve the wrong tree. ``CLAUDE_PROJECT_DIR`` / ``OPENHANDS_PROJECT_DIR`` are
stripped from the child environment for the same reason — they outrank the
payload and would silently redirect every gate to another project.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent
ENGINE = PLUGIN_ROOT / "hooks" / "engine" / "main.py"

# Tool calls the engine must judge. Everything else (read_file, web_search,
# browser_*, skill_view, …) is a read-only call the guard allows by design;
# skipping them here keeps the per-call cost at zero for the common turn.
# `process_manage` is only gated for its write/submit actions (it feeds a PTY's
# stdin, which is a shell channel); list/poll/log/wait stay free.
GATED_TOOLS = frozenset({
    "write_file", "patch", "multi_edit", "notebook_edit",
    "terminal", "execute_code", "process_manage",
})

# Plugin settings (declared in plugin.yaml config_schema, read once in
# register()). They exist because the honest default differs per project:
# a report-only Stop equivalent and a GUI tool policy are policy choices, not
# runtime facts, so they are configuration rather than hard-coded behaviour.
OPTIONS: dict = {
    "execute_code_guard": "warn",   # warn | deny — unresolvable execute_code writes
    "block_gui_tools": False,       # deny computer_use (GUI bypasses file hooks)
    "carry_stop_report": True,      # deliver the last stop report at next session start
    "nudge_on_stop": False,         # pre_verify: turn an actionable stop report into one nudge
    "include_skill_triggers": True, # fold skill `triggers:` into the registered description
}


def configure(options: dict | None) -> None:
    """Apply plugin settings (unknown values fall back to the documented default)."""
    for key, value in (options or {}).items():
        if key not in OPTIONS:
            continue
        if key == "execute_code_guard":
            OPTIONS[key] = "deny" if str(value).lower() == "deny" else "warn"
        elif key in ("block_gui_tools", "carry_stop_report", "nudge_on_stop",
                     "include_skill_triggers"):
            OPTIONS[key] = bool(value)

# Modes whose failure must block. ``pre`` bundles guard+quality+deploy, i.e.
# the three that can deny a write, a commit or a deploy.
FAIL_CLOSED_MODES = frozenset({"pre", "guard", "quality", "deploy"})

# The engine's own hook timeout in hooks/hooks.json is 10s for PreToolUse and
# 2s for the audit trail. Match those budgets: Hermes bounds plugin callbacks
# at 30s, and a gate that answers after the tool already ran is worthless.
TIMEOUTS = {
    "pre": 12.0, "guard": 12.0, "quality": 12.0, "deploy": 12.0,
    "audit": 4.0, "stop": 10.0, "session_start": 10.0,
}

_ENV_LOCK = threading.Lock()


class EngineUnavailable(RuntimeError):
    """No interpreter or no engine on disk — a fail-closed condition for `pre`."""


def python_executable() -> str:
    """Interpreter for the engine subprocess.

    ``sys.executable`` first: the plugin runs inside Hermes, so this is the
    exact interpreter that already has the engine's requirements (3.11+), and
    it is the only one guaranteed to exist on Windows installs where ``python3``
    is not on PATH.
    """
    candidates = [sys.executable, shutil.which("python3"), shutil.which("python")]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    raise EngineUnavailable("no python interpreter found for the methodology engine")


def agent_cwd() -> str:
    """The session's working directory (never the gateway's own cwd)."""
    try:  # Hermes pins the session dir in a contextvar for in-process plugins
        from agent.runtime_cwd import resolve_agent_cwd

        return str(resolve_agent_cwd())
    except Exception:
        return os.getcwd()


def plugin_version() -> str:
    try:
        import yaml  # Hermes ships PyYAML

        data = yaml.safe_load((PLUGIN_ROOT / "plugin.yaml").read_text(encoding="utf-8"))
        return str((data or {}).get("version") or "")
    except Exception:
        return ""


def build_payload(tool_name: str, args: dict | None, *, cwd: str | None = None,
                  session_id: str = "", event: str = "PreToolUse") -> dict:
    """Claude-shaped payload the engine's normalize_hook_input() expects.

    ``tool_name`` stays in Hermes spelling: the engine's ``--runtime=hermes``
    branch owns the mapping (including ``execute_code`` → terminal), so the
    vocabulary lives in exactly one place.
    """
    return {
        "tool_name": tool_name,
        "tool_input": args or {},
        "cwd": cwd or agent_cwd(),
        "session_id": session_id,
        "hook_event_name": event,
    }


def _child_env(mode: str) -> dict:
    env = dict(os.environ)
    # Outrank the payload → strip them (see module docstring).
    env.pop("CLAUDE_PROJECT_DIR", None)
    env.pop("OPENHANDS_PROJECT_DIR", None)
    env["METODOLOJI_RUNTIME"] = "hermes"
    env["HOOK_TYPE"] = mode
    env["METODOLOJI_PLUGIN_ROOT"] = str(PLUGIN_ROOT)
    env.setdefault("METODOLOJI_HERMES", "1")
    return env


def run_engine(mode: str, payload: dict, *, timeout: float | None = None,
               cwd: str | None = None) -> dict:
    """Run one engine mode and normalize its answer.

    Returns ``{"decision": "allow"|"deny", "reason": str, "warnings": [...],
    "context": str, "exit_code": int, "stderr": str}``. Never raises for the
    report-only modes; raises :class:`EngineUnavailable` only when the engine
    itself is missing (callers decide the policy, see :func:`check_tool`).
    """
    if not ENGINE.is_file():
        raise EngineUnavailable(f"engine missing at {ENGINE}")

    workdir = cwd or str(payload.get("cwd") or agent_cwd())
    limit = TIMEOUTS.get(mode, 10.0) if timeout is None else timeout
    argv = [python_executable(), str(ENGINE), "--runtime=hermes"]

    with _ENV_LOCK:  # env is built per call; the lock keeps diagnostics ordered
        env = _child_env(mode)
    try:
        proc = subprocess.run(
            argv,
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=limit,
            cwd=workdir,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return _result("deny" if mode in FAIL_CLOSED_MODES else "allow",
                       f"Methodology {mode} hook timed out after {limit:g}s",
                       exit_code=124)
    except OSError as exc:
        return _result("deny" if mode in FAIL_CLOSED_MODES else "allow",
                       f"Methodology {mode} hook could not start: {exc}", exit_code=127)

    out = (proc.stdout or "").strip()
    parsed: dict = {}
    if out:
        try:
            decoded = json.loads(out)
            if isinstance(decoded, dict):
                parsed = decoded
        except (ValueError, RecursionError):
            parsed = {}

    hso_value = parsed.get("hookSpecificOutput")
    hso = hso_value if isinstance(hso_value, dict) else {}
    permission = hso.get("permissionDecision")
    decision = str(permission or parsed.get("decision") or "allow").lower()
    reason = str(hso.get("permissionDecisionReason") or parsed.get("reason") or "")
    context = str(hso.get("additionalContext") or "")
    warnings = [line for line in context.splitlines() if line.strip()] if context else []

    # Stop mode is loop-safe by design: top-level block + additionalContext.
    if mode == "stop" and str(parsed.get("decision") or "").lower() == "block":
        decision = "deny"
        reason = reason or context

    # Gated modes require the engine's explicit permission envelope and a
    # successful process exit. A parseable partial allow response is not a
    # valid decision; accepting it would turn a crashed hook into a bypass.
    if mode in FAIL_CLOSED_MODES and (
        proc.returncode != 0 or permission not in ("allow", "deny")
    ):
        return _result(
            "deny",
            f"Methodology {mode} hook produced no valid decision "
            f"(exit {proc.returncode}) — fail-closed blocked.",
            exit_code=proc.returncode,
            stderr=(proc.stderr or "")[-2000:],
        )

    if decision not in ("allow", "deny", "ask") or (not parsed and proc.returncode != 0):
        # No decidable answer. Fail closed where the engine would have, and
        # say which mode produced the silence so the block message is actionable.
        return _result("deny" if mode in FAIL_CLOSED_MODES else "allow",
                       f"Methodology {mode} hook produced no decision "
                       f"(exit {proc.returncode}) — fail-closed blocked.",
                       exit_code=proc.returncode, stderr=(proc.stderr or "")[-2000:])

    return _result("deny" if decision == "deny" else "allow", reason,
                   warnings=warnings, context=context,
                   exit_code=proc.returncode, stderr=(proc.stderr or "")[-2000:])


def _result(decision: str, reason: str = "", *, warnings=None, context: str = "",
            exit_code: int = 0, stderr: str = "") -> dict:
    return {
        "decision": decision,
        "reason": reason,
        "warnings": list(warnings or []),
        "context": context,
        "exit_code": exit_code,
        "stderr": stderr,
    }


def check_tool(tool_name: str, args: dict | None, *, session_id: str = "",
               cwd: str | None = None) -> dict:
    """Pre-tool gate for one Hermes tool call.

    Returns the engine result with an extra ``block`` key: the Hermes hook
    directive (``{"action": "block", "message": …}``) when the call must be
    stopped, otherwise ``None``.

    Three Hermes-only policies live here rather than in the engine, because
    they are about the HARNESS, not the methodology:
    * ``process_manage`` is gated only for write/submit (PTY stdin = shell).
    * ``computer_use`` cannot be gated at file level at all; the operator can
      deny it outright (``block_gui_tools``), otherwise it passes untouched.
    * ``execute_code`` writes whose target stays unresolved can be denied
      instead of warned (``execute_code_guard: deny``).
    """
    args = args or {}

    if tool_name == "process_manage":
        if str(args.get("action") or "").lower() not in ("write", "submit"):
            return {**_result("allow"), "block": None}
    if tool_name == "computer_use":
        if not OPTIONS.get("block_gui_tools"):
            return {**_result("allow"), "block": None}
        return {
            **_result("deny", "computer_use is disabled for this profile "
                               "(plugins.entries.metodoloji.settings.block_gui_tools) — "
                               "GUI automation bypasses the record gate."),
            "block": {
                "action": "block",
                "message": "[metodoloji] computer_use is blocked by policy. Work through "
                           "write_file/patch/terminal so the record gate can see the change.",
            },
        }
    if tool_name not in GATED_TOOLS:
        return {**_result("allow"), "block": None}

    payload = build_payload(tool_name, args, cwd=cwd, session_id=session_id)
    try:
        result = run_engine("pre", payload, cwd=cwd)
    except EngineUnavailable as exc:
        # Same policy as hook-entry.sh: an unresolvable engine denies.
        result = _result("deny", f"Methodology engine unavailable — fail-closed blocked. ({exc})")

    # Policy: an execute_code write whose target the engine could not resolve
    # arrives here as the `unknown` tool path. `warn` keeps it visible and
    # allowed (the guard's documented stance); `deny` makes the blind spot a
    # hard stop with the remedy spelled out.
    if (tool_name == "execute_code" and OPTIONS.get("execute_code_guard") == "deny"
            and result["decision"] == "allow"
            and any("execute_code" in warning for warning in result["warnings"])):
        result = _result(
            "deny",
            "execute_code writes a file whose path is not a literal, so the record "
            "gate cannot judge it. Name the target literally "
            "(open('src/file.py','w') / Path('src/file.py').write_text(...)) or use "
            "write_file. (plugins.entries.metodoloji.settings.execute_code_guard=deny)",
            warnings=result["warnings"], exit_code=result.get("exit_code", 0),
        )

    block = None
    if result["decision"] == "deny":
        message = result["reason"] or "Methodology gate denied this call."
        block = {
            "action": "block",
            "message": f"{message}\n\n[metodoloji] The record chain (E→IR→SP→S→QR→PR) "
                       f"gates code writes. Read the denial, fix the record, then retry.",
        }
    return {**result, "block": block}


def _consume_stop_report(root: str) -> str:
    """Take the last session-end report, once — and only if it is new.

    Hermes has no Stop hook: the engine's `stop` mode runs on ``on_session_end``
    and its report is report-only by design (it must never wedge a close). The
    report still has to reach the MODEL, so it is delivered at the next session
    start and then cleared.

    ``stop`` also reports project-wide state (waiting hand-offs), which the next
    session regenerates verbatim — so "clear and deliver" alone would re-inject
    the same paragraph every session. The digest of the last delivered report is
    kept beside it; an identical report is consumed silently.
    """
    log_path = Path(root) / ".metodoloji" / "logs" / "hermes-stop.log"
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if not text.strip():
        return ""
    last = text.split("\n--- ")[-1].strip()
    if last.startswith("--- "):  # single block: drop its session-id header
        last = last.split("\n", 1)[-1].strip()
    if not last:
        return ""
    try:  # delivered once: a stale report must not be re-announced forever
        log_path.write_text("", encoding="utf-8")
    except OSError:
        pass

    digest = hashlib.sha256(last.encode("utf-8")).hexdigest()
    marker = log_path.with_suffix(".delivered")
    try:
        if marker.read_text(encoding="utf-8").strip() == digest:
            return ""  # same project state as last time; nothing new to say
    except OSError:
        pass
    try:
        marker.write_text(digest, encoding="utf-8")
    except OSError:
        pass
    return last


def session_context(*, session_id: str = "", cwd: str | None = None) -> str:
    """SessionStart context for the first turn of a Hermes session.

    The engine returns the fixed methodology sentence plus live record/board
    state; the two root tokens it references are appended here because Hermes
    slash commands and skills do not expand ``{metodoloji-root}`` themselves.
    The previous session's stop report and a tool index are appended too.
    """
    root = cwd or agent_cwd()
    payload = build_payload("", {}, cwd=root, session_id=session_id, event="SessionStart")
    try:
        result = run_engine("session_start", payload)
        body = result.get("context") or ""
    except EngineUnavailable as exc:
        body = f"(methodology engine unavailable: {exc})"

    header = (
        f"metodoloji-root: {PLUGIN_ROOT}\n"
        f"project-root: {root}\n"
        f"metodoloji-plugin: hermes v{plugin_version() or 'unknown'}\n"
        f"tools: metodoloji_orient, metodoloji_init, metodoloji_verify, "
        f"metodoloji_key, metodoloji_audit (resolve via tool_search if absent)"
    )
    parts = [header, body.strip()]
    if OPTIONS.get("carry_stop_report"):
        report = _consume_stop_report(root)
        if report:
            parts.append("Previous session report (report-only): " + report)
    return "\n".join(part for part in parts if part).strip()


def stop_report(*, session_id: str = "", cwd: str | None = None) -> str:
    """Session-end report (report-only; never blocks)."""
    payload = build_payload("", {}, cwd=cwd or agent_cwd(), session_id=session_id,
                            event="Stop")
    try:
        result = run_engine("stop", payload)
    except EngineUnavailable:
        return ""
    return result.get("reason") or result.get("context") or ""


def audit_tool_call(tool_name: str, args: dict | None, *, session_id: str = "") -> None:
    """Append one redacted line to the audit trail (report-only)."""
    if tool_name not in GATED_TOOLS:
        return
    payload = build_payload(tool_name, args, session_id=session_id, event="PostToolUse")
    try:
        run_engine("audit", payload)
    except EngineUnavailable:
        return


def run_plugin_script(relative: str, argv: list[str], *, cwd: str | None = None,
                      timeout: float = 60.0) -> subprocess.CompletedProcess:
    """Run a shipped project script (skeleton/orient/gate) in the session cwd."""
    script = PLUGIN_ROOT / relative
    if not script.is_file():
        raise EngineUnavailable(f"script missing: {script}")
    return subprocess.run(
        [python_executable(), str(script), *argv],
        capture_output=True, text=True, timeout=timeout,
        cwd=cwd or agent_cwd(), env=_child_env("tool"),
    )

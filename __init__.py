"""Metodoloji for Hermes Agent — plugin entry point.

Metodoloji enforces a record chain (E → IR → SP → S → QR → PR) mechanically:
no code is written outside an approved, HMAC-verified experiment scope, commits
and deploys must point at their records, and every write leaves an audit line.
The engine that decides this already ships in this repository
(``hooks/engine/main.py``) and is shared with the Claude Code, OpenHands and
OpenCode bridges — this module only connects Hermes' in-process hook contract
and tool registry to it.

What is registered
------------------
tools   metodoloji_orient / metodoloji_init / metodoloji_verify / metodoloji_key
        / metodoloji_audit
hooks   pre_tool_call   → engine ``pre``   (guard + quality + deploy; can BLOCK)
        post_tool_call  → engine ``audit`` (redacted trail; never blocks)
        pre_llm_call    → engine ``session_start`` context, injected once
        on_session_start → reset the per-session flags
        pre_verify      → optional one-shot continuation (``nudge_on_stop``)
        on_session_end  → engine ``stop``  (session report; never blocks)
skills  every ``skills/*/SKILL.md`` in the repository, namespaced ``metodoloji:<name>``
cmds    /metodoloji, /metodoloji-init, /metodoloji-gate-setup, /metodoloji-verify,
        /metodoloji-audit

Hard rule: a gated tool call is blocked ONLY by an explicit engine denial. A
crash, timeout or unreadable answer from the engine blocks too (fail closed);
a missing plugin, a read-only tool or a logging failure never does.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

try:
    from .hermes_adapter import (
        GATED_TOOLS, OPTIONS, PLUGIN_ROOT, agent_cwd, audit_tool_call, check_tool,
        configure, plugin_version, session_context, stop_report,
    )
    from .hermes_tools import (
        handle_audit, handle_init, handle_key, handle_orient, handle_verify,
        register_tools,
    )
except ImportError:  # pragma: no cover - standalone import (tests)
    from hermes_adapter import (  # type: ignore[no-redef]
        GATED_TOOLS, OPTIONS, PLUGIN_ROOT, agent_cwd, audit_tool_call, check_tool,
        configure, plugin_version, session_context, stop_report,
    )
    from hermes_tools import (  # type: ignore[no-redef]
        handle_audit, handle_init, handle_key, handle_orient, handle_verify,
        register_tools,
    )

logger = logging.getLogger(__name__)

__version__ = plugin_version() or "0.0.0"

SKILLS_DIR = PLUGIN_ROOT / "skills"
_MAX_WARNINGS = 12
_STATE_LOCK = threading.Lock()
# Per-session state: whether the methodology context was injected already, and
# soft-gate warnings the engine produced since the last turn. Bounded, and
# dropped at session end so a long-lived gateway cannot leak memory.
_SESSIONS: dict[str, dict] = {}

# Tool calls the gate BLOCKED, keyed by Hermes' tool_call_id. post_tool_call
# still fires for a blocked call (with the denial as its result), so auditing
# it unconditionally told the engine's stop report that the file had been
# written — a denied `src/app.py` write was reported back as "code written
# this session". Pre-tool records the id here; post-tool consumes it and skips
# the audit line. Entries are pruned by age so a blocked call whose post-hook
# never fires cannot accumulate.
_BLOCKED_TTL_SECONDS = 600.0
_BLOCKED_CALLS: dict[str, float] = {}


def _mark_blocked(tool_call_id: str) -> None:
    if not tool_call_id:
        return
    now = time.monotonic()
    with _STATE_LOCK:
        for key in [k for k, ts in _BLOCKED_CALLS.items() if now - ts > _BLOCKED_TTL_SECONDS]:
            _BLOCKED_CALLS.pop(key, None)
        if len(_BLOCKED_CALLS) > 256:  # hard cap: never a memory leak
            _BLOCKED_CALLS.clear()
        _BLOCKED_CALLS[tool_call_id] = now


def _consume_blocked(tool_call_id: str) -> bool:
    if not tool_call_id:
        return False
    with _STATE_LOCK:
        return _BLOCKED_CALLS.pop(tool_call_id, None) is not None


# --------------------------------------------------------------------------- #
# skills
# --------------------------------------------------------------------------- #
def _frontmatter(path: Path) -> dict:
    """Parse a SKILL.md frontmatter block without failing on odd YAML."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    block = text[3:end]
    try:
        import yaml

        data = yaml.safe_load(block)
        return data if isinstance(data, dict) else {}
    except Exception:
        # Fall back to a single-line scan: a broken sibling must not cost the
        # other 128 skills their registration.
        meta: dict[str, str] = {}
        for line in block.splitlines():
            key, _, value = line.partition(":")
            if value and key.strip() in ("name", "description"):
                meta[key.strip()] = value.strip().strip("\"'")
        return meta


def _skill_description(meta: dict, name: str) -> str:
    """Description Hermes shows in the available-skills index.

    Hermes routes on the description alone, while BMB skills carry their
    activation words in a separate ``triggers:`` list. Folding the first few
    triggers in keeps that routing information visible to the model (the
    difference between "the agent finds this skill" and "the user must type
    the slash command"). Capped: the index line is a prompt cost, not a
    spec sheet.
    """
    description = str(meta.get("description") or "").strip().replace("\n", " ")
    if not description:
        description = f"Metodoloji skill: {name}"
    triggers = meta.get("triggers")
    if OPTIONS.get("include_skill_triggers") and isinstance(triggers, (list, tuple)):
        extra = []
        for trigger in triggers:
            text = str(trigger).strip()
            if not text or text.startswith("/"):
                continue
            if text.lower() in description.lower():
                continue
            extra.append(text)
            if len(extra) == 3:
                break
        if extra:
            description = f"{description} Triggers: {', '.join(extra)}."
    return description[:400]


def register_skills(ctx) -> int:
    """Register every shipped skill as ``metodoloji:<name>``.

    Skipped wholesale with ``METODOLOJI_HERMES_SKIP_SKILLS=1``: 128 descriptions
    ride in the system prompt's available-skills list, and a user who wants the
    gates without the skill catalog should be able to say so.
    """
    if os.environ.get("METODOLOJI_HERMES_SKIP_SKILLS", "").strip() in ("1", "true", "yes"):
        logger.info("metodoloji: skill registration skipped by METODOLOJI_HERMES_SKIP_SKILLS")
        return 0
    if not SKILLS_DIR.is_dir():
        return 0

    registered = 0
    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        skill_md = skill_dir / "SKILL.md"
        if not skill_dir.is_dir() or not skill_md.is_file():
            continue
        name = skill_dir.name
        try:
            meta = _frontmatter(skill_md)
            ctx.register_skill(
                name=name,
                path=skill_md,
                description=_skill_description(meta, name),
                frontmatter=meta,
            )
            registered += 1
        except Exception as exc:  # one bad skill must not abort the plugin load
            logger.warning("metodoloji: skill %s not registered (%s)", name, exc)
    logger.info("metodoloji: registered %d skills", registered)
    return registered


# --------------------------------------------------------------------------- #
# hooks
# --------------------------------------------------------------------------- #
def _state(session_id: str) -> dict:
    key = session_id or "_"
    with _STATE_LOCK:
        return _SESSIONS.setdefault(key, {"injected": False, "warnings": []})


def on_pre_tool_call(tool_name: str, args: dict, task_id: str = "", **kwargs):
    """Gate a Hermes tool call through the methodology engine.

    Returning ``{"action": "block", "message": ...}`` stops the call before it
    executes — the equivalent of Claude Code's PreToolUse deny. Soft-gate
    warnings (code_guard=soft) are stashed and injected with the next turn:
    Hermes' pre_tool_call contract can block or rewrite, not add context.
    """
    if tool_name not in GATED_TOOLS:
        return None
    try:
        result = check_tool(tool_name, args, session_id=task_id or kwargs.get("session_id", ""))
    except Exception as exc:  # adapter bug → fail closed, never silent allow
        logger.warning("metodoloji: pre_tool_call failed (%s)", exc)
        return {
            "action": "block",
            "message": f"[metodoloji] gate error: {type(exc).__name__}: {exc} — fail-closed.",
        }

    if result.get("warnings"):
        state = _state(task_id or kwargs.get("session_id", ""))
        with _STATE_LOCK:
            state["warnings"].extend(result["warnings"])
            del state["warnings"][:-_MAX_WARNINGS]
    block = result.get("block")
    if block:
        _mark_blocked(str(kwargs.get("tool_call_id") or ""))
    return block


def on_post_tool_call(tool_name: str, args: dict, result: str = "", task_id: str = "",
                      duration_ms: int = 0, **kwargs):
    """Append the redacted audit line (report-only, never blocks).

    A blocked call reaches this hook too, carrying the denial as its result.
    Logging it would make the engine's stop report claim a file was written
    that never was, so blocked ids are consumed and dropped here.
    """
    if tool_name not in GATED_TOOLS:
        return None
    if _consume_blocked(str(kwargs.get("tool_call_id") or "")):
        return None
    try:
        audit_tool_call(tool_name, args, session_id=task_id or kwargs.get("session_id", ""))
    except Exception as exc:
        logger.debug("metodoloji: audit skipped (%s)", exc)
    return None


def on_session_start(session_id: str, model: str = "", platform: str = "", **kwargs):
    """Reset session state. The context itself is injected on the first turn."""
    with _STATE_LOCK:
        _SESSIONS[session_id or "_"] = {"injected": False, "warnings": []}
    return None


def on_pre_llm_call(session_id: str, is_first_turn: bool = False, **kwargs):
    """Inject the methodology session context on the first turn.

    Hermes appends returned text to the CURRENT turn's user message (never the
    system prompt), so the prompt cache survives — the same contract the
    session-start hook uses in the other runtimes.
    """
    state = _state(session_id)
    with _STATE_LOCK:
        warnings = state["warnings"]
        state["warnings"] = []
        already = state["injected"]

    parts: list[str] = []
    if is_first_turn or not already:
        try:
            parts.append(session_context(session_id=session_id))
            with _STATE_LOCK:
                state["injected"] = True
        except Exception as exc:
            logger.debug("metodoloji: session context skipped (%s)", exc)
            parts.append(f"metodoloji-root: {PLUGIN_ROOT}")
    if warnings:
        parts.append("[metodoloji] soft-gate warnings from the previous tool calls:\n"
                     + "\n".join(f"- {w}" for w in warnings))
    text = "\n\n".join(p for p in parts if p).strip()
    return {"context": text} if text else None


_ACTIONABLE_REPORT_MARKERS = (
    "in-progress", "no approved", "without an approved", "unapproved",
    "open plan", "hand-off", "handoff",
)


def on_pre_verify(session_id: str, coding: bool = False, attempt: int = 0, **kwargs):
    """Optional Stop equivalent: one nudge when the session report names open work.

    Hermes has no Stop hook that can hand context back to the model. `pre_verify`
    fires once per turn when the agent edited code and may keep the agent going
    — the closest analogue. It is OPT-IN (`nudge_on_stop`), because the engine's
    `stop` mode is deliberately report-only: turning every report into a forced
    continuation would change methodology semantics. Bounded by Hermes itself
    (`agent.max_verify_nudges`, default 3) and made one-shot here via `attempt`.
    """
    if not OPTIONS.get("nudge_on_stop") or not coding or attempt:
        return None
    try:
        report = stop_report(session_id=session_id)
    except Exception:
        return None
    if not report:
        return None
    lowered = report.lower()
    if not any(marker in lowered for marker in _ACTIONABLE_REPORT_MARKERS):
        return None
    return {
        "action": "continue",
        "message": ("Metodoloji kapanış raporu (report-only — kapatmayı engellemez, "
                    "yalnızca açık işi hatırlatır): " + report),
    }


def on_session_end(session_id: str, **kwargs):
    """Write the session report to the project's log (report-only)."""
    try:
        report = stop_report(session_id=session_id)
    except Exception as exc:
        logger.debug("metodoloji: stop report skipped (%s)", exc)
        report = ""
    if report:
        try:
            log_dir = Path(agent_cwd()) / ".metodoloji" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            with (log_dir / "hermes-stop.log").open("a", encoding="utf-8") as handle:
                handle.write(f"--- {session_id}\n{report}\n")
        except Exception:
            logger.info("metodoloji session report: %s", report)
    with _STATE_LOCK:
        _SESSIONS.pop(session_id or "_", None)
    return None


# --------------------------------------------------------------------------- #
# slash commands (they run in the gateway and must RETURN their text)
# --------------------------------------------------------------------------- #
def _render_digest(digest: dict) -> str:
    """Human summary of orient.py's digest (chat-sized, ordered by urgency)."""
    lines = [
        f"Metodoloji {digest.get('plugin_version') or ''} — "
        f"{digest.get('project_root') or '?'}".rstrip()
    ]
    skeleton = digest.get("skeleton") or {}
    if skeleton:
        marker = (skeleton.get("marker") or {}).get("initialized_at")
        state = "installed" + (f" (init {marker})" if marker else "") if skeleton.get("installed") else "NOT installed"
        if skeleton.get("problems"):
            state += f" — {len(skeleton['problems'])} problem(s)"
        lines.append(f"- skeleton: {state}")
    records = digest.get("records") or {}
    if records:
        parts = [f"{k}={v.get('count', 0) if isinstance(v, dict) else v}" for k, v in records.items()]
        lines.append("- records: " + ", ".join(parts))
    gate = digest.get("gate_key") or {}
    if gate:
        lines.append(f"- gate key: {'present' if gate.get('present') else 'MISSING (run /metodoloji-verify action=check_secret)'}")
    handoffs = digest.get("handoffs") or {}
    if handoffs:
        lines.append(f"- hand-offs waiting: {handoffs.get('total', 0)}")
    plan = digest.get("plan") or {}
    if plan.get("line"):
        lines.append(f"- plan: {plan['line']}")
    sprint = digest.get("sprint") or {}
    if sprint.get("found"):
        lines.append(f"- sprint: {sprint.get('path') or 'found'}")
    for step in (digest.get("next_steps") or [])[:3]:
        lines.append(f"- next: {step}")
    return "\n".join(lines)


def _cmd_json(result: str) -> str:
    """Render a tool handler's JSON answer as compact chat text."""
    try:
        data = json.loads(result)
    except ValueError:
        return result
    if not data.get("success"):
        return f"metodoloji: {data.get('error') or data.get('stderr') or 'failed'}"
    digest = data.get("digest")
    if isinstance(digest, dict):
        return _render_digest(digest)
    install = data.get("install")
    if isinstance(install, dict):
        created = install.get("directories_created") or []
        copied = install.get("templates_copied") or []
        skipped = install.get("templates_skipped") or []
        marker = install.get("marker") or {}
        lines = [f"Metodoloji skeleton — {install.get('root') or data.get('project_root')}"]
        lines.append(f"- directories created: {len(created)}" if created else "- directories: already present")
        lines.append(f"- templates copied: {len(copied)}, kept existing: {len(skipped)}")
        lines.append(f"- marker: {'written' if install.get('marker_written') else 'already present'}"
                     + (f" (init {marker.get('initialized_at')})" if marker.get("initialized_at") else ""))
        lines.append("- next: /metodoloji-audit")
        return "\n".join(lines)
    for key in ("status", "verdict", "next_step", "summary_text"):
        if data.get(key):
            return str(data[key])
    return json.dumps(data, ensure_ascii=False)[:2000]


def _command_status(raw_args: str) -> str:
    project = raw_args.strip().split()[0] if raw_args.strip() else ""
    return _cmd_json(handle_orient({"project_root": project} if project else {}))


def _command_init(raw_args: str) -> str:
    force = "--force" in raw_args.split()
    project = next((a for a in raw_args.split() if not a.startswith("-")), "")
    params: dict = {"force": force}
    if project:
        params["project_root"] = project
    return _cmd_json(handle_init(params))


def _command_verify(raw_args: str) -> str:
    record = raw_args.strip()
    if not record:
        return "Usage: /metodoloji-verify <path-to-record.md>"
    return _cmd_json(handle_verify({"record": record, "action": "verify"}))


def _command_audit(raw_args: str) -> str:
    scope = "full" if "full" in raw_args.split() else "records"
    return _cmd_json(handle_audit({"scope": scope}))


def _command_gate_setup(_raw_args: str) -> str:
    """One-time gate key creation — the 4th canonical command.

    Mirrors `/metodoloji:gate-setup`: check first, generate only when missing.
    Re-running `--init-secret` on a machine that already has a key silently
    invalidates every token the first key issued, so "already installed" is a
    success, not a reason to try again.
    """
    status = json.loads(handle_key({"action": "status"}))
    if status.get("present"):
        lines = [line for line in (status.get("stdout") or "").splitlines() if line.strip()]
        return ("Gate key already installed — not regenerating (a second key would "
                "invalidate every existing token).\n" + "\n".join(lines)).strip()
    created = json.loads(handle_key({"action": "init"}))
    if created.get("success") or created.get("present"):
        return ("Gate key created (~/.bmad/gate-key, 0600, outside the repo).\n"
                + (created.get("stdout") or "").strip()).strip()
    return ("Gate key setup failed: "
            + str(created.get("error") or created.get("stderr") or "unknown error"))


COMMANDS = (
    ("metodoloji", _command_status, "Metodoloji orientation digest: records, skeleton, gate state."),
    ("metodoloji-init", _command_init, "Install the methodology record skeleton ([--force] [project])."),
    ("metodoloji-gate-setup", _command_gate_setup, "Create the machine-local gate key once (never regenerated)."),
    ("metodoloji-verify", _command_verify, "Verify an experiment record's HMAC gate token."),
    ("metodoloji-audit", _command_audit, "Methodology health check ([full] runs check-plugin.sh)."),
)


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #
def _settings_from(ctx) -> dict:
    """Read plugin settings declared in plugin.yaml's config_schema."""
    defaults = dict(OPTIONS)
    settings = {}
    for key, default in defaults.items():
        try:
            settings[key] = ctx.get_config(key, default)
        except Exception:
            settings[key] = default
    return settings


def register(ctx) -> None:
    """Wire tools, hooks, skills and slash commands into Hermes."""
    configure(_settings_from(ctx))
    register_tools(ctx)

    ctx.register_hook("pre_tool_call", on_pre_tool_call)
    ctx.register_hook("post_tool_call", on_post_tool_call)
    ctx.register_hook("on_session_start", on_session_start)
    ctx.register_hook("pre_llm_call", on_pre_llm_call)
    ctx.register_hook("on_session_end", on_session_end)
    # Always registered, no-op unless `nudge_on_stop` is on: Hermes only runs
    # the pre_verify path when at least one callback exists, and an honestly
    # declared manifest beats a hook that appears/disappears with config.
    ctx.register_hook("pre_verify", on_pre_verify)

    register_skills(ctx)

    for name, handler, description in COMMANDS:
        try:
            ctx.register_command(name=name, handler=handler, description=description)
        except Exception as exc:
            logger.warning("metodoloji: command /%s not registered (%s)", name, exc)

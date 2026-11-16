"""User-facing workflow notices — the human channel (E-175).

Every workflow surface used to speak to the model: `advisory.peek` is injected
into the session context, every CLI command prints JSON to the agent, and the
Stop report rides `additionalContext`. The person driving the session saw none
of it, so a run could advance stage by stage with nobody at the keyboard
knowing — reported 2026-11-16 on a real project.

This module renders the same computed position as ONE bounded line for the
harness's user channel (`systemMessage`). It is read-only and fail-open exactly
like `advisory`: it never writes state, never advances a stage, and any failure
yields "" so the session opens as before. The text is a fixed template; only
the position is interpolated, and it follows `core.communication_language`.
"""

from __future__ import annotations

import importlib.util
import pathlib

from . import advisory

# A status line, not a report: one screen line, clipped by construction.
MAX_CHARS = 300

_TEMPLATES = {
    "session": {
        "tr": "Metodoloji zinciri — {position}",
        "en": "Methodology chain — {position}",
    },
    # Neutral, because the same channel carries a block or a refusal: the line
    # must describe the position, never claim an advance that did not happen.
    "turn": {
        "tr": "Zincir — {position}",
        "en": "Chain — {position}",
    },
    "stop": {
        "tr": "Zincir durumu — {position}",
        "en": "Chain status — {position}",
    },
}


def _language(project_root: str) -> str:
    """`tr` when the project speaks Turkish, else `en` (fail-open)."""
    try:
        path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "resolve_config.py"
        spec = importlib.util.spec_from_file_location("_notice_resolve_config", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        core = module.resolve(
            project_root, keys=["core.communication_language"])
        value = str(core.get("core.communication_language") or "")
    except Exception:
        return "en"
    return "tr" if value.strip().lower().startswith("turk") else "en"


def _line(kind: str, project_root: str) -> str:
    position = advisory.peek(project_root, limit=1)
    if not position:
        return ""
    text = _TEMPLATES[kind][_language(project_root)].format(position=position)
    text = " ".join(text.split())
    return text if len(text) <= MAX_CHARS else text[: MAX_CHARS - 1].rstrip() + "…"


def session_line(project_root: str) -> str:
    """The position at session start — "" when nothing is running."""
    return _line("session", project_root)


def turn_line(project_root: str) -> str:
    """The position after a kernel mutation — "" when nothing is running."""
    return _line("turn", project_root)


def stop_line(project_root: str) -> str:
    """The position at session close — "" when nothing is running."""
    return _line("stop", project_root)

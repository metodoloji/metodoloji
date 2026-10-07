#!/usr/bin/env python3
"""BMAD hooks engine — main entry point.

This module provides the entry point for OpenHands hooks.
It delegates to the appropriate handler based on the hook type.
"""

import json
import os
import sys

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Lazy imports (Faz 1): every hook process used to import ALL gate modules
# (guard+quality+deploy+audit+stop, incl. blackboard) even though it runs exactly
# one of them. Import per HOOK_TYPE instead — the common pre path skips
# audit/stop/blackboard entirely. check-plugin.sh invokes `main.py <mode>`
# positionally, hook-entry.sh passes HOOK_TYPE env; both are honored here.
# (session_start imports blackboard lazily for its read-only handoff peek;
# pre/guard stay blackboard-free.)
_LAZY_IMPORTS = {
    "pre": ("modules.guard", "pre"),
    "guard": ("modules.guard", "guard"),
    "quality": ("modules.guard", "quality"),
    "deploy": ("modules.guard", "deploy"),
    "audit": ("modules.audit", "audit"),
    "session_start": ("modules.audit", "session_start"),
    "stop": ("modules.stop", "stop"),
}


def _resolve_hook_type() -> str:
    """HOOK_TYPE env wins; a positional non-flag argv is the direct-call mode.

    hook-entry.sh passes the mode via HOOK_TYPE env (invocation:
      python3 main.py --runtime=openhands  → sys.argv[1] is a FLAG, not a mode).
    Direct calls may also pass the mode positionally:  main.py guard --runtime=openhands
    """
    hook_type = os.environ.get("HOOK_TYPE", "")
    for arg in sys.argv[1:]:
        if arg.startswith("--runtime="):
            os.environ["METODOLOJI_RUNTIME"] = arg.split("=", 1)[1]
        elif not arg.startswith("-"):
            hook_type = arg
    return hook_type


def _load_handler(hook_type: str):
    """Import only the module needed for hook_type; return the handler fn."""
    import importlib

    mod_name, fn_name = _LAZY_IMPORTS[hook_type]
    mod = importlib.import_module(mod_name)
    return getattr(mod, fn_name)


def _deny_no_input(hook_type: str) -> None:
    if hook_type == "stop":
        print(json.dumps({
            "decision": "block",
            "reason": "Methodology hook received no input — fail-closed blocked.",
            "hookSpecificOutput": {"hookEventName": "Stop"},
        }))
    else:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "Methodology hook received no input — fail-closed blocked.",
            }
        }))


def main():
    """Main entry point for hook execution."""
    # Resolve the hook type BEFORE importing any gate module — only the
    # matching module is imported below.
    hook_type = _resolve_hook_type()

    handler = None
    if hook_type in _LAZY_IMPORTS:
        try:
            handler = _load_handler(hook_type)
        except (ImportError, AttributeError) as exc:
            sys.stderr.write(f"metodoloji-hooks: handler import failed ({hook_type}) — {exc}\n")
            handler = None

    # Read JSON input from stdin. Empty stdin is only fail-open for
    # non-blocking hooks; guard/stop stay fail-closed (their _fail path in
    # hook-entry.sh already denies, this keeps direct-engine calls safe).
    try:
        json_in = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError, EOFError):
        if hook_type in ("stop", "guard", "pre"):
            _deny_no_input(hook_type)
            return
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": os.environ.get("HOOK_TYPE", "PreToolUse"),
                "permissionDecision": "allow",
            }
        }))
        return

    # Execute the handler. A failed lazy import on a fail-closed hook denies
    # (same policy as hook-entry.sh _fail); on fail-open hooks it allows.
    if handler is None:
        if hook_type in ("stop", "guard", "pre"):
            _deny_no_input(hook_type)
            return
        result = {"decision": "allow"}
    elif hook_type:
        result = handler(json_in)
    else:
        # Unknown hook type - allow
        result = {"decision": "allow"}

    # Output result — Claude Code v2 schema: hookSpecificOutput wrapper.
    # Schema differs per event type:
    #   PreToolUse (pre/guard/quality/deploy) → permissionDecision
    #   PostToolUse (audit)               → additionalContext
    #   Stop (stop)                       → decision block/reason (loop-safe)
    #   SessionStart                      → additionalContext
    event_name = hook_type or "PreToolUse"
    if hook_type in ("guard", "quality", "deploy", "pre"):
        # PreToolUse: permissionDecision controls allow/deny/ask
        hso = {
            "hookEventName": "PreToolUse",
            "permissionDecision": result.get("decision", "allow"),
        }
        reason = result.get("reason")
        if reason:
            hso["permissionDecisionReason"] = reason
        warnings = result.get("methodology_warnings")
        if warnings:
            hso["additionalContext"] = "\n".join(warnings)
    elif hook_type == "audit":
        # PostToolUse: additionalContext feeds info back to the model
        hso = {"hookEventName": "PostToolUse"}
        warnings = result.get("methodology_warnings")
        if warnings:
            hso["additionalContext"] = "\n".join(warnings)
    elif hook_type == "stop":
        # Stop: top-level decision block/reason is the documented loop-safe
        # pattern; additionalContext carries the feedback so the model can
        # act on it. stop_hook_active re-fires pass stop_hook_active=true in
        # stdin, which stop() honors by allowing.
        out = {}
        reason = result.get("reason")
        if result.get("decision") == "deny":
            out["decision"] = "block"
            if reason:
                out["reason"] = reason
        hso = {"hookEventName": "Stop"}
        if reason:
            hso["additionalContext"] = reason
        print(json.dumps({**out, "hookSpecificOutput": hso}, ensure_ascii=False))
        return
    elif hook_type == "session_start":
        # SessionStart: context injection only, never blocks.
        hso = {"hookEventName": "SessionStart"}
        ctx = result.get("additionalContext")
        if ctx:
            hso["additionalContext"] = ctx
    else:
        # Unknown hook type — flat allow
        hso = {"hookEventName": event_name, "permissionDecision": "allow"}

    print(json.dumps({"hookSpecificOutput": hso}, ensure_ascii=False))


if __name__ == "__main__":
    main()

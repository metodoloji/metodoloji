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

# Bound hook payloads before JSON decoding. A malformed or unexpectedly large
# tool call must not consume unbounded memory in a long-lived host process.
MAX_HOOK_INPUT_CHARS = 16 * 1024 * 1024


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


def _deny(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))


def _deny_no_input() -> None:
    _deny("Methodology hook received no input — fail-closed blocked.")


def _allow_no_input(hook_type: str) -> None:
    """Return a valid non-blocking envelope when an advisory hook cannot run."""
    event_names = {
        "stop": "Stop",
        "audit": "PostToolUse",
        "session_start": "SessionStart",
    }
    event_name = event_names.get(hook_type, "PreToolUse")
    hso = {"hookEventName": event_name}
    if hook_type == "stop":
        hso["additionalContext"] = "Methodology Stop report unavailable (invalid hook input)."
    elif hook_type in ("quality", "deploy"):
        hso["additionalContext"] = (
            f"Methodology {hook_type} gate received invalid input; "
            "soft mode allowed the call without a gate decision."
        )
    elif event_name == "PreToolUse":
        hso["permissionDecision"] = "allow"
    print(json.dumps({"hookSpecificOutput": hso}, ensure_ascii=False))


def _allow_failed_handler(hook_type: str, detail: str) -> dict:
    """Preserve report-only behavior while surfacing soft-gate failures."""
    if hook_type in ("quality", "deploy"):
        return {
            "decision": "allow",
            "methodology_warnings": [
                f"{hook_type} gate unavailable ({detail}); soft mode allowed the call without a decision."
            ],
        }
    if hook_type == "stop":
        return {"decision": "allow", "reason": "Methodology Stop report unavailable."}
    return {"decision": "allow"}


def _is_fail_closed(hook_type: str) -> bool:
    """Whether a handler failure must block under the active gate settings."""
    if hook_type in ("guard", "pre"):
        return True
    gate_keys = {"quality": "quality_gate", "deploy": "deploy_guard"}
    gate_key = gate_keys.get(hook_type)
    if gate_key is None:
        return False
    try:
        from modules.config import hook_gate_mode
        return hook_gate_mode(gate_key) == "hard"
    except (Exception, SystemExit):
        # If strictness cannot be read, do not let a broken gate pass a
        # commit/deploy hook.
        return True


def _deny_unknown_hook_type() -> None:
    """Fail closed when dispatch is missing or names an unknown hook.

    The shell dispatcher already rejects unknown modes. Keep direct engine
    invocations equally safe: an unrecognized PreToolUse mode must never fall
    through to the generic allow response below.
    """
    _deny("Unknown methodology hook mode — fail-closed blocked.")


def main():
    """Main entry point for hook execution."""
    # Resolve the hook type BEFORE importing any gate module — only the
    # matching module is imported below.
    hook_type = _resolve_hook_type()

    if hook_type not in _LAZY_IMPORTS:
        _deny_unknown_hook_type()
        return

    handler = None
    if hook_type in _LAZY_IMPORTS:
        try:
            handler = _load_handler(hook_type)
        except (Exception, SystemExit) as exc:
            sys.stderr.write(f"metodoloji-hooks: handler import failed ({hook_type}) — {exc}\n")
            handler = None

    # Read JSON input from stdin. Empty stdin follows each hook's failure
    # policy: guard/pre always deny; quality/deploy follow their configured
    # hard/soft mode; audit/session_start stay fail-open.
    # The fail-closed contract covers BOTH bad-input classes: unparseable JSON
    # AND valid JSON that is not an object (array/string/number/null). The
    # second class used to reach the handler and raise inside
    # normalize_hook_input ("'list' object has no attribute 'get'") — a
    # traceback is neither allow nor deny, and a crashed PreToolUse hook can be
    # read by the runner as an allow (fail-open), the exact leak the guard
    # exists to prevent. RecursionError joins the tuple (E-007): a wire payload
    # nested past the parser's limit (CPython 3.14 raises "Stack overflow …
    # while decoding") is a RecursionError, NOT a JSONDecodeError/ValueError —
    # it used to escape json.load uncaught, so at depth ~20k EVERY mode exited
    # 1 with empty stdout (no decision on stop/guard/pre either).
    try:
        raw_input = sys.stdin.read(MAX_HOOK_INPUT_CHARS + 1)
        if len(raw_input) > MAX_HOOK_INPUT_CHARS:
            if _is_fail_closed(hook_type):
                _deny(
                    "Methodology hook input exceeds the 16 Mi character limit "
                    "— fail-closed blocked."
                )
            else:
                _allow_no_input(hook_type)
            return
        json_in = json.loads(raw_input)
    except (json.JSONDecodeError, ValueError, EOFError, OSError, RecursionError):
        json_in = None

    if not isinstance(json_in, dict):
        if _is_fail_closed(hook_type):
            _deny_no_input()
            return
        _allow_no_input(hook_type)
        return

    # Execute the handler. A failed lazy import on a fail-closed hook denies
    # (same policy as hook-entry.sh _fail); on fail-open hooks it allows.
    # A failed lazy import on a known fail-open hook is allowed by policy.
    if handler is None:
        if _is_fail_closed(hook_type):
            _deny(f"Methodology {hook_type} hook handler unavailable — fail-closed blocked.")
            return
        result = _allow_failed_handler(hook_type, "handler unavailable")
    elif hook_type:
        # E-007: a handler exception must still END in a decision — the same
        # policy as bad/no input, including quality/deploy strictness.
        # The probe made the class concrete: a deep routing value raised
        # RecursionError inside _coerce_json_scalar and escaped HERE, so guard,
        # pre, quality, deploy and audit exited 1 with empty stdout — a
        # no-decision turn the runner may read as an allow. Any future handler
        # bug now lands on the same wall: decide, never traceback.
        try:
            result = handler(json_in)
        except (Exception, SystemExit) as exc:
            sys.stderr.write(
                f"metodoloji-hooks: handler crashed ({hook_type}) — "
                f"{type(exc).__name__}: {exc}\n")
            if _is_fail_closed(hook_type):
                _deny(f"Methodology {hook_type} hook failed — fail-closed blocked.")
                return
            result = _allow_failed_handler(hook_type, type(exc).__name__)
    else:
        # Defensive: dispatch was validated above, so this is unreachable.
        _deny_unknown_hook_type()
        return

    # Output result — Claude Code v2 schema: hookSpecificOutput wrapper.
    # Schema differs per event type:
    #   PreToolUse (pre/guard/quality/deploy) → permissionDecision
    #   PostToolUse (audit)               → additionalContext
    #   Stop (stop)                       → report-only; never block
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
        # Stop is report-only: even if the report handler fails, it must never
        # wedge a session close. additionalContext carries the report only.
        reason = result.get("reason")
        hso = {"hookEventName": "Stop"}
        if reason:
            hso["additionalContext"] = reason
        print(json.dumps({"hookSpecificOutput": hso}, ensure_ascii=False))
        return
    elif hook_type == "session_start":
        # SessionStart: context injection only, never blocks.
        hso = {"hookEventName": "SessionStart"}
        ctx = result.get("additionalContext")
        if ctx:
            hso["additionalContext"] = ctx
    else:
        # Defensive: never pass a mode with no known output schema.
        _deny_unknown_hook_type()
        return

    print(json.dumps({"hookSpecificOutput": hso}, ensure_ascii=False))


if __name__ == "__main__":
    main()

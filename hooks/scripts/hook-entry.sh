#!/bin/sh
# hook-entry.sh — single resolution point: find engine, pass to python, apply policy.
# Usage: sh hook-entry.sh <pre|guard|quality|deploy|stop|audit|session_start> [runtime]
# Cross-platform: Windows/macOS/Linux.
# Policies (Claude parity):
#   pre/guard      fail-closed  (engine missing → deny + exit 2)
#   stop           fail-closed, loop-safe (decision block + exit 0; exit 2
#                  re-triggers Stop and wedges the session)
#   quality/deploy fail-open    (engine missing → silent pass)
#   audit/session_start fail-open
SELF=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PLUGIN_ROOT=$(CDPATH= cd -- "$SELF/../.." && pwd)
ENGINE="$PLUGIN_ROOT/hooks/engine/main.py"
MODE="$1"

# Find a working python interpreter (cross-platform).
# Cached (Faz 1): a validated interpreter path is reused while it stays
# executable. Cache lives in TMPDIR (per-user), stale entries are dropped.
_PY_CACHE="${TMPDIR:-/tmp}/metodoloji-py-$USER.cache"
PY=
if [ -n "$USER" ] && [ -f "$_PY_CACHE" ]; then
    _CACHED_PY=$(cat "$_PY_CACHE" 2>/dev/null)
    if [ -n "$_CACHED_PY" ] && command -v "$_CACHED_PY" >/dev/null 2>&1; then
        PY="$_CACHED_PY"
    else
        rm -f "$_PY_CACHE" 2>/dev/null || true
    fi
fi
if [ -z "$PY" ]; then
for c in python3 python py; do
    command -v "$c" >/dev/null 2>&1 && PY="$c" && break
done
fi
if [ -z "$PY" ]; then
    # Last resort: try common Windows paths
    for p in "/c/Python3*/python.exe" "/c/Users/$USER/AppData/Local/Programs/Python/Python3*/python.exe"; do
        for f in $p; do
            [ -x "$f" ] && PY="$f" && break 2
        done
    done
fi
if [ -n "$PY" ] && [ -n "$USER" ]; then
    printf '%s' "$PY" > "$_PY_CACHE" 2>/dev/null || true
fi

# MODE comes from $1 (our own hooks.json, not user input), but pin it to the
# known set anyway so a typo can never interpolate into the JSON output.
_fail() {
    case "$MODE" in
        # Stop uses the loop-safe envelope: decision block + exit 0. Exit 2 on
        # Stop re-triggers the hook and wedges the session (stop_hook_active
        # never propagates on a non-zero exit path).
        stop)
            printf '%s\n' '{"decision":"block","reason":"Methodology hook engine could not run (no python or missing engine) — fail-closed blocked.","hookSpecificOutput":{"hookEventName":"Stop"}}'
            exit 0
            ;;
        guard|pre)
            printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"Methodology hook engine could not run (no python or missing engine) — fail-closed blocked."}}'
            exit 2
            ;;
        quality|deploy|audit|bootstrap|session_start)
            exit 0
            ;;
        *)
            # Unknown mode: treat as guard (fail-closed) — never silently pass
            # a hook we don't recognize.
            printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"Unknown methodology hook mode — fail-closed blocked."}}'
            exit 2
            ;;
    esac
}

if [ -z "$PY" ] || [ ! -f "$ENGINE" ]; then
    _fail
fi

# Set hook type environment variable
export HOOK_TYPE="$MODE"

# Runtime selection: 2nd arg > env > default openhands
RUNTIME="${2:-${METODOLOJI_RUNTIME:-openhands}}"

# Read stdin and pass to engine
exec "$PY" "$ENGINE" --runtime="$RUNTIME"

#!/bin/sh
# hook-entry.sh — single resolution point: find engine, pass to python, apply policy.
# Usage: sh hook-entry.sh <pre|guard|quality|deploy|stop|audit|session_start> [runtime]
# Cross-platform: Windows/macOS/Linux.
# Policies (Claude parity):
#   pre/guard      fail-closed  (engine missing → deny + exit 2)
#   stop           report-only (missing engine cannot block session close)
#   quality/deploy configured   (hard → deny; soft → allow + warning)
#   audit/session_start fail-open
SELF=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PLUGIN_ROOT=$(CDPATH= cd -- "$SELF/../.." && pwd)
ENGINE="$PLUGIN_ROOT/hooks/engine/main.py"
MODE="$1"

# Find a working python interpreter (cross-platform).
PY=
for c in python3 python py; do
    command -v "$c" >/dev/null 2>&1 && PY="$c" && break
done
if [ -z "$PY" ]; then
    # Last resort: try common Windows paths
    for p in "/c/Python3*/python.exe" "/c/Users/$USER/AppData/Local/Programs/Python/Python3*/python.exe"; do
        for f in $p; do
            [ -x "$f" ] && PY="$f" && break 2
        done
    done
fi
# MODE comes from $1 (our own hooks.json, not user input), but pin it to the
# known set anyway so a typo can never interpolate into the JSON output.
_fail() {
    case "$MODE" in
        stop)
            printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"Stop","additionalContext":"Methodology Stop report unavailable (engine could not run)."}}'
            exit 0
            ;;
        guard|pre)
            printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"Methodology hook engine could not run (no python or missing engine) — fail-closed blocked."}}'
            exit 2
            ;;
        quality|deploy)
            if _gate_is_hard "$MODE"; then
                printf '%s\n' "{\"hookSpecificOutput\":{\"hookEventName\":\"PreToolUse\",\"permissionDecision\":\"deny\",\"permissionDecisionReason\":\"Methodology $MODE gate could not run (no python or missing engine) — hard mode blocked.\"}}"
                exit 2
            fi
            printf '%s\n' "{\"hookSpecificOutput\":{\"hookEventName\":\"PreToolUse\",\"permissionDecision\":\"allow\",\"additionalContext\":\"Methodology $MODE gate could not run (no python or missing engine); soft mode allowed the call without a gate decision.\"}}"
            exit 0
            ;;
        audit|bootstrap|session_start)
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

_gate_is_hard() {
    case "$1" in
        quality) _key=quality_gate ;;
        deploy) _key=deploy_guard ;;
        *) return 1 ;;
    esac
    _cfg="$PLUGIN_ROOT/custom/config.toml"
    [ -r "$_cfg" ] || return 1
    awk -v target="$_key" '
        function trim(value) {
            sub(/^[[:space:]]+/, "", value)
            sub(/[[:space:]]+$/, "", value)
            return value
        }
        {
            line = $0
            sub(/[[:space:]]+#.*/, "", line)
            if (trim(line) == "[hooks]") { in_hooks = 1; next }
            if (in_hooks && line ~ /^[[:space:]]*\[/) exit
            if (!in_hooks) next
            equals = index(line, "=")
            if (!equals) next
            name = trim(substr(line, 1, equals - 1))
            value = trim(substr(line, equals + 1))
            gsub(/"/, "", value)
            gsub(sprintf("%c", 39), "", value)
            if (name == target && value == "hard") found = 1
        }
        END { exit(found ? 0 : 1) }
    ' "$_cfg"
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

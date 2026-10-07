#!/bin/sh
# bootstrap.sh — SessionStart: check/create the gate-key and inject short context
# (additionalContext). Non-blocking (fail-open). Cross-platform: Windows/macOS/Linux.
SELF=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SYNCED=$(CDPATH= cd -- "$SELF/../.." && pwd)

WS="${CLAUDE_PROJECT_DIR:-$OPENHANDS_PROJECT_DIR}"
[ -z "$WS" ] && WS=$(pwd)
# Forward slashes are accepted by Git Bash, WSL, PowerShell and Python; a native
# `C:\Users\…` path does NOT survive a POSIX shell — the backslashes collapse
# (`ls C:\Users\me\proj` reads as `ls C:Usersmeproj`), which a real win32
# session hit before every command failed-path retry. Normalize ONCE here so
# filesystem work and every injected path are dialect-safe.
WS=$(printf '%s' "$WS" | tr '\\' '/')

# Find a working python interpreter (cross-platform).
# Cached (Faz 1): shared with hook-entry.sh — a validated interpreter path is
# reused while it stays executable.
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

if [ -z "$PY" ]; then
    printf '%s\n' '{"additionalContext":"METODOLOJI active but python not found — hooks disabled. Install Python 3."}'
    exit 0
fi

# Auto-setup: create gate-key if missing (atomic write — a crashed python
# must never leave a truncated zero-byte key behind).
if [ ! -f "$HOME/.bmad/gate-key" ]; then
    mkdir -p "$HOME/.bmad"
    if "$PY" -c "import secrets; print(secrets.token_hex(32))" > "$HOME/.bmad/gate-key.tmp"; then
        mv "$HOME/.bmad/gate-key.tmp" "$HOME/.bmad/gate-key"
        chmod 600 "$HOME/.bmad/gate-key" 2>/dev/null || true
    else
        rm -f "$HOME/.bmad/gate-key.tmp"
    fi
fi

# Runtime dir: the hook audit trail always lives here, every session.
mkdir -p "$WS/.metodoloji/logs"

# Record-skeleton setup is a ONE-TIME concern (`/metodoloji:init` writes the
# marker below). Re-running the skeleton step and re-suggesting init on every
# session made each downstream skill (bmad-prd, bmad-architecture, …) look like
# it re-initialized the project — the agent saw the init hint in context and
# replayed the whole flow per skill. Marker present → skip the skeleton step and
# stop advertising init; absent → ensure the base dir and point at the one-time
# command.
# The hint names an executable command, not only the slash command: only Claude
# Code exposes `/metodoloji:init`, so a real OpenHands session (2026-09-21,
# /root/mailjs) read "run /metodoloji:init once" at every session start and
# never ran it — the skeleton stayed absent through PRD → architecture →
# spec → epics. skeleton.py is the same install, runnable in any harness.
# Skeleton probes (skeleton.py SKELETON_PROBES — keep the two in step): any ONE
# present proves the skeleton was installed. A skeleton WITHOUT the marker is a
# broken init, not a missing one: the mailjs OpenHands session (2026-09-23) had
# every template in place but no marker, so every session read "Record skeleton
# not installed" and re-advertised a FULL init although only the one-byte marker
# was missing. skeleton.py --install is exactly that repair (idempotent: copies
# nothing new, writes the marker) — the hint must name the drift as drift.
SKELETON_PRESENT=0
for _f in docs/experiments/_template.md docs/development/_template_IR.md docs/development/stories/_template_S.md; do
    [ -f "$WS/$_f" ] && SKELETON_PRESENT=1 && break
done
if [ -f "$WS/.metodoloji/initialized" ]; then
    INIT_HINT="Record skeleton already installed (init is one-time) — do NOT re-run /metodoloji:init."
elif [ "$SKELETON_PRESENT" -eq 1 ]; then
    INIT_HINT="Record skeleton present but .metodoloji/initialized marker is MISSING (broken init) — repair once: python3 \"$SYNCED/bmad/scripts/skeleton.py\" --install (idempotent: copies nothing new, only writes the marker)."
else
    mkdir -p "$WS/docs/experiments"
    INIT_HINT="Record skeleton not installed — run /metodoloji:init once (no slash commands in this harness? run: python3 \"$SYNCED/bmad/scripts/skeleton.py\" --install)."
fi

# {metodoloji-root} is named in the context so no skill has to hunt for it —
# the real session spent turns on `find / -name research-methodology.md` and on
# guessing whether the root was ~/.metodoloji, /root/bmad or the plugin dir,
# even though this line was already printing it.
# Short context: gate-key status + record chain reminder.
if [ -f "$HOME/.bmad/gate-key" ]; then KEY="present"; else KEY="MISSING — python run_experiment.py --init-secret"; fi

# Single python call (Faz 5): engine session_start stamps the marker and
# returns the fixed context sentence, plus the read-only handoff peek
# (PROACTIVE nudge for waiting relay signals — announce-only, never
# consumed). The old scope bridge (METODOLOJI_SCOPE export + a second
# python -c) died with the guard's warn-only scope notices, which were its
# only consumer.
# ponytail: shell vars cross into python via env, never string interpolation
# (a quote in $WS/$SYNCED would break `python -c` and silently empty the scope).
# WS with quote/backslash/dollar is passed through env raw — no shell
# interpolation happens, so no early-exit case is needed.
export METODOLOJI_WS="$WS"
export METODOLOJI_SYNCED="$SYNCED"
export METODOLOJI_KEY="$KEY"
export METODOLOJI_INIT="$INIT_HINT"
"$PY" -c "
import json, sys, os, pathlib

plugin = pathlib.Path(os.environ.get('METODOLOJI_SYNCED', ''))
sys.path.insert(0, str(plugin / 'hooks' / 'engine'))
ws = os.environ.get('METODOLOJI_WS', '')
os.environ['CLAUDE_PROJECT_DIR'] = ws

key = os.environ.get('METODOLOJI_KEY', '')
init_hint = os.environ.get('METODOLOJI_INIT', '')
# Both roots are injected as FORWARD-SLASH strings: that form is accepted by
# Git Bash, PowerShell and Python, so the agent can paste a root into any shell
# tool. os.sep keeps the replacement dialect-free (no literal backslash in this
# double-quoted shell heredoc).
plugin_disp = str(plugin).replace(os.sep, '/')
ws_disp = ws.replace(os.sep, '/')
head = ('METODOLOJI active. {metodoloji-root} = ' + plugin_disp
        + '; {project-root} = ' + ws_disp
        + ' (both literal — use them verbatim, never search the filesystem for them). ')
chain = 'Record chain: E → IR → SP → S → QR → PR. '
tools = ('Tools: call every tool by the exact bare name your harness lists, with '
         'complete arguments, and issue one command per call; if a call is '
         'rejected as unknown, re-issue it immediately with the bare name; write '
         'every path with forward slashes on every OS (a backslash path collapses '
         'in a POSIX shell); use the dialect of the shell tool you call — Bash is '
         'POSIX sh, PowerShell is PowerShell. ')
tail = ('Before writing code you need a scope-matching VERIFIED experiment approval; gate key: '
        + key + '. ' + init_hint)
ctx = head + chain + tools + tail

try:
    from modules.audit import session_start as engine_session_start
    res = engine_session_start({'cwd': ws})
    extra = (res or {}).get('additionalContext', '')
    # The engine's session_start sentence carries the chain itself (audit.py):
    # appending it after our own chain line printed the same sentence twice on
    # one line in a real session. One mention, whoever supplies it — so drop
    # ONLY our duplicate chain line, never the tools clause: the old
    # ctx = head + tail silently discarded the tool contract on every session,
    # because the engine sentence always carries the Record chain itself —
    # leaving the agent with no complete-arguments / platform guidance at all.
    if extra and 'Record chain' in extra and chain in ctx:
        ctx = head + tools + tail
    if extra and extra not in ctx:
        ctx += ' ' + extra
except Exception:
    try:
        from modules.stop import record_session_start
        record_session_start(ws)
    except Exception:
        pass

print(json.dumps({'additionalContext': ctx}))
" 2>/dev/null || printf '%s\n' "{\"additionalContext\":\"METODOLOJI active. {metodoloji-root} = $SYNCED; {project-root} = $WS (both literal — use them verbatim, never search the filesystem for them). Record chain: E → IR → SP → S → QR → PR. Tools: call every tool by the exact bare name your harness lists, with complete arguments; one command per call; if a call is rejected as unknown, re-issue it with the bare name; write every path with forward slashes on every OS (a backslash path collapses in a POSIX shell); use the dialect of the shell tool you call — Bash is POSIX sh, PowerShell is PowerShell. Before writing code you need a scope-matching VERIFIED experiment approval; gate key: $KEY. $INIT_HINT\"}"
exit 0

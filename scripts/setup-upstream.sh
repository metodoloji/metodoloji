#!/usr/bin/env bash
# Install the two upstream projects this kernel exports to, so an export can be checked by the real tools.
#
#   bash scripts/setup-upstream.sh
#
# Everything lands in the project and stays out of git: `vendor/hermit` (a pinned checkout) and
# `.venv-upstream` (hermit-harness installed editable, plus the SkillOpt version Hermit pins). Hermit is installed
# from source, not from PyPI: the 0.1.0 wheel on PyPI has no `hermit/agent` package, so the documented quickstart
# cannot run from it.
#
# Windows: the checkout needs `patches/hermit-0.1.0-windows.patch`. Upstream 0.1.0 imports `fcntl` unconditionally,
# symlinks the `learning_material/current` pointer (a privilege a normal account lacks), keeps Windows environment
# names out of the sandbox's allowlist (`SystemRoot` is stored upper-cased, so Winsock never starts), hands the
# worker its result through `pass_fds` (unsupported on Windows) and treats git's read-only object files as deletable.
set -euo pipefail

HERMIT_REPO="https://github.com/Infini-AI-Lab/hermit.git"
HERMIT_COMMIT="56fb676d30af8257246f345ef4fd745c53ff73c1"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENDOR="$ROOT/vendor/hermit"
VENV="$ROOT/.venv-upstream"
PATCH="$ROOT/patches/hermit-0.1.0-windows.patch"

say() { printf '%s\n' "$*"; }

command -v git >/dev/null || { say "git is required"; exit 1; }
PYTHON="${PYTHON:-python}"
"$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' \
    || { say "Hermit needs Python 3.12+; set PYTHON to a newer interpreter"; exit 1; }

if [ ! -d "$VENDOR/.git" ]; then
    say "cloning hermit at $HERMIT_COMMIT"
    mkdir -p "$ROOT/vendor"
    git clone -q "$HERMIT_REPO" "$VENDOR"
    git -C "$VENDOR" checkout -q "$HERMIT_COMMIT"
else
    say "using the existing checkout at $VENDOR"
fi

if [ -f "$PATCH" ]; then
    if git -C "$VENDOR" apply --check "$PATCH" 2>/dev/null; then
        say "applying $PATCH"
        git -C "$VENDOR" apply "$PATCH"
    elif git -C "$VENDOR" apply --reverse --check "$PATCH" 2>/dev/null; then
        say "the Windows patch is already applied"
    else
        say "warning: $PATCH neither applies nor is applied; the checkout has other local changes"
    fi
fi

if [ ! -x "$VENV/Scripts/python.exe" ] && [ ! -x "$VENV/bin/python" ]; then
    say "creating $VENV"
    "$PYTHON" -m venv "$VENV"
fi
PY="$VENV/Scripts/python.exe"; [ -x "$PY" ] || PY="$VENV/bin/python"

"$PY" -m pip install --quiet --upgrade "pip>=25.1"
say "installing hermit-harness from source with the skillopt dependency group"
(cd "$VENDOR" && "$PY" -m pip install --quiet -e . --group skillopt)

say ""
say "installed:"
"$PY" - <<'PY'
import importlib.metadata as md
for name in ("hermit-harness", "skillopt"):
    try:
        print(f"  {name} {md.version(name)}")
    except md.PackageNotFoundError:
        print(f"  {name} MISSING")
PY
HERMIT="$VENV/Scripts/hermit.exe"; [ -x "$HERMIT" ] || HERMIT="$VENV/bin/hermit"
"$HERMIT" --help >/dev/null && say "  hermit CLI ok: $HERMIT"
say ""
say "Next: bash scripts/verify-upstream.sh"

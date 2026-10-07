#!/bin/sh
# check-techdebt.sh — static quality audit of the docs/development/tech-debt.md and
# templates/tech-debt.md inventories. It mechanizes the root cause of the TD-002
# false-positive monitor: template ↔ live inventory drift, ID collision,
# orphan TODO, P0 limit, paid/active collision.
#
#   1. Template identity (templates/tech-debt.md ↔ docs/development/tech-debt.md)
#   2. Active debt table: unique + sequential IDs (TD-NNN)
#   3. Active P0 count <= 5 (manifesto hard limit)
#   4. No paid/active ID collision
#   5. Orphan TODO: every TD-XXX in a record is referenced in either the active
#      table or the paid table; [TD-XXX] in comments always in the inventory
#
# Usage:  sh scripts/check-techdebt.sh
#            sh scripts/check-techdebt.sh --negtest
#            (negative-test only: inject ID collision + orphan TODO →
#             catch MISS → restore)
# Output:    [OK] / [WARNING] / [ERROR] at the start of each line; overall status at the end.
set -u

PROBLEMS=0

if [ "${1:-}" = "--negtest" ]; then
    SELF=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
    PLUGIN_ROOT=$(CDPATH= cd -- "$SELF/.." && pwd)
    PY=
    for cand in python3 python py; do
        if command -v "$cand" >/dev/null 2>&1; then PY="$cand"; break; fi
    done
    if [ -z "$PY" ]; then
        echo "[ERROR] python3/python/py not found — negative test cannot run." >&2
        exit 1
    fi
    PLUGIN_ROOT="$PLUGIN_ROOT" "$PY" - <<'PY'
import os, re, subprocess, sys
from pathlib import Path

PLUGIN = Path(os.environ["PLUGIN_ROOT"])
td = PLUGIN / "docs" / "development" / "tech-debt.md"
check_script = PLUGIN / "scripts" / "check-techdebt.sh"
total_stages = 3

def run_check() -> subprocess.CompletedProcess:
    return subprocess.run(
        ["sh", str(check_script)],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        cwd=str(PLUGIN),
    )

orig = td.read_text(encoding="utf-8")

# Stage 1/3: Adding a duplicate ID to the active table (TD-010, both active and
# paid) triggers §4 collision WARNING + §3 P0 limit?
print(f"[1/{total_stages}] does §4 catch a collision when a duplicate TD-010 is added to the active table")
# Add a TD-010 row to the active table (as P0); it already exists in the paid table.
# Fallback chain: P0 placeholder → TD-003 (P2) → TD-001 (old assumption)
new_row = "| TD-010 | duplicate-test | test | 2026-08-26 | Test | @test | SP-001 |\n"
broken = re.sub(
    r"(\| —    \| —     \| —             \| —             \| —    \| —      \| —            \|\n)",
    r"\1" + new_row, orig, count=1)
if "TD-010 | duplicate-test" not in broken:
    broken = re.sub(r"(\| TD-003 \|[^\n]+\n)", r"\1" + new_row, orig, count=1)
if "TD-010 | duplicate-test" not in broken:
    broken = re.sub(r"(\| TD-001 \|[^\n]+\n)", r"\1" + new_row, orig, count=1)
td.write_text(broken, encoding="utf-8")
try:
    r = run_check()
    if "TD-010" in r.stdout and "both active and paid" in r.stdout and r.returncode == 1:
        print("  [OK] §4 collision caught, exit=1")
    else:
        print(f"  [ERROR] §4 collision expected, output end: ...{r.stdout[-400:]!r}")
        sys.exit(1)
finally:
    td.write_text(orig, encoding="utf-8")

# Stage 2/3: When the active P0 count is raised to 6, does §3 hard limit kick in?
print(f"[2/{total_stages}] does §3 hard limit kick in when the P0 count becomes 6")
# Find the "—    | —" placeholder row of the P0 section and add 5 new P0 rows.
# If a real P0 row like TD-001 exists, add after it; otherwise add after the
# placeholder.
broken = re.sub(
    r"(\| —    \| —     \| —             \| —             \| —    \| —      \| —            \|\n)",
    r"\1| TD-101 | P0-test-1 | t | 2026-08-26 | Test | @t | SP-001 |\n"
    r"| TD-102 | P0-test-2 | t | 2026-08-26 | Test | @t | SP-001 |\n"
    r"| TD-103 | P0-test-3 | t | 2026-08-26 | Test | @t | SP-001 |\n"
    r"| TD-104 | P0-test-4 | t | 2026-08-26 | Test | @t | SP-001 |\n"
    r"| TD-105 | P0-test-5 | t | 2026-08-26 | Test | @t | SP-001 |\n"
    r"| TD-106 | P0-test-6 | t | 2026-08-26 | Test | @t | SP-001 |\n",
    orig, count=1)
# If no placeholder exists, add after the TD-001 row (old behavior).
if "P0-test-1" not in broken:
    broken = re.sub(
        r"(\| TD-001 \|[^\n]+\n)",
        r"\1| TD-101 | P0-test-1 | t | 2026-08-26 | Test | @t | SP-001 |\n"
        r"| TD-102 | P0-test-2 | t | 2026-08-26 | Test | @t | SP-001 |\n"
        r"| TD-103 | P0-test-3 | t | 2026-08-26 | Test | @t | SP-001 |\n"
        r"| TD-104 | P0-test-4 | t | 2026-08-26 | Test | @t | SP-001 |\n"
        r"| TD-105 | P0-test-5 | t | 2026-08-26 | Test | @t | SP-001 |\n"
        r"| TD-106 | P0-test-6 | t | 2026-08-26 | Test | @t | SP-001 |\n",
        orig, count=1)
td.write_text(broken, encoding="utf-8")
try:
    r = run_check()
    if "Active P0 count 6 > 5" in r.stdout and r.returncode == 1:
        print("  [OK] §3 hard limit caught, exit=1")
    else:
        print(f"  [ERROR] §3 hard limit expected, output end: ...{r.stdout[-400:]!r}")
        sys.exit(1)
finally:
    td.write_text(orig, encoding="utf-8")

# Stage 3/3: Orphan TODO: inject [TD-999] into scratch/ → does §5 catch it?
# (the inventory file is outside §5 scope — writing there would be a legitimate reference)
print(f"[3/{total_stages}] does §5 catch an orphan TODO [TD-999] injected into scratch/")
negtest_artifact = PLUGIN / "scratch" / "_negtest_orphan.py"
artifact_orig = None
if negtest_artifact.exists():
    artifact_orig = negtest_artifact.read_text(encoding="utf-8")
try:
    negtest_artifact.write_text(
        "# TODO: [TD-999] orphan-test-comment (negtest artifact, will be deleted)\n",
        encoding="utf-8")
    r = run_check()
    if "TD-999" in r.stdout and "orphan" in r.stdout and r.returncode == 1:
        print("  [OK] §5 orphan TODO caught, exit=1")
    else:
        print(f"  [ERROR] §5 orphan TODO expected, output end: ...{r.stdout[-400:]!r}")
        sys.exit(1)
finally:
    if artifact_orig is None:
        negtest_artifact.unlink(missing_ok=True)
    else:
        negtest_artifact.write_text(artifact_orig, encoding="utf-8")

print(f"[OK] all {total_stages} negtest stages successful")
sys.exit(0)
PY
    exit $?
fi

# Normal mode: PLUGIN_ROOT and file paths
SELF=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PLUGIN_ROOT=$(CDPATH= cd -- "$SELF/.." && pwd)

TEMPLATE="$PLUGIN_ROOT/templates/tech-debt.md"
LIVE="$PLUGIN_ROOT/docs/development/tech-debt.md"

echo "== 1) Template identity (templates/ ↔ docs/development/) =="
if [ ! -f "$TEMPLATE" ]; then
    echo "[ERROR] $TEMPLATE not found"
    PROBLEMS=$((PROBLEMS + 1))
elif [ ! -f "$LIVE" ]; then
    echo "[ERROR] $LIVE not found"
    PROBLEMS=$((PROBLEMS + 1))
elif diff -q "$TEMPLATE" "$LIVE" >/dev/null 2>&1; then
    echo "[OK]   templates/tech-debt.md ↔ docs/development/tech-debt.md identical"
else
    echo "[ERROR] templates/tech-debt.md ↔ docs/development/tech-debt.md DRIFT (files differ)"
    PROBLEMS=$((PROBLEMS + 1))
fi

if [ ! -f "$LIVE" ]; then
    echo
    echo "STATUS: $PROBLEMS problems found (LIVE file missing, later sections skipped)"
    [ "$PROBLEMS" -eq 0 ] && exit 0 || exit 1
fi

echo "== 2) Active debt table: are IDs unique and sequential? =="
# Active table: between "## Active Technical Debts" (or legacy "## Aktif Teknik Borçlar")
# and "## Paid Debts" (or legacy "## Ödenmiş Borçlar")
ACTIVE_IDS=$(awk '/^## Active Technical Debts|^## Aktif Teknik Borçlar/{flag=1; next} /^## Paid Debts|^## Ödenmiş Borçlar/{flag=0} flag && /^\| TD-/{print $2}' "$LIVE" | sed 's/|//g')
DUPES=$(echo "$ACTIVE_IDS" | sort | uniq -d)
if [ -n "$DUPES" ]; then
    echo "[ERROR] duplicate ID in active table: $(echo $DUPES | tr '\n' ' ')"
    PROBLEMS=$((PROBLEMS + 1))
else
    echo "[OK]   no duplicate IDs in active table ($(echo "$ACTIVE_IDS" | wc -l | tr -d ' ') unique)"
fi
# Sequential: is TD-NNN numerically increasing?
SORTED=$(echo "$ACTIVE_IDS" | sort -t- -k2 -n)
if [ "$ACTIVE_IDS" = "$SORTED" ]; then
    echo "[OK]   active table IDs numerically sequential"
else
    echo "[WARNING] active table IDs not sequential (expected ascending TD-NNN)"
    PROBLEMS=$((PROBLEMS + 1))
fi

echo "== 3) Active P0 count hard limit (<= 5) =="
# P0 rows: | TD-... | rows inside the "### Critical Priority" (or legacy "### Kritik Öncelik") section
P0_COUNT=$(awk '/^### Critical Priority|^### Kritik Öncelik/{flag=1; next} /^### /{flag=0} flag && /^\| TD-/{count++} END{print count+0}' "$LIVE")
if [ "$P0_COUNT" -le 5 ]; then
    echo "[OK]   active P0 = $P0_COUNT (<= 5 limit)"
else
    echo "[ERROR] Active P0 count $P0_COUNT > 5 — manifesto hard limit exceeded (no new features taken)"
    PROBLEMS=$((PROBLEMS + 1))
fi

echo "== 4) No active/paid ID collision =="
PAID_IDS=$(awk '/^## Paid Debts|^## Ödenmiş Borçlar/{flag=1; next} flag && /^\| TD-/{print $2}' "$LIVE" | sed 's/|//g')
# POSIX-compatible intersection: sort both lists, comm from temp files
TMPA=$(mktemp); TMPB=$(mktemp)
trap 'rm -f "$TMPA" "$TMPB"' EXIT
echo "$ACTIVE_IDS" | sort -u > "$TMPA"
echo "$PAID_IDS"   | sort -u > "$TMPB"
OVERLAP=$(comm -12 "$TMPA" "$TMPB" | tr -d '[:space:]')
if [ -z "$OVERLAP" ]; then
    echo "[OK]   active and paid tables are disjoint (no TD-XXX collision)"
else
    echo "[ERROR] TD-XXX both active and paid: $(echo $OVERLAP | tr '\n' ' ')"
    PROBLEMS=$((PROBLEMS + 1))
fi

echo "== 5) No orphan TODO [TD-XXX] referencing outside the inventory =="
# TODO comment standard: "# TODO: [TD-XXX]" or "<!-- TODO: [TD-XXX]"
# All IDs in the inventory
ALL_IDS=$(printf '%s\n%s\n' "$ACTIVE_IDS" "$PAID_IDS" | sort -u | grep -v '^$' || true)
# IDs referenced in TODOs (build artifacts and scratch dirs excluded)
TODO_IDS=$(grep -rhoE --binary-files=without-match \
    'TODO:[[:space:]]*\[TD-[0-9]+\]' \
    --exclude-dir=__pycache__ --exclude='*.pyc' \
    --exclude-dir=_generated_splits --exclude-dir=.metodoloji \
    "$PLUGIN_ROOT/scratch/" "$PLUGIN_ROOT/custom/" 2>/dev/null \
    | sed -E 's/.*\[(TD-[0-9]+)\].*/\1/' | sort -u)
if [ -z "$TODO_IDS" ]; then
    echo "[OK]   no TODO comments in scanned dirs (scratch/, custom/)"
else
    TMPC=$(mktemp); TMPD=$(mktemp)
    trap 'rm -f "$TMPC" "$TMPD"' EXIT
    echo "$TODO_IDS" > "$TMPC"
    echo "$ALL_IDS"   > "$TMPD"
    ORPHANS=$(comm -23 "$TMPC" "$TMPD" | tr -d '[:space:]')
    if [ -n "$ORPHANS" ]; then
        echo "[ERROR] orphan TODO (not in inventory): $(echo $ORPHANS | tr '\n' ' ')"
        PROBLEMS=$((PROBLEMS + 1))
    else
        echo "[OK]   all TODO [TD-XXX] are recorded in the inventory"
    fi
fi

echo
if [ "$PROBLEMS" -eq 0 ]; then
    echo "STATUS: HEALTHY (tech-debt inventory intact)"
    exit 0
else
    echo "STATUS: $PROBLEMS problems found"
    exit 1
fi

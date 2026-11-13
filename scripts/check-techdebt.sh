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
#   5. Orphan TODO: every [TD-XXX] marker in the shipped tree is referenced in
#      either the active table or the paid table (the inventory pair and this
#      auditor are excluded — they carry the standard's own example ids)
#   6. Malformed reference: every bare TD-NNN token equals an inventory id
#      (e.g. a lone `TD-14` where the inventory records `TD-014`)
#   7. The live ledger carries no template placeholder (and the shipped
#      template still carries at least one — it is a template, not a ledger)
#   8. The metrics block agrees with the tables it summarizes: active counts per
#      priority, the paid figure over the last three recorded sprints, one trend
#      row per sprint record, and the trend's final total. A wrong NUMBER is not
#      a placeholder, which is why §7 alone left the reporting half unread.
#
# Usage:  sh scripts/check-techdebt.sh
#            sh scripts/check-techdebt.sh --negtest
#            (negative-test only: inject ID collision + P0 overflow + orphan
#             TODO + malformed TD reference + live placeholder + wrong metrics
#             total → catch MISS → restore)
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
total_stages = 6

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

# Stage 3/4: Orphan TODO: inject [TD-999] into scratch/ → does §5 catch it?
# (the inverse — writing a recorded id into the inventory — is the healthy case)
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

# Stage 4/4: Malformed reference: inject a bare `TD-99` into scratch/ → does §6
# catch it? (a bare token is not an orphan TODO, so §5 must stay silent).
print(f"[4/{total_stages}] does §6 catch a malformed TD reference TD-99 injected into scratch/")
negtest_artifact = PLUGIN / "scratch" / "_negtest_dangling.py"
artifact_orig = None
if negtest_artifact.exists():
    artifact_orig = negtest_artifact.read_text(encoding="utf-8")
try:
    negtest_artifact.write_text(
        "# bound by TD-99 (negtest artifact, will be deleted)\n",
        encoding="utf-8")
    r = run_check()
    if ("TD-99" in r.stdout and "not in the inventory" in r.stdout
            and "orphan TODO (not in inventory)" not in r.stdout
            and r.returncode == 1):
        print("  [OK] §6 malformed reference caught, exit=1")
    else:
        print(f"  [ERROR] §6 malformed reference expected, output end: ...{r.stdout[-400:]!r}")
        sys.exit(1)
finally:
    if artifact_orig is None:
        negtest_artifact.unlink(missing_ok=True)
    else:
        negtest_artifact.write_text(artifact_orig, encoding="utf-8")

# Stage 5/5: Placeholder: append a template slot to the live ledger → does §7
# catch it? (E-116: the live ledger owns the content, the template owns the
# shape; a `[X items]` left behind means the reporting half was never read.)
print(f"[5/{total_stages}] does §7 catch a template placeholder injected into the live ledger")
td.write_text(orig + "\n- **Total active debt:** [X items]\n", encoding="utf-8")
try:
    r = run_check()
    if ("placeholder" in r.stdout.lower() and "[X items]" in r.stdout
            and r.returncode == 1):
        print("  [OK] §7 placeholder caught, exit=1")
    else:
        print(f"  [ERROR] §7 placeholder expected, output end: ...{r.stdout[-400:]!r}")
        sys.exit(1)
finally:
    td.write_text(orig, encoding="utf-8")

# Stage 6/6: Metrics vs tables. The reporting half used to be unread: a WRONG
# number is not a placeholder, so §7 could stay green over a block that
# disagreed with the tables it summarizes.
print(f"[6/{total_stages}] does §8 catch a metrics block that disagrees with the tables")
td.write_text(re.sub(r"(?m)^- \*\*Total active debt:\*\* \d+ items?",
                     "- **Total active debt:** 3 items", orig, count=1),
              encoding="utf-8")
try:
    r = run_check()
    if ("[ERROR] metrics" in r.stdout and "Total active debt says 3" in r.stdout
            and r.returncode == 1):
        print("  [OK] §8 metrics disagreement caught, exit=1")
    else:
        print(f"  [ERROR] §8 metrics disagreement expected, output end: ...{r.stdout[-400:]!r}")
        sys.exit(1)
finally:
    td.write_text(orig, encoding="utf-8")

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

# §8 derives the metrics block from the tables, which needs more than awk can
# carry honestly (per-subsection counts, a sprint list, a trend table). The
# whole audit fails closed if no interpreter exists: a section that silently
# skips is the "claim nothing measures" shape §8 exists to remove.
PY=
for cand in python3 python py; do
    if command -v "$cand" >/dev/null 2>&1; then PY="$cand"; break; fi
done

echo "== 1) Ledger structure matches the shipped template =="
if [ ! -f "$TEMPLATE" ]; then
    echo "[ERROR] $TEMPLATE not found"
    PROBLEMS=$((PROBLEMS + 1))
elif [ ! -f "$LIVE" ]; then
    echo "[ERROR] $LIVE not found"
    PROBLEMS=$((PROBLEMS + 1))
else
    # TD-017 / E-116: the template owns the SHAPE, the live ledger owns the
    # CONTENT. Comparing bytes forced the shipped template to carry this repo's
    # debt history (or the live ledger to keep the template's placeholders), so
    # the audit could stay HEALTHY while the metrics block read `[X items]`.
    # Structure = the heading sequence plus every table's header row (which is
    # what makes a column set drift visible); the prose and the rows are free.
    structure_of() {
        awk '
            BEGIN { pending = "" }
            {
                if (pending != "") {
                    if ($0 ~ /^\|[[:space:]:|-]+\|[[:space:]]*$/) { print "T:" pending }
                    pending = ""
                }
                if ($0 ~ /^#/) { print "H:" $0; next }
                if ($0 ~ /^\|/) { pending = $0 }
            }
        ' "$1"
    }
    TEMPLATE_STRUCT=$(mktemp)
    LIVE_STRUCT=$(mktemp)
    structure_of "$TEMPLATE" > "$TEMPLATE_STRUCT"
    structure_of "$LIVE" > "$LIVE_STRUCT"
    if diff -q "$TEMPLATE_STRUCT" "$LIVE_STRUCT" >/dev/null 2>&1; then
        echo "[OK]   docs/development/tech-debt.md follows templates/tech-debt.md (headings + table columns)"
    else
        echo "[ERROR] ledger structure drifted from templates/tech-debt.md:"
        diff "$TEMPLATE_STRUCT" "$LIVE_STRUCT" | sed -n '1,20p' | sed 's/^/         /'
        PROBLEMS=$((PROBLEMS + 1))
    fi
    rm -f "$TEMPLATE_STRUCT" "$LIVE_STRUCT"
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
ACTIVE_COUNT=0
[ -n "$ACTIVE_IDS" ] && ACTIVE_COUNT=$(printf '%s\n' "$ACTIVE_IDS" | wc -l | tr -d ' ')
DUPES=$(echo "$ACTIVE_IDS" | sort | uniq -d)
if [ -n "$DUPES" ]; then
    echo "[ERROR] duplicate ID in active table: $(echo $DUPES | tr '\n' ' ')"
    PROBLEMS=$((PROBLEMS + 1))
else
    echo "[OK]   no duplicate IDs in active table ($ACTIVE_COUNT unique)"
fi
# ORDER, not consecutiveness: ids are allocated across active+paid, so two
# active rows can legitimately be non-consecutive when a debt between them was
# already paid. The message used to say "sequential", which reads as "must be
# consecutive" and pointed a maintainer at a rule the check does not hold.
if [ "$ACTIVE_COUNT" -le 1 ]; then
    echo "[OK]   active table has $ACTIVE_COUNT row(s) — order check is vacuous"
else
    SORTED=$(printf '%s\n' "$ACTIVE_IDS" | sort -t- -k2 -n)
    if [ "$ACTIVE_IDS" = "$SORTED" ]; then
        echo "[OK]   active table IDs are in ascending order"
    else
        echo "[WARNING] active table IDs are not in ascending TD-NNN order"
        PROBLEMS=$((PROBLEMS + 1))
    fi
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

echo "== 5) No orphan TODO [TD-XXX] in the shipped code or free surfaces =="
# The debt standard puts the marker in the CODE ("# TODO: [TD-XXX]"), so the
# scan covers the code trees (hooks/ scripts/ bmad/ skills/) plus the free
# surfaces (custom/ scratch/) — not docs/ (prose examples like TD-042), not
# templates/, and not test directories (which embed synthetic ids by design).
# This auditor is excluded by name (its --negtest injects ids).
ALL_IDS=$(printf '%s\n%s\n' "$ACTIVE_IDS" "$PAID_IDS" | sort -u | grep -v '^$' || true)
SCAN_EXCLUDES="--exclude-dir=__pycache__ --exclude-dir=tests --exclude=check-techdebt.sh"
SCAN_ROOTS="$PLUGIN_ROOT/hooks $PLUGIN_ROOT/scripts $PLUGIN_ROOT/bmad $PLUGIN_ROOT/skills $PLUGIN_ROOT/custom $PLUGIN_ROOT/scratch"
# IDs referenced in TODO markers (code trees + free surfaces only)
TODO_IDS=$(grep -rhoE --binary-files=without-match $SCAN_EXCLUDES \
    'TODO:[[:space:]]*\[TD-[0-9]+\]' \
    $SCAN_ROOTS 2>/dev/null \
    | sed -E 's/.*\[(TD-[0-9]+)\].*/\1/' | sort -u)
if [ -z "$TODO_IDS" ]; then
    echo "[OK]   no TODO comments in the scanned code/free trees"
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

echo "== 6) No malformed TD reference (every TD-NNN must be an inventory id) =="
# A bare reference that drops the zero padding names debt the inventory does
# not contain (it records the padded form): a mistyped or dangling id is drift
# the orphan-TODO scan above cannot see, because that scan matches only the
# `TODO: [..]` shape. Same scope as §5 (code + free surfaces).
ALL_IDS_FLAT=$(printf '%s' "$ALL_IDS" | tr '\n' ' ')
REF_IDS=$(grep -rhoE --binary-files=without-match $SCAN_EXCLUDES \
    'TD-[0-9]+' $SCAN_ROOTS 2>/dev/null | sort -u)
DANGLING=
for id in $REF_IDS; do
    case " $ALL_IDS_FLAT " in
        *" $id "*) : ;;
        *) DANGLING="$DANGLING $id" ;;
    esac
done
if [ -n "$DANGLING" ]; then
    echo "[ERROR] TD reference not in the inventory:$DANGLING"
    PROBLEMS=$((PROBLEMS + 1))
else
    echo "[OK]   every TD reference matches an inventory id"
fi

echo "== 7) Live ledger carries no template placeholders =="
# E-116: the template is a template, so it keeps `[X items]`-style slots; the
# live ledger has to replace every one of them with a real number. Before this
# check the reporting half was never read by anything.
PLACEHOLDER_RE='\[(X|Y|Z|W|V) [^]]*\]|\[(TBD|PLACEHOLDER)[^]]*\]|\[YYYY-MM-DD\]|\[20% or [^]]*\]'
LEFTOVERS=$(grep -nE "$PLACEHOLDER_RE" "$LIVE" 2>/dev/null || true)
if [ -z "$LEFTOVERS" ]; then
    echo "[OK]   no template placeholders in the live ledger"
else
    echo "[ERROR] live ledger still carries template placeholders:"
    printf '%s\n' "$LEFTOVERS" | sed -n '1,10p' | sed 's/^/         /'
    PROBLEMS=$((PROBLEMS + 1))
fi
if grep -qE "$PLACEHOLDER_RE" "$TEMPLATE" 2>/dev/null; then
    echo "[OK]   templates/tech-debt.md keeps its placeholders"
else
    echo "[ERROR] templates/tech-debt.md carries no placeholder — it is a filled ledger, not a template"
    PROBLEMS=$((PROBLEMS + 1))
fi

echo "== 8) The metrics block agrees with the tables it summarizes =="
# The reporting half used to be unread: §7 catches a placeholder, but a WRONG
# number is not a placeholder. `Total active debt: 0` is a perfectly valid
# sentence next to a full active table, and the trend table can stop at a
# sprint that has since been superseded. Every claim below is derived from the
# ledger's own rows, so the block cannot drift from what it summarizes.
if [ -z "$PY" ]; then
    echo "[ERROR] no python interpreter — the metrics cannot be derived"
    PROBLEMS=$((PROBLEMS + 1))
else
    "$PY" - "$LIVE" "$PLUGIN_ROOT/docs/development" <<'PYEOF'
import pathlib
import re
import sys

live = pathlib.Path(sys.argv[1])
sprint_dir = pathlib.Path(sys.argv[2])
text = live.read_text(encoding="utf-8", errors="replace")
problems: list[str] = []


def section(start_pat: str, end_pat: str) -> str:
    start = re.search(start_pat, text, re.MULTILINE)
    if not start:
        return ""
    end = re.search(end_pat, text[start.end():], re.MULTILINE)
    return text[start.end():start.end() + (end.start() if end else len(text))]


# --- tables ---------------------------------------------------------------
active = section(r"^## Active Technical Debts?\b|^## Aktif Teknik Borçlar",
                 r"^## ")
paid = section(r"^## Paid Debts\b|^## Ödenmiş Borçlar", r"^## ")

active_rows = re.findall(r"^\| (TD-\d{3}) ", active, re.MULTILINE)
priorities = {}
for label, pat in (("P0", r"^### Critical Priority|^### Kritik Öncelik"),
                   ("P1", r"^### High Priority|^### Yüksek Öncelik"),
                   ("P2", r"^### Medium Priority|^### Orta Öncelik"),
                   ("P3", r"^### Low Priority|^### Düşük Öncelik")):
    chunk = section(pat, r"^### |^## ")
    priorities[label] = len(re.findall(r"^\| TD-\d{3} ", chunk, re.MULTILINE))

# --- claimed numbers ------------------------------------------------------
def claimed_int(pattern: str, what: str) -> int | None:
    m = re.search(pattern, text, re.MULTILINE)
    if not m:
        problems.append(f"metrics line missing: {what}")
        return None
    return int(m.group(1))


total_claim = claimed_int(r"^- \*\*Total active debt:\*\* (\d+) items?", "Total active debt")
prio_claims = {
    "P0": claimed_int(r"^\s+- P0 \(Critical\): (\d+) items?", "P0 count"),
    "P1": claimed_int(r"^\s+- P1 \(High\): (\d+) items?", "P1 count"),
    "P2": claimed_int(r"^\s+- P2 \(Medium\): (\d+) items?", "P2 count"),
    "P3": claimed_int(r"^\s+- P3 \(Low\): (\d+) items?", "P3 count"),
}

actual_total = len(active_rows)
if total_claim is not None and total_claim != actual_total:
    problems.append(f"Total active debt says {total_claim}, the active table has "
                    f"{actual_total} row(s)")
for key, claim in prio_claims.items():
    if claim is not None and claim != priorities[key]:
        problems.append(f"{key} says {claim}, its subsection has {priorities[key]} row(s)")
if sum(priorities.values()) != actual_total:
    problems.append(f"priority subsections total {sum(priorities.values())} but the "
                    f"active table has {actual_total} row(s) "
                    f"({priorities})")

# --- sprint list and the trend table --------------------------------------
sprints = sorted({p.stem for p in sprint_dir.glob("SP-*.md")
                  if re.fullmatch(r"SP-\d{3,}", p.stem)})
window = sprints[-3:]

# Cells, not a greedy regex: a non-greedy `.*?` pair captures the SECOND and
# THIRD columns (description, solution) and reports the sprint column as
# "solution". The Paid table's shape is fixed by §1, so the id is cell 1 and
# the sprint is the third-from-last cell.
paid_sprints: list[tuple[str, str]] = []
for line in paid.splitlines():
    if not re.match(r"^\| TD-\d{3} ", line):
        continue
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    if len(cells) < 5:
        continue
    paid_sprints.append((cells[0], cells[-2]))  # ID | desc | solution | date | SPRINT | PR/QR
in_window = [tid for tid, sprint in paid_sprints if sprint in window]
paid_claim = claimed_int(
    r"^- \*\*Debt paid in the last 3 sprints:\*\* (\d+) items?", "paid in last 3")
if paid_claim is not None and paid_claim != len(in_window):
    problems.append(f"Debt paid in the last 3 sprints says {paid_claim}, the paid "
                    f"table has {len(in_window)} in {window} ({in_window})")

# A horizontal rule is a line of dashes and nothing else: the table's own
# separator row also starts with `---`, so an unanchored `^---` ended the
# section at the header and every row vanished.
trend = section(r"^### Trend", r"^\*\*How to read|^-{3,}\s*$")
rows = [ln for ln in trend.splitlines() if ln.count("|") >= 4]
labels: list[str] = []
for row in rows:
    first = row.split("|", 1)[0].strip()
    m = re.search(r"SP-\d{3,}", first)
    if m:
        labels.append(m.group(0))
# Every RECORDED sprint must have a row. A row may also describe a sprint that
# predates the record series (the trend is the ledger's whole history, and the
# ledgers earliest sprints have no SP file), but never one the series has not
# reached: a label beyond the last recorded sprint is a typo that would sit in
# the table forever.
def _num(label: str) -> int:
    m = re.search(r"(\d+)", label)
    return int(m.group(1)) if m else -1

highest = max((_num(sp) for sp in sprints), default=-1)
corroborated = set(sprints) | {sp for _tid, sp in paid_sprints if sp}
missing = [sp for sp in sprints if sp not in labels]
phantom = [sp for sp in labels
           if sp not in corroborated and _num(sp) > highest]
if missing:
    problems.append(f"trend table has no row for {missing}")
if phantom:
    problems.append(f"trend table names {phantom}, which has no sprint record")
if rows:
    # The last row's Total Debt is the number a reader takes away.
    tail = [c.strip() for c in rows[-1].strip().strip("|").split("|")]
    if tail and tail[-1].isdigit() and int(tail[-1]) != actual_total:
        problems.append(f"trend table's final Total Debt is {tail[-1]}, the active "
                        f"table has {actual_total} row(s)")

print(f"  active rows: {actual_total} {priorities} · sprints: {len(sprints)} "
      f"· paid in {window or '[]'}: {len(in_window)}")
for p in problems:
    print(f"[ERROR] metrics: {p}")
sys.exit(1 if problems else 0)
PYEOF
    if [ $? -eq 0 ]; then
        echo "[OK]   metrics agree with the tables (counts, sprint window, trend tail)"
    else
        PROBLEMS=$((PROBLEMS + 1))
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

#!/bin/bash
# check-methodology.sh — Methodology chain validation script
# Was referenced as INFRA_FILES in Config.py but was missing.
# Validates the whole methodology chain: E → S → QR → PR + the development
# gates (IR → SP → PR), so a closed gate can never read as "0 issues".
# CHECK 8 (template hygiene) catches a template's own guidance shipped into a
# record — see B-006/E-070.
#
# Usage: sh scripts/check-methodology.sh [--fix] [--verbose]
#
# Exit codes:
#   0 = all checks passed
#   1 = methodology violations found
#   2 = script error

set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# Project root = the target project being validated, NOT the methodology repo.
# Resolve from the standard env vars like the hook engine does.
PROJECT_ROOT="${OPENHANDS_PROJECT_DIR:-${CLAUDE_PROJECT_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}}"

FIX_MODE=false
VERBOSE=false

# Newest-first glob expander (mirrors run-hook.sh) — used by CHECK 1 discovery.
_join_newest_first() {
    for d in $1; do
        [ -d "$d" ] && printf '%s %s\n' "$(stat -c %Y "$d" 2>/dev/null || stat -f %m "$d" 2>/dev/null || echo 0)" "$d"
    done | sort -rn | cut -d' ' -f2-
}

for arg in "$@"; do
    case "$arg" in
        --fix) FIX_MODE=true ;;
        --verbose) VERBOSE=true ;;
    esac
done

ISSUES=0
WARNINGS=0

log_issue() {
    ISSUES=$((ISSUES + 1))
    echo "❌ ISSUE: $1"
}

log_warn() {
    WARNINGS=$((WARNINGS + 1))
    echo "⚠️  WARN: $1"
}

log_ok() {
    if [ "$VERBOSE" = true ]; then
        echo "✅ OK: $1"
    fi
}

# ─── CHECK 1: plugin methodology manifestos exist (plugin-canonical) ───
echo "═══════════════════════════════════════════════════"
echo "CHECK 1: Methodology Files"
echo "═══════════════════════════════════════════════════"

# The three methodology manifestos are plugin-canonical: they live under the
# plugin root and are read from there (custom/*.toml persistent_facts use
# {metodoloji-root}/docs/bmad/...). Resolve the plugin root the same way the
# hook engine does; fall back to this repo (dogfooding: repo == plugin).
METODOLOJI_ROOT="${METODOLOJI_PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-}}"
if [ -z "$METODOLOJI_ROOT" ]; then
    # Fixed candidates first, then versioned cache globs (newest first),
    # mirroring run-hook.sh's discovery list.
    for cand in "$SCRIPT_DIR/.."; do
        if [ -f "$cand/docs/bmad/research-methodology.md" ]; then METODOLOJI_ROOT="$cand"; break; fi
    done
fi
if [ -z "$METODOLOJI_ROOT" ]; then
    for cand in $(_join_newest_first "$HOME/.claude/plugins/marketplaces/metodoloji") \
                $(_join_newest_first "$HOME/.claude/plugins/cache/metodoloji/metodoloji/*") \
                $(_join_newest_first "$HOME/.claude/plugins/cache/metodoloji/*/*") \
                $(_join_newest_first "$HOME/.claude/plugins/cache/yunusgungor/metodoloji/*") \
                $(_join_newest_first "$HOME/.openhands/plugins/cache/*") \
                "$HOME/.openhands/plugins/installed/metodoloji"; do
        if [ -f "$cand/docs/bmad/research-methodology.md" ]; then METODOLOJI_ROOT="$cand"; break; fi
    done
fi
if [ -z "$METODOLOJI_ROOT" ]; then
    METODOLOJI_ROOT="$SCRIPT_DIR/.."
fi

for f in docs/bmad/research-methodology.md docs/bmad/development-methodology.md docs/bmad/dev-skill-to-methodology-bridge.md; do
    if [ -f "$METODOLOJI_ROOT/$f" ]; then
        log_ok "$f exists (plugin root)"
    else
        log_issue "$f is MISSING in plugin root ($METODOLOJI_ROOT) — required by custom/*.toml persistent_facts"
    fi
done

# ─── CHECK 2: docs/development/ structure ───
echo ""
echo "═══════════════════════════════════════════════════"
echo "CHECK 2: Development Directory Structure"
echo "═══════════════════════════════════════════════════"

for d in docs/development docs/development/stories docs/quality docs/experiments; do
    if [ -d "$PROJECT_ROOT/$d" ]; then
        log_ok "$d/ exists"
    else
        log_warn "$d/ is MISSING — creating"
        if [ "$FIX_MODE" = true ]; then
            mkdir -p "$PROJECT_ROOT/$d"
            echo "  → Created $d/"
        fi
    fi
done

# Canonical story template lives in stories/ (legacy docs/development/
# _template_S.md was removed — accept it as a fallback, not a requirement).
if [ -f "$PROJECT_ROOT/docs/development/stories/_template_S.md" ]; then
    log_ok "docs/development/stories/_template_S.md exists"
elif [ -f "$PROJECT_ROOT/docs/development/_template_S.md" ]; then
    log_warn "legacy docs/development/_template_S.md in use — move to docs/development/stories/_template_S.md"
else
    log_issue "docs/development/stories/_template_S.md is MISSING — required by bridge doc §2.3"
fi

# ─── CHECK 3: Story files have methodology references ───
echo ""
echo "═══════════════════════════════════════════════════"
echo "CHECK 3: Story Files — Methodology References"
echo "═══════════════════════════════════════════════════"

STORY_COUNT=0
STORY_WITH_REFS=0
STORY_WITHOUT_REFS=0

for story_file in "$PROJECT_ROOT"/docs/development/stories/S-*.md; do
    [ -f "$story_file" ] || continue
    STORY_COUNT=$((STORY_COUNT + 1))
    basename_story=$(basename "$story_file")

    # Check if native story reference exists
    if grep -q "Native Story\|native_story" "$story_file" 2>/dev/null; then
        STORY_WITH_REFS=$((STORY_WITH_REFS + 1))
        log_ok "$basename_story has native story reference"
    else
        STORY_WITHOUT_REFS=$((STORY_WITHOUT_REFS + 1))
        log_warn "$basename_story missing native story reference"
    fi
done

if [ "$STORY_COUNT" -eq 0 ]; then
    log_warn "No methodology story records found in docs/development/stories/"
else
    echo "  Total story records: $STORY_COUNT"
    echo "  With native refs: $STORY_WITH_REFS"
    echo "  Without native refs: $STORY_WITHOUT_REFS"
fi

# ─── CHECK 4: Experiment records integrity ───
echo ""
echo "═══════════════════════════════════════════════════"
echo "CHECK 4: Experiment Records"
echo "═══════════════════════════════════════════════════"

EXP_COUNT=0
EXP_APPROVED=0
EXP_PENDING=0
EXP_REJECTED=0

# Canonical fields are gate-written (see templates/_template_E.md): the gate
# sets `- **Status:** completed` on approval and `- **Status:** REJECTED` on
# rejection, and always writes `- **Decision:** APPROVED|REJECTED`. The legacy
# `Status: APPROVED|PENDING` forms stay accepted for older records.
for exp_file in "$PROJECT_ROOT"/docs/experiments/E-*.md; do
    [ -f "$exp_file" ] || continue
    EXP_COUNT=$((EXP_COUNT + 1))
    basename_exp=$(basename "$exp_file")

    if grep -qE "(Decision|Status):[* ]*APPROVED|(Decision|Status):[* ]*ONAYLANDI" "$exp_file" 2>/dev/null; then
        EXP_APPROVED=$((EXP_APPROVED + 1))
        log_ok "$basename_exp is APPROVED"
    elif grep -qE "(Decision|Status):[* ]*REJECTED|(Decision|Status):[* ]*REDDEDİLDİ" "$exp_file" 2>/dev/null; then
        EXP_REJECTED=$((EXP_REJECTED + 1))
        log_warn "$basename_exp is REJECTED"
    elif grep -qE "(Status):[* ]*(planned|PENDING|BEKLİYOR)" "$exp_file" 2>/dev/null; then
        EXP_PENDING=$((EXP_PENDING + 1))
        log_warn "$basename_exp is PENDING"
    else
        log_warn "$basename_exp status unknown"
    fi
done

if [ "$EXP_COUNT" -eq 0 ]; then
    log_warn "No experiment records found in docs/experiments/"
else
    echo "  Total experiments: $EXP_COUNT"
    echo "  Approved: $EXP_APPROVED"
    echo "  Rejected: $EXP_REJECTED"
    echo "  Pending: $EXP_PENDING"
fi

# ─── CHECK 5: QR records completeness ───
echo ""
echo "═══════════════════════════════════════════════════"
echo "CHECK 5: Quality Records"
echo "═══════════════════════════════════════════════════"

QR_COUNT=0
QR_PASSED=0
QR_FAILED=0

for qr_file in "$PROJECT_ROOT"/docs/quality/QR-*.md; do
    [ -f "$qr_file" ] || continue
    QR_COUNT=$((QR_COUNT + 1))
    basename_qr=$(basename "$qr_file")

    # Canonical QR status field (templates/_template_QR.md):
    #   - **Status:** in-review | APPROVED | REJECTED | REVISED
    # The old grep expected a "QR Status: pass" line the template never had,
    # so every QR record read as "status unknown".
    if grep -qE "Status:[* ]*APPROVED" "$qr_file" 2>/dev/null; then
        QR_PASSED=$((QR_PASSED + 1))
        log_ok "$basename_qr PASSED"
    elif grep -qE "Status:[* ]*REJECTED|QR Status: (fail|failed)" "$qr_file" 2>/dev/null; then
        QR_FAILED=$((QR_FAILED + 1))
        log_warn "$basename_qr FAILED"
    else
        # an in-review QR carries no verdict yet: the loop's honest nag until
        # the review lands (same class as an E record still PENDING)
        log_warn "$basename_qr status unknown"
    fi
done

if [ "$QR_COUNT" -eq 0 ]; then
    log_warn "No QR records found in docs/quality/"
else
    echo "  Total QR records: $QR_COUNT"
    echo "  Passed: $QR_PASSED"
    echo "  Failed: $QR_FAILED"
fi

# ─── CHECK 6: Chain completeness (E → S → QR) ───
echo ""
echo "═══════════════════════════════════════════════════"
echo "CHECK 6: Methodology Chain Completeness"
echo "═══════════════════════════════════════════════════"

# Check if there are stories in sprint-status that lack methodology records
SPRINT_STATUS=""
for candidate in \
    "$PROJECT_ROOT/docs/development/native/sprint-status.yaml" \
    "$PROJECT_ROOT/bmad-output/implementation-artifacts/sprint-status.yaml" \
    "$PROJECT_ROOT/_bmad-output/implementation-artifacts/sprint-status.yaml"; do
    if [ -f "$candidate" ]; then
        SPRINT_STATUS="$candidate"
        break
    fi
done

if [ -n "$SPRINT_STATUS" ] && [ -f "$SPRINT_STATUS" ]; then
    # Count YAML STORY ENTRIES, not prose. Two drift bugs met here:
    # 1. The shipped sprint-status template documents its vocabulary in comments
    #    ("  - in-progress: Developer actively working"), so a bare
    #    `grep -c in-progress` reported legend lines as stories for every project
    #    generated from that template.
    # 2. A generic `<key>: <status>` form also counted the NON-story status keys
    #    ("epic-1: in-progress"), because an epic key looks exactly like a story
    #    key to a loose pattern.
    # Match the story-key form the engine itself parses
    # (hooks/engine/modules/stop.py): `N-N-slug`, indented, never a comment.
    # `|| true` (not `|| echo "0"`) keeps a zero count as "0" — the old fallback
    # appended a second line, so `[ "$N" -gt 0 ]` hit "integer expression
    # expected" whenever nothing matched.
    _STATUS_ENTRY='^[[:space:]]+[0-9]+-[0-9]+-[a-z][a-z0-9-]*:[[:space:]]*'
    IN_PROGRESS=$(grep -cE "${_STATUS_ENTRY}in-progress[[:space:]]*$" "$SPRINT_STATUS" 2>/dev/null || true)
    DONE=$(grep -cE "${_STATUS_ENTRY}done[[:space:]]*$" "$SPRINT_STATUS" 2>/dev/null || true)
    echo "  Sprint status: $SPRINT_STATUS"
    echo "  In-progress stories: $IN_PROGRESS"
    echo "  Done stories: $DONE"

    # Epic-lag (S3 signal, mailjs 2026-09-23): epics claimed in-progress while
    # EVERY member story reads done — the roll-up nobody did, so the next
    # session re-derived state from stale memory. awk because story→epic
    # grouping needs the key arithmetic, not just a grep.
    EPIC_LAG=$(awk '
        /^[[:space:]]*(epic-[0-9]+|[0-9]+-[0-9]+-[a-z][a-z0-9-]*):[[:space:]]*/ {
            line=$0; sub(/^[[:space:]]+/,"",line); sub(/[[:space:]]*$/, "",line);
            split(line, kv, ":"); key=kv[1]; st=kv[2]; gsub(/[[:space:]]/,"",st);
            if (key ~ /^epic-[0-9]+$/) epic[key]=st;
            else { split(key, parts, "-"); e="epic-" parts[1]; total[e]++; if (st=="done") done[e]++ }
        }
        END {
            for (e in epic) {
                if (total[e] > 0 && epic[e]=="in-progress" && done[e]==total[e])
                    print e "|in-progress lag: every member story done — roll the epic up to done and run the retrospective";
                else if (total[e] > 0 && epic[e]=="done" && done[e]<total[e])
                    print e "|marked done but " (total[e]-done[e]) " member story/stories not done";
            }
        }
    ' "$SPRINT_STATUS" 2>/dev/null || true)
    if [ -n "$EPIC_LAG" ]; then
        printf '%s\n' "$EPIC_LAG" | while IFS= read -r lag; do
            epic="${lag%%|*}"; msg="${lag#*|}"
            log_warn "$epic: $msg"
        done
    fi

    # Retrospective signal (S4): the mailjs session never saw its pending
    # retros — nothing counted them. Vocabulary is the shipped template's
    # (optional | done); anything not done is surfaced.
    RETRO_OPEN=$(grep -cE '^[[:space:]]+[A-Za-z0-9_-]*retrospective:[[:space:]]*(optional|open|backlog|in-progress|pending)[[:space:]]*$' "$SPRINT_STATUS" 2>/dev/null || true)
    if [ "${RETRO_OPEN:-0}" -gt 0 ]; then
        log_warn "$RETRO_OPEN retrospective(s) not done — a completed epic without its retro is closed work without the lesson record (run bmad-retrospective)"
    fi

    if [ "$IN_PROGRESS" -gt 0 ]; then
        log_warn "There are $IN_PROGRESS in-progress stories — verify methodology records exist"
    fi

    # Sprint-record pointer list (second-sprint run, 2026-10-01): this comment
    # named ONE record, so the moment a second sprint was planned the board
    # stopped naming the first — the chain looked shorter than it was. It is now
    # one line per sprint record (oldest first), and the board must name every
    # SP record it sits next to. A dangling pointer, a repeated record or a list
    # that stopped growing is chain incoherence, not cosmetics.
    POINTERS=$(sed -nE 's|^[[:space:]]*#[[:space:]]*Methodology record:[[:space:]]*([^[:space:]]+).*|\1|p' \
        "$SPRINT_STATUS" 2>/dev/null || true)
    SP_RECORDS=$( (cd "$PROJECT_ROOT/docs/development" 2>/dev/null && ls SP-*.md 2>/dev/null) || true )
    PTR_COUNT=$(printf '%s' "$POINTERS" | grep -c . || true)
    REC_COUNT=$(printf '%s' "$SP_RECORDS" | grep -c . || true)
    if [ "${REC_COUNT:-0}" -gt 0 ] && [ "${PTR_COUNT:-0}" -eq 0 ]; then
        log_warn "sprint-status.yaml names no '# Methodology record:' pointer — add one line per sprint record (${REC_COUNT} in docs/development/)"
    elif [ "${PTR_COUNT:-0}" -gt 0 ]; then
        DUPES=$(printf '%s\n' "$POINTERS" | sort | uniq -d | tr '\n' ' ' || true)
        if [ -n "${DUPES% }" ]; then
            log_issue "sprint-status.yaml names the same sprint record twice: ${DUPES% }"
        fi
        for p in $POINTERS; do
            case "$p" in
                /*|[A-Za-z]:*) abs="$p" ;;
                docs/development/*) abs="$PROJECT_ROOT/$p" ;;
                *) abs="$PROJECT_ROOT/docs/development/$p" ;;
            esac
            [ -f "$abs" ] || log_issue "sprint-status.yaml points at a missing sprint record: $p"
        done
        for f in $SP_RECORDS; do
            printf '%s\n' "$POINTERS" | grep -qE "(^|/)${f}$" || \
                log_issue "sprint record $f is named in no '# Methodology record:' line — the board forgot a sprint"
        done
    fi
else
    log_warn "No sprint-status.yaml found — chain cannot be validated"
fi

# ─── CHECK 7: Development gate records (IR / SP / PR) ───
echo ""
echo "═══════════════════════════════════════════════════"
echo "CHECK 7: Development Gate Records (IR / SP / PR)"
echo "═══════════════════════════════════════════════════"

# CHECK 3/4/5 look at stories, experiments and QR records; the IR gate — the one
# that decides whether the sprint may start at all — had NO check. A project
# could therefore sit on an INCOMPLETE readiness record with an unchecked
# mandatory item and still read "0 issues / 0 warnings": the checker never
# looked at the gate, so it could never warn that the gate was closed
# (graph-engineering-arge design-wing session, 2026-10-01: IR-001 INCOMPLETE,
# `- [ ] Success criteria clear and measurable` unchecked, its PRD's §7 carried
# only unfalsifiable criteria).
IR_SEEN=0
IR_OPEN=0
for ir_file in "$PROJECT_ROOT"/docs/development/IR-*.md; do
    [ -f "$ir_file" ] || continue
    case "$(basename "$ir_file")" in _*) continue ;; esac
    IR_SEEN=$((IR_SEEN + 1))
    ir_base=$(basename "$ir_file")

    # Status: bold bullet or table cell, English or legacy Turkish.
    ir_status=$(grep -m1 -E '\*\*(Status|Durum):\*\*|^\| *(Status|Durum) *\|' "$ir_file" 2>/dev/null || true)
    ir_status=$(printf '%s' "$ir_status" | tr -d '\r' \
        | sed -E 's/.*\*\*(Status|Durum):\*\* *//; s/^\| *[^|]*\| *//; s/[|→—].*//; s/^ *//; s/ *$//')
    case "$ir_status" in
        READY|HAZIR|INCOMPLETE|EKSİK|preparing|"") : ;;
        *) log_issue "$ir_base -> unexpected Status: '$ir_status' (expected READY | INCOMPLETE)" ;;
    esac
    if ! grep -qE '\*\*(Date|Tarih):\*\*|^\| *(Date|Tarih) *\|' "$ir_file" 2>/dev/null; then
        log_issue "$ir_base -> no Date field"
    fi

    ir_decision=$(grep -m1 -E '\*\*(Decision|Karar):\*\*|^\| *(Decision|Karar) *\|' "$ir_file" 2>/dev/null || true)
    ir_decision=$(printf '%s' "$ir_decision" | tr -d '\r' \
        | sed -E 's/.*\*\*(Decision|Karar):\*\* *//; s/^\| *[^|]*\| *//; s/[|→—].*//; s/^ *//; s/ *$//')
    if [ -z "$ir_decision" ]; then
        log_issue "$ir_base -> no Decision field (Gate 1 must state its verdict)"
    fi
    case "$ir_status:$ir_decision" in
        READY:INCOMPLETE|HAZIR:EKSİK|INCOMPLETE:READY|EKSİK:HAZIR)
            log_issue "$ir_base -> Status and Decision disagree ('$ir_status' vs '$ir_decision')" ;;
    esac

    # Success criteria must be falsifiable: a criterion naming a Metric/Source/
    # Target, or an explicit instrumentation gap. Qualitative-only criteria make
    # the gate decorative — the exact shape this check was born from.
    if ! grep -qE '\*\*(Success criteria|Başarı kriterleri):\*\*' "$ir_file" 2>/dev/null; then
        log_issue "$ir_base -> no 'Success criteria' field — Gate 1 cannot judge an unstated goal"
    elif ! grep -qE 'Metric:|Source:|Target:|Instrumentation gaps:' "$ir_file" 2>/dev/null; then
        log_warn "$ir_base -> success criteria name no metric/source/target and no instrumentation gap (qualitative-only — unfalsifiable)"
    fi

    case "$ir_status" in
        READY|HAZIR)
            # A READY verdict with an unchecked checklist item is a broken gate:
            # the record claims "cleared" while its own checklist says "not judged".
            unchecked=$(awk '/^##[[:space:]]*Checklist/ {c=1; next} /^##[[:space:]]/ {c=0} c && /^- \[ \]/ {n++} END {print n+0}' "$ir_file" 2>/dev/null || true)
            if [ "${unchecked:-0}" -gt 0 ]; then
                log_issue "$ir_base -> Status is READY but $unchecked checklist item(s) are unchecked — the verdict is INCOMPLETE, not READY"
                grep -E '^- \[ \]' "$ir_file" 2>/dev/null | sed 's/^/       /' || true
            fi
            ;;
        INCOMPLETE|EKSİK)
            IR_OPEN=$((IR_OPEN + 1))
            if ! grep -qE '\*\*(Gaps|Boşluklar):\*\*' "$ir_file" 2>/dev/null; then
                log_issue "$ir_base -> INCOMPLETE without a 'Gaps' field — an open gate must name its plan"
            fi
            log_warn "$ir_base -> Gate 1 is OPEN (INCOMPLETE): sprint planning must not start until it is READY"
            ;;
        preparing|"")
            log_warn "$ir_base -> no verdict yet (Status '$ir_status') — Gate 1 has not been judged" ;;
    esac

    # A named design input that is not on disk is a dangling reference (the
    # deleted-product-definition shape): the verdict judged an artifact nobody
    # can open now. Tokens ({planning_artifacts}/…) and "none" are skipped.
    for ir_ref in $(sed -nE 's/^[[:space:]]*-[[:space:]]*(PRD|UX Spec|Architecture Plan)[[:space:]]*:[[:space:]]*([^[:space:]]+).*/\2/p' "$ir_file" 2>/dev/null || true); do
        case "$ir_ref" in
            none|NONE|none.|'['*|'{'*|*'{'*) continue ;;
        esac
        case "$ir_ref" in
            /*|[A-Za-z]:*) ir_ref_abs="$ir_ref" ;;
            docs/*) ir_ref_abs="$PROJECT_ROOT/$ir_ref" ;;
            *) continue ;;
        esac
        [ -f "$ir_ref_abs" ] || log_issue "$ir_base -> names a design input not on disk: $ir_ref"
    done
done
if [ "$IR_SEEN" -gt 0 ]; then
    echo "  IR records: $IR_SEEN (open: $IR_OPEN)"
fi

# SP — Gate 2: status vocabulary, a date, and the single-sentence goal the
# sprint-planning contract requires. An unfilled template copy passes the
# vocabulary test but an empty goal does not.
SP_SEEN=0
for sp_file in "$PROJECT_ROOT"/docs/development/SP-*.md; do
    [ -f "$sp_file" ] || continue
    case "$(basename "$sp_file")" in _*) continue ;; esac
    SP_SEEN=$((SP_SEEN + 1))
    sp_base=$(basename "$sp_file")
    sp_status=$(grep -m1 -E '\*\*(Status|Durum):\*\*|^\| *(Status|Durum) *\|' "$sp_file" 2>/dev/null || true)
    sp_status=$(printf '%s' "$sp_status" | tr -d '\r' \
        | sed -E 's/.*\*\*(Status|Durum):\*\* *//; s/^\| *[^|]*\| *//; s/[|→—].*//; s/^ *//; s/ *$//')
    case "$sp_status" in
        planned|in-progress|completed|cancelled|canceled|planlandı|tamamlandı|iptal|"") : ;;
        *) log_issue "$sp_base -> unexpected Status: '$sp_status' (expected planned | in-progress | completed | cancelled)" ;;
    esac
    if ! grep -qE '\*\*(Date|Tarih):\*\*|^\| *(Date|Tarih) *\|' "$sp_file" 2>/dev/null; then
        log_issue "$sp_base -> no Date field"
    fi
    if ! grep -qE '\*\*(Sprint goal|Sprint hedefi):\*\*' "$sp_file" 2>/dev/null; then
        log_issue "$sp_base -> no 'Sprint goal' field — Gate 2 needs a single-sentence goal"
    fi
done
# PR — Gate 4: status vocabulary (the template's `preparing` included), date,
# and a stated decision.
PR_SEEN=0
for pr_file in "$PROJECT_ROOT"/docs/development/PR-*.md; do
    [ -f "$pr_file" ] || continue
    case "$(basename "$pr_file")" in _*) continue ;; esac
    PR_SEEN=$((PR_SEEN + 1))
    pr_base=$(basename "$pr_file")
    pr_status=$(grep -m1 -E '\*\*(Status|Durum):\*\*|^\| *(Status|Durum) *\|' "$pr_file" 2>/dev/null || true)
    pr_status=$(printf '%s' "$pr_status" | tr -d '\r' \
        | sed -E 's/.*\*\*(Status|Durum):\*\* *//; s/^\| *[^|]*\| *//; s/[|→—].*//; s/^ *//; s/ *$//')
    case "$pr_status" in
        preparing|READY|WAITING|HAZIR|BEKLİYOR|"") : ;;
        *) log_issue "$pr_base -> unexpected Status: '$pr_status' (expected preparing | READY | WAITING)" ;;
    esac
    if ! grep -qE '\*\*(Date|Tarih):\*\*|^\| *(Date|Tarih) *\|' "$pr_file" 2>/dev/null; then
        log_issue "$pr_base -> no Date field"
    fi
done
# A project-side custom/ directory is an ORPHAN: the gate reads team/user config
# from {metodoloji-root}/custom/ only (plugin-global policy — see
# hooks/engine/modules/config.py), so a copy in the target project looks
# authoritative and is never read. The graph-engineering-arge session
# (2026-10-01) found exactly this and had to decide whether it was in scope.
# Skipped when the project IS the plugin checkout (dogfood: same directory).
if [ -f "$PROJECT_ROOT/custom/config.toml" ]; then
    _proj_abs=$(cd "$PROJECT_ROOT" 2>/dev/null && pwd || printf '%s' "$PROJECT_ROOT")
    _plug_abs=$(cd "$METODOLOJI_ROOT" 2>/dev/null && pwd || printf '%s' "$METODOLOJI_ROOT")
    if [ "$_proj_abs" != "$_plug_abs" ]; then
        log_warn "project-side custom/config.toml is never read — custom/ is plugin-global policy ({metodoloji-root}/custom/); a project copy looks authoritative but has no effect; move the settings to docs/config.toml or bmad/config.toml"
    fi
fi

GATE_TOTAL=$((IR_SEEN + SP_SEEN + PR_SEEN))
if [ "$GATE_TOTAL" -eq 0 ]; then
    # A fresh project has judged no gate yet — one honest warning, like CHECK 4/5.
    log_warn "No development gate records (IR/SP/PR) in docs/development/ — no gate has been judged yet"
else
    # A gate that was never written while its siblings exist was skipped, not
    # passed: the chain cannot be complete with a hole in it.
    if [ "$IR_SEEN" -eq 0 ]; then
        log_warn "development records exist but no IR readiness record — Gate 1 was skipped"
    fi
    if [ "$SP_SEEN" -eq 0 ] && [ "$IR_SEEN" -gt 0 ]; then
        log_warn "IR exists but no SP sprint record — Gate 2 was skipped"
    fi
fi

# ─── CHECK 8: record template hygiene (meta-text leakage) ───
echo ""
echo "═══════════════════════════════════════════════════"
echo "CHECK 8: Record Template Hygiene (meta-text leakage)"
echo "═══════════════════════════════════════════════════"

# A record is an artifact, not a template. When a template writes its guidance
# as visible record text — a `> This template is used for …` banner, a
# duplicated `## <kind>: <id>` title, a `**Label:** [restated label]` hint —
# an agent that copies the template ships the instructions into the record. A
# real Claude Code IR run did exactly that (IR-003, 2026-10-05; B-006).
# Severity split: a shipped or init-copied TEMPLATE with meta-text is an ISSUE
# (fix the source); a produced RECORD with it is a WARN (visible and honest,
# never silently rewritten — a written verdict is a snapshot, not a property).
LEAK_BANNER='^> (This template is used for|Represents Development Gate)'
LEAK_TITLE='^## (Implementation Readiness|Sprint|Quality Review|Production Readiness|Story): '
LEAK_PLACEHOLDER='\[(the PRD.s Success Metrics|Known risks \+ mitigation plan|If incomplete, what.s missing|PRD/UX/architecture file references|APIs, libraries, infrastructure|claims from the product definition|S-id list \+ priority|Estimated story points / team velocity|Debt addressed in this sprint|Known blockers \+ resolution plan|External team/system dependencies|What worked well, should continue|What could be better|What will change in the next sprint|How many points were completed|Which features were demoed|QR-id list|S-id / story path list|Under what conditions rollback happens|How many files|Performance of critical functions|Which APIs required|Database, queue etc|Other S-ids)'

_leak_reason() {
    if grep -qE "$LEAK_BANNER" "$1" 2>/dev/null; then printf 'template banner text'; return 0; fi
    if grep -qE "$LEAK_TITLE" "$1" 2>/dev/null; then printf 'duplicated template title heading'; return 0; fi
    if grep -qE "$LEAK_PLACEHOLDER" "$1" 2>/dev/null; then printf 'unfilled template placeholder'; return 0; fi
    return 1
}

_leak_check() {  # $1 = file, $2 = ISSUE|WARN
    [ -f "$1" ] || return 0
    _leak_why=$(_leak_reason "$1") || return 0
    if [ "$2" = ISSUE ]; then
        log_issue "$(basename "$1") -> carries template meta-text ($_leak_why) — the template shipped guidance as record content"
    else
        log_warn "$(basename "$1") -> carries template meta-text ($_leak_why) — copy a clean template when the record is next written"
    fi
    return 0
}

LEAK_TEMPLATES_CHECKED=0
for _tpl in \
    "$METODOLOJI_ROOT/templates/_template_IR.md" "$METODOLOJI_ROOT/templates/_template_SP.md" \
    "$METODOLOJI_ROOT/templates/_template_QR.md" "$METODOLOJI_ROOT/templates/_template_PR.md" \
    "$METODOLOJI_ROOT/templates/_template_S.md" \
    "$PROJECT_ROOT/docs/development/_template_IR.md" "$PROJECT_ROOT/docs/development/_template_SP.md" \
    "$PROJECT_ROOT/docs/development/_template_QR.md" "$PROJECT_ROOT/docs/development/_template_PR.md" \
    "$PROJECT_ROOT/docs/development/stories/_template_S.md"; do
    if [ -f "$_tpl" ]; then LEAK_TEMPLATES_CHECKED=$((LEAK_TEMPLATES_CHECKED + 1)); fi
    _leak_check "$_tpl" ISSUE
done

LEAK_RECORDS_CHECKED=0
for _rec in \
    "$PROJECT_ROOT"/docs/development/IR-*.md "$PROJECT_ROOT"/docs/development/SP-*.md \
    "$PROJECT_ROOT"/docs/development/PR-*.md "$PROJECT_ROOT"/docs/development/QR-*.md \
    "$PROJECT_ROOT"/docs/development/stories/S-*.md "$PROJECT_ROOT"/docs/quality/QR-*.md; do
    [ -f "$_rec" ] || continue
    case "$(basename "$_rec")" in _*) continue ;; esac
    LEAK_RECORDS_CHECKED=$((LEAK_RECORDS_CHECKED + 1))
    _leak_check "$_rec" WARN
done
echo "  templates checked: $LEAK_TEMPLATES_CHECKED (meta-text is an issue) · records checked: $LEAK_RECORDS_CHECKED (meta-text is a warning)"

# ─── SUMMARY ───
echo ""
echo "═══════════════════════════════════════════════════"
echo "SUMMARY"
echo "═══════════════════════════════════════════════════"
echo "  Issues:  $ISSUES"
echo "  Warnings: $WARNINGS"

if [ "$ISSUES" -gt 0 ]; then
    echo ""
    echo "❌ METHODOLOGY VALIDATION FAILED — $ISSUES issues found"
    echo "   Run with --fix to auto-create missing directories"
    exit 1
elif [ "$WARNINGS" -gt 0 ]; then
    echo ""
    echo "⚠️  METHODOLOGY VALIDATION PASSED with $WARNINGS warnings"
    exit 0
else
    echo ""
    echo "✅ METHODOLOGY VALIDATION PASSED — all checks clean"
    exit 0
fi

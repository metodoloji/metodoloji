#!/bin/sh
# check-custom.sh — static quality audit of custom/ bridge TOMLs.
#
#   0. TOML parse + table presence
#   1. Persistent-facts trio  (research / development / project-context)
#   2. activation_steps_append depth
#        Gate enforcers (GATE_REQUIRED)  → >= 3 steps
#        Other workflows (MOD_A_RECORD_ONLY + tools) → >= 1 step
#   3. Hard-gate keywords
#        GATE_REQUIRED    → must contain ONAYLANDI/REDDEDİLDİ/FORGED/VERIFIED
#        GATE_REFERENCE_OK → may carry as reference (e.g. bmad-tea)
#        Others → leak (wrong layer) → fail
#   4. Bridge verify pattern (gate enforcers: "ls -la ... error and recreate")
#   5. gds-* Mod A bridge reference (dev-skill-to-methodology-bridge required)
#   6. config.toml [hooks] soft/hard contract (DRY: same parser as §5b)
#   7. Bridge document §N.N reference drift audit (synced with bridge.md sections)
#
# Usage:  sh scripts/check-custom.sh
#            sh scripts/check-custom.sh --negtest
#            (negative-test only: §3 hard-gate + §7 bridge drift (2/2:
#             delete §N.N, inject "bolum N.N") → catch MISS → restore)
# Output:    [OK] / [WARNING] / [ERROR] at the start of each line; overall status at the end.
set -u

PROBLEMS=0

if [ "${1:-}" = "--negtest" ]; then
    # Negative test 1: §3 hard-gate — temporarily remove the hard-gate
    # keywords (ONAYLANDI / REDDEDİLDİ / FORGED / VERIFIED) from
    # bmad-dev-story.toml, verify §3 logic produces a MISS, then restore the file.
    # Negative test 2: §7 bridge drift — temporarily remove the "### §2.3" heading
    # from bridge.md, verify §7 logic produces a MISS because of the §2.3
    # references in custom/ files, then restore the bridge.
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
toml = PLUGIN / "custom" / "bmad-dev-story.toml"
bridge = PLUGIN / "docs" / "bmad" / "dev-skill-to-methodology-bridge.md"
# Accept both the legacy Turkish markers and the new English markers.
KEYWORDS = ("APPROVED", "REJECTED", "FORGED", "VERIFIED", "ONAYLANDI", "REDDEDİLDİ")

orig_toml = toml.read_text(encoding="utf-8")
orig_bridge = bridge.read_text(encoding="utf-8")

# ---- Test 1: §3 hard-gate ----
def has_dev_story_gate_error(text: str) -> bool:
    toml.write_text(text, encoding="utf-8")
    try:
        r = subprocess.run(
            ["sh", str(PLUGIN / "scripts" / "check-custom.sh")],
            capture_output=True, text=True, encoding="utf-8", timeout=30)
        out = r.stdout
        for line in out.splitlines():
            if "bmad-dev-story" in line and "hard-gate" in line:
                return True
        return False
    finally:
        toml.write_text(orig_toml, encoding="utf-8")

if has_dev_story_gate_error(orig_toml):
    print("[ERROR] hard-gate error visible even in intact custom TOML — test setup broken")
    sys.exit(1)

broken_toml = "\n".join(
    l for l in orig_toml.splitlines()
    if not any(k in l for k in KEYWORDS)
)
removed = sum(orig_toml.count(k) for k in KEYWORDS)
removed_after = sum(broken_toml.count(k) for k in KEYWORDS)
if removed == removed_after or removed == 0:
    print("[ERROR] keywords not found or could not be removed — test setup broken")
    sys.exit(1)

if not has_dev_story_gate_error(broken_toml):
    print("[ERROR] negative test 1 failed: §3 logic did not catch removed hard-gate")
    sys.exit(1)
print("[OK]   negative test 1/3: hard-gate keywords removed → §3 MISS caught")

# ---- Test 2: §7 bridge drift ----
def has_bridge_drift_error(bridge_text: str) -> bool:
    """§7 drift: when §2.3 is removed from the bridge, a 'bridge §N.N drift'
    WARNING line must appear because of the §2.3 references in custom/ files."""
    bridge.write_text(bridge_text, encoding="utf-8")
    try:
        r = subprocess.run(
            ["sh", str(PLUGIN / "scripts" / "check-custom.sh")],
            capture_output=True, text=True, encoding="utf-8", timeout=30)
        out = r.stdout
        for line in out.splitlines():
            if "bridge §N.N drift" in line or "not in bridge" in line:
                return True
        return False
    finally:
        bridge.write_text(orig_bridge, encoding="utf-8")

if has_bridge_drift_error(orig_bridge):
    print("[ERROR] drift error visible even in intact bridge — test setup broken")
    sys.exit(1)

# Remove the "### §2.3" block from the bridge line-by-line: up to the next
# `### ` or `## ` heading. Minimal intervention for the drift test.
def remove_section_23(text: str) -> str:
    lines = text.splitlines(keepends=True)
    out = []
    skip = False
    for line in lines:
        if line.startswith("### §2.3"):
            skip = True
            continue
        if skip and (line.startswith("### ") or line.startswith("## ")):
            skip = False
        if not skip:
            out.append(line)
    return "".join(out)

broken_bridge = remove_section_23(orig_bridge)
if "### §2.3" in broken_bridge or "§2.3" in broken_bridge:
    print("[ERROR] §2.3 could not be removed from bridge — heading different or no match")
    sys.exit(1)

if not has_bridge_drift_error(broken_bridge):
    print("[ERROR] negative test 2 failed: §7 logic did not catch §2.3 removed from bridge")
    sys.exit(1)
print("[OK]   negative test 2/3: bridge §2.3 removed → §7 MISS caught → bridge restored")

# ---- Test 3: §7 bolum N.N pattern ----
# Inject "bolum 99.99" into bmad-testarch-atdd.toml — a section not in the
# bridge; §7's "bolum" pattern must also catch it.
test_toml = PLUGIN / "custom" / "bmad-testarch-atdd.toml"
orig_test_toml = test_toml.read_text(encoding="utf-8")

def has_bolum_drift_error(text: str) -> bool:
    test_toml.write_text(text, encoding="utf-8")
    try:
        r = subprocess.run(
            ["sh", str(PLUGIN / "scripts" / "check-custom.sh")],
            capture_output=True, text=True, encoding="utf-8", timeout=30)
        out = r.stdout
        for line in out.splitlines():
            if "bmad-testarch-atdd" in line and "not in bridge" in line:
                return True
        return False
    finally:
        test_toml.write_text(orig_test_toml, encoding="utf-8")

if has_bolum_drift_error(orig_test_toml):
    print("[ERROR] bolum drift error visible even in intact testarch TOML — test setup broken")
    sys.exit(1)

# Inject as a comment line so TOML semantics are not broken. The testarch
# file already carries a bridge reference, so §7 audits it.
broken_toml = orig_test_toml + "\n# drift test: bolum 99.99\n"
if not has_bolum_drift_error(broken_toml):
    print("[ERROR] negative test 3 failed: §7 logic did not catch injected 'bolum 99.99'")
    sys.exit(1)
print("[OK]   negative test 3/3: 'bolum 99.99' injected → §7 MISS caught → testarch TOML restored")
sys.exit(0)
PY
    exit $?
fi

# (Main flow below)

SELF=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PLUGIN_ROOT=$(CDPATH= cd -- "$SELF/.." && pwd)
export PLUGIN_ROOT

# Python resolver (same rule as check-plugin.sh).
PY=
for cand in python3 python py; do
    if command -v "$cand" >/dev/null 2>&1; then PY="$cand"; break; fi
done
if [ -z "$PY" ]; then
    echo "[ERROR] python3/python/py not found — check-custom cannot run." >&2
    exit 1
fi

echo "== 0) TOML parse + table presence =="
"$PY" - <<'PY'
import glob, os, sys, tomllib
for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError): pass

PLUGIN = os.environ.get("PLUGIN_ROOT") or "."
problems = []
checked = 0
for path in sorted(glob.glob(os.path.join(PLUGIN, "custom", "*.toml"))):
    name = os.path.basename(path)
    if name == "config.toml":
        continue  # audited separately in §6
    checked += 1
    try:
        data = tomllib.load(open(path, "rb"))
    except Exception as e:
        problems.append("%s: parse error — %s" % (name, e))
        continue
    if not any(sec in data for sec in ("workflow", "agent")):
        problems.append("%s: no [workflow] or [agent] table" % name)

print("  checked custom files: %d" % checked)
for p in problems:
    print("  MISS: %s" % p)
raise SystemExit(1 if problems else 0)
PY
if [ $? -eq 0 ]; then
    echo "[OK]   all custom TOMLs parse and contain a table"
else
    echo "[WARNING] parse/table issue (see above)"
    PROBLEMS=$((PROBLEMS + 1))
fi

echo "== 1) Persistent-facts trio (research / development / project-context) =="
"$PY" - <<'PY'
import glob, os, sys, tomllib
for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError): pass

PLUGIN = os.environ.get("PLUGIN_ROOT") or "."
# Development wing — those in this list must load the trio (research + development +
# project-context). For those not in the list, the only requirement is
# project-context.md; other manifesto pointers remain optional.
# Note: bmad-eval-runner / bmad-research-experiment / bmad-review-* are
# deliberately not in the list — they "don't write code, they measure/audit" and
# only load the research manifesto (they produce experiment records).
DEV_WING = {
    "bmad-check-implementation-readiness", "bmad-prd", "bmad-ux",
    "bmad-create-architecture", "bmad-sprint-planning", "bmad-create-story",
    "bmad-create-epics-and-stories", "bmad-dev-story", "bmad-quick-dev",
    "bmad-dev-auto",
    "bmad-testarch-atdd", "bmad-testarch-automate", "bmad-testarch-ci",
    "bmad-testarch-framework", "bmad-testarch-nfr", "bmad-testarch-test-design",
    "bmad-testarch-test-review", "bmad-testarch-trace",
    "bmad-code-review",
    "bmad-qa-generate-e2e-tests",
    "bmad-document-project",
    "bmad-retrospective", "bmad-correct-course", "bmad-sprint-status",
    "bmad-agent-dev", "bmad-agent-architect", "bmad-agent-pm",
    "bmad-agent-ux-designer", "bmad-agent-tech-writer", "bmad-tea",
}

problems = []
checked = 0
for path in sorted(glob.glob(os.path.join(PLUGIN, "custom", "*.toml"))):
    name = os.path.basename(path)[:-5]  # strip .toml
    if name == "config":
        continue
    try:
        data = tomllib.load(open(path, "rb"))
    except Exception:
        continue  # §0 already reported
    checked += 1
    facts = []
    for sec in ("workflow", "agent"):
        facts += data.get(sec, {}).get("persistent_facts", []) or []
    if not facts:
        problems.append("%s: no persistent_facts" % name)
        continue
    if not any("project-context.md" in x for x in facts):
        problems.append("%s: no project-context.md pointer" % name)
    if name in DEV_WING:
        if not any("research-methodology.md" in x for x in facts):
            problems.append("%s: no research-methodology.md pointer (DEV wing)" % name)
        elif not any("{metodoloji-root}/docs/bmad/research-methodology.md" in x for x in facts):
            problems.append("%s: research-methodology.md pointer not plugin-canonical ({metodoloji-root})" % name)
        if not any("development-methodology.md" in x for x in facts):
            problems.append("%s: no development-methodology.md pointer (DEV wing)" % name)
        elif not any("{metodoloji-root}/docs/bmad/development-methodology.md" in x for x in facts):
            problems.append("%s: development-methodology.md pointer not plugin-canonical ({metodoloji-root})" % name)

print("  checked: %d" % checked)
for p in problems:
    print("  MISS: %s" % p)
raise SystemExit(1 if problems else 0)
PY
if [ $? -eq 0 ]; then
    echo "[OK]   persistent_facts trio present on all surfaces"
else
    echo "[WARNING] persistent_facts missing (see above)"
    PROBLEMS=$((PROBLEMS + 1))
fi

echo "== 2) activation_steps_append depth =="
"$PY" - <<'PY'
import glob, os, sys, tomllib
for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError): pass

PLUGIN = os.environ.get("PLUGIN_ROOT") or "."
# Gate-ENFORCING surfaces (write code / check merges) — min 3 steps:
#   bridge instruction + at least one verify pattern + DOGRULAMA step
# Gate-non-enforcing Mod A surfaces (produce records but don't write code) — min 1 step.
# This distinction targets the files that actually use the hard-gate contract.
GATE_REQUIRED = {
    "bmad-dev-story", "bmad-dev-auto", "bmad-quick-dev",
    "bmad-code-review", "bmad-agent-dev",
    "bmad-create-story",   # ONAYLANDI check on AC experiments = gate verifier
    "gds-dev-story", "gds-quick-dev", "gds-code-review",
    "gds-create-story",    # gds create-story is likewise a verifier
    "gds-agent-game-dev", "wds-agent-mimir-builder",
}
MOD_A_RECORD_ONLY = {
    "bmad-research-experiment", "bmad-check-implementation-readiness",
    "bmad-sprint-planning",
    "gds-check-implementation-readiness",
    "gds-sprint-planning",
}
MIN_RECORD = 1
MIN_GATE = 3

problems = []
checked = 0
for path in sorted(glob.glob(os.path.join(PLUGIN, "custom", "*.toml"))):
    name = os.path.basename(path)[:-5]
    if name == "config":
        continue
    try:
        data = tomllib.load(open(path, "rb"))
    except Exception:
        continue
    checked += 1
    asa = data.get("workflow", {}).get("activation_steps_append", []) or []
    n = len(asa)
    if n == 0:
        # [agent]-only files may be empty (no activation_steps_append, that's fine).
        if "agent" in data and "workflow" not in data:
            continue
        problems.append("%s: empty activation_steps_append (workflow file)" % name)
        continue
    if name in GATE_REQUIRED:
        if n < MIN_GATE:
            problems.append("%s: gate enforcer but depth=%d (must be >= %d)" %
                            (name, n, MIN_GATE))
    else:
        if n < MIN_RECORD:
            problems.append("%s: depth=%d (must be >= %d)" % (name, n, MIN_RECORD))

print("  checked: %d" % checked)
for p in problems:
    print("  MISS: %s" % p)
raise SystemExit(1 if problems else 0)
PY
if [ $? -eq 0 ]; then
    echo "[OK]   activation_steps_append depth conforms to the rule"
else
    echo "[WARNING] depth violation (see above)"
    PROBLEMS=$((PROBLEMS + 1))
fi

echo "== 3) Hard-gate keywords (APPROVED / REJECTED / FORGED / VERIFIED) =="
"$PY" - <<'PY'
import glob, os, re, sys, tomllib
for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError): pass

PLUGIN = os.environ.get("PLUGIN_ROOT") or "."
# Mod A: surfaces that PRODUCE or ENFORCE the gate — hard-gate keyword required.
# This set covers the files where hard-gate is actually used (writes code / checks
# merges / mechanically approves). Surfaces that only produce documentary
# records (produce records but don't enforce the gate) are NOT here.
GATE_REQUIRED = {
    "bmad-dev-story", "bmad-dev-auto", "bmad-quick-dev",
    "bmad-code-review", "bmad-agent-dev",
    "bmad-create-story",   # APPROVED check on AC experiments = gate verifier
    "gds-dev-story", "gds-quick-dev", "gds-code-review",
    "gds-create-story",    # gds create-story is likewise a verifier
    "gds-agent-game-dev", "wds-agent-mimir-builder",
}
# Surfaces that carry the gate as a reference but don't enforce it (principle
# statement or for training). For those in this list the hard-gate keyword is
# not counted as a leak; appearing anywhere else is an error.
GATE_REFERENCE_OK = {
    "bmad-tea",  # test strategy documentary: "depends on Mod A mechanical approval"
    "bmad-quality-record",        # QR consumer: reads experiment/story gate status
    "bmad-production-readiness",  # PR consumer: reads QR gate status before certifying
}
# May appear in both activation_steps_append and principles.
# Accept both the legacy Turkish markers and the new English markers.
KEYWORDS = ("APPROVED", "REJECTED", "FORGED", "VERIFIED", "ONAYLANDI", "REDDEDİLDİ")

problems = []
checked = 0
for path in sorted(glob.glob(os.path.join(PLUGIN, "custom", "*.toml"))):
    name = os.path.basename(path)[:-5]
    if name == "config":
        continue
    try:
        data = tomllib.load(open(path, "rb"))
    except Exception:
        continue
    checked += 1
    blob_parts = []
    for sec in ("workflow", "agent"):
        s = data.get(sec, {})
        blob_parts += s.get("activation_steps_append", []) or []
        blob_parts += s.get("principles", []) or []
    blob = "\n".join(blob_parts)
    has_gate = any(k in blob for k in KEYWORDS)
    if name in GATE_REQUIRED:
        if not has_gate:
            problems.append("%s: hard-gate enforcer but no keyword "
                            "(expected %s)" % (name, "|".join(KEYWORDS)))
    elif name not in GATE_REFERENCE_OK and has_gate:
        problems.append("%s: not Mod A but hard-gate keyword leaked — "
                        "wrong layer; check it" % name)

print("  checked: %d" % checked)
for p in problems:
    print("  MISS: %s" % p)
raise SystemExit(1 if problems else 0)
PY
if [ $? -eq 0 ]; then
    echo "[OK]   hard-gate keywords in the correct layer"
else
    echo "[WARNING] hard-gate leak or missing keyword (see above)"
    PROBLEMS=$((PROBLEMS + 1))
fi

echo "== 4) Bridge DOGRULAMA pattern (Mod A) =="
"$PY" - <<'PY'
import glob, os, sys, tomllib
for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError): pass

PLUGIN = os.environ.get("PLUGIN_ROOT") or "."
# VERIFY pattern: the append must contain both "VERIFY" and "error and recreate".
# This forces the LLM to verify the file via the terminal after creating it.
# Only for gate-enforcing surfaces (write code / check merges / produce bridge);
# documentary record producers (research-experiment, sprint-planning, etc.)
# rely on their own --validate line.
KOPRU = {
    "bmad-dev-story", "bmad-dev-auto", "bmad-quick-dev",
    "bmad-code-review", "bmad-create-story",
    "gds-dev-story", "gds-code-review", "gds-quick-dev",
}

problems = []
checked = 0
for name in sorted(KOPRU):
    path = os.path.join(PLUGIN, "custom", name + ".toml")
    if not os.path.isfile(path):
        continue
    try:
        data = tomllib.load(open(path, "rb"))
    except Exception:
        continue
    checked += 1
    asa = data.get("workflow", {}).get("activation_steps_append", []) or []
    blob = "\n".join(asa)
    low = blob.lower()
    # Accept both legacy Turkish markers and the new English markers.
    has_verify = any(m in blob for m in ("DOGRULAMA", "VERIFICATION", "VERIFY"))
    has_error = any(m in low for m in ("hata ver", "error and recreate"))
    if not has_verify or not has_error:
        problems.append("%s: verify + error pattern missing" % name)

print("  checked bridge skills: %d" % checked)
for p in problems:
    print("  MISS: %s" % p)
raise SystemExit(1 if problems else 0)
PY
if [ $? -eq 0 ]; then
    echo "[OK]   verify+error pattern present in all bridge skills"
else
    echo "[WARNING] verify pattern missing (see above)"
    PROBLEMS=$((PROBLEMS + 1))
fi

echo "== 5) gds-* Mod A bridge reference (dev-skill-to-methodology-bridge) =="
# Note: the hard-gate keyword audit is done in §3; here we only check whether
# the bmm module (gds) gives an explicit reference to the bridge document —
# DRY (no duplicate hard-gate report).
"$PY" - <<'PY'
import glob, os, sys, tomllib
for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError): pass

PLUGIN = os.environ.get("PLUGIN_ROOT") or "."
GDS_MOD_A = {
    "gds-dev-story", "gds-create-story",
    "gds-code-review", "gds-quick-dev",
}

problems = []
checked = 0
for name in sorted(GDS_MOD_A):
    path = os.path.join(PLUGIN, "custom", name + ".toml")
    if not os.path.isfile(path):
        continue
    try:
        data = tomllib.load(open(path, "rb"))
    except Exception:
        continue
    checked += 1
    blob_parts = []
    for sec in ("workflow", "agent"):
        s = data.get(sec, {})
        blob_parts += s.get("activation_steps_append", []) or []
        blob_parts += s.get("principles", []) or []
    blob = "\n".join(blob_parts)
    if "dev-skill-to-methodology-bridge" not in blob:
        problems.append("%s: no bridge reference (gds Mod A bmm contract)" % name)

print("  checked gds Mod A: %d" % checked)
for p in problems:
    print("  MISS: %s" % p)
raise SystemExit(1 if problems else 0)
PY
if [ $? -eq 0 ]; then
    echo "[OK]   gds Mod A bridge references present"
else
    echo "[WARNING] gds Mod A bridge reference missing (see above)"
    PROBLEMS=$((PROBLEMS + 1))
fi

echo "== 6) config.toml [hooks] soft/hard contract =="
"$PY" - <<'PY'
import os, sys, tomllib
for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError): pass

PLUGIN = os.environ.get("PLUGIN_ROOT") or "."
cfg = os.path.join(PLUGIN, "custom", "config.toml")
if not os.path.isfile(cfg):
    print("  [ERROR] %s missing" % cfg)
    sys.exit(1)
try:
    d = tomllib.load(open(cfg, "rb"))
except Exception as e:
    print("  [ERROR] config parse: %s" % e)
    sys.exit(1)
h = d.get("hooks", {}) or {}
qg = str(h.get("quality_gate", "soft")).strip().lower()
dg = str(h.get("deploy_guard", "soft")).strip().lower()
cg = str(h.get("code_guard", "hard")).strip().lower()
sg = str(h.get("stop_guard", "hard")).strip().lower()
print("  quality_gate: %s" % qg)
print("  deploy_guard: %s" % dg)
print("  code_guard: %s" % cg)
print("  stop_guard: %s" % sg)
problems = []
for name, val in (("quality_gate", qg), ("deploy_guard", dg),
                  ("code_guard", cg), ("stop_guard", sg)):
    if val not in ("soft", "hard"):
        problems.append("%s = %r (invalid — must be soft|hard)" % (name, val))
if problems:
    for p in problems:
        print("  MISS: %s" % p)
    sys.exit(1)
sys.exit(0)
PY
if [ $? -eq 0 ]; then
    echo "[OK]   config soft/hard contract valid"
else
    echo "[WARNING] config soft/hard issue (see above)"
    PROBLEMS=$((PROBLEMS + 1))
fi

echo "== 6b) custom/ ↔ skills/ pairing (orphan TOMLs, missing SKILL.md) =="
# Every custom/<name>.toml (except config.toml) must pair with skills/<name>/SKILL.md.
"$PY" - <<'PY'
import glob, os, sys
PLUGIN = os.environ.get("PLUGIN_ROOT") or "."
problems = []
for path in sorted(glob.glob(os.path.join(PLUGIN, "custom", "*.toml"))):
    name = os.path.basename(path)[:-5]
    if name == "config":
        continue
    skill_md = os.path.join(PLUGIN, "skills", name, "SKILL.md")
    if not os.path.isfile(skill_md):
        problems.append("%s.toml: no matching skills/%s/SKILL.md (orphan)" % (name, name))
for skill_dir in sorted(glob.glob(os.path.join(PLUGIN, "skills", "*"))):
    if not os.path.isdir(skill_dir):
        continue
    if not os.path.isfile(os.path.join(skill_dir, "SKILL.md")):
        problems.append("skills/%s: SKILL.md missing" % os.path.basename(skill_dir))
for p in problems:
    print("  MISS: %s" % p)
raise SystemExit(1 if problems else 0)
PY
if [ $? -eq 0 ]; then
    echo "[OK]   custom/ ↔ skills/ pairing intact"
else
    echo "[WARNING] orphan TOML or missing SKILL.md (see above)"
    PROBLEMS=$((PROBLEMS + 1))
fi

echo "== 7) Bridge document §N.N reference drift audit =="
# In custom/*.toml files carrying a dev-skill-to-methodology-bridge reference,
# every §N.N used (or the "bolum N.N" pattern) must actually exist in bridge.md.
# This catches drift in custom/ when the bridge document is updated (wrong
# references born from deleted/renamed sections).
"$PY" - <<'PY'
import glob, os, re, sys, tomllib
for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError): pass

PLUGIN = os.environ.get("PLUGIN_ROOT") or "."
BRIDGE = os.path.join(PLUGIN, "docs", "bmad", "dev-skill-to-methodology-bridge.md")
if not os.path.isfile(BRIDGE):
    print("  [ERROR] bridge document missing: %s" % BRIDGE)
    sys.exit(1)

# Extract bridge sections: ## §N or ### §N.N[a-z]?
bridge_secs = set()
with open(BRIDGE, encoding="utf-8") as f:
    for m in re.finditer(r'^(?:##|###) §([0-9]+(?:\.[0-9]+[a-z]?)?)', f.read(), re.M):
        bridge_secs.add(m.group(1))

# Catch three patterns in each file: "§N.N", "section N.N", and "bolum/bölüm N.N"
# (case insensitive). The second and third patterns catch misspellings that
# forgot the § prefix (e.g. "bolum 1.1 ve 3.1 Faz 3" — seen in 7 gds-* test files).
SECTION_RE = re.compile(
    r'(?:§([0-9]+(?:\.[0-9]+[a-z]?)?)'
    r'|(?:section|bol[uü]m)\s+([0-9]+(?:\.[0-9]+[a-z]?)?))',
    re.IGNORECASE,
)

problems = []
checked = 0
for path in sorted(glob.glob(os.path.join(PLUGIN, "custom", "*.toml"))):
    name = os.path.basename(path)[:-5]
    if name == "config":
        continue
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        continue
    # Files not carrying a bridge reference are exempt (they may reference
    # different manifestos — audit only those that use the bridge).
    if "dev-skill-to-methodology-bridge" not in text:
        continue
    checked += 1
    refs = set()
    for m in SECTION_RE.finditer(text):
        sec = m.group(1) or m.group(2)
        if sec:
            refs.add(sec)
    unknown = sorted(refs - bridge_secs)
    if unknown:
        problems.append("%s: §N.N not in bridge → %s (typo or stale "
                        "section; check bridge.md)" %
                        (name, ", ".join(unknown)))

print("  checked bridge users: %d" % checked)
for p in problems:
    print("  MISS: %s" % p)
raise SystemExit(1 if problems else 0)
PY
if [ $? -eq 0 ]; then
    echo "[OK]   bridge §N.N references in sync with bridge.md"
else
    echo "[WARNING] bridge §N.N drift (see above)"
    PROBLEMS=$((PROBLEMS + 1))
fi

echo
if [ "${PROBLEMS:-0}" -eq 0 ]; then
    echo "STATUS: HEALTHY (custom/ passed all static checks)"
    exit 0
else
    echo "STATUS: ${PROBLEMS} problems found"
    exit 1
fi

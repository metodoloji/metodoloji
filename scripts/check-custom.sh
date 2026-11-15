#!/bin/sh
# check-custom.sh — static quality audit of the skill customize.toml bridge TOMLs.
#
#   0. TOML parse + table presence
#   1. Persistent-facts pair  (methodology contract / project-context)
#   2. activation_steps_append depth
#        Gate enforcers (GATE_REQUIRED)  → >= 3 steps
#        Other workflows (MOD_A_RECORD_ONLY + tools) → >= 1 step
#   2b. activation_steps_append compliance frame
#        Every entry must carry the methodology contract (a structured
#        "LABEL: ..." check, a before/after/during frame, or a named
#        contract token) — never a bare workflow-stage command. The field is
#        executed at Step 7 ("in order ... before starting the work"), so a
#        workflow body there runs the whole flow during activation (E-040).
#   3. Hard-gate keywords
#        GATE_REQUIRED    → must contain ONAYLANDI/REDDEDİLDİ/FORGED/VERIFIED
#        GATE_REFERENCE_OK → may carry as reference (e.g. bmad-quality-record)
#        Others → leak (wrong layer) → fail
#   4. Bridge verify pattern (gate enforcers: "ls -la ... error and recreate")
#   6. config.toml [hooks] soft/hard contract (DRY: same parser as the config audit)
#   7. Record steps name a live owner (shipped template + chain-spec stage)
#
# Usage:  sh scripts/check-custom.sh
#            sh scripts/check-custom.sh --negtest
#            (negative-test only, 4 stages: §3 hard-gate + §7 dead owner ×2
#             (unshipped template, unknown stage) + §2b bare activation step
#             → catch MISS → restore)
# Output:    [OK] / [WARNING] / [ERROR] at the start of each line; overall status at the end.
set -u

PROBLEMS=0

if [ "${1:-}" = "--negtest" ]; then
    # Negative test 1: §3 hard-gate — temporarily remove the hard-gate
    # keywords (ONAYLANDI / REDDEDİLDİ / FORGED / VERIFIED) from
    # bmad-dev-story.toml, verify §3 logic produces a MISS, then restore the file.
    # Negative tests 2/3: §7 dead owner — inject a step naming an unshipped
    # template (2) and a stage outside the chain spec (3); §7 must MISS.
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
toml = PLUGIN / "skills" / "bmad-dev-story" / "customize.toml"
# Accept both the legacy Turkish markers and the new English markers.
KEYWORDS = ("APPROVED", "REJECTED", "FORGED", "VERIFIED", "ONAYLANDI", "REDDEDİLDİ")
# Single source of truth for the stage count (E-026): the three banners and the
# closing line below all read it, and the bench pins it to the stages that exist.
total_stages = 7

orig_toml = toml.read_text(encoding="utf-8")

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
    print("[ERROR] hard-gate error visible even in the intact customize.toml — test setup broken")
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
print(f"[OK]   negative test 1/{total_stages}: hard-gate keywords removed → §3 MISS caught")

# ---- Test 2: §7 a record step naming a template that does not ship ----
def has_dead_owner_error() -> bool:
    """§7: a record step naming a template the tree does not ship must MISS."""
    r = subprocess.run(
        ["sh", str(PLUGIN / "scripts" / "check-custom.sh")],
        capture_output=True, text=True, encoding="utf-8", timeout=30)
    for line in r.stdout.splitlines():
        if "does not ship" in line or "not in the chain spec" in line:
            return True
    return False

if has_dead_owner_error():
    print("[ERROR] §7 reports a dead owner in the intact tree — test setup broken")
    sys.exit(1)

# Inject a step that names a template the tree does not ship.
broken_toml = orig_toml + (
    '\n  "Create the record from {metodoloji-root}/templates/_template_NOPE.md.",\n')
# Re-open the array so the injected line lands inside it.
broken_toml = broken_toml.replace(
    "activation_steps_append = [",
    'activation_steps_append = [\n  "Create the record from '
    '{metodoloji-root}/templates/_template_NOPE.md.",', 1)
toml.write_text(broken_toml, encoding="utf-8")
try:
    caught = has_dead_owner_error()
finally:
    toml.write_text(orig_toml, encoding="utf-8")
if not caught:
    print("[ERROR] negative test 2 failed: §7 did not catch a template that does not ship")
    sys.exit(1)
print(f"[OK]   negative test 2/{total_stages}: unshipped template named → §7 MISS caught → TOML restored")

# ---- Test 3: §7 a record step naming a stage the chain spec does not define ----
broken_toml = orig_toml.replace(
    "activation_steps_append = [",
    'activation_steps_append = [\n  "Run workflow.py complete --stage '
    'not-a-real-stage --slug S-001 --project-root {project-root}.",', 1)
toml.write_text(broken_toml, encoding="utf-8")
try:
    caught = has_dead_owner_error()
finally:
    toml.write_text(orig_toml, encoding="utf-8")
if not caught:
    print("[ERROR] negative test 3 failed: §7 did not catch a stage outside the chain spec")
    sys.exit(1)
print(f"[OK]   negative test 3/{total_stages}: unknown stage named → §7 MISS caught → TOML restored")

# ---- Test 4: §2b activation compliance frame ----
# Inject a bare workflow-stage command into a custom TOML's
# activation_steps_append — §2b must flag it (the defect E-040 fixed).
bmb = PLUGIN / "skills" / "bmad-workflow-builder" / "customize.toml"
orig_bmb = bmb.read_text(encoding="utf-8")

def has_bare_activation_error(text: str) -> bool:
    bmb.write_text(text, encoding="utf-8")
    try:
        r = subprocess.run(
            ["sh", str(PLUGIN / "scripts" / "check-custom.sh")],
            capture_output=True, text=True, encoding="utf-8", timeout=30)
        out = r.stdout
        for line in out.splitlines():
            if "bmad-workflow-builder" in line and "bare workflow step" in line:
                return True
        return False
    finally:
        bmb.write_text(orig_bmb, encoding="utf-8")

if has_bare_activation_error(orig_bmb):
    print("[ERROR] bare-activation error visible even in the intact TOML — test setup broken")
    sys.exit(1)

injected_bmb = orig_bmb.replace(
    "activation_steps_append = [",
    'activation_steps_append = [\n  "Install the dependencies",', 1)
if injected_bmb == orig_bmb:
    print("[ERROR] could not inject a bare activation step — test setup broken")
    sys.exit(1)
if not has_bare_activation_error(injected_bmb):
    print("[ERROR] negative test 4 failed: §2b logic did not catch a bare activation step")
    sys.exit(1)
print(f"[OK]   negative test 4/{total_stages}: bare workflow step injected → §2b MISS caught → TOML restored")

# ---- Test 5: §1 a run-owning surface that lost its kernel pointer ----
# The chain spec declares the ORDER; workflow.py is what opens and advances a
# run. A surface that names only the spec describes the chain and never drives
# it, so the stage can look owned and stay unreachable.
cr = PLUGIN / "skills" / "bmad-code-review" / "customize.toml"
orig_cr = cr.read_text(encoding="utf-8")

def has_no_kernel_error(text: str) -> bool:
    cr.write_text(text, encoding="utf-8")
    try:
        r = subprocess.run(
            ["sh", str(PLUGIN / "scripts" / "check-custom.sh")],
            capture_output=True, text=True, encoding="utf-8", timeout=30)
        return any("never names the kernel" in line for line in r.stdout.splitlines())
    finally:
        cr.write_text(orig_cr, encoding="utf-8")

if has_no_kernel_error(orig_cr):
    print("[ERROR] kernel-pointer error visible even in the intact TOML — test setup broken")
    sys.exit(1)
broken_cr = orig_cr.replace("hooks/scripts/workflow.py", "hooks/scripts/not-the-kernel.py")
if broken_cr == orig_cr:
    print("[ERROR] could not break the kernel pointer — test setup broken")
    sys.exit(1)
if not has_no_kernel_error(broken_cr):
    print("[ERROR] negative test 5 failed: §1 did not catch a run owner without the kernel")
    sys.exit(1)
print(f"[OK]   negative test 5/{total_stages}: kernel pointer removed → §1 MISS caught → TOML restored")

# ---- Test 6: §7 a shipped command the shell cannot run ----
# `--flag tier=<1|2|3>` is a redirect and a pipe to sh: the copy runs three
# commands or none, and the run is created with a tier no edge matches.
def has_unrunnable_placeholder_error(text: str) -> bool:
    toml.write_text(text, encoding="utf-8")
    try:
        r = subprocess.run(
            ["sh", str(PLUGIN / "scripts" / "check-custom.sh")],
            capture_output=True, text=True, encoding="utf-8", timeout=30)
        return any("shell cannot run" in line for line in r.stdout.splitlines())
    finally:
        toml.write_text(orig_toml, encoding="utf-8")

if has_unrunnable_placeholder_error(orig_toml):
    print("[ERROR] placeholder error visible even in the intact TOML — test setup broken")
    sys.exit(1)
broken_toml = orig_toml.replace(
    "activation_steps_append = [",
    'activation_steps_append = [\n  "Run workflow.py create --flag tier=<1|2|3> '
    '--slug S-001 --project-root {project-root}.",', 1)
if broken_toml == orig_toml:
    print("[ERROR] could not inject a placeholder flag — test setup broken")
    sys.exit(1)
if not has_unrunnable_placeholder_error(broken_toml):
    print("[ERROR] negative test 6 failed: §7 did not catch an unrunnable placeholder")
    sys.exit(1)
print(f"[OK]   negative test 6/{total_stages}: shell placeholder injected → §7 MISS caught → TOML restored")

# ---- Test 7: §7 a branch declared by a surface that cannot reach it ----
# The deploy edge is read when quality-review completes; a producer that
# advances that stage without naming the flag sends the run to `done`.
qr = PLUGIN / "skills" / "bmad-quality-record" / "customize.toml"
orig_qr = qr.read_text(encoding="utf-8")

def has_undeclared_branch_error(text: str) -> bool:
    qr.write_text(text, encoding="utf-8")
    try:
        r = subprocess.run(
            ["sh", str(PLUGIN / "scripts" / "check-custom.sh")],
            capture_output=True, text=True, encoding="utf-8", timeout=30)
        return any("without declaring the 'deploy' branch" in line
                   for line in r.stdout.splitlines())
    finally:
        qr.write_text(orig_qr, encoding="utf-8")

if has_undeclared_branch_error(orig_qr):
    print("[ERROR] branch error visible even in the intact TOML — test setup broken")
    sys.exit(1)
broken_qr = orig_qr.replace("--key deploy --value true", "--key other --value true")
if broken_qr == orig_qr:
    print("[ERROR] could not drop the deploy declaration — test setup broken")
    sys.exit(1)
if not has_undeclared_branch_error(broken_qr):
    print("[ERROR] negative test 7 failed: §7 did not catch an undeclared branch")
    sys.exit(1)
print(f"[OK]   negative test 7/{total_stages}: deploy declaration dropped → §7 MISS caught → TOML restored")
print(f"[OK]   all {total_stages} negtest stages successful")
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
for path in sorted(glob.glob(os.path.join(PLUGIN, "skills", "*", "customize.toml"))):
    name = os.path.basename(os.path.dirname(path))
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

echo "== 1) Persistent-facts pair (methodology contract / project-context) =="
"$PY" - <<'PY'
import glob, os, sys, tomllib
for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError): pass

PLUGIN = os.environ.get("PLUGIN_ROOT") or "."
# Development wing — those in this list must load the chain spec
# (hooks/workflow/builtin/methodology-chain.json) plus project-context. For
# those not in the list only project-context.md is required.
# Note: bmad-eval-runner / bmad-research-experiment / bmad-review-* are
# deliberately not in the list — they "don't write code, they measure/audit".
DEV_WING = {
    "bmad-check-implementation-readiness", "bmad-prd", "bmad-ux",
    "bmad-create-architecture", "bmad-sprint-planning", "bmad-create-story",
    "bmad-create-epics-and-stories", "bmad-dev-story", "bmad-quick-dev",
    "bmad-dev-auto",
    "bmad-code-review",
    "bmad-document-project",
    "bmad-retrospective", "bmad-correct-course", "bmad-sprint-status",
}

# Surfaces that own a methodology-chain stage. The chain spec declares the
# ORDER; `workflow.py` is the only thing that opens or advances a run. A surface
# that names the spec but never the kernel can describe the chain and never
# drive it — the gap this list closes, measured instead of remembered.
RUN_OWNERS = {
    "bmad-create-story", "bmad-dev-story", "bmad-dev-auto", "bmad-quick-dev",
    "bmad-code-review", "bmad-quality-record", "bmad-sprint-planning",
    "bmad-check-implementation-readiness", "bmad-production-readiness",
    "bmad-workflow",
}

problems = []
checked = 0
for path in sorted(glob.glob(os.path.join(PLUGIN, "skills", "*", "customize.toml"))):
    name = os.path.basename(os.path.dirname(path))
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
        # The contract page is retired (E-151): the rule text lives in its owners
        # (chain spec, templates, create-record.py), and the spec is the pointer.
        if not any("methodology-chain.json" in x for x in facts):
            problems.append("%s: no chain spec pointer (DEV wing)" % name)
        elif not any("hooks/workflow/builtin/methodology-chain.json" in x for x in facts):
            problems.append("%s: chain spec pointer not plugin-canonical ({metodoloji-root})" % name)
    if name in RUN_OWNERS:
        # The spec declares the order; the kernel is what opens and advances a
        # run. A surface that reads only the spec describes the chain and never
        # drives it, so a stage can look owned and stay unreachable.
        text = ""
        for rel in ("customize.toml", "SKILL.md"):
            f = os.path.join(os.path.dirname(path), rel)
            if os.path.isfile(f):
                text += open(f, encoding="utf-8").read()
        if "hooks/scripts/workflow.py" not in text:
            problems.append("%s: owns a chain stage but never names the kernel "
                            "(hooks/scripts/workflow.py)" % name)

print("  checked: %d" % checked)
for p in problems:
    print("  MISS: %s" % p)
raise SystemExit(1 if problems else 0)
PY
if [ $? -eq 0 ]; then
    echo "[OK]   persistent_facts contract + project-context present on all surfaces"
else
    echo "[WARNING] persistent_facts incomplete (see above)"
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
    "bmad-code-review",
    "bmad-create-story",   # ONAYLANDI check on AC experiments = gate verifier
}
MOD_A_RECORD_ONLY = {
    "bmad-research-experiment", "bmad-check-implementation-readiness",
    "bmad-sprint-planning",
}
MIN_RECORD = 1
MIN_GATE = 3

problems = []
checked = 0
for path in sorted(glob.glob(os.path.join(PLUGIN, "skills", "*", "customize.toml"))):
    name = os.path.basename(os.path.dirname(path))
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
        # Agent surfaces carry no activation steps (that's fine). Since E-147 the
        # surface is the skill's customize.toml, which also holds the resolver's
        # default [workflow].persistent_facts, so "no [workflow] table" is no
        # longer the test — an empty workflow step list on an [agent] file is.
        if "agent" in data:
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

echo "== 2b) activation_steps_append compliance frame =="
"$PY" - <<'PY'
import glob, os, sys, tomllib
for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError): pass

PLUGIN = os.environ.get("PLUGIN_ROOT") or "."
# The field is documented as "pre-flight loads, compliance checks": an
# activation step exists to carry the methodology contract, not to restate
# the workflow. The skill executes every entry at Step 7 ("in order ...
# before starting the work"), so a bare workflow-stage command there runs the
# whole flow during activation — E-040: the team override shipped "Define the
# hypothesis" / "Create the experiment design" / "Analyze the results" as
# activation steps (and the same shape in bmb/index/loop/module/shard).
# A valid step carries at least one frame marker:
#   - a structured check ("LABEL: ..."),
#   - a temporal/compliance frame ("before ", "after ", "during ", "per "),
#   - or a named contract token (bridge, methodology manifesto, record chain,
#     Mod A, APPROVED/REJECTED, VERIFY, "error and recreate", ...).
# Matched case-insensitively against step.lower(); all lowercase here.
MARKERS = (":", "before ", "after ", "during ", "per ", "bridge",
           "methodology manifesto", "record chain", "mod a", "do not ",
           "never ", "error and recreate", "approved", "rejected", "verify",
           "compliance", "manifesto")

problems = []
checked = 0
for path in sorted(glob.glob(os.path.join(PLUGIN, "skills", "*", "customize.toml"))):
    name = os.path.basename(os.path.dirname(path))
    if name == "config":
        continue
    try:
        data = tomllib.load(open(path, "rb"))
    except Exception:
        continue
    asa = data.get("workflow", {}).get("activation_steps_append", []) or []
    if not asa:
        continue
    checked += 1
    for step in asa:
        if not any(m in step.lower() for m in MARKERS):
            problems.append("%s: bare workflow step in "
                            "activation_steps_append — %r" % (name, step[:70]))

print("  checked: %d" % checked)
for p in problems:
    print("  MISS: %s" % p)
raise SystemExit(1 if problems else 0)
PY
if [ $? -eq 0 ]; then
    echo "[OK]   activation_steps_append entries carry the methodology contract"
else
    echo "[WARNING] bare workflow step in activation (see above)"
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
    "bmad-code-review",
    "bmad-create-story",   # APPROVED check on AC experiments = gate verifier
}
# Surfaces that carry the gate as a reference but don't enforce it (principle
# statement or for training). For those in this list the hard-gate keyword is
# not counted as a leak; appearing anywhere else is an error.
GATE_REFERENCE_OK = {
    "bmad-quality-record",        # QR consumer: reads experiment/story gate status
    "bmad-production-readiness",  # PR consumer: reads QR gate status before certifying
    # Mod A record producer (§2 MOD_A_RECORD_ONLY): its default step RESTATES the
    # decision (APPROVED / REJECTED) to the user; it does not enforce the gate.
    "bmad-research-experiment",
}
# May appear in both activation_steps_append and principles.
# Accept both the legacy Turkish markers and the new English markers.
KEYWORDS = ("APPROVED", "REJECTED", "FORGED", "VERIFIED", "ONAYLANDI", "REDDEDİLDİ")

problems = []
checked = 0
for path in sorted(glob.glob(os.path.join(PLUGIN, "skills", "*", "customize.toml"))):
    name = os.path.basename(os.path.dirname(path))
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
}

problems = []
checked = 0
for name in sorted(KOPRU):
    path = os.path.join(PLUGIN, "skills", name, "customize.toml")
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

echo "== 6) config.toml [hooks] soft/hard contract =="
"$PY" - <<'PY'
import os, sys, tomllib
for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError): pass

PLUGIN = os.environ.get("PLUGIN_ROOT") or "."
cfg = os.path.join(PLUGIN, "config.toml")
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
for path in sorted(glob.glob(os.path.join(PLUGIN, "skills", "*", "customize.toml"))):
    name = os.path.basename(os.path.dirname(path))
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

echo "== 7) Record steps name a shipped template and a live stage =="
# The bridge document is gone: the order lives in the chain spec and the field
# lists in templates/. A record step that points at a deleted document, a
# template that does not ship, or a stage the spec does not define sends the
# agent to an owner that cannot answer. This measures the step against the
# owners themselves instead of against a prose section number.
"$PY" - <<'PY'
import glob, json, os, re, sys
for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError): pass

PLUGIN = os.environ.get("PLUGIN_ROOT") or "."
SPEC = os.path.join(PLUGIN, "hooks", "workflow", "builtin", "methodology-chain.json")
if not os.path.isfile(SPEC):
    print("  [ERROR] chain spec missing: %s" % SPEC)
    sys.exit(1)
with open(SPEC, encoding="utf-8") as f:
    _spec = json.load(f)
stage_ids = {st["id"] for st in _spec["stages"]}
# Every conditional edge is a branch the producing surface must declare BEFORE
# the source stage completes: the edge consumes the flag, so a flag set after
# the transition is read by nothing (the deploy branch reached exactly this).
branch_keys = {st["id"]: {e["when"]["key"] for e in st.get("next", []) if e.get("when")}
               for st in _spec["stages"]}
shipped = {os.path.basename(p) for p in
           glob.glob(os.path.join(PLUGIN, "templates", "_template_*.md"))}

SURFACES = (sorted(glob.glob(os.path.join(PLUGIN, "skills", "*", "customize.toml")))
            + sorted(glob.glob(os.path.join(PLUGIN, "skills", "*", "SKILL.md")))
            + sorted(glob.glob(os.path.join(PLUGIN, "commands", "*.md"))))
problems = []
checked = 0
for path in SURFACES:
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        continue
    rel = os.path.relpath(path, PLUGIN).replace(os.sep, "/")
    # A conditional edge consumes its flag when it is taken, so the surface that
    # advances the source stage must declare the branch first. Naming the stage
    # and not the flag is how the deploy branch became unreachable: quality-review
    # completed, the run went to `done`, and the PR producer's flag arrived after
    # the only edge that reads it. Checked before the record filter below: a
    # KERNEL line need not mention a record path to own a stage.
    for stage in set(re.findall(r"--stage\s+([a-z-]+)", text)):
        for key in branch_keys.get(stage, ()):
            # A branch is declared at create (`--flag tier=1`) or later
            # (`--key deploy --value true`); both spellings put the key in the text.
            if not re.search(r"--(?:key|flag)\s+%s\b" % re.escape(key), text):
                problems.append("%s: advances '%s' without declaring the '%s' "
                                "branch its conditional edge reads"
                                % (rel, stage, key))
    if "docs/development/" not in text and "docs/quality/QR" not in text:
        continue
    checked += 1
    if re.search(r"\{metodoloji-root\}/(docs/bmad|docs/methodology\.md)"
                 r"|docs/(bmad|methodology)[A-Za-z0-9_./-]*\.md", text):
        problems.append("%s: still points at a retired methodology doc" % rel)
    for name in set(re.findall(r"_template_[A-Za-z0-9]+\.md", text)):
        if name not in shipped:
            problems.append("%s: names %s, which does not ship" % (rel, name))
    for stage in set(re.findall(r"--stage\s+([a-z-]+)", text)):
        if stage not in stage_ids:
            problems.append("%s: names stage '%s', not in the chain spec" % (rel, stage))
    # A shipped command is copied, not read: an alternation or angle-bracket
    # placeholder inside a `--flag`/`--stage` value is a pipe and a redirect to
    # the shell, so the copy runs three commands or none (the observed failure:
    # `--flag tier=<1|2|3>` created a run whose tier no edge matched).
    if re.search(r"--flag\s+\w+=[^\s`\"']*[<>|]", text) or \
       re.search(r"--stage\s+[a-z-]+\|", text):
        problems.append("%s: a --flag/--stage example carries a placeholder the "
                        "shell cannot run (alternation or angle brackets)" % rel)

print("  checked record surfaces: %d" % checked)
for p in problems:
    print("  MISS: %s" % p)
raise SystemExit(1 if problems else 0)
PY
if [ $? -eq 0 ]; then
    echo "[OK]   record steps name live owners (templates + chain stages)"
else
    echo "[WARNING] record step points at a dead owner (see above)"
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

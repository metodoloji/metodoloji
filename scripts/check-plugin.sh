#!/bin/sh
# check-plugin.sh — methodology plugin health check (single command, plugin variant).
#
#   0. Is the gate key installed?            (run_experiment.py --check-secret)
#   0b. Base config carries no project-name leak (bmad/config.toml is shared by
#       every install; a real name there renames other projects' run folders)
#   1. Gate + hook engine selfcheck        (plugin copies, both)
#   1b. hooks.json dispatch locator drift  (generated from sync-hooks-json.py; == run-hook.sh roots)
#   2. Manifesto + project-context wiring (for EVERY surface) + bridge audit
#   2b. Are bridge instructions visible at runtime? (resolve_customization deep_merge)
#   3. Approved experiment inventory              (records where the guard opened code writing)
#   4. Documentary (B/C/D) record completeness  (run_experiment.py --validate)
#   5. Engine drift audit               (plugin engine == repo canonical, if repo reachable)
#   5b. Hard gate enforcement mode (soft/hard — custom/config.toml [hooks])
#   5c. custom/ bridge TOMLs static quality audit (scripts/check-custom.sh)
#   6. Development records format check  (run_experiment.py --validate)
#   6c. Template copy identity (templates/ ↔ docs/_template copies)
#   6d. Init marker integrity (skeleton installed ⇔ .metodoloji/initialized)
#   6e. Help catalog integrity (13 columns, no new code collisions, no
#       record-prefix menu code on a non-producing skill)
#   6f. Trigger/description collisions (routing determinism)
#   6g. No namespaced-tool priming tokens in shipped agent-facing text
#   Coverage map (authoritative for docs/wiki — see docs/SELF-CHECK.md)
#   docs/images/*.png are illustrative, NOT self-check inputs.
#
# Usage:  sh scripts/check-plugin.sh   (from the plugin root or anywhere;
#            target project root is cwd or $OPENHANDS_PROJECT_DIR)
#            sh scripts/check-plugin.sh --negtest
#            (negative tests, 8 stages: §6a .env inventory, §2b BRIDGE
#             visibility ×2 (skill + agent principles), §1b dispatch locator
#             drift, §6c template copy drift, §6d init marker integrity,
#             §6e help catalog integrity, §6g priming-token scan —
#             break → catch MISS → restore)
# Output:    [OK] / [WARNING] / [ERROR] at the start of each line; overall status at the end.

set -u

if [ "${1:-}" = "--negtest" ]; then
    # Negative test (repo convention: break → catch MISS → restore).
    # Temporarily removes the BRIDGE line from custom/bmad-dev-story.toml,
    # verifies §2b logic produces a MISS, then restores the file.
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
import json, os, subprocess, sys
from pathlib import Path

PLUGIN = Path(os.environ["PLUGIN_ROOT"])
check_script = PLUGIN / "scripts" / "check-plugin.sh"
total_stages = 8
# The full check runs every section (engine audit, gate, records, catalog) and
# takes ~65s on Windows CI-class disks, so the 60s budget it used to carry made
# stage 1 fail as a timeout rather than as a fixable defect.

# Stage 1/8: .env line removed from .gitignore → §6a.2 should catch an ERROR
print(f"[1/{total_stages}] does §6a emit an ERROR when .env is removed from .gitignore")
gitignore = PLUGIN / ".gitignore"
orig_gitignore_bytes = gitignore.read_bytes()
orig_gitignore = orig_gitignore_bytes.decode("utf-8")
broken = "\n".join(l for l in orig_gitignore.splitlines() if l.strip() != ".env")
try:
    gitignore.write_text(broken, encoding="utf-8")
    r = subprocess.run(
        ["sh", str(check_script)],
        capture_output=True, text=True, encoding="utf-8", timeout=300,
        cwd=str(PLUGIN),
    )
    if ".env line missing in .gitignore" in r.stdout and r.returncode == 1:
        print("  [OK] §6a.3 ERROR caught, exit=1")
    else:
        print(f"  [ERROR] §6a.3 ERROR expected, output: ...{r.stdout[-400:]!r}")
        sys.exit(1)
finally:
    gitignore.write_bytes(orig_gitignore_bytes)  # byte-exact: no CRLF/LF churn

# Stage 2/8: BRIDGE removed from custom/bmad-dev-story.toml → §2b should catch a MISS
print(f"[2/{total_stages}] does §2b emit a MISS when BRIDGE is removed from custom/bmad-dev-story.toml")
toml = PLUGIN / "custom" / "bmad-dev-story.toml"
resolver = PLUGIN / "hooks" / "engine" / "resolve_customization.py"
skill = PLUGIN / "skills" / "bmad-dev-story"
orig_bytes = toml.read_bytes()
orig = orig_bytes.decode("utf-8")

def bridge_visible(text: str) -> bool:
    # write_bytes, not write_text: text mode translates \n → os.linesep on
    # Windows, turning the file's \r\n into \r\r\n, which tomllib rejects with
    # "invalid character '\r'" — the resolver then drops custom/ and the test
    # falsely reports the BRIDGE as invisible.
    toml.write_bytes(text.encode("utf-8"))
    try:
        r = subprocess.run(
            [sys.executable, str(resolver), "-s", str(skill),
             "-k", "workflow.activation_steps_append"],
            capture_output=True, text=True, encoding="utf-8", timeout=15)
        d = json.loads(r.stdout)
        return any("BRIDGE" in s for s in d.get("workflow.activation_steps_append", []))
    finally:
        toml.write_bytes(orig_bytes)  # byte-exact: no CRLF/LF churn

try:
    broken = "\n".join(l for l in orig.splitlines() if "BRIDGE" not in l)
    if not bridge_visible(orig):
        print("  [ERROR] BRIDGE not visible even in intact custom TOML — test setup broken")
        sys.exit(1)
    if bridge_visible(broken):
        print("  [ERROR] §2b MISS expected, BRIDGE still visible after removal")
        sys.exit(1)
    print("  [OK] §2b MISS caught, custom TOML restored")
finally:
    toml.write_bytes(orig_bytes)

# Stage 3/8: BRIDGE removed from an agent-principles surface → §2b should catch a MISS
print(f"[3/{total_stages}] does §2b emit a MISS when BRIDGE is removed from custom/bmad-agent-dev.toml (agent.principles)")
atoml = PLUGIN / "custom" / "bmad-agent-dev.toml"
askill = PLUGIN / "skills" / "bmad-agent-dev"
aorig_bytes = atoml.read_bytes()
aorig = aorig_bytes.decode("utf-8")

def agent_bridge_visible(text: str) -> bool:
    # write_bytes: see bridge_visible — text mode corrupts CRLF on Windows.
    atoml.write_bytes(text.encode("utf-8"))
    try:
        r = subprocess.run(
            [sys.executable, str(resolver), "-s", str(askill),
             "-k", "agent.principles"],
            capture_output=True, text=True, encoding="utf-8", timeout=15)
        d = json.loads(r.stdout)
        return any("BRIDGE" in s for s in d.get("agent.principles", []))
    finally:
        atoml.write_bytes(aorig_bytes)  # byte-exact: no CRLF/LF churn

try:
    abroken = "\n".join(l for l in aorig.splitlines() if "BRIDGE" not in l)
    if not agent_bridge_visible(aorig):
        print("  [ERROR] agent BRIDGE not visible even in intact custom TOML — test setup broken")
        sys.exit(1)
    if agent_bridge_visible(abroken):
        print("  [ERROR] §2b MISS expected for agent.principles, BRIDGE still visible after removal")
        sys.exit(1)
    print("  [OK] agent-principles §2b MISS caught, custom TOML restored")
finally:
    atoml.write_bytes(aorig_bytes)

# Stage 4/8: hooks.json locator desynced in ONE hook → §1b should catch drift
print(f"[4/{total_stages}] does §1b catch a desynced hooks.json dispatch locator")
hj = PLUGIN / "hooks" / "hooks.json"
hj_bytes = hj.read_bytes()
hj_orig = hj_bytes.decode("utf-8")
if '"$PWD"' not in hj_orig.replace('\\"', '"'):
    print("  [ERROR] test setup broken: hooks.json has no $PWD locator candidate")
    sys.exit(1)
try:
    # Desync the FIRST hook command only → the six locator lists must diverge.
    broken = hj_orig.replace('\\"$PWD\\"', '\\"$BOGUS_ROOT\\"', 1)
    hj.write_text(broken, encoding="utf-8")
    r = subprocess.run(
        ["sh", str(check_script)],
        capture_output=True, text=True, encoding="utf-8", timeout=300,
        cwd=str(PLUGIN),
    )
    if "distinct locator lists" in r.stdout and r.returncode == 1:
        print("  [OK] §1b drift caught, hooks.json restored, exit=1")
    else:
        print(f"  [ERROR] §1b drift expected, output: ...{r.stdout[-400:]!r}")
        sys.exit(1)
finally:
    hj.write_bytes(hj_bytes)  # byte-exact: no CRLF/LF churn

# Stage 5/8: docs template copy desynced from templates/ → §6c should catch DRIFT
print(f"[5/{total_stages}] does §6c catch template copy drift")
tmpl = PLUGIN / "templates" / "_template_IR.md"
cp = PLUGIN / "docs" / "development" / "_template_IR.md"
if not tmpl.is_file() or not cp.is_file():
    print("  [ERROR] test setup broken: templates/_template_IR.md or docs copy missing")
    sys.exit(1)
tmpl_orig = tmpl.read_bytes()
cp_orig = cp.read_bytes()
try:
    cp.write_bytes(cp_orig + b"\n<!-- negtest drift -->\n")
    r = subprocess.run(
        ["sh", str(check_script)],
        capture_output=True, text=True, encoding="utf-8", timeout=300,
        cwd=str(PLUGIN),
    )
    if "DRIFT" in r.stdout and r.returncode == 1:
        print("  [OK] §6c template copy drift caught, exit=1")
    else:
        print(f"  [ERROR] §6c drift expected, output: ...{r.stdout[-400:]!r}")
        sys.exit(1)
finally:
    cp.write_bytes(cp_orig)
    tmpl.write_bytes(tmpl_orig)

# Stage 6/8: init marker removed while the skeleton is present → §6d should ERROR
print(f"[6/{total_stages}] does §6d catch a missing init marker with the skeleton present")
marker = PLUGIN / ".metodoloji" / "initialized"
if not (PLUGIN / "docs" / "experiments" / "_template.md").is_file():
    print("  [ERROR] test setup broken: skeleton copy docs/experiments/_template.md missing")
    sys.exit(1)
if not marker.is_file():
    print("  [ERROR] test setup broken: .metodoloji/initialized missing (dogfood repo should be initialized)")
    sys.exit(1)
marker_orig = marker.read_bytes()
try:
    marker.unlink()
    r = subprocess.run(
        ["sh", str(check_script)],
        capture_output=True, text=True, encoding="utf-8", timeout=300,
        cwd=str(PLUGIN),
    )
    if "record skeleton installed but .metodoloji/initialized is missing" in r.stdout and r.returncode == 1:
        print("  [OK] §6d missing-marker ERROR caught, exit=1")
    else:
        print(f"  [ERROR] §6d missing-marker ERROR expected, output: ...{r.stdout[-400:]!r}")
        sys.exit(1)
finally:
    marker.write_bytes(marker_orig)  # byte-exact restore

# Stage 7/8: a second skill inside one module wears an existing menu code →
# §6e should catch the intra-module duplicate (the ambiguity class that made
# "[SP]" mean two different skills).
print(f"[7/{total_stages}] does §6e catch a duplicate menu code inside one module")
catalog = PLUGIN / "bmad" / "_config" / "bmad-help.csv"
cat_orig = catalog.read_bytes()
cat_text = cat_orig.decode("utf-8")
# Core/bmad-spec ships as SPEC; EP is Core/bmad-editorial-review-prose in the
# same module, so this is exactly an intra-module collision.
if "Core,bmad-spec,Spec,SPEC," not in cat_text:
    print("  [ERROR] test setup broken: Core/bmad-spec row is not SPEC")
    sys.exit(1)
try:
    catalog.write_bytes(cat_text.replace("Core,bmad-spec,Spec,SPEC,",
                                         "Core,bmad-spec,Spec,EP,").encode("utf-8"))
    r = subprocess.run(
        ["sh", str(check_script)],
        capture_output=True, text=True, encoding="utf-8", timeout=300,
        cwd=str(PLUGIN),
    )
    if "duplicate menu code EP inside Core" in r.stdout and r.returncode == 1:
        print("  [OK] §6e duplicate-code ERROR caught, exit=1")
    else:
        print(f"  [ERROR] §6e duplicate-code ERROR expected, output: ...{r.stdout[-400:]!r}")
        sys.exit(1)
finally:
    catalog.write_bytes(cat_orig)  # byte-exact restore

# Stage 8/8: a namespaced-tool token reintroduced into agent-facing text →
# §6g should catch the priming class that made a real session emit `default.Bash`
# (harness: "No such tool available: default.X") after reading its own docs.
print(f"[8/{total_stages}] does §6g catch a reintroduced `default.Bash` priming token")
audit_cmd = PLUGIN / "commands" / "audit.md"
audit_orig = audit_cmd.read_bytes()
try:
    audit_cmd.write_bytes(audit_orig + b"\nUse `default.Bash` for shell calls.\n")
    r = subprocess.run(
        ["sh", str(check_script)],
        capture_output=True, text=True, encoding="utf-8", timeout=300,
        cwd=str(PLUGIN),
    )
    if "namespaced-tool priming token" in r.stdout and r.returncode == 1:
        print("  [OK] §6g priming ERROR caught, exit=1")
    else:
        print(f"  [ERROR] §6g priming ERROR expected, output: ...{r.stdout[-400:]!r}")
        sys.exit(1)
finally:
    audit_cmd.write_bytes(audit_orig)  # byte-exact restore

print(f"[OK] all {total_stages} negtest stages successful")
sys.exit(0)
PY
    exit $?
fi

# PLUGIN_ROOT: derived from this script's location (it lives under commands/).
SELF=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PLUGIN_ROOT=$(CDPATH= cd -- "$SELF/.." && pwd)
export PLUGIN_ROOT

# PROJECT_ROOT: the target project (where records live). OPENHANDS_PROJECT_DIR > cwd.
PROJECT_ROOT=${OPENHANDS_PROJECT_DIR:-$(pwd)}
# Dogfooding: if the script is invoked from within the repo and cwd is the methodology repo, cwd stays valid.
export PROJECT_ROOT

GATE="$PLUGIN_ROOT/skills/bmad-research-experiment/scripts/run_experiment.py"
PROBLEMS=0

# Python resolver: python3 -> python -> py (Windows Launcher). Without it we cannot run.
PY=
for cand in python3 python py; do
    if command -v "$cand" >/dev/null 2>&1; then PY="$cand"; break; fi
done
if [ -z "$PY" ]; then
    echo "[ERROR] python3/python/py not found — check-plugin cannot run." >&2
    exit 1
fi

echo "== 0) Is the gate key installed? =="
if "$PY" "$GATE" --check-secret >/dev/null 2>&1; then
    echo "[OK]   gate key present (HMAC — tokens can be produced)"
else
    echo "[ERROR] gate key MISSING. Run: $PY $GATE --init-secret"
    echo "       (writes ~/.bmad/gate-key outside the repo; without a key no approval/token can be produced)"
    PROBLEMS=$((PROBLEMS + 1))
fi

echo "== 0b) Plugin base config carries no project-name leak =="
# bmad/config.toml is the BASE config layer for every install
# (resolve_config.py layer 1, required=True). A concrete project name there
# becomes the default project_name for every target project that does not
# override it, and run folders are named from project_name
# (prd-{project_name}-{date}) — so a leaked name renames another project's
# artifacts. The graph-engineering-arge session (2026-10-01) spent user turns
# deciding which identity governed its record paths, and reported the leak as a
# live defect. Identity belongs in the project's own layer
# ({project-root}/docs/config.toml); the shipped base must stay neutral.
if "$PY" - "$PLUGIN_ROOT" <<'PY'
import os, sys, tomllib
plugin = sys.argv[1]
NEUTRAL = {"", "unconfigured", "bmad-project", "project"}
path = os.path.join(plugin, "bmad", "config.toml")
try:
    with open(path, "rb") as fh:
        name = str((tomllib.load(fh).get("core") or {}).get("project_name") or "")
except Exception as exc:
    print(f"[ERROR] cannot read bmad/config.toml: {exc}")
    sys.exit(1)
if name.strip().lower() not in NEUTRAL:
    print(f"[ERROR] bmad/config.toml ships project_name='{name}' — a real project "
          f"name in the BASE layer leaks into every install (run folders become "
          f"prd-{name}-...); keep it neutral, set identity in the project layer")
    sys.exit(1)
print(f"[OK]   base project_name is neutral ('{name}')")
sys.exit(0)
PY
then
    :
else
    PROBLEMS=$((PROBLEMS + 1))
fi

echo "== 1) Gate + hook engine selfcheck (plugin copies) =="
if "$PY" "$GATE" --selfcheck >/tmp/meth-selfcheck.$$.log 2>&1; then
    echo "[OK]   gate --selfcheck passed"
else
    echo "[ERROR] gate --selfcheck failed:"
    sed 's/^/       /' /tmp/meth-selfcheck.$$.log
    PROBLEMS=$((PROBLEMS + 1))
fi
rm -f /tmp/meth-selfcheck.$$.log
if echo '{}' | "$PY" "$PLUGIN_ROOT/hooks/engine/main.py" guard --runtime=openhands >/tmp/meth-hooks.$$.log 2>&1; then
    # v2 schema emits hookSpecificOutput.permissionDecision; accept legacy flat "decision" too
    if grep -q -e '"permissionDecision"' -e '"decision"' /tmp/meth-hooks.$$.log; then
        echo "[OK]   hook engine running (main.py guard → returned a decision)"
    else
        echo "[ERROR] hook engine returned no decision:"
        sed 's/^/       /' /tmp/meth-hooks.$$.log
        PROBLEMS=$((PROBLEMS + 1))
    fi
else
    echo "[ERROR] hook engine guard test failed:"
    sed 's/^/       /' /tmp/meth-hooks.$$.log
    PROBLEMS=$((PROBLEMS + 1))
fi
rm -f /tmp/meth-hooks.$$.log
# pre hook test (PreToolUse — combined guard+quality+deploy, single process)
if echo '{}' | "$PY" "$PLUGIN_ROOT/hooks/engine/main.py" pre --runtime=openhands >/tmp/meth-pre.$$.log 2>&1; then
    if grep -q -e '"permissionDecision"' -e '"decision"' /tmp/meth-pre.$$.log; then
        echo "[OK]   hook engine running (main.py pre → returned a decision)"
    else
        echo "[ERROR] hook engine pre returned no decision:"
        sed 's/^/       /' /tmp/meth-pre.$$.log
        PROBLEMS=$((PROBLEMS + 1))
    fi
else
    echo "[ERROR] hook engine pre test failed:"
    sed 's/^/       /' /tmp/meth-pre.$$.log
    PROBLEMS=$((PROBLEMS + 1))
fi
rm -f /tmp/meth-pre.$$.log
# quality hook test (PreToolUse — direct gate invocation; hooks.json
# dispatches the combined "pre" mode, these stay as engine self-checks)
if echo '{}' | "$PY" "$PLUGIN_ROOT/hooks/engine/main.py" quality --runtime=openhands >/tmp/meth-quality.$$.log 2>&1; then
    if grep -q -e '"permissionDecision"' -e '"decision"' /tmp/meth-quality.$$.log; then
        echo "[OK]   hook engine running (main.py quality → returned a decision)"
    else
        echo "[ERROR] hook engine quality returned no decision:"
        sed 's/^/       /' /tmp/meth-quality.$$.log
        PROBLEMS=$((PROBLEMS + 1))
    fi
else
    echo "[ERROR] hook engine quality test failed:"
    sed 's/^/       /' /tmp/meth-quality.$$.log
    PROBLEMS=$((PROBLEMS + 1))
fi
rm -f /tmp/meth-quality.$$.log
# deploy hook test (PreToolUse — terminalmatcher)
if echo '{}' | "$PY" "$PLUGIN_ROOT/hooks/engine/main.py" deploy --runtime=openhands >/tmp/meth-deploy.$$.log 2>&1; then
    if grep -q -e '"permissionDecision"' -e '"decision"' /tmp/meth-deploy.$$.log; then
        echo "[OK]   hook engine running (main.py deploy → returned a decision)"
    else
        echo "[ERROR] hook engine deploy returned no decision:"
        sed 's/^/       /' /tmp/meth-deploy.$$.log
        PROBLEMS=$((PROBLEMS + 1))
    fi
else
    echo "[ERROR] hook engine deploy test failed:"
    sed 's/^/       /' /tmp/meth-deploy.$$.log
    PROBLEMS=$((PROBLEMS + 1))
fi
rm -f /tmp/meth-deploy.$$.log

echo "== 1b) hooks.json dispatch locator drift (generated; roots = run-hook.sh) =="
# Every hook command embeds a plugin-root locator loop (runtime portability:
# neither runtime injects a guaranteed plugin-root env var, so hooks.json cannot
# reference run-hook.sh by a single fixed path). hooks.json commands are
# GENERATED from the canonical locator in scripts/sync-hooks-json.py — the single
# editable place for dispatch roots — and the roots stay in sync with run-hook.sh
# or hooks silently stop firing (guard fail-open). This section mechanically
# catches drift in both directions:
#   (1) all hook commands share ONE identical locator list,
#   (2) every locator candidate used in hooks.json is resolvable by run-hook.sh,
#   (3) hooks.json is byte-identical to the canonical locator (sync-hooks-json.py).
"$PY" - <<'PY'
import json, os, re, sys
from pathlib import Path
PLUGIN = Path(os.environ.get("PLUGIN_ROOT") or ".")

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

LOC_RE = re.compile(r"for d in (.*?); do")

def hook_commands(obj, out):
    if isinstance(obj, dict):
        if "command" in obj:
            out.append(obj["command"])
        for v in obj.values():
            hook_commands(v, out)
    elif isinstance(obj, list):
        for v in obj:
            hook_commands(v, out)

manifest = PLUGIN / "hooks" / "hooks.json"
runner = PLUGIN / "hooks" / "scripts" / "run-hook.sh"
if not manifest.is_file() or not runner.is_file():
    print("  MISS: hooks/hooks.json or hooks/scripts/run-hook.sh missing")
    sys.exit(1)

cmds = []
hook_commands(json.loads(manifest.read_text(encoding="utf-8")), cmds)
locs = []
for c in cmds:
    m = LOC_RE.search(c)
    locs.append(m.group(1).strip() if m else "")
locs = [l for l in locs if l]

problems = []
if len(locs) != len(cmds):
    problems.append("not every hook command embeds the locator loop")
elif len(set(locs)) != 1:
    problems.append("hook commands drifted apart (%d distinct locator lists)" % len(set(locs)))
else:
    toks = []
    for t in locs[0].split():
        # Tokens look like "ROOT" or "ROOT"* (shell glob). Normalize: drop
        # the shell quotes and a trailing glob star before comparing.
        toks.append(t.replace('"', "").rstrip("*"))
    text = runner.read_text(encoding="utf-8")
    for t in toks:
        # Path literals (incl. $HOME globs) must appear verbatim in
        # run-hook.sh; bare env/cwd tokens must at least be referenced there.
        if "/" in t or "." == t:
            if t not in text:
                problems.append("locator path %r absent from run-hook.sh (single source of truth)" % t)
        elif t.startswith("$") and t not in text:
            problems.append("locator env %s absent from run-hook.sh" % t)
    print("  hook commands: %d, distinct locators: %d" % (len(cmds), len(set(locs))))
    print("  locator candidates: %s" % " ".join(toks))
for p in problems:
    print("  MISS: %s" % p)
print("  problems: %d" % len(problems))
sys.exit(1 if problems else 0)
PY
if [ $? -eq 0 ]; then
    echo "[OK]   hooks.json dispatch locators in sync with run-hook.sh (single source)"
else
    echo "[ERROR] hooks.json dispatch locator drift (see above) — sync hooks.json copies with run-hook.sh"
    PROBLEMS=$((PROBLEMS + 1))
fi
# hooks.json commands are GENERATED from the canonical locator in
# scripts/sync-hooks-json.py (the single editable place for dispatch roots).
# Byte-exact sync is enforced here: a hand-edit of hooks.json or a locator
# change without a `--write` fails the self-check.
if "$PY" "$PLUGIN_ROOT/scripts/sync-hooks-json.py" --check "$PLUGIN_ROOT/hooks/hooks.json" >/tmp/meth-locator.$$.log 2>&1; then
    echo "[OK]   hooks.json generated dispatch commands in sync (sync-hooks-json.py)"
else
    sed 's/^/       /' /tmp/meth-locator.$$.log
    echo "[ERROR] hooks.json locator drifted from scripts/sync-hooks-json.py — run: python3 scripts/sync-hooks-json.py --write"
    PROBLEMS=$((PROBLEMS + 1))
fi
rm -f /tmp/meth-locator.$$.log

echo "== 2) Manifesto wired to all surfaces + bridge (native→record)? =="
"$PY" - <<'PY'
import glob, os, sys, tomllib

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

PLUGIN = os.environ.get("PLUGIN_ROOT") or "."
PROJECT = os.environ.get("PROJECT_ROOT") or "."

# Non-methodology tool surfaces — explicitly excluded from the audit (outside the capability map).
EXCLUDED = {"graft", "memory", "sync"}

# Development wing surfaces (development-methodology.md §6 mapping + dev agents):
DEV_WING = {
    "bmad-check-implementation-readiness", "bmad-prd", "bmad-ux",
    "bmad-create-architecture", "bmad-sprint-planning", "bmad-create-story",
    "bmad-create-epics-and-stories", "bmad-dev-story", "bmad-quick-dev",
    "bmad-dev-auto", "bmad-research-experiment",
    "bmad-testarch-atdd", "bmad-testarch-automate", "bmad-testarch-ci",
    "bmad-testarch-framework", "bmad-testarch-nfr", "bmad-testarch-test-design",
    "bmad-testarch-test-review", "bmad-testarch-trace",
    "bmad-code-review", "bmad-review-adversarial-general",
    "bmad-review-edge-case-hunter", "bmad-qa-generate-e2e-tests",
    "bmad-eval-runner", "gds-performance-test", "bmad-document-project",
    "bmad-retrospective", "bmad-correct-course", "bmad-sprint-status",
    "bmad-agent-dev", "bmad-agent-architect", "bmad-agent-pm",
    "bmad-agent-ux-designer", "bmad-agent-tech-writer", "bmad-tea",
}

missing = []
checked = 0
excluded = 0

skills = sorted(
    d for d in glob.glob(os.path.join(PLUGIN, "skills", "*"))
    if os.path.isdir(d) and os.path.isfile(os.path.join(d, "SKILL.md"))
)
for d in skills:
    name = os.path.basename(d)
    if name in EXCLUDED:
        excluded += 1
        continue
    checked += 1

    root = os.path.join(d, "customize.toml")
    team = os.path.join(PLUGIN, "custom", "%s.toml" % name)
    user = os.path.join(PLUGIN, "custom", "%s.user.toml" % name)

    if os.path.isfile(root):
        # Customization line: root + team + personal — effective facts (merge additive)
        facts = []
        ok = True
        for p in (root, team, user):
            if not os.path.isfile(p):
                continue
            try:
                data = tomllib.load(open(p, "rb"))
            except Exception as e:
                print("  PARSE ERROR: %s: %s" % (p, e))
                missing.append("%s (parse)" % name)
                ok = False
                break
            for sec in ("agent", "workflow"):
                facts += data.get(sec, {}).get("persistent_facts", [])
        if not ok:
            continue
        if not any("research-methodology.md" in x for x in facts):
            missing.append("%s (no manifesto: root+team+user)" % name)
        elif not any("project-context.md" in x for x in facts):
            missing.append("%s (no project-context)" % name)
        if name in DEV_WING and not any("development-methodology.md" in x for x in facts):
            missing.append("%s (no development manifesto)" % name)
        # Consumption: facts must not just be configured — the surface must actually load them.
        consumed = False
        for root2, _, files in os.walk(d):
            for f in files:
                if not f.endswith((".md", ".py", ".sh")):
                    continue
                try:
                    if "resolve_customization" in open(os.path.join(root2, f),
                                                       encoding="utf-8",
                                                       errors="replace").read():
                        consumed = True
                        break
                except OSError:
                    continue
            if consumed:
                break
        if not consumed:
            missing.append("%s (cosmetic: does not call resolve_customization)" % name)
    else:
        # No customization line → SKILL.md pointer
        try:
            txt = open(os.path.join(d, "SKILL.md"), encoding="utf-8").read()
        except Exception as e:
            missing.append("%s (unreadable: %s)" % (name, e))
            continue
        if "research-methodology.md" not in txt:
            missing.append("%s (no SKILL.md pointer)" % name)
        if name in DEV_WING and "development-methodology.md" not in txt:
            missing.append("%s (no SKILL.md development manifesto pointer)" % name)

# Menu skill targets must exist (broken capability link class).
for cp in sorted(set(glob.glob(os.path.join(PLUGIN, "skills", "*", "customize.toml")))
                 | set(glob.glob(os.path.join(PLUGIN, "custom", "*.toml")))):
    if os.path.basename(cp) == "config.toml":
        continue
    try:
        cd = tomllib.load(open(cp, "rb"))
    except Exception:
        continue
    for sec in ("agent", "workflow"):
        for it in cd.get(sec, {}).get("menu", []):
            s = it.get("skill")
            if s and not os.path.isdir(os.path.join(PLUGIN, "skills", s)):
                missing.append("%s menu → %s (skill directory missing)" % (os.path.basename(cp), s))

# Development wing manifesto: plugin-canonical — lives under the plugin root
# ({metodoloji-root}/docs/bmad/). No per-project copy is required.
DEVWING = os.path.join(PLUGIN, "docs", "bmad", "development-methodology.md")
if not os.path.isfile(DEVWING):
    missing.append("%s (document missing — plugin manifestos broken)" % DEVWING)

# --- Bridge audit: native skill output → methodology record translation ---
BRIDGE = os.path.join(PLUGIN, "docs", "bmad", "dev-skill-to-methodology-bridge.md")
if not os.path.isfile(BRIDGE):
    missing.append("%s (bridge document missing — native output not translated to methodology record)" % BRIDGE)

# Phase-1 bridge skills: each must produce docs/development/<record>-*.md.
# QR records may live under docs/quality/ (new) or docs/development/ (legacy).
BRIDGE_SKILLS = {
    "bmad-check-implementation-readiness": ("IR", "docs/development/", "create"),
    "bmad-sprint-planning": ("SP", "docs/development/", "create"),
    "bmad-create-story": ("S", "docs/development/stories/", "create"),
    "bmad-code-review": ("QR", "docs/quality/QR", "create"),
    "bmad-dev-story": ("S", "docs/development/stories/", "update"),
    "bmad-quick-dev": ("S", "docs/development/stories/", "update"),
    "bmad-dev-auto": ("S", "docs/development/stories/", "update"),
}
for skill, (rec_type, target, _) in BRIDGE_SKILLS.items():
    toml_path = os.path.join(PLUGIN, "custom", "%s.toml" % skill)
    if not os.path.isfile(toml_path):
        missing.append("%s (no bridge skill override)" % skill)
        continue
    try:
        txt = open(toml_path, encoding="utf-8").read()
    except OSError:
        missing.append("%s (unreadable)" % toml_path)
        continue
    if "dev-skill-to-methodology-bridge" not in txt:
        missing.append("%s (no bridge reference — append step may have been removed)" % skill)
    if target not in txt:
        missing.append("%s (methodology record target %s missing)" % (skill, target))

# Phase-3 QR feeders — ones working through the "## Methodology" section of SKILL.md.
QR_FEEDERS_SKILLMD = ["bmad-review-adversarial-general", "bmad-review-edge-case-hunter", "bmad-eval-runner"]
for skill in QR_FEEDERS_SKILLMD:
    skill_md = os.path.join(PLUGIN, "skills", skill, "SKILL.md")
    if not os.path.isfile(skill_md):
        missing.append("%s (no SKILL.md)" % skill)
        continue
    try:
        txt = open(skill_md, encoding="utf-8").read()
    except OSError:
        missing.append("%s (unreadable)" % skill_md)
        continue
    if "dev-skill-to-methodology-bridge" not in txt:
        missing.append("%s (no bridge reference in SKILL.md)" % skill)
    if "docs/quality/QR" not in txt and "docs/development/QR" not in txt:
        missing.append("%s (no QR feed target in SKILL.md)" % skill)

# Phase-3 QR feeders — ones working via custom/{skill}.toml activation_steps_append.
QR_FEEDERS_TOML = [
    "bmad-qa-generate-e2e-tests",
    "bmad-testarch-atdd", "bmad-testarch-automate", "bmad-testarch-ci",
    "bmad-testarch-framework", "bmad-testarch-nfr", "bmad-testarch-test-design",
    "bmad-testarch-test-review", "bmad-testarch-trace",
]
for skill in QR_FEEDERS_TOML:
    toml_path = os.path.join(PLUGIN, "custom", "%s.toml" % skill)
    if not os.path.isfile(toml_path):
        missing.append("%s (no bridge skill override)" % skill)
        continue
    try:
        txt = open(toml_path, encoding="utf-8").read()
    except OSError:
        missing.append("%s (unreadable)" % toml_path)
        continue
    if "dev-skill-to-methodology-bridge" not in txt:
        missing.append("%s (no bridge reference in append)" % skill)
    if "docs/quality/QR" not in txt and "docs/development/QR" not in txt:
        missing.append("%s (no QR feed target in append)" % skill)

print("  checked surfaces: %d, excluded (non-methodology tool): %d" % (checked, excluded))
for m in missing:
    print("  MISS: %s" % m)
print("  problems: %d" % len(missing))
raise SystemExit(1 if missing else 0)
PY
if [ $? -eq 0 ]; then
    echo "[OK]   manifesto wired to all methodology surfaces"
else
    echo "[WARNING] wiring incomplete (see above)"
    PROBLEMS=$((PROBLEMS + 1))
fi

echo "== 2b) Bridge instructions visible at runtime? (resolve_customization merge) =="
# The BRIDGE step in custom/{skill}.toml must merge with the skill-root customize.toml
# via resolve_customization.py deep_merge (append). Append semantics are persistent.
"$PY" - <<'PY'
import json, os, subprocess, sys
from pathlib import Path
PLUGIN = Path(os.environ.get("PLUGIN_ROOT") or ".")
RESOLVER = PLUGIN / "hooks" / "engine" / "resolve_customization.py"
TOML_SKILLS = [
    "bmad-check-implementation-readiness", "bmad-sprint-planning",
    "bmad-create-story", "bmad-code-review",
    "bmad-dev-story", "bmad-quick-dev", "bmad-dev-auto",
    "bmad-qa-generate-e2e-tests",
    "bmad-testarch-atdd", "bmad-testarch-automate", "bmad-testarch-ci",
    "bmad-testarch-framework", "bmad-testarch-nfr", "bmad-testarch-test-design",
    "bmad-testarch-test-review", "bmad-testarch-trace",
    "gds-dev-story", "gds-quick-dev", "gds-code-review",
    "gds-check-implementation-readiness", "gds-sprint-planning", "gds-create-story",
    "gds-test-automate", "gds-test-design", "gds-test-framework",
    "gds-test-review", "gds-e2e-scaffold", "gds-performance-test", "gds-playtest-plan",
    "wds-5-agentic-development",
]
# Agent-BRIDGE surfaces: BRIDGE lives in [agent].principles, not workflow steps.
AGENT_TOML_SKILLS = [
    "bmad-agent-dev", "gds-agent-game-dev", "gds-agent-game-solo-dev",
]
missing = []
checked = 0
for name in TOML_SKILLS + AGENT_TOML_SKILLS:
    skill_dir = PLUGIN / "skills" / name
    if not skill_dir.is_dir():
        missing.append("%s (skill directory missing — BRIDGE override can never load)" % name)
        continue
    # resolve_customization hard-requires the skill-root customize.toml ("must
    # contain customize.toml"); a team override with no root file silently
    # dead-ends. Flag it instead of skipping (was silently passing before).
    if not (skill_dir / "customize.toml").is_file():
        missing.append("%s (no root customize.toml — team BRIDGE override never merges)" % name)
        continue
    checked += 1
    key = "agent.principles" if name in AGENT_TOML_SKILLS else "workflow.activation_steps_append"
    try:
        r = subprocess.run(
            [sys.executable, str(RESOLVER), "-s", str(skill_dir), "-k", key],
            capture_output=True, text=True, encoding="utf-8", timeout=15)
        d = json.loads(r.stdout)
        asa = d.get(key, [])
        if not any("BRIDGE" in s or "KOPRU" in s for s in asa):
            missing.append("%s (%s BRIDGE absent after merge)" % (name, key))
    except Exception as e:
        missing.append("%s (resolve error: %s)" % (name, str(e)[:60]))
print("  checked toml skills (installed): %d" % checked)
for m in missing:
    print("  MISS: %s" % m)
print("  problems: %d" % len(missing))
raise SystemExit(1 if missing else 0)
PY
if [ $? -eq 0 ]; then
    echo "[OK]   BRIDGE instructions visible at runtime (deep_merge append persistent)"
else
    echo "[WARNING] BRIDGE merge problem (see above)"
    PROBLEMS=$((PROBLEMS + 1))
fi

echo "== 2c) BRIDGE verify instructions present? =="
"$PY" - <<'PY'
import json, os, subprocess, sys
from pathlib import Path
PLUGIN = Path(os.environ.get("PLUGIN_ROOT") or ".")
RESOLVER = PLUGIN / "hooks" / "engine" / "resolve_customization.py"
# Every producer BRIDGE surface carries a VERIFY step. The BRIDGE text lives in
# workflow.activation_steps_append for the workflow producers and in
# [agent].principles for the agent-principles producers (the same key §2b uses).
# This map must cover EVERY producer named by GUIDE.md "Producer (N,
# creates/updates records)" — the bench check "producer VERIFY audit covers
# every producer surface (E-018)" pins that equality, so a surface cannot drop
# out of the audit. Before E-018 §2c read only workflow.activation_steps_append
# for 13 of the 17 producers: the three agent-principles producers were never
# read (probe: stripping VERIFY from bmad-agent-dev left §2c green) and
# wds-5-agentic-development was not listed at all.
PRODUCER_KEY = {
    "bmad-check-implementation-readiness": "workflow.activation_steps_append",
    "bmad-sprint-planning": "workflow.activation_steps_append",
    "bmad-create-story": "workflow.activation_steps_append",
    "bmad-code-review": "workflow.activation_steps_append",
    "bmad-dev-story": "workflow.activation_steps_append",
    "bmad-quick-dev": "workflow.activation_steps_append",
    "bmad-dev-auto": "workflow.activation_steps_append",
    "bmad-agent-dev": "agent.principles",
    "gds-check-implementation-readiness": "workflow.activation_steps_append",
    "gds-sprint-planning": "workflow.activation_steps_append",
    "gds-create-story": "workflow.activation_steps_append",
    "gds-code-review": "workflow.activation_steps_append",
    "gds-dev-story": "workflow.activation_steps_append",
    "gds-quick-dev": "workflow.activation_steps_append",
    "gds-agent-game-dev": "agent.principles",
    "gds-agent-game-solo-dev": "agent.principles",
    "wds-5-agentic-development": "workflow.activation_steps_append",
}
missing = []
checked = 0
for name, key in PRODUCER_KEY.items():
    skill_dir = PLUGIN / "skills" / name
    if not skill_dir.is_dir():
        missing.append("%s (skill directory missing — VERIFY cannot load)" % name)
        continue
    checked += 1
    try:
        r = subprocess.run(
            [sys.executable, str(RESOLVER), "-s", str(skill_dir), "-k", key],
            capture_output=True, text=True, encoding="utf-8", timeout=15)
        d = json.loads(r.stdout)
        steps = d.get(key, [])
        # Accept legacy Turkish marker and both English markers.
        has_verify = any(m in s for s in steps for m in ("DOGRULAMA", "VERIFICATION", "VERIFY"))
        if not has_verify:
            missing.append("%s (no verify marker in BRIDGE[%s] — LLM may skip the record)" % (name, key))
    except Exception as e:
        missing.append("%s (resolve error: %s)" % (name, str(e)[:60]))
print("  checked BRIDGE skills: %d" % checked)
for m in missing:
    print("  MISS: %s" % m)
print("  problems: %d" % len(missing))
raise SystemExit(1 if missing else 0)
PY
if [ $? -eq 0 ]; then
    echo "[OK]   BRIDGE verify instructions present (LLM will auto-verify)"
else
    echo "[WARNING] BRIDGE verify missing (see above)"
    PROBLEMS=$((PROBLEMS + 1))
fi

echo "== 3) Approved experiment inventory (did guard open code writing?) =="
FOUND=0
for rec in "$PROJECT_ROOT"/docs/experiments/*.md; do
    [ -f "$rec" ] || continue
    case "$(basename "$rec")" in _template.md) continue ;; esac
    # Detect the approved decision in both the new English format
    # (**Decision:** APPROVED / Status: APPROVED) and the legacy Turkish one.
    if grep -qE '\*\*(Decision|Karar):\*\*[[:space:]]*(APPROVED|ONAYLANDI)|^Status:[[:space:]]*(APPROVED|ONAYLANDI)' "$rec"; then
        "$PY" "$GATE" --verify --record "$rec" >/dev/null 2>&1
        vrc=$?
        if [ "$vrc" -eq 0 ]; then
            echo "[OK]   $rec -> VERIFIED"
            FOUND=$((FOUND + 1))
        elif [ "$vrc" -eq 2 ]; then
            # ADVISORY-BLOCK: token genuine but sample too small — genuine, not forged.
            # Does not open code writing, so not counted in FOUND (guard stayed closed),
            # but must NOT be reported as FORGED.
            echo "[OK]   $rec -> VERIFIED (ADVISORY-BLOCK: genuine token, small sample — code stayed closed)"
        elif grep -q 'Re-Measured-By:' "$rec"; then
            # CROSS-MACHINE provenance, not tampering: the record was approved under a
            # key outside this machine's trust ring (the HMAC keys live outside the
            # repo, so --verify cannot re-derive the token here). The operator has
            # declared the re-measurement record via the plain 'Re-Measured-By:'
            # marker; that record must verify under this machine's trust ring
            # (own key + imported peer keys — run_experiment.py --import-key).
            remeasured=$(grep 'Re-Measured-By:' "$rec" | head -1 | sed 's/.*Re-Measured-By:\*\{0,2\}[[:space:]]*\(E-[0-9][0-9]*\).*/\1/')
            rmrec="$PROJECT_ROOT/docs/experiments/$remeasured.md"
            rmvrc=3
            [ -n "$remeasured" ] && [ -f "$rmrec" ] && \
                { "$PY" "$GATE" --verify --record "$rmrec" >/dev/null 2>&1; rmvrc=$?; }
            # exit 0 = VERIFIED, exit 2 = ADVISORY-BLOCK (token genuine, small sample —
            # provenance needs genuineness, not code unlock, so both count).
            if [ "$rmvrc" -eq 0 ] || [ "$rmvrc" -eq 2 ]; then
                echo "[WARN] $rec -> CROSS-MACHINE: approved under a key outside this machine's trust ring (keys live outside the repo, tokens are not re-derivable here by design)."
                echo "       Re-measured by $remeasured -> VERIFIED under this machine's trust ring. Gate fields untouched. (Key-free alternative: import the approving machine's key, run_experiment.py --import-key.)"
            else
                echo "[ERROR] $rec -> CROSS-MACHINE but re-measurement missing/unverified: $remeasured."
                echo "        Two cures: import the approving machine's key (run_experiment.py --import-key;"
                echo "        key material via BMAD_PEER_GATE_KEY) — every record it signed verifies here —"
                echo "        or create docs/experiments/$remeasured.md and run the gate: run_experiment.py --record ... --run ..."
                PROBLEMS=$((PROBLEMS + 1))
            fi
        else
            echo "[ERROR] $rec -> approved but --verify failed (FORGED?)"
            echo "        If approved on another machine of yours: import that key (run_experiment.py --import-key;"
            echo "        key material via BMAD_PEER_GATE_KEY) or re-measure with a Re-Measured-By marker."
            PROBLEMS=$((PROBLEMS + 1))
        fi
    fi
done
if [ "$FOUND" -eq 0 ]; then
    echo "[WARNING] no approved experiment — guard keeps code writing closed. Start with bmad-research-experiment."
    echo "        scratch/ can be used for exploration/experimentation code."
fi

echo "== 3b) Code-scope coverage (did every edited file sit inside an approved scope?) =="
# Paths ride ARGV (bash converts MSYS /c/... to Windows paths for native
# python — heredoc TEXT gets no such conversion, so in-script $PROJECT_ROOT
# would resolve to a nonexistent /c/... directory).
"$PY" - "$PROJECT_ROOT" "$GATE" <<'PYEOF'
import os
import subprocess
import sys
from pathlib import Path

PLUGIN = Path(sys.argv[1])
GATE = Path(sys.argv[2])
sys.path.insert(0, str(GATE.parent))
import run_experiment as gate  # noqa: E402  (the gate's own matcher — single source)

# Free-surface doctrine: the guard's own is_free — single source, so the
# audit and the hook can never disagree again (the audit used to call
# hooks//scripts//skills//custom/ free in EVERY project; the guard only
# frees them in the methodology repo itself). Pin the project-root env to
# this script's PROJECT_ROOT contract so dogfood detection resolves right
# even when invoked from elsewhere.
ENGINE = Path(os.environ.get("PLUGIN_ROOT", "")) / "hooks" / "engine"
try:
    sys.path.insert(0, str(ENGINE))
    from modules.utils import is_free as _engine_is_free  # noqa: E402
    os.environ["OPENHANDS_PROJECT_DIR"] = str(PLUGIN)
    os.environ["CLAUDE_PROJECT_DIR"] = str(PLUGIN)

    def _is_free(norm: str) -> bool:
        return bool(_engine_is_free(norm))

    _FREE_SRC = "engine is_free"
except Exception:
    _FREE_SRC = "built-in fallback (engine import failed)"

# Dogfood exemption: the plugin's own repository is the self-modification zone,
# not a consumer project. Its packaging/root files (manifests, README, license,
# config catalogs) ARE the plugin, so demanding an approved experiment scope for
# them would make the plugin unable to maintain its own manifests. A consumer
# project (project root != methodology root) still gets the full check.
try:
    from modules.utils import _project_is_methodology_root as _dogfood_root  # noqa: E402
    if _dogfood_root():
        print("  dogfood repo (project root == methodology root) — scope coverage gate not applicable")
        raise SystemExit(0)
except SystemExit:
    raise
except Exception:
    pass

    def _is_free(norm: str) -> bool:  # noqa: E302
        # Parity: scratch/tmp/temp + the other guard FREE_PREFIXES, root-level
        # explore_* files only (nested explore_* paths stay gated), docs/*.md.
        if norm.startswith("docs/") and norm.endswith(".md"):
            return True
        if norm.startswith("explore_") and "/" not in norm:
            return True
        return norm.startswith(("scratch/", "tmp/", "temp/", "graft/",
                                "_bmad/", ".metodoloji/", "openhands/",
                                ".git/"))

problems = []
checked = 0


def _git_names(args: list) -> list:
    out = subprocess.run(["git", "-C", str(PLUGIN)] + args,
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace")
    return [l.strip() for l in out.stdout.splitlines() if l.strip()]


# The decision being audited: everything touched since methodology adoption
# (the commit that added .metodoloji/initialized) PLUS uncommitted and
# untracked files. Auditing only the uncommitted delta let `git commit`
# launder uncovered work out of the audit — the next run was green with no
# record created. Pre-adoption files a brownfield project never touched
# stay out of scope (grandfathered).
_base = _git_names(["log", "--diff-filter=A", "--format=%H",
                    "--", ".metodoloji/initialized"])
adopted = _base[-1] if _base else ""
since_adoption = _git_names(["diff", "--name-only", f"{adopted}..HEAD"]) if adopted else []
worktree = _git_names(["diff", "--name-only", "HEAD"])
untracked = _git_names(["ls-files", "--others", "--exclude-standard"])
edited = list(dict.fromkeys(since_adoption + worktree + untracked))
_baseline = (f"{adopted[:12]} (methodology adoption commit)" if adopted
             else "work-tree only (.metodoloji/initialized not committed yet)")

# Approved scopes: every APPROVED record's Code Scope, parsed by the gate.
# Exit 2 = ADVISORY-BLOCK (genuine token, small sample) still counts as
# approved provenance; FORGED records are provenance-blind and are skipped.
scopes = []
for rec in sorted((PLUGIN / "docs" / "experiments").glob("E-*.md")):
    if rec.name == "_template.md":
        continue
    text = rec.read_text(encoding="utf-8", errors="replace")
    fields = gate.record_fields(text)
    decision = fields.get("Decision", "")
    if "APPROVED" not in decision or fields.get("Gate Evidence", "").strip() == "":
        continue
    rc = subprocess.run([sys.executable, str(GATE), "--verify", "--record", str(rec)],
                        capture_output=True).returncode
    if rc not in (0, 2):
        continue  # Re-Measured-By/CROSS-MACHINE spans are covered by their re-measurement record
    scope_value = fields.get("Code Scope", "")
    if scope_value and scope_value.strip().lower() != "none":
        scopes.append((rec.stem, scope_value))

for f in edited:
    checked += 1
    norm = f.replace("\\", "/")
    covered = any(gate.scope_matches(sv, norm) for _rid, sv in scopes)
    if covered:
        continue
    if _is_free(norm):
        continue
    problems.append(norm)

print(f"  baseline: {_baseline}")
print(f"  free-surface doctrine: {_FREE_SRC}")
print(f"  edited files checked: {checked}")
print(f"  approved scopes in force: {len(scopes)}")
for p in problems[:20]:
    print(f"  MISS: {p} — outside every approved Code Scope; open an experiment "
          "(E record with this file in its scope) or move it to a free surface")
if len(problems) > 20:
    print(f"  ... and {len(problems) - 20} more (see full list via git)")
print(f"  problems: {len(problems)}")
raise SystemExit(1 if problems else 0)
PYEOF
if [ $? -eq 0 ]; then
    echo "[OK]   scope coverage: every edited file sits inside an approved Code Scope (or a free surface)"
else
    echo "[WARNING] scope coverage: edited files outside every approved Code Scope (see MISS lines)"
    PROBLEMS=$((PROBLEMS + 1))
fi

echo "== 4) Documentary (B/C/D) record completeness =="
DOCPROBLEMS=0
DOCCHECKED=0
for rec in "$PROJECT_ROOT"/docs/research/*.md "$PROJECT_ROOT"/docs/design/*.md; do
    [ -f "$rec" ] || continue
    case "$(basename "$rec")" in _template.md) continue ;; esac
    DOCCHECKED=$((DOCCHECKED + 1))
    "$PY" "$GATE" --validate "$rec" >/tmp/meth-doc.$$.log 2>&1
    rc=$?
    if [ "$rc" -eq 0 ]; then
        echo "[OK]   $rec"
    elif [ "$rc" -eq 2 ]; then
        echo "[IGNORED] $rec (Mod A / unrecognized — not documentary)"
    else
        echo "[WARNING] $rec missing/integrity issue:"
        sed 's/^/        /' /tmp/meth-doc.$$.log
        DOCPROBLEMS=$((DOCPROBLEMS + 1))
    fi
done
rm -f /tmp/meth-doc.$$.log
if [ "$DOCCHECKED" -eq 0 ]; then
    echo "[OK]   no documentary records (docs/research/, docs/design/) — new records will be checked"
fi
PROBLEMS=$((PROBLEMS + DOCPROBLEMS))

echo "== 5) Engine integrity audit (modular engine: main.py + modules/) =="
# The canonical is this repo's hooks/engine/ tree; the plugin copy must match the repo.
# Drift = missing/broken engine file (the old single-file bmad-hooks.py is gone).
ENGINE_OK=1
for f in main.py resolve_customization.py modules/__init__.py \
         modules/config.py modules/utils.py modules/archive.py modules/bash_targets.py \
         modules/guard.py modules/audit.py modules/stop.py; do
    if [ ! -f "$PLUGIN_ROOT/hooks/engine/$f" ]; then
        echo "[ERROR] engine file missing: hooks/engine/$f"
        ENGINE_OK=0
    fi
done
if [ "$ENGINE_OK" -eq 1 ]; then
    if "$PY" -c "
import py_compile, os, sys
engine=os.path.normpath(sys.argv[1])
files=['main.py','resolve_customization.py',
       'modules/__init__.py','modules/config.py','modules/utils.py',
       'modules/archive.py','modules/bash_targets.py','modules/guard.py',
       'modules/audit.py','modules/stop.py']
for f in files:
    py_compile.compile(os.path.join(engine, f), doraise=True)
print('import-ok')
" "$PLUGIN_ROOT/hooks/engine" >/tmp/meth-engine.$$.log 2>&1; then
        echo "[OK]   modular engine complete and importable (main.py + modules/)"
    else
        echo "[ERROR] modular engine import test failed:"
        sed 's/^/       /' /tmp/meth-engine.$$.log
        PROBLEMS=$((PROBLEMS + 1))
    fi
    rm -f /tmp/meth-engine.$$.log
else
    PROBLEMS=$((PROBLEMS + 1))
fi

echo "== 5b) Hard gate enforcement mode (soft/hard) =="
# quality/deploy/code/stop gates are config-gated: custom/config.toml [hooks].
# quality+deploy default soft; code+stop default hard (fail-closed).
"$PY" - <<'PY'
import tomllib, os, sys
PLUGIN = os.environ.get("PLUGIN_ROOT") or "."
PROJECT = os.environ.get("PROJECT_ROOT") or "."
cfg = os.path.join(PLUGIN, "custom", "config.toml")
qg, dg, cg, sg = "soft", "soft", "hard", "hard"
src = "(no config — defaults: quality/deploy soft, code/stop hard)"
if os.path.isfile(cfg):
    try:
        d = tomllib.load(open(cfg, "rb"))
        h = d.get("hooks", {}) or {}
        qg = str(h.get("quality_gate", "soft")).strip().lower()
        dg = str(h.get("deploy_guard", "soft")).strip().lower()
        cg = str(h.get("code_guard", "hard")).strip().lower()
        sg = str(h.get("stop_guard", "hard")).strip().lower()
        src = "config"
    except Exception as e:
        print("  [ERROR] config parse: %s" % e)
        sys.exit(1)
problems = []
for name, val in (("quality_gate", qg), ("deploy_guard", dg),
                  ("code_guard", cg), ("stop_guard", sg)):
    if val not in ("soft", "hard"):
        problems.append("%s = %r (invalid — must be soft|hard)" % (name, val))
print("  quality_gate: %s (%s)" % (qg, src))
print("  deploy_guard: %s (%s)" % (dg, src))
print("  code_guard: %s (%s)" % (cg, src))
print("  stop_guard: %s (%s)" % (sg, src))
# Dogfood exemption: this repository IS the plugin. The plugin's global gate
# policy (custom/config.toml) is deliberately hardened, and the plugin itself
# ships no IR/SP/QR/PR records (the record set was pruned 2026-10-06) —
# escalating here would demand the plugin keep its own experiment records just
# to pass its own audit. Report the mode and pass; the consumer-project case
# (project root != plugin root) still escalates below.
if os.path.realpath(PROJECT) == os.path.realpath(PLUGIN):
    print("  dogfood repo (project root == plugin root) — consumer-record escalation not applicable")
elif qg == "hard" or dg == "hard":
    import glob as _glob
    _dev = os.path.join(PROJECT, "docs", "development")
    _real = [f for pat in ("IR-*.md", "SP-*.md", "QR-*.md", "PR-*.md")
             for f in _glob.glob(os.path.join(_dev, pat))
             if not os.path.basename(f).startswith("_")]
    if not _real:
        problems.append("hard mode active but no real record in docs/development/ "
                        "(IR-/SP-/QR-/PR-) — every commit/push/deploy is blocked; "
                        "switch to soft or produce a record first")
print("  note: guard/quality/deploy/stop hooks are ACTIVE (hooks.json:")
print("       PreToolUse -> guard/quality/deploy, Stop -> stop, PostToolUse -> audit).")
print("       quality_gate/deploy_guard/code_guard enforced at hook level;")
print("       quality: DENY git commit without IR/QR/SP (incl. story metadata/chain);")
print("       deploy: DENY deploy without IR/QR/SP/PR;")
print("       guard: DENY unapproved writes (code_guard=soft → warn);")
print("       stop: report-only (never blocks; stop_guard kept for backward compat).")
if problems:
    for p in problems:
        print("  MISS: %s" % p)
    sys.exit(1)
sys.exit(0)
PY
if [ $? -eq 0 ]; then
    echo "[OK]   gate mode valid (quality/deploy/code per config; stop report-only, never blocks)"
else
    echo "[WARNING] hard gate mode issue (see above)"
    PROBLEMS=$((PROBLEMS + 1))
fi

echo "== 5c) custom/ bridge TOMLs static quality audit =="
# check-custom.sh §0-§6: TOML parse, persistent_facts, depth, hard-gate
# keywords, DOGRULAMA pattern, gds Mod A bridge reference, config
# soft/hard contract. Independent script; same style, same [OK]/[WARNING]/[ERROR]
# output. Root output kept internal; only exit code added to PROBLEMS.
if [ -f "$SELF/check-custom.sh" ]; then
    if sh "$SELF/check-custom.sh" >/tmp/meth-custom.$$.log 2>&1; then
        echo "[OK]   custom/ static quality audit passed"
    else
        echo "[WARNING] custom/ static quality violation:"
        sed 's/^/       /' /tmp/meth-custom.$$.log
        PROBLEMS=$((PROBLEMS + 1))
    fi
    rm -f /tmp/meth-custom.$$.log
else
    echo "[WARNING] scripts/check-custom.sh not found (skipped)"
fi

echo "== 6) Development records format check =="
DEVPROBLEMS=0
DEVCHECKED=0
for rec in \
    "$PROJECT_ROOT"/docs/development/IR-*.md "$PROJECT_ROOT"/docs/development/SP-*.md \
    "$PROJECT_ROOT"/docs/development/QR-*.md "$PROJECT_ROOT"/docs/development/PR-*.md \
    "$PROJECT_ROOT"/docs/development/stories/S-*.md "$PROJECT_ROOT"/docs/development/incidents/PM-*.md \
    "$PROJECT_ROOT"/docs/quality/QR-*.md; do
    [ -f "$rec" ] || continue
    DEVCHECKED=$((DEVCHECKED + 1))
    case "$(basename "$rec")" in
        # `preparing` is the template's own draft state (templates/_template_IR.md)
        # — accepting only final verdicts flagged every in-progress IR record.
        IR-*) ALLOWED="preparing READY INCOMPLETE HAZIR EKSİK" ;;
        QR-*) ALLOWED="in-review APPROVED REJECTED REVISED ONAYLANDI REDDEDİLDİ REVİZE" ;;
        # `preparing` is the template's own state (templates/_template_PR.md).
        PR-*) ALLOWED="preparing READY WAITING HAZIR BEKLİYOR" ;;
        SP-*) ALLOWED="planned in-progress completed cancelled planlandı devam ediyor tamamlandı iptal" ;;
        # 'ready-for-dev' is the engine's own vocabulary
        # (guard._validate_story_metadata) and what the sprint-status template
        # documents; a checker narrower than the engine flags valid records.
        S-*)  ALLOWED="backlog ready-for-dev sprint in-progress review done blocked" ;;
        PM-*) ALLOWED="open investigation resolved closed açık" ;;
        *)    continue ;;
    esac
    # Accept both English and legacy Turkish field labels, in BOTH canonical
    # forms: the bold bullet the IR/SP/PR templates use (`- **Status:** planned`)
    # and the field table bridge §2.3 specifies for the S record, which
    # create-methodology-record.py writes (`| Status | ready-for-dev |`).
    # Accepting only the bullet form made every generated S record warn
    # "no Decision/Status field" — the checker disagreeing with the generator
    # the bridge specifies.
    line=$(grep -m1 '\*\*Decision:\*\*' "$rec" || grep -m1 '\*\*Status:\*\*' "$rec" \
        || grep -m1 '\*\*Karar:\*\*' "$rec" || grep -m1 '\*\*Durum:\*\*' "$rec" \
        || grep -m1 '^| *Decision *|' "$rec" || grep -m1 '^| *Status *|' "$rec" \
        || grep -m1 '^| *Karar *|' "$rec" || grep -m1 '^| *Durum *|' "$rec")
    if [ -z "$line" ]; then
        echo "[WARNING] $rec -> no Decision/Status field"
        DEVPROBLEMS=$((DEVPROBLEMS + 1))
        continue
    fi
    # T-01: strip CR so CRLF-ended records (Windows) compare cleanly.
    dec=$(echo "$line" | tr -d '\r' | sed -E 's/.*\*\*(Decision|Status|Karar|Durum):\*\* *//; s/^\| *[^|]*\| *//; s/[|→—].*//' | sed 's/^ *//; s/ *$//')
    found=0
    case "$(basename "$rec")" in
        IR-*) case "$dec" in preparing|READY|INCOMPLETE|HAZIR|EKSİK) found=1 ;; esac ;;
        QR-*) case "$dec" in in-review|APPROVED|REJECTED|REVISED|ONAYLANDI|REDDEDİLDİ|REVİZE) found=1 ;; esac ;;
        PR-*) case "$dec" in preparing|READY|WAITING|HAZIR|BEKLİYOR) found=1 ;; esac ;;
        SP-*) case "$dec" in planned|"in-progress"|completed|cancelled|planlandı|"devam ediyor"|tamamlandı|iptal) found=1 ;; esac ;;
        S-*)  case "$dec" in backlog|ready-for-dev|sprint|in-progress|review|done|blocked) found=1 ;; esac ;;
        PM-*) case "$dec" in open|investigation|resolved|closed|açık) found=1 ;; esac ;;
    esac
    if [ "$found" -eq 0 ]; then
        echo "[WARNING] $rec -> unexpected Decision/Status: '$dec' (allowed: $ALLOWED)"
        DEVPROBLEMS=$((DEVPROBLEMS + 1))
        continue
    fi
    if ! grep -q '\*\*Date:\*\*' "$rec" && ! grep -q '\*\*Tarih:\*\*' "$rec" \
        && ! grep -q '^| *Date *|' "$rec" && ! grep -q '^| *Tarih *|' "$rec"; then
        echo "[WARNING] $rec -> no Date field"
        DEVPROBLEMS=$((DEVPROBLEMS + 1))
        continue
    fi
    echo "[OK]   $rec ($dec)"
done
if [ "$DEVCHECKED" -eq 0 ]; then
    echo "[OK]   no development records (docs/development/) — new records will be checked"
fi
PROBLEMS=$((PROBLEMS + DEVPROBLEMS))

echo "== 6a) .env inventory: any hard-coded API key leakage? =="
ENVPROBLEMS=0
# 6a.1) Is .env committable? (LLM credentials must never be committed)
# ERROR only when the file is git-tracked OR present-but-not-ignored.
# An ignored, untracked local .env is a normal dev setup — OK, not an ERROR
# (SP-006: the old "any .env present → ERROR" rule cried wolf on every
# dev machine and hid the real leak signal).
if [ -f "$PROJECT_ROOT/.env" ]; then
    if git -C "$PROJECT_ROOT" ls-files --error-unmatch .env >/dev/null 2>&1; then
        echo "[ERROR]  .env is git-tracked — credentials must never be committed (remove + rotate)"
        ENVPROBLEMS=$((ENVPROBLEMS + 1))
    elif tr -d '\r' < "$PROJECT_ROOT/.gitignore" 2>/dev/null | grep -qx '.env'; then
        echo "[OK]    .env present but ignored + untracked (local dev setup)"
    else
        echo "[ERROR]  .env present and NOT ignored — leak risk (add .env to .gitignore, never commit)"
        ENVPROBLEMS=$((ENVPROBLEMS + 1))
    fi
else
    echo "[OK]    .env not present (protected via .gitignore)"
fi
# 6a.2) Is .env in .gitignore? (CR-tolerant: Windows checkouts carry CRLF,
# and grep -x would miss '.env\r' — strip carriage returns first.)
if tr -d '\r' < "$PROJECT_ROOT/.gitignore" 2>/dev/null | grep -qx '.env'; then
    echo "[OK]    .gitignore → .env present"
else
    echo "[ERROR]  .env line missing in .gitignore — local key leak risk"
    ENVPROBLEMS=$((ENVPROBLEMS + 1))
fi
PROBLEMS=$((PROBLEMS + ENVPROBLEMS))

echo "== 6b) Tech-debt inventory integrity (drift/ID/P0/orphan) =="
# Presence, not the executable bit: the sibling self-check scripts ship in git
# as mode 100644 (non-executable) and are invoked through `sh`, so a `-x` test
# is always false on a clean checkout — §6b skipped itself and forced exit 1 on
# an otherwise healthy tree (E-001).
if [ -f "$SELF/check-techdebt.sh" ]; then
    sh "$SELF/check-techdebt.sh"
    TDEXIT=$?
    if [ "$TDEXIT" -ne 0 ]; then
        PROBLEMS=$((PROBLEMS + 1))
    fi
else
    echo "[WARNING] $SELF/check-techdebt.sh not found — §6b skipped"
    PROBLEMS=$((PROBLEMS + 1))
fi

echo "== 6c) Template copy identity (templates/ ↔ docs/_template copies) =="
# /metodoloji:init copies templates/_template_*.md into the project docs. The
# plugin repo itself dogfoods those docs copies (docs/development/…,
# docs/experiments/…), so they must stay byte-identical to the canonical
# templates/ files — updating a template in only one place is drift. tech-debt.md
# is excluded here because check-techdebt §1 (run in §6b) already enforces it.
# Pairs whose destination dir does not exist yet (docs/research/, docs/design/)
# are skipped: those record families are not used inside the plugin itself.
"$PY" - <<'PY'
import os, sys
from pathlib import Path
PLUGIN = Path(os.environ.get("PLUGIN_ROOT") or ".")

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

# label, canonical source, dogfood destination (relative to the PLUGIN root —
# this section checks the plugin's own dogfood copies, NOT the target
# project's skeleton; /metodoloji:init writes to {project-root} and can
# never fix a MISS here).
PAIRS = [
    ("experiments E", "templates/_template_E.md", "docs/experiments/_template.md"),
    ("development IR", "templates/_template_IR.md", "docs/development/_template_IR.md"),
    ("development SP", "templates/_template_SP.md", "docs/development/_template_SP.md"),
    ("development QR", "templates/_template_QR.md", "docs/development/_template_QR.md"),
    ("development PR", "templates/_template_PR.md", "docs/development/_template_PR.md"),
    ("development S", "templates/_template_S.md", "docs/development/stories/_template_S.md"),
    ("development README", "templates/README.md", "docs/development/README.md"),
]

problems = []
checked = 0
skipped = 0
for label, src, dst in PAIRS:
    s = PLUGIN / src
    d = PLUGIN / dst
    if not s.is_file():
        problems.append("%s: source missing %s" % (label, src))
        continue
    if not d.is_file():
        if d.parent.is_dir():
            problems.append("%s: copy missing %s (plugin dogfood copy — sync templates/ into the plugin docs copy; /metodoloji:init only writes the TARGET project and cannot fix this)" % (label, dst))
        else:
            skipped += 1
            print("  SKIP %s: no %s/ in plugin (record family not used yet)"
                  % (label, d.parent))
        continue
    checked += 1
    if s.read_bytes() != d.read_bytes():
        problems.append("%s: DRIFT %s <> %s (update templates/ AND the docs copy together)"
                        % (label, src, dst))
print("  template pairs checked: %d, skipped (dir absent): %d" % (checked, skipped))
print("  coverage: tech-debt.md excluded here — enforced by check-techdebt.sh §1; "
      "absent-dest-dir pairs skipped (record family unused in plugin)")
for p in problems:
    print("  MISS: %s" % p)
print("  problems: %d" % len(problems))
sys.exit(1 if problems else 0)
PY
if [ $? -eq 0 ]; then
    echo "[OK]   docs template copies identical to templates/ (no drift)"
else
    echo "[ERROR] template copy drift (see above) — templates/ ↔ docs copies out of sync"
    PROBLEMS=$((PROBLEMS + 1))
fi

echo "== 6d) Init marker integrity (skeleton installed ⇔ .metodoloji/initialized) =="
# Init is one-time: /metodoloji:init writes .metodoloji/initialized, and the
# SessionStart hook uses it to stop advertising init. A skeleton with no marker
# means every skill keeps re-hinting/replaying init (the "each step re-inits"
# regression); a marker with no skeleton means the marker is stale and init
# would short-circuit over a missing skeleton. Both are drift.
INIT_MARKER="$PROJECT_ROOT/.metodoloji/initialized"
SKELETON_PRESENT=0
for f in docs/experiments/_template.md docs/development/_template_IR.md docs/development/stories/_template_S.md; do
    if [ -f "$PROJECT_ROOT/$f" ]; then SKELETON_PRESENT=1; break; fi
done
if [ "$SKELETON_PRESENT" -eq 1 ] && [ ! -f "$INIT_MARKER" ]; then
    echo "[ERROR] record skeleton installed but .metodoloji/initialized is missing"
    echo "        → run /metodoloji:init once so the marker records the one-time init"
    PROBLEMS=$((PROBLEMS + 1))
elif [ "$SKELETON_PRESENT" -eq 0 ] && [ -f "$INIT_MARKER" ]; then
    echo "[ERROR] .metodoloji/initialized present but no skeleton copy found — stale marker"
    echo "        → re-run /metodoloji:init --force, or delete the marker"
    PROBLEMS=$((PROBLEMS + 1))
else
    if [ -f "$INIT_MARKER" ]; then
        echo "[OK]   init marker consistent with skeleton (marker present)"
    else
        echo "[OK]   init not required yet (no skeleton, no marker)"
    fi
fi

echo "== 6d2) Command→script reference integrity (commands/*.md ↔ bmad/scripts/*.py) =="
# A real session hit this: the installed plugin served a NEW commands/init.md
# (skeleton.py contract) over an OLD plugin tree without bmad/scripts/skeleton.py,
# so init fell back to a hand copy. Every bmad/scripts/*.py a command names must
# exist in the plugin tree — otherwise the command's contract is unexecutable.
REFS_OK=1
for cmd in "$PLUGIN_ROOT"/commands/*.md; do
    # shellcheck disable=SC2013
    for ref in $(grep -o 'bmad/scripts/[A-Za-z0-9_.-]*\.py' "$cmd" 2>/dev/null | sort -u); do
        if [ ! -f "$PLUGIN_ROOT/$ref" ]; then
            echo "[ERROR] $(basename "$cmd") references $ref but it is missing from the plugin tree"
            PROBLEMS=$((PROBLEMS + 1))
            REFS_OK=0
        fi
    done
done
if [ "$REFS_OK" -eq 1 ]; then
    echo "[OK]   every script referenced by commands/ exists"
fi

echo "== 6d3) Plugin version consistency (.claude-plugin ↔ .plugin ↔ pyproject) =="
# Stale marketplace caches serve old trees under an unchanged version number,
# which is exactly how a NEW command met a MISSING script. The functional
# manifests must agree; a bump that forgets one of them re-creates the skew.
VERSION_FILES="$PLUGIN_ROOT/.claude-plugin/plugin.json $PLUGIN_ROOT/.claude-plugin/marketplace.json $PLUGIN_ROOT/.plugin/plugin.json $PLUGIN_ROOT/.plugin/marketplace.json $PLUGIN_ROOT/pyproject.toml"
VERSION_SEEN=""
VERSION_BAD=0
for vf in $VERSION_FILES; do
    v=$(grep -o '"version"[[:space:]]*:[[:space:]]*"[^"]*"\|version[[:space:]]*=[[:space:]]*"[^"]*"' "$vf" 2>/dev/null | head -1 | grep -o '"[^"]*"$' | tr -d '"')
    if [ -z "$v" ]; then
        echo "[ERROR] no version found in $vf"
        PROBLEMS=$((PROBLEMS + 1))
        VERSION_BAD=1
    elif [ -z "$VERSION_SEEN" ]; then
        VERSION_SEEN="$v"
    elif [ "$v" != "$VERSION_SEEN" ]; then
        echo "[ERROR] version skew: $vf says $v, expected $VERSION_SEEN"
        PROBLEMS=$((PROBLEMS + 1))
        VERSION_BAD=1
    fi
done
if [ "$VERSION_BAD" -eq 0 ]; then
    echo "[OK]   plugin version consistent ($VERSION_SEEN)"
fi

echo "== 6e) Help catalog integrity (columns, code collisions, code vocabulary) =="
# bmad-help routes on bmad/_config/bmad-help.csv, and a real session showed what
# a sloppy catalog costs: with `Core/bmad-spec` wearing the code SP, the help
# output offered "[SP] Sprint Planning" while SP is also this methodology's own
# Sprint-Plan record prefix, and 23 codes are shared across modules (CU/CS/DS…),
# so a bare code can point at two different skills. This check keeps the catalog
# parseable and keeps NEW ambiguity from appearing silently: everything below is
# drift we control, and the allowlist must be edited deliberately.
"$PY" - <<'PY'
import collections, csv, os, sys
from pathlib import Path
PLUGIN = Path(os.environ.get("PLUGIN_ROOT") or ".")
CATALOG = PLUGIN / "bmad" / "_config" / "bmad-help.csv"
HEADER = ["module", "skill", "display-name", "menu-code", "description", "action",
          "args", "phase", "preceded-by", "followed-by", "required",
          "output-location", "outputs"]
# Cross-module collisions that predate this check (upstream module codes we do
# not rename). A code that is NOT in here and is shared by two skills is a new
# ambiguity: either rename one side or add it here with a reason.
KNOWN_AMBIGUOUS = {"AT", "CC", "CE", "CR", "CS", "CU", "DP", "DR", "DS",
                   "ER", "ES", "FI", "IR", "PRD", "SB", "SP", "SS", "ST",
                   "TA", "TD", "TF", "TR", "VD"}
# The record chain's prefixes (E/IR/SP/S/QR/PR) belong to RECORDS. A skill
# whose stage produces that record may still wear the code (bmad-sprint-planning
# → SP, bmad-check-implementation-readiness → IR); anything else wearing it is a
# routing hazard. The concrete regression this stops: bmad-spec shipped as SP,
# so help offered "[SP] Sprint Planning" for a Spec skill (renamed to SPEC).
RESERVED_PREFIXES = ("E", "IR", "SP", "S", "QR", "PR")
MISUSING_REGRESSION = "bmad-spec"  # the skill the rename fixed

problems = []
try:
    with open(CATALOG, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
except OSError as exc:
    print("  MISS: catalog unreadable (%s)" % exc)
    sys.exit(1)
if not rows:
    print("  MISS: catalog is empty")
    sys.exit(1)
if rows[0] != HEADER:
    problems.append("header drifted from the 13-column contract: %r" % (rows[0],))
bad_rows = [i for i, r in enumerate(rows[1:], 2) if len(r) != len(HEADER)]
if bad_rows:
    problems.append("%d row(s) with the wrong column count: lines %s"
                    % (len(bad_rows), bad_rows[:5]))

per_module = collections.defaultdict(lambda: collections.defaultdict(list))
per_code = collections.defaultdict(set)
skills = 0
for row in rows[1:]:
    if len(row) != len(HEADER):
        continue
    module, skill, _display, code = row[0], row[1], row[2], row[3]
    if skill == "_meta":
        continue
    skills += 1
    if code:
        per_module[module][code].append(skill)
        per_code[code].add(module)

# Intra-module duplicates are always wrong: nothing upstream forces them.
for module, codes in sorted(per_module.items()):
    for code, owners in sorted(codes.items()):
        if len(owners) > 1:
            problems.append("duplicate menu code %s inside %s: %s"
                            % (code, module, ", ".join(owners)))

ambiguous = {c for c, modules in per_code.items() if len(modules) > 1}
new_ambiguous = sorted(ambiguous - KNOWN_AMBIGUOUS)
if new_ambiguous:
    problems.append("new cross-module menu-code collision(s): %s "
                    "(rename one side, or declare it in KNOWN_AMBIGUOUS)"
                    % ", ".join(new_ambiguous))
stale_allowlist = sorted(KNOWN_AMBIGUOUS - ambiguous)
if stale_allowlist:
    problems.append("KNOWN_AMBIGUOUS lists code(s) that no longer collide: %s "
                    "(they stopped being ambiguous — drop them from the allowlist)"
                    % ", ".join(stale_allowlist))

notes = []
for code in RESERVED_PREFIXES:
    owners = sorted((module, skill) for module, codes in per_module.items()
                    for skill in codes.get(code, []))
    if not owners:
        continue
    named = ", ".join("%s/%s" % pair for pair in owners)
    if any(skill == MISUSING_REGRESSION for _module, skill in owners):
        problems.append("menu code %s is a record-chain prefix and %s wears it "
                        "(%s) — rename the menu code"
                        % (code, MISUSING_REGRESSION, named))
    else:
        notes.append("menu code %s doubles as the %s record prefix (%s) — "
                     "fine while the skill naming it is the stage that produces "
                     "that record" % (code, code, named))

print("  catalog rows: %d skills, %d modules, %d ambiguous code(s)"
      % (skills, len(per_module), len(ambiguous)))
for p in problems:
    print("  MISS: %s" % p)
for n in notes:
    print("  NOTE: %s" % n)
print("  problems: %d" % len(problems))
sys.exit(1 if problems else 0)
PY
if [ $? -eq 0 ]; then
    echo "[OK]   help catalog parseable, no new code collisions, no record-prefix codes"
else
    echo "[ERROR] help catalog drift (see above) — bmad-help routes on this file"
    PROBLEMS=$((PROBLEMS + 1))
fi

echo "== 6f) Trigger/description collisions (routing determinism, F4.1) =="
# scripts/check-triggers.py: identical descriptions or shared bare triggers
# across skills make routing non-deterministic (bmad vs gds vertical).
# Presence, not a bare call: an unguarded `python3` on a missing sibling emits
# an interpreter error that this stage then counts as a collision — a false
# ERROR on an otherwise healthy tree (same stale-tool class as the §6b `-x`
# guard, E-001/E-002).
if [ -f "$SELF/check-triggers.py" ]; then
    if python3 "$SELF/check-triggers.py" --project-root "$PROJECT_ROOT"; then
        echo "[OK]   no trigger/description collisions"
    else
        echo "[WARNING] trigger/description collision (see above)"
        PROBLEMS=$((PROBLEMS + 1))
    fi
else
    echo "[WARNING] $SELF/check-triggers.py not found — §6f skipped"
    PROBLEMS=$((PROBLEMS + 1))
fi

echo "== 6g) No namespaced-tool priming tokens in shipped agent-facing text =="
# The plugin's own guidance must never spell out a tool naming that does not
# exist. A real Claude Code session emitted `default.Bash`/`default.Read`
# (harness: "No such tool available: default.X") right after reading the skill
# and command text that spelled those failures out — the guidance primed the
# exact token it warned against (B-004 S4, B-006). The source rewrite removed
# every literal, but the class is mechanical, so this scan keeps it from
# returning: any `default.<ToolName>` spelling, or the harness error string, in
# a file the agent reads is an ERROR. Prose like "by default." is not a match —
# the pattern requires the namespaced tool spelling. Engine tests are excluded:
# the contract test asserts the token is ABSENT, so it legitimately names it.
PRIMING_ERRORS=0
PRIMING_PATTERN='default\.(x|read|write|edit|multiedit|bash|powershell|grep|glob|task|skill|terminal|file_editor)\b|no such tool available: default\.'
PRIMING_SCAN="$PLUGIN_ROOT/hooks/scripts $PLUGIN_ROOT/hooks/engine $PLUGIN_ROOT/commands $PLUGIN_ROOT/skills"
PRIMING_HITS=$(grep -rnEi "$PRIMING_PATTERN" $PRIMING_SCAN 2>/dev/null \
    | grep -v "/tests/" | grep -v "__pycache__" || true)
if [ -n "$PRIMING_HITS" ]; then
    echo "$PRIMING_HITS" | head -10
    echo "[ERROR] namespaced-tool priming token(s) in shipped agent-facing text — use the harness's bare tool names"
    PROBLEMS=$((PROBLEMS + 1))
    PRIMING_ERRORS=1
fi
if [ "$PRIMING_ERRORS" -eq 0 ]; then
    echo "[OK]   no namespaced-tool priming tokens in shipped agent-facing text"
fi

echo
if [ "$PROBLEMS" -eq 0 ]; then
    echo "STATUS: HEALTHY (all checks passed)"
    exit 0
else
    echo "STATUS: $PROBLEMS problems found"
    exit 1
fi

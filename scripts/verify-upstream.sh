#!/usr/bin/env bash
# Check this kernel's exports against the real upstream tools installed by scripts/setup-upstream.sh.
#
#   bash scripts/verify-upstream.sh
#
# It answers three questions and prints one line per answer:
#   1. does the Hermit install run its own documented quickstart (0.0 -> 1.0, no errors)?
#   2. does `hermit check` accept the run.yaml + adapter tree `optimization export` writes, and does `hermit eval`
#      roll it out (against a local stub endpoint, so no paid model and no network)?
#   3. does the SkillOpt CLI parse the six-section `skillopt.yaml` the native lane writes?
#
# Step 3 stops where the native lane is documented to stop: an env adapter. "Unknown environment" after the full
# header is the pass condition, because it proves every section and key was accepted first.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HERMIT_SRC="$ROOT/vendor/hermit"
PY="$ROOT/.venv-upstream/Scripts/python.exe"; [ -x "$PY" ] || PY="$ROOT/.venv-upstream/bin/python"
HERMIT="$ROOT/.venv-upstream/Scripts/hermit.exe"; [ -x "$HERMIT" ] || HERMIT="$ROOT/.venv-upstream/bin/hermit"
SKILLOPT="$ROOT/.venv-upstream/Scripts/skillopt-train.exe"; [ -x "$SKILLOPT" ] || SKILLOPT="$ROOT/.venv-upstream/bin/skillopt-train"

[ -x "$PY" ] && [ -x "$HERMIT" ] || { echo "run bash scripts/setup-upstream.sh first"; exit 1; }
[ -d "$HERMIT_SRC" ] || { echo "vendor/hermit is missing; run bash scripts/setup-upstream.sh"; exit 1; }

TMP="$(mktemp -d)"
STUB_PID=""
cleanup() {
    [ -n "$STUB_PID" ] && kill "$STUB_PID" 2>/dev/null
    if [ "${FAILED:-0}" -eq 0 ]; then rm -rf "$TMP"; else echo "logs kept in $TMP"; fi
}
trap cleanup EXIT

W() { cygpath -m "$1" 2>/dev/null || printf '%s' "$1"; }   # a path Python and Hermit agree on (native form on Windows)
TMPW="$(W "$TMP")"
export MODEL_API_KEY=stub MODEL_BASE_URL=http://127.0.0.1:8765/v1 MODEL_NAME=stub-model
export OPENAI_API_KEY=stub
FAILED=0

# ---------- 1. Hermit's own quickstart ----------
echo "== Hermit quickstart (docs/_examples/first_training) =="
( cd "$HERMIT_SRC" && rm -rf .demo && "$HERMIT" check --config docs/_examples/first_training/run.yaml >"$TMP/q-check.log" 2>&1 )
[ $? -eq 0 ] && echo "  check ..... ok" || { echo "  check ..... FAILED (see $TMP/q-check.log)"; FAILED=1; }
( cd "$HERMIT_SRC" && "$HERMIT" train --config docs/_examples/first_training/run.yaml >"$TMP/q-train.log" 2>&1 ) \
    && echo "  train ..... ok" || { echo "  train ..... FAILED (see $TMP/q-train.log)"; FAILED=1; }
( cd "$HERMIT_SRC" && "$HERMIT" eval --config docs/_examples/first_training/run.yaml --adapter final >"$TMP/q-eval.log" 2>&1 )
SCORE="$("$PY" - "$(W "$TMP")" <<'PY'
import json, pathlib, re, sys
text = (pathlib.Path(sys.argv[1]) / "q-eval.log").read_text(encoding="utf-8", errors="replace")
match = re.search(r"\{.*\}", text, re.S)
print(json.loads(match.group(0))["mean_score"] if match else "none")
PY
)"
if [ "$SCORE" = "1.0" ]; then echo "  eval ...... ok (mean_score 1.0)"; else echo "  eval ...... FAILED (mean_score $SCORE)"; FAILED=1; fi

# ---------- 2. this kernel's Hermit export, through the real checker ----------
echo "== optimization export --target hermit, checked and rolled out by Hermit =="
BUNDLE="$TMP/bundle"; OUT="$TMP/hermit-export"
"$PY" - "$(W "$BUNDLE")" <<'PY'
import json, pathlib, sys
d = pathlib.Path(sys.argv[1]); d.mkdir(parents=True)
(d / "ADAPTER.md").write_text("# Adapter\n\nAnswer the question and cite the table.\n", encoding="utf-8")
(d / "cases.jsonl").write_text("".join(json.dumps({
    "id": f"c{i:02d}",
    "input": f"Question {i}: report the quarterly figure and cite the table it came from.",
    "requires": ["answer"]}) + "\n" for i in range(1, 33)), encoding="utf-8")
(d / "optimization.toml").write_text("""[run]
name = "live"
method = "skillopt"
seed = 7
epochs = 1
batch_size = 8
val_size = 4
heldout_size = 4
min_cases = 2

[artifact]
kind = "adapter"
path = "ADAPTER.md"
max_tokens = 2000

[cases]
path = "cases.jsonl"

[gate]
min_improvement = 0.0
alpha = 0.05
max_edit_chars = 400
textual_lr = 0.20
max_hunks = 3
require_heldout = true
""", encoding="utf-8")
PY
( cd "$ROOT" && "$PY" -m optimization.cli train --spec "$(W "$BUNDLE")/optimization.toml" --epochs 1 >"$TMP/train.log" 2>&1 )
TRAIN_RC=$?
# 3 is a rejected gate: a run that measured and refused still has a version to export (v0000).
if [ "$TRAIN_RC" -eq 0 ] || [ "$TRAIN_RC" -eq 3 ]; then
    echo "  train ..... ok (exit $TRAIN_RC)"
else
    echo "  train ..... FAILED (exit $TRAIN_RC, see $TMP/train.log)"; FAILED=1
fi
( cd "$ROOT" && "$PY" -m optimization.cli export --spec "$(W "$BUNDLE")/optimization.toml" --target hermit --out "$(W "$OUT")" >"$TMP/export.log" 2>&1 )
[ $? -eq 0 ] && echo "  export .... ok" || { echo "  export .... FAILED (see $TMP/export.log)"; FAILED=1; }

cat > "$TMP/mock_model.py" <<'STUB'
"""A local OpenAI-compatible chat endpoint: one deterministic answer, so the check needs no paid model."""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        body = {"id": "stub", "object": "chat.completion", "model": "stub-model",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "answer"},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 1, "total_tokens": 13}}
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 8765), Handler).serve_forever()
STUB
"$PY" "$TMP/mock_model.py" >"$TMP/stub.log" 2>&1 &
STUB_PID=$!
for _ in $(seq 1 50); do
    "$PY" -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8765/v1/chat/completions", b"{}", timeout=1)' 2>/dev/null && break
    sleep 0.2
done

if [ -f "$OUT/run.yaml" ]; then
    ( cd "$OUT" && "$HERMIT" check --config run.yaml llm.base_url=http://127.0.0.1:8765/v1 llm.model=stub-model >"$TMP/h-check.log" 2>&1 )
    [ $? -eq 0 ] && echo "  check ..... ok (template, evolver, task, sandbox, agent probe)" \
                 || { echo "  check ..... FAILED (see $TMP/h-check.log)"; FAILED=1; }
    ( cd "$OUT" && "$HERMIT" eval --config run.yaml --adapter none llm.base_url=http://127.0.0.1:8765/v1 llm.model=stub-model >"$TMP/h-eval.log" 2>&1 )
    METRICS="$("$PY" - "$(W "$TMP")" <<'PY'
import json, pathlib, re, sys
text = (pathlib.Path(sys.argv[1]) / "h-eval.log").read_text(encoding="utf-8", errors="replace")
match = re.search(r"\{.*\}", text, re.S)
if not match:
    print("none")
else:
    data = json.loads(match.group(0))
    print(f"{data['tasks']} tasks, mean {data['mean_score']}, errors {data['errors']}")
PY
)"
    case "$METRICS" in
        "4 tasks, mean 1.0, errors 0") echo "  eval ...... ok ($METRICS)" ;;
        *) echo "  eval ...... FAILED ($METRICS, see $TMP/h-eval.log)"; FAILED=1 ;;
    esac
else
    echo "  check ..... SKIPPED (no run.yaml)"; FAILED=1
fi

# ---------- 3. the SkillOpt CLI on the native config ----------
echo "== optimization export --target skillopt --lane native, parsed by skillopt-train =="
NATIVE="$TMP/native"
( cd "$ROOT" && "$PY" -m optimization.cli export --spec "$(W "$BUNDLE")/optimization.toml" --target skillopt --out "$(W "$NATIVE")" --lane native >"$TMP/native.log" 2>&1 )
if [ -f "$NATIVE/skillopt.yaml" ]; then
    ( cd "$NATIVE" && "$SKILLOPT" --config skillopt.yaml >"$TMP/so.log" 2>&1 )
    if grep -q "Unknown environment" "$TMP/so.log" && grep -q "edit_budget:    3" "$TMP/so.log"; then
        echo "  config .... ok (all six sections parsed; stops at the env adapter, as documented)"
    else
        echo "  config .... FAILED (see $TMP/so.log)"; FAILED=1
    fi
else
    echo "  config .... FAILED (no skillopt.yaml)"; FAILED=1
fi

echo ""
[ "$FAILED" -eq 0 ] && echo "VERIFIED: both upstream tools accept this kernel's exports." \
                    || echo "FAILED: see the logs above."
exit "$FAILED"

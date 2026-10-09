#!/usr/bin/env python3
"""Record-bound mechanical approval gate for the research methodology.

The gate reads the hypothesis threshold FROM the experiment record file, so
the threshold cannot be silently changed at the command line. It is the ONLY
writer of the decision: a record that says APPROVED without a valid GATE-OK
token is forged (detected by --verify).

Flow:
  draft record written (Theory/Hypothesis/Measurement Metrics/Experiment Design, Status: planned)
  -> experiment runs, value measured MECHANICALLY by the gate
  -> run_experiment.py --record <rec> --run "<cmd>" [--raw "<note>"]
        gate executes <cmd>, parses the measured value from its stdout (it never
        trusts an operator-supplied value), compares to the claim, writes
        Raw Results / Decision / Gate Evidence / Next Step / Status
  -> later: run_experiment.py --verify <rec>   (confirms the decision is genuine)

One measurement, one decision per record. A decided record refuses a re-run;
a new measurement requires a new experiment record.

`--dry-run` previews a decision (parsed claim/threshold, measured value,
PASS/FAIL, Wilson bound, the lines that would be written, and the would-be
GATE-OK token) WITHOUT writing anything to the record. Every format/draft
check must use --dry-run: a gate run without it WRITES a real decision into
the record (E-189 lesson).

Usage:
  run_experiment.py --record docs/experiments/E-001.md --run "<cmd>"
  run_experiment.py --record docs/experiments/E-001.md --run "<cmd>" --dry-run
  run_experiment.py --verify  docs/experiments/E-001.md
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import math
import os
import pathlib
import re
import secrets as _secrets
import shlex
import subprocess
import sys
from datetime import date as _date

# Plan-layer helpers (E-065): the engine's plan module owns plan parsing and
# the amendment chain; the gate imports it for --amend-plan and for binding
# the final token to the final plan hash. sys.path first: the module lives in
# the engine tree, not next to this script. Layout (this file at <root>/skills/
# bmad-research-experiment/scripts/): parents[0]=scripts, [1]=skill, [2]=skills,
# [3]=<methodology-root>.
_ENGINE_DIR = pathlib.Path(__file__).resolve().parents[3] / "hooks" / "engine"
if _ENGINE_DIR.is_dir() and str(_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(_ENGINE_DIR))
try:
    from modules import plan as _plan  # noqa: E402
except Exception:  # pragma: no cover - standalone installs without the engine
    _plan = None

OPS = {">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b, "==": lambda a, b: a == b,
       ">": lambda a, b: a > b, "<": lambda a, b: a < b}
OP_RE = re.compile(r">=|<=|==|>|<")
QUOTED_CLAIM = re.compile(r'"([^"]+)"')
# A measured value line from a bench script: `metric_accuracy=0.93 (14/15)` or
# `metric_score=0.80`. The gate only trusts a number it parsed from the run's own output.
# group 2 = value; groups 3/4 = optional `(x/y)` sample-size fraction.
MEASURED_RE = re.compile(
    r"(?i)(?:^|\s)([A-Za-z_][\w]*)_(?:accuracy|validity|precision|score|rate|quality)"
    r"\s*=\s*(-?\d+(?:\.\d+)?)(?:\s*\(\s*(\d+)\s*/\s*(\d+)\s*\))?")

# --- GATE-OK token: HMAC-SHA256(key, did|claim|measured). Keys live OUTSIDE the
# repo: the machine's own key at ~/.bmad/gate-key (or the BMAD_GATE_KEY env var)
# plus an optional trust ring of peer keys in ~/.bmad/gate-keys/*.key. Without a
# key the token cannot be reproduced, so forged records cannot pass --verify. ---
SECRET_ENV = "BMAD_GATE_KEY"
SECRET_FILE = str(pathlib.Path.home() / ".bmad" / "gate-key")
SECRETS_DIR = str(pathlib.Path.home() / ".bmad" / "gate-keys")


class GateError(Exception):
    pass


def load_secret() -> bytes | None:
    """Return the machine's own gate key (env overrides file), or None."""
    env = os.environ.get(SECRET_ENV)
    if env and env.strip():
        return env.strip().encode("utf-8")
    try:
        data = pathlib.Path(SECRET_FILE).read_text(encoding="utf-8").strip()
        if data:
            return data.encode("utf-8")
    except OSError:
        pass
    return None


def trusted_secrets() -> list[bytes]:
    """Keys that --verify accepts, primary first, deduplicated.

    Multi-machine development (the 2026-09-24 request: the same repo is
    developed in parallel on several computers): each machine signs with its
    own key, but verification consults the developer's whole trust ring —
    the machine's own key plus every peer key imported into
    ~/.bmad/gate-keys/ (one 64-hex key per file). A token is genuine if it
    re-derives under ANY ring key: provenance across your own machines,
    not tampering. Attack surface is unchanged — an attacker still needs
    one of the ring keys, which live outside the repo. Fail-open: an
    unreadable or malformed peer file is skipped, never fatal.
    """
    ring: list[bytes] = []
    primary = load_secret()
    if primary is not None:
        ring.append(primary)
    try:
        for peer in sorted(pathlib.Path(SECRETS_DIR).glob("*.key")):
            try:
                data = peer.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            key = data.encode("utf-8")
            if key and key not in ring:
                ring.append(key)
    except OSError:
        pass
    return ring


def require_secret() -> bytes:
    secret = load_secret()
    if secret is None:
        raise GateError(
            "gate key not found. Run first: python3 run_experiment.py --init-secret "
            f"(writes: {SECRET_FILE}) or set the {SECRET_ENV} environment variable.")
    return secret


def require_any_secret() -> list[bytes]:
    """Keys --verify may accept (own key + trust ring), or GateError if empty."""
    secrets = trusted_secrets()
    if not secrets:
        raise GateError(
            "gate key not found. Run first: python3 run_experiment.py --init-secret "
            f"(writes: {SECRET_FILE}) or set the {SECRET_ENV} environment variable.")
    return secrets


def import_key() -> int:
    """Import a peer machine's gate key into this machine's trust ring.

    Reads the key material from the BMAD_PEER_GATE_KEY environment variable
    (never from argv — command lines leak through shell history and process
    listings) and stores it in ~/.bmad/gate-keys/<label>.key outside the
    repo. After importing, records APPROVED on the peer machine verify here
    without any re-measurement ceremony. Refuses malformed (non-hex) keys:
    a wrong paste should fail loudly, not silently verify nothing.
    """
    peer = os.environ.get("BMAD_PEER_GATE_KEY", "").strip()
    if not peer:
        print("ERROR: set BMAD_PEER_GATE_KEY to the peer machine's key material "
              "(stdin/argv transfer is refused — command lines leak).", file=sys.stderr)
        return 2
    if not re.fullmatch(r"[0-9a-fA-F]{64}", peer):
        print("ERROR: key material must be 64 hex characters (the output of "
              "--init-secret's key file).", file=sys.stderr)
        return 2
    label = _peer_label()
    path = pathlib.Path(SECRETS_DIR) / f"{label}.key"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(peer + "\n", encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass  # chmod limited on Windows; out-of-repo file protection is sufficient
        print(f"peer key imported: {path}")
        print("Records APPROVED on that machine now verify here. "
              "Signing always uses this machine's own key.")
        return 0
    except OSError as exc:
        print(f"ERROR: could not write peer key: {exc}", file=sys.stderr)
        return 2


def _peer_label() -> str:
    label = os.environ.get("BMAD_PEER_LABEL", "").strip()
    if label and re.fullmatch(r"[A-Za-z0-9._-]{1,64}", label):
        return label
    return "peer"


def init_secret() -> int:
    """Generate a fresh gate key outside the repo. Call once per machine."""
    key = _secrets.token_hex(32)
    path = pathlib.Path(SECRET_FILE)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(key + "\n", encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass  # chmod limited on Windows; out-of-repo file protection is sufficient
        print(f"GATE-OK key written: {path}")
        print("Key is kept OUTSIDE the repo (not in git). If lost, existing approvals")
        print("cannot be verified — you need a new key for new approvals.")
        return 0
    except OSError as exc:
        print(f"ERROR: could not write key: {exc}", file=sys.stderr)
        return 2


def check_secret() -> int:
    ring = trusted_secrets()
    own = load_secret() is not None
    if own:
        print(f"[OK] gate key present ({SECRET_FILE} or {SECRET_ENV})")
    if own and len(ring) > 1:
        print(f"[OK] trust ring: {len(ring)} keys — own key + {len(ring) - 1} peer "
              f"key(s) from {SECRETS_DIR}")
    elif own:
        print(f"[OK] trust ring: own key only (import peers with --import-key "
              f"into {SECRETS_DIR})")
    if ring:
        if not own:
            print(f"[OK] no own key, but trust ring has {len(ring)} peer key(s) "
                  f"({SECRETS_DIR}) — verify works, signing does not.")
        return 0
    print(f"[ERROR] gate key not found. Run: python3 run_experiment.py --init-secret",
          file=sys.stderr)
    return 1

# Records use English field labels. The gate parses exactly these labels.
# 'Code Scope' is mandatory: it tells the gate which code files the approval opens.
# guard only allows writes to files matching the scope.
REQUIRED_DRAFT = ("Theory", "Hypothesis", "Measurement Metrics", "Experiment Design", "Code Scope")


def parse_scope(scope: str) -> list[str]:
    """Split a 'Code Scope' value into patterns (comma/whitespace separated)."""
    if not scope:
        return []
    return [p for p in re.split(r"[,\s]+", scope) if p]


def glob_to_regex(pattern: str) -> str:
    """Convert a scope glob to a regex. Semantics:
      '*'  -> one path segment, '**' -> any depth (incl. zero), '?' -> one char.
    Backslashes are normalized to forward slashes first.
    """
    pat = pattern.replace("\\", "/")
    out = ["^"]
    i, n = 0, len(pat)
    while i < n:
        c = pat[i]
        if c == "*":
            if i + 1 < n and pat[i + 1] == "*":
                if i + 2 < n and pat[i + 2] == "/":
                    out.append("(?:[^/]*/)*")
                    i += 3
                else:
                    out.append(".*")
                    i += 2
            else:
                out.append("[^/]*")
                i += 1
            continue
        if c == "?":
            out.append("[^/]")
            i += 1
            continue
        out.append(re.escape(c))
        i += 1
    out.append("$")
    return "".join(out)


def scope_matches(scope: str, target: str) -> bool:
    """True if the record's 'Code Scope' covers the target file path (project-relative)."""
    if not scope or not target:
        return False
    target = target.replace("\\", "/").lstrip("./")
    for pat in parse_scope(scope):
        if re.fullmatch(glob_to_regex(pat), target, re.IGNORECASE):
            return True
    return False


def record_scope(record_path: str) -> str:
    """Return the 'Code Scope' field of a record ('' when missing)."""
    try:
        text = open(record_path, encoding="utf-8").read()
    except (OSError, UnicodeDecodeError):
        # Missing OR not valid UTF-8: a record with no readable scope authorizes
        # nothing, so '' is the fail-safe direction and never a traceback (E-016).
        return ""
    return record_fields(text).get("Code Scope", "").strip()


# --- Documentary mode (B/C/D) record validator. Unlike the numeric gate:
# not mechanical, but checks completeness and honesty fields. ---
DOC_FIELDS = {
    "Finding": ("Date", "Status", "Research Question", "Context", "Method", "Finding",
                "Evidence", "Counter-evidence", "Interpretation", "Uncertainty", "Decision", "Next Step"),
    "Design": ("Date", "Status", "Design Question", "User / Need", "Scenario",
               "Design Idea", "Prototype", "Feedback", "Uncertainty", "Decision",
               "Next Step"),
    "Contextual": ("Date", "Status", "Contextual Issue", "Scope / Context", "Stakeholders",
                   "Conditions / Constraints", "System Dynamics", "Evidence", "Uncertainty",
                   "Decision", "Next Step"),
}
# Honesty fields: must not be left empty (counter-evidence, uncertainty, source).
HONESTY_FIELDS = {
    "Finding": ("Evidence", "Counter-evidence", "Uncertainty"),
    "Design": ("Feedback", "Uncertainty"),
    "Contextual": ("Evidence", "Uncertainty"),
}
DECISION_RE = re.compile(r"^(APPROVED|REJECTED|REVISED|DEFERRED)")

# A draft record leaves its Decision/Gate Evidence/Next Step as a placeholder
# (gate hasn't written yet). Two placeholder forms exist in the codebase: the
# template's '<gate writes: ...>' angle bracket and the em-dash '— (gate writes)'
# used by the record writers (both count as undecided).
# A placeholder must NOT count as a decision — otherwise a draft copy would be
# rejected as "already decided" or flagged FORGED.
def _is_placeholder(value: str) -> bool:
    v = value.strip()
    return not v or bool(re.fullmatch(r"<.*>", v)) or v.startswith("—")


def validate_doc(path: str) -> int:
    """Validate a Mod B/C/D record for completeness and honesty. 0=OK, 1=issues, 2=not a doc."""
    try:
        text = open(path, encoding="utf-8").read()
    except (OSError, UnicodeDecodeError) as exc:
        print(f"ERROR: cannot read: {path}: {exc}", file=sys.stderr)
        return 2
    fields = record_fields(text)
    if re.search(r"##\s+Experiment:", text):
        print("This is a Mod A record (E-id) — --validate is for Mod B/C/D; use --verify for Mod A.",
              file=sys.stderr)
        return 2
    kind = None
    for k in ("Finding", "Design", "Contextual"):
        if re.search(rf"##\s+{k}:", text):
            kind = k
            break
    if kind is None:
        print(f"Unrecognized record header: {path}", file=sys.stderr)
        return 2

    problems = []
    for f in DOC_FIELDS[kind]:
        val = fields.get(f, "").strip()
        if _is_placeholder(val):
            problems.append(f"missing/empty '{f}'")
    karar = fields.get("Decision", "").strip()
    if karar and not _is_placeholder(karar) and not DECISION_RE.search(karar):
        problems.append(f"Invalid Decision format: '{karar[:40]}'")
    for f in HONESTY_FIELDS[kind]:
        val = fields.get(f, "").strip()
        if _is_placeholder(val):
            problems.append(f"honesty field '{f}' is empty")

    for p in problems:
        print(f"  {path}: {p}")
    if problems:
        print(f"[WARNING] {kind} record incomplete: {len(problems)} issues")
        return 1
    print(f"[OK] {path} — {kind} record complete with all honesty fields filled.")
    return 0


def parse_claim(claim: str) -> tuple[float, str]:
    """From '<metric> <op> <threshold>' -> (threshold, op)."""
    m = OP_RE.search(claim)
    if not m:
        raise ValueError(f"'{claim}' has no supported operator (>, >=, ==, <=, <)")
    op = m.group(0)
    try:
        threshold = float(claim[m.end():].strip())
    except ValueError as exc:
        raise ValueError(f"threshold in '{claim}' is not a number") from exc
    return threshold, op


def evaluate(claim: str, measured: float) -> tuple[bool, str]:
    """Return (passed, human-readable summary). Raises ValueError on bad claim."""
    threshold, op = parse_claim(claim)
    passed = OPS[op](measured, threshold)
    return passed, f"measured={measured} {op} threshold={threshold}"


def record_fields(text: str) -> dict:
    """Extract '- **Field:** value' lines into a dict."""
    out = {}
    for line in text.splitlines():
        m = re.match(r"^\s*-\s*\*\*([^*]+):\*\*\s*(.*)$", line)
        if m:
            out[m.group(1).strip()] = m.group(2).strip()
    return out


def _missing_field_hint(text: str, need: str) -> str:
    """Closest-match hint when a REQUIRED_DRAFT field is absent.

    2026-09-25 LIMX session: the E-001 draft carried its content under
    `## Experiment Design`-style headings (or translated labels), so
    record_fields() — bullet+bold only — found nothing and the gate printed
    a bare "record missing 'Experiment Design'". The agent then spent 7 tool
    calls reading the gate's own parser source (parse_record, record_fields)
    instead of fixing the format. Name the closest line found and the exact
    required bullet form so the fix is one edit, not source archaeology.
    """
    import difflib
    candidates: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        m = re.match(r"^\s*-\s*\*\*([^*]+):\*\*.*$", line)
        if m:
            candidates.append(m.group(1).strip())
            continue
        m = re.match(r"^#{1,4}\s+(.+?)\s*$", s)
        if m:
            candidates.append(m.group(1).strip().rstrip(":"))
    hit = difflib.get_close_matches(need, candidates, n=1, cutoff=0.6)
    if hit:
        return (f" — found similar '{hit[0]}': rename it to the exact "
                f"bullet form '- **{need}:** ...' (see docs/experiments/_template.md)")
    return (f" — add the exact bullet line '- **{need}:** ...' "
            f"(see docs/experiments/_template.md)")


def deney_id(text: str) -> str:
    m = re.search(r"##\s+Experiment:\s*([\w.\-]+)", text)
    return m.group(1) if m else "E-?"


# An experiment id must NAME a run: 'E-' + a token carrying a number
# ('E-056', 'E-056-integration-completion', 'E-2026-09-28'). Anything else
# ('E-?', 'E-XYZ') cannot be bound to a decision token honestly.
_EXPERIMENT_ID_RE = re.compile(r"E-[\w.\-]*\d[\w.\-]*\Z")


def experiment_id_issue(text: str) -> str | None:
    """Why the record's Experiment id cannot name a run (None = fine).

    The id is read from an h2 heading (`## Experiment: E-054`) and the approval
    token is BOUND to it, so a record whose heading sits at another level
    (`# Experiment: …`) or whose id token is unparseable (`## Experiment:`
    followed by an em-dash) used to be measured anyway: the id collapsed to
    'E-?' and the gate still wrote a decision under a run that does not exist —
    only `--verify` on the already-decided record exposed the forged id (real
    session, 2026-09-28). Reject before the measurement runs.
    """
    if not re.search(r"##\s+Experiment:", text):
        return ("record has no parseable '## Experiment: E-<id>' h2 heading — a "
                "heading at another level (h1) or a bold 'Experiment:' label is "
                "not read, so the run id would collapse to 'E-?' and the decision "
                "would name no experiment. Keep the record heading as "
                "'## Experiment: E-<id> — <title>' (see docs/experiments/_template.md).")
    did = deney_id(text)
    if not _EXPERIMENT_ID_RE.match(did):
        return (f"record's '## Experiment: E-<id>' heading carries no usable id "
                f"(read as '{did}') — the id must look like E-<number> "
                f"(e.g. '## Experiment: E-056 — title').")
    return None


def hypothesis_claim(hypothesis: str) -> tuple[str, str]:
    """From 'H-001: \"accuracy >= 0.90\"' -> (id, claim)."""
    hm = re.search(r"(H-\d+)[:\s]", hypothesis)
    hid = hm.group(1) if hm else "H-?"
    m = QUOTED_CLAIM.search(hypothesis)
    if m:
        return hid, m.group(1)
    tail = re.search(r"[:\s]+(.+)$", hypothesis)
    if tail:
        return hid, tail.group(1).strip()
    raise ValueError("record 'Hypothesis' line must look like 'H-001: \"metric >= 0.90\"'")


def gate_token(claim: str, measured: float, did: str, secret: bytes,
               cmd: str | None = None, scope: str | None = None,
               plan_hash: str | None = None) -> str:
    """GATE-OK token: HMAC-SHA256(secret, 'GATE-OK|did|claim|measured[|cmd_sha256][|scope][|plan_sha256]').

    Secret-gated: without the key the token cannot be reproduced, so a forged
    APPROVED record cannot pass --verify (unlike the old sha1(claim|measured)
    scheme, which anyone with the open-source script could compute).
    cmd: the measurement command. When given, the token binds to the EXACT
    measurement (new-style); editing 'Measurement Command' after approval breaks the
    token. Legacy tokens (cmd=None) keep verifying — backward compatibility.
    scope: the record's Code Scope. When given, the token binds to the approved
    file set — widening the scope after approval breaks the token. Records
    approved before scope binding verify via the legacy path.
    plan_hash: the record's final Implementation Plan hash (E-065). When given,
    the token binds to the FINAL plan (post-amendments) — plan growth after
    approval (stripping/reordering amendments) breaks the token; growth before
    approval goes through --amend-plan and is legitimate. Verify accepts records
    whose token carries NO plan binding (pre-E-065 approvals) via the legacy
    path — never the reverse.
    """
    payload = f"GATE-OK|{did}|{claim}|{measured}"
    if cmd is not None:
        payload += "|" + hashlib.sha256(cmd.encode("utf-8")).hexdigest()
    if scope is not None:
        payload += "|" + hashlib.sha256(" ".join(sorted(parse_scope(scope))).encode("utf-8")).hexdigest()
    if plan_hash is not None:
        payload += "|" + plan_hash
    mac = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"GATE-OK-{did}-{mac[:32]}"


def wilson_lower(x: int, n: int, z: float = 1.96) -> float:
    """95% Wilson score lower bound for observed x/n successes. 0.0 when n <= 0."""
    if n <= 0:
        return 0.0
    p = x / n
    z2 = z * z
    denom = 1 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
    return max(0.0, center - half)


METRIC_SUFFIXES = ("_accuracy", "_validity", "_precision", "_score", "_rate", "_quality")


def metric_stem(name: str) -> str:
    """Strip a known metric suffix: 'llm_overnight_accuracy' -> 'llm_overnight'."""
    for s in METRIC_SUFFIXES:
        if name.endswith(s):
            return name[: -len(s)]
    return name


def claim_metric_name(claim: str) -> str:
    """Metric name before the operator, stemmed: 'llm_overnight_accuracy >= 0.90' -> 'llm_overnight'."""
    m = OP_RE.search(claim)
    if not m:
        return ""
    return metric_stem(claim[: m.start()].strip())


def uncertainty_note(x: int | None, n: int | None, value: float, threshold: float,
                     op: str) -> str:
    """Mechanical rule-4 confession: return the 'Uncertainty' line content.

    n is advisory, never a rejection. Returns 'none' when the sample is large enough
    for the threshold, an explanation when it is too small, or 'n unknown'.
    """
    if n is None or x is None:
        return "n unknown (sample size could not be parsed)"
    # The count only matters for upper-bound claims: a small sample inflates
    # confidence that the rate >= threshold.
    if op not in (">=", ">"):
        return "none (lower-bound claim: no small-sample risk)"
    if abs(x / n - value) > 0.02:
        return f"n unknown (x/y={x}/{n} value {value} inconsistent)"
    lower = wilson_lower(x, n)
    if lower >= threshold:
        return f"none (n={n}, 95% Wilson lower bound {lower:.2f} >= threshold {threshold:g})"
    return (f"n={n} (small sample: 95% Wilson lower bound {lower:.2f} < threshold {threshold:g})")


def run_and_measure(cmd: str, claim_metric: str | None = None
                    ) -> tuple[float, int | None, int | None, str | None]:
    """Run the measurement script; return (value, x, y, metric_stem) from its stdout.

    When claim_metric is given (the metric name the hypothesis claims), the
    matching ``metric_*`` line is chosen — a bench that prints several metrics
    must not have its decision bind to whichever line happened to come first
    (E-004). No line carries the claimed metric: the FIRST parsed line is
    used and the normal metric-MISMATCH advisory handles the redefinition
    (backward compatible). Two lines with the same stem but conflicting values
    are self-contradicting output and fail closed.
    """
    try:
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=600)
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"measurement run timed out after 600s: {cmd}") from exc
    if proc.returncode != 0:
        raise ValueError(
            f"measurement run exited {proc.returncode}: {cmd}\n{proc.stdout}\n{proc.stderr}")
    matches = list(MEASURED_RE.finditer(proc.stdout))
    if not matches:
        raise ValueError(
            f"could not parse a measured value from the run output: {cmd}\n{proc.stdout}")
    # Self-contradicting output fails closed: the SAME metric printed twice
    # must agree (banner/summary repetition is fine — same values). Different
    # metric stems may legitimately differ.
    by_stem: dict[str, list] = {}
    for mm in matches:
        by_stem.setdefault(metric_stem(mm.group(1)), []).append(mm)
    for stem, group in by_stem.items():
        first = group[0]
        for other in group[1:]:
            if (abs(float(other.group(2)) - float(first.group(2))) > 1e-12
                    or int(other.group(3) or 0) != int(first.group(3) or 0)
                    or int(other.group(4) or 0) != int(first.group(4) or 0)):
                raise ValueError(
                    f"conflicting repeated metric lines for '{stem}' "
                    f"({first.group(1)}={first.group(2)} vs "
                    f"{other.group(1)}={other.group(2)}): {cmd}")
    if claim_metric and claim_metric in by_stem:
        m = by_stem[claim_metric][0]
    else:
        # Claim absent from the output: first line binds and the normal
        # metric-MISMATCH advisory handles the redefinition downstream.
        m = matches[0]
    val = float(m.group(2))
    x = int(m.group(3)) if m.group(3) else None
    y = int(m.group(4)) if m.group(4) else None
    # --run mode requires the sample-size denominator: without (x/y) the gate cannot
    # enforce rule 4, and 'n bilinmiyor' would be a bypass of ADVISORY-BLOCK.
    if y is None:
        raise ValueError(
            f"no sample-size denominator '(x/y)' in the run output: {cmd}\n{proc.stdout}")
    return val, x, y, metric_stem(m.group(1))


# --- Rule: measurement script cannot live in a free zone ---
# The gate requires the measurement itself to be under methodology protection.
# Free zones (scratch/tmp/temp and the other guard-free surfaces: graft/,
# openhands/, _bmad/, .metodoloji/, root-level explore_* files) hold freely
# fabricated drafts; a bench there could pass approval without any gate
# oversight. A bench living in a protected area becomes an approved artifact
# (every change goes through the gate). Console (stdout) leakage and base64
# obfuscation are documented attack boundaries.
# the guard's lexical normalization (hooks/engine/modules/utils.py — norm_path):
# './' and drive prefixes are stripped BEFORE matching, so './explore_probe.py'
# and 'C:\proj\scratch\b.py' are the same free surface as their bare spellings.
# Anchor two cases: (^|/) for guard prefixes/stems, and a bare-start alternative
# for the guard's ./-stripped root-file shapes.
_AGENT_BENCH_ZONE = re.compile(
    r"(?i)(?:^|[/\\])(?:scratch|tmp|temp|graft|openhands|_bmad|\.metodoloji)"
    r"(?:[/\\]|$)")  # guard FREE_PREFIXES (project-relative, norm_path'd)
_EXPLORE_FILE = re.compile(r"(?i)^(?:\./)?explore_[^/\\]*$")
_KNOWN_INTERP = {"python", "python3", "py", "sh", "bash", "zsh", "node", "nodejs",
                 "deno", "bun", "perl", "ruby", "php", "rscript", "uv"}


# Single-flag interpreter options that take no value (skipped to find the script).
_INTERP_VALUELESS = frozenset({
    "-u", "-b", "-B", "-E", "-I", "-O", "-OO", "-q", "-s", "-S", "-v", "-V",
    "-W", "-X", "-x", "--verbose",
})


def _bench_target(cmd: str) -> str | None:
    """Return the first script/file path the measurement command EXECUTES (for target checking).

    'python3 src/bench.py data.csv' -> 'src/bench.py' (not data arguments);
    'sh scripts/bench.sh' -> 'scripts/bench.sh'; './bench' -> './bench';
    interpreter flags ('python3 -u bench.py') are skipped; inline (-c/-m) ->
    None (no file target). Shell chains (';', '&&', '|') invalidate the
    command (None) — only a single measurable command is gateable.
    """
    # A chained command mixes several programs' outputs; the gate cannot tell
    # which one produced the measured line — refuse to attribute it. Checked
    # on the raw string: shlex glues ';' to the neighbor ('a.py;').
    if re.search(r"(?<![<>])\s*(?:;|\&\&|\|\||\|)\s*\S", cmd):
        return None
    try:
        # posix=False: on Windows the interpreter arrives as an absolute path
        # (C:\...\python.exe). POSIX mode eats the backslashes as escapes and
        # glues the whole path into one token ('C:Users...python'), so the
        # interpreter is never recognized and the free-zone check sees the
        # INTERPRETER as the target — every refusal is silently bypassed
        # (bench_gate_lifecycle B5 caught this). posix=False keeps backslashes
        # literal; forward-slash normalization below still applies.
        toks = shlex.split(cmd, posix=False)
    except ValueError:
        return None
    toks = [t.strip('"').strip("'") for t in toks]
    if not toks:
        return None
    i = 0
    base0 = toks[0].replace("\\", "/").split("/")[-1].lower()
    for suf in (".exe", ".sh", ".cmd", ".bat"):
        if base0.endswith(suf):
            base0 = base0[: -len(suf)]
            break
    if base0 in _KNOWN_INTERP:
        i = 1
        # Skip valueless flags ('-u') and flags with values ('-W ignore').
        # -m/-c ALWAYS switch to inline mode (a following token is a module
        # name or code string, never a script path).
        while i < len(toks) and toks[i].startswith("-") and toks[i] not in ("-", "--"):
            if toks[i] in ("-m", "-c"):
                return None  # inline — dosya hedefi yok
            if toks[i] in _INTERP_VALUELESS:
                i += 1
            elif "=" in toks[i]:
                i += 1
            else:
                # Unknown flag shape: may take a value — skip both only when
                # the next token doesn't look like a script.
                if i + 1 < len(toks) and not toks[i + 1].startswith("-") \
                        and "/" not in toks[i + 1] and not toks[i + 1].endswith(".py"):
                    i += 2
                else:
                    i += 1
            if i < len(toks) and toks[i] in ("-c", "-m"):
                return None
        if i >= len(toks) or toks[i].startswith("-"):
            return None
    return toks[i]


def bench_in_free_zone(cmd: str) -> bool:
    """True if the run command executes a script inside a free zone.

    The free-surface set mirrors the guard (hooks/engine/modules/config.py —
    FREE_PREFIXES plus the root-level explore_* file doctrine): any bench in
    those surfaces is unattributable to a protected artifact and must be
    refused.

    Unattributable commands (shell chains) count as free-zone: the gate must
    refuse what it cannot attribute.
    """
    bench = _bench_target(cmd)
    if bench is None and any(t in cmd for t in (";", "&&", "||", "|")):
        return True
    if not bench:
        return False
    norm = bench.replace("\\", "/")
    # Strip the guard's norm_path prefixes (./, drive letter) before matching.
    norm = re.sub(r"^[a-zA-Z]:", "", norm)
    while norm.startswith("./"):
        norm = norm[2:]
    if _EXPLORE_FILE.match(norm):
        return True  # guard-free root-level explore_* file
    return bool(_AGENT_BENCH_ZONE.search(norm))


def upsert(lines: list[str], prefix: str, value: str) -> list[str]:
    """Replace the first line starting with prefix, or append it. Returns new lines."""
    for i, l in enumerate(lines):
        if l.strip().startswith(prefix):
            lines[i] = f"{prefix} {value}"
            return lines
    lines.append(f"{prefix} {value}")
    return lines


# --- Blackboard mirror (chain heartbeat) -------------------------------------
# The gate decision lives in the record file; the methodology chain progress
# (E→IR→SP→S→QR→PR) lives on the blackboard. Without this mirror the board
# stays static when an operator runs "gate → APPROVED → code" and skips the
# skill's Stage-6 close-out commands. The mirror is fail-open and best-effort:
# it never changes the gate verdict, never writes on --dry-run, and silently
# skips when no project root can be derived (e.g. temp-dir records in tests).
# Opt out with --no-blackboard or METODOLOJI_NO_BLACKBOARD=1.
_READINESS_SKILL = "bmad-check-implementation-readiness"


def _derive_project_root(record_path: str, explicit: str | None) -> str | None:
    """Project root for the board mirror, or None when it cannot be derived.

    1. Explicit --project-root wins.
    2. A record under <root>/docs/experiments/<id>.md derives <root>.
    3. Otherwise None (no mirror — never guess cwd, so temp-dir records in
       selfcheck/pytest can never pollute a real board).
    """
    if explicit and explicit.strip():
        return os.path.abspath(explicit.strip())
    try:
        parts = pathlib.Path(os.path.abspath(record_path)).parts
        # Last docs/experiments occurrence wins (a parent dir may itself
        # contain a "docs" segment).
        idx = max(
            (i for i in range(len(parts) - 1)
             if parts[i] == "docs" and parts[i + 1] == "experiments"),
            default=None,
        )
        if idx is not None:
            return os.path.join(*parts[:idx]) if parts[:idx] else None
    except (OSError, ValueError):
        pass
    return None


def _record_ref(project_root: str | None, record_path: str | None,
                did: str) -> str:
    """The path a mirror note may safely name — a file that actually exists.

    The note used to spell `docs/experiments/<id>.md`, but the record file
    carries a title-derived name (`E-056-gate-heading-guard.md`), so the hand-off
    pointed the receiving skill at a file that does not exist. Prefer the record's
    real path relative to the project root, else its bare name, and only fall back
    to the id-derived spelling when no record path is known at all.
    """
    if record_path:
        # Normalize BOTH sides through realpath: on macOS /tmp is a symlink to
        # /private/tmp, so abspath(record) vs resolve(root) disagree and the
        # relative path falls back to the bare name (selfcheck _record_ref
        # assert on E-056). realpath resolves symlinks on both sides.
        p = pathlib.Path(os.path.realpath(record_path))
        if project_root:
            try:
                return p.relative_to(
                    pathlib.Path(os.path.realpath(project_root))).as_posix()
            except (OSError, ValueError):
                pass
        return p.name
    return f"docs/experiments/{did}.md"


def _mirror_board(project_root: str | None, did: str, passed: bool,
                  summary: str, token: str = "",
                  record_path: str | None = None) -> None:
    """Best-effort board heartbeat after a real gate decision (fail-open).

    Delegates to the central mirror (`hooks/engine/modules/mirror.py`):
    PASS → run-key heartbeat + hand-off to the readiness skill; FAIL →
    heartbeat only (an unapproved experiment cannot unlock code).
    """
    if not project_root:
        return
    if os.environ.get("METODOLOJI_NO_BLACKBOARD", "").strip() == "1":
        return
    try:
        plugin_root = pathlib.Path(__file__).resolve().parent.parent.parent.parent
        engine_dir = str(plugin_root / "hooks" / "engine")
        if engine_dir not in sys.path:
            sys.path.insert(0, engine_dir)
        from modules import mirror as mir
        if passed:
            value = (f"APPROVED: {summary} ({token} verified)"
                     if token else f"APPROVED: {summary}")
            mir.mirror(project_root, did, value,
                       to=_READINESS_SKILL,
                       note=(f"Experiment {did} APPROVED — see "
                             f"{_record_ref(project_root, record_path, did)}"))
        else:
            mir.mirror(project_root, did,
                       f"REJECTED: {summary} — return to theory")
    except Exception as exc:
        print(f"blackboard mirror skipped ({exc})", file=sys.stderr)


def amend_plan_cli(record_path: str, files: list[str], reason: str) -> int:
    """--amend-plan: append a chained, HMAC-signed amendment to the record's plan.

    E-065: multi-file operations discover files mid-implementation; the plan
    grows WITHOUT re-measurement, but every growth is mechanically chained
    (sha256 over prev hash + files + reason) and signed with the gate key
    (AMEND-OK token). Decided records refuse amendment — the plan is final at
    the moment the token binds to it. Tampering with an entry breaks the chain
    (plan.amendment_chain_issue) exactly as editing Gate Evidence breaks the
    decision token.
    """
    if _plan is None:
        print("ERROR: plan module unavailable (hooks/engine missing) — cannot amend.",
              file=sys.stderr)
        return 2
    if not files:
        print("ERROR: --amend-plan requires --files (comma/space separated paths).",
              file=sys.stderr)
        return 2
    if not reason or not reason.strip():
        print("ERROR: --amend-plan requires --reason (why this file joined the plan).",
              file=sys.stderr)
        return 2
    try:
        text = open(record_path, encoding="utf-8").read()
    except (OSError, UnicodeDecodeError) as exc:
        print(f"ERROR: cannot read record: {record_path}: {exc}", file=sys.stderr)
        return 2
    fields = record_fields(text)
    decision = fields.get("Decision", "").strip()
    if decision and not _is_placeholder(decision):
        print(f"ERROR: record already decided ('{decision[:60]}') — its plan is frozen. "
              "Open a new experiment record for the additional files.", file=sys.stderr)
        return 2
    id_issue = experiment_id_issue(text)
    if id_issue:
        print(f"ERROR: {id_issue}", file=sys.stderr)
        return 2
    secret = require_secret()
    existing = _plan.parse_plan_fields(text)["planned_files"]
    norm_files = [f.replace("\\", "/").lstrip("./") for f in files]
    new_files = [f for f in norm_files if f not in existing]
    if not new_files:
        print("No-op: every file is already in the plan.")
        return 0
    seq = len(_plan.parse_amendments(text)) + 1
    new_text, _chain = _plan.append_amendment(
        text, seq, _date.today().isoformat(), new_files,
        reason.strip(), secret)
    new_text = _plan.bump_planned_files(new_text, new_files)
    open(record_path, "w", encoding="utf-8").write(new_text)
    print(f"Amendment AMEND-{seq} appended: +{len(new_files)} file(s) "
          f"({', '.join(new_files)}); reason: {reason.strip()}")
    print(f"Plan is now {len(existing) + len(new_files)} file(s) wide. "
          "The final --run token binds to this plan hash.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Record-bound mechanical approval gate.")
    ap.add_argument("--record", help="path to the experiment record .md")
    ap.add_argument("--run", help="measurement command to execute; the gate parses the "
                                  "measured value from its output (only mechanical path)")
    ap.add_argument("--raw", default="", help="extra raw-result note (optional)")
    ap.add_argument("--dry-run", action="store_true",
                    help="preview the decision (measured vs threshold, Wilson bound, "
                         "would-be GATE-OK token) WITHOUT writing to the record")
    ap.add_argument("--verify", action="store_true", help="verify an existing decision")
    ap.add_argument("--init-secret", action="store_true",
                    help="generate the gate key (HMAC secret) outside the repo, once per machine")
    ap.add_argument("--import-key", action="store_true",
                    help="import a peer machine's gate key into this machine's trust ring "
                         "(key material via the BMAD_PEER_GATE_KEY env var, label via "
                         "BMAD_PEER_LABEL) so records approved on that machine verify here")
    ap.add_argument("--check-secret", action="store_true",
                    help="report whether the gate key is configured")
    ap.add_argument("--validate", metavar="PATH",
                    help="validate a Mod B/C/D (belgesel) record for completeness/honesty")
    ap.add_argument("--project-root", default=None,
                    help="project root for the blackboard mirror "
                         "(default: derived from --record under docs/experiments/)")
    ap.add_argument("--no-blackboard", action="store_true",
                    help="skip the blackboard mirror (record is still decided)")
    ap.add_argument("--amend-plan", metavar="RECORD",
                    help="append a chained amendment to the record's Implementation Plan "
                         "(E-065): plan-external files join the declared plan without "
                         "re-measurement, each entry HMAC-signed with the gate key")
    ap.add_argument("--files", default="",
                    help="comma/space separated file paths for --amend-plan")
    ap.add_argument("--reason", default="",
                    help="why these files joined the plan (stored verbatim in the entry)")
    args = ap.parse_args()

    if args.init_secret:
        return init_secret()
    if args.import_key:
        return import_key()
    if args.check_secret:
        return check_secret()
    if args.validate:
        return validate_doc(args.validate)
    if args.amend_plan:
        files = [f for f in re.split(r"[,\s]+", args.files.strip()) if f]
        return amend_plan_cli(args.amend_plan, files, args.reason)
    if args.verify:
        if not args.record:
            ap.error("--verify requires --record")
        return verify(args.record)
    if not args.record:
        ap.error("need --record")
    if not args.run:
        ap.error("need --run <measurement command> — the gate runs the measurement itself; "
                 "operator-declared values are not accepted")

    try:
        with open(args.record, encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        # rc 1 collides with REJECTED; an unreadable record is a PRE-MEASUREMENT
        # config error (bench_gate_lifecycle A1e caught the raw traceback). A
        # non-UTF-8 record is the same class (E-016): refuse, never crash.
        print(f"ERROR: cannot read record: {args.record}: {exc}", file=sys.stderr)
        return 2
    fields = record_fields(text)
    for need in REQUIRED_DRAFT:
        if not fields.get(need):
            hint = _missing_field_hint(text, need)
            print(f"ERROR: record missing '{need}' — finish the design draft before measuring.{hint}",
                  file=sys.stderr)
            return 2

    # Rule: the record's Experiment id must name a run BEFORE anything is
    # measured (see experiment_id_issue): the token is bound to that id, so a
    # heading the id parser cannot read must never reach a decision.
    id_issue = experiment_id_issue(text)
    if id_issue:
        print(f"ERROR: {id_issue}", file=sys.stderr)
        return 2

    did = deney_id(text)
    hid, claim = hypothesis_claim(fields.get("Hypothesis", ""))

    # Rule: measurement script cannot live in a free zone (scratch/tmp/temp and
    # the other guard-free surfaces: graft/, openhands/, _bmad/, .metodoloji/,
    # explore_*). The gate requires the measurement itself to be under
    # methodology protection; otherwise the agent could produce approval with
    # a fabricated free-zone bench. (Reject before execution.)
    if bench_in_free_zone(args.run):
        print("ERROR: measurement script cannot run in a free zone (scratch/tmp/temp "
              "or any guard-free surface: graft/, openhands/, _bmad/, .metodoloji/, "
              "root-level explore_* files) — "
              "the gate requires the measurement itself to be under methodology protection. "
              "Move the measurement script to a protected directory (e.g. scripts/bench/); "
              "every bench change goes through the approval gate.", file=sys.stderr)
        return 2

    # Template placeholder (angle bracket '<...>' or em-dash '— (gate writes)')
    # is NOT a decision — the gate hasn't written yet. Only a real APPROVED/REJECTED
    # value counts as "decided"; otherwise a template copy would be rejected as
    # "already decided" (record-format trap).
    decision_val = fields.get("Decision", "").strip()
    if decision_val and not _is_placeholder(decision_val):
        print(f"ERROR: record already decided ('{decision_val[:60]}'). "
              "Open a new experiment record for a new measurement.", file=sys.stderr)
        return 2

    # Token (GATE-OK) is produced with HMAC-SHA256(key, ...) — key is outside repo.
    # Decision is only written when the key is configured (forged records blocked).
    try:
        secret = require_secret()
    except GateError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    # Measurement: the gate runs the command ITSELF and parses the value from output.
    # Operator declares no numbers — reality is mechanical (manifesto: --run is canonical).
    # The claimed metric name steers which metric_* line binds the decision
    # (E-004): a multi-metric bench must not have its first line decide.
    claim_metric = claim_metric_name(claim)
    try:
        val, x, y, run_metric = run_and_measure(args.run, claim_metric=claim_metric)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        threshold, op = parse_claim(claim)
        passed, summary = evaluate(claim, val)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    # Metric-name cross-check (Rule 1): the measured metric name (parsed from output)
    # is compared against the metric name in the hypothesis claim.
    measured_metric = run_metric
    metric_mismatch = measured_metric is not None and measured_metric != claim_metric
    if metric_mismatch:
        fate = "would write warning into record (dry-run)" if args.dry_run \
            else "writing warning into record"
        print(f"WARNING: run measured '{measured_metric}', claim names '{claim_metric}'. "
              f"Metric redefinition — {fate}.", file=sys.stderr)

    # Sample size: in --run mode the denominator (x/y) is parsed by the gate and
    # is mandatory; 'n unknown' is not a decision path (ADVISORY-BLOCK bypass closed).
    n = y
    if x is None:
        x = int(round(n * val))  # n exists but x missing: derive from value (for Wilson)

    belirsizlik = uncertainty_note(x, n, val, threshold, op)

    # Metric cross-check note: 'uyumlu' when the measured metric equals the claimed one,
    # 'UYUMSUZ' on a redefinition (rule-1 spirit, advisory).
    if measured_metric is not None:
        if not metric_mismatch:
            metric_note = f"consistent (measured {measured_metric} == claimed {claim_metric})"
        else:
            metric_note = (f"MISMATCH — measured {measured_metric}, claimed {claim_metric}: "
                           "different metric measured (metric redefinition)")
    else:
        metric_note = "n/a"

    # Dry-run: preview the full decision but write NOTHING. This is the only safe
    # way to check a record/format without deciding it — a gate run without --dry-run
    # writes a REAL decision (E-189 lesson).
    if args.dry_run:
        # Mirror the exact lines a real run would write so the operator sees
        # precisely what will land in the record.
        _dry_plan_hash = _plan.final_plan_hash(text) if _plan is not None else None
        print(f"[DRY-RUN] {hid}: {summary} -> {'PASS' if passed else 'FAIL'}")
        print(f"[DRY-RUN]   Uncertainty will be written: {belirsizlik}")
        print(f"[DRY-RUN]   Metric will be written: {metric_note}")
        print(f"[DRY-RUN]   Measurement command will be written: {args.run}")
        if _dry_plan_hash is not None:
            print(f"[DRY-RUN]   Final plan hash will bind the token: {_dry_plan_hash[:16]}…")
        if passed:
            tok = gate_token(claim, val, did, secret, args.run)
            print(f"[DRY-RUN]   Decision will be: APPROVED — {hid}: {summary}")
            print(f'[DRY-RUN]   Gate evidence will be: measured={val} claim="{claim}" {tok}')
            print("[DRY-RUN]   Next Step will be: Proceed to Code; "
                  "Status will be: completed")
        else:
            print(f"[DRY-RUN]   Decision will be: REJECTED — {hid}: {summary} (gate FAIL)")
            print("[DRY-RUN]   Next Step will be: Return to Theory; open new experiment "
                  "for new hypothesis; Status will be: REJECTED")
        print(f"[DRY-RUN] NO CHANGES WRITTEN — {args.record} untouched.")
        return 0

    lines = text.splitlines(keepends=False)
    raw_val = f"measured={val}" + (f"; n={n}" if n is not None else "") \
              + (f"; {args.raw}" if args.raw else "")
    lines = upsert(lines, "- **Raw Results:**", raw_val)
    lines = upsert(lines, "- **Uncertainty:**", belirsizlik)
    lines = upsert(lines, "- **Metric:**", metric_note)
    # Rule: measurement command is written to record; new-style token binds to this command,
    # so changing the command after approval breaks the token (verify rejects).
    lines = upsert(lines, "- **Measurement Command:**", args.run)
    # E-065: the final token binds to the FINAL plan hash (post-amendments).
    # The line is materialized so the binding is inspectable; --verify
    # re-derives it from the record body and refuses a doctored one.
    plan_hash = _plan.final_plan_hash(text) if _plan is not None else None

    if passed:
        tok = gate_token(claim, val, did, secret, args.run, fields.get("Code Scope", ""),
                         plan_hash)
        lines = upsert(lines, "- **Decision:**", f"APPROVED — {hid}: {summary}")
        lines = upsert(lines, "- **Gate Evidence:**", f'measured={val} claim="{claim}" {tok}')
        lines = upsert(lines, "- **Next Step:**", "Proceed to Code")
        lines = upsert(lines, "- **Status:**", "completed")
        if plan_hash is not None:
            lines = upsert(lines, _plan.FINAL_PLAN_FIELD, plan_hash)
        open(args.record, "w", encoding="utf-8").write("\n".join(lines) + "\n")
        print(f"[{hid}] {summary} -> PASS")
        print(f"DECISION: APPROVED  token={tok}")
        print(f"Record updated: {args.record}")
        if not args.no_blackboard:
            _mirror_board(_derive_project_root(args.record, args.project_root),
                          did, True, summary, tok, record_path=args.record)
        return 0

    lines = upsert(lines, "- **Decision:**", f"REJECTED — {hid}: {summary} (gate FAIL)")
    lines = upsert(lines, "- **Next Step:**", "Return to Theory; open new experiment for new hypothesis")
    lines = upsert(lines, "- **Status:**", "REJECTED")
    open(args.record, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print(f"[{hid}] {summary} -> FAIL")
    print("DECISION: REJECTED")
    print(f"Record updated: {args.record}")
    if not args.no_blackboard:
        _mirror_board(_derive_project_root(args.record, args.project_root),
                      did, False, summary)
    return 1


def verify(path: str) -> int:
    """Return 0 if the record's APPROVED is backed by a genuine gate token, else:
    1 = FORGED / undecided / rejected, 2 = ADVISORY-BLOCK (genuine but no code),
    3 = gate key missing (cannot verify).

    Multi-machine: the token is checked against the developer's trust ring —
    this machine's own key plus every peer key in ~/.bmad/gate-keys/. Signing
    still uses the machine's own key only.
    """
    try:
        secrets = require_any_secret()
    except GateError as exc:
        print(f"SECRET-MISSING: {path} — {exc}", file=sys.stderr)
        return 3
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        # rc 1 collides with FORGED/undecided; an unreadable record is a config
        # error, not a provenance verdict (bench_gate_lifecycle A1e parity). A
        # non-UTF-8 record is the same class (E-016): refuse, never crash.
        print(f"ERROR: cannot read record: {path}: {exc}", file=sys.stderr)
        return 2
    fields = record_fields(text)
    decision = fields.get("Decision", "").strip()
    evidence = fields.get("Gate Evidence", "")

    # Template placeholder (angle bracket '<...>' or em-dash '— (gate writes)') is
    # not a decision — even if it contains 'APPROVED' (as in
    # `<gate writes: APPROVED | REJECTED — reason>`) it must NOT get a FORGED stamp;
    # the gate hasn't written yet, so it counts as "undecided".
    if _is_placeholder(decision):
        decision = ""

    if "APPROVED" in decision:
        m = re.search(r'measured=(-?[\d.]+)\s+claim="([^"]+)"\s+(GATE-OK-[\w\-]+)', evidence)
        if not m:
            print(f"FORGED: {path} says APPROVED but has no valid gate evidence.")
            return 1
        try:
            measured = float(m.group(1))
        except ValueError:
            print(f"FORGED: {path} gate evidence measured value is not a number.")
            return 1
        claim, tok = m.group(2), m.group(3)
        # Cross-check: the hypothesis claim recorded in the Hypothesis field must match
        # the claim the gate actually evaluated. Editing the threshold after approval
        # (then keeping the token) is a forged outcome. Whitespace is
        # canonicalized first — formatting alone must not forge a record.
        try:
            _, recorded_claim = hypothesis_claim(fields.get("Hypothesis", ""))
        except ValueError as exc:
            print(f"FORGED: record Hypothesis cannot be parsed ({exc}).")
            return 1
        if re.sub(r"\s+", " ", recorded_claim.strip()) != re.sub(r"\s+", " ", claim.strip()):
            print(f"FORGED: recorded Hypothesis claim '{recorded_claim}' != gate claim '{claim}'.")
            return 1
        # Rule: token binds to 'Measurement Command' + 'Code Scope' in the
        # record (new-style). Records WITH the command field accept the
        # scope-bound token; a cmd-only token from before scope binding is
        # grandfathered (migration path) — but changing the command OR
        # widening the scope after approval breaks the token (FORGED).
        # Records WITHOUT the command field are pre-rule records verified
        # with the legacy token.
        cmd_field = fields.get("Measurement Command", "").strip()
        scope_field = fields.get("Code Scope", "").strip()
        did = deney_id(text)
        # E-065: re-derive the final plan hash from the record BODY and refuse
        # a doctored Final Plan Hash line. The hash covers the post-amendment
        # planned set + amendment chain: stripping or reordering amendments
        # after approval changes the re-derived hash and breaks the token
        # (FORGED), while --amend-plan before approval is the legitimate path.
        plan_hash_now = _plan.final_plan_hash(text) if _plan is not None else None
        recorded_plan_hash = fields.get("Final Plan Hash", "").strip()
        if plan_hash_now is not None and recorded_plan_hash and plan_hash_now != recorded_plan_hash:
            print(f"FORGED: recorded Final Plan Hash {recorded_plan_hash[:16]}… != "
                  f"re-derived {plan_hash_now[:16]}… — the plan (amendments) changed "
                  f"after approval.")
            return 1
        # Trust ring: the token is genuine if it re-derives under ANY key the
        # developer trusts (own machine + imported peers). Signing stays
        # single-key; verification is ring-wide — the multi-machine contract.
        # Plan binding (E-065): a token signed WITH the plan hash only verifies
        # when the re-derived hash matches; a pre-plan token (plan_hash=None at
        # signing) keeps verifying WITHOUT the plan input — legacy acceptance is
        # one-way, so the never-wider rule holds (a plan-bound token cannot
        # verify as legacy, but a legacy token is not forgeable into a plan
        # context: new approvals ALWAYS carry the plan binding).
        new_tok = any(gate_token(claim, measured, did, s, cmd_field, scope_field,
                                 plan_hash_now if _plan is not None else None) == tok
                      for s in secrets) if cmd_field else False
        pre_plan_tok = any(gate_token(claim, measured, did, s, cmd_field, scope_field) == tok
                           for s in secrets) if cmd_field else False
        cmd_only_tok = any(gate_token(claim, measured, did, s, cmd_field) == tok
                           for s in secrets) if cmd_field else False
        legacy_ok = any(gate_token(claim, measured, did, s) == tok for s in secrets)
        if not new_tok and pre_plan_tok and not recorded_plan_hash:
            # Pre-E-065 approval (no Final Plan Hash line): accepted as-is —
            # re-measurement under the current gate re-binds the plan.
            new_tok = True
        if new_tok:
            ok = True
        elif cmd_field and cmd_only_tok:
            ok = True  # pre-scope-binding approval — re-measure to bind scope
        elif cmd_field:
            ok = False  # field present but doesn't match new-style token => forged/downgrade
        else:
            ok = legacy_ok
        if ok:
            # The token is genuine, but rule 4 (sample size) and rule 1 (metric
            # identity) are mechanically enforced here: a record that confesses a
            # small sample, an unknown sample ('n unknown'), or a metric mismatch
            # does NOT unlock code.
            uncertainty = fields.get("Uncertainty", "").strip()
            metric = fields.get("Metric", "").strip()
            blocks = []
            # 'n unknown' BLOCKS: sample denominator is parsed by gate (--run);
            # removing n after approval invalidates approval. For pre-rule records
            # the safe behavior is to not unlock code.
            if "small sample" in uncertainty or "n unknown" in uncertainty:
                blocks.append(f"sample uncertainty ({uncertainty})")
            if metric and metric.startswith("MISMATCH"):
                blocks.append(f"metric mismatch ({metric})")
            if blocks:
                print(f"ADVISORY-BLOCK: {path} — APPROVED genuine (token {tok}), "
                      f"but {'; '.join(blocks)}. Fix the experiment before code.")
                return 2
            print(f"VERIFIED: {path} — APPROVED genuine (token {tok}). Code may proceed.")
            return 0
        # Everything above parsed and matched EXCEPT the HMAC itself, so this
        # is either tampering or — just as likely in a multi-machine setup — a
        # record approved under a key outside this machine's trust ring. Keys
        # outside the repo mean the token cannot be re-derived here, so
        # --verify reports unknown provenance as FORGED by design. Distinguish
        # the two for the operator: importing the approving machine's key
        # (--import-key, BMAD_PEER_GATE_KEY env var) makes the record verify
        # without any re-measurement; the Re-Measured-By marker remains the
        # key-free remedy (check-plugin.sh §3).
        print(
            f"FORGED: token {tok} does not match the record's claim/measured/cmd/plan "
            f"under any of this machine's trusted keys ({len(secrets)} in the ring). "
            f"If this record was APPROVED on another machine of yours, import that "
            f"machine's key: --import-key (key via BMAD_PEER_GATE_KEY, label via "
            f"BMAD_PEER_LABEL) — the record then verifies here without re-measurement. "
            f"Otherwise re-measure it under this machine's key with a 'Re-Measured-By: "
            f"E-XXX' marker. If it was approved here, the record was tampered with."
        )
        return 1
    if "REJECTED" in decision:
        print(f"{path} — REJECTED (no code). Reason: {decision}")
        return 1
    print(f"{path} — undecided (no Decision). Run the gate with --record and --run.")
    return 1


def _selfcheck() -> None:
    import os
    import tempfile

    # Selfcheck uses a fixed test key (via env var; doesn't touch file path).
    # Restored at the end.
    _old_env = os.environ.get(SECRET_ENV)
    os.environ[SECRET_ENV] = "selfcheck-test-key"

    assert evaluate("accuracy >= 0.90", 0.93)[0]
    assert not evaluate("accuracy >= 0.90", 0.87)[0]

    # Sample-size helpers.
    assert wilson_lower(35, 35) >= 0.90   # n=35 perfect at >=0.90 clears
    assert wilson_lower(4, 4) < 0.90      # n=4 perfect at >=0.90 does NOT clear
    assert wilson_lower(0, 0) == 0.0      # n<=0 -> 0.0
    m = MEASURED_RE.search("metric_accuracy=0.93 (14/15)")
    assert (m.group(2), m.group(3), m.group(4)) == ("0.93", "14", "15")
    m = MEASURED_RE.search("metric_score=0.80")
    assert (m.group(2), m.group(3), m.group(4)) == ("0.80", None, None)

    # 'Code Scope' glob matching: '**' any depth, '*' single segment, '?' single char.
    assert scope_matches("src/**", "src/foo.py")
    assert scope_matches("src/**", "src/engine/foo.py")
    assert not scope_matches("src/**", "tests/foo.py")
    assert scope_matches("src/engine/**", "src/engine/core/x.py")
    assert scope_matches("src/*.py", "src/foo.py")
    assert not scope_matches("src/*.py", "src/foo/bar.py")
    assert scope_matches("src/**", "src\\engine\\foo.py")   # Windows separators normalized
    assert scope_matches("lib/**,tools/*", "tools/build.py")
    assert scope_matches("src/engine/**", "src/engine/core/x.py")
    assert not scope_matches("", "src/foo.py")
    assert scope_matches("src/**", "src/foo.py")
    assert glob_to_regex("**").endswith(".*$")

    # Rule: free-zone bench is rejected; protected bench is allowed;
    # token binds to measurement command (cmd param) — command change breaks token.
    assert bench_in_free_zone("python3 scratch/bench.py") is True
    assert bench_in_free_zone("python3 tmp/bench.py") is True
    assert bench_in_free_zone("python3 graft/bench.py") is True
    assert bench_in_free_zone("python3 openhands/bench.py") is True
    assert bench_in_free_zone("python3 _bmad/bench.py") is True
    assert bench_in_free_zone("python3 .metodoloji/bench.py") is True
    assert bench_in_free_zone("python3 explore_probe.py") is True
    assert bench_in_free_zone("python3 ./explore_probe.py") is True  # guard norms ./ away
    assert bench_in_free_zone("python3 explore_dir/bench.py") is False  # nested: guard gates it
    assert bench_in_free_zone("python3 sub/explore_probe.py") is False  # not root-level
    assert bench_in_free_zone("sh scripts/bench.sh") is False
    assert bench_in_free_zone("python3 src/bench.py data.csv") is False
    assert bench_in_free_zone("python3 -c 'print(1)'") is False
    assert bench_in_free_zone(f'"{sys.executable}" "scripts/fake_bench.py"') is False
    assert _bench_target("python3 src/bench.py data.csv") == "src/bench.py"
    assert _bench_target("sh scripts/bench.sh") == "scripts/bench.sh"
    assert _bench_target("python3 -m timeit 'x'") is None
    _tok_a = gate_token("a >= 0.9", 1.0, "E-X", b"k")
    _tok_b = gate_token("a >= 0.9", 1.0, "E-X", b"k", "cmd")
    _tok_c = gate_token("a >= 0.9", 1.0, "E-X", b"k", "cmd2")
    assert _tok_a != _tok_b and _tok_b != _tok_c

    # Rule: the record's Experiment id must NAME a run (the token is bound to it).
    assert experiment_id_issue("## Experiment: E-001 — ok\n") is None
    assert experiment_id_issue("## Experiment: E-001\n") is None
    assert experiment_id_issue("## Experiment: E-056-integration-completion\n") is None
    assert experiment_id_issue("## Experiment: E-2026-09-28\n") is None
    assert experiment_id_issue("# Experiment: E-001 — h1\n") is not None
    assert experiment_id_issue("#Experiment: E-001\n") is not None
    assert experiment_id_issue("**Experiment:** E-001\n") is not None
    assert experiment_id_issue("no heading here\n") is not None
    assert experiment_id_issue("## Experiment:\n") is not None
    assert experiment_id_issue("## Experiment: — em-dash id\n") is not None
    assert experiment_id_issue("## Experiment: E-XYZ\n") is not None
    assert experiment_id_issue("## Experiment: X-001\n") is not None

    # Rule: the E→IR mirror note names a file that exists. The record carries a
    # title-derived name, so the id-derived spelling (`docs/experiments/E-056.md`)
    # sent the receiving skill to a file that was never created.
    assert _record_ref("/tmp/proj",
                       "/tmp/proj/docs/experiments/E-056-gate-id-guard.md",
                       "E-056") == "docs/experiments/E-056-gate-id-guard.md"
    assert _record_ref(None, "/tmp/proj/docs/experiments/E-001.md",
                       "E-001") == "E-001.md"
    assert _record_ref("/tmp/proj", None, "E-001") == "docs/experiments/E-001.md"

    # Rule: benches live temporarily under the gate's OWN directory —
    # if they live in OS temp (/tmp, %TEMP%) the free-zone rule produces false
    # positives on Linux; the temp directory is deleted when selfcheck ends.
    with tempfile.TemporaryDirectory(dir=os.path.dirname(os.path.abspath(__file__)),
                                     prefix="meth-selfcheck-") as td:
        bench = os.path.join(td, "fake_bench.py")
        with open(bench, "w", encoding="utf-8") as fh:
            fh.write('print("fake_accuracy=1.00 (40/40)")\n')
        rec = os.path.join(td, "E-001.md")
        with open(rec, "w", encoding="utf-8") as fh:
            fh.write(
                "## Experiment: E-001 — selfcheck\n"
                "- **Status:** planned\n"
                "- **Theory:** test theory\n"
                '- **Hypothesis:** H-001: "fake_accuracy >= 0.90"\n'
                "- **Measurement Metrics:** fake_accuracy >= 0.90\n"
                "- **Experiment Design:** unit test\n"
                "- **Code Scope:** none\n"
            )
        # Rule: an unreadable Experiment id is refused BEFORE the measurement runs
        # (trap bench would leave a marker), and the record stays untouched.
        trap_marker = os.path.join(td, "trap-ran.txt")
        trap = os.path.join(td, "trap_bench.py")
        with open(trap, "w", encoding="utf-8") as fh:
            fh.write("import os, pathlib\n"
                     "pathlib.Path(os.environ['BB_TRAP_MARKER']).write_text('ran')\n"
                     "print('fake_accuracy=1.00 (40/40)')\n")
        rec_h1 = os.path.join(td, "E-001-h1.md")
        with open(rec_h1, "w", encoding="utf-8") as fh:
            fh.write(
                "# Experiment: E-001 — h1 heading (the real-session trap)\n"
                "- **Status:** planned\n"
                "- **Theory:** test theory\n"
                '- **Hypothesis:** H-001: "fake_accuracy >= 0.90"\n'
                "- **Measurement Metrics:** fake_accuracy >= 0.90\n"
                "- **Experiment Design:** unit test\n"
                "- **Code Scope:** none\n"
            )
        import contextlib, io as _io
        os.environ["BB_TRAP_MARKER"] = trap_marker
        before_h1 = open(rec_h1, encoding="utf-8").read()
        _out_h1 = _io.StringIO()
        sys.argv = ["run_experiment.py", "--record", rec_h1,
                    "--run", f'"{sys.executable}" "{trap}"']
        with contextlib.redirect_stderr(_out_h1):
            assert main() == 2                       # refused
        assert not os.path.exists(trap_marker)       # measurement never ran
        assert open(rec_h1, encoding="utf-8").read() == before_h1
        assert "## Experiment: E-<id>" in _out_h1.getvalue()
        os.environ.pop("BB_TRAP_MARKER", None)
        old = sys.argv
        sys.argv = ["run_experiment.py", "--record", rec,
                    "--run", f'"{sys.executable}" "{bench}"']
        assert main() == 0
        assert verify(rec) == 0  # genuine approval verifies
        forged = os.path.join(td, "E-001-forged.md")
        text = open(rec, encoding="utf-8").read()
        stripped = "\n".join(l for l in text.splitlines()
                             if "- **Gate Evidence:**" not in l)
        with open(forged, "w", encoding="utf-8") as fh:
            fh.write(stripped)
        assert verify(forged) == 1  # APPROVED without token is forged
        # Threshold tamper after approval: edit the Hypothesis claim, keep the token.
        tampered = os.path.join(td, "E-001-tampered.md")
        swapped = text.replace('H-001: "fake_accuracy >= 0.90"',
                               'H-001: "fake_accuracy >= 0.99"')
        with open(tampered, "w", encoding="utf-8") as fh:
            fh.write(swapped)
        assert verify(tampered) == 1  # edited Hypothesis != gate claim is forged
        # Wrong secret: HMAC token cannot be reproduced without the key -> FORGED.
        wrongkey = os.path.join(td, "E-001-wrongkey.md")
        tok_re = re.search(r"(GATE-OK-[\w\-]+)", text)
        assert tok_re
        fake_tok = gate_token("fake_accuracy >= 0.90", 1.0, "E-001", b"wrong-secret-key")
        with open(wrongkey, "w", encoding="utf-8") as fh:
            fh.write(text.replace(tok_re.group(1), fake_tok))
        assert verify(wrongkey) == 1  # wrong-key token -> FORGED
        # Rule: changing 'Measurement Command' after approval breaks token (FORGED);
        # deleting the field and downgrading to legacy token stays FORGED (downgrade closed).
        cmd_line = re.search(r"- \*\*Measurement Command:\*\* .*", text)
        assert cmd_line
        cmd_tamper = os.path.join(td, "E-001-cmdtamper.md")
        with open(cmd_tamper, "w", encoding="utf-8") as fh:
            fh.write(text.replace(cmd_line.group(0), "- **Measurement Command:** python3 other_bench.py"))
        assert verify(cmd_tamper) == 1
        # Rule (secretless downgrade): deleting the field and keeping the new-style
        # token is also FORGED — command-bound token cannot be verified as legacy in
        # a field-less record.
        strip = os.path.join(td, "E-001-strip.md")
        with open(strip, "w", encoding="utf-8") as fh:
            fh.write("\n".join(l for l in text.splitlines()
                               if not l.startswith("- **Measurement Command:**")) + "\n")
        assert verify(strip) == 1

        # --run mode: the gate parses the value from the measurement command's output.
        rec2 = os.path.join(td, "E-001-run.md")
        with open(rec2, "w", encoding="utf-8") as fh:
            fh.write(
                "## Experiment: E-001 — selfcheck run\n"
                "- **Status:** planned\n"
                "- **Theory:** test theory\n"
                '- **Hypothesis:** H-001: "fake_accuracy >= 0.90"\n'
                "- **Measurement Metrics:** fake_accuracy >= 0.90\n"
                "- **Experiment Design:** unit test\n"
                "- **Code Scope:** none\n"
            )
        sys.argv = ["run_experiment.py", "--record", rec2,
                    "--run", f'"{sys.executable}" "{bench}"']
        assert main() == 0  # parsed 1.0 >= 0.90 -> PASS
        assert verify(rec2) == 0
        # A script that prints no measured value must fail the gate without touching the record.
        rec3 = os.path.join(td, "E-001-novalue.md")
        with open(rec3, "w", encoding="utf-8") as fh:
            fh.write(
                "## Experiment: E-001 — selfcheck novalue\n"
                "- **Status:** planned\n"
                "- **Theory:** test theory\n"
                '- **Hypothesis:** H-001: "accuracy >= 0.90"\n'
                "- **Measurement Metrics:** accuracy >= 0.90\n"
                "- **Experiment Design:** unit test\n"
                "- **Code Scope:** none\n"
            )
        with open(bench, "w", encoding="utf-8") as fh:
            fh.write('print("hello")\n')
        before = open(rec3, encoding="utf-8").read()
        sys.argv = ["run_experiment.py", "--record", rec3,
                    "--run", f'"{sys.executable}" "{bench}"']
        assert main() == 2  # no measured value -> gate refuses
        assert open(rec3, encoding="utf-8").read() == before  # record untouched

        # Sample-size: small-n warns but still PASSes.
        with open(bench, "w", encoding="utf-8") as fh:
            fh.write('print("fake_accuracy=0.93 (14/15)")\n')
        rec4 = os.path.join(td, "E-001-smalln.md")
        with open(rec4, "w", encoding="utf-8") as fh:
            fh.write(
                "## Experiment: E-001 — selfcheck small-n\n"
                "- **Status:** planned\n"
                "- **Theory:** test theory\n"
                '- **Hypothesis:** H-001: "accuracy >= 0.90"\n'
                "- **Measurement Metrics:** accuracy >= 0.90\n"
                "- **Experiment Design:** unit test\n"
                "- **Code Scope:** none\n"
            )
        sys.argv = ["run_experiment.py", "--record", rec4,
                    "--run", f'"{sys.executable}" "{bench}"']
        assert main() == 0  # 0.93 >= 0.90 still PASSes
        assert "Uncertainty:** n=15" in open(rec4, encoding="utf-8").read()
        assert verify(rec4) == 2  # small-n blocks code

        # Sample-size: sufficient-n writes 'yok' (affirmative no-warning).
        with open(bench, "w", encoding="utf-8") as fh:
            fh.write('print("fake_accuracy=1.00 (40/40)")\n')
        rec5 = os.path.join(td, "E-001-suff.md")
        with open(rec5, "w", encoding="utf-8") as fh:
            fh.write(
                "## Experiment: E-001 — selfcheck sufficient-n\n"
                "- **Status:** planned\n"
                "- **Theory:** test theory\n"
                '- **Hypothesis:** H-001: "accuracy >= 0.90"\n'
                "- **Measurement Metrics:** accuracy >= 0.90\n"
                "- **Experiment Design:** unit test\n"
                "- **Code Scope:** none\n"
            )
        sys.argv = ["run_experiment.py", "--record", rec5,
                    "--run", f'"{sys.executable}" "{bench}"']
        assert main() == 0
        assert "Uncertainty:** none" in open(rec5, encoding="utf-8").read()

        # --run requires the (x/y) denominator: a value-only bench is rejected so
        # 'n bilinmiyor' cannot bypass ADVISORY-BLOCK.
        with open(bench, "w", encoding="utf-8") as fh:
            fh.write('print("fake_score=0.95")\n')
        rec6 = os.path.join(td, "E-001-nounk.md")
        with open(rec6, "w", encoding="utf-8") as fh:
            fh.write(
                "## Experiment: E-001 — selfcheck value-only\n"
                "- **Status:** planned\n"
                "- **Theory:** test theory\n"
                '- **Hypothesis:** H-001: "accuracy >= 0.90"\n'
                "- **Measurement Metrics:** accuracy >= 0.90\n"
                "- **Experiment Design:** unit test\n"
                "- **Code Scope:** none\n"
            )
        before6 = open(rec6, encoding="utf-8").read()
        # Distinct from rec3 ('hello' prints no value at all): this bench DID print a
        # metric value, so the refusal must be specifically the missing-(x/y) error,
        # not the could-not-parse-value error. Capture exit code + stderr in one run.
        import contextlib, io as _io
        _err = _io.StringIO()
        sys.argv = ["run_experiment.py", "--record", rec6,
                    "--run", f'"{sys.executable}" "{bench}"']
        with contextlib.redirect_stderr(_err):
            _rc6 = main()
        assert _rc6 == 2  # no denominator -> gate refuses
        assert "no sample-size denominator" in _err.getvalue()
        assert open(rec6, encoding="utf-8").read() == before6  # record untouched

        # --measured / --metric / --n REMOVED (security boundary): gate runs measurement
        # itself; operator-declared values don't exist. Rule 1 (metric identity) and
        # Rule 4 (sample) are mechanical on every approval — rec9/rec10 below test with --run.

        # Pre-rule record: 'n unknown' confession + genuine token -> code BLOCKED (rc=2).
        rec_nunk = os.path.join(td, "E-001-nunk.md")
        text5 = open(rec5, encoding="utf-8").read()
        swapped_nunk = text5.replace(
            "- **Uncertainty:** none",
            "- **Uncertainty:** n unknown (sample size could not be parsed)")
        with open(rec_nunk, "w", encoding="utf-8") as fh:
            fh.write(swapped_nunk)
        assert verify(rec_nunk) == 2  # n unknown NOW blocks

        # Mod B (documentary) validation: complete record OK, empty honesty field -> WARNING.
        doc_ok = os.path.join(td, "B-001-ok.md")
        with open(doc_ok, "w", encoding="utf-8") as fh:
            fh.write("\n".join([
                "## Finding: B-001 — selfcheck",
                "- **Date:** 13.08.2026",
                "- **Status:** completed",
                "- **Research Question:** question",
                "- **Context:** context",
                "- **Method:** method",
                "- **Finding:** finding",
                "- **Evidence:** evidence",
                "- **Counter-evidence:** none",
                "- **Interpretation:** interpretation",
                "- **Uncertainty:** small sample",
                "- **Decision:** APPROVED — finding",
                "- **Next Step:** code",
                ""]))
        assert validate_doc(doc_ok) == 0
        doc_bad = os.path.join(td, "B-001-bad.md")
        with open(doc_bad, "w", encoding="utf-8") as fh:
            fh.write("\n".join([
                "## Finding: B-001 — selfcheck bad",
                "- **Date:** 13.08.2026",
                "- **Status:** completed",
                "- **Research Question:** question",
                "- **Context:** context",
                "- **Method:** method",
                "- **Finding:** finding",
                "- **Evidence:** <evidence>",
                "- **Counter-evidence:**",
                "- **Interpretation:** interpretation",
                "- **Uncertainty:**",
                "- **Decision:** APPROVED",
                "- **Next Step:** code",
                ""]))
        assert validate_doc(doc_bad) == 1  # empty honesty fields -> issue
        doc_modA = os.path.join(td, "E-002.md")
        with open(doc_modA, "w", encoding="utf-8") as fh:
            fh.write("## Experiment: E-002\n- **Theory:** t\n")
        assert validate_doc(doc_modA) == 2  # Mod A outside --validate scope

        # Metric cross-check: mismatch is warned in the record.
        rec9 = os.path.join(td, "E-001-metmismatch.md")
        with open(rec9, "w", encoding="utf-8") as fh:
            fh.write("""## Experiment: E-001 - selfcheck metric mismatch
- **Status:** planned
- **Theory:** test theory
- **Hypothesis:** H-001: "accuracy >= 0.90"
- **Measurement Metrics:** accuracy >= 0.90
- **Experiment Design:** unit test
- **Code Scope:** none
""")
        with open(bench, "w", encoding="utf-8") as fh:
            fh.write('print("fake_accuracy=0.93 (14/15)")')
        sys.argv = ["run_experiment.py", "--record", rec9,
                    "--run", f'"{sys.executable}" "{bench}"']
        assert main() == 0
        assert "MISMATCH — measured fake" in open(rec9, encoding="utf-8").read()
        assert verify(rec9) == 2  # metric mismatch blocks code

        # Metric cross-check: match writes consistent.
        rec10 = os.path.join(td, "E-001-metmatch.md")
        with open(rec10, "w", encoding="utf-8") as fh:
            fh.write("""## Experiment: E-001 - selfcheck metric match
- **Status:** planned
- **Theory:** test theory
- **Hypothesis:** H-001: "grounding_accuracy >= 0.90"
- **Measurement Metrics:** grounding_accuracy >= 0.90
- **Experiment Design:** unit test
- **Code Scope:** none
""")
        with open(bench, "w", encoding="utf-8") as fh:
            fh.write('print("grounding_accuracy=0.93 (14/15)")')
        sys.argv = ["run_experiment.py", "--record", rec10,
                    "--run", f'"{sys.executable}" "{bench}"']
        assert main() == 0
        assert "consistent (measured grounding" in open(rec10, encoding="utf-8").read()

        # --dry-run: previews the decision WITHOUT writing to the record.
        rec_dry = os.path.join(td, "E-001-dryrun.md")
        with open(rec_dry, "w", encoding="utf-8") as fh:
            fh.write(
                "## Experiment: E-001 — selfcheck dry-run\n"
                "- **Status:** planned\n"
                "- **Theory:** test theory\n"
                '- **Hypothesis:** H-001: "accuracy >= 0.90"\n'
                "- **Measurement Metrics:** accuracy >= 0.90\n"
                "- **Experiment Design:** unit test\n"
                "- **Code Scope:** none\n"
            )
        with open(bench, "w", encoding="utf-8") as fh:
            fh.write('print("fake_accuracy=0.93 (14/15)")')
        before_dry = open(rec_dry, encoding="utf-8").read()
        _out = _io.StringIO()
        sys.argv = ["run_experiment.py", "--record", rec_dry,
                    "--run", f'"{sys.executable}" "{bench}"', "--dry-run"]
        with contextlib.redirect_stdout(_out):
            assert main() == 0
        assert "DRY-RUN" in _out.getvalue()
        assert "PASS" in _out.getvalue()
        assert "GATE-OK-E-001-" in _out.getvalue()  # would-be token previewed
        assert open(rec_dry, encoding="utf-8").read() == before_dry  # untouched
        assert "Decision" not in open(rec_dry, encoding="utf-8").read()
        # Template placeholder: '<...>' Decision (e.g. `<gate writes: APPROVED | REJECTED — reason>`)
        # is NOT a decision — gate should not reject as "already decided", --verify should not say FORGED.
        rec_ph = os.path.join(td, "E-001-placeholder.md")
        with open(rec_ph, "w", encoding="utf-8") as fh:
            fh.write(
                "## Experiment: E-001 — selfcheck placeholder\n"
                "- **Date:** 13.08.2026\n"
                "- **Status:** planned\n"
                "- **Theory:** test theory\n"
                '- **Hypothesis:** H-001: "accuracy >= 0.90"\n'
                "- **Measurement Metrics:** accuracy >= 0.90\n"
                "- **Experiment Design:** unit test\n"
                "- **Code Scope:** none\n"
                "- **Raw Results:** <numbers — as-is>\n"
                "- **Uncertainty:** <gate writes: small sample | none | n unknown>\n"
                "- **Metric:** <gate writes: consistent | MISMATCH | n/a>\n"
                "- **Decision:** <gate writes: APPROVED | REJECTED — reason>\n"
                "- **Gate Evidence:** <gate writes: GATE-OK-...>\n"
                "- **Next Step:** <gate writes: Proceed to Code | Return to Theory>\n"
            )
        _out_ph = _io.StringIO()
        sys.argv = ["run_experiment.py", "--record", rec_ph,
                    "--run", f'"{sys.executable}" "{bench}"', "--dry-run"]
        with contextlib.redirect_stdout(_out_ph):
            assert main() == 0  # placeholder karar bloklamaz
        assert "PASS" in _out_ph.getvalue()
        assert "already decided" not in _out_ph.getvalue()
        # --verify placeholder: undecided, NOT FORGED
        _out_ph2 = _io.StringIO()
        sys.argv = ["run_experiment.py", "--verify", "--record", rec_ph]
        with contextlib.redirect_stdout(_out_ph2):
            assert verify(rec_ph) == 1
        assert "FORGED" not in _out_ph2.getvalue()
        assert "undecided" in _out_ph2.getvalue()
        # dry-run on a FAILing value also writes nothing.
        with open(bench, "w", encoding="utf-8") as fh:
            fh.write('print("fake_accuracy=0.50 (20/40)")')
        before_dry2 = open(rec_dry, encoding="utf-8").read()
        _out2 = _io.StringIO()
        sys.argv = ["run_experiment.py", "--record", rec_dry,
                    "--run", f'"{sys.executable}" "{bench}"', "--dry-run"]
        with contextlib.redirect_stdout(_out2):
            assert main() == 0
        assert "REJECTED" in _out2.getvalue()
        assert open(rec_dry, encoding="utf-8").read() == before_dry2  # still untouched
        # dry-run with --run: the bench runs, the record stays untouched.
        with open(bench, "w", encoding="utf-8") as fh:
            fh.write('print("fake_accuracy=0.93 (14/15)")')
        before_dry3 = open(rec_dry, encoding="utf-8").read()
        _out3 = _io.StringIO()
        sys.argv = ["run_experiment.py", "--record", rec_dry,
                    "--run", f'"{sys.executable}" "{bench}"', "--dry-run"]
        with contextlib.redirect_stdout(_out3):
            assert main() == 0
        assert "DRY-RUN" in _out3.getvalue()
        assert "fake" in _out3.getvalue() or "uyumlu" in _out3.getvalue()
        assert open(rec_dry, encoding="utf-8").read() == before_dry3
        # after a real run the record is decided; dry-run then refuses as decided.
        sys.argv = ["run_experiment.py", "--record", rec_dry,
                    "--run", f'"{sys.executable}" "{bench}"']
        assert main() == 0
        before_dry4 = open(rec_dry, encoding="utf-8").read()
        _out4 = _io.StringIO()
        sys.argv = ["run_experiment.py", "--record", rec_dry,
                    "--run", f'"{sys.executable}" "{bench}"', "--dry-run"]
        with contextlib.redirect_stderr(_out4):
            assert main() == 2  # already decided -> refuses (dry-run included)
        assert open(rec_dry, encoding="utf-8").read() == before_dry4

        sys.argv = old
    if _old_env is None:
        os.environ.pop(SECRET_ENV, None)
    else:
        os.environ[SECRET_ENV] = _old_env
    print("selfcheck OK")


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    if "--selfcheck" in sys.argv:
        _selfcheck()
        sys.exit(0)
    sys.exit(main())

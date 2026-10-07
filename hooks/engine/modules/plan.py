"""Implementation Plan (GRP) — record-bound plan support for the guard.

E-065: multi-file operations (ports, refactors) discover their real file set
DURING implementation, but Code Scope is frozen at approval time and its token
breaks when widened. The plan layer adds a declared, record-bound working set:

* the record carries an `## Implementation Plan (GRP)` section (Planned Files /
  Planned Tests / Known Gaps / Amendments),
* the guard checks a target against that plan FIRST (set membership after one
  parse — O(1) per target, unlike the per-write record sweep in find_approved),
* plan-external files discovered mid-operation enter through a mechanically
  chained amendment (`run_experiment.py --amend-plan`): discovery stays free,
  leaving a trace is mandatory,
* the final --run token binds to the FINAL plan hash, so the plan can grow up
  to the moment of measurement without breaking provenance.

Enforcement stays mechanical: set membership, test existence, test runs.
`Known Gaps` is advisory text for the human reviewer and never gates.

A record without a plan section, a missing record file, or any parse error
yields an EMPTY plan (fail-open at this layer): the guard then falls back to
the Code Scope path exactly as before. The plan can only ever ADD a legitimate
working set on top of the record — never bypass a check.
"""
from __future__ import annotations

import hashlib
import hmac
import pathlib
import re
import sys
from functools import lru_cache

# --- Record plan section parsing ----------------------------------------------
#
#   ## Implementation Plan (GRP)
#
#   - **Planned Files:** src/a.py, src/b.py
#   - **Planned Tests:** tests/test_a.py
#   - **Known Gaps:** advisory prose …
#   - **Amendments:** none            ← gate/CLI-maintained counter line
#     - AMEND-1 2026-10-05 files=src/c.py hash=<64hex> reason="…"
#
# Every line is an English-labelled field, parsed like the record body's own
# `- **Field:**` lines. Continuation sub-bullets belong to the labelled field
# (same convention the guard uses for `- **Stories:**` lists).

_PLAN_SECTION_RE = re.compile(
    r"^##\s+Implementation\s+Plan\s*(?:\(GRP\))?\s*$",
    re.IGNORECASE | re.MULTILINE)
# Field values are OPTIONAL: `- **Planned Tests:**` with nothing after the
# colon must parse to an EMPTY value, never swallow the next bullet line (the
# (.*) after \s* can cross the newline when the line ends at the colon — so
# the value group only matches non-newline content explicitly).
_PLANNED_FILES_RE = re.compile(r"^- \*\*Planned Files:\*\*([^\n]*)$", re.MULTILINE | re.IGNORECASE)
_PLANNED_TESTS_RE = re.compile(r"^- \*\*Planned Tests:\*\*([^\n]*)$", re.MULTILINE | re.IGNORECASE)
_KNOWN_GAPS_RE = re.compile(r"^- \*\*Known Gaps:\*\*([^\n]*)$", re.MULTILINE | re.IGNORECASE)
_AMENDMENTS_RE = re.compile(r"^- \*\*Amendments:\*\*([^\n]*)$", re.MULTILINE | re.IGNORECASE)

# `none` (any case, optionally padded) = no amendments yet.
_AMEND_NONE_RE = re.compile(r"^none$", re.IGNORECASE)

# One amendment entry: `- AMEND-<n> <date> files=<a,b> hash=<64hex> [token=AMEND-OK-…] reason="…"`
# token is optional in the grammar (hash-only entries stay parseable) but
# --amend-plan always writes it, and amendment_chain_issue re-derives it when
# a secret is available.
_AMEND_ENTRY_RE = re.compile(
    r"^\s*-\s+AMEND-(\d+)\s+(\S+)\s+files=(\S+)\s+hash=([0-9a-f]{64})"
    r"(?:\s+token=([\w-]+))?\s+reason=\"([^\"]*)\"",
    re.MULTILINE)

# Split a planned-files/tests value into entries (comma/whitespace separated).
_PLAN_LIST_SPLIT_RE = re.compile(r"[,\s]+")

# Bare '**' (any depth) declared as the whole plan: the anti-tiptoe rule.
# The plan must name real files or bounded sub-trees, never "everything".
_BARE_GLOB_ALL_RE = re.compile(r"^\*\*$")

# Chain genesis: the first amendment's hash chains from this constant, so the
# record id/planned-set state before the first amendment is also covered.
_CHAIN_GENESIS = "GRP-CHAIN-GENESIS"

# Token binds the final plan: field label the gate upserts after the decision
# (the hash itself lives in Gate Evidence via the token; this line makes the
# binding inspectable and is what --verify cross-checks).
FINAL_PLAN_FIELD = "- **Final Plan Hash:**"

AMEND_TOKEN_PREFIX = "AMEND-OK-"


def plan_section(text: str) -> str:
    """Return the `## Implementation Plan (GRP)` section body ('' when absent)."""
    m = _PLAN_SECTION_RE.search(text)
    if not m:
        return ""
    tail = text[m.end():]
    nxt = re.search(r"^##\s+", tail, re.MULTILINE)
    return tail[: nxt.start()] if nxt else tail


def _split_plan_list(value: str) -> list[str]:
    """Split a plan list value into clean, forward-slashed entries."""
    v = value.strip()
    if v == "**":
        # A bolded continuation (**) after a colon-less field label is markup,
        # not a list item — the value is EMPTY (the bench E-065 trial that
        # caught this: `- **Planned Tests:**` followed by `- **Known Gaps:**`).
        return []
    out = []
    for item in _PLAN_LIST_SPLIT_RE.split(v):
        item = item.strip().replace("\\", "/").strip('"')
        if item:
            out.append(item)
    return out


def parse_plan_fields(text: str) -> dict:
    """Parse the plan section into its raw fields (all '' / empty when absent).

    Returns {planned_files: [str], planned_tests: [str], known_gaps: str,
    amendments_raw: str}.
    """
    section = plan_section(text)
    files_m = _PLANNED_FILES_RE.search(section)
    tests_m = _PLANNED_TESTS_RE.search(section)
    gaps_m = _KNOWN_GAPS_RE.search(section)
    amend_m = _AMENDMENTS_RE.search(section)
    return {
        "planned_files": _split_plan_list(files_m.group(1)) if files_m else [],
        "planned_tests": _split_plan_list(tests_m.group(1)) if tests_m else [],
        "known_gaps": gaps_m.group(1).strip() if gaps_m else "",
        "amendments_raw": amend_m.group(1).strip() if amend_m else "",
    }


# --- Plan compilation and matching ---------------------------------------------
#
# Semantics: EXPLICIT planned files are exact set membership. A planned entry
# may still carry glob wildcards (src/dep/**, lib/*.py) — those compile to
# regexes. When a record declares ANY explicit plan, the plan is AUTHORITATIVE
# for what it names: a frozen `src/**` Code Scope cannot leak plan-external
# siblings through the plan path (the guard's Code Scope check still runs
# underneath; the plan only narrows precedence, it never widens the scope).

def _glob_to_regex(pattern: str) -> str:
    """Same glob dialect as run_experiment.glob_to_regex (* segment, ** depth,
    ? char) — kept local so the engine never imports the gate module."""
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


# Guard hot path: the plan is compiled once per (record, mtime, size) and
# reused for every write in the session — the same (mtime_ns, size) cache
# discipline the guard uses for Code Scope and for verify results. A stale
# entry can never survive a record edit (mtime/size change) and the cache is
# bounded, so a long session over many records cannot grow it without limit.
@lru_cache(maxsize=256)
def _compile_cached(record_path: str, mtime_ns: int, size: int) -> dict:
    return _compile_uncached(record_path)


def compile_plan(record_path: str) -> dict:
    """Compile a record's plan (mtime+size cached), or an empty plan.

    Returns {planned_exact: set[str], planned_patterns: [compiled_re],
    planned_tests: set[str], planned_count: int, known_gaps: str}.
    A record without a plan section, a bare-'**' plan (anti-tiptoe), or any
    parse problem yields an EMPTY plan — the layer fails open; the guard then
    falls back to the Code Scope path exactly as before.

    The returned dict is CACHED and shared: callers must treat it as
    immutable (plan_match and the guard only read it).
    """
    try:
        st = pathlib.Path(record_path).stat()
    except OSError:
        return _compile_uncached(record_path)
    return _compile_cached(record_path, st.st_mtime_ns, st.st_size)


def _compile_uncached(record_path: str) -> dict:
    """Uncached compile — the body compile_plan documents."""
    empty = {"planned_exact": set(), "planned_patterns": [],
             "planned_tests": set(), "planned_count": 0, "known_gaps": ""}
    try:
        text = pathlib.Path(record_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return empty
    fields = parse_plan_fields(text)
    if not fields["planned_files"] and not fields["planned_tests"]:
        return empty
    exact: set[str] = set()
    patterns: list[re.Pattern[str]] = []
    # Anti-tiptoe: a bare '**' planned entry alone declares nothing — reject
    # it so the plan can never quietly become "everything".
    if len(fields["planned_files"]) == 1 and _BARE_GLOB_ALL_RE.match(fields["planned_files"][0]):
        return empty
    for entry in fields["planned_files"]:
        if _BARE_GLOB_ALL_RE.match(entry):
            continue
        if any(ch in entry for ch in "*?"):
            patterns.append(re.compile(_glob_to_regex(entry), re.IGNORECASE))
        else:
            exact.add(entry.lstrip("./"))
    tests = {t.lstrip("./") for t in fields["planned_tests"]}
    return {"planned_exact": exact, "planned_patterns": patterns,
            "planned_tests": tests, "planned_count": len(exact) + len(patterns),
            "known_gaps": fields["known_gaps"]}


def plan_match(plan: dict, target: str) -> bool:
    """True when the (project-relative, forward-slashed) target is in the plan."""
    t = (target or "").replace("\\", "/").lstrip("./")
    if not t:
        return False
    if t in plan["planned_exact"]:
        return True
    return any(p.match(t) for p in plan["planned_patterns"])


def plan_progress(record_path: str, project_root: str) -> dict:
    """Count planned files done (exists on disk) vs pending.

    Best-effort and fail-open: a missing root or unreadable record counts
    nothing. Used by the Stop report and the blackboard plan-progress peek.
    """
    plan = compile_plan(record_path)
    done = 0
    for f in sorted(plan["planned_exact"]):
        try:
            if (pathlib.Path(project_root) / f).is_file():
                done += 1
        except OSError:
            continue
    return {"done": done, "pending": max(plan["planned_count"] - done, 0),
            "planned": plan["planned_count"]}


def active_plan_for(target: str, records_dir: str,
                    is_verified=None) -> str | None:
    """The record whose plan covers target ('' targets → None).

    Prefer the VERIFIED record when several plans cover the target: an
    approved plan owns the file; a draft plan on the same target must not
    shadow it. `is_verified(record_path) -> bool` is injectable (the guard
    passes its HMAC verify wrapper); records that raise on check are skipped.
    Missing records dir → None (fail-open; guard falls back to Code Scope).
    """
    if not target:
        return None
    base = pathlib.Path(records_dir)
    if not base.is_dir():
        return None
    verified: list[str] = []
    any_cover: list[str] = []
    for rec in sorted(base.glob("*.md")):
        if rec.name.startswith("_"):
            continue
        try:
            plan = compile_plan(str(rec))
        except Exception:
            continue
        if plan["planned_count"] == 0 or not plan_match(plan, target):
            continue
        ok = False
        if is_verified is not None:
            try:
                ok = bool(is_verified(str(rec)))
            except Exception:
                ok = False
        if ok:
            verified.append(str(rec))
        else:
            any_cover.append(str(rec))
    # Specificity FIRST among equals: the record whose plan is the NARROWEST
    # for this target wins (a single-file plan beats a wide plan that merely
    # includes the file). Prefer verified records, then narrower plans.
    def specificity(rec_path: str) -> int:
        try:
            return compile_plan(rec_path)["planned_count"]
        except Exception:
            return 1 << 30
    if verified:
        return min(verified, key=specificity)
    return min(any_cover, key=specificity) if any_cover else None


# --- Amendment chain (HMAC) -----------------------------------------------------
#
# Each amendment entry commits to:
#   HMAC-SHA256(key, "AMEND|<rec_id>|<prev_hash>|<seq>|<files_joined>|<reason>")
# and the entry line stores `<hash> <token>` where token = AMEND-OK-<rec>-<mac32>.
# prev_hash of the FIRST entry is the chain genesis constant, so the whole
# sequence (order + content) is tamper-evident without any re-measurement.
# The gate key (machine-local, ~/.bmad/gate-key) signs amendments exactly as
# it signs decisions — same trust ring, no new key material.

def amend_payload(rec_id: str, prev_hash: str, seq: int, files: list[str],
                  reason: str) -> str:
    """The canonical string an amendment's hash commits to."""
    return f"AMEND|{rec_id}|{prev_hash}|{seq}|{','.join(files)}|{reason}"


def amend_token(payload: str, secret: bytes) -> str:
    """AMEND-OK-<mac32> for the canonical payload under the given key."""
    mac = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{AMEND_TOKEN_PREFIX}{mac[:32]}"


def amend_entry_hash(payload: str) -> str:
    """The chain hash of an amendment: sha256 over the canonical payload.

    (The HMAC token proves the key-holder approved the entry; the chain hash
    binds entries to each other — both are checked by amendment_chain_issue.)
    """
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _record_id_of(text: str) -> str:
    m = re.search(r"^##\s+Experiment:\s*([A-Za-z]+-\d+)", text, re.MULTILINE)
    return m.group(1) if m else ""


def parse_amendments(text: str) -> list[dict]:
    """Parse amendment entries into ordered dicts (seq, date, files, hash,
    reason, token). Empty list when none."""
    section = plan_section(text)
    out: list[dict] = []
    for m in _AMEND_ENTRY_RE.finditer(section):
        out.append({
            "seq": int(m.group(1)),
            "date": m.group(2),
            "files": _split_plan_list(m.group(3)),
            "hash": m.group(4),
            "token": m.group(5) or "",
            "reason": m.group(6),
        })
    return out


def last_chain_hash(text: str) -> str | None:
    """The chain hash of the last amendment, or None when there is none."""
    amends = parse_amendments(text)
    return amends[-1]["hash"] if amends else None


def final_plan_hash(text: str) -> str:
    """The hash the final --run token binds to: sha256 over the record id,
    the planned file set (post-amendment, order-normalized), the planned test
    set and the full amendment chain hash. Always computable — empty plans
    hash to a stable value, so binding never depends on a plan existing."""
    fields = parse_plan_fields(text)
    rec_id = _record_id_of(text)
    chain = last_chain_hash(text) or _CHAIN_GENESIS
    material = "|".join([
        f"FINAL|{rec_id}",
        ",".join(sorted(fields["planned_files"])),
        ",".join(sorted(fields["planned_tests"])),
        chain,
    ])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def amendment_chain_issue(text: str, secret=None) -> str | None:
    """Mechanical integrity check over the amendment chain, or None when the
    chain is intact (including the zero-amendment case).

    With NO secret: chain integrity only (sequence + hash re-derivation).
    The AMEND-OK token is NOT re-derived here — verify() owns the key
    (machine-local trust ring); a caller holding a key may pass it and get
    token checks too, but the default check must stay key-free so the guard
    can run it per-write.

    Checks per entry, in order: (1) sequential numbering from 1, (2) the chain
    hash re-derives from the canonical payload over the PREVIOUS entry's hash
    (order+content binding), (3) with a supplied secret, the AMEND-OK token
    re-derives under it (key-holder approval). Returns a human-readable
    refusal reason on the first broken link.
    """
    amends = parse_amendments(text)
    if not amends:
        # 'Amendments:' must say none/empty when there are no entries — a
        # counter line claiming N amendments with zero entries is broken state.
        raw = parse_plan_fields(text)["amendments_raw"]
        if raw and not _AMEND_NONE_RE.match(raw):
            return "Amendments counter claims entries but the chain is empty"
        return None
    rec_id = _record_id_of(text)
    prev = _CHAIN_GENESIS
    for i, a in enumerate(amends, start=1):
        if a["seq"] != i:
            return f"amendment sequence broken at AMEND-{a['seq']} (expected {i})"
        payload = amend_payload(rec_id, prev, a["seq"], a["files"], a["reason"])
        if amend_entry_hash(payload) != a["hash"]:
            return (f"amendment AMEND-{a['seq']} chain hash does not match its "
                    f"declared files/reason (edited or reordered after signing)")
        if secret is not None:
            # Key-holder approval: the AMEND-OK token must re-derive under the
            # given key. An entry with a hash-only line (no token) fails here.
            if not a["token"] or amend_token(payload, secret) != a["token"]:
                return (f"amendment AMEND-{a['seq']} token does not match its "
                        f"payload under the supplied key (forged or foreign entry)")
        prev = a["hash"]
    return None


# --- Record mutation helpers (used by run_experiment.py --amend-plan) ----------

def append_amendment(text: str, seq: int, date: str, files: list[str],
                     reason: str, secret: bytes) -> tuple[str, str]:
    """Return (new_text, chain_hash) after appending one amendment entry.

    Chains from the current last hash (or the genesis constant), flips the
    `Amendments:` counter line from `none` to `1` (or bumps the number), and
    appends the signed entry sub-bullet under it. Does NOT write the file —
    the caller owns the write (single-writer rule stays with the CLI).
    """
    rec_id = _record_id_of(text)
    prev = last_chain_hash(text) or _CHAIN_GENESIS
    payload = amend_payload(rec_id, prev, seq, files, reason)
    chain_hash = amend_entry_hash(payload)
    token = amend_token(payload, secret)
    entry = (f"  - AMEND-{seq} {date} files={','.join(files)} "
             f"hash={chain_hash} token={token} reason=\"{reason}\"")
    lines = text.splitlines()
    counter_idx = None
    for i, line in enumerate(lines):
        if _AMENDMENTS_RE.match(line.strip()):
            counter_idx = i
            break
    if counter_idx is None:
        lines.append("- **Amendments:** 1")
        lines.append(entry)
    else:
        raw = _AMENDMENTS_RE.match(lines[counter_idx].strip()).group(1).strip()
        if _AMEND_NONE_RE.match(raw):
            lines[counter_idx] = "- **Amendments:** 1"
        else:
            try:
                n = int(raw.split()[0])
            except (ValueError, IndexError):
                n = seq - 1
            lines[counter_idx] = f"- **Amendments:** {n + 1}"
        lines.append(entry)
    return "\n".join(lines) + "\n", chain_hash


def bump_planned_files(text: str, files: list[str]) -> str:
    """Add files to the Planned Files field (dedup, order kept)."""
    fields = parse_plan_fields(text)
    merged = list(dict.fromkeys([*fields["planned_files"],
                                   *(f.replace("\\", "/") for f in files)]))
    value = ", ".join(merged)
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if _PLANNED_FILES_RE.match(line.strip()):
            lines[i] = f"- **Planned Files:** {value}"
            return "\n".join(lines) + "\n"
    # No Planned Files line (plan section may exist without it): insert one
    # right after the section heading.
    m = _PLAN_SECTION_RE.search(text)
    if not m:
        return text
    insert_at = text.index("\n", m.end()) + 1 if "\n" in text[m.end():] else len(text)
    return text[:insert_at] + f"- **Planned Files:** {value}\n" + text[insert_at:]


# --- Gate-side binding helpers (imported by run_experiment.py) ------------------

def plan_hash_for_record(record_path: str) -> str:
    """Read a record file and return its final plan hash (E-065 token input)."""
    try:
        text = pathlib.Path(record_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    return final_plan_hash(text)


# --- Surfacing (E-068): one formatter, every session-edge surface -----------------
#
# E-066 surfaced plan progress at the session CLOSE only. An operation spanning
# sessions still started blind, so the same derived fact is now injected at the
# session EDGE (SessionStart context) and in the orient/board digest. All three
# surfaces call the formatter here, so they can never disagree about what an
# open plan has left. Derivation stays read-only (record files only, mtime+size
# cached through compile_plan) and bounded (the open list is capped).

def _progress_fragment(prog: dict) -> str:
    """`n/m planned files on disk (k pending)` — the one count sentence."""
    return (f"{prog['done']}/{prog['planned']} planned files on disk "
            f"({prog['pending']} pending)")


def plan_progress_text(record_path: str, project_root: str) -> str:
    """Shared progress text for a record, or '' when there is nothing pending.

    Empty for a record without a plan AND for a completed plan (every planned
    file on disk): a finished operation has nothing to surface. This is the
    single source the open list, the SessionStart context and the digest print.
    """
    prog = plan_progress(record_path, project_root)
    if prog["planned"] == 0 or prog["pending"] == 0:
        return ""
    return _progress_fragment(prog)


def open_plan_line(record_path: str, project_root: str) -> str | None:
    """`<record-name> n/m planned files on disk (k pending)`, or None.

    None when the record has no plan or nothing pending (see
    plan_progress_text). The record name leads so a resumed session knows WHICH
    operation the counts belong to.
    """
    frag = plan_progress_text(record_path, project_root)
    if not frag:
        return None
    return f"{pathlib.Path(record_path).name} {frag}"


# Cap on open plans a single surface names: a nudge, never a directory listing.
_OPEN_PLAN_LIMIT = 3


def open_plans(records_dir: str, project_root: str,
               limit: int = _OPEN_PLAN_LIMIT) -> list[dict]:
    """Records whose plan still has pending files — name-sorted, capped, read-only.

    Only records with a plan AND at least one pending planned file become rows
    (a completed plan is silent by design). Returns
    [{name, path, line, done, pending, planned}]. Fail-open: a missing records
    dir or an unreadable record contributes nothing.
    """
    base = pathlib.Path(records_dir)
    if not base.is_dir() or limit <= 0:
        return []
    rows: list[dict] = []
    for rec in sorted(base.glob("*.md")):
        if rec.name.startswith("_"):
            continue
        if len(rows) >= limit:
            break
        try:
            line = open_plan_line(str(rec), project_root)
        except Exception:
            continue
        if not line:
            continue
        prog = plan_progress(str(rec), project_root)
        rows.append({"name": rec.name, "path": str(rec), "line": line,
                     "done": prog["done"], "pending": prog["pending"],
                     "planned": prog["planned"]})
    return rows


def open_plans_text(records_dir: str, project_root: str,
                    limit: int = _OPEN_PLAN_LIMIT) -> str:
    """The open plans joined into one bounded line, or '' when none."""
    rows = open_plans(records_dir, project_root, limit)
    return "; ".join(row["line"] for row in rows)


# Stop-report hook surface: the session wrap-up can surface plan progress for
# records touched this session (the guard's audit trail names the records).
def progress_line(record_path: str, project_root: str) -> str | None:
    """One-line plan progress summary, or None when the record has no plan.

    Unlike plan_progress_text this keeps a completed plan visible (nothing
    pending still reports `n/n`); it exists for callers that already decided to
    report a record and only need the counts.
    """
    prog = plan_progress(record_path, project_root)
    if prog["planned"] == 0:
        return None
    return f"plan progress: {_progress_fragment(prog)}"


def _selfcheck() -> int:
    """Minimal self-asserts (also exercised by the bench)."""
    assert "**Planned Files:**" in plan_section(
        "## Implementation Plan (GRP)\n- **Planned Files:** a\n")
    assert plan_section("no plan here") == ""
    assert parse_plan_fields("## Implementation Plan (GRP)\n"
                             "- **Planned Files:** a.py, b.py\n")["planned_files"] == \
        ["a.py", "b.py"]
    assert _glob_to_regex("src/**") == "^src/.*$"
    assert _glob_to_regex("src/dep/**").startswith("^src/dep/")
    p = compile_plan.__doc__  # presence guard — real cases live in the bench/tests
    assert p
    # E-068 formatter: empty without pending, name-prefixed when pending.
    assert plan_progress_text("Z:/none/E-1.md", ".") == ""
    assert open_plan_line("Z:/none/E-1.md", ".") is None
    assert open_plans("Z:/none/dir", ".") == []
    print("plan selfcheck OK")
    return 0


if __name__ == "__main__":
    sys.exit(_selfcheck())

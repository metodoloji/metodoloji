"""Freshness plane — derive, never store, whether a claim still holds.

A recorded decision (a board hand-off signal, an APPROVED experiment scope, a
run key) is a claim about the CODE STATE at the moment it was written. The
engine's validity vocabulary used to hold two states — delivered or absent —
so a signal that named a decision the code has since moved past was delivered
as live work, and validity was proxied by AGE alone (the write-path TTL). The
failure this module closes (E-052 measured the baseline at 1.0):

    yesterday's decision is still in the handoff file, and the agent obeys it
    after the code has already moved on

Design rules (E-053):

1. **Every claim carries a witness.** The witness is the code state the claim
   was written against: ``{commit, tree, scope, ts}``. ``commit`` is HEAD at
   write time, ``tree`` a digest of the dirty work-tree (status/porcelain)
   at write time, ``scope`` a digest of the claim's own Code Scope globs.
2. **Freshness is derived, never stored.** Nothing on the board says "this is
   fresh"; :func:`classify` recomputes the answer from the witness, the
   current HEAD, and the changed-file set. (The same single-source doctrine
   ``chain_health`` follows — TD-014.)
3. **Three-valued verdict** — ``CURRENT | STALE | UNKNOWN``. ``UNKNOWN`` is a
   real answer ("cannot be established here": no witness, no git, no readable
   scope) and never silently degrades to ``CURRENT``. That third state is the
   whole point: without it, "unverifiable" reads as "valid".
4. **Scope-sensitive, not commit-sensitive.** A commit outside the claim's
   code scope must never stale it. Commit-sensitivity would stale every
   approval on every unrelated commit — noise, not signal.
5. **Two anchors.** A STORED witness (written by the posting/approval path) is
   exact: its ``tree`` digest also catches uncommitted scope edits. A DERIVED
   witness (a legacy claim with no witness event) is best effort: the commit
   that last touched the record that carries the claim, which is the
   "date every recorded decision to a commit" remedy applied retrospectively.
6. **Fail-explicit and bounded.** Every git call is timeout-bounded and any
   failure yields ``UNKNOWN`` with a reason — never an exception on a read
   path, never a silent allow.
"""

from __future__ import annotations

import hashlib
import pathlib
import re
import subprocess
import time
from contextlib import contextmanager

# Verdicts. RETIRED is a verdict too (a first-class "this no longer applies"),
# but it is owned by the board's retire plane and only surfaced here.
CURRENT = "CURRENT"
STALE = "STALE"
UNKNOWN = "UNKNOWN"
RETIRED = "RETIRED"

# One git call inside this budget; the whole classification stays inside the
# PreToolUse envelope the guard already respects (config.SCOPE_GIT_TIMEOUT_SECONDS).
GIT_TIMEOUT_SECONDS = 5

# Caching is BATCH-SCOPED, never time-based. A wall-clock TTL was measured
# producing a false CURRENT: the bench posts a claim, commits, and re-reads
# within milliseconds, and a cached HEAD hid the commit (E-053, first run of
# bench_freshness). Freshness must answer with the code state as it is NOW, so
# outside a batch every call re-reads git, and a batch (one board scan) folds
# its repeated reads into one. The cache is dropped when the batch closes.
_CACHE_MAX = 64
_BATCH = {"depth": 0}
_HEAD_CACHE: dict[str, str | None] = {}
_CHANGED_CACHE: dict[tuple[str, str], list[str] | None] = {}
_DIRTY_CACHE: dict[str, list[str] | None] = {}
_SUPERSEDE_CACHE: dict[tuple[str, str], list[str]] = {}

# A run key is the identity a claim is dated against: `E-048`, `story.1-1-x`,
# `IR-2026-10-01`. Loose on purpose (the protocol is extensible) but anchored
# so ordinary prose before a colon is not mistaken for a run key.
RUN_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{1,79}$")
_RECORD_ID_RE = re.compile(r"^([A-Z]+-\d+)\b")
_FIELD_RE = re.compile(r"^\s*-\s*\*\*([^*]+):\*\*\s*(.*)$")


# --- git access (bounded, fail-open) ------------------------------------------
def _strip_dot_prefix(path: str) -> str:
    """Remove a leading './' REPEATEDLY — and nothing else.

    `str.lstrip("./")` strips any leading run of '.' and '/' characters, so it
    eats the dot of a dot-directory: '.github/workflows/ci.yml' became
    'github/workflows/ci.yml' while the pattern kept its dot, and the two could
    never match. Every dot-directory (.github/, .githooks/, .devcontainer/,
    .vscode/) was therefore impossible to cover with a scope — including an
    exact, fully spelled path.
    """
    p = (path or "").replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


def _git(root: str, *args: str) -> str | None:
    """stdout of a bounded git call, or None when git cannot answer at all."""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=GIT_TIMEOUT_SECONDS, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout


def batch():
    """Fold the repeated git reads of ONE read operation into one each.

    Usage: `with fr.batch(): …` around a board scan. Nested batches are safe;
    the caches are cleared when the outermost closes, so nothing outlives the
    operation that produced it.
    """
    return _batch_cm()


@contextmanager
def _batch_cm():
    _BATCH["depth"] += 1
    try:
        yield
    finally:
        _BATCH["depth"] = max(0, _BATCH["depth"] - 1)
        if _BATCH["depth"] == 0:
            _HEAD_CACHE.clear()
            _CHANGED_CACHE.clear()
            _DIRTY_CACHE.clear()
            _SUPERSEDE_CACHE.clear()


def _cached(cache: dict, key, producer):
    """Memoized producer WITHIN a batch; uncached outside one (see _BATCH)."""
    if _BATCH["depth"] == 0:
        return producer()
    if key in cache:
        return cache[key]
    value = producer()
    if len(cache) >= _CACHE_MAX:
        cache.pop(next(iter(cache)))
    cache[key] = value
    return value


def head(root: str) -> str | None:
    """Current commit ('' for a repo without commits), or None when unknown."""
    def produce() -> str | None:
        out = _git(root, "rev-parse", "HEAD")
        if out is None:
            return None
        return out.strip()
    return _cached(_HEAD_CACHE, str(root), produce)


def dirty_files(root: str) -> list[str] | None:
    """Work-tree files differing from HEAD (modified + untracked), or None.

    One `git status --porcelain` call; the file list is all this plane needs —
    no file contents are read, so the cost is independent of the workspace size.
    """
    def produce() -> list[str] | None:
        out = _git(root, "status", "--porcelain", "--untracked-files=all")
        if out is None:
            return None
        files = []
        for line in out.splitlines():
            if len(line) < 4:
                continue
            path = line[3:].strip().strip('"')
            if " -> " in path:  # rename: the destination is the live path
                path = path.split(" -> ", 1)[1]
            if path:
                files.append(path.replace("\\", "/"))
        return files
    return _cached(_DIRTY_CACHE, str(root), produce)


def changed_since(root: str, commit: str) -> list[str] | None:
    """Files moved since `commit`: committed diff plus the dirty work tree.

    None when the question cannot be answered (unknown commit, git failure) —
    the caller then reports UNKNOWN rather than assuming nothing moved.
    """
    if not commit or commit == "none":
        return None
    key = (str(root), commit)

    def produce() -> list[str] | None:
        out = _git(root, "diff", "--name-only", f"{commit}..HEAD")
        if out is None:
            # A commit that is not in this repo (a rewritten history, or a
            # different checkout) is not "no change": it is unanswerable.
            return None
        paths = [l.strip().replace("\\", "/") for l in out.splitlines() if l.strip()]
        dirty = dirty_files(root)
        if dirty:
            for p in dirty:
                if p not in paths:
                    paths.append(p)
        return paths
    return _cached(_CHANGED_CACHE, key, produce)


def tree_digest(root: str) -> str:
    """Digest of the dirty work-tree state ('' entries included, deterministic).

    Only the porcelain file set is hashed — bounded, no content reads, and
    exactly as precise as the question "did the tree move" needs.
    """
    dirty = dirty_files(root)
    if dirty is None:
        return ""
    payload = "\n".join(sorted(dirty))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# --- scope matching (parity-tested against the gate) --------------------------
def parse_scope(scope: str) -> list[str]:
    """Split a 'Code Scope' value into patterns (comma/whitespace separated)."""
    if not scope:
        return []
    return [p for p in re.split(r"[,\s]+", scope) if p]


def glob_to_regex(pattern: str) -> str:
    """Convert a scope glob to a regex.

    Semantics mirror the gate's own matcher (``run_experiment.glob_to_regex``,
    the single source): `*` one path segment, `**` any depth, `?` one char.
    A parity test pins the two implementations — this copy exists so the read
    path never pays a gate import, not to define new semantics.
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
    """True when the scope value covers a project-relative path."""
    if not scope or not target:
        return False
    target = _strip_dot_prefix(target)
    return any(re.fullmatch(glob_to_regex(pat), target, re.IGNORECASE)
               for pat in parse_scope(scope))


def scope_digest(scope: str) -> str:
    """Stable digest of a normalized Code Scope — the claim's own identity.

    Empty when the scope declares no code, or when `[freshness] scope_digest`
    is switched off: the operator then chose commit-only classification, and an
    empty digest is how that choice reaches `classify` (E-053 plan §5.5).
    """
    if not scope or scope.strip().lower() == "none":
        return ""
    if not scope_digest_enabled():
        return ""
    normalized = " ".join(sorted(parse_scope(scope)))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def scope_digest_enabled() -> bool:
    """`[freshness] scope_digest` (default true): classify on the claim's scope.

    Off → every commit after the witness stales the claim (the noisy,
    commit-sensitive mode the doctrine warns about, offered only because an
    operator may have code it cannot scope-glob).
    """
    try:
        from .config import freshness_setting
        return freshness_setting("scope_digest").strip().lower() not in (
            "false", "off", "no", "0")
    except Exception:
        return True


def max_digest_files() -> int:
    """`[freshness] max_digest_files` (default 2000): bound the changed set.

    Above it the answer is UNKNOWN rather than a guess: a workspace that moved
    more files than the bound cannot be classified honestly inside the budget.
    """
    try:
        from .config import freshness_setting
        return max(1, int(freshness_setting("max_digest_files")))
    except Exception:
        return 2000


def unknown_policy() -> str:
    """`[freshness] unknown_policy` — how UNKNOWN is reported (default warn).

    "warn"   → reported everywhere (an absence of evidence is visible)
    "silent" → counted, but not announced (a legacy corpus with no witnesses
               does not repeat "unverifiable" on every surface)
    "deny"   → treated like STALE by the write-time gate
    """
    try:
        from .config import freshness_setting
        value = freshness_setting("unknown_policy").strip().lower()
    except Exception:
        return "warn"
    return value if value in ("warn", "silent", "deny") else "warn"


def blocks(verdict: str) -> bool:
    """Does this verdict keep an approval from opening code (mode-aware)?

    One place answers the question so guard/quality/deploy cannot disagree:
    STALE blocks under `[hooks] freshness = deny`, UNKNOWN blocks under
    `[freshness] unknown_policy = deny`.
    """
    verdict = str(verdict or "").upper()
    if mode() != "deny":
        return False
    if verdict == STALE:
        return True
    return verdict == UNKNOWN and unknown_policy() == "deny"


# --- claims: records and run keys ---------------------------------------------
def record_path(root: str, run_key: str) -> pathlib.Path | None:
    """The record file a claim's run key resolves to, when it resolves.

    `E-048` → `docs/experiments/E-048.md`; `story.1-1-x` / `IR-*` ids resolve
    where the canonical layout keeps them. None when the key names no record
    (then the claim has no code-state anchor and reads UNKNOWN).
    """
    key = str(run_key or "").strip()
    m = _RECORD_ID_RE.match(key)
    if not m:
        return None
    record_id = m.group(1)
    kind = record_id.split("-", 1)[0]
    base = pathlib.Path(root)
    candidates = {
        "E": [base / "docs" / "experiments" / f"{record_id}.md"],
        "IR": [base / "docs" / "development" / f"{record_id}.md"],
        "SP": [base / "docs" / "development" / f"{record_id}.md"],
        "S": [base / "docs" / "development" / "stories" / f"{record_id}.md",
              base / "docs" / "development" / f"{record_id}.md"],
        "QR": [base / "docs" / "quality" / f"{record_id}.md"],
        "PR": [base / "docs" / "development" / f"{record_id}.md"],
    }.get(kind, [])
    for cand in candidates:
        try:
            if cand.is_file():
                return cand
        except OSError:
            continue
    return None


def read_fields(record_file: pathlib.Path) -> dict:
    """`- **Field:** value` fields of a record (fail-open: {} when unreadable)."""
    try:
        text = record_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    out = {}
    for line in text.splitlines():
        if len(line) > 40_000:
            break
        m = _FIELD_RE.match(line)
        if m:
            out[m.group(1).strip()] = m.group(2).strip()
    return out


def record_scope(root: str, run_key: str) -> str:
    """The claim's own Code Scope ('' when the key names no readable record)."""
    rec = record_path(root, run_key)
    if rec is None:
        return ""
    return read_fields(rec).get("Code Scope", "").strip()


def superseded_by(root: str, run_key: str) -> str | None:
    """The record that supersedes this claim's record, when one is declared.

    The key-free cross-machine remedy (`- **Re-Measured-By:** E-053 (date)`) is
    also a *supersede* declaration: the old record's decision is no longer the
    operative one, so any signal citing it is cascade-invalidated rather than
    merely old. Returns the new record id, or None.
    """
    rec = record_path(root, run_key)
    if rec is None:
        return None
    value = read_fields(rec).get("Re-Measured-By", "")
    m = _RECORD_ID_RE.match(value.strip())
    if not m:
        return None
    new_id = m.group(1)
    if new_id == str(run_key).strip():
        return None
    return new_id if record_path(root, new_id) is not None else None


def superseded_records(root: str, new_key: str) -> list[str]:
    """Older records that declare `Re-Measured-By: <new_key>` (cascade set).

    The re-measurement marker is the key-free cross-machine remedy AND a
    supersede declaration: once a record is re-measured by a newer one, its
    decision is no longer the operative one, so every claim citing it is
    cascade-invalidated rather than merely old. Bounded scan (500 records, one
    stat each, cached per process) — a run-key write pays it once.
    """
    key = str(new_key or "").strip()
    if not key:
        return []
    directory = pathlib.Path(root) / "docs" / "experiments"

    def scan() -> list[str]:
        found: list[str] = []
        try:
            files = sorted(directory.glob("E-*.md"))[:500]
        except OSError:
            files = []
        for rec in files:
            if rec.name == "_template.md":
                continue
            try:
                if rec.stat().st_size > 400_000:
                    continue
            except OSError:
                continue
            value = read_fields(rec).get("Re-Measured-By", "")
            m = _RECORD_ID_RE.match(value.strip())
            if m and m.group(1) == key and rec.stem != key:
                found.append(rec.stem)
        return found

    return _cached(_SUPERSEDE_CACHE, (str(directory), key), scan)


def witness_of(root: str, *, scope: str = "") -> dict:
    """The witness to record for a claim written NOW (called by the write path)."""
    commit = head(root) or ""
    return {"commit": commit or "none", "tree": tree_digest(root) or "none",
            "scope": scope_digest(scope) or "none", "ts": time.time()}


def derived_witness(root: str, run_key: str) -> dict | None:
    """Best-effort witness for a claim with no stored one: its record's commit.

    The record that carries the claim is dated to the commit that last touched
    it — the retrospective form of "date every recorded decision to a commit".
    None when the key resolves to no record (no anchor exists at all).
    """
    rec = record_path(root, run_key)
    if rec is None:
        return None
    rel = rec.relative_to(pathlib.Path(root)).as_posix() \
        if str(rec).startswith(str(pathlib.Path(root))) else str(rec)
    out = _git(root, "log", "-1", "--format=%H", "--", rel)
    if out is None:
        return None
    commit = out.strip().splitlines()[0].strip() if out.strip() else ""
    if not commit:
        return None
    return {"commit": commit, "tree": "none",
            "scope": scope_digest(record_scope(root, run_key)) or "none",
            "ts": 0.0, "derived": True}


# --- classification -----------------------------------------------------------
def mode() -> str:
    """Configured freshness mode: 'off' | 'warn' | 'deny' (default 'warn')."""
    try:
        from .config import _read_hooks_section
        value = str(_read_hooks_section().get("freshness", "warn")).strip().lower()
    except Exception:
        return "warn"
    return value if value in ("off", "warn", "deny") else "warn"


def enabled() -> bool:
    """False when freshness is switched off — surfaces then behave as before."""
    return mode() != "off"


def classify(root: str, *, commit: str = "", scope: str = "",
             tree: str = "", run_key: str = "", retired: bool = False) -> tuple[str, dict]:
    """Three-valued freshness of one claim. Never raises.

    Returns ``(verdict, detail)``; detail always carries ``reason`` and, when
    it could be computed, ``changed`` (the scoped paths that moved).
    """
    detail: dict = {"reason": "", "changed": [], "commit": commit, "scope": scope}
    if retired:
        return RETIRED, {**detail, "reason": "retired — this claim no longer applies"}
    if not enabled():
        return CURRENT, {**detail, "reason": "freshness disabled ([hooks] freshness = off)"}
    if run_key:
        newer = superseded_by(root, run_key)
        if newer:
            return STALE, {**detail,
                           "reason": f"superseded by {newer} (cascade invalidation)",
                           "superseded_by": newer}
    if not commit or commit == "none":
        return UNKNOWN, {**detail, "reason": "no code-state anchor (no witness commit)"}
    head_now = head(root)
    if head_now is None:
        return UNKNOWN, {**detail, "reason": "git unavailable (cannot establish the code state)"}
    if not scope_digest_enabled():
        # Commit-only classification (the operator switched scope_digest off,
        # §5.5): any movement at all — a new commit or a dirty work tree — is
        # treated as touching the claim. Deliberately noisy, deliberately
        # explicit, and never an accidental downgrade to CURRENT.
        if commit != head_now or (dirty_files(root) or []):
            moved = changed_since(root, commit) or []
            return STALE, {**detail, "changed": moved,
                           "reason": ("the code moved since the witness and "
                                      "[freshness] scope_digest is off "
                                      "(commit-only classification)")}
        return CURRENT, {**detail, "reason": "code state unchanged since the witness"}
    if commit == head_now and tree and tree != "none":
        # Same commit: the only way the code can still have moved is the dirty
        # work tree, and the stored tree digest answers that exactly.
        now_tree = tree_digest(root)
        if now_tree and now_tree != tree:
            moved = [p for p in (dirty_files(root) or [])
                     if not scope or scope_matches(scope, p)]
            if moved:
                return STALE, {**detail, "changed": moved,
                               "reason": "uncommitted edits inside the claim's scope"}
            return CURRENT, {**detail,
                             "reason": "work tree moved, but outside the claim's scope"}
        return CURRENT, {**detail, "reason": "code state unchanged since the witness"}
    changed = changed_since(root, commit)
    if changed is None:
        return UNKNOWN, {**detail, "reason": "cannot compare code states (git cannot read the witness commit)"}
    if len(changed) > max_digest_files():
        return UNKNOWN, {**detail,
                         "reason": (f"{len(changed)} files moved — past "
                                    f"[freshness] max_digest_files "
                                    f"({max_digest_files()}), so the code "
                                    f"state cannot be classified honestly")}
    if commit == head_now:
        return CURRENT, {**detail, "reason": "code state unchanged since the witness"}
    if not scope or scope.strip().lower() == "none":
        # An empty scope is only an answer when the claim actually HAS a
        # record: a measurement-only record authorizes no code, so nothing in
        # the tree can have staled it. A key that resolves to no record at all
        # has no anchor and reads UNKNOWN.
        if run_key and record_path(root, run_key) is not None:
            return CURRENT, {**detail, "changed": changed,
                             "reason": "the claim declares no code scope — nothing to stale"}
        return UNKNOWN, {**detail, "changed": changed,
                         "reason": "code moved, but the claim declares no scope to test against"}
    scoped = [p for p in changed if scope_matches(scope, p)]
    if scoped:
        return STALE, {**detail, "changed": scoped,
                       "reason": f"{len(scoped)} file(s) in the claim's scope changed since it was recorded"}
    return CURRENT, {**detail, "reason": "commits moved, none inside the claim's scope"}


def annotate(root: str, run_key: str, *, stored: dict | None = None,
             retired: bool = False, now: float | None = None) -> dict:
    """Freshness annotation for one claim, keyed to its own record.

    The anchor is the DECISION's code state, not the moment the signal was
    posted: when the run key resolves to a record, that record's last-touching
    commit dates the claim (a baton posted today citing a decision the code
    moved past yesterday is stale — dating it to the posting would make every
    baton infallibly fresh at birth and leave the plane empty). A stored
    witness still contributes its dirty-TREE digest, which is what catches an
    edit that was never committed; a claim whose key resolves to no record
    falls back to the stored witness (posting time), and to nothing (UNKNOWN)
    when neither exists.

    Returns ``{verdict, reason, age_s, changed, changed_count, run_key, anchor}``
    with anchor in ``record | stored | none``.
    """
    ref = now if now is not None else time.time()
    stored = stored if isinstance(stored, dict) and stored.get("commit") else None
    derived = derived_witness(root, run_key)
    if derived and derived.get("commit"):
        anchor = "record"
        commit = str(derived["commit"])
    elif stored:
        anchor = "stored"
        commit = str(stored["commit"])
    else:
        anchor = "none"
        commit = ""
    tree = str((stored or {}).get("tree", ""))
    # The record's own (token-bound) Code Scope is the matcher; a stored
    # witness only carries a digest of it, never the globs themselves.
    scope = record_scope(root, run_key)
    verdict, detail = classify(root, commit=commit, scope=scope, tree=tree,
                               run_key=run_key, retired=retired)
    ts = (stored or {}).get("ts") or (derived or {}).get("ts") or 0.0
    try:
        age_s = max(0.0, ref - float(ts)) if float(ts) else None
    except (TypeError, ValueError):
        age_s = None
    return {"verdict": verdict, "reason": detail.get("reason", ""),
            "age_s": age_s, "changed": detail.get("changed", []),
            "changed_count": len(detail.get("changed", [])),
            "run_key": run_key, "anchor": anchor,
            "superseded_by": detail.get("superseded_by")}


def key_of(text: str) -> str:
    """The run key a claim's text cites (`"E-048: …"` → `E-048`), or ''."""
    head_txt = str(text or "").split(":", 1)[0].strip()
    return head_txt if RUN_KEY_RE.match(head_txt) else ""


def label(verdict: str, age_s: float | None = None) -> str:
    """Short display form: ``STALE (1.8d)`` / ``CURRENT`` / ``UNKNOWN``."""
    verdict = str(verdict or CURRENT).upper()
    if age_s is None:
        return verdict
    try:
        seconds = float(age_s)
    except (TypeError, ValueError):
        return verdict
    if seconds >= 86400:
        return f"{verdict} ({seconds / 86400:.1f}d)"
    return f"{verdict} ({seconds / 3600:.1f}h)"


def tally(entries) -> dict:
    """``{CURRENT: n, STALE: n, UNKNOWN: n, RETIRED: n, total: n}``."""
    out = {CURRENT: 0, STALE: 0, UNKNOWN: 0, RETIRED: 0, "total": 0}
    for entry in entries or []:
        verdict = str((entry or {}).get("verdict", CURRENT)).upper()
        out[verdict] = out.get(verdict, 0) + 1
        out["total"] += 1
    return out


def reset_cache() -> None:
    """Drop the per-process caches (tests; also a git-state change of heart)."""
    _HEAD_CACHE.clear()
    _CHANGED_CACHE.clear()
    _DIRTY_CACHE.clear()
    _SUPERSEDE_CACHE.clear()

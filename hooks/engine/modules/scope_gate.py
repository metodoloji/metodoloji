"""Experiment-scope approval and structural declaration checks.

The public entrypoints remain on ``guard``; implementations bind shared gate
helpers there at call time so runtime behavior and test seams stay centralized.
"""

from __future__ import annotations

from typing import Any

def find_approved(target: str, recs_dir: str | None = None,
                  root: str = "") -> tuple[bool, str, dict | None]:
    """Find a VERIFIED record whose scope matches target.

    Returns ``(approved, detail, freshness)``. `freshness` is the derived
    verdict of the record that opened the door (None when the plane is off):
    under `[hooks] freshness = "deny"` a STALE approval does NOT unlock code,
    and under the default `"warn"` it unlocks with the reason attached so the
    caller can surface it — the write is never silently blessed by an approval
    the code has moved past (E-053). Whether a verdict blocks is decided in ONE
    place (`freshness.blocks`), so the write path, the commit gate and the
    deploy gate cannot disagree — UNKNOWN joins STALE there only when
    `[freshness] unknown_policy = "deny"`.
    """
    from . import guard as _guard
    _cached_verify = _guard._cached_verify
    _load_gate = _guard._load_gate
    _plugin_script_cmd = _guard._plugin_script_cmd
    _record_freshness = _guard._record_freshness
    norm_path = _guard.norm_path
    pathlib = _guard.pathlib
    record_scope_cached = _guard.record_scope_cached
    repo_root = _guard.repo_root
    if not _load_gate():
        return False, "gate script not available", None
    gate_mod = _guard.gate
    if gate_mod is None:
        return False, "gate script not available", None
    target_rel = norm_path(target).lstrip("/")
    if not root:
        root = repo_root({})
    recs_dir = recs_dir or "docs/experiments"
    if pathlib.PurePosixPath(recs_dir).is_absolute():
        return False, "absolute recs_dir rejected", None
    base = pathlib.Path(root) / recs_dir
    if not base.is_dir():
        return False, "docs/experiments/ not found", None
    key_missing = False
    best = None
    advisory = None
    advisory_msg = ("record {rec} is ADVISORY-BLOCKED (genuine token, "
                    "code stays closed: small sample, n unknown, or metric "
                    "mismatch — re-measure in a new record)")

    deferred = []
    scope_error = set()
    for rec in sorted(base.glob("*.md")):
        if rec.name == "_template.md":
            continue
        try:
            # Cheap scope pre-filter: cached file read, no gate key needed.
            candidate = gate_mod.scope_matches(record_scope_cached(str(rec)), target_rel)
        except Exception:
            candidate = False
            scope_error.add(rec)
        if not candidate:
            deferred.append(rec)
            continue
        rc, _scope = _cached_verify(str(rec))
        if rc == 3:
            key_missing = True
        elif rc == 2:
            if advisory is None:
                advisory = advisory_msg.format(rec=rec)
        elif rc == 0:
            fresh = _record_freshness(root, str(rec))
            # Local import (the engine imports freshness lazily): without it the
            # verdict comparison below raised NameError on every scope-matched
            # APPROVED record, so the write path crashed instead of warning.
            fr: Any = None
            try:
                from . import freshness as freshness_module
                fr = freshness_module
            except Exception:
                fr = None
            if fr and fresh and fresh["verdict"] in (fr.STALE, fr.UNKNOWN):
                try:
                    blocks = fr.blocks(fresh["verdict"])
                except Exception:
                    blocks = False
                if fresh["verdict"] == fr.STALE:
                    trouble = (
                        f"but the code has moved past it: {fresh['reason']} "
                        f"({fresh['changed'][:3] if fresh['changed'] else 'code state'} "
                        f"changed after approval"
                        f"{', witness ' + fresh['commit'][:12] if fresh['commit'] else ''}). ")
                else:
                    trouble = (f"but its code state cannot be established "
                               f"here: {fresh['reason']}. ")
                stale_msg = (
                    f"record {rec} is APPROVED and scope-matched, "
                    f"{trouble}"
                    f"Re-measure in a new E record (add a plain "
                    f"`- **Re-Measured-By:** E-NNN (date)` line to {rec.name}), or "
                    f"declare this file in the current record's plan "
                    f"(--amend-plan). "
                    f"[hooks] freshness = deny blocks stale; "
                    f"[freshness] unknown_policy = deny blocks unverifiable.")
                if blocks:
                    return False, stale_msg, fresh
                return True, f"record {rec} (scope matched)", fresh
            return True, f"record {rec} (scope matched)", fresh

    for rec in deferred:
        rc, _scope = _cached_verify(str(rec))
        if rc == 3:
            key_missing = True
        elif rc == 2:
            if advisory is None:
                advisory = advisory_msg.format(rec=rec)
        elif rc == 0 and rec not in scope_error and best is None:
            best = f"record {rec} scope not matched"

    if key_missing:
        return False, ("gate key not configured (" + _plugin_script_cmd(
            'skills/bmad-research-experiment/scripts/run_experiment.py',
            '--init-secret') + ")"), None
    # A VERIFIED record that simply does not cover this target is the ACCURATE
    # reason, and it must win over an advisory seeded by an unrelated record:
    # with E-001 ADVISORY-BLOCKED and E-002 VERIFIED, a write to `lib/x.py`
    # (outside BOTH scopes) was reported as "E-001 is ADVISORY-BLOCKED", which
    # sends the agent off to re-measure E-001 instead of widening the scope it
    # is actually missing. `advisory` still wins when no scope-mismatch exists,
    # i.e. for the write the advisory record DOES cover.
    if best:
        return False, best, None
    return False, advisory or "no approved experiment record", None

# Instrument paths stage BEFORE approval: the gate refuses to measure a bench
# in a free zone (scratch/tmp) and --run is the only measurement path, so a
# bench that could only be written post-approval would deadlock every first
# experiment under code_guard=hard. Staging is narrow: only scripts/bench/**
# targets, only under a well-formed UNDECIDED record whose Code Scope covers
# the bench path. Product code still requires a VERIFIED record — staging
# never unlocks apps/**, src/** or any other code target.
_INSTRUMENT_PREFIXES = ("scripts/bench/",)

def _is_instrument_path(rel: str) -> bool:
    """True when the project-relative path is a measurement bench target."""
    from . import guard as _guard
    _INSTRUMENT_PREFIXES = _guard._INSTRUMENT_PREFIXES
    norm_path = _guard.norm_path
    p = norm_path(rel or "").lstrip("/")
    return any(p.startswith(prefix) for prefix in _INSTRUMENT_PREFIXES)

def find_staged_instrument(target: str, root: str = "") -> tuple[bool, str]:
    """True when target is a bench staged by a well-formed undecided record.

    Well-formed = all REQUIRED_DRAFT fields present, Experiment id names a
    run, hypothesis claim parses, and no Decision written yet (placeholder
    or empty). The record's Code Scope must cover the bench path. Any
    unreadable record, parse failure or missing gate fails CLOSED (no allow).
    """
    from . import guard as _guard
    _load_gate = _guard._load_gate
    norm_path = _guard.norm_path
    pathlib = _guard.pathlib
    repo_root = _guard.repo_root
    if not _load_gate():
        return False, "gate script not available"
    gate_mod = _guard.gate
    if gate_mod is None:
        return False, "gate script not available"
    try:
        target_rel = norm_path(target).lstrip("/")
        if not root:
            root = repo_root({})
        base = pathlib.Path(root) / "docs" / "experiments"
        if not base.is_dir():
            return False, "docs/experiments/ not found"
        for rec in sorted(base.glob("*.md")):
            if rec.name == "_template.md":
                continue
            try:
                text = rec.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            try:
                fields = gate_mod.record_fields(text)
                if any(not fields.get(need) for need in gate_mod.REQUIRED_DRAFT):
                    continue
                if gate_mod.experiment_id_issue(text) is not None:
                    continue
                gate_mod.hypothesis_claim(fields.get("Hypothesis", ""))
                decision = (fields.get("Decision", "") or "").strip()
                if decision and not gate_mod._is_placeholder(decision):
                    continue  # decided — the VERIFIED path owns it, not staging
                if not gate_mod.scope_matches(gate_mod.record_scope(str(rec)), target_rel):
                    continue
            except (ValueError, AttributeError, TypeError):
                continue
            except Exception:
                continue
            return True, f"staged instrument for undecided {rec.name} (scope matched)"
    except Exception:
        return False, "staged-instrument check failed"
    return False, "no undecided record stages this bench"

_STRUCTURAL_DIR = ("docs", "development", "structural")
_STRUCTURAL_MIN_JUSTIFICATION = 40

def _struct_field(text: str, name: str) -> str:
    from . import guard as _guard
    re = _guard.re
    m = re.search(rf"^-\s*\*\*{re.escape(name)}:\*\*\s*(.+)$", text, re.M)
    return m.group(1).strip() if m else ""

def find_structural_declaration(target: str, root: str = "") -> tuple[bool, str]:
    """True when a Structural Scope record declares this path.

    The honest third category. The guard knew two kinds of code: product code
    (needs a VERIFIED experiment) and measurement benches (stage under an
    undecided record). Code that carries no empirical claim had neither — a
    scaffold, a CI workflow, a layout shell, a wiring module. The only way past
    was to invent a hypothesis, and a fabricated experiment is a worse outcome
    than an admitted exemption: a subagent in a consuming project did exactly
    that, writing a fake E-001 to obtain permission to save an HTML file.

    A structural record says, in public: this code makes no claim that could be
    measured, here is why, and a QR must ratify it. Deliberately narrow:

      - never covers scripts/bench/** — a bench needs a real experiment, and
        this path must not become a way around the instrument rule;
      - Status must be 'declared' or 'ratified'; anything else is ignored;
      - a Scope glob and a justification of at least 40 characters are both
        required, because an unexplained exemption is the abuse case;
      - an approved record wins: this check is only reached when no approval
        covered the path, so it can never downgrade a measured claim;
      - every write warns, so the exemption is never invisible in a session.
    """
    from . import guard as _guard
    _STRUCTURAL_DIR = _guard._STRUCTURAL_DIR
    _STRUCTURAL_MIN_JUSTIFICATION = _guard._STRUCTURAL_MIN_JUSTIFICATION
    _struct_field = _guard._struct_field
    norm_path = _guard.norm_path
    pathlib = _guard.pathlib
    re = _guard.re
    repo_root = _guard.repo_root
    _load_gate = _guard._load_gate
    try:
        if not _load_gate() or _guard.gate is None:
            return False, "gate script not available"
        gate_mod = _guard.gate
        if not root:
            root = repo_root({})
        base = pathlib.Path(root).joinpath(*_STRUCTURAL_DIR)
        if not base.is_dir():
            return False, "no structural declarations"
        target_rel = norm_path(target).lstrip("/")
        if not target_rel:
            return False, "empty target"
        for rec in sorted(base.glob("*.md")):
            if rec.name.startswith("_"):
                continue
            try:
                text = rec.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            status = _struct_field(text, "Status").lower()
            if status not in ("declared", "ratified"):
                continue
            if len(_struct_field(text, "Justification")) < _STRUCTURAL_MIN_JUSTIFICATION:
                continue
            scope = _struct_field(text, "Scope").replace("`", "")
            patterns = [p for p in re.split(r"[,\s]+", scope) if p]
            for pat in patterns:
                try:
                    if gate_mod.scope_matches(pat, target_rel):
                        return True, (f"structural scope declared in {rec.name} "
                                      f"(status: {status}, unmeasured)")
                except Exception:
                    continue
    except Exception:
        return False, "structural check failed"
    return False, "no structural declaration covers this path"

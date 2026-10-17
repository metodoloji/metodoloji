"""Evidence witnesses — derive, never trust, whether a completed stage's proof
still holds.

The workflow kernel checks a stage's evidence exactly ONCE, at `complete`
time. After that the run's state says `completed` and `next` keeps computing
forward, so a foundation the workspace has moved past — the artifact deleted,
the declared token removed, the glob match gone — is still delivered as
satisfied work. That is the same failure E-052/E-053 closed on the board plane
("yesterday's decision is still in the handoff file, and the agent obeys it
after the code has already moved on"), one plane over: the plan marked it as
F-13 and did not own it.

Design rules (E-056, carrying E-053's doctrine onto this plane):

1. **A completion carries a witness.** When an `artifact` stage completes, the
   predicate that satisfied it (`path`, `contains`, `min`) is recorded in the
   run's state. That is the claim the completion makes.
2. **Freshness is derived, never stored.** Nothing in state says "still
   satisfied"; :func:`verify` recomputes the answer from the artifact on disk
   every time a read surface asks.
3. **Three-valued verdict** — ``CURRENT | STALE | UNKNOWN``. A stage that
   completed before witnesses existed reads UNKNOWN, never a silent CURRENT; an
   artifact that exists but cannot be read is UNKNOWN, not a false STALE.
4. **Predicate-sensitive, not digest-sensitive.** The claim is "this artifact
   carries the declared tokens", so an edit that keeps them stays CURRENT. A
   digest-only design would call every later edit stale — noise, not signal —
   which is why the bench's paired false-alarm control rejects it.
5. **Only code-state claims are classified.** ``command`` evidence ("this argv
   exited 0") and ``note`` evidence are recorded history, not claims about the
   workspace; they are reported as unwitnessed rather than mislabelled.
6. **Fail-open on the read path.** Nothing here raises: a missing witness, a
   missing spec stage or an unreadable artifact degrades to a named verdict.
7. **The re-derivation read is BOUNDED (E-058).** This plane runs on the
   session edge (the advisory is computed at SessionStart), so the read side
   refuses to pay for an artifact it cannot afford to re-derive: past
   :data:`MAX_ARTIFACT_BYTES` per candidate or :data:`MAX_CANDIDATES` glob
   matches, the answer is UNKNOWN with the reason — the same doctrine as
   ``[freshness] max_digest_files`` on the board plane (E-053 §5.5). A POSITIVE
   answer stays exact (a match found inside the bound really is a match); only
   the negative side degrades, so the bound can never invent a STALE. The
   COMPLETION path is deliberately unbounded: accepting a stage reads the
   artifact the operator just produced, once.
"""

from __future__ import annotations

import glob as globmod
import os
import pathlib
import time

from . import spec as spec_mod

CURRENT = "CURRENT"
STALE = "STALE"
UNKNOWN = "UNKNOWN"

ARTIFACT = "artifact"

_GLOB_CHARS = ("*", "?", "[")
MAX_CONTAINS = 32  # bounded: a predicate is a token list, never a document

# The read budget of the RE-DERIVATION path (E-058). The completion path is
# unbounded on purpose: it reads the artifact the operator just produced, once,
# which is the price of accepting the work. Every later read (a status view, the
# computed next step, the run inventory, the SessionStart advisory) runs on this
# budget, so a spec cannot make opening a session scan a directory tree.
MAX_ARTIFACT_BYTES = 4 * 1024 * 1024
MAX_CANDIDATES = 200


def _contains(ev: dict) -> list[str]:
    return [str(t) for t in (ev.get("contains") or [])][:MAX_CONTAINS]


def _minimum(ev: dict) -> int:
    try:
        return max(1, int(ev.get("min", 1)))
    except (TypeError, ValueError):
        return 1


def artifact_matches(project_root: str, path_pattern: str,
                     contains: list[str], minimum: int, *,
                     bounded: bool = False) -> tuple[bool | None, str]:
    """Re-apply one artifact predicate. Returns ``(ok, detail)``.

    ``ok`` is True (the predicate holds), False (it does not) or None (it
    cannot be established — an unreadable candidate, or one the read budget put
    out of reach). The third result is how UNKNOWN reaches the caller:
    "unreadable" and "unaffordable" must never be reported as "satisfied" or
    as a definite "no".

    ``bounded=True`` enforces the re-derivation budget (rule 7): at most
    :data:`MAX_CANDIDATES` candidates are scanned and no candidate past
    :data:`MAX_ARTIFACT_BYTES` is read. A match found inside the budget answers
    True exactly as before; when no match was found, anything that was skipped
    makes the answer None instead of False.
    """
    raw_path = str(path_pattern or "").strip()
    if not raw_path:
        return False, "artifact evidence has no path"
    full_pattern = os.path.join(project_root, raw_path)
    if any(ch in raw_path for ch in _GLOB_CHARS):
        matches = [p for p in globmod.glob(full_pattern, recursive=True)
                   if os.path.isfile(p)]
        if not matches:
            return False, f"artifact not found: {raw_path}"
        candidates = sorted(matches, key=lambda p: (os.path.getmtime(p), p),
                            reverse=True)
    else:
        if not os.path.isfile(full_pattern):
            return False, f"artifact not found: {raw_path}"
        candidates = [full_pattern]

    tokens = _contains({"contains": contains})
    if not tokens:
        return True, f"artifact {raw_path} ok"
    want = _minimum({"min": minimum})

    truncated = 0
    if bounded and len(candidates) > MAX_CANDIDATES:
        # Newest-first (the ordering above), so the candidates most likely to be
        # the live artifact are the ones inside the budget.
        truncated = len(candidates) - MAX_CANDIDATES
        candidates = candidates[:MAX_CANDIDATES]

    unreadable = 0
    oversized = 0
    for cand in candidates:
        if bounded:
            try:
                if os.path.getsize(cand) > MAX_ARTIFACT_BYTES:
                    oversized += 1
                    continue
            except OSError:
                unreadable += 1
                continue
        try:
            text = pathlib.Path(cand).read_text(encoding="utf-8", errors="replace")
        except OSError:
            unreadable += 1
            continue
        hits = sum(text.count(tok) for tok in tokens)
        if hits >= want:
            return True, f"artifact {os.path.basename(cand)} ok"

    # No match inside the budget. Anything skipped could still have carried the
    # evidence, so the honest answer is "cannot establish", never a false STALE.
    if truncated:
        return None, (f"artifact {raw_path}: {truncated} further match(es) past "
                      f"the {MAX_CANDIDATES}-file scan budget — the evidence "
                      f"cannot be re-derived within the read budget "
                      f"(narrow the path pattern)")
    if oversized:
        return None, (f"artifact {raw_path}: {oversized} candidate(s) exceed the "
                      f"{MAX_ARTIFACT_BYTES}-byte read budget — the evidence "
                      f"cannot be re-derived within budget (split the artifact, "
                      f"or narrow the path pattern)")
    if unreadable == len(candidates):
        return None, f"artifact {raw_path} cannot be read (cannot establish its evidence)"
    return False, (f"artifact {raw_path} has no candidate matching {tokens} "
                   f"(need >= {want})")


def witness_for(stage: dict, *, now: float | None = None) -> dict | None:
    """The witness to record when a stage completes on artifact evidence.

    None for every other evidence kind: a `command` run and a `--note` are
    history, not a claim about the workspace, so there is nothing to re-derive.
    """
    ev = (stage or {}).get("evidence") or {}
    if ev.get("kind") != ARTIFACT:
        return None
    return {"kind": ARTIFACT, "path": str(ev.get("path", "")),
            "contains": _contains(ev), "min": _minimum(ev),
            "ts": time.time() if now is None else now}


def classify(project_root: str, witness: dict) -> tuple[str, dict]:
    """Three-valued freshness of one recorded completion. Never raises."""
    w = witness if isinstance(witness, dict) else {}
    path = str(w.get("path", ""))
    detail: dict = {"reason": "", "path": path, "detail": ""}
    if w.get("kind") != ARTIFACT or not path.strip():
        return UNKNOWN, {**detail,
                         "reason": ("no evidence witness — this stage completed "
                                    "before completions were dated (E-056)")}
    ok, message = artifact_matches(project_root, path,
                                   [str(t) for t in (w.get("contains") or [])],
                                   _minimum(w), bounded=True)
    detail["detail"] = message
    if ok is True:
        return CURRENT, {**detail,
                         "reason": "its artifact still carries the recorded evidence"}
    if ok is False:
        return STALE, {**detail, "reason": message}
    return UNKNOWN, {**detail, "reason": message}


def verify(project_root: str, state: dict, spec: dict) -> dict:
    """Derived evidence health of a run's completed artifact stages (read-only).

    Returns ``{"stages": {id: {verdict, reason, path, detail}}, "tally": {...},
    "stale": [ids], "unknown": [ids]}``. Only `artifact` evidence is classified
    (rule 5); a stage whose current spec switched away from artifact evidence
    but still carries a witness keeps being classified, because the witness —
    not the spec — is the claim that was made.
    """
    witnesses = (state or {}).get("evidence") or {}
    out: dict[str, dict] = {}
    tally = {CURRENT: 0, STALE: 0, UNKNOWN: 0, "total": 0}
    stale: list[str] = []
    unknown: list[str] = []
    for stage_id in (state or {}).get("completed", []) or []:
        witness = witnesses.get(stage_id)
        stage = spec_mod.stage_by_id(spec, stage_id) if spec else None
        spec_kind = (((stage or {}).get("evidence") or {}).get("kind"))
        if not witness and spec_kind != ARTIFACT:
            continue
        verdict, detail = classify(project_root, witness or {})
        out[stage_id] = {"verdict": verdict, **detail}
        tally[verdict] = tally.get(verdict, 0) + 1
        tally["total"] += 1
        if verdict == STALE:
            stale.append(stage_id)
        elif verdict == UNKNOWN:
            unknown.append(stage_id)
    return {"stages": out, "tally": tally, "stale": stale, "unknown": unknown}


def foundation_of(state: dict, stage: dict) -> list[str]:
    """The completed stages an advance rests on: its declared `requires`.

    A spec that declares no `requires` for this stage makes the whole completed
    chain the foundation — a run is a chain by default, and the refusal below
    is scoped to the stages the kernel itself knows must come first.
    """
    required = [str(r) for r in (stage.get("requires") or [])]
    if required:
        return required
    return [str(s) for s in (state or {}).get("completed", []) or []]

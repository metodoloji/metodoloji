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
5. **Only re-derivable claims are classified, and everything else says so.**
   ``note`` evidence is a statement, not a claim about the workspace, so it
   carries no verdict. ``command`` evidence ("this argv exited 0") cannot be
   re-derived by looking, which is why leaving it UNCLASSIFIED was the wrong
   answer: silence delivered a green suite as satisfied forever (E-063). It is
   classified now, by comparison against the code state it was run in (rule 9).
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
8. **The read never leaves the project root (E-060).** A run's evidence is a
   claim about ITS OWN project, so an `artifact` path that resolves outside
   :func:`root_of` — a `..` traversal, an absolute path, a symlink out — is not
   re-derived at all: the read surfaces answer UNKNOWN ("outside the project
   root") and the completion path refuses, naming the cause. Containment lives
   HERE, in the one matcher, for the same reason the matcher itself is shared
   (E-056): a second check in spec validation or in each caller would be a
   second opinion on the same predicate, and a witness already stored — written
   by an older tree, or edited by hand — would still be read by whoever did not
   think to duplicate it. Refusing the WRITE is the completion path's job (it is
   a gate); reporting "cannot establish" is the READ path's job (it is a view),
   so the two surfaces keep the asymmetry E-058 gave them.
9. **A command completion is dated to a code state (E-064).** ``command``
   evidence is the one claim this plane cannot re-derive by looking, and until
   E-063 the plane answered that by silence: a completed stage whose proof was
   a green suite read exactly like one that passed against the current code.
   Now the completion records the TRACKED code state the command ran in, and a
   read compares it: unchanged answers CURRENT ("recorded against this code
   state"), moved answers UNKNOWN naming both states, and a project that cannot
   be dated at all (no git, no commit yet, git unavailable) answers UNKNOWN with
   the reason. It never answers STALE — a command's outcome is not falsified by
   a look — so the dating never blocks an advance. The probe is ONE bounded call
   per read pass (:class:`CodeStateProbe`), because the inventory classifies
   every run and an inventory must not cost a subprocess per run; it is lazy, so
   a project whose completions are all artifact evidence pays nothing. Only
   TRACKED files are inside the fingerprint, which the verdict's wording says.
"""

from __future__ import annotations

import glob as globmod
import hashlib
import os
import pathlib
import subprocess
import time

from . import spec as spec_mod
# Containment lives in ONE module both planes call (E-062). `root_of` is
# re-exported because this module's rule 8 documents it as the boundary of the
# evidence read.
from .paths import outside as _outside  # noqa: F401  (used below)
from .paths import root_of  # noqa: F401  (documented re-export)

CURRENT = "CURRENT"
STALE = "STALE"
UNKNOWN = "UNKNOWN"

ARTIFACT = "artifact"
COMMAND = "command"

_GLOB_CHARS = ("*", "?", "[")
# One bounded probe of the code state per read pass (rule 9). A `git status` on a
# large work tree is not free and this plane runs on the session edge, so the
# probe has a timeout and a failure (no git, no repository, an old git without
# `--porcelain=v2`) answers "cannot establish", never a guess.
CODE_STATE_TIMEOUT = 5


class _Unset:
    """Sentinel: distinct from None, which is a real "cannot establish" answer."""


_UNSET = _Unset()
MAX_CONTAINS = 32  # bounded: a predicate is a token list, never a document

# The read budget of the RE-DERIVATION path (E-058). The completion path is
# unbounded on purpose: it reads the artifact the operator just produced, once,
# which is the price of accepting the work. Every later read (a status view, the
# computed next step, the run inventory, the SessionStart advisory) runs on this
# budget, so a spec cannot make opening a session scan a directory tree.
MAX_ARTIFACT_BYTES = 4 * 1024 * 1024
MAX_CANDIDATES = 200


def code_state(project_root: str) -> str | None:
    """A short digest of the project's TRACKED code state, or None (rule 9).

    One bounded call: `git status --porcelain=v2 --branch` reports the commit
    HEAD names AND the tracked working-tree changes, so a completion made on a
    dirty tree is dated to that dirty state rather than to a commit it never had.
    Untracked files are deliberately outside the fingerprint (a run's own
    `.metodoloji/` state is untracked, and a benchmark's scratch files should not
    make a completion look moved), which is why every verdict worded from this
    says "tracked".
    """
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain=v2", "--untracked-files=no", "--branch"],
            cwd=project_root or ".", capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=CODE_STATE_TIMEOUT, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    oid = ""
    changes: list[str] = []
    for line in (proc.stdout or "").splitlines():
        if line.startswith("# branch.oid "):
            oid = line.split(" ", 2)[2].strip()
        elif line.startswith("#"):
            continue
        elif line.strip():
            changes.append(line.strip())
    if not oid or oid.startswith("("):  # unborn branch: nothing to date against
        return None
    digest = hashlib.sha256("\n".join(changes).encode("utf-8")).hexdigest()[:12]
    return f"{oid[:12]}.{digest}"


class CodeStateProbe:
    """One code-state probe per read pass, shared across the runs it classifies.

    Lazy on purpose: a project whose completions are all artifact evidence never
    pays for a subprocess, and `list` (which verifies every run) pays once rather
    than once per run.
    """

    def __init__(self, project_root: str) -> None:
        self._root = project_root
        self._value: object = _UNSET

    def get(self) -> str | None:
        if isinstance(self._value, _Unset):
            self._value = code_state(self._root)
        return self._value  # type: ignore[return-value]

    # Callable so a caller can hand the probe itself to `verify`/`classify`; the
    # resolution there only needs "something that answers the code state".
    __call__ = get


def _resolve_code_state(state_now, project_root: str) -> str | None:
    """Resolve the probe argument into a value (rule 9)."""
    if isinstance(state_now, _Unset):
        return code_state(project_root)
    if callable(state_now):
        return state_now()
    return state_now


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

    Containment (rule 8) is applied on both sides of the glob: a DECLARED path
    that leaves the root is refused before anything is read, and a candidate
    that resolves outside it (a symlink, or a glob prefix that traverses) is
    skipped and counted, so a skipped candidate turns the negative answer into
    None exactly like the read budget does — the plane reports what it could not
    establish, never a STALE it did not verify.
    """
    raw_path = str(path_pattern or "").strip()
    if not raw_path:
        return False, "artifact evidence has no path"
    root_real = root_of(project_root)
    full_pattern = os.path.join(project_root, raw_path)
    if _outside(root_real, full_pattern):
        return None, (f"artifact path is outside the project root: {raw_path} — "
                      f"evidence may only name an artifact inside {root_real}")
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
    escaped = 0
    for cand in candidates:
        if _outside(root_real, cand):
            # Symlinks and traversing glob prefixes land here. Counting them
            # keeps the negative answer honest (rule 8 + rule 7's asymmetry).
            escaped += 1
            continue
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
    if escaped:
        return None, (f"artifact {raw_path}: {escaped} candidate(s) are outside "
                      f"the project root {root_real} — not read (evidence may "
                      f"only name artifacts inside it)")
    if unreadable == len(candidates):
        return None, f"artifact {raw_path} cannot be read (cannot establish its evidence)"
    return False, (f"artifact {raw_path} has no candidate matching {tokens} "
                   f"(need >= {want})")


def witness_for(stage: dict, *, now: float | None = None,
                project_root: str = "") -> dict | None:
    """The witness to record when a stage completes on re-derivable evidence.

    `artifact` records the predicate that satisfied it (E-056); `command` records
    the argv, the expected exit code and the TRACKED code state it ran in (rule
    9, E-064 — the state is what makes a command's result comparable later).
    `note` still records nothing: a statement is not a claim about the workspace.
    """
    ev = (stage or {}).get("evidence") or {}
    kind = ev.get("kind")
    if kind == ARTIFACT:
        return {"kind": ARTIFACT, "path": str(ev.get("path", "")),
                "contains": _contains(ev), "min": _minimum(ev),
                "ts": time.time() if now is None else now}
    if kind == COMMAND:
        target = project_root or "."
        return {"kind": COMMAND, "argv": [str(a) for a in (ev.get("argv") or [])],
                "rc": int(ev.get("rc", 0)),
                "code_state": code_state(target),
                "ts": time.time() if now is None else now}
    return None


def classify(project_root: str, witness: dict, *,
             code_state_now: object = _UNSET) -> tuple[str, dict]:
    """Three-valued freshness of one recorded completion. Never raises.

    ``artifact`` witnesses are re-derived from the file (rules 3-8); ``command``
    witnesses are compared against the tracked code state (rule 9), which is why
    this function takes the pass's probe as an argument: resolving it here would
    make every classified stage pay a subprocess.
    """
    w = witness if isinstance(witness, dict) else {}
    kind = w.get("kind")
    if kind == COMMAND:
        return _classify_command(project_root, w, code_state_now)
    path = str(w.get("path", ""))
    detail: dict = {"reason": "", "path": path, "detail": ""}
    if kind != ARTIFACT or not path.strip():
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


def _classify_command(project_root: str, w: dict,
                      code_state_now: object = _UNSET) -> tuple[str, dict]:
    """A command completion compared against the code state it was run in.

    Three answers, none of them STALE: the tracked state is the recorded one
    (CURRENT), it moved (UNKNOWN, naming both states), or it cannot be
    established at all (UNKNOWN with the reason). A look cannot falsify "this
    argv exited 0", so this plane never pretends it did — and `complete` never
    refuses an advance over it.
    """
    argv = " ".join(str(a) for a in (w.get("argv") or []))[:120]
    detail: dict = {"reason": "", "path": "", "detail": f"command `{argv}` rc={w.get('rc', 0)}"}
    if "code_state" not in w:
        return UNKNOWN, {**detail,
                         "reason": ("its command was recorded before "
                                    "completions were dated to a code state "
                                    "(E-064) — re-complete the stage to date it")}
    recorded = w.get("code_state")
    if not recorded:
        return UNKNOWN, {**detail,
                         "reason": ("no code state could be established when its "
                                    "command was recorded (not a git work tree, "
                                    "no commit yet, or git unavailable) — the "
                                    "command's result cannot be re-derived")}
    now = _resolve_code_state(code_state_now, project_root)
    if now is None:
        return UNKNOWN, {**detail,
                         "reason": ("the tracked code state cannot be "
                                    "established here (not a git work tree, no "
                                    "commit yet, or git unavailable) — the "
                                    "command's result cannot be re-derived")}
    if str(recorded) == str(now):
        return CURRENT, {**detail,
                         "reason": (f"its command was recorded against this "
                                    f"tracked code state ({recorded}), which is "
                                    f"unchanged")}
    return UNKNOWN, {**detail,
                     "reason": (f"the tracked code state moved since this "
                                f"command was recorded ({recorded} -> {now}) — "
                                f"re-complete the stage to run it again")}


def verify(project_root: str, state: dict, spec: dict, *,
           code_state_now: object = _UNSET) -> dict:
    """Derived evidence health of a run's completed stages (read-only).

    Returns ``{"stages": {id: {verdict, reason, path, detail}}, "tally": {...},
    "stale": [ids], "unknown": [ids]}``. `artifact` stages are re-derived from
    the file and `command` stages are compared against the tracked code state
    (rules 3-9); a stage whose current spec switched away from both but still
    carries a witness keeps being classified, because the witness — not the spec
    — is the claim that was made. ``code_state_now`` lets one read pass share its
    probe across every run it classifies (pass a :class:`CodeStateProbe`'s bound
    `get`, or a value); omitted, the pass probes once itself.
    """
    witnesses = (state or {}).get("evidence") or {}
    out: dict[str, dict] = {}
    tally = {CURRENT: 0, STALE: 0, UNKNOWN: 0, "total": 0}
    stale: list[str] = []
    unknown: list[str] = []
    probe: object = _UNSET
    if isinstance(code_state_now, _Unset):
        probe = CodeStateProbe(project_root)
    else:
        probe = code_state_now
    for stage_id in (state or {}).get("completed", []) or []:
        witness = witnesses.get(stage_id)
        stage = spec_mod.stage_by_id(spec, stage_id) if spec else None
        spec_kind = (((stage or {}).get("evidence") or {}).get("kind"))
        if not witness and spec_kind not in (ARTIFACT, COMMAND):
            continue
        verdict, detail = classify(project_root, witness or {},
                                   code_state_now=probe)
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

"""Workflow spec — schema, validation, and deterministic transition computation.

A workflow is DECLARED data, never model improvisation. The model authors the
spec from the user's intent (that part is creative); everything after is
mechanical: which stage is current, whether it may start, whether its evidence
is real, and which stage comes next are all computed from the spec plus the
recorded state. That is the property that makes a multi-session process
"correct and stable" instead of "usually remembered".

Spec shape (JSON)::

    {
      "id": "seo-visibility",
      "version": 1,
      "title": "SEO visibility + performance",
      "intent": "one paragraph: what this workflow is for",
      "artifact": "sorunlar.md",          # optional durable source of truth
      "start": "analyze",
      "stages": [
        {"id": "analyze", "title": "...", "goal": "...",
         "actions": ["what the executor must do"],
         "evidence": {"kind": "artifact", "path": "sorunlar.md",
                      "contains": ["ISSUE-"], "min": 1},
         "next": [{"to": "map"}]},
        {"id": "map", "title": "...", "requires": ["analyze"], "next": [{"to": "plan"}]},
        {"id": "plan", "title": "...", "next": [{"to": "apply"}]},
        {"id": "apply", "title": "...", "next": [{"to": "test"}]},
        {"id": "test", "title": "...",
         "next": [{"to": "apply", "when": {"key": "regressed", "equals": "true"}},
                  {"to": "done"}]},
        {"id": "done", "title": "Complete", "terminal": true}
      ]
    }

Condition grammar (``when``), deliberately tiny and total:

    {"key": "<flag>", "equals": "<string>"}       # flag value == string
    {"all": [<cond>, ...]}                          # every condition holds
    {"any": [<cond>, ...]}                          # at least one holds
    {} / null                                       # always true

A conditional edge whose key held is "consumed": the engine clears that key
after taking it, so an unset flag falls through to the default edge next time
and a branch can never loop forever on a stale value.
"""

from __future__ import annotations

import json
import pathlib
import re

EVIDENCE_KINDS = ("artifact", "command", "note")
CLEARED_FLAG_VALUE = ""

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
# Bridge prefixes (e.g. "E-", "IR-") may use uppercase stage abbreviations.
_BRIDGE_RE = re.compile(r"^[A-Za-z0-9]+-?$")


class SpecError(ValueError):
    """A spec is malformed or internally inconsistent."""


def load(path: str | pathlib.Path) -> dict:
    """Read a JSON spec, validate it, and return it (raises SpecError)."""
    p = pathlib.Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SpecError(f"cannot read spec {p}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SpecError(f"spec {p} is not valid JSON: {exc}") from exc
    problems = validate(data)
    if problems:
        raise SpecError("; ".join(problems))
    return data


def stage_by_id(spec: dict, stage_id: str) -> dict | None:
    for st in spec.get("stages", []):
        if st.get("id") == stage_id:
            return st
    return None


def spec_id(spec: dict) -> str:
    return str(spec.get("id", "")).strip()


def validate(spec: dict) -> list[str]:
    """Return a list of structural problems; empty means the spec is runnable."""
    problems: list[str] = []
    if not isinstance(spec, dict):
        return ["spec is not an object"]

    sid = spec.get("id")
    if not sid or not isinstance(sid, str) or not _ID_RE.match(sid):
        problems.append("missing or malformed 'id' (lowercase [a-z0-9._-])")
    if not str(spec.get("title", "")).strip():
        problems.append("missing 'title'")

    stages = spec.get("stages")
    if not isinstance(stages, list) or not stages:
        problems.append("'stages' must be a non-empty list")
        return problems

    ids: list[str] = []
    for i, st in enumerate(stages):
        if not isinstance(st, dict):
            problems.append(f"stage[{i}] is not an object")
            continue
        st_id = st.get("id")
        if not st_id or not isinstance(st_id, str) or not _ID_RE.match(st_id):
            problems.append(f"stage[{i}] has a missing/malformed id")
            continue
        if st_id in ids:
            problems.append(f"duplicate stage id '{st_id}'")
        ids.append(st_id)
        if not str(st.get("title", "")).strip():
            problems.append(f"stage '{st_id}' has no title")

        bridge = st.get("bridge")
        if bridge is not None and not isinstance(bridge, str):
            problems.append(f"stage '{st_id}': bridge must be a string")
        elif bridge is not None and not _BRIDGE_RE.match(str(bridge)):
            problems.append(f"stage '{st_id}': bridge must be a prefix like 'E-' or 'IR-' ({bridge!r})")

        ev = st.get("evidence")
        if ev is not None:
            if not isinstance(ev, dict):
                problems.append(f"stage '{st_id}': evidence must be an object")
            else:
                kind = ev.get("kind")
                if kind not in EVIDENCE_KINDS:
                    problems.append(
                        f"stage '{st_id}': evidence.kind must be one of {EVIDENCE_KINDS}")
                if kind == "artifact" and not str(ev.get("path", "")).strip():
                    problems.append(f"stage '{st_id}': artifact evidence needs 'path'")
                if kind == "artifact":
                    try:
                        min_val = int(ev.get("min", 1))
                        if min_val < 1:
                            problems.append(
                                f"stage '{st_id}': evidence.min must be >= 1 (got {min_val})")
                    except (TypeError, ValueError):
                        problems.append(
                            f"stage '{st_id}': evidence.min must be an integer")
                if kind == "command" and not ev.get("argv"):
                    problems.append(f"stage '{st_id}': command evidence needs 'argv' (list)")
        elif not st.get("terminal"):
            # Evidence is how a stage is proven done; a non-terminal stage with
            # none would let `complete` advance on nothing — reject it.
            problems.append(f"stage '{st_id}' has no 'evidence' (use kind 'note' for a statement)")

    id_set = set(ids)
    start = spec.get("start")
    if not start or start not in id_set:
        problems.append(f"'start' must name a stage ({start!r} is not one)")

    for st in stages:
        if not isinstance(st, dict) or st.get("id") not in id_set:
            continue
        st_id = st["id"]
        for req in st.get("requires", []) or []:
            if req not in id_set:
                problems.append(f"stage '{st_id}': requires unknown stage '{req}'")
        nxt = st.get("next", [])
        if st.get("terminal"):
            if nxt:
                problems.append(f"terminal stage '{st_id}' must not declare 'next'")
            continue
        if not isinstance(nxt, list) or not nxt:
            problems.append(f"non-terminal stage '{st_id}' has no 'next' edge")
            continue
        for j, edge in enumerate(nxt):
            if not isinstance(edge, dict):
                problems.append(f"stage '{st_id}': next[{j}] is not an object")
                continue
            to = edge.get("to")
            if to in (None, "end"):
                continue
            if to not in id_set:
                problems.append(f"stage '{st_id}': next[{j}].to names unknown stage '{to}'")
            when = edge.get("when")
            if when and not _valid_when(when):
                problems.append(f"stage '{st_id}': next[{j}].when is malformed")

    # Reachability: an unreachable stage is dead weight the executor can never
    # enter, so the spec does not do what it looks like it does.
    reachable = _reachable(spec, start)
    for st_id in ids:
        if st_id not in reachable:
            problems.append(f"stage '{st_id}' is unreachable from start '{start}'")
    return problems


def _valid_when(when) -> bool:
    if when in (None, {}):
        return True
    if not isinstance(when, dict):
        return False
    if "key" in when:
        return "equals" in when and isinstance(when["key"], str)
    if "all" in when:
        return isinstance(when["all"], list) and all(_valid_when(c) for c in when["all"])
    if "any" in when:
        return isinstance(when["any"], list) and all(_valid_when(c) for c in when["any"])
    return False


def _reachable(spec: dict, start: str) -> set[str]:
    seen: set[str] = set()
    stack = [start]
    by_id = {st.get("id"): st for st in spec.get("stages", []) if isinstance(st, dict)}
    while stack:
        sid = stack.pop()
        if sid in seen or sid not in by_id:
            continue
        seen.add(sid)
        for edge in (by_id[sid].get("next") or []):
            to = edge.get("to")
            if to and to != "end":
                stack.append(to)
    return seen


def evaluate_when(when, ctx: dict) -> bool:
    """Evaluate a ``when`` condition against a flat {flag: value} context."""
    if when in (None, {}):
        return True
    if not isinstance(when, dict):
        return False
    if "key" in when:
        return str(ctx.get(when["key"], "")) == str(when.get("equals", ""))
    if "all" in when:
        return all(evaluate_when(c, ctx) for c in when["all"])
    if "any" in when:
        return any(evaluate_when(c, ctx) for c in when["any"])
    return False


def condition_keys(when) -> list[str]:
    """Flag names a ``when`` reads (used to clear consumed branch flags)."""
    out: list[str] = []
    if not isinstance(when, dict):
        return out
    if "key" in when:
        out.append(str(when["key"]))
    for sub in ("all", "any"):
        for c in when.get(sub, []) or []:
            out.extend(condition_keys(c))
    return out


def compute_next(spec: dict, *, current: str, completed: set[str],
                 ctx: dict) -> dict:
    """Deterministically decide where the run goes after ``current``.

    Returns one of:
        {"decision": "advance", "stage": <id>, "cleared": [keys]}
        {"decision": "end"}
        {"decision": "blocked", "reason": "<why>"}
    """
    stage = stage_by_id(spec, current)
    if stage is None:
        return {"decision": "blocked", "reason": f"unknown current stage {current!r}"}
    if stage.get("terminal"):
        return {"decision": "end"}
    edges = stage.get("next") or []
    for edge in edges:
        to = edge.get("to")
        if to in (None, "end"):
            return {"decision": "end"}
        when = edge.get("when")
        if not evaluate_when(when, ctx):
            continue
        return {"decision": "advance", "stage": to,
                "cleared": condition_keys(when)}
    return {"decision": "blocked",
            "reason": f"no 'next' edge from '{current}' matched the current state"}

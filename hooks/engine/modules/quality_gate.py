"""Commit-time quality checks.

This module owns the orchestration of commit validation. The detailed record,
story, freshness, and scope checks stay in their domain modules and are supplied
by ``guard`` as the compatibility surface. Keeping that dependency explicit
lets existing callers and focused tests replace individual checks without
duplicating the policy orchestration here.
"""

from __future__ import annotations

import os
from types import ModuleType


def quality(json_in: dict, *, guard_module: ModuleType) -> dict:
    """Apply the commit gate using check implementations owned by guard."""
    g = guard_module
    norm = g.normalize_hook_input(json_in)
    if norm["tool_name"] != "terminal":
        return {"decision": "allow"}

    command = norm["tool_input"].get("command", "")
    if not g._is_git_commit(command):
        return {"decision": "allow"}

    root = os.path.abspath(g.repo_root(json_in))

    # The commit is the last point to report whether an approval still applies
    # to the code before it leaves the working tree.
    res_fresh = g._freshness_chain_row(root, "git commit blocked")
    if res_fresh.get("decision") == "deny":
        return g._apply_gate_strictness(res_fresh, "quality_gate")
    fresh_warnings = list(res_fresh.get("methodology_warnings") or [])

    # Gate record checks (IR → QR → SP).
    res_gate = g._check_gate_records(root, "git commit blocked")
    if res_gate.get("decision") == "deny":
        return g._apply_gate_strictness(res_gate, "quality_gate")

    # Story metadata, methodology-chain, and embedded QR coherence.
    for check in (g._check_all_stories_quality, g._check_qr_table_coherence):
        result = check(root, "git commit blocked")
        if result.get("decision") == "deny":
            return g._apply_gate_strictness(result, "quality_gate")

    # Scope coverage is advisory: failures in the mirror or blackboard must
    # not block a commit, while a real uncovered edit remains visible.
    try:
        misses, ran = g._check_scope_coverage(root)
    except Exception:
        misses, ran = [], False
    if misses:
        warn = ("SCOPE COVERAGE: " + ", ".join(misses[:3])
                + (f" (+{len(misses) - 3} more)" if len(misses) > 3 else "")
                + " — outside every approved Code Scope; open an E record with "
                  "this file in its scope before relying on these edits")
        try:
            from .config import blackboard_enabled
            if blackboard_enabled():
                from . import blackboard as bb
                bb.consume_alerts(root, "scope")
                bb.post_alert(root, "scope", "warn", warn, sender="quality-gate")
        except Exception:
            pass  # board unavailable — the transcript warning already fired
        return {"decision": "allow", "methodology_warnings": [warn] + fresh_warnings}

    if ran:
        # A verified-clean result clears only this channel's previous debt.
        try:
            from .config import blackboard_enabled
            if blackboard_enabled():
                from . import blackboard as bb
                bb.consume_alerts(root, "scope")
        except Exception:
            pass  # board unavailable — nothing to clear, nothing to block

    if fresh_warnings:
        return {"decision": "allow", "methodology_warnings": fresh_warnings}
    return {"decision": "allow"}

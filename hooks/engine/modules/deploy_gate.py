"""Deploy-command detection and deployment gate orchestration."""

from __future__ import annotations

import os
import re
from types import ModuleType


DEPLOY_CMD_RE = re.compile(
    r"(?i)(?:"
    r"\bterraform\s+(?:apply|destroy|plan)\b|"
    r"\bkubectl\s+(?:apply|rollout|deploy)\b|"
    r"\bdocker\s+(?:compose\s+)?(?:up|deploy)\b|"
    r"\bansible\s+(?:playbook|deploy)\b|"
    r"\bgit\s+push\s+(?:origin|upstream)\s+(?:main|master|production|prod)\b|"
    r"\b部署\b|"
    r"\bdeploy\b"
    r")"
)


def deploy(json_in: dict, *, guard_module: ModuleType) -> dict:
    """Apply record and freshness checks to a recognized deploy command."""
    g = guard_module
    norm = g.normalize_hook_input(json_in)
    if norm["tool_name"] != "terminal":
        return {"decision": "allow"}

    command = norm["tool_input"].get("command", "")
    if not command or not g._DEPLOY_CMD_RE.search(command):
        return {"decision": "allow"}

    root = os.path.abspath(g.repo_root(json_in))
    result = g._check_gate_records(root, "Deploy blocked", include_pr=True)
    if result.get("decision") != "deny":
        fresh = g._freshness_chain_row(root, "Deploy blocked")
        if fresh.get("decision") == "deny":
            result = fresh
        elif fresh.get("methodology_warnings"):
            result = dict(fresh)
    return g._apply_gate_strictness(result, "deploy_guard")

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
    r"\b(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?deploy\b|"
    r"\bmake\s+deploy\b|"
    r"\b部署\b|"
    # A bare `deploy` is a deploy only when it is the COMMAND being run. The
    # word used to match anywhere in the command text, so a read-only command
    # that merely named a path like `deploy/docker-compose.yml` was blocked
    # with "Deploy blocked" (E-174, observed 2026-11-16).
    r"(?:^|[;&|]\s*)(?:\S*/)?deploy(?:\.sh)?(?:\s|$)"
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

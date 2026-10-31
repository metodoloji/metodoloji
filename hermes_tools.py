"""Metodoloji tools for Hermes Agent.

Four deterministic entry points. Each one shells out to a script the plugin
already ships — none of them re-implements methodology logic, so the CLI and
the agent can never disagree about what "APPROVED" or "HEALTHY" means:

    metodoloji_orient   bmad/scripts/orient.py --json        (read-only digest)
    metodoloji_init     bmad/scripts/skeleton.py --install   (idempotent)
    metodoloji_verify   skills/bmad-research-experiment/scripts/run_experiment.py
    metodoloji_audit    scripts/check-plugin.sh              (optional, slow)

Every handler returns a JSON string, which is what Hermes' tool registry
expects. Errors are reported as ``{"success": false, ...}`` rather than raised:
a failed audit must not end the turn, it must tell the model what failed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

try:  # Hermes imports plugins as a package (hermes_plugins.<name>)
    from .hermes_adapter import (
        PLUGIN_ROOT, EngineUnavailable, agent_cwd, run_plugin_script,
    )
except ImportError:  # standalone import (tests, ad-hoc probes)
    from hermes_adapter import (  # type: ignore[no-redef]
        PLUGIN_ROOT, EngineUnavailable, agent_cwd, run_plugin_script,
    )

GATE_SCRIPT = "skills/bmad-research-experiment/scripts/run_experiment.py"

_GATE_TOOLSET = "metodoloji"

ORIENT_SCHEMA = {
    "name": "metodoloji_orient",
    "description": (
        "Read-only orientation digest for a Metodoloji project: plugin root, record "
        "inventory (E/IR/SP/S/QR/PR), skeleton install state, blackboard focus, "
        "waiting hand-offs, gate-key presence. Call this at the start of work in a "
        "project that uses the methodology instead of re-deriving state from files."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_root": {
                "type": "string",
                "description": "Project to inspect. Defaults to the session working directory.",
            },
        },
    },
}

INIT_SCHEMA = {
    "name": "metodoloji_init",
    "description": (
        "Install the methodology record skeleton (docs/experiments, docs/development, "
        "docs/quality, docs/research, docs/design, scratch/ + record templates) into a "
        "project. Idempotent: existing files are never overwritten unless force=true. "
        "Writes the .metodoloji/initialized marker that tells later sessions init already ran."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_root": {"type": "string", "description": "Target project. Defaults to the session cwd."},
            "force": {
                "type": "boolean",
                "description": "Replace existing skeleton copies and rewrite the init marker.",
                "default": False,
            },
        },
    },
}

VERIFY_SCHEMA = {
    "name": "metodoloji_verify",
    "description": (
        "Verify a methodology experiment record with the HMAC gate. "
        "action=verify returns VERIFIED / FORGED / REJECTED / ADVISORY-BLOCK for one "
        "record; check_secret reports whether this machine holds the gate key; "
        "validate checks a documentary (Mod B/C/D) record for completeness. "
        "Use it before claiming a code scope is approved."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "record": {
                "type": "string",
                "description": "Path to the record (e.g. docs/experiments/E-001.md). "
                               "Required for verify and validate.",
            },
            "action": {
                "type": "string",
                "enum": ["verify", "check_secret", "validate"],
                "default": "verify",
            },
        },
    },
}

KEY_SCHEMA = {
    "name": "metodoloji_key",
    "description": (
        "Inspect or create the machine-local HMAC gate key that signs APPROVED "
        "records (~/.bmad/gate-key, outside the repo, never printed). action=status "
        "reports key + trust-ring state; action=init generates the key ONCE per "
        "machine (a second key silently invalidates every token the first issued). "
        "Never shell-probe the key path — the guard denies those references on purpose."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["status", "init"], "default": "status"},
        },
    },
}

AUDIT_SCHEMA = {
    "name": "metodoloji_audit",
    "description": (
        "Methodology health check. scope=records (default, fast) reports record-chain "
        "gaps and gate state from the orient digest; scope=full runs the plugin's own "
        "scripts/check-plugin.sh integrity audit (slower, ~1-2 min) and summarizes "
        "errors. Use scope=full before a release or when the chain looks inconsistent."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "scope": {"type": "string", "enum": ["records", "full"], "default": "records"},
            "project_root": {"type": "string", "description": "Project to audit. Defaults to the session cwd."},
        },
    },
}


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _run(argv_script: str, argv: list[str], *, cwd: str, timeout: float = 60.0) -> dict:
    try:
        proc = run_plugin_script(argv_script, argv, cwd=cwd, timeout=timeout)
    except (EngineUnavailable, subprocess.TimeoutExpired, OSError) as exc:
        return {"success": False, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "success": proc.returncode == 0,
        "exit_code": proc.returncode,
        "stdout": (proc.stdout or "").strip(),
        "stderr": (proc.stderr or "").strip()[-2000:],
    }


def handle_orient(params: dict, **kwargs) -> str:
    del kwargs
    cwd = str(params.get("project_root") or agent_cwd())
    proc = _run("bmad/scripts/orient.py", ["--json", "--project-root", cwd], cwd=cwd)
    if not proc.get("success"):
        return _json({"success": False, "project_root": cwd, **proc})
    try:
        digest = json.loads(proc["stdout"])
    except ValueError:
        return _json({"success": True, "project_root": cwd, "digest_text": proc["stdout"]})
    return _json({"success": True, "project_root": cwd, "digest": digest})


def handle_init(params: dict, **kwargs) -> str:
    del kwargs
    cwd = str(params.get("project_root") or agent_cwd())
    argv = ["--install", "--project-root", cwd, "--json"]
    if params.get("force"):
        argv.append("--force")
    proc = _run("bmad/scripts/skeleton.py", argv, cwd=cwd, timeout=90.0)
    if not proc.get("success"):
        return _json({"success": False, "project_root": cwd, **proc})
    try:
        summary = json.loads(proc["stdout"])
    except ValueError:
        summary = {"summary_text": proc["stdout"]}
    return _json({
        "success": True,
        "project_root": cwd,
        "next_step": "/metodoloji-audit — verify the skeleton and the record chain.",
        "install": summary,
    })


def handle_verify(params: dict, **kwargs) -> str:
    del kwargs
    action = str(params.get("action") or "verify")
    record = str(params.get("record") or "").strip()
    cwd = agent_cwd()
    if action == "check_secret":
        argv = ["--check-secret"]
    elif action == "validate":
        if not record:
            return _json({"success": False, "error": "action=validate needs `record`"})
        argv = ["--validate", record]
    else:
        if not record:
            return _json({"success": False, "error": "action=verify needs `record`"})
        argv = ["--verify", "--record", record]
    proc = _run(GATE_SCRIPT, argv, cwd=cwd, timeout=30.0)
    verdict = ""
    for line in (proc.get("stdout") or "").splitlines():
        if line.strip():
            verdict = line.strip()  # last non-empty line carries the verdict
    # Exit 2 on --verify is ADVISORY-BLOCK: a real verdict, not a tool failure.
    success = bool(proc.get("success")) or proc.get("exit_code") == 2
    return _json({
        "success": success,
        "action": action,
        "record": record or None,
        "verdict": verdict,
        **proc,
    })


def handle_key(params: dict, **kwargs) -> str:
    """Gate key status / one-time creation (the `/metodoloji-gate-setup` contract)."""
    del kwargs
    action = str(params.get("action") or "status")
    cwd = agent_cwd()
    argv = ["--init-secret"] if action == "init" else ["--check-secret"]
    proc = _run(GATE_SCRIPT, argv, cwd=cwd, timeout=30.0)
    present = "gate key present" in (proc.get("stdout") or "")
    missing = "gate key not found" in (proc.get("stdout") or "")
    return _json({
        "success": bool(proc.get("success")) or present,
        "action": action,
        "present": present,
        "missing": missing,
        **proc,
    })


def handle_audit(params: dict, **kwargs) -> str:
    del kwargs
    cwd = str(params.get("project_root") or agent_cwd())
    scope = str(params.get("scope") or "records")

    if scope != "full":
        proc = _run("bmad/scripts/orient.py", ["--json", "--project-root", cwd], cwd=cwd)
        if not proc.get("success"):
            return _json({"success": False, "project_root": cwd, **proc})
        try:
            digest = json.loads(proc["stdout"])
        except ValueError:
            return _json({"success": True, "scope": scope, "digest_text": proc["stdout"]})
        return _json({"success": True, "scope": scope, "project_root": cwd, "digest": digest})

    script = PLUGIN_ROOT / "scripts" / "check-plugin.sh"
    if not script.is_file():
        return _json({"success": False, "error": f"missing {script}"})
    try:
        proc = subprocess.run(
            ["sh", str(script)], capture_output=True, text=True,
            timeout=240.0, cwd=cwd,
        )
    except subprocess.TimeoutExpired:
        return _json({"success": False, "error": "check-plugin.sh exceeded 240s"})
    output = proc.stdout or ""
    errors = [line for line in output.splitlines() if line.startswith("[ERROR]")]
    status = [line for line in output.splitlines() if line.startswith("STATUS:")]
    registry_ok = True
    registry: dict[str, Any] = {}
    registry_script = PLUGIN_ROOT / "scripts" / "external-context.py"
    if registry_script.is_file():
        try:
            registry_proc = subprocess.run(
                [sys.executable, str(registry_script), "check",
                 "--project-root", cwd, "--strict", "--json"],
                capture_output=True, text=True, timeout=60.0, cwd=cwd,
            )
            registry = json.loads(registry_proc.stdout or "{}")
            registry_ok = registry_proc.returncode == 0
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            registry_ok = False
            registry = {"error": f"external-context check failed: {exc}"}
    else:
        registry_ok = False
        registry = {"error": f"missing {registry_script}"}
    return _json({
        "success": proc.returncode == 0 and registry_ok,
        "scope": scope,
        "project_root": cwd,
        "status": status[-1] if status else "unknown",
        "error_count": len(errors),
        "errors": errors[:40],
        "registry_ok": registry_ok,
        "registry": registry,
        "tail": output.strip().splitlines()[-25:],
    })


TOOLS = (
    (ORIENT_SCHEMA, handle_orient),
    (INIT_SCHEMA, handle_init),
    (VERIFY_SCHEMA, handle_verify),
    (KEY_SCHEMA, handle_key),
    (AUDIT_SCHEMA, handle_audit),
)


def register_tools(ctx) -> None:
    for schema, handler in TOOLS:
        ctx.register_tool(
            name=schema["name"],
            toolset=_GATE_TOOLSET,
            schema=schema,
            handler=handler,
            description=schema["description"].split(".")[0],
        )

#!/usr/bin/env python3
"""The chain speaks to the human (E-175).

Ground truth today: every workflow surface is model-facing — `advisory.peek`
rides the session context, the CLI prints JSON to the agent, the Stop report
rides `additionalContext`. The person driving the session sees none of it, so a
run advances with nobody at the keyboard knowing (reported 2026-11-16 on a real
project).

Claim, closed set: the shipped hooks now put the same computed position on the
harness's USER channel (`systemMessage`) at the three edges — session open, a
kernel mutation, session close — and ask the human only at the thresholds they
cannot undo: `complete --force`, `block`, `create`. Every other stage transition
flows, so an unattended loop keeps running.

Probes drive the real entry point: `hooks/engine/main.py <mode>` as a subprocess
with a JSON payload on stdin, exactly as the harness invokes it.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]

# The pre-fix tree: HEAD when this record was written.
PREFIX_REV = "ebc4ddf"

# Informational. The gate requires Code Scope and Planned Files for setup
# records; a contract measures behaviour, not a sample count.
SAMPLE_SIZE = 12

BROKEN = False

PROBES = (
    "session_position",
    "session_idle",
    "turn_mutation",
    "turn_unrelated",
    "stop_position",
    "stop_idle",
    "ask_force",
    "allow_plain_complete",
    "ask_block",
    "ask_create",
    "relay_other_runtime",
    "bounded_line",
)


# ---------------------------------------------------------------- helpers

def _project(base: pathlib.Path) -> pathlib.Path:
    project = base / "project"
    project.mkdir()
    return project


def _create_run(tree: pathlib.Path, project: pathlib.Path) -> bool:
    """Declare a run with the tree's own CLI; False when it refuses."""
    for builtin in ("methodology-chain", "seo-visibility"):
        result = subprocess.run(
            [sys.executable, str(tree / "hooks" / "scripts" / "workflow.py"),
             "create", "--builtin", builtin, "--slug", "demo",
             "--project-root", str(project)],
            capture_output=True, text=True, stdin=subprocess.DEVNULL)
        if result.returncode == 0:
            return True
    return False


def _hook(tree: pathlib.Path, mode: str, payload: dict,
          runtime: str = "claude") -> dict:
    env = {**os.environ, "METODOLOJI_RUNTIME": runtime}
    result = subprocess.run(
        [sys.executable, str(tree / "hooks" / "engine" / "main.py"), mode],
        input=json.dumps(payload), capture_output=True, text=True, env=env)
    try:
        return json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return {"_raw": result.stdout, "_err": result.stderr}


def _payload(project: pathlib.Path, command: str | None = None) -> dict:
    payload: dict = {"cwd": str(project)}
    if command is not None:
        payload["tool_name"] = "Bash"
        payload["tool_input"] = {"command": command}
        payload["tool_response"] = {"exit_code": 0}
    return payload


def _cli(tree: pathlib.Path, project: pathlib.Path, *args: str) -> str:
    return " ".join([sys.executable, str(tree / "hooks" / "scripts" / "workflow.py"),
                     *args, "--project-root", str(project)])


def _decision(out: dict) -> str:
    hso = out.get("hookSpecificOutput") or {}
    return str(hso.get("permissionDecision") or out.get("permissionDecision") or "")


# ---------------------------------------------------------------- probes

def _probe(name: str, tree: pathlib.Path, base: pathlib.Path) -> tuple[bool, str]:
    project = _project(base)

    if name == "session_idle":
        out = _hook(tree, "session_start", _payload(project))
        message = out.get("systemMessage", "")
        ok = not message and bool(out.get("hookSpecificOutput"))
        return ok, f"systemMessage={message!r}"

    if name == "stop_idle":
        out = _hook(tree, "stop", _payload(project))
        return not out.get("systemMessage", ""), f"systemMessage={out.get('systemMessage')!r}"

    if name == "turn_unrelated":
        out = _hook(tree, "audit", _payload(project, "npm run build"))
        return not out.get("systemMessage", ""), f"systemMessage={out.get('systemMessage')!r}"

    if name in ("ask_force", "allow_plain_complete", "ask_block", "ask_create",
                "relay_other_runtime"):
        forced = name in ("ask_force", "relay_other_runtime")
        if name == "ask_block":
            command = _cli(tree, project, "block", "--slug", "demo", "--reason", "x")
        elif name == "ask_create":
            command = _cli(tree, project, "create", "--builtin", "methodology-chain",
                           "--slug", "demo2")
        else:
            command = _cli(tree, project, "complete", "--slug", "demo",
                           "--stage", "experiment", *(["--force"] if forced else []))
        runtime = "openhands" if name == "relay_other_runtime" else "claude"
        out = _hook(tree, "pre", _payload(project, command), runtime=runtime)
        decision = _decision(out)
        if name == "allow_plain_complete":
            return decision == "allow", f"permissionDecision={decision!r}"
        if name == "relay_other_runtime":
            context = str((out.get("hookSpecificOutput") or {}).get("additionalContext") or "")
            ok = decision != "ask" and "ask the user" in context.lower()
            return ok, f"decision={decision!r} context={context[:120]!r}"
        return decision == "ask", f"permissionDecision={decision!r}"

    # Everything below needs a live run.
    if not _create_run(tree, project):
        return False, "create refused"

    if name == "session_position":
        out = _hook(tree, "session_start", _payload(project))
        message = out.get("systemMessage", "")
        ok = ("demo" in message and "WORKFLOW" in message
              and bool(out.get("hookSpecificOutput")))
        return ok, f"systemMessage={message!r}"

    if name == "bounded_line":
        message = _hook(tree, "session_start", _payload(project)).get("systemMessage", "")
        ok = bool(message) and "\n" not in message and len(message) <= 300
        return ok, f"len={len(message)} newline={chr(10) in message}"

    if name == "turn_mutation":
        command = _cli(tree, project, "complete", "--slug", "demo", "--stage", "experiment")
        out = _hook(tree, "audit", _payload(project, command))
        message = out.get("systemMessage", "")
        return bool(message and "demo" in message), f"systemMessage={message!r}"

    if name == "stop_position":
        out = _hook(tree, "stop", _payload(project))
        message = out.get("systemMessage", "")
        return bool(message and "demo" in message), f"systemMessage={message!r}"

    raise SystemExit(f"unknown probe: {name}")


# ---------------------------------------------------------------- driver mode

def driver(name: str, tree: pathlib.Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ok, detail = _probe(name, tree, pathlib.Path(tmp))
    print(json.dumps({"ok": ok, "detail": detail}, ensure_ascii=False))


# ---------------------------------------------------------------- bench mode

def _prefix_tree(base: pathlib.Path) -> pathlib.Path:
    import tarfile
    archive = base / "prefix.tar"
    with archive.open("wb") as handle:
        subprocess.run(["git", "archive", PREFIX_REV], cwd=ROOT,
                       stdout=handle, stdin=subprocess.DEVNULL, check=True)
    tree = base / "prefix"
    tree.mkdir()
    with tarfile.open(archive) as tar:
        tar.extractall(tree)
    return tree


def _run(tree: pathlib.Path, samples: pathlib.Path | None, broken: bool) -> bool:
    rows, passed = [], 0
    for name in PROBES:
        result = subprocess.run(
            [sys.executable, str(pathlib.Path(__file__).resolve()), "--driver", name,
             "--tree", str(tree)], capture_output=True, text=True,
            stdin=subprocess.DEVNULL)
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            payload = {"ok": False, "detail": (result.stderr or result.stdout)[-200:]}
        ok = bool(payload.get("ok"))
        passed += 1 if ok else 0
        rows.append({"probe": name, "ok": ok, "detail": payload.get("detail", "")})
        print(f"  {'PASS' if ok else 'FAIL'}  {name}  — {payload.get('detail', '')}")
    total = len(PROBES)
    value = passed / total
    print(f"workflow_visibility_accuracy={value:.2f} ({passed}/{total})")
    if samples is not None:
        samples.parent.mkdir(parents=True, exist_ok=True)
        with samples.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps({"broken": broken, **row}, ensure_ascii=False) + "\n")
    return passed == total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=pathlib.Path, default=None)
    parser.add_argument("--broken", action="store_true")
    parser.add_argument("--driver", default=None)
    parser.add_argument("--tree", type=pathlib.Path, default=None)
    args = parser.parse_args()

    if args.driver:
        driver(args.driver, args.tree or ROOT)
        return 0

    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        tree = _prefix_tree(base) if args.broken else ROOT
        print(f"tree: {tree}")
        ok = _run(tree, args.samples, args.broken)
    # The control is EXPECTED to miss the claim; a non-zero exit would read as a
    # crashed measurement instead of a measured refutation (rule 7).
    return 0 if (ok or args.broken) else 1


if __name__ == "__main__":
    raise SystemExit(main())

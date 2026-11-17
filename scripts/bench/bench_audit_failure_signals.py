#!/usr/bin/env python3
"""The audit trail stops inventing shell failures (E-177).

    python3 scripts/bench/bench_audit_failure_signals.py --samples docs/experiments/E-177/raw/samples.jsonl
    python3 scripts/bench/bench_audit_failure_signals.py --broken   # rule-7 control

`audit.command_failure` was handed the harness's whole `tool_response` mapping
and stringified it, so a NON-output field (`returnCodeInterpretation`) was
scanned as if the command had printed it. In this checkout's own trail
(2026-11-17) 23 rows carry `glob_nomatch`; 13 of them have empty stdout and
stderr plus the label `No matches found` — a `grep` that found nothing was
reported to the agent as "Command aborted by an unmatched glob — zsh (macOS)
cancels the whole line". The correction names an abort that never happened and
the per-class failure baseline the trail exists to provide is wrong.

Every probe drives the tree's own module, and the expected verdict is stated
once here — nothing is sampled. `--broken` runs the same probes against the
pre-fix tree (205fa6d), where the classifier reads the envelope.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
PREFIX_REV = "205fa6d"  # last commit before the audit classifier read the envelope
BROKEN = False

_PREFIX_CACHE: pathlib.Path | None = None


def _tree() -> pathlib.Path:
    return ROOT if not BROKEN else _prefix_tree()


def _prefix_tree() -> pathlib.Path:
    global _PREFIX_CACHE
    if _PREFIX_CACHE is not None:
        return _PREFIX_CACHE
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="audit-signals-prefix-"))
    out = subprocess.run(["git", "-C", str(ROOT), "archive", PREFIX_REV],
                         capture_output=True, stdin=subprocess.DEVNULL)
    if out.returncode != 0:
        raise RuntimeError(f"cannot archive {PREFIX_REV}: {out.stderr[:200]!r}")
    subprocess.run(["tar", "-x", "-C", str(tmp)], input=out.stdout, check=True)
    _PREFIX_CACHE = tmp
    return tmp


# --- driver: runs one probe against ONE tree, in its own process ------------
# One process per probe keeps two trees' `modules` packages from colliding in
# sys.modules; the bench itself only aggregates verdicts.

# The expected verdict per probe, stated once. None means "no signature".
#
# The harness mapping below is the shape a real Claude Code session recorded
# (`.metodoloji/logs/hook-audit.log`, 2026-11-16): empty streams, the outcome
# reduced to `returnCodeInterpretation`. It is an interpretation of the exit
# code, never text the command produced.
EXPECTED = {
    "glob_zsh_string": "glob_nomatch",
    "glob_bash_string": "glob_nomatch",
    "glob_zsh_mapping": "glob_nomatch",
    "glob_harness_label": None,
    "glob_program_prose": None,
    "clean_stdout": None,
    "unknown_output": None,
    "backslash_mapping": "backslash_path",
    "command_not_found_mapping": "command_not_found",
    "command_not_found_string": "command_not_found",
    "audit_false_label_silent": None,
    "audit_real_abort_logged": "glob_nomatch",
}

_HARNESS_LABEL = {
    "stdout": "",
    "stderr": "",
    "returnCodeInterpretation": "No matches found",
    "interrupted": False,
    "isImage": False,
    "noOutputExpected": False,
}

_INPUTS = {
    "glob_zsh_string": "zsh: no matches found: docs/design/prds/*prd*.md",
    "glob_bash_string": "bash: no match: docs/*.md",
    "glob_zsh_mapping": {
        "stdout": "",
        "stderr": "zsh: no matches found: docs/*.md",
        "interrupted": False,
        "isImage": False,
        "noOutputExpected": False,
    },
    "glob_harness_label": _HARNESS_LABEL,
    "glob_program_prose": {
        "stdout": "No matches found for query 'retired'.",
        "stderr": "",
    },
    "clean_stdout": {"stdout": "total 8\n-rw-r--r-- 1 a staff 0 x\n", "stderr": ""},
    "unknown_output": "total 8\n-rw-r--r-- 1 a staff 0 x",
    "backslash_mapping": {
        "stdout": "",
        "stderr": "ls: cannot access 'C:Usersx': No such file or directory",
    },
    "command_not_found_mapping": {
        "stdout": "",
        "stderr": "/bin/bash: line 7: _prints_only: command not found",
    },
    "command_not_found_string": "zsh: command not found: nppm",
}


def _end_to_end(name: str) -> tuple[bool, str]:
    """Drive audit() itself: the agent-visible warning and the trail row."""
    root = pathlib.Path(tempfile.mkdtemp(prefix="audit-signals-root-"))
    try:
        os.environ["CLAUDE_PROJECT_DIR"] = str(root)
        os.environ.pop("OPENHANDS_PROJECT_DIR", None)
        from modules.audit import audit
        if name == "audit_false_label_silent":
            # A grep that found nothing: exit 1, no glob in the command at all.
            payload = {"tool_name": "Bash",
                       "tool_input": {"command": 'grep -rn "Measured: true" skills/x/SKILL.md'},
                       "tool_response": dict(_HARNESS_LABEL)}
            command_failed = None
        else:
            payload = {"tool_name": "Bash",
                       "tool_input": {"command": "ls docs/*.md"},
                       "tool_response": {
                           "stdout": "",
                           "stderr": "zsh: no matches found: docs/*.md",
                           "interrupted": False,
                           "isImage": False,
                           "noOutputExpected": False,
                       }}
            command_failed = "glob_nomatch"
        res = audit(payload)
        log = root / ".metodoloji" / "logs" / "hook-audit.log"
        rows = ([json.loads(line) for line in
                 log.read_text(encoding="utf-8").splitlines()] if log.is_file() else [])
        warns = list(res.get("methodology_warnings") or [])
        if name == "audit_false_label_silent":
            ok = not rows and not warns
            return ok, f"rows={len(rows)} warnings={warns}"
        if not rows:
            return False, "a real abort wrote no trail row"
        got = rows[-1].get("command_failed")
        ok = got == command_failed and any("unmatched glob" in w for w in warns)
        return ok, f"rows={len(rows)} command_failed={got} warnings={warns}"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _driver(name: str, tree: pathlib.Path) -> tuple[bool, str]:
    sys.path.insert(0, str(tree / "hooks" / "engine"))
    sys.path.insert(0, str(tree / "hooks" / "scripts"))

    if name.startswith("audit_"):
        return _end_to_end(name)

    from modules.audit import command_failure
    got = command_failure("terminal", _INPUTS[name])
    kind = got[0] if got else None
    want = EXPECTED[name]
    return kind == want, f"kind={kind} want={want}"


CASES = list(EXPECTED)


def _run_probe(name: str) -> tuple[bool, str]:
    proc = subprocess.run(
        [sys.executable, str(pathlib.Path(__file__).resolve()),
         "--driver", name, "--tree", str(_tree())],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180, stdin=subprocess.DEVNULL)
    try:
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return False, f"driver crashed: {proc.stderr.strip()[:200]}"
    return bool(payload["ok"]), str(payload["detail"])


def main() -> int:
    global BROKEN
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--broken", action="store_true", help="rule-7 control arm")
    ap.add_argument("--samples", help="write per-check JSONL samples here")
    ap.add_argument("--driver", help=argparse.SUPPRESS)
    ap.add_argument("--tree", help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.driver:
        ok, detail = _driver(args.driver, pathlib.Path(args.tree))
        print(json.dumps({"ok": ok, "detail": detail}, ensure_ascii=False))
        return 0

    BROKEN = args.broken
    results = []
    for name in CASES:
        try:
            ok, detail = _run_probe(name)
        except Exception as exc:  # a crashed probe is a failed check, never a pass
            ok, detail = False, f"probe crashed: {type(exc).__name__}: {exc}"
        results.append({"check": name, "ok": bool(ok), "detail": detail})
        print(f"  {'ok  ' if ok else 'FAIL'}  {name}: {detail}")

    passed = sum(1 for r in results if r["ok"])
    if args.samples:
        path = pathlib.Path(args.samples)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n"
                                for r in results), encoding="utf-8")
    print(f"audit_failure_signal_accuracy={passed / len(results):.2f} "
          f"({passed}/{len(results)})")
    if _PREFIX_CACHE is not None:
        shutil.rmtree(_PREFIX_CACHE, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""The audit classifier walks the whole harness envelope (E-178).

    python3 scripts/bench/bench_audit_output_shapes.py --samples docs/experiments/E-178/raw/samples.jsonl
    python3 scripts/bench/bench_audit_output_shapes.py --broken   # rule-7 control

E-177 stopped the classifier reading the harness's whole payload as command
text, but it assumed a FLAT envelope (`stdout`/`stderr` at the top level).
This checkout's own trail (2026-11-17) carries a real `terminal` row shaped

    {"output": {"exit": 0, "truncated": false, "output": "/bin/bash: line 1: rg: command not found"},
     "status": "ok"}

against which the flat extractor returns the word `ok` and drops the real
command-not-found: a silent miss in place of a correct row. A nested
`result`/`stdout` envelope is one adapter away. The extractor therefore walks
the envelope, skips metadata at every level, and stops at a bounded depth —
past the bound it stays silent rather than guessing.

Every probe drives the tree's own module, and the expected verdict is stated
once here. `--broken` runs the same probes against the pre-E-177 tree at
205fa6d, whose classifier stringifies the whole envelope.
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
PREFIX_REV = "205fa6d"  # last commit before the envelope was read as command text
BROKEN = False

_PREFIX_CACHE: pathlib.Path | None = None


def _tree() -> pathlib.Path:
    return ROOT if not BROKEN else _prefix_tree()


def _prefix_tree() -> pathlib.Path:
    global _PREFIX_CACHE
    if _PREFIX_CACHE is not None:
        return _PREFIX_CACHE
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="audit-envelope-prefix-"))
    out = subprocess.run(["git", "-C", str(ROOT), "archive", PREFIX_REV],
                         capture_output=True, stdin=subprocess.DEVNULL)
    if out.returncode != 0:
        raise RuntimeError(f"cannot archive {PREFIX_REV}: {out.stderr[:200]!r}")
    subprocess.run(["tar", "-x", "-C", str(tmp)], input=out.stdout, check=True)
    _PREFIX_CACHE = tmp
    return tmp


# The expected verdict per probe, stated once. None means "no signature".
EXPECTED = {
    "flat_string_abort": "glob_nomatch",
    "flat_mapping_abort": "glob_nomatch",
    "nested_result_abort": "glob_nomatch",
    "nested_opencode_not_found": "command_not_found",
    "nested_metadata_only": None,
    "nested_label": None,
    "flat_label": None,
    "program_prose": None,
    "list_output": None,
    "depth_bounded": None,
    "audit_nested_failure_logged": "command_not_found",
    "audit_nested_no_output_silent": None,
}

_OPENCODE_FAIL = {
    "output": {"exit": 1, "truncated": False,
               "output": "/bin/bash: line 1: rg: command not found\n"},
    "status": "ok",
}

_INPUTS = {
    "flat_string_abort": "zsh: no matches found: docs/*.md",
    "flat_mapping_abort": {"stdout": "", "stderr": "zsh: no matches found: docs/*.md"},
    "nested_result_abort": {"result": {"stdout": "zsh: no matches found: docs/*.md",
                                       "stderr": ""}},
    "nested_opencode_not_found": _OPENCODE_FAIL,
    "nested_metadata_only": {"output": {"exit": 1, "status": "failed"}},
    "nested_label": {"output": {"output": "",
                                "returnCodeInterpretation": "No matches found"}},
    "flat_label": {"stdout": "", "stderr": "",
                   "returnCodeInterpretation": "No matches found"},
    "program_prose": {"stdout": "No matches found for query 'retired'.", "stderr": ""},
    "list_output": {"output": ["ok", "done"]},
    "depth_bounded": ({"output": {"output": {"output": {"output": {
        "output": "zsh: no matches found: docs/*.md"}}}}}),
}


def _end_to_end(name: str) -> tuple[bool, str]:
    """Drive audit() itself: the agent-visible warning and the trail row."""
    root = pathlib.Path(tempfile.mkdtemp(prefix="audit-envelope-root-"))
    try:
        os.environ["CLAUDE_PROJECT_DIR"] = str(root)
        os.environ.pop("OPENHANDS_PROJECT_DIR", None)
        from modules.audit import audit
        if name == "audit_nested_failure_logged":
            payload = {"tool_name": "Bash",
                       "tool_input": {"command": "grep -rn x hooks"},
                       "tool_response": dict(_OPENCODE_FAIL)}
            want = "command_not_found"
        else:
            payload = {"tool_name": "Bash",
                       "tool_input": {"command": "ls docs/*.md"},
                       "tool_response": {"output": {"exit": 0, "truncated": False,
                                                    "output": "total 8"},
                                         "status": "ok"}}
            want = None
        res = audit(payload)
        log = root / ".metodoloji" / "logs" / "hook-audit.log"
        rows = ([json.loads(line) for line in
                 log.read_text(encoding="utf-8").splitlines()] if log.is_file() else [])
        warns = list(res.get("methodology_warnings") or [])
        if want is None:
            ok = not rows and not warns
            return ok, f"rows={len(rows)} warnings={warns}"
        if not rows:
            return False, "a real nested failure wrote no trail row"
        got = rows[-1].get("command_failed")
        ok = got == want and any("never resolved" in w.lower() for w in warns)
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
    print(f"audit_envelope_accuracy={passed / len(results):.2f} "
          f"({passed}/{len(results)})")
    if _PREFIX_CACHE is not None:
        shutil.rmtree(_PREFIX_CACHE, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

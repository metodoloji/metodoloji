#!/usr/bin/env python3
"""A collapsed path is the program's diagnostic (E-181).

    python3 scripts/bench/bench_audit_path_shapes.py --samples docs/experiments/E-181/raw/samples.jsonl
    python3 scripts/bench/bench_audit_path_shapes.py --broken   # rule-7 control

`backslash_path` was the last phrase-shaped class in the failure table: the
pattern `cannot access ['\"]?[a-z]:[^/\\]` had no line anchor and no program
prefix, so any TEXT that names the message matched. Reading the module's own
tests — the thing an agent does all session — was reported as a collapsed path
(reproduced 2026-11-17):

    sed -n '751,765p' hooks/engine/tests/test_audit.py      -> backslash_path
    sed -n '106,115p' scripts/bench/bench_audit_failure_signals.py -> backslash_path

In 800 recorded terminal calls the class never fired on a real failure, so the
only measured effect of the loose pattern is wrong corrections. A filesystem
tool prints its own diagnostic with its name at line start (`ls: cannot access
'C:Usersx': No such file or directory`), and only the COLLAPSED drive form is a
backslash problem — `C:/Users/x` reached the tool intact.

Every probe drives the tree's own module, and the expected verdict is stated
once here. `--broken` runs the same probes against the pre-E-177 tree at
205fa6d, whose unanchored phrase fires on every prose payload.
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
PREFIX_REV = "205fa6d"  # last commit before the failure table was narrowed at all
BROKEN = False

_PREFIX_CACHE: pathlib.Path | None = None


def _tree() -> pathlib.Path:
    return ROOT if not BROKEN else _prefix_tree()


def _prefix_tree() -> pathlib.Path:
    global _PREFIX_CACHE
    if _PREFIX_CACHE is not None:
        return _PREFIX_CACHE
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="audit-path-prefix-"))
    out = subprocess.run(["git", "-C", str(ROOT), "archive", PREFIX_REV],
                         capture_output=True, stdin=subprocess.DEVNULL)
    if out.returncode != 0:
        raise RuntimeError(f"cannot archive {PREFIX_REV}: {out.stderr[:200]!r}")
    subprocess.run(["tar", "-x", "-C", str(tmp)], input=out.stdout, check=True)
    _PREFIX_CACHE = tmp
    return tmp


# The expected verdict per probe, stated once. None means "no signature".
EXPECTED = {
    "ls_quoted": "backslash_path",
    "ls_flat_stderr": "backslash_path",
    "cat_unquoted": "backslash_path",
    "drive_path_intact": None,
    "quoted_colon_not_a_drive": None,
    "glob_still_routed": "glob_nomatch",
    "test_file_chunk": None,
    "bench_chunk": None,
    "doc_sentence": None,
    "module_source": None,
    "audit_prose_silent": None,
    "test_file_whole": None,
}

_INPUTS = {
    "ls_quoted": "ls: cannot access 'C:Usersx': No such file or directory",
    "ls_flat_stderr": {"stdout": "", "stderr":
                       "ls: cannot access 'C:Usersx': No such file or directory\n"},
    "cat_unquoted": {"stdout": "cat: cannot access c:tempnotes.txt: No such file\n",
                     "stderr": ""},
    # GNU ls quotes the path and prints the intact form when the path survived:
    # nothing collapsed, so no forward-slash advice applies.
    "drive_path_intact": "ls: cannot access 'C:/Users/x': No such file or directory",
    "quoted_colon_not_a_drive": "ls: cannot access 'notes:draft.md': No such file",
    "glob_still_routed": "zsh: no matches found: docs/*.md",
    "doc_sentence": "A native path collapses and the tool prints cannot access "
                    "'C:Usersx', which is what this sentence documents.\n",
}

# The same ordinary reads that used to be misreported: the expected-value dict
# in the module's own tests, and a probe payload in the shipped bench.
_TEST_CHUNK = (
    '        "zsh: no matches found: docs/*": "glob_nomatch",\n'
    '        "ls: cannot access \'C:Usersx\': No such file or directory": "backslash_path",\n'
    '        "sed: 1: file.py: invalid command code 1": "bsd_sed",\n'
)
_BENCH_CHUNK = (
    '    "ls_flat": {\n'
    '        "stderr": "ls: cannot access \'C:Usersx\': No such file or directory",\n'
    '    },\n'
)

_TABLE_INPUTS = {
    "test_file_chunk": {"stdout": _TEST_CHUNK, "stderr": ""},
    "bench_chunk": {"stdout": _BENCH_CHUNK, "stderr": ""},
}


def _end_to_end(name: str) -> tuple[bool, str]:
    """Drive audit() itself: the agent-visible warning and the trail row."""
    root = pathlib.Path(tempfile.mkdtemp(prefix="audit-path-root-"))
    try:
        os.environ["CLAUDE_PROJECT_DIR"] = str(root)
        os.environ.pop("OPENHANDS_PROJECT_DIR", None)
        from modules.audit import audit
        payload = {"tool_name": "Bash",
                   "tool_input": {"command": "sed -n '751,765p' hooks/engine/tests/test_audit.py"},
                   "tool_response": dict(_TABLE_INPUTS["test_file_chunk"])}
        res = audit(payload)
        log = root / ".metodoloji" / "logs" / "hook-audit.log"
        rows = ([json.loads(line) for line in
                 log.read_text(encoding="utf-8").splitlines()] if log.is_file() else [])
        warns = list(res.get("methodology_warnings") or [])
        ok = not rows and not warns
        return ok, f"rows={len(rows)} warnings={warns}"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _driver(name: str, tree: pathlib.Path) -> tuple[bool, str]:
    sys.path.insert(0, str(tree / "hooks" / "engine"))
    sys.path.insert(0, str(tree / "hooks" / "scripts"))

    if name.startswith("audit_"):
        return _end_to_end(name)

    from modules.audit import command_failure
    if name == "module_source":
        payload = {"stdout": (tree / "hooks" / "engine" / "modules" / "audit.py")
                   .read_text(encoding="utf-8"), "stderr": ""}
    elif name == "test_file_whole":
        payload = {"stdout": (tree / "hooks" / "engine" / "tests" / "test_audit.py")
                   .read_text(encoding="utf-8"), "stderr": ""}
    elif name in _TABLE_INPUTS:
        payload = _TABLE_INPUTS[name]
    else:
        payload = _INPUTS[name]
    got = command_failure("terminal", payload)
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
    print(f"path_signature_accuracy={passed / len(results):.2f} "
          f"({passed}/{len(results)})")
    if _PREFIX_CACHE is not None:
        shutil.rmtree(_PREFIX_CACHE, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

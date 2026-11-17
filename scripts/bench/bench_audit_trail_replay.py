#!/usr/bin/env python3
"""A trail row must re-classify to the class it recorded (E-182).

    python3 scripts/bench/bench_audit_trail_replay.py --samples docs/experiments/E-182/raw/samples.jsonl
    python3 scripts/bench/bench_audit_trail_replay.py --broken   # rule-7 control

The trail exists so a LATER session can count failures per class — the module
says so next to the table, and the records' Next Steps ask for that baseline.
That only holds if a stored row can reproduce its own verdict. It could not:
`output_summary` was `str(tool_response)`, a Python repr with escaped newlines
cut at 500 characters, while every signature is anchored to a real line start.
Reproduced 2026-11-17 against ba05e5a:

    zsh abort on line 4     recorded glob_nomatch   replay from the row -> None
    2000 chars + tail sed   recorded bsd_sed        replay from the row -> None

So the row carried a class nothing inside it could justify, and the text was
unreadable to a person too.

Each probe writes through the real audit() in its own temporary root, reads the
log line back, and re-classifies what the row stored. `--broken` runs the same
probes against ba05e5a.
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
PREFIX_REV = "ba05e5a"  # last commit that stored str(tool_response)[:500]
BROKEN = False

_PREFIX_CACHE: pathlib.Path | None = None


def _tree() -> pathlib.Path:
    return ROOT if not BROKEN else _prefix_tree()


def _prefix_tree() -> pathlib.Path:
    global _PREFIX_CACHE
    if _PREFIX_CACHE is not None:
        return _PREFIX_CACHE
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="audit-replay-prefix-"))
    out = subprocess.run(["git", "-C", str(ROOT), "archive", PREFIX_REV],
                         capture_output=True, stdin=subprocess.DEVNULL)
    if out.returncode != 0:
        raise RuntimeError(f"cannot archive {PREFIX_REV}: {out.stderr[:200]!r}")
    subprocess.run(["tar", "-x", "-C", str(tmp)], input=out.stdout, check=True)
    _PREFIX_CACHE = tmp
    return tmp


_FILLER = "".join(f"   checking module {i:03d} … ok\n" for i in range(70))

CALL = {"command": "git commit -m 'x'"}  # anything but read-only: it is logged

# name -> (tool_name, tool_input, tool_response, expected write-time class)
PROBES: dict[str, tuple[str, dict, dict, str | None]] = {
    "single_line_glob": ("Bash", CALL, {
        "stdout": "", "stderr": "zsh: no matches found: docs/*.md\n"}, "glob_nomatch"),
    "multiline_glob": ("Bash", CALL, {
        "stdout": "running\nchecking docs\nexecuting…\n"
                  "zsh: no matches found: docs/*.md\n"}, "glob_nomatch"),
    "multiline_command_not_found": ("Bash", CALL, {
        "stdout": "build\ncollecting\n", "stderr": "bash: line 3: rg: command not found\n"},
        "command_not_found"),
    "multiline_backslash": ("Bash", CALL, {
        "stdout": "step 1\nstep 2\n",
        "stderr": "cat: cannot access 'C:Usersx': No such file or directory\n"},
        "backslash_path"),
    "stderr_only_glob": ("Bash", CALL, {
        "stdout": "no output here\n", "stderr": "zsh: no matches found: src/*\n"},
        "glob_nomatch"),
    "nested_envelope_glob": ("Bash", CALL, {
        "result": {"stdout": "warm-up\nzsh: no matches found: docs/*\n"}}, "glob_nomatch"),
    "long_head_glob": ("Bash", CALL, {
        "stdout": "zsh: no matches found: docs/*\n" + _FILLER}, "glob_nomatch"),
    "long_tail_sed": ("Bash", CALL, {
        "stdout": _FILLER + 'sed: 1: "f.py": invalid command code 1\n'}, "bsd_sed"),
    "silent_long_ok": ("Bash", CALL, {"stdout": _FILLER}, None),
    "silent_multiline_ok": ("Bash", CALL, {
        "stdout": "all tests passed\n3 files changed\n", "stderr": ""}, None),
    "no_output": ("Bash", CALL, {}, None),
    "file_editor_body_bounded": ("file_editor", {
        "command": "str_replace", "path": "x.py",
        "old_str": "a", "new_str": "b"},
        {"content": "SECRETBODY " * 400, "output": "edited"}, None),
}

CASES = list(PROBES)


def _probe(name: str, tree: pathlib.Path) -> tuple[bool, str]:
    tool_name, tool_input, tool_response, want = PROBES[name]
    sys.path.insert(0, str(tree / "hooks" / "engine"))
    sys.path.insert(0, str(tree / "hooks" / "scripts"))
    from modules.audit import audit, command_failure

    root = pathlib.Path(tempfile.mkdtemp(prefix="audit-replay-root-"))
    try:
        os.environ["CLAUDE_PROJECT_DIR"] = str(root)
        os.environ.pop("OPENHANDS_PROJECT_DIR", None)
        payload = {"tool_name": tool_name, "tool_input": tool_input}
        if tool_response:
            payload["tool_response"] = tool_response
        audit(payload)
        log = root / ".metodoloji" / "logs" / "hook-audit.log"
        rows = ([json.loads(line) for line in
                 log.read_text(encoding="utf-8").splitlines()] if log.is_file() else [])
        if not rows:
            return False, "no trail row written"
        row = rows[-1]
        written = row.get("command_failed")
        stored = row.get("output_summary")
        # What a later session can do with the row, and nothing else.
        replayed = None
        if isinstance(stored, str) and stored:
            got = command_failure("terminal", {"stdout": stored})
            replayed = got[0] if got else None
        ok = written == want and replayed == want
        if name == "file_editor_body_bounded":
            bounded = stored is None or len(str(stored)) <= 600
            ok = ok and bounded
        detail = f"written={written} replayed={replayed} want={want}"
        if name == "file_editor_body_bounded" and not bounded:
            detail += " (body stored whole)"
        return ok, detail
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _run(name: str) -> tuple[bool, str]:
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
        ok, detail = _probe(args.driver, pathlib.Path(args.tree))
        print(json.dumps({"ok": ok, "detail": detail}, ensure_ascii=False))
        return 0

    BROKEN = args.broken
    results = []
    for name in CASES:
        try:
            ok, detail = _run(name)
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
    print(f"trail_replay_accuracy={passed / len(results):.2f} ({passed}/{len(results)})")
    if _PREFIX_CACHE is not None:
        shutil.rmtree(_PREFIX_CACHE, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

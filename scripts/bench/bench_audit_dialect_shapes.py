#!/usr/bin/env python3
"""The dialect signatures are program diagnostics (E-180).

    python3 scripts/bench/bench_audit_dialect_shapes.py --samples docs/experiments/E-180/raw/samples.jsonl
    python3 scripts/bench/bench_audit_dialect_shapes.py --broken   # rule-7 control

E-179 made `command_not_found` a shell diagnostic; `bsd_sed` and
`powershell_dialect` were still bare phrases. This module documents both of
them in its own failure table, so READING that table is classified as the
failure it documents (reproduced 2026-11-17):

    sed -n '104,112p' hooks/engine/modules/audit.py   -> bsd_sed
    sed -n '120,135p' hooks/engine/modules/audit.py   -> powershell_dialect

and the agent is told to rewrite its `sed -i` or switch shells while it only
printed a file. Each class must match the shape the program prints: `sed:`
prefixing its own diagnostic, PowerShell quoting the term it could not resolve
and naming the error id, cmd.exe quoting the command it could not find.

Every probe drives the tree's own module, and the expected verdict is stated
once here. `--broken` runs the same probes against the pre-E-177 tree at
205fa6d, whose bare phrase regexes fire on every prose payload.
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
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="audit-dialect-prefix-"))
    out = subprocess.run(["git", "-C", str(ROOT), "archive", PREFIX_REV],
                         capture_output=True, stdin=subprocess.DEVNULL)
    if out.returncode != 0:
        raise RuntimeError(f"cannot archive {PREFIX_REV}: {out.stderr[:200]!r}")
    subprocess.run(["tar", "-x", "-C", str(tmp)], input=out.stdout, check=True)
    _PREFIX_CACHE = tmp
    return tmp


# The expected verdict per probe, stated once. None means "no signature".
EXPECTED = {
    "ps_term_sentence": "powershell_dialect",
    "ps_error_id": "powershell_dialect",
    "cmd_quoted": "powershell_dialect",
    "ps_token_separator": "powershell_dialect",
    "bsd_sed_flat": "bsd_sed",
    "bsd_sed_stderr": "bsd_sed",
    "glob_still_routed": "glob_nomatch",
    "table_sed_comment": None,
    "table_powershell_regex": None,
    "module_source": None,
    "doc_naming_both": None,
    "audit_prose_silent": None,
}

# The module's own failure table, verbatim: the comment that names the macOS
# diagnostic and the regex table that names the PowerShell one.
_TABLE_SED_COMMENT = (
    "#   macOS     GNU-style `sed -i 's/…/'` under BSD sed → invalid command code\n"
    "# The signature below is a SHELL diagnostic, not the words \"no matches\".\n"
)
_TABLE_PS_REGEX = (
    '    ("powershell_dialect",\n'
    '     re.compile(r"(?i)CommandNotFoundException|not recognized as the name of "\n'
    '                r"a cmdlet|is not recognized as an internal or external "\n'
    '                r"command|is not a valid statement separator"),\n'
)

_INPUTS = {
    "ps_term_sentence": {
        "stdout": "grep : The term 'grep' is not recognized as the name of a "
                  "cmdlet, function, script file, or operable program.\n",
        "stderr": "",
    },
    "ps_error_id": {
        "stdout": "    + FullyQualifiedErrorId : CommandNotFoundException\n",
        "stderr": "At line:1 char:1\n",
    },
    "cmd_quoted": {
        "stdout": "'grep' is not recognized as an internal or external command, "
                  "operable program or batch file.\n",
        "stderr": "",
    },
    "ps_token_separator": {
        "stdout": "The token '&&' is not a valid statement separator in this "
                  "version.\n",
        "stderr": "",
    },
    "bsd_sed_flat": 'sed: 1: "notes.md": invalid command code n',
    "bsd_sed_stderr": {"stdout": "", "stderr": 'sed: 1: "notes.md": invalid command code n\n'},
    "glob_still_routed": "zsh: no matches found: docs/*.md",
    "table_sed_comment": {"stdout": _TABLE_SED_COMMENT, "stderr": ""},
    "table_powershell_regex": {"stdout": _TABLE_PS_REGEX, "stderr": ""},
    "doc_naming_both": {
        "stdout": "macOS sed prints invalid command code; PowerShell prints "
                  "CommandNotFoundException or 'is not a valid statement "
                  "separator'.\n",
        "stderr": "",
    },
}


def _end_to_end(name: str) -> tuple[bool, str]:
    """Drive audit() itself: the agent-visible warning and the trail row."""
    root = pathlib.Path(tempfile.mkdtemp(prefix="audit-dialect-root-"))
    try:
        os.environ["CLAUDE_PROJECT_DIR"] = str(root)
        os.environ.pop("OPENHANDS_PROJECT_DIR", None)
        from modules.audit import audit
        payload = {"tool_name": "Bash",
                   "tool_input": {"command": "sed -n '104,112p' hooks/engine/modules/audit.py"},
                   "tool_response": dict(_INPUTS["table_sed_comment"])}
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
        source = (tree / "hooks" / "engine" / "modules" / "audit.py").read_text(
            encoding="utf-8")
        payload = {"stdout": source, "stderr": ""}
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
    print(f"dialect_signature_accuracy={passed / len(results):.2f} "
          f"({passed}/{len(results)})")
    if _PREFIX_CACHE is not None:
        shutil.rmtree(_PREFIX_CACHE, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

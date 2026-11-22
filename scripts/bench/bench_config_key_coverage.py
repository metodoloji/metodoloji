#!/usr/bin/env python3
"""A config typo list names every key it rejected (E-197).

Found live on a scratch tree, 2026-11-22: a config.toml with five misspelled
keys produced, on stderr, once per hook invocation::

    metodoloji: config error: Invalid [hooks] keys in /tmp/p/config.toml:
    line 2: 'code_gard' (valid: blackboard, ...); line 3: 'blackbord' (valid:
    ...); line 4: 'stop_gard' (valid: ...)

`qualty_gate` (line 5) and `freshnes` (line 6) were never mentioned — no marker,
no count — so the message read as the complete list of what to fix; the operator
fixes three, reruns, and meets the next two. Under E-196's rule a bounded list is
a claim about the whole list, and this one is bounded at three.

Claim, closed set: every key the validator rejects is either named or counted,
for both tables, in the string that reaches stderr; the bound stays at three; a
list that fits prints verbatim; the valid-key list is stated once; and the join
has exactly one definition.

Control: `--broken` runs the same probes against the tree at `PREFIX_REV`
(E-196's commit), which slices `invalid_keys[:3]` and repeats the valid list.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
PREFIX_REV = "bad08ce"  # E-196's commit; predates the counted key list
VALID_HOOKS = ("blackboard", "blackboard_backend", "code_guard", "deploy_guard",
               "freshness", "quality_gate", "stop_guard")

PROBES = (
    "hooks_all_keys_accounted",
    "hooks_singular_marker",
    "hooks_no_marker_when_it_fits",
    "valid_list_stated_once",
    "freshness_table_counted",
    "hidden_count_matches_arithmetic",
    "live_stderr_names_the_remainder",
    "live_stderr_quiet_on_valid_config",
    "live_stderr_singular_at_four",
    "one_definition_of_the_join",
)


def _tree(base: pathlib.Path, rev: str | None) -> pathlib.Path:
    """A scratch methodology root: the engine plus a config.toml we own.

    Never the working copy — every probe rewrites `config.toml`, and the
    plugin's own config decides the guard on this repository.
    """
    tree = base / "tree"
    engine = tree / "hooks" / "engine"
    engine.parent.mkdir(parents=True)
    if rev is None:
        shutil.copytree(ROOT / "hooks" / "engine", engine)
        return tree
    archive = base / f"{rev}.tar"
    with archive.open("wb") as handle:
        subprocess.run(["git", "archive", rev, "hooks/engine"], cwd=ROOT,
                       stdout=handle, stdin=subprocess.DEVNULL, check=True)
    with tarfile.open(archive) as tar:
        tar.extractall(tree)
    return tree


def _write_config(tree: pathlib.Path, keys: int, section: str = "hooks") -> None:
    body = "".join(f'typo{n} = "x"\n' for n in range(1, keys + 1))
    (tree / "config.toml").write_text(f"[{section}]\n{body}", encoding="utf-8")


def _validate(tree: pathlib.Path, section: str) -> str:
    """Run the validator in-tree and return the message ('' when valid)."""
    script = (
        "import sys; sys.path.insert(0, 'hooks/engine')\n"
        "from modules import config\n"
        f"ok, err = config._validate_{section}_config()\n"
        "print(err)\n")
    res = subprocess.run([sys.executable, "-c", script], cwd=str(tree),
                         capture_output=True, text=True,
                         stdin=subprocess.DEVNULL)
    return res.stdout.strip()


def _stderr(tree: pathlib.Path, payload: str) -> str:
    res = subprocess.run(
        [sys.executable, "hooks/engine/main.py"], cwd=str(tree),
        input=payload, capture_output=True, text=True,
        env={**__import__("os").environ, "HOOK_TYPE": "stop"})
    return res.stderr


def _probe(name: str, tree: pathlib.Path) -> tuple[bool, str]:
    if name == "one_definition_of_the_join":
        utils = (tree / "hooks" / "engine" / "modules" / "utils.py").read_text(
            encoding="utf-8")
        counting = tree / "hooks" / "engine" / "modules" / "counting.py"
        ok = ("def counted_list" not in utils and counting.is_file()
              and counting.read_text(encoding="utf-8").count(
                  "def counted_list") == 1)
        return ok, "counted_list defined once, in counting.py"

    if name == "live_stderr_names_the_remainder":
        _write_config(tree, 5)
        err = _stderr(tree, '{"cwd": "%s"}\n' % tree)
        ok = ("config error" in err and "… (+2 more keys)" in err
              and "'typo3'" in err)
        return ok, f"stderr={err.strip()[-90:]!r}"

    if name == "live_stderr_singular_at_four":
        _write_config(tree, 4)
        err = _stderr(tree, '{"cwd": "%s"}\n' % tree)
        ok = "config error" in err and "… (+1 more key)" in err
        return ok, f"stderr={err.strip()[-60:]!r}"

    if name == "live_stderr_quiet_on_valid_config":
        (tree / "config.toml").write_text('[hooks]\ncode_guard = "hard"\n',
                                          encoding="utf-8")
        err = _stderr(tree, '{"cwd": "%s"}\n' % tree)
        ok = "config error" not in err
        return ok, f"stderr={err.strip()[-60:]!r}"

    section = "freshness" if name == "freshness_table_counted" else "hooks"
    keys = {"hooks_all_keys_accounted": 5, "hooks_singular_marker": 4,
            "hooks_no_marker_when_it_fits": 3, "valid_list_stated_once": 5,
            "freshness_table_counted": 5,
            "hidden_count_matches_arithmetic": 8}[name]
    _write_config(tree, keys, section)
    message = _validate(tree, section)

    if name == "hooks_all_keys_accounted":
        ok = (message.count("line ") == 3 and "… (+2 more keys)" in message
              and "typo1" in message and "typo3" in message)
        return ok, f"msg={message[-110:]!r}"

    if name == "hooks_singular_marker":
        ok = "… (+1 more key)" in message
        return ok, f"msg={message[-80:]!r}"

    if name == "hooks_no_marker_when_it_fits":
        ok = "more key" not in message and message.count("line ") == 3
        return ok, f"msg={message[-80:]!r}"

    if name == "valid_list_stated_once":
        ok = message.count("(valid: ") == 1 and all(
            key in message for key in VALID_HOOKS)
        return ok, f"valid_mentions={message.count('(valid: ')}"

    if name == "freshness_table_counted":
        ok = "[freshness]" in message and "… (+2 more keys)" in message
        return ok, f"msg={message[-80:]!r}"

    if name == "hidden_count_matches_arithmetic":
        match = re.search(r"\(\+(\d+) more keys?\)", message)
        named = message.count("line ")
        ok = bool(match) and named + int(match.group(1)) == keys
        return ok, f"named={named} hidden={match.group(1) if match else '?'}"

    raise SystemExit(f"unknown probe: {name}")


def _run(tree: pathlib.Path, samples: pathlib.Path | None, broken: bool) -> bool:
    rows, passed = [], 0
    for name in PROBES:
        try:
            ok, detail = _probe(name, tree)
        except Exception as exc:  # a crashed probe is a failed probe
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        passed += 1 if ok else 0
        rows.append({"probe": name, "ok": ok, "detail": detail})
        print(f"  {'PASS' if ok else 'FAIL'}  {name}  — {detail}")
    total = len(PROBES)
    print(f"config_key_accounting_accuracy={passed / total:.2f} ({passed}/{total})")
    if samples is not None:
        samples.parent.mkdir(parents=True, exist_ok=True)
        with samples.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps({"broken": broken, **row},
                                        ensure_ascii=False) + "\n")
    return passed == total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=pathlib.Path, default=None)
    parser.add_argument("--broken", action="store_true")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        # the working copy is copied in, so uncommitted work is measured too
        tree = _tree(base, PREFIX_REV if args.broken else None)
        print(f"tree: {tree}")
        ok = _run(tree, args.samples, args.broken)
    return 0 if (ok or args.broken) else 1


if __name__ == "__main__":
    raise SystemExit(main())

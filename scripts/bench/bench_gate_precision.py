#!/usr/bin/env python3
"""The gate's own edges stop lying (E-174).

    python3 scripts/bench/bench_gate_precision.py --samples docs/experiments/E-174/raw/samples.jsonl
    python3 scripts/bench/bench_gate_precision.py --broken   # rule-7 control

Four edges of the shipped engine report things that are not true, and each one
was observed live (2026-11-16, analysing the Agently session):

1. `deploy_gate` matches the bare word `deploy` ANYWHERE, so a read-only
   command that merely named `deploy/docker-compose.yml` was blocked with
   "Deploy blocked".
2. `bash_targets` turns any quoted dotted token into a write candidate, so
   reading a board key (`b['epics.agently']`) was denied as "No approved
   experiment record for epics.agently".
3. `freshness` counts EVERY dirty file against a claim that declares no Code
   Scope, so a tracked runtime log (`M .metodoloji/logs/hook-audit.log`) left
   SP-001 permanently STALE — while the same function's other branch already
   says an unscoped record has "nothing to stale".
4. `_prior_attempts` counts substring hits across the whole audit trail, so a
   first denial reported "attempt #59" for a one-character target.

Every probe drives the tree's own modules, and the expected verdict is stated
once here — nothing is sampled. `--broken` runs the same probes against the
pre-fix tree, so the control fails for exactly the reasons this change names.
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
PREFIX_REV = "44f7bbc"  # last commit before the gate's four edges were repaired
BROKEN = False

_PREFIX_CACHE: pathlib.Path | None = None


def _tree() -> pathlib.Path:
    return ROOT if not BROKEN else _prefix_tree()


def _prefix_tree() -> pathlib.Path:
    global _PREFIX_CACHE
    if _PREFIX_CACHE is not None:
        return _PREFIX_CACHE
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="gate-precision-prefix-"))
    out = subprocess.run(["git", "-C", str(ROOT), "archive", PREFIX_REV],
                         capture_output=True, stdin=subprocess.DEVNULL)
    if out.returncode != 0:
        raise RuntimeError(f"cannot archive {PREFIX_REV}: {out.stderr[:200]!r}")
    subprocess.run(["tar", "-x", "-C", str(tmp)], input=out.stdout, check=True)
    _PREFIX_CACHE = tmp
    return tmp


# --- driver: runs one probe against ONE tree, in its own process ------------
# A separate process per probe keeps two trees' `modules` packages from
# colliding in sys.modules; the bench itself only aggregates verdicts.

def _git(root: pathlib.Path, *args: str) -> None:
    env = dict(os.environ, GIT_AUTHOR_NAME="bench", GIT_AUTHOR_EMAIL="b@b",
               GIT_COMMITTER_NAME="bench", GIT_COMMITTER_EMAIL="b@b")
    subprocess.run(["git", "-C", str(root), *args], check=True,
                   capture_output=True, env=env, stdin=subprocess.DEVNULL)


def _repo() -> pathlib.Path:
    root = pathlib.Path(tempfile.mkdtemp(prefix="gate-precision-"))
    (root / "docs" / "experiments").mkdir(parents=True)
    (root / "src").mkdir()
    (root / ".metodoloji" / "logs").mkdir(parents=True)
    (root / "docs" / "experiments" / "E-999.md").write_text(
        "## Experiment: E-999 — fixture\n", encoding="utf-8")
    (root / "src" / "a.ts").write_text("export const a = 1;\n", encoding="utf-8")
    (root / ".metodoloji" / "logs" / "hook-audit.log").write_text("", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "fixture")
    return root


def _driver(name: str, tree: pathlib.Path) -> tuple[bool, str]:
    sys.path.insert(0, str(tree / "hooks" / "engine"))
    sys.path.insert(0, str(tree / "hooks" / "scripts"))

    if name.startswith("deploy_"):
        from modules.deploy_gate import DEPLOY_CMD_RE
        commands = {
            # a read-only command that only NAMES a deploy path
            "deploy_readonly": "python3 -c \"print('deploy/docker-compose.yml')\"",
            "deploy_shell": "./deploy.sh",
            "deploy_npm": "npm run deploy",
            "deploy_git": "git push origin main",
        }
        hit = bool(DEPLOY_CMD_RE.search(commands[name]))
        want = name != "deploy_readonly"
        return hit == want, f"command={commands[name]!r} detected={hit} want={want}"

    if name.startswith("inline_"):
        from modules.bash_targets import extract_bash_targets_ex
        commands = {
            # unrecognizably-read-only code that only READS a board key
            "inline_key": ("python3 -c \"import sqlite3; b=sqlite3.connect("
                           "'.metodoloji/blackboard.sqlite').execute("
                           "'select data from board').fetchone(); "
                           "print(b['epics.agently'])\""),
            "inline_write": "python3 -c \"open('src/gen.ts','w').write('x')\"",
            # destination built at runtime: must stay fail-closed
            "inline_opaque": "python3 -c \"p='out'; open(p+'.py','w').write('x')\"",
            # same read, delivered on stdin instead of -c
            "inline_stdin": ("python3 - <<'PY'\n"
                             "import sqlite3\n"
                             "b=sqlite3.connect('.metodoloji/blackboard.sqlite')"
                             ".execute('q').fetchone()\n"
                             "print(b['epics.agently'])\nPY"),
        }
        targets, dropped = extract_bash_targets_ex(commands[name])
        if name in ("inline_key", "inline_stdin"):
            ok = "epics.agently" not in targets and not dropped
            return ok, f"targets={targets} dropped={dropped}"
        if name == "inline_write":
            return "src/gen.ts" in targets, f"targets={targets}"
        return dropped, f"targets={targets} dropped={dropped}"

    if name.startswith("freshness_"):
        from modules import freshness
        root = _repo()
        try:
            head = freshness.head(str(root))
            tree_digest = freshness.tree_digest(str(root))
            dirty = (root / ".metodoloji" / "logs" / "hook-audit.log"
                     if "outside" in name or name == "freshness_unscoped"
                     else root / "src" / "a.ts")
            with dirty.open("a", encoding="utf-8") as fh:
                fh.write("// moved\n")
            scope = "" if name in ("freshness_unscoped", "freshness_no_record") else "src/**"
            run_key = "E-998" if name == "freshness_no_record" else "E-999"
            # freshness_no_record has no record AND no stored witness: an
            # anchorless claim can only read UNKNOWN, never a fake CURRENT.
            anchor = "" if name == "freshness_no_record" else head
            witness_tree = "" if name == "freshness_no_record" else tree_digest
            verdict, detail = freshness.classify(
                str(root), commit=anchor, scope=scope, tree=witness_tree,
                run_key=run_key)
            want_map = {"freshness_unscoped": freshness.CURRENT,
                        "freshness_scoped": freshness.STALE,
                        "freshness_outside": freshness.CURRENT,
                        "freshness_no_record": freshness.UNKNOWN}
            want = want_map[name]
            return verdict == want, f"verdict={verdict} want={want} reason={detail.get('reason')}"
        finally:
            shutil.rmtree(root, ignore_errors=True)

    if name.startswith("attempt_"):
        from modules import guard
        root = _repo()
        try:
            log = root / ".metodoloji" / "logs" / "hook-audit.log"
            log.write_text(
                '{"tool":"Bash","input":{"command":"echo x src/app.ts"}}\n',
                encoding="utf-8")
            short = guard._prior_attempts(str(root), "x")
            exact = guard._prior_attempts(str(root), "src/app.ts")
            if name == "attempt_short":
                return short == 0, f"prior(x)={short} want=0"
            return exact == 1, f"prior(src/app.ts)={exact} want=1"
        finally:
            shutil.rmtree(root, ignore_errors=True)

    if name.startswith("skeleton_"):
        import skeleton
        root = pathlib.Path(tempfile.mkdtemp(prefix="gate-precision-seed-"))
        try:
            if name == "skeleton_preserves":
                (root / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
            skeleton.install(root)
            if name == "skeleton_idempotent":
                skeleton.install(root)
            ignore = root / ".gitignore"
            text = ignore.read_text(encoding="utf-8") if ignore.is_file() else ""
            if name == "skeleton_preserves":
                ok = ".metodoloji/" in text and "node_modules/" in text
            elif name == "skeleton_idempotent":
                ok = text.count(".metodoloji/") == 1
            else:
                ok = ".metodoloji/" in text
            return ok, f"gitignore={text!r}"
        finally:
            shutil.rmtree(root, ignore_errors=True)

    raise ValueError(f"unknown probe {name!r}")


CASES = [
    "deploy_readonly", "deploy_shell", "deploy_npm", "deploy_git",
    "inline_key", "inline_stdin", "inline_write", "inline_opaque",
    "freshness_unscoped", "freshness_scoped", "freshness_outside",
    "freshness_no_record",
    "attempt_short", "attempt_exact",
    "skeleton_gitignore", "skeleton_idempotent", "skeleton_preserves",
]


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
    print(f"gate_edge_accuracy={passed / len(results):.2f} ({passed}/{len(results)})")
    if _PREFIX_CACHE is not None:
        shutil.rmtree(_PREFIX_CACHE, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

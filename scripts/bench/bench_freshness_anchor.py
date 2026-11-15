#!/usr/bin/env python3
"""A record is anchored to the code state it was measured against — one owner (E-160).

    python3 scripts/bench/bench_freshness_anchor.py --samples docs/experiments/E-160/raw/samples.jsonl
    python3 scripts/bench/bench_freshness_anchor.py --broken   # rule-7 control

Measured 2026-11-14 on this tree: the freshness plane had TWO implementations of
"what anchors a record's claim". `freshness.annotate()` is the owner — it dates
a claim to the commit that last touched its record (the derived witness). The
write-path consumer, `guard._record_freshness()`, re-implemented the anchor by
hand: it read the record's `Witness` line and classified from THAT commit. The
two disagreed on 41 of 155 shipped records (the guard said UNKNOWN where the
owner said STALE), and both were wrong in the same direction for the newest
records: the `Witness` line is written at HEAD, and the commit that lands the
record then edits the record's own `Code Scope`. So `changed_since(witness)`
counted the landing commit itself and every record was STALE at birth — 110 of
144 scoped records, and 9 of them ONLY because of their own landing commit.

That made `config.toml`'s documented hardening precondition unachievable: it
says to set `[hooks] freshness = "deny"` once "the reported STALE set is
empty", and the set can never empty. The plane could therefore never enforce.

The population is closed and derived, never hand-listed (E-039): every shipped
`docs/experiments/E-*.md`, plus one throwaway git repo for the dirty-tree probe.

  1. the guard and the owner agree      — every record, same verdict from both
  2. no record is born stale            — a record whose scope did not move
                                          after its own landing commit reads
                                          CURRENT
  3. real drift is still caught         — a commit inside the scope, landing
                                          AFTER the record, reads STALE (the
                                          born-stale fix must not swallow it)
  4. no dead witness declaration        — every module-level compiled regex in
                                          the gate is read somewhere

`--broken` re-derives the pre-fix anchor (classify from the `Witness` commit,
no tree digest) and must score below the threshold. Stdlib + git, offline,
deterministic.
"""

from __future__ import annotations

import argparse
import ast
import json
import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
BROKEN = False

sys.path.insert(0, str(ROOT / "hooks" / "engine"))

RECORDS = ROOT / "docs" / "experiments"
GATE = ROOT / "skills" / "bmad-research-experiment" / "scripts" / "run_experiment.py"

_WITNESS_COMMIT_RE = re.compile(r"commit=([0-9a-f]{7,40}|none)")
# Records spell the field as `- **Key:** value` (the colon inside the bold).
_FIELD_RE = re.compile(r"^\s*[-*]\s*\*\*(?P<key>[^*]+?):?\*\*:?\s*(?P<val>.*)$")


def _modules():
    from modules import freshness as fr  # noqa: E402
    from modules import guard  # noqa: E402
    return fr, guard


def _fields(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        m = _FIELD_RE.match(line)
        if m:
            out[m.group("key").strip()] = m.group("val").strip()
    return out


def _records() -> list[pathlib.Path]:
    return sorted(p for p in RECORDS.glob("E-*.md") if p.name != "_template.md")


def _witness_commit(text: str) -> str:
    m = _WITNESS_COMMIT_RE.search(_fields(text).get("Witness", ""))
    if not m or m.group(1) == "none":
        return ""
    return m.group(1)


def _scope(text: str) -> str:
    return _fields(text).get("Code Scope", "").strip()


def _legacy_verdict(fr, root: str, text: str) -> str:
    """The pre-fix anchor: classify from the `Witness` commit, no tree digest."""
    verdict, _detail = fr.classify(root, commit=_witness_commit(text), scope=_scope(text))
    return verdict


def _case_one_owner() -> tuple[bool, str]:
    """Every record gets the same verdict from the guard and from the owner."""
    fr, guard = _modules()
    disagree = []
    with fr.batch():
        for rec in _records():
            key = rec.stem
            owner = fr.annotate(str(ROOT), key)["verdict"]
            consumer = (guard._record_freshness(str(ROOT), str(rec)) or {}).get("verdict")
            if owner != consumer:
                disagree.append(f"{key}: owner={owner} guard={consumer}")
    if disagree:
        return (False, "; ".join(disagree[:4]) + ("…" if len(disagree) > 4 else ""))
    return (True, f"{len(_records())} record(s): the guard and the owner agree")


def _case_no_born_stale() -> tuple[bool, str]:
    """A record whose scope did not move after its landing commit reads CURRENT.

    The landing commit edits the record's own `Code Scope`, so anchoring to the
    parent makes every record stale at birth. This enumerates the exact set: a
    record is born stale when nothing moved after its own commit yet it still
    reads STALE.
    """
    fr, guard = _modules()
    born = []
    with fr.batch():
        for rec in _records():
            key = rec.stem
            if fr.superseded_by(str(ROOT), key):
                continue  # a declared supersede is a real STALE, not birth
            landing = subprocess.run(
                ["git", "-C", str(ROOT), "log", "-1", "--format=%H", "--",
                 rec.relative_to(ROOT).as_posix()],
                capture_output=True, text=True, encoding="utf-8",
                stdin=subprocess.DEVNULL)
            commit = landing.stdout.strip()
            if not commit:
                continue  # uncommitted record: no landing commit to anchor to
            scope = _scope(rec.read_text(encoding="utf-8", errors="replace"))
            if not scope or scope.lower() == "none":
                continue
            # `changed_since` returns the scoped diff plus every dirty path, so
            # the scope filter is applied here exactly as `classify` does.
            after = [p for p in (fr.changed_since(str(ROOT), commit, scope) or [])
                     if fr.scope_matches(scope, p)]
            if after:
                continue  # the code genuinely moved after it landed
            got = (guard._record_freshness(str(ROOT), str(rec)) or {}).get("verdict")
            if got == fr.STALE:
                born.append(key)
    if born:
        return (False, f"{len(born)} record(s) stale with nothing moved after "
                       f"their own landing commit: {born[:6]}")
    return (True, "no record is stale only because of its own landing commit")


def _case_real_drift_is_caught() -> tuple[bool, str]:
    """A commit inside the scope, landing AFTER the record, still reads STALE.

    This is the half the born-stale fix must not swallow: anchoring past a
    record's own landing commit is only legal for THAT commit. Anything that
    moves afterwards is exactly the drift E-053 exists to catch.
    """
    fr, guard = _modules()
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        (root / "src").mkdir()
        (root / "src" / "a.py").write_text("# a\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "init", "-q"], check=True,
                       stdin=subprocess.DEVNULL)
        _commit(root, "base")
        head = fr.head(str(root))
        rec = root / "docs" / "experiments" / "E-900.md"
        rec.parent.mkdir(parents=True)
        rec.write_text(
            "## Experiment: E-900 — fixture\n"
            "- **Status:** completed\n"
            "- **Code Scope:** src/**\n"
            f"- **Witness:** commit={head} tree=none scope=none\n"
            "- **Decision:** APPROVED — fixture\n", encoding="utf-8")
        _commit(root, "land the record")
        fr.reset_cache()
        landed = (guard._record_freshness(str(root), str(rec)) or {}).get("verdict")
        if landed == fr.STALE:
            return (False, "the record read STALE immediately after its own landing commit")
        (root / "src" / "a.py").write_text("# a v2\n", encoding="utf-8")
        _commit(root, "later scoped change")
        fr.reset_cache()
        after = (guard._record_freshness(str(root), str(rec)) or {}).get("verdict")
        if after != fr.STALE:
            return (False, f"a later in-scope commit read {after!r}, not STALE")
        return (True, "CURRENT at its own landing commit, STALE after later drift")


def _case_no_dead_witness_declaration() -> tuple[bool, str]:
    """Every module-level compiled regex in the gate is read somewhere."""
    tree = ast.parse(GATE.read_text(encoding="utf-8"))
    names = [n.targets[0].id for n in tree.body
             if isinstance(n, ast.Assign) and len(n.targets) == 1
             and isinstance(n.targets[0], ast.Name)
             and isinstance(n.value, ast.Call)
             and getattr(n.value.func, "attr", "") == "compile"]
    dead = [name for name in names
            if sum(1 for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id == name) <= 1]
    if dead:
        return (False, f"declared and never read: {dead}")
    return (True, f"all {len(names)} module-level regexes are read")


def _commit(root, message: str) -> None:
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True,
                   stdin=subprocess.DEVNULL)
    subprocess.run(["git", "-C", str(root), "-c", "user.email=t@e.invalid",
                    "-c", "user.name=t", "commit", "-qm", message], check=True,
                   stdin=subprocess.DEVNULL)


PROBES = (
    ("the guard and the owner agree", _case_one_owner),
    ("no record is born stale", _case_no_born_stale),
    ("real drift is still caught", _case_real_drift_is_caught),
    ("no dead witness declaration", _case_no_dead_witness_declaration),
)


def run() -> list[dict]:
    results = []
    for name, probe in PROBES:
        try:
            ok, detail = probe()
        except Exception as exc:  # a probe that cannot answer is a failure
            ok, detail = False, f"probe error: {type(exc).__name__}: {exc}"
        results.append({"check": name, "ok": bool(ok), "detail": detail})
    return results


def main() -> int:
    global BROKEN
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", default=None,
                    help="write one JSON object per line here (the gate recomputes x/n)")
    ap.add_argument("--broken", action="store_true",
                    help="rule-7 control: re-derive the pre-fix anchor")
    args = ap.parse_args()
    BROKEN = args.broken

    if BROKEN:
        # The pre-fix consumer anchored to the Witness commit and dropped the
        # tree digest. Re-point the guard at that legacy answer so the control
        # measures the old behaviour on the same tree.
        fr, guard = _modules()

        def _legacy(root: str, rec_path: str):
            text = pathlib.Path(rec_path).read_text(encoding="utf-8", errors="replace")
            return {"verdict": _legacy_verdict(fr, root, text), "reason": "pre-fix anchor",
                    "changed": [], "commit": _witness_commit(text),
                    "record": pathlib.Path(rec_path).name}

        guard._record_freshness = _legacy

    results = run()
    if args.samples:
        path = pathlib.Path(args.samples)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for item in results:
                fh.write(json.dumps(item, ensure_ascii=False) + "\n")

    ok = sum(1 for r in results if r["ok"])
    n = len(results)
    for r in results:
        print(f"  {'ok  ' if r['ok'] else 'FAIL'}  {r['check']}: {r['detail']}")
    print(f"freshness_anchor_accuracy={ok / n:.2f} ({ok}/{n})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

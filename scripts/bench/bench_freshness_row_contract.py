#!/usr/bin/env python3
"""The freshness row carries every key its consumers read (E-165).

    python3 scripts/bench/bench_freshness_row_contract.py --samples docs/experiments/E-165/raw/samples.jsonl
    python3 scripts/bench/bench_freshness_row_contract.py --broken   # rule-7 control

Measured 2026-11-15 on this tree: E-160 deleted `guard._record_freshness`'s
hand-rolled anchor and delegated to `freshness.annotate`, the plane's one owner.
The guard's returned row is read by TWO call sites, and they do not read the
same fields:

  * `guard._freshness_chain_row` reads `verdict`, `reason`, `changed`
  * `scope_gate.scope_advisory` reads `fresh['commit']` for its remedy line

`annotate` had the commit in hand but did not return it, so the delegation
silently dropped a key the write path indexes unconditionally: any APPROVED
record whose scope matched and read STALE raised `KeyError: 'commit'` on the
write path — a crash where a warning was meant. Nothing caught it because the
guard's own test stubs `_record_freshness` with a hand-built dict that happens
to carry the key.

The probe is the contract, not the incident: every key ANY consumer indexes out
of the row must be a key the producer returns, checked by reading both sides
rather than by a hand list (E-039).

`--broken` re-derives the pre-fix row (no `commit`) and must score below the
threshold. Stdlib + ast, offline, deterministic.
"""

from __future__ import annotations

import argparse
import ast
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
BROKEN = False

sys.path.insert(0, str(ROOT / "hooks" / "engine"))

GUARD = ROOT / "hooks" / "engine" / "modules" / "guard.py"
SCOPE_GATE = ROOT / "hooks" / "engine" / "modules" / "scope_gate.py"
FRESHNESS = ROOT / "hooks" / "engine" / "modules" / "freshness.py"

# The producers whose row is under contract. A binding is a consumer only if it
# is assigned from one of these calls (E-039: derive, never name-guess — a
# variable called `fresh` may hold a different producer's row entirely).
_PRODUCERS = ("_record_freshness", "annotate")


def _consumer_bindings(path: pathlib.Path) -> dict[str, set[str]]:
    """Every string key indexed out of a name assigned from a producer call."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        call = node.value
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if called in _PRODUCERS:
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    found: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Subscript):
            continue
        value = node.value
        if not (isinstance(value, ast.Name) and value.id in names):
            continue
        sl = node.slice
        if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
            found.setdefault(value.id, set()).add(sl.value)
    return found


def _annotate_keys() -> set[str]:
    """The keys `freshness.annotate` returns, read from its return statement."""
    tree = ast.parse(FRESHNESS.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "annotate":
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Return) or not isinstance(inner.value, ast.Dict):
                continue
            keys = set()
            for k in inner.value.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    keys.add(k.value)
            if keys:
                return keys
    return set()


def _case_every_consumed_key_is_produced() -> tuple[bool, str]:
    """No consumer indexes a key the producer does not return."""
    produced = _annotate_keys()
    if not produced:
        return (False, "annotate's return dict could not be read — the probe's "
                       "own population is empty")
    if BROKEN:
        produced.discard("commit")  # the pre-fix shape
    missing: dict[str, list[str]] = {}
    for path in (GUARD, SCOPE_GATE):
        for binding, keys in _consumer_bindings(path).items():
            for key in sorted(keys - produced):
                missing.setdefault(key, []).append(
                    f"{path.name}:{binding}[{key!r}]")
    if missing:
        detail = "; ".join(f"{key!r} read by {', '.join(where)}"
                           for key, where in sorted(missing.items()))
        return (False, f"a consumer indexes a key annotate does not return: {detail}")
    return (True, f"every key the consumers index ({len(produced)} produced) is returned")


def _case_the_live_annotate_carries_the_contract() -> tuple[bool, str]:
    """A real annotate() call carries every consumed key, not just the source's."""
    from modules import freshness as fr  # noqa: E402
    record = next((p.stem for p in sorted(
        (ROOT / "docs" / "experiments").glob("E-*.md"))), "")
    if not record:
        return (False, "no record to annotate — the probe's population is empty")
    row = fr.annotate(str(ROOT), record)
    if BROKEN:
        row = {k: v for k, v in row.items() if k != "commit"}
    consumed: set[str] = set()
    for path in (GUARD, SCOPE_GATE):
        for keys in _consumer_bindings(path).values():
            consumed |= keys
    missing = sorted(consumed - set(row))
    if missing:
        return (False, f"annotate({record}) omits {missing}")
    return (True, f"annotate({record}) carries all {len(consumed)} consumed key(s)")


PROBES = (
    ("every consumed key is produced", _case_every_consumed_key_is_produced),
    ("the live annotate carries the contract", _case_the_live_annotate_carries_the_contract),
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
                    help="rule-7 control: drop the key the fix restored")
    args = ap.parse_args()
    BROKEN = args.broken

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
    print(f"freshness_row_contract_accuracy={ok / n:.2f} ({ok}/{n})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

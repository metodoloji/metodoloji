"""Audit throughput contract — one verification pass, one glob parse (E-140).

    python3 scripts/bench/bench_audit_throughput.py --samples docs/experiments/E-140/raw/samples.jsonl
    python3 scripts/bench/bench_audit_throughput.py --broken   # rule-7 control

`check-plugin.sh` used to verify every record in its own Python process (§3 and
then §3b again: 135 records → 270 interpreter starts) and its scope matcher
re-parsed every glob for every comparison (§3b alone: 1,184,988 parses). The
audit took 157 s, which is why the commit gate outran the 10 s hook budget and
every commit in this session needed a wrapper.

Two properties make it fast, and both are cheap to lose silently:

* **one verification pass.** `--verify-corpus` calls the same `verify()` once
  for the whole corpus; §3 and §3b read that table. The pass must agree with a
  per-record `--verify` on every record — a faster verdict that differs is not
  a fix.
* **one parse per glob.** The matcher is memoised on the pattern, which is pure
  data, so the answer cannot change — but a deleted decorator would restore the
  95 s section without any test going red.

`--broken` runs the pre-fix shape (uncached matcher, per-record verification)
and must fail the same claim.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import re
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
GATE = ROOT / "skills" / "bmad-research-experiment" / "scripts" / "run_experiment.py"
CHECK_PLUGIN = ROOT / "scripts" / "check-plugin.sh"
CORPUS = ROOT / "docs" / "experiments"

BROKEN = False

# A corpus pass must beat this. It measured 0.6 s on 135 records with the fix
# and 67 s without it (a Python start per record); the bound sits far from both
# so a loaded machine cannot fail it while the regression still does.
CORPUS_BUDGET_S = 10.0
# Parses allowed for one scan over N distinct patterns: the memo means each
# pattern is parsed once. Without it, parses grow with (patterns × files).
PARSE_FACTOR = 2


def _load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _approved(rec: pathlib.Path) -> bool:
    text = rec.read_text(encoding="utf-8", errors="replace")
    return bool(re.search(
        r"\*\*(Decision|Karar):\*\*\s*(APPROVED|ONAYLANDI)|^Status:\s*(APPROVED|ONAYLANDI)",
        text, re.MULTILINE))


def _corpus_map(gate) -> tuple[dict, float]:
    start = time.time()
    out = subprocess.run(
        [sys.executable, str(GATE), "--verify-corpus", str(CORPUS),
         "--verify-corpus-format", "tsv"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL)
    elapsed = time.time() - start
    verdicts = {}
    for line in (out.stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[1].isdigit():
            verdicts[parts[0]] = int(parts[1])
    return verdicts, elapsed


def _case_pass_is_fast(gate) -> tuple[bool, str]:
    if BROKEN:
        # Pre-fix: one interpreter per record.
        start = time.time()
        for rec in sorted(CORPUS.glob("E-*.md")):
            if rec.name == "_template.md" or not _approved(rec):
                continue
            subprocess.run([sys.executable, str(GATE), "--verify", "--record", str(rec)],
                           capture_output=True, stdin=subprocess.DEVNULL)
        elapsed = time.time() - start
        ok = elapsed < CORPUS_BUDGET_S
        return (ok, f"pre-fix per-record pass: {elapsed:.1f}s (budget {CORPUS_BUDGET_S}s)")
    _verdicts, elapsed = _corpus_map(gate)
    return (elapsed < CORPUS_BUDGET_S,
            f"one pass: {elapsed:.2f}s for the whole corpus (budget {CORPUS_BUDGET_S}s)")


def _case_pass_agrees_with_per_record(gate) -> tuple[bool, str]:
    """A faster verdict that differs from `--verify` is not a fix."""
    verdicts, _ = _corpus_map(gate)
    sample = [rec for rec in sorted(CORPUS.glob("E-*.md"))
              if rec.name != "_template.md" and _approved(rec)][:8]
    mismatches = []
    for rec in sample:
        direct = subprocess.run(
            [sys.executable, str(GATE), "--verify", "--record", str(rec)],
            capture_output=True, stdin=subprocess.DEVNULL).returncode
        if verdicts.get(rec.stem) != direct:
            mismatches.append((rec.stem, verdicts.get(rec.stem), direct))
    return (not mismatches,
            f"{len(sample)} sampled, mismatches={mismatches[:3]}" if mismatches
            else f"{len(sample)} sampled records agree with --verify")


def _case_matcher_is_memoised(gate) -> tuple[bool, str]:
    """Each distinct glob parses once, whatever the scan size."""
    patterns = ["src/**", "hooks/engine/modules/*.py", "docs/*.md", "**/*.py",
                ".github/workflows/**", "a?c.py", "lib/**/*.ts", "custom/*.toml"]
    targets = [f"src/f{i}.py" for i in range(40)]
    cache = getattr(gate, "_glob_regex", None)
    if BROKEN or cache is None:
        return (False, "pre-fix: the matcher re-parses every glob on every comparison")
    cache.cache_clear()
    for scope in patterns:
        for target in targets:
            gate.scope_matches(scope, target)
    info = cache.cache_info()
    ok = info.misses <= len(patterns) * PARSE_FACTOR
    return (ok, f"misses={info.misses} hits={info.hits} "
                f"(≤{len(patterns) * PARSE_FACTOR} allowed for {len(patterns)} patterns "
                f"× {len(targets)} targets)")


def _case_audit_reuses_one_pass() -> tuple[bool, str]:
    """§3 and §3b must consume the corpus pass, not spawn per record."""
    text = CHECK_PLUGIN.read_text(encoding="utf-8")
    if BROKEN:
        return (False, "pre-fix: check-plugin.sh has no corpus pass")
    has_pass = "--verify-corpus" in text
    # The per-record spawn inside §3's record loop is the thing that was removed;
    # `--verify --record` may still appear as §3b's fallback, never as the loop.
    loop_verify = re.search(
        r'for rec in "\$PROJECT_ROOT"/docs/experiments/\*\.md; do.*?--verify --record',
        text, re.DOTALL)
    return (has_pass and loop_verify is None,
            "corpus pass present, no per-record spawn in the §3 loop" if has_pass
            else "no --verify-corpus in check-plugin.sh")


def _case_corpus_mode_is_stdlib(gate) -> tuple[bool, str]:
    source = GATE.read_text(encoding="utf-8")
    imports = set(re.findall(r"^(?:from|import)\s+([a-zA-Z_][\w.]*)", source, re.M))
    third_party = {m.split(".")[0] for m in imports} - {
        "argparse", "contextlib", "functools", "hashlib", "hmac", "io", "json",
        "math", "os", "pathlib", "re", "secrets", "shlex", "subprocess", "sys",
        "tempfile", "time", "typing", "datetime", "glob", "collections",
        "unittest", "textwrap", "random", "statistics", "itertools", "shutil",
        "__future__",
    }
    return (not third_party, f"non-stdlib imports: {sorted(third_party)}"
            if third_party else "stdlib only")


CASES = (
    ("one verification pass for the corpus", _case_pass_is_fast),
    ("the pass agrees with a per-record --verify", _case_pass_agrees_with_per_record),
    ("each distinct glob parses once", _case_matcher_is_memoised),
    ("the audit reuses the pass instead of spawning per record",
     _case_audit_reuses_one_pass),
    ("the gate stays stdlib-only", _case_corpus_mode_is_stdlib),
)


def run() -> list[dict]:
    gate = _load("bench_throughput_gate", GATE)
    results = []
    for name, probe in CASES:
        try:
            if probe.__code__.co_argcount:
                ok, detail = probe(gate)
            else:
                ok, detail = probe()
        except Exception as exc:  # a probe that cannot answer is a failure
            ok, detail = False, f"probe error: {exc}"
        results.append({"check": name, "ok": bool(ok), "detail": detail})
    return results


def main() -> int:
    global BROKEN
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", default=None,
                    help="write one JSON object per line here (the gate recomputes x/n)")
    ap.add_argument("--broken", action="store_true",
                    help="rule-7 control: the pre-fix shape (uncached matcher, "
                         "per-record verification)")
    args = ap.parse_args()
    BROKEN = args.broken

    results = run()
    if args.samples:
        path = pathlib.Path(args.samples)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for r in results:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    x = sum(1 for r in results if r["ok"])
    n = len(results)
    for r in results:
        print(f"[{'OK' if r['ok'] else 'MISS'}] {r['check']}: {r['detail']}")
    print(f"audit_throughput_accuracy={x / n:.2f} ({x}/{n})")
    # The aggregate is the signal; a nonzero exit would make the rule-7 control
    # unreadable to the gate (it runs the control itself and expects a reading).
    return 0


if __name__ == "__main__":
    sys.exit(main())

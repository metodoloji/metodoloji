"""The record-status vocabulary has one owner (DW-9, E-145).

    python3 scripts/bench/bench_status_vocabulary.py --samples docs/experiments/E-145/raw/samples.jsonl
    python3 scripts/bench/bench_status_vocabulary.py --broken   # rule-7 control

`check-methodology.sh` CHECK 4 carried its own hand-typed alternation
(`planned|PENDING|BEKLİYOR`) while `scripts/create-record.py` owned the schema
status set — two copies that only agreed by hand, the same shape E-047/E-048
closed for the version manifests. The drift bit: E-110's record opened with
`proposed`, a word neither copy knew, so it classified as "status unknown",
which failed `test_check4_classifies_every_shipped_record`. The gate runs pytest
as its falsifier, so the record rejected itself while it was being measured —
and a record that cannot be measured cannot be fixed.

The audit now asks the owner (`create-record.py --status-regex experiment`).
`--broken` classifies with the PRE-FIX section, read from git, so the control is
the old reader rather than a paraphrase of it.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check-methodology.sh"
OWNER = ROOT / "scripts" / "create-record.py"
PREFIX_REV = "HEAD"          # still carries the hand-typed alternation

BROKEN = False


def _section(text: str) -> str:
    i = text.index("CHECK 4: Experiment Records")
    j = text.index("CHECK 5: Quality Records", i)
    return text[i:j]


def _patterns(section: str, fallback: str | None = None) -> list[str]:
    """The section's grep patterns in source order, with the variable resolved."""
    raw = re.findall(r'grep -qE? "([^"]+)"', section)
    return [p.replace("$EXP_PENDING_RE", fallback or "") for p in raw]


def _derived() -> str:
    out = subprocess.run([sys.executable, str(OWNER), "--status-regex", "experiment"],
                         capture_output=True, text=True, encoding="utf-8",
                         stdin=subprocess.DEVNULL)
    return out.stdout.strip() if out.returncode == 0 else ""


def _prefix_text() -> str:
    out = subprocess.run(
        ["git", "-C", str(ROOT), "show", f"{PREFIX_REV}:scripts/check-methodology.sh"],
        capture_output=True, text=True, encoding="utf-8",
        stdin=subprocess.DEVNULL)
    if out.returncode != 0:
        raise RuntimeError(f"cannot read {PREFIX_REV}:scripts/check-methodology.sh")
    return out.stdout


def _prefix_section() -> str:
    return _section(_prefix_text())


def _classifier():
    """(patterns, label) — the shipped reader, or the pre-fix one."""
    if BROKEN:
        text = _prefix_section()
        return (_patterns(text), "pre-fix (git HEAD)")
    text = SCRIPT.read_text(encoding="utf-8")
    return (_patterns(_section(text), _derived()), "shipped")


def _branch(patterns: list[str], body: str):
    for index, pattern in enumerate(patterns):
        if re.search(pattern, body):
            return index
    return None


def _case_single_owner() -> tuple[bool, str]:
    """CHECK 4 must ask the owner, not restate the words."""
    text = SCRIPT.read_text(encoding="utf-8")
    section = _section(text)
    if BROKEN:
        return (False, "pre-fix: CHECK 4 restates the pending words inline")
    ok = ("$EXP_PENDING_RE" in section
          and "--status-regex" in section
          and _derived() != "")
    return (ok, "CHECK 4 derives the vocabulary from create-record.py" if ok
            else "the audit still carries its own copy")


def _case_schema_words_classify() -> tuple[bool, str]:
    patterns, label = _classifier()
    misses = []
    for word in ("planned", "proposed", "draft", "PENDING", "BEKLİYOR",
                 "in-progress"):
        if _branch(patterns, f"- **Status:** {word}") != 2:
            misses.append(word)
    return (not misses, f"{label}: unclassified draft words {misses}" if misses
            else f"{label}: every draft word classifies as pending")


def _case_unknown_stays_diagnostic() -> tuple[bool, str]:
    """The fix must not degrade to 'everything is pending'."""
    patterns, label = _classifier()
    leaks = [junk for junk in ("compleeted", "???", "")
             if _branch(patterns, f"- **Status:** {junk}") is not None]
    return (not leaks, f"{label}: a non-status word still classifies ({leaks})"
            if leaks else f"{label}: a genuine non-status stays unknown")


def _case_end_to_end_proposed_record() -> tuple[bool, str]:
    """A `proposed` record must read as PENDING in a real audit run."""
    with tempfile.TemporaryDirectory() as td:
        proj = pathlib.Path(td)
        (proj / "docs" / "experiments").mkdir(parents=True)
        (proj / "docs" / "experiments" / "E-001.md").write_text(
            "## Experiment: E-001 — proposed draft\n\n- **Date:** 2026-11-13\n"
            "- **Status:** proposed\n- **Theory:** x\n", encoding="utf-8")
        env = dict(os.environ)
        env["CLAUDE_PROJECT_DIR"] = str(proj)
        env["METODOLOJI_PLUGIN_ROOT"] = str(ROOT)
        env.pop("OPENHANDS_PROJECT_DIR", None)
        target = SCRIPT
        if BROKEN:
            # Run the PRE-FIX script, not the shipped one, or the control would
            # be measuring the fix it is supposed to falsify.
            target = pathlib.Path(td) / "scripts" / "check-methodology.sh"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_prefix_text(), encoding="utf-8")
        r = subprocess.run(["sh", str(target), "--only", "4"], cwd=str(proj),
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env, stdin=subprocess.DEVNULL)
    lines = [ln for ln in r.stdout.splitlines() if "E-001" in ln]
    if BROKEN:
        # The pre-fix reader has no such branch: it reports the record unknown.
        ok = bool(lines) and "status unknown" in lines[0]
        return (ok, f"pre-fix would say: {lines}")
    ok = bool(lines) and "is PENDING" in lines[0]
    return (ok, f"{lines[0].strip() if lines else '<no line>'}")


def _case_fallback_is_a_superset() -> tuple[bool, str]:
    """With no interpreter the historical list must still cover every draft word."""
    section = _section(SCRIPT.read_text(encoding="utf-8"))
    fallback = re.search(r'^EXP_PENDING_RE="([^"]+)"', section, re.M)
    if BROKEN or not fallback:
        return (False, "pre-fix: the vocabulary is the fallback itself")
    have = set(fallback.group(1).split("|"))
    derived = set(_derived().split("|"))
    missing = sorted(w for w in derived if w not in have)
    return (not missing, f"fallback covers {len(have)} words" if not missing
            else f"fallback misses {missing}")


CASES = (
    ("the audit asks the vocabulary owner", _case_single_owner),
    ("every draft word classifies as pending", _case_schema_words_classify),
    ("a genuine non-status stays unknown", _case_unknown_stays_diagnostic),
    ("a proposed record reads as PENDING end to end",
     _case_end_to_end_proposed_record),
    ("the no-interpreter fallback is a superset", _case_fallback_is_a_superset),
)


def run() -> list[dict]:
    results = []
    for name, probe in CASES:
        try:
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
                    help="rule-7 control: classify with the pre-fix section from git")
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
    print(f"status_vocabulary_accuracy={x / n:.2f} ({x}/{n})")
    # The aggregate is the signal; a nonzero exit would make the rule-7 control
    # unreadable to the gate (it runs the control itself and expects a reading).
    return 0


if __name__ == "__main__":
    sys.exit(main())

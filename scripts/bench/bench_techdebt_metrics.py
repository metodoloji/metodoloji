"""Tech-debt metrics contract — the reporting block is derived, not trusted (E-143).

    python3 scripts/bench/bench_techdebt_metrics.py --samples docs/experiments/E-143/raw/samples.jsonl
    python3 scripts/bench/bench_techdebt_metrics.py --broken   # rule-7 control

`scripts/check-techdebt.sh` guards the inventory's tables: ID collisions, the
P0 limit, orphan markers, malformed references, template placeholders. None of
those reads the *reporting* block, and a wrong number is not a placeholder —
`Total active debt: 0` is a perfectly valid sentence next to a full active
table, and a trend table can stop at a sprint the record series has long
passed. That staleness was real in this repository: SP-017 (2026-11-09) had no
trend row, and nothing said so.

§8 derives the block from the ledger's own rows — active counts per priority
subsection, the paid figure over the last three recorded sprints, one trend row
per sprint record, and the trend's final total — and fails when they disagree.

`--broken` runs the same fixtures against the checker with §8 stripped: the
pre-fix reader, which must fail the claims. Deterministic, offline, stdlib +
POSIX shell only.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHECK = ROOT / "scripts" / "check-techdebt.sh"
TEMPLATE = ROOT / "templates" / "tech-debt.md"
LIVE = ROOT / "docs" / "development" / "tech-debt.md"
SPRINTS = ROOT / "docs" / "development"

BROKEN = False


def _fixture(td: pathlib.Path, ledger: str | None = None,
             checker: str | None = None) -> pathlib.Path:
    """A minimal plugin tree the checker runs against hermetically."""
    root = td / "plugin"
    (root / "scripts").mkdir(parents=True)
    (root / "templates").mkdir(parents=True)
    (root / "docs" / "development").mkdir(parents=True)
    (root / "hooks").mkdir()
    (root / "hooks" / "app.py").write_text("x = 1\n", encoding="utf-8")
    (root / "scripts" / "check-techdebt.sh").write_text(
        checker if checker is not None else CHECK.read_text(encoding="utf-8"),
        encoding="utf-8")
    shutil.copy(TEMPLATE, root / "templates" / "tech-debt.md")
    (root / "docs" / "development" / "tech-debt.md").write_text(
        ledger if ledger is not None else LIVE.read_text(encoding="utf-8"),
        encoding="utf-8")
    for sp in sorted(SPRINTS.glob("SP-*.md")):
        shutil.copy(sp, root / "docs" / "development" / sp.name)
    return root


def _strip_section8(checker: str) -> str:
    """The pre-fix checker: §8 removed, everything else untouched."""
    start = checker.index('echo "== 8)')
    end = checker.index('echo\nif [ "$PROBLEMS" -eq 0 ]', start)
    return checker[:start] + checker[end:]


def _run(root: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run(["sh", str(root / "scripts" / "check-techdebt.sh")],
                          cwd=str(root), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120,
                          stdin=subprocess.DEVNULL)


def _ledger_text() -> str:
    return LIVE.read_text(encoding="utf-8")


def _probe(ledger: str | None, expect: str) -> tuple[bool, str]:
    """Run one fixture; `expect` is a substring the audit must print ("" = clean)."""
    checker = None
    if BROKEN:
        checker = _strip_section8(CHECK.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as td:
        root = _fixture(pathlib.Path(td), ledger=ledger, checker=checker)
        r = _run(root)
    if expect:
        ok = r.returncode == 1 and expect in r.stdout
        detail = f"rc={r.returncode} " + (
            "caught" if ok else f"missed (tail={r.stdout[-160:]!r})")
    else:
        ok = r.returncode == 0
        detail = f"rc={r.returncode} " + ("healthy" if ok else f"red ({r.stdout[-160:]!r})")
    return (ok, detail)


def _case_clean_ledger() -> tuple[bool, str]:
    return _probe(None, "")


def _case_wrong_total() -> tuple[bool, str]:
    ledger = _ledger_text().replace(
        "- **Total active debt:** 0 items", "- **Total active debt:** 3 items", 1)
    return _probe(ledger, "Total active debt says 3")


def _case_wrong_priority() -> tuple[bool, str]:
    ledger = _ledger_text().replace(
        "  - P1 (High): 0 items", "  - P1 (High): 2 items", 1)
    return _probe(ledger, "P1 says 2")


def _case_missing_sprint_row() -> tuple[bool, str]:
    rows = [ln for ln in _ledger_text().splitlines()
            if not ln.startswith("SP-017 (")]
    return _probe("\n".join(rows) + "\n", "no row for")


def _case_future_sprint_row() -> tuple[bool, str]:
    ledger = _ledger_text().replace(
        "SP-017 (11-09)      | 0        | 0         | 0          | 0",
        "SP-017 (11-09)      | 0        | 0         | 0          | 0\n"
        "SP-077 (12-01)      | 0        | 0         | 0          | 0", 1)
    return _probe(ledger, "SP-077")


def _case_wrong_paid_window() -> tuple[bool, str]:
    ledger = _ledger_text().replace(
        "- **Debt paid in the last 3 sprints:** 1 item",
        "- **Debt paid in the last 3 sprints:** 4 items", 1)
    return _probe(ledger, "Debt paid in the last 3 sprints says 4")


def _case_section_exists() -> tuple[bool, str]:
    text = CHECK.read_text(encoding="utf-8")
    hit = 'echo "== 8)' in text and "--verify-corpus" not in text
    return (hit, "§8 present" if hit else "the checker carries no metrics section")


CASES = (
    ("the checker carries a metrics section", _case_section_exists),
    ("a consistent ledger is healthy", _case_clean_ledger),
    ("a wrong active total is caught", _case_wrong_total),
    ("a wrong priority count is caught", _case_wrong_priority),
    ("a sprint record with no trend row is caught", _case_missing_sprint_row),
    ("a trend row beyond the series is caught", _case_future_sprint_row),
    ("a wrong paid-in-window figure is caught", _case_wrong_paid_window),
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
                    help="rule-7 control: the checker with §8 stripped")
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
    print(f"techdebt_metrics_accuracy={x / n:.2f} ({x}/{n})")
    # The aggregate is the signal; a nonzero exit would make the rule-7 control
    # unreadable to the gate (it runs the control itself and expects a reading).
    return 0


if __name__ == "__main__":
    sys.exit(main())

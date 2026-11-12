#!/usr/bin/env python3
"""Classify legacy experiment records as `experiment` or `contract`.

The methodology historically filed deterministic test-suite runs as
"experiments", complete with Wilson intervals that describe a population the
tests never sampled. `Record Kind` fixes that for NEW records; this migration
makes the distinction visible in the EXISTING corpus without rewriting any
decision.

Two modes:

  --report   (default) print the proposed classification, change nothing.
  --apply    write `- **Record Kind:** <kind>` into records that lack the field.

Classification is mechanical and conservative:

  * A record whose claim asserts 100% (`== 1.0` / `>= 1.0`) over a declared
    census is a **contract**: the cases are the population, not a sample.
  * A record whose measurement is a shipped test suite (the Measurement
    Command names pytest/unittest) is a **contract** candidate.
  * A record that declares a census but hedges its threshold (`>= 0.999`) is
    reported as a *candidate only*: tightening the claim is a new measurement,
    not a migration.
  * Everything else stays `experiment` (the default) — no downgrade, no
    silent reclassification.

The migration never touches `Decision`, `Gate Evidence`, `Uncertainty` or any
gate-written line, so every token keeps verifying. It is idempotent: a record
that already carries `Record Kind` is skipped.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

_FIELD_RE = re.compile(r"^\s*-\s*\*\*([^*]+):\*\*\s*(.*)$")
_CONTRACT_CLAIM_RE = re.compile(r"(?:==|>=)\s*1(?:\.0+)?\s*[\"']?\s*$")
_TEST_CMD_RE = re.compile(r"\b(?:pytest|py\.test|unittest|test_)\b", re.IGNORECASE)
_CENSUS_MIN = 40


def _fields(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = _FIELD_RE.match(line)
        if m:
            out.setdefault(m.group(1).strip(), m.group(2).strip())
    return out


def _hypothesis_claim(hypothesis: str) -> str:
    m = re.search(r'"([^"]+)"', hypothesis or "")
    return m.group(1) if m else ""


def classify(text: str) -> tuple[str, str]:
    """(kind, reason) for one record body.

    A `contract` means the claim asserts 100% over a closed, enumerated
    population — the user's rule: a deterministic test over a closed set either
    passes every case or it does not. A record that merely DECLARES a census but
    hedges its threshold (>= 0.999) is reported as a candidate, never silently
    rewritten: tightening the claim is a new measurement, not a migration.
    """
    fields = _fields(text)
    if fields.get("Record Kind", "").strip():
        return fields["Record Kind"].strip().lower(), "already declared"
    hypothesis = fields.get("Hypothesis", "")
    claim = _hypothesis_claim(hypothesis)
    census = fields.get("Census", "").strip()
    command = fields.get("Measurement Command", "")

    if claim and _CONTRACT_CLAIM_RE.search(claim) and len(census) >= _CENSUS_MIN:
        return "contract", "100% claim over a declared closed-set census"
    if len(census) >= _CENSUS_MIN and claim:
        return "experiment", ("candidate: declares a closed census but claims "
                              "a hedged threshold — re-measure as a contract "
                              "with '== 1.0' if every case must pass")
    if command and _TEST_CMD_RE.search(command):
        return "contract", "measurement command runs a shipped test suite"
    return "experiment", "stochastic/unspecified claim — default kind"


def _insert_kind(text: str, kind: str) -> str:
    """Insert `Record Kind` after the Status line (or before Theory)."""
    lines = text.splitlines()
    if any(_FIELD_RE.match(l) and _FIELD_RE.match(l).group(1).strip() == "Record Kind"
           for l in lines):
        return text
    insert_at = None
    for i, line in enumerate(lines):
        m = _FIELD_RE.match(line)
        if m and m.group(1).strip() == "Status":
            insert_at = i + 1
            break
    if insert_at is None:
        for i, line in enumerate(lines):
            m = _FIELD_RE.match(line)
            if m and m.group(1).strip() == "Theory":
                insert_at = i
                break
    if insert_at is None:
        return text
    lines.insert(insert_at, f"- **Record Kind:** {kind}")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Classify legacy E records by kind")
    ap.add_argument("--records-dir", default="docs/experiments")
    ap.add_argument("--apply", action="store_true",
                    help="write the classification (default is report-only)")
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    args = ap.parse_args()

    base = pathlib.Path(args.records_dir)
    if not base.is_dir():
        print(f"ERROR: not a directory: {base}", file=sys.stderr)
        return 2

    rows = []
    for rec in sorted(base.glob("E-*.md")):
        if rec.name.startswith("_"):
            continue
        try:
            text = rec.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not _fields(text).get("Hypothesis"):
            continue
        kind, reason = classify(text)
        rows.append((rec, kind, reason))
        if args.apply and reason != "already declared" and kind == "contract":
            rec.write_text(_insert_kind(text, kind), encoding="utf-8")

    if args.json:
        import json
        print(json.dumps([{"record": str(r), "kind": k, "reason": why}
                          for r, k, why in rows], indent=2))
    else:
        for rec, kind, reason in rows:
            print(f"{kind:<10} {rec.name:<28} {reason}")
        contracts = sum(1 for _, k, _ in rows if k == "contract")
        print()
        print(f"{len(rows)} record(s): {contracts} contract, "
              f"{len(rows) - contracts} experiment")
        if not args.apply:
            print("report-only — re-run with --apply to write Record Kind into "
                  "legacy contract records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Contract test for check-plugin.sh §6 (development records format).

§6 classifies every development record by its Decision/Status field: the
record's prefix (IR/SP/S/QR/PR) must carry a value from that prefix's
vocabulary, and the record must have a Date. Two drifts met there, both
against a source of truth that already existed:

1. Field FORM. §6 only grepped the bold bullet (`- **Status:** planned`) that
   the IR/SP/PR templates use, while bridge §2.3 specifies the S record as a
   field table — which is what create-methodology-record.py writes. Every
   generated S record therefore warned "no Decision/Status field", and the
   full health check reported a problem on a correct record.
2. VOCABULARY. §6's S vocabulary lacked `ready-for-dev`, the value the engine
   itself accepts (guard._validate_story_metadata) and the sprint-status
   template documents. A checker narrower than the engine flags valid records.

The patterns and vocabularies are read from the shipped script, so these tests
fail if the script stops accepting a canonical form or falls behind the engine.
"""

import re
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))

from hooks.engine.modules import guard  # noqa: E402

SCRIPT = PLUGIN / "scripts" / "check-plugin.sh"
DEV_DIR = PLUGIN / "docs" / "development"


def _s6_section() -> str:
    text = SCRIPT.read_text(encoding="utf-8", errors="replace")
    i = text.index("== 6) Development records format check ==")
    j = text.index("== 6a)", i)
    return text[i:j]


S6 = _s6_section()
# The shell's `||` chain, in source order. The greps run without -E, so a `|`
# in one of them is the LITERAL table-column pipe (BRE), never an alternation —
# in Python it would be, and `^| *Status *|` would then match every line.
FIELD_PATTERNS = [p.replace("|", r"\|") for p in re.findall(r"grep -m1 '([^']+)'", S6)]


def _allowed(prefix: str) -> list[str]:
    m = re.search(rf"{re.escape(prefix)}-\*\)\s+ALLOWED=\"([^\"]+)\"", S6)
    assert m, f"§6 must define a vocabulary for {prefix}"
    return m.group(1).split()


def _status_value(text: str) -> str:
    """The value §6 reads from *text*: first matching pattern, first line, sed.

    Mirrors `grep -m1 '<pattern>'` over the `||` chain, then the extraction
    sed on that single line.
    """
    for pattern in FIELD_PATTERNS:
        for line in text.splitlines():
            if re.search(pattern, line):
                dec = re.sub(r".*\*\*(Decision|Status|Karar|Durum):\*\* *", "", line)
                dec = re.sub(r"^\| *[^|]*\| *", "", dec)   # table form
                dec = re.sub(r"[|→—].*", "", dec)
                return dec.strip()
    return ""


def _engine_story_statuses() -> list[str]:
    """Every story status the engine accepts — probed, not hard-coded."""
    candidates = ["backlog", "ready-for-dev", "sprint", "in-progress",
                  "review", "done", "blocked"]
    accepted = [s for s in candidates
                if guard._validate_story_metadata(f"Status: {s}\n")[0]]
    assert "backlog" in accepted and "done" in accepted, accepted
    return accepted


def test_s6_reads_both_canonical_field_forms():
    assert _status_value("- **Status:** planned") == "planned"
    assert _status_value("| Status | ready-for-dev |") == "ready-for-dev"
    assert _status_value("- **Decision:** READY — rationale here") == "READY"
    assert _status_value("| Decision | READY |") == "READY"


def test_s6_accepts_a_date_in_either_form():
    assert r"grep -q '\*\*Date:\*\*'" in S6
    assert "grep -q '^| *Date *|'" in S6, "table form must be accepted"


def test_s6_vocabulary_covers_every_status_the_engine_accepts():
    allowed = _allowed("S")
    missing = [s for s in _engine_story_statuses() if s not in allowed]
    assert not missing, f"§6 would flag engine-valid statuses: {missing}"


def test_every_shipped_development_record_satisfies_s6(tmp_path):
    """The records in the tree must pass the checker that judges them.

    Record-less plugin repo (the shipped record set was pruned 2026-10-06): when
    the tree ships no development records, synthesize one canonical record per
    prefix so the checker's field-form and vocabulary contract still runs — an
    empty scan would otherwise assert nothing.
    """
    # Name-matched, not globbed: on Windows a `[A-Z]*-*.md` glob also matches
    # README.md / tech-debt.md (case-insensitive filesystem). The slug
    # suffix is accepted so slugged records (S-056-gate-id-guard.md) are
    # validated too — not silently skipped.
    record_re = re.compile(r"(IR|SP|QR|PR|PM|S)-\d+(?:-[A-Za-z0-9-]+)?\.md$")
    records = sorted(p for p in DEV_DIR.rglob("*.md") if record_re.search(p.name))
    if not records:
        synth = tmp_path / "docs" / "development"
        synth.mkdir(parents=True, exist_ok=True)
        for prefix in ("IR", "SP", "QR", "PR", "S"):
            allowed = _allowed(prefix)
            assert allowed, f"§6 must define a vocabulary for {prefix}"
            rec = synth / f"{prefix}-001.md"
            rec.write_text(f"# {prefix}-001\n- **Date:** 2026-10-06\n"
                           f"- **Status:** {allowed[0]}\n", encoding="utf-8")
            records.append(rec)
    assert records, "no development records to check"
    problems = []
    for record in records:
        text = record.read_text(encoding="utf-8", errors="replace")
        prefix = record.name.split("-", 1)[0]
        dec = _status_value(text)
        if not dec or dec not in _allowed(prefix):
            problems.append(f"{record.name}: '{dec}' not in {_allowed(prefix)}")
    assert not problems, problems

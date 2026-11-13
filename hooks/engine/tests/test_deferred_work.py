"""The deferred-work ledger parser — one source, four surfaces (E-108/E-139).

`state.deferred_work_inventory` is what the orient digest, `check-methodology`
CHECK 9, the commit advisory and the integration bench all read. It had no unit
tests: every case below is one the shipped tree or a real ledger produced, and
the failures are the ones that made the number a reader acts on lie.

The three that matter most:

* **legacy bullets are open work.** A ledger with nine un-migrated findings
  reported `DW=0` in the digest — the count said "nothing to do" about real
  deferred work. `open_total` is the fix.
* **a mixed ledger must not over-count.** Counting bullets to EOF charged a
  later section's bullets (and a canonical entry's own sub-list) as legacy.
* **duplicate DW numbers are a defect, not a style choice.** The format says
  entries are never renumbered, so two `DW-3:` blocks make every reference
  ambiguous.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

PLUGIN = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PLUGIN / "hooks" / "engine"))

from modules import state  # noqa: E402

LEDGER_REL = "docs/development/native/deferred-work.md"


def _ledger(tmp_path: pathlib.Path, text: str) -> pathlib.Path:
    target = tmp_path / LEDGER_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return tmp_path


CANONICAL = """# Deferred Work

### DW-1: a real finding
origin: code review of S-001, 2026-11-03
location: src/a.py:10
severity: high
reason: needs a follow-up record
status: open

### DW-2: closed one
origin: quick-dev, 2026-11-03
location: src/b.py:20
severity: low
reason: cosmetic
status: done 2026-11-03
"""

LEGACY = """# Deferred work

## Deferred from: code review of S-001 (2026-11-03)

- first finding worth doing later
- second finding
"""

MIXED = """# Deferred work

## Deferred from: code review of S-001 (2026-11-03)

- one legacy finding

### DW-1: canonical finding
origin: code review of S-002, 2026-11-04
location: src/a.py:1
severity: medium
reason: deferred
status: open

## Deferred from: consistency repair (2026-11-05)

- another legacy finding
- and a third
"""


def test_canonical_counts_open_done_total(tmp_path):
    info = state.deferred_work_inventory(_ledger(tmp_path, CANONICAL))
    assert (info["total"], info["open"], info["done"]) == (2, 1, 1)
    assert info["malformed"] == 0
    assert info["legacy_items"] == 0
    assert info["open_total"] == 1
    assert info["newest_seq"] == 2


def test_missing_ledger_is_zeros_not_an_error(tmp_path):
    info = state.deferred_work_inventory(tmp_path)
    assert info["path"] == ""
    assert (info["total"], info["open"], info["open_total"]) == (0, 0, 0)
    assert info["error"] == ""


def test_legacy_bullets_are_outstanding_work(tmp_path):
    """The defect this closes: nine real findings read as `DW=0`."""
    info = state.deferred_work_inventory(_ledger(tmp_path, LEGACY))
    assert info["legacy"] is True
    assert info["legacy_items"] == 2
    assert info["total"] == 0
    # The number a reader acts on counts them.
    assert info["open_total"] == 2


def test_mixed_ledger_counts_each_side_once(tmp_path):
    """Bullets are counted inside their own section, never to EOF."""
    info = state.deferred_work_inventory(_ledger(tmp_path, MIXED))
    assert info["legacy_items"] == 3          # 1 + 2, not 4
    assert info["open"] == 1
    assert info["open_total"] == 4
    assert info["legacy"] is False            # canonical entries exist
    assert info["newest_seq"] == 1


def test_fenced_examples_are_documentation_not_entries(tmp_path):
    text = """# Deferred Work

The shape:

```markdown
### DW-1: <title>
status: open
```

## Deferred from: example

```markdown
- a bullet inside a fence
```
"""
    info = state.deferred_work_inventory(_ledger(tmp_path, text))
    assert info["total"] == 0
    assert info["legacy_items"] == 0


def test_unterminated_fence_is_malformed(tmp_path):
    text = "# Deferred Work\n\n```markdown\n### DW-1: x\nstatus: open\n"
    info = state.deferred_work_inventory(_ledger(tmp_path, text))
    assert info["malformed"] >= 1


def test_missing_status_is_malformed(tmp_path):
    text = ("# Deferred Work\n\n### DW-1: no status\norigin: x\nreason: y\n")
    info = state.deferred_work_inventory(_ledger(tmp_path, text))
    assert info["malformed"] == 1
    assert info["open"] == 0


def test_unrecognized_status_is_malformed(tmp_path):
    text = "# Deferred Work\n\n### DW-1: x\nstatus: maybe\n"
    info = state.deferred_work_inventory(_ledger(tmp_path, text))
    assert info["malformed"] == 1


def test_duplicate_numbers_are_reported(tmp_path):
    text = ("# Deferred Work\n\n### DW-3: first\nstatus: open\n\n"
            "### DW-3: second\nstatus: open\n")
    info = state.deferred_work_inventory(_ledger(tmp_path, text))
    assert info["duplicate_seq"] == 1
    assert info["total"] == 2
    assert info["newest_seq"] == 3


def test_severity_is_optional_but_counted(tmp_path):
    text = ("# Deferred Work\n\n### DW-1: no severity\nstatus: open\n\n"
            "### DW-2: bad severity\nseverity: urgent\nstatus: open\n\n"
            "### DW-3: good\nseverity: HIGH\nstatus: open\n")
    info = state.deferred_work_inventory(_ledger(tmp_path, text))
    assert info["severity_unspecified"] == 2
    assert info["open"] == 3


def test_record_inventory_exposes_the_open_work_number(tmp_path):
    root = _ledger(tmp_path, LEGACY)
    dw = state.record_inventory(root)["DW"]
    assert dw["count"] == 2                   # legacy bullets are open work
    assert dw["legacy_items"] == 2
    assert dw["newest"] == "legacy"


def test_record_inventory_omits_absent_ledger(tmp_path):
    assert "DW" not in state.record_inventory(tmp_path)


def test_populated_legacy_path_wins_over_empty_canonical(tmp_path):
    """E-108 review: an empty canonical ledger must not hide a real one."""
    empty = tmp_path / LEDGER_REL
    empty.parent.mkdir(parents=True, exist_ok=True)
    empty.write_text("# Deferred Work\n", encoding="utf-8")
    old = tmp_path / "bmad-output" / "implementation-artifacts" / "deferred-work.md"
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_text(LEGACY, encoding="utf-8")
    info = state.deferred_work_inventory(tmp_path)
    assert info["path"].endswith("bmad-output/implementation-artifacts/deferred-work.md")
    assert info["legacy_items"] == 2

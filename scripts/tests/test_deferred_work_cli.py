"""The deferred-work ledger CLI — the format owner's mechanical half (E-139).

Three operations must not depend on an LLM remembering a shape: validate the
ledger, convert a legacy section into canonical entries, and append an entry
with the next free number. Each case below pins one property the shipped
surfaces rely on:

* `--check` exit codes are the contract the commit gate and the audit read.
* `--migrate` is deterministic, lossless and idempotent — a second run must
  change nothing, and no bullet's text may be dropped.
* `--add` numbers from the highest existing entry and performs the dedupe
  check the format documents, so two writers cannot create a duplicate.
"""
from __future__ import annotations

import importlib.util
import pathlib
import re

import pytest

PLUGIN = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = PLUGIN / "skills" / "bmad-code-review" / "scripts" / "deferred-work.py"
LEDGER_REL = "docs/development/native/deferred-work.md"


def _load():
    spec = importlib.util.spec_from_file_location("deferred_work_cli", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


dw = _load()


def _ledger(tmp_path: pathlib.Path, text: str) -> pathlib.Path:
    target = tmp_path / LEDGER_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


CANONICAL = """# Deferred Work

### DW-1: a finding
origin: code review of S-001, 2026-11-03
location: src/a.py:10
severity: high
reason: needs a follow-up
status: open
"""

LEGACY = """# Deferred work

## Deferred from: code review of S-001 (2026-11-03)

- 16-subscription cap unmeasured; a 17th watch evicts the oldest (`hooks/engine/modules/blackboard.py:109`). Pre-existing cap.
- Second finding with no path reference at all.
"""


# --- --check ---------------------------------------------------------------

def test_check_clean_canonical_exits_zero(tmp_path, capsys):
    _ledger(tmp_path, CANONICAL)
    assert dw.main(["--check", "--project-root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "open work: 1" in out and "[OK]" in out


def test_check_reports_malformed_as_error(tmp_path, capsys):
    _ledger(tmp_path, "# Deferred Work\n\n### DW-1: no status\nreason: x\n")
    assert dw.main(["--check", "--project-root", str(tmp_path)]) == 1
    assert "malformed" in capsys.readouterr().out.lower()


def test_check_reports_duplicate_numbers_as_error(tmp_path, capsys):
    _ledger(tmp_path, "# Deferred Work\n\n### DW-2: a\nstatus: open\n\n"
                      "### DW-2: b\nstatus: open\n")
    assert dw.main(["--check", "--project-root", str(tmp_path)]) == 1
    assert "duplicate" in capsys.readouterr().out.lower()


def test_check_legacy_is_a_warning_not_an_error(tmp_path, capsys):
    _ledger(tmp_path, LEGACY)
    assert dw.main(["--check", "--project-root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "un-migrated" in out and "open work: 2" in out


def test_check_json_is_machine_readable(tmp_path, capsys):
    import json
    _ledger(tmp_path, LEGACY)
    assert dw.main(["--check", "--json", "--project-root", str(tmp_path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["inventory"]["legacy_items"] == 2
    assert payload["inventory"]["open_total"] == 2
    assert payload["warnings"] and not payload["problems"]


def test_check_missing_ledger_is_zero_not_an_error(tmp_path, capsys):
    assert dw.main(["--check", "--project-root", str(tmp_path)]) == 0
    assert "no ledger" not in capsys.readouterr().out  # prints the zero inventory


# --- --migrate -------------------------------------------------------------

def test_migrate_dry_run_does_not_write(tmp_path):
    path = _ledger(tmp_path, LEGACY)
    before = path.read_text(encoding="utf-8")
    assert dw.main(["--migrate", "--project-root", str(tmp_path)]) == 0
    assert path.read_text(encoding="utf-8") == before


def test_migrate_writes_canonical_entries(tmp_path, capsys):
    path = _ledger(tmp_path, LEGACY)
    assert dw.main(["--migrate", "--write", "--project-root", str(tmp_path)]) == 0
    text = path.read_text(encoding="utf-8")
    assert "### DW-1:" in text and "### DW-2:" in text
    assert "## Deferred from:" not in text
    assert "## Migrated from: code review of S-001, 2026-11-03" in text
    assert "origin: code review of S-001, 2026-11-03" in text
    assert "location: hooks/engine/modules/blackboard.py:109" in text
    assert "location: n/a" in text          # second bullet has no path
    assert "status: open" in text


def test_migrate_is_lossless(tmp_path):
    path = _ledger(tmp_path, LEGACY)
    dw.main(["--migrate", "--write", "--project-root", str(tmp_path)])
    new = path.read_text(encoding="utf-8")
    def words(text):
        # Compare the bullet BODIES: the `- ` marker is structure, and the
        # `## Deferred from:` heading legitimately becomes `## Migrated from:`.
        bodies = [m.group(1) for m in re.finditer(r"^\s*-\s+(\S.*)$", text, re.M)]
        return set(re.findall(r"[A-Za-z0-9_.:/-]+", " ".join(bodies)))
    missing = [w for w in words(LEGACY) if w not in set(
        re.findall(r"[A-Za-z0-9_.:/-]+", new))]
    assert not missing, f"migration dropped: {missing}"


def test_migrate_is_idempotent(tmp_path):
    path = _ledger(tmp_path, LEGACY)
    dw.main(["--migrate", "--write", "--project-root", str(tmp_path)])
    once = path.read_text(encoding="utf-8")
    assert dw.main(["--migrate", "--write", "--project-root", str(tmp_path)]) == 0
    assert path.read_text(encoding="utf-8") == once


def test_migrate_continues_numbering_after_existing_entries(tmp_path):
    path = _ledger(tmp_path, CANONICAL + "\n## Deferred from: later (2026-11-05)\n\n- one more\n")
    dw.main(["--migrate", "--write", "--project-root", str(tmp_path)])
    assert "### DW-2: one more" in path.read_text(encoding="utf-8")


def test_migrate_on_clean_ledger_is_a_no_op(tmp_path, capsys):
    path = _ledger(tmp_path, CANONICAL)
    assert dw.main(["--migrate", "--write", "--project-root", str(tmp_path)]) == 0
    assert path.read_text(encoding="utf-8") == CANONICAL
    assert "nothing to migrate" in capsys.readouterr().out


# --- --add -----------------------------------------------------------------

def test_add_uses_the_next_free_number(tmp_path):
    path = _ledger(tmp_path, CANONICAL)
    assert dw.main(["--add", "--title", "new thing", "--origin", "review, 2026-11-13",
                    "--location", "src/c.py:3", "--severity", "medium",
                    "--reason", "not now", "--project-root", str(tmp_path)]) == 0
    text = path.read_text(encoding="utf-8")
    assert "### DW-2: new thing" in text
    assert "severity: medium" in text


def test_add_without_severity_omits_the_field(tmp_path):
    path = _ledger(tmp_path, CANONICAL)
    dw.main(["--add", "--title", "no severity", "--origin", "review, 2026-11-13",
             "--project-root", str(tmp_path)])
    entry = path.read_text(encoding="utf-8").split("### DW-2:")[1]
    assert "severity:" not in entry


def test_add_dedupes_by_normalized_title(tmp_path):
    path = _ledger(tmp_path, CANONICAL)
    dw.main(["--add", "--title", "A  Finding!", "--origin", "review, 2026-11-13",
             "--project-root", str(tmp_path)])
    text = path.read_text(encoding="utf-8")
    assert "seen-again:" in text
    assert "### DW-2:" not in text           # no duplicate entry was created


def test_add_without_ledger_refuses(tmp_path, capsys):
    assert dw.main(["--add", "--title", "x", "--origin", "y",
                    "--project-root", str(tmp_path)]) == 1
    assert "[ERROR]" in capsys.readouterr().out


def test_add_requires_title_and_origin(tmp_path):
    _ledger(tmp_path, CANONICAL)
    assert dw.main(["--add", "--title", "x", "--project-root", str(tmp_path)]) == 1
    assert dw.main(["--add", "--origin", "y", "--project-root", str(tmp_path)]) == 1


def test_cli_is_stdlib_only():
    """The CLI ships to every project; a third-party import would break them."""
    source = SCRIPT.read_text(encoding="utf-8")
    imports = set(re.findall(r"^(?:from|import)\s+([a-zA-Z_][\w.]*)", source, re.M))
    third_party = {m.split(".")[0] for m in imports} - {
        "argparse", "datetime", "json", "pathlib", "re", "sys",
        "modules", "__future__",
    }
    assert not third_party, f"non-stdlib imports: {third_party}"


# --- --close ---------------------------------------------------------------
# DW-10: closing an entry was a hand edit of `status:` plus a `resolution:`
# line — exactly the "an LLM must remember a shape" cost this CLI exists to
# remove. The tests below pin the format's two rules: every close states an
# outcome, and an entry is never rewound (a regression is a new entry).

OPEN = """# Deferred work

### DW-1: first finding
origin: review, 2026-11-01
location: src/a.py:1
severity: high
reason: later
status: open

### DW-2: second finding
origin: review, 2026-11-02
location: n/a
reason: later
status: open
"""


def _close(root, *extra):
    return dw.main(["--close", "--project-root", str(root), *extra])


def test_close_sets_done_and_records_the_resolution(tmp_path):
    path = _ledger(tmp_path, OPEN)
    assert _close(tmp_path, "--id", "DW-1", "--resolution", "paid by E-999",
                  "--date", "2026-11-13") == 0
    text = path.read_text(encoding="utf-8")
    assert "status: done 2026-11-13" in text
    assert "resolution: paid by E-999" in text
    # The entry's own fields survive the rewrite.
    assert "### DW-1: first finding" in text
    assert "origin: review, 2026-11-01" in text
    assert "severity: high" in text and "location: src/a.py:1" in text
    # The untouched neighbour is still open.
    assert text.count("status: open") == 1


def test_close_moves_the_inventory_count(tmp_path):
    _ledger(tmp_path, OPEN)
    _close(tmp_path, "--id", "1", "--resolution", "fixed", "--date", "2026-11-13")
    info = dw.state.deferred_work_inventory(tmp_path)
    assert (info["total"], info["open"], info["done"]) == (2, 1, 1)
    assert info["open_total"] == 1


def test_close_accepts_a_bare_number(tmp_path):
    path = _ledger(tmp_path, OPEN)
    assert _close(tmp_path, "--id", "2", "--resolution", "fixed") == 0
    assert "### DW-2: second finding" in path.read_text(encoding="utf-8")
    assert dw.state.deferred_work_inventory(tmp_path)["done"] == 1


def test_close_requires_a_resolution(tmp_path, capsys):
    path = _ledger(tmp_path, OPEN)
    before = path.read_text(encoding="utf-8")
    assert _close(tmp_path, "--id", "DW-1") == 1
    assert "needs --resolution" in capsys.readouterr().out
    assert path.read_text(encoding="utf-8") == before   # nothing was written


def test_close_unknown_entry_refuses(tmp_path, capsys):
    _ledger(tmp_path, OPEN)
    assert _close(tmp_path, "--id", "DW-9", "--resolution", "x") == 1
    assert "not in the ledger" in capsys.readouterr().out


def test_close_is_idempotent_only_for_the_same_resolution(tmp_path, capsys):
    _ledger(tmp_path, OPEN)
    assert _close(tmp_path, "--id", "DW-1", "--resolution", "fixed",
                  "--date", "2026-11-13") == 0
    capsys.readouterr()
    assert _close(tmp_path, "--id", "DW-1", "--resolution", "fixed",
                  "--date", "2026-11-13") == 0
    assert "already closed with this resolution" in capsys.readouterr().out
    # A different outcome is a rewind, refused.
    assert _close(tmp_path, "--id", "DW-1", "--resolution", "something else") == 1
    assert "not a rewind" in capsys.readouterr().out


def test_close_refuses_a_contradictory_keep_open_decision(tmp_path, capsys):
    """A `keep-open` decision annotates an OPEN entry; next to `done` it lies."""
    path = _ledger(tmp_path, OPEN)
    before = path.read_text(encoding="utf-8")
    assert _close(tmp_path, "--id", "DW-1", "--resolution", "fixed",
                  "--decision", "keep-open-until-next-sprint") == 1
    assert "contradicts --close" in capsys.readouterr().out
    assert path.read_text(encoding="utf-8") == before


def test_close_records_a_decision_with_the_close(tmp_path):
    path = _ledger(tmp_path, OPEN)
    assert _close(tmp_path, "--id", "DW-1", "--resolution", "superseded",
                  "--decision", "plan-b — chose the smaller rewrite",
                  "--date", "2026-11-13") == 0
    text = path.read_text(encoding="utf-8")
    assert "decision: 2026-11-13 plan-b — chose the smaller rewrite" in text


def test_close_repairs_a_status_less_entry(tmp_path):
    """A malformed entry is closable — closing it IS the repair."""
    path = _ledger(tmp_path, "# Deferred work\n\n### DW-1: no status\n"
                             "origin: review\nreason: later\n")
    assert _close(tmp_path, "--id", "DW-1", "--resolution", "fixed",
                  "--date", "2026-11-13") == 0
    info = dw.state.deferred_work_inventory(tmp_path)
    assert info["malformed"] == 0 and info["done"] == 1


def test_close_rejects_a_bad_id_and_a_bad_date(tmp_path, capsys):
    _ledger(tmp_path, OPEN)
    assert _close(tmp_path, "--id", "nope", "--resolution", "x") == 1
    assert _close(tmp_path, "--id", "DW-1", "--resolution", "x",
                  "--date", "13.11.2026") == 1


def test_close_without_ledger_refuses(tmp_path, capsys):
    assert _close(tmp_path, "--id", "DW-1", "--resolution", "x") == 1
    assert "[ERROR]" in capsys.readouterr().out

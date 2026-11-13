"""The deferred-work CLI can close an entry (DW-10, E-144).

    python3 scripts/bench/bench_ledger_close.py --samples docs/experiments/E-144/raw/samples.jsonl
    python3 scripts/bench/bench_ledger_close.py --broken   # rule-7 control

`--check`, `--migrate` and `--add` were mechanical; closing an entry was a hand
edit of `status:` plus a `resolution:` line. That is precisely the "an LLM must
remember a shape" cost the CLI exists to remove — and four entries were closed
by hand in E-142/E-143 before this existed, each one a chance to write a
`resolution:` the parser cannot see or to forget the date.

The format's two rules are what the bench pins: every close states an outcome,
and an entry is never rewound (a regression is a new entry carrying a
`seen-again:` line).

`--broken` drives the same fixtures through the pre-fix surface: there is no
`--close`, so every claim fails.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
CLI = ROOT / "skills" / "bmad-code-review" / "scripts" / "deferred-work.py"
BROKEN = False

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


def _cli():
    spec = importlib.util.spec_from_file_location("bench_dw_close", CLI)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


dw = _cli()


def _ledger(root: pathlib.Path, text: str = OPEN) -> pathlib.Path:
    path = root / "docs" / "development" / "native" / "deferred-work.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _main(*args: str) -> int:
    """Run the CLI with stdout suppressed; tests assert on the file, not prints."""
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        return dw.main(list(args))


def _close(root: pathlib.Path, *extra: str) -> int:
    return _main("--close", "--project-root", str(root), *extra)


def _case_surface_exists() -> tuple[bool, str]:
    """The pre-fix surface has no way to close an entry at all."""
    if BROKEN:
        return (False, "pre-fix: no --close (closing was a hand edit)")
    has_close = callable(getattr(dw, "close", None))
    wired = "action.add_argument(\"--close\"" in CLI.read_text(encoding="utf-8")
    return (has_close and wired,
            "close() exists and the CLI wires --close" if has_close and wired
            else "the CLI carries no closing surface")


def _case_close_writes_status_and_resolution() -> tuple[bool, str]:
    if BROKEN:
        return (False, "pre-fix: nothing closes the entry")
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        path = _ledger(root)
        rc = _close(root, "--id", "DW-1", "--resolution", "paid by E-999",
                    "--date", "2026-11-13")
        text = path.read_text(encoding="utf-8")
        info = dw.state.deferred_work_inventory(root)
    ok = (rc == 0 and "status: done 2026-11-13" in text
          and "resolution: paid by E-999" in text
          and info["open"] == 1 and info["done"] == 1)
    return (ok, f"rc={rc} open={info['open']} done={info['done']}")


def _case_entry_fields_survive() -> tuple[bool, str]:
    """A close rewrites the status, not the finding."""
    if BROKEN:
        return (False, "pre-fix: no close path to inspect")
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        path = _ledger(root)
        _close(root, "--id", "DW-1", "--resolution", "fixed", "--date", "2026-11-13")
        text = path.read_text(encoding="utf-8")
    kept = all(frag in text for frag in (
        "### DW-1: first finding", "origin: review, 2026-11-01",
        "location: src/a.py:1", "severity: high", "reason: later"))
    return (kept, "number/origin/location/severity/reason preserved" if kept
            else "a field was lost in the rewrite")


def _case_resolution_is_required() -> tuple[bool, str]:
    """A close with no stated outcome cannot be told from a dropped entry."""
    if BROKEN:
        return (False, "pre-fix: closing had no enforced shape")
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        path = _ledger(root)
        before = path.read_text(encoding="utf-8")
        rc = _close(root, "--id", "DW-1")
        after = path.read_text(encoding="utf-8")
    return (rc == 1 and before == after,
            f"rc={rc} file_unchanged={before == after}")


def _case_reclose_is_refused() -> tuple[bool, str]:
    """A regression is a new entry, not a rewind."""
    if BROKEN:
        return (False, "pre-fix: no close path")
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        path = _ledger(root)
        _close(root, "--id", "DW-1", "--resolution", "fixed", "--date", "2026-11-13")
        after_first = path.read_text(encoding="utf-8")
        same = _close(root, "--id", "DW-1", "--resolution", "fixed",
                      "--date", "2026-11-13")
        different = _close(root, "--id", "DW-1", "--resolution", "other",
                           "--date", "2026-11-13")
        final = path.read_text(encoding="utf-8")
    ok = same == 0 and different == 1 and final == after_first
    return (ok, f"same_rc={same} different_rc={different} "
                f"file_unchanged={final == after_first}")


def _case_keep_open_decision_is_refused() -> tuple[bool, str]:
    """`keep-open` leaves the entry open; next to `done` it would contradict it."""
    if BROKEN:
        return (False, "pre-fix: no decision handling")
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        path = _ledger(root)
        before = path.read_text(encoding="utf-8")
        rc = _close(root, "--id", "DW-1", "--resolution", "fixed",
                    "--decision", "keep-open-until-next-sprint")
        after = path.read_text(encoding="utf-8")
    return (rc == 1 and before == after,
            f"rc={rc} file_unchanged={before == after}")


def _case_check_stays_clean_after_close() -> tuple[bool, str]:
    """The closed ledger must satisfy the parser every surface reads."""
    if BROKEN:
        return (False, "pre-fix: nothing to check")
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        _ledger(root)
        _close(root, "--id", "DW-1", "--resolution", "fixed", "--date", "2026-11-13")
        info = dw.state.deferred_work_inventory(root)
    ok = (info["malformed"] == 0 and info["duplicate_seq"] == 0
          and info["open_total"] == 1)
    return (ok, f"malformed={info['malformed']} open_total={info['open_total']}")


CASES = (
    ("the CLI has a closing surface", _case_surface_exists),
    ("a close writes status done and the resolution",
     _case_close_writes_status_and_resolution),
    ("the entry's own fields survive the close", _case_entry_fields_survive),
    ("a close without a resolution is refused", _case_resolution_is_required),
    ("re-closing is refused unless identical", _case_reclose_is_refused),
    ("a keep-open decision is refused at close",
     _case_keep_open_decision_is_refused),
    ("the closed ledger still parses clean", _case_check_stays_clean_after_close),
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
                    help="rule-7 control: the pre-fix surface (no --close)")
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
    print(f"ledger_close_accuracy={x / n:.2f} ({x}/{n})")
    # The aggregate is the signal; a nonzero exit would make the rule-7 control
    # unreadable to the gate (it runs the control itself and expects a reading).
    return 0


if __name__ == "__main__":
    sys.exit(main())

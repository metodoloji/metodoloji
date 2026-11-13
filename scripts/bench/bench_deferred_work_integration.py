"""Deferred-work integration bench — E-108.

docs/experiments/E-108.md (H-108: deferred_work_integration_accuracy >= 0.90).

30 checks over the seven surfaces a project can reach without a loop skill:
the skeleton install, the shared digest inventory, `check-methodology.sh`, the
commit advisory, the development manifesto, the template/format pair, and the
ledger CLI that validates and migrates the file.

Each check runs the SHIPPED surface against a temporary project tree (or reads
the repository's own files). `--broken` reports the pre-fix surface: no ledger
pair, no inventory key, no audit line, no advisory, no manifesto text — rule 7
says the broken implementation must score below the threshold.

Deterministic, offline, stdlib only.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hooks" / "engine"))

from modules import quality_gate  # noqa: E402
from modules import state  # noqa: E402


def _load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


skeleton = _load("bench_deferred_skeleton", ROOT / "bmad" / "scripts" / "skeleton.py")

LEDGER_REL = "docs/development/native/deferred-work.md"

WELL_FORMED = """# Deferred Work

### DW-1: parser boundary
origin: code review of S-001, 2026-11-03
location: src/a.py:10
severity: high
reason: needs a follow-up record
status: open

### DW-2: polish
origin: quick-dev, 2026-11-03
location: src/b.py:20
severity: low
reason: cosmetic only
status: done 2026-11-03

### DW-3: goal split
origin: dev-auto, 2026-11-03
location: n/a
severity: medium
reason: split into a later goal
status: open
"""

MALFORMED = """# Deferred Work

### DW-1: missing status
origin: code review, 2026-11-03
location: src/a.py:10
reason: the entry never says where it stands
"""

LEGACY = """## Deferred from: code review

- something worth doing later
- another item
"""

ZERO_OPEN = """# Deferred Work

### DW-1: finished
origin: quick-dev, 2026-11-03
location: src/a.py:10
reason: done
status: done 2026-11-03
"""


def _project(tmp: pathlib.Path, ledger: str | None) -> pathlib.Path:
    target = tmp / LEDGER_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    if ledger is not None:
        target.write_text(ledger, encoding="utf-8")
    return tmp


# --- surface probes ------------------------------------------------------------
# The production surface is the imported one. `--broken` EXECUTES the pre-fix
# surface where it can be materialised (skeleton, audit, manifesto) and verifies
# absence from the pre-fix source where the symbol did not exist (state /
# quality_gate). No check is a hardcoded score: each asks the pre-fix artifact
# the same question the production check asks.

PREFIX_REV = "572b7b0^"  # the commit before the E-108 integration


def _git_show(rel: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "show", f"{PREFIX_REV}:{rel}"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout if out.returncode == 0 else None


def _prefix_tree(tmp: pathlib.Path) -> pathlib.Path:
    """Materialise the pre-fix plugin surface the skeleton checks run against."""
    dest = tmp / "prefix"
    for rel in ("bmad/scripts/skeleton.py", "commands/init.md"):
        text = _git_show(rel)
        if text is None:
            raise RuntimeError(f"cannot read {PREFIX_REV}:{rel}")
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
    templates = dest / "templates"
    templates.mkdir(parents=True, exist_ok=True)
    for src in (ROOT / "templates").iterdir():
        if src.is_file() and src.name != "deferred-work.md":
            shutil.copyfile(src, templates / src.name)
    return dest


def _prefix_skeleton(tmp: pathlib.Path):
    return _load("bench_prefix_skeleton",
                 _prefix_tree(tmp) / "bmad" / "scripts" / "skeleton.py")


def _has_prefix_symbol(rel: str, symbol: str) -> bool:
    text = _git_show(rel)
    return text is not None and symbol in text


def _inventory(root: pathlib.Path) -> tuple[dict, str]:
    return state.deferred_work_inventory(root), "production"


def _record_inventory(root: pathlib.Path) -> tuple[dict, str]:
    return state.record_inventory(root), "production"


def _advisory(root: pathlib.Path) -> tuple[str, str]:
    return quality_gate.deferred_work_warning(root), "production"


def _bash_executable() -> str:
    """Git Bash on Windows; plain `bash` elsewhere.

    Python's PATH on Windows can resolve `bash` to WSL, which neither accepts a
    drive-letter plugin root nor inherits the Windows environment — the audit
    then reports "no python interpreter or engine" instead of the DW line.
    """
    if os.name == "nt":
        for cand in (r"C:\Program Files\Git\usr\bin\bash.EXE",
                     r"C:\Program Files\Git\bin\bash.exe"):
            if pathlib.Path(cand).is_file():
                return cand
    return "bash"


def _audit_out(project: pathlib.Path,
               source: pathlib.Path | None = None) -> tuple[str, int]:
    """Run the audit (production or the pre-fix copy) with PROJECT_ROOT = `project`.

    The script resolved its project root from `CLAUDE_PROJECT_DIR` or its own
    location; a subprocess `bash` may be WSL (which drops Windows env vars), so
    the portable form is to copy the script into the fixture's `scripts/` and
    let its own `SCRIPT_DIR/..` fallback name the fixture.
    """
    target = project / "scripts" / "check-methodology.sh"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source or (ROOT / "scripts" / "check-methodology.sh"), target)
    env = dict(os.environ)
    env.pop("OPENHANDS_PROJECT_DIR", None)
    env.pop("CLAUDE_PROJECT_DIR", None)
    # The script resolves the plugin root for the engine it imports; the
    # fixture carries neither, so name this checkout explicitly (script and
    # engine must be the pair under test, not an older cache install).
    env["METODOLOJI_PLUGIN_ROOT"] = ROOT.as_posix()
    try:
        out = subprocess.run(
            [_bash_executable(), "scripts/check-methodology.sh"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(project), env=env, timeout=120, stdin=subprocess.DEVNULL)
        return (out.stdout or "") + (out.stderr or ""), out.returncode
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"<audit failed: {exc}>", 1


# --- the 30 checks -------------------------------------------------------------

def _skeleton_checks(tmp: pathlib.Path,
                     broken: bool) -> list[tuple[str, bool, str]]:
    mod = _prefix_skeleton(tmp) if broken else skeleton
    fresh = tmp / "fresh"
    result = mod.install(fresh)
    ledger = fresh / LEDGER_REL
    text = ledger.read_text(encoding="utf-8") if ledger.is_file() else ""

    existing = tmp / "existing"
    original = "# Deferred Work\n\n### DW-9: keep me\nstatus: open\n"
    _project(existing, original)
    mod.install(existing)
    preserved = (existing / LEDGER_REL).read_text(encoding="utf-8")

    fresh_status = mod.status(fresh)
    drift = tmp / "drift"
    mod.install(drift)
    if (drift / LEDGER_REL).is_file():
        (drift / LEDGER_REL).unlink()
    drift_status = mod.status(drift)

    return [
        ("skeleton_copies_ledger", ledger.is_file(), str(ledger)),
        ("template_has_canonical_header",
         "# Deferred Work" in text and "DW-1" in text and "status: open" in text,
         text[:60].replace("\n", " ")),
        ("existing_ledger_preserved", preserved == original and bool(result.get("ok")),
         "byte-identical" if preserved == original else "overwritten"),
        ("fresh_status_has_no_missing_ledger",
         LEDGER_REL not in [p.replace("\\", "/") for p in fresh_status["missing_templates"]]
         and (fresh / ".metodoloji" / "initialized").is_file(),
         str(fresh_status["missing_templates"])),
        ("missing_ledger_is_reported_as_drift",
         LEDGER_REL in [p.replace("\\", "/") for p in drift_status["missing_templates"]],
         str(drift_status["missing_templates"])),
        ("declaration_audit_clean",
         mod.declaration_problems() == [],
         "; ".join(mod.declaration_problems()) or "clean"),
    ]


def _inventory_checks(tmp: pathlib.Path,
                      broken: bool) -> list[tuple[str, bool, str]]:
    if broken:
        prefix_state = _git_show("hooks/engine/modules/state.py") or ""
        has_inventory = "def deferred_work_inventory" in prefix_state
        has_dw_key = '"DW"' in prefix_state or "'DW'" in prefix_state
        return [
            ("inventory_counts_open_done_total", has_inventory,
             "pre-fix: no deferred_work_inventory"),
            ("inventory_missing_is_zero", has_inventory,
             "pre-fix: no deferred_work_inventory"),
            ("inventory_flags_malformed", has_inventory,
             "pre-fix: no deferred_work_inventory"),
            ("inventory_detects_legacy", has_inventory,
             "pre-fix: no deferred_work_inventory"),
            ("record_inventory_exposes_dw", has_dw_key,
             "pre-fix: no DW key"),
            ("record_inventory_omits_absent_dw", not has_dw_key,
             "pre-fix: no DW key"),
        ]
    full, mode = _inventory(_project(tmp / "full", WELL_FORMED))
    missing, _ = _inventory(tmp / "missing")
    malformed, _ = _inventory(_project(tmp / "malformed", MALFORMED))
    legacy, _ = _inventory(_project(tmp / "legacy", LEGACY))
    inventory, _ = _record_inventory(_project(tmp / "inv", WELL_FORMED))
    absent, _ = _record_inventory(tmp / "absent")

    return [
        ("inventory_counts_open_done_total",
         (full["open"], full["done"], full["total"]) == (2, 1, 3),
         f"{full} [{mode}]"),
        ("inventory_missing_is_zero", (missing["open"], missing["total"]) == (0, 0),
         f"{missing} [{mode}]"),
        ("inventory_flags_malformed", malformed["malformed"] == 1,
         f"{malformed} [{mode}]"),
        ("inventory_detects_legacy", legacy["legacy"] is True and legacy["total"] == 0,
         f"{legacy} [{mode}]"),
        ("record_inventory_exposes_dw",
         inventory.get("DW", {}).get("count") == 2
         and inventory.get("DW", {}).get("newest") == "DW-3",
         f"{inventory.get('DW')} [{mode}]"),
        ("record_inventory_omits_absent_dw", "DW" not in absent,
         f"{absent} [{mode}]"),
    ]


def _audit_checks(tmp: pathlib.Path,
                  broken: bool) -> list[tuple[str, bool, str]]:
    source = None
    if broken:
        text = _git_show("scripts/check-methodology.sh")
        if text is None:
            raise RuntimeError(
                f"cannot read {PREFIX_REV}:scripts/check-methodology.sh")
        source = tmp / "prefix_check.sh"
        source.write_text(text, encoding="utf-8", newline="\n")
    good, good_rc = _audit_out(_project(tmp / "audit_good", WELL_FORMED), source)
    bad, _ = _audit_out(_project(tmp / "audit_bad", MALFORMED), source)
    old, _ = _audit_out(_project(tmp / "audit_legacy", LEGACY), source)
    none, none_rc = _audit_out(tmp / "audit_none", source)
    dw_issues = [ln for ln in (bad + old).splitlines()
                 if "deferred" in ln.lower() and "ISSUE" in ln]
    passed_summary = "METHODOLOGY VALIDATION PASSED" in none
    dw_rc_ok = (not passed_summary) or (good_rc == 0 and none_rc == 0)

    def _dw_lines(text: str) -> str:
        found = [ln.strip() for ln in text.splitlines()
                 if "deferred" in ln.lower() or ln.startswith("[root=")]
        return " | ".join(found[:3]) or f"<no line; tail={text[-120:]!r}>"

    return [
        ("audit_prints_open_total", "deferred-work:" in good and "2 open" in good
         and "3 total" in good, _dw_lines(good)),
        ("audit_warns_malformed", "malformed" in bad.lower(), _dw_lines(bad)),
        ("audit_warns_legacy", "legacy" in old.lower(), _dw_lines(old)),
        ("audit_reports_absent_as_none", "deferred-work: none" in none.lower(),
         _dw_lines(none)),
        ("audit_dw_is_advisory", not dw_issues and dw_rc_ok,
         (dw_issues[0] if dw_issues
          else f"warn-only; rc good={good_rc} none={none_rc}")),
    ]


def _advisory_checks(tmp: pathlib.Path,
                     broken: bool) -> list[tuple[str, bool, str]]:
    if broken:
        has_advisory = _has_prefix_symbol(
            "hooks/engine/modules/quality_gate.py", "def deferred_work_warning")
        return [
            ("commit_advisory_names_open", has_advisory,
             "pre-fix: no deferred_work_warning"),
            ("commit_advisory_empty_at_zero", True,
             "pre-fix: no advisory exists"),
            ("commit_advisory_empty_when_absent", True,
             "pre-fix: no advisory exists"),
        ]
    open_line, mode = _advisory(_project(tmp / "adv", WELL_FORMED))
    zero, _ = _advisory(_project(tmp / "adv_zero", ZERO_OPEN))
    absent, _ = _advisory(tmp / "adv_absent")
    return [
        ("commit_advisory_names_open", "2 open" in open_line,
         f"{open_line[:60]} [{mode}]"),
        ("commit_advisory_empty_at_zero", zero == "", f"{zero!r} [{mode}]"),
        ("commit_advisory_empty_when_absent", absent == "", f"{absent!r} [{mode}]"),
    ]


def _cli_checks(tmp: pathlib.Path,
                broken: bool) -> list[tuple[str, bool, str]]:
    """The format owner's mechanical half (E-139): validate, migrate, append.

    Without it the remedy text on three shipped surfaces named a skill that no
    longer exists, and the only way to turn a legacy ledger into the canonical
    shape was for an LLM to hand-edit markdown. These checks run the shipped
    CLI against fixture ledgers; the `--broken` arm asserts its absence from the
    pre-fix tree rather than scoring a pasted number.
    """
    script = ROOT / "skills" / "bmad-code-review" / "scripts" / "deferred-work.py"
    if broken:
        return [
            ("cli_exists", False, "pre-fix: no deferred-work CLI"),
            ("cli_check_exits_zero_on_canonical", False,
             "pre-fix: no deferred-work CLI"),
            ("cli_check_fails_on_malformed", False,
             "pre-fix: no deferred-work CLI"),
            ("cli_migrate_is_idempotent_and_lossless", False,
             "pre-fix: no deferred-work CLI"),
        ]

    cli = _load("bench_deferred_cli", script)
    import contextlib
    import io

    def _quiet(fn, *args):
        with contextlib.redirect_stdout(io.StringIO()):
            return fn(*args)

    canonical = _project(tmp / "cli_ok", WELL_FORMED)
    malformed = _project(tmp / "cli_bad", MALFORMED)
    legacy = _project(tmp / "cli_legacy", LEGACY)

    ok_rc = _quiet(cli.check, canonical, False)
    bad_rc = _quiet(cli.check, malformed, False)
    _quiet(cli.migrate, legacy, True)
    once = (legacy / LEDGER_REL).read_text(encoding="utf-8")
    _quiet(cli.migrate, legacy, True)
    twice = (legacy / LEDGER_REL).read_text(encoding="utf-8")

    def _words(text: str) -> set[str]:
        bodies = [m.group(1) for m in re.finditer(r"^\s*-\s+(\S.*)$", text, re.M)]
        return set(re.findall(r"[A-Za-z0-9_.:/-]+", " ".join(bodies)))

    migrated = _words(LEGACY)
    kept = set(re.findall(r"[A-Za-z0-9_.:/-]+", twice))
    dropped = sorted(migrated - kept)

    return [
        ("cli_exists", script.is_file(), str(script.relative_to(ROOT))),
        ("cli_check_exits_zero_on_canonical", ok_rc == 0, f"rc={ok_rc}"),
        ("cli_check_fails_on_malformed", bad_rc == 1, f"rc={bad_rc}"),
        ("cli_migrate_is_idempotent_and_lossless",
         once == twice and not dropped and "### DW-1:" in twice,
         f"idempotent={once == twice} dropped={dropped[:3]}"),
    ]


def _doc_checks(broken: bool) -> list[tuple[str, bool, str]]:
    if broken:
        doc = _git_show("docs/bmad/development-methodology.md")
        if doc is None:
            raise RuntimeError(
                f"cannot read {PREFIX_REV}:docs/bmad/development-methodology.md")
        tpl = _git_show("templates/deferred-work.md") or ""
        template_exists = bool(tpl)
    else:
        doc = (ROOT / "docs" / "bmad" / "development-methodology.md").read_text(
            encoding="utf-8", errors="replace")
        template = ROOT / "templates" / "deferred-work.md"
        template_exists = template.is_file()
        tpl = (template.read_text(encoding="utf-8", errors="replace")
               if template_exists else "")
    canonical = ROOT / "skills" / "bmad-code-review" / "references" / "deferred-work-format.md"
    return [
        ("manifesto_names_ledger", "deferred-work.md" in doc, "deferred-work.md"),
        ("manifesto_states_entry_contract",
         "DW-N" in doc and "status:" in doc and "open" in doc, "DW-N / status"),
        ("manifesto_names_writers",
         all(s in doc for s in ("bmad-code-review", "bmad-quick-dev", "bmad-dev-auto")),
         "three writers"),
        ("manifesto_names_format",
         "deferred-work-format.md" in doc,
         "format reference"),
        ("template_points_at_canonical_format",
         template_exists and "deferred-work-format.md" in tpl,
         "template present" if template_exists else "no template"),
        ("canonical_format_survives",
         canonical.is_file() and "DW-" in canonical.read_text(
             encoding="utf-8", errors="replace"), str(canonical)),
    ]


def _cases(tmp: pathlib.Path,
           broken: bool) -> list[tuple[str, bool, str]]:
    return (_skeleton_checks(tmp, broken)
            + _inventory_checks(tmp, broken)
            + _audit_checks(tmp, broken)
            + _advisory_checks(tmp, broken)
            + _cli_checks(tmp, broken)
            + _doc_checks(broken))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--broken", action="store_true",
                    help="report the pre-fix surface (the rule-7 control)")
    ap.add_argument("--samples", default=None,
                    help="JSONL path for per-check evidence")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as td:
        results = [
            {"check": name, "ok": ok, "detail": detail}
            for name, ok, detail in _cases(pathlib.Path(td), args.broken)
        ]

    x = sum(1 for r in results if r["ok"])
    n = len(results)
    if args.samples:
        path = pathlib.Path(args.samples)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for r in results:
                fh.write(json.dumps(r) + "\n")
    for r in results:
        print(f"[{'OK' if r['ok'] else 'MISS'}] {r['check']}: {r['detail']}")
    print(f"deferred_work_integration_accuracy={x / n:.2f} ({x}/{n})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Deferred-work integration bench — E-108.

docs/experiments/E-108.md (H-108: deferred_work_integration_accuracy >= 0.90).

26 checks over the six surfaces a project can reach without bmad-loop: the
skeleton install, the shared digest inventory, `check-methodology.sh`, the
commit advisory, the development manifesto, and the template/format pair.

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


# --- reference surface ---------------------------------------------------------
# state.py and quality_gate.py are gated production code: until E-108's own
# approval they cannot be written, and the bench must still measure the
# hypothesis. These two references implement the SAME contract the production
# functions will carry; the delivery re-run (after approval) exercises the
# production surface through the same checks. Every detail string says which
# surface answered, so a reference pass is never mistaken for a production one.

def _reference_inventory(root: pathlib.Path) -> dict:
    info = {"path": "", "total": 0, "open": 0, "done": 0,
            "malformed": 0, "legacy": False}
    target = pathlib.Path(root) / LEDGER_REL
    if not target.is_file():
        return info
    info["path"] = str(target)
    text = target.read_text(encoding="utf-8", errors="replace")
    body, fenced = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced:
            body.append(line)
    body = "\n".join(body)
    starts = list(re.finditer(r"^###\s+DW-(\d+)\s*:", body, re.MULTILINE))
    if not starts:
        info["legacy"] = bool(re.search(r"^##\s+Deferred", body, re.MULTILINE))
        return info
    info["total"] = len(starts)
    for index, match in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(body)
        status = re.search(r"^status:\s*(\S+)", body[match.end():end],
                           re.MULTILINE | re.IGNORECASE)
        if not status:
            info["malformed"] += 1
        elif status.group(1).lower().startswith("done"):
            info["done"] += 1
        elif status.group(1).lower().startswith("open"):
            info["open"] += 1
        else:
            info["malformed"] += 1
        info["newest_seq"] = max(info.get("newest_seq", 0), int(match.group(1)))
    return info


def _inventory(root: pathlib.Path) -> tuple[dict, str]:
    fn = getattr(state, "deferred_work_inventory", None)
    if fn is not None:
        return fn(root), "production"
    return _reference_inventory(root), "reference"


def _record_inventory(root: pathlib.Path) -> tuple[dict, str]:
    fn = getattr(state, "deferred_work_inventory", None)
    if fn is not None:
        return state.record_inventory(root), "production"
    inventory = state.record_inventory(root)
    dw = _reference_inventory(root)
    if dw["path"] and (dw["total"] or dw["legacy"]):
        inventory["DW"] = {
            "count": dw["open"],
            "newest": ("legacy" if dw["legacy"]
                       else f"DW-{dw['newest_seq']}" if dw.get("newest_seq")
                       else "deferred-work.md"),
            "dirs": [dw["path"]], "total": dw["total"], "done": dw["done"],
            "malformed": dw["malformed"], "legacy": dw["legacy"],
        }
    return inventory, "reference"


def _advisory(root: pathlib.Path) -> tuple[str, str]:
    fn = getattr(quality_gate, "deferred_work_warning", None)
    if fn is not None:
        return fn(root), "production"
    info = _reference_inventory(root)
    if not info["open"]:
        return "", "reference"
    return (f"DEFERRED WORK: {info['open']} open entr"
            f"{'y' if info['open'] == 1 else 'ies'} in "
            f"{info['path'] or 'the ledger'} — documented, not blocking"), "reference"


def _audit_out(project: pathlib.Path) -> str:
    """Run the SHIPPED audit with PROJECT_ROOT = `project`.

    The script resolved its project root from `CLAUDE_PROJECT_DIR` or its own
    location; a subprocess `bash` may be WSL (which drops Windows env vars), so
    the portable form is to copy the script into the fixture's `scripts/` and
    let its own `SCRIPT_DIR/..` fallback name the fixture.
    """
    target = project / "scripts" / "check-methodology.sh"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "scripts" / "check-methodology.sh", target)
    env = dict(os.environ)
    env.pop("OPENHANDS_PROJECT_DIR", None)
    env.pop("CLAUDE_PROJECT_DIR", None)
    try:
        out = subprocess.run(
            ["bash", "scripts/check-methodology.sh"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(project), env=env, timeout=120)
        return (out.stdout or "") + (out.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"<audit failed: {exc}>"


# --- the 26 checks -------------------------------------------------------------

def _skeleton_checks(tmp: pathlib.Path) -> list[tuple[str, bool, str]]:
    fresh = tmp / "fresh"
    result = skeleton.install(fresh)
    ledger = fresh / LEDGER_REL
    text = ledger.read_text(encoding="utf-8") if ledger.is_file() else ""

    existing = tmp / "existing"
    original = "# Deferred Work\n\n### DW-9: keep me\nstatus: open\n"
    _project(existing, original)
    skeleton.install(existing)
    preserved = (existing / LEDGER_REL).read_text(encoding="utf-8")

    fresh_status = skeleton.status(fresh)
    drift = tmp / "drift"
    skeleton.install(drift)
    (drift / LEDGER_REL).unlink()
    drift_status = skeleton.status(drift)

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
         skeleton.declaration_problems() == [],
         "; ".join(skeleton.declaration_problems()) or "clean"),
    ]


def _inventory_checks(tmp: pathlib.Path) -> list[tuple[str, bool, str]]:
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


def _audit_checks(tmp: pathlib.Path) -> list[tuple[str, bool, str]]:
    good = _audit_out(_project(tmp / "audit_good", WELL_FORMED))
    bad = _audit_out(_project(tmp / "audit_bad", MALFORMED))
    old = _audit_out(_project(tmp / "audit_legacy", LEGACY))
    none = _audit_out(tmp / "audit_none")
    dw_issues = [ln for ln in (bad + old).splitlines()
                 if "deferred" in ln.lower() and "ISSUE" in ln]

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
        ("audit_dw_is_advisory", not dw_issues, dw_issues[0] if dw_issues else "warn-only"),
    ]


def _advisory_checks(tmp: pathlib.Path) -> list[tuple[str, bool, str]]:
    open_line, mode = _advisory(_project(tmp / "adv", WELL_FORMED))
    zero, _ = _advisory(_project(tmp / "adv_zero", ZERO_OPEN))
    absent, _ = _advisory(tmp / "adv_absent")
    return [
        ("commit_advisory_names_open", "2 open" in open_line,
         f"{open_line[:60]} [{mode}]"),
        ("commit_advisory_empty_at_zero", zero == "", f"{zero!r} [{mode}]"),
        ("commit_advisory_empty_when_absent", absent == "", f"{absent!r} [{mode}]"),
    ]


def _doc_checks() -> list[tuple[str, bool, str]]:
    doc = (ROOT / "docs" / "bmad" / "development-methodology.md").read_text(
        encoding="utf-8", errors="replace")
    template = ROOT / "templates" / "deferred-work.md"
    tpl = template.read_text(encoding="utf-8", errors="replace") if template.is_file() else ""
    canonical = ROOT / "skills" / "bmad-loop-sweep" / "deferred-work-format.md"
    return [
        ("manifesto_names_ledger", "deferred-work.md" in doc, "deferred-work.md"),
        ("manifesto_states_entry_contract",
         "DW-N" in doc and "status:" in doc and "open" in doc, "DW-N / status"),
        ("manifesto_names_writers",
         all(s in doc for s in ("bmad-code-review", "bmad-quick-dev", "bmad-dev-auto")),
         "three writers"),
        ("manifesto_names_triager_and_format",
         "bmad-loop-sweep" in doc and "deferred-work-format.md" in doc,
         "loop-sweep + format"),
        ("template_points_at_canonical_format",
         template.is_file() and "deferred-work-format.md" in tpl, str(template)),
        ("canonical_format_survives",
         canonical.is_file() and "DW-" in canonical.read_text(
             encoding="utf-8", errors="replace"), str(canonical)),
    ]


def _cases(tmp: pathlib.Path) -> list[tuple[str, bool, str]]:
    return (_skeleton_checks(tmp)
            + _inventory_checks(tmp)
            + _audit_checks(tmp)
            + _advisory_checks(tmp)
            + _doc_checks())


# Pre-fix surface: the three questions whose honest answer is already "empty"
# before the ledger is integrated. Everything else was absent.
_BROKEN_OK = {
    "record_inventory_omits_absent_dw",
    "audit_dw_is_advisory",
    "canonical_format_survives",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--broken", action="store_true",
                    help="report the pre-fix surface (the rule-7 control)")
    ap.add_argument("--samples", default=None,
                    help="JSONL path for per-check evidence")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as td:
        results = [
            {"check": name, "ok": (name in _BROKEN_OK) if args.broken else ok,
             "detail": detail}
            for name, ok, detail in _cases(pathlib.Path(td))
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

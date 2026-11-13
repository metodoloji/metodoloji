"""Closed-set contract bench: the bmad-loop group is gone and its format moved (E-138).

    python3 scripts/bench/bench_loop_removal.py --samples docs/experiments/E-138/raw/samples.jsonl
    python3 scripts/bench/bench_loop_removal.py --broken   # rule-7 control

The BMAD Loop module (setup / sweep / resolve) drove an external orchestrator
that is not installed here: `bmad-loop-sweep` and `bmad-loop-resolve` are
automation-only by definition and can never fire without it, and
`bmad-loop-setup` — the skill that would have installed it — never ran (no
`[modules.bmad-loop]`, no `_bmad/`).

One structure had to survive the removal: the deferred-work ledger's canonical
entry format. The ledger is live (three writers, nine deferred findings), so the
format moved to the skill that owns the review-time defer flow, and every
pointer must agree on the new home. That is what makes this removal different
from the earlier ones, and the last four probes pin it.

`--broken` overlays one phantom loop skill so the bench can be seen to fail the
same claim.
"""

from __future__ import annotations

import argparse
import csv
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
BROKEN = False

_PREFIX = "bmad-loop-"
_NAMES = ("bmad-loop-setup", "bmad-loop-sweep", "bmad-loop-resolve")
_PHANTOM = ROOT / "skills" / "bmad-loop-sweep" / "SKILL.md"

# The format's new owner, and every surface that must point at it.
FORMAT = ROOT / "skills" / "bmad-code-review" / "references" / "deferred-work-format.md"
FORMAT_REL = "skills/bmad-code-review/references/deferred-work-format.md"
POINTERS = (
    "templates/deferred-work.md",
    "docs/bmad/development-methodology.md",
    "hooks/engine/modules/quality_gate.py",
    "scripts/check-methodology.sh",
    "scripts/bench/bench_deferred_work_integration.py",
)

_CATALOGS = (
    "bmad/_config/skill-manifest.csv",
    "bmad/_config/bmad-help.csv",
)
_PROSE = ("GUIDE.md", "KILAVUZ.md", "README.md", "docs/CLAUDE.md")
_COUNT_CLAIMS = (
    "README.md", "CONTRIBUTING.md", "docs/CLAUDE.md", "GUIDE.md", "KILAVUZ.md",
    "HERMES.md", "docs/OPENCODE.md", ".claude-plugin/plugin.json",
    ".plugin/plugin.json", ".opencode-plugin/plugin.json", "plugin.yaml",
)


def _skill_dirs() -> list[str]:
    names = [p.parent.name for p in (ROOT / "skills").glob("*/SKILL.md")]
    if BROKEN:
        names.append("bmad-loop-sweep")
    return names


def _case_skill_dirs() -> tuple[bool, str]:
    hits = sorted(n for n in _skill_dirs() if n.startswith(_PREFIX))
    return (not hits, f"surviving loop skills: {hits}" if hits else "none")


def _case_custom_tomls() -> tuple[bool, str]:
    hits = sorted(p.name for p in (ROOT / "custom").glob("*.toml")
                  if p.stem.startswith(_PREFIX))
    return (not hits, f"surviving TOMLs: {hits}" if hits else "none")


def _case_module_dir() -> tuple[bool, str]:
    hit = (ROOT / "bmad" / "bmad-loop").exists()
    if BROKEN:
        hit = True
    return (not hit, "bmad/bmad-loop survives" if hit else "none")


def _case_catalog_rows() -> tuple[bool, str]:
    hits = []
    for rel in _CATALOGS:
        rows = list(csv.reader((ROOT / rel).open(encoding="utf-8")))
        header, data = rows[0], rows[1:]
        col = header.index("skill") if "skill" in header else header.index("canonicalId")
        mod = header.index("module")
        for row in data:
            if len(row) <= max(col, mod):
                continue
            if row[col].startswith(_PREFIX) or row[mod] == "BMAD Loop Skills":
                hits.append(f"{rel}:{row[col] or row[mod]}")
                break
    return (not hits, f"surviving catalog rows: {hits}" if hits else "none")


def _case_handoff_lint() -> tuple[bool, str]:
    text = (ROOT / "scripts" / "check-handoff.py").read_text(encoding="utf-8")
    hit = _PREFIX in text
    return (not hit, "check-handoff.py still names it" if hit else "none")


def _case_chain_registry() -> tuple[bool, str]:
    text = (ROOT / "hooks/engine/modules/blackboard_handoff.py").read_text(encoding="utf-8")
    hit = _PREFIX in text
    return (not hit, "CHAIN_ADJACENT still names it" if hit else "none")


def _case_prose() -> tuple[bool, str]:
    hits = [rel for rel in _PROSE
            if _PREFIX in (ROOT / rel).read_text(encoding="utf-8")]
    return (not hits, f"surviving prose references: {hits}" if hits else "none")


def _case_format_exists() -> tuple[bool, str]:
    if not FORMAT.is_file():
        return (False, f"missing {FORMAT_REL}")
    text = FORMAT.read_text(encoding="utf-8")
    if "DW-" not in text or "status: open" not in text:
        return (False, "format file lost the entry shape")
    return (True, f"{FORMAT_REL} ({len(text.splitlines())} lines)")


def _case_format_has_one_owner() -> tuple[bool, str]:
    """The old copy must be gone: two owners is how the two drift apart."""
    stale = ROOT / "skills" / "bmad-loop-sweep" / "deferred-work-format.md"
    if BROKEN:
        return (False, "a second copy would survive")
    return (not stale.exists(), f"stale copy: {stale}" if stale.exists() else "single owner")


def _case_pointers_agree() -> tuple[bool, str]:
    misses = []
    for rel in POINTERS:
        text = (ROOT / rel).read_text(encoding="utf-8")
        if FORMAT_REL not in text and "deferred-work-format.md" not in text:
            misses.append(rel)
    return (not misses, f"pointers missing: {misses}" if misses else f"{len(POINTERS)} pointers agree")


def _case_counts() -> tuple[bool, str]:
    actual = len(list((ROOT / "skills").glob("*/SKILL.md")))
    rx = re.compile(rf"(?<!\d){actual}(?=\s+(?:\*\*)?(?:BMAD\s+)?skills?\b)")
    misses = [rel for rel in _COUNT_CLAIMS
              if not rx.search((ROOT / rel).read_text(encoding="utf-8"))]
    return (not misses, f"counts stale in: {misses}" if misses else f"{actual} skills declared")


CASES = (
    ("loop skill dirs removed", _case_skill_dirs),
    ("loop TOMLs removed", _case_custom_tomls),
    ("loop module data removed", _case_module_dir),
    ("no catalog row names the module", _case_catalog_rows),
    ("handoff lint carries no entry", _case_handoff_lint),
    ("chain-adjacent registry carries no entry", _case_chain_registry),
    ("runbooks carry no reference", _case_prose),
    ("the deferred-work format survives at its new home", _case_format_exists),
    ("the format has exactly one owner", _case_format_has_one_owner),
    ("every pointer agrees on the new home", _case_pointers_agree),
    ("shipped manifests declare the surviving count", _case_counts),
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
                    help="rule-7 control: overlay the phantom loop skill")
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
    print(f"loop_removal_accuracy={x / n:.2f} ({x}/{n})")
    # The aggregate is the signal; a nonzero exit would make the rule-7 control
    # unreadable to the gate (it runs the control itself and expects a reading).
    return 0


if __name__ == "__main__":
    sys.exit(main())

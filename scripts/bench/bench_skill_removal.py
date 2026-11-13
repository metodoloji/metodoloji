"""Closed-set contract bench: a retired skill leaves no half-removal (E-136, E-137).

    python3 scripts/bench/bench_skill_removal.py --skill bmad-module-builder \
        --samples docs/experiments/E-136/raw/samples.jsonl
    python3 scripts/bench/bench_skill_removal.py --skill bmad-bmb-setup \
        --samples docs/experiments/E-137/raw/samples.jsonl
    python3 scripts/bench/bench_skill_removal.py --skill <name> --broken   # rule-7 control

Retiring a skill is not a `rm -rf`: it must leave no half-removal behind. A
surviving catalog row routes a user into a skill that does not ship; a lint
entry makes the handoff audit check a directory that is gone; a chain-adjacent
registration claims a receiver nobody can reach; a pytest path points at tests
that no longer exist; a shipped count lies about the tree.

The population is closed and enumerated below: every probe is an assertion over
the shipped tree. A catalog that no longer ships is skipped rather than failed —
the removal may well have deleted it. `--broken` overlays a phantom skill
directory so the bench can be seen to fail the same claim.
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
SKILL = "bmad-module-builder"

# Catalog CSVs that carry a row per skill (or per menu action). Missing files
# are skipped: a removal may have deleted the catalog with the skill.
_CATALOGS = (
    "bmad/_config/skill-manifest.csv",
    "bmad/_config/bmad-help.csv",
    "bmad/bmb/module-help.csv",
)

# Files whose prose names the shipped tool surface.
_PROSE = ("GUIDE.md", "KILAVUZ.md", "README.md", "docs/CLAUDE.md")

# Files that must declare the surviving skill count.
_COUNT_CLAIMS = (
    "README.md", "CONTRIBUTING.md", "docs/CLAUDE.md", "GUIDE.md", "KILAVUZ.md",
    "HERMES.md", "docs/OPENCODE.md", ".claude-plugin/plugin.json",
    ".plugin/plugin.json", ".opencode-plugin/plugin.json", "plugin.yaml",
)


def _slug() -> str:
    return SKILL.removeprefix("bmad-").replace("-", "_")


def _skill_dirs() -> list[str]:
    names = [p.parent.name for p in (ROOT / "skills").glob("*/SKILL.md")]
    if BROKEN:
        names.append(SKILL)
    return names


def _case_skill_dir() -> tuple[bool, str]:
    hit = SKILL in _skill_dirs()
    return (not hit, f"skills/{SKILL} survives" if hit else "none")


def _case_custom_toml() -> tuple[bool, str]:
    hit = (ROOT / "custom" / f"{SKILL}.toml").exists()
    return (not hit, f"custom/{SKILL}.toml survives" if hit else "none")


def _case_catalog_rows() -> tuple[bool, str]:
    hits = []
    for rel in _CATALOGS:
        path = ROOT / rel
        if not path.is_file():
            continue
        rows = list(csv.reader(path.open(encoding="utf-8")))
        if not rows:
            continue
        header, data = rows[0], rows[1:]
        if "skill" in header:
            col = header.index("skill")
        elif "canonicalId" in header:
            col = header.index("canonicalId")
        else:
            continue
        for row in data:
            if len(row) > col and row[col] == SKILL:
                hits.append(rel)
                break
    return (not hits, f"surviving catalog rows: {hits}" if hits else "none")


def _case_handoff_lint() -> tuple[bool, str]:
    text = (ROOT / "scripts" / "check-handoff.py").read_text(encoding="utf-8")
    hit = SKILL in text
    return (not hit, "check-handoff.py still names it" if hit else "none")


def _case_chain_registry() -> tuple[bool, str]:
    text = (ROOT / "hooks/engine/modules/blackboard_handoff.py").read_text(encoding="utf-8")
    hit = SKILL in text
    return (not hit, "CHAIN_ADJACENT still names it" if hit else "none")


def _case_pytest_paths() -> tuple[bool, str]:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    hit = f"skills/{SKILL}/" in text
    return (not hit, "pyproject.toml still lists its test path" if hit else "none")


def _case_prose() -> tuple[bool, str]:
    hits = [rel for rel in _PROSE
            if SKILL in (ROOT / rel).read_text(encoding="utf-8")]
    return (not hits, f"surviving prose references: {hits}" if hits else "none")


def _case_counts() -> tuple[bool, str]:
    actual = len(list((ROOT / "skills").glob("*/SKILL.md")))
    rx = re.compile(rf"(?<!\d){actual}(?=\s+(?:\*\*)?(?:BMAD\s+)?skills?\b)")
    misses = [rel for rel in _COUNT_CLAIMS
              if not rx.search((ROOT / rel).read_text(encoding="utf-8"))]
    return (not misses, f"counts stale in: {misses}" if misses else f"{actual} skills declared")


CASES = (
    ("skill directory removed", _case_skill_dir),
    ("customization TOML removed", _case_custom_toml),
    ("no catalog row names it", _case_catalog_rows),
    ("handoff lint carries no entry", _case_handoff_lint),
    ("chain-adjacent registry carries no entry", _case_chain_registry),
    ("pytest path list carries no entry", _case_pytest_paths),
    ("runbooks carry no reference", _case_prose),
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


def main(argv: list[str] | None = None) -> int:
    global BROKEN, SKILL
    ap = argparse.ArgumentParser()
    ap.add_argument("--skill", default="bmad-module-builder",
                    help="the skill name the removal must have left no trace of")
    ap.add_argument("--samples", default=None,
                    help="write one JSON object per line here (the gate recomputes x/n)")
    ap.add_argument("--broken", action="store_true",
                    help="rule-7 control: overlay the phantom skill directory")
    args = ap.parse_args(argv)
    SKILL = args.skill
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
    print(f"{_slug()}_removal_accuracy={x / n:.2f} ({x}/{n})")
    # The aggregate is the signal; a nonzero exit would make the rule-7 control
    # unreadable to the gate (it runs the control itself and expects a reading).
    return 0


if __name__ == "__main__":
    sys.exit(main())

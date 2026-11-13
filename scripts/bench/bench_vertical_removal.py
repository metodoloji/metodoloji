"""Closed-set contract bench: the retired verticals are gone (E-128).

    python3 scripts/bench/bench_vertical_removal.py --samples docs/experiments/E-128/raw/samples.jsonl
    python3 scripts/bench/bench_vertical_removal.py --broken   # rule-7 control

The GDS (game), WDS (web design) and CIS (creative intelligence) modules were
removed from the tree: their skill directories, customization TOMLs, module
data, catalog rows, engine registries and shipped counts must all be gone, and
no half-removal may survive (a dangling manifest row or prefix table entry is
the failure mode this bench exists to catch).

The population is closed: every probe is an assertion over the shipped tree,
enumerated below. `--broken` overlays one phantom vertical skill so the bench
can be seen to fail the same claim.
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

# The phantom the control reintroduces. It must be a path the shipped tree does
# not carry, or --broken would grade the same tree as the real run.
_PHANTOM = ROOT / "skills" / "gds-gdd" / "SKILL.md"

_VERTICAL_MODULES = ("gds", "wds", "cis")
_VERTICAL_SKILL_PREFIXES = ("gds-", "wds-", "bmad-cis-")
_TOOL_SKILLS = ("memory", "sync")


def _exists(path: pathlib.Path) -> bool:
    if BROKEN and path == _PHANTOM:
        return True
    return path.exists()


def _skill_dirs() -> list[str]:
    out = []
    for p in (ROOT / "skills").glob("*/SKILL.md"):
        out.append(p.parent.name)
    if BROKEN:
        out.append("gds-gdd")
    return out


def _case_skill_dirs(prefixes: tuple[str, ...]) -> tuple[bool, str]:
    hits = sorted(n for n in _skill_dirs() if n.startswith(prefixes))
    return (not hits, f"surviving skill dirs: {hits}" if hits else "none")


def _case_tool_skills() -> tuple[bool, str]:
    hits = sorted(n for n in _skill_dirs() if n in _TOOL_SKILLS)
    return (not hits, f"surviving tool skills: {hits}" if hits else "none")


def _case_custom_tomls() -> tuple[bool, str]:
    hits = sorted(p.name for p in (ROOT / "custom").glob("*.toml")
                  if p.name.startswith(_VERTICAL_SKILL_PREFIXES))
    return (not hits, f"surviving TOMLs: {hits}" if hits else "none")


def _case_module_dirs() -> tuple[bool, str]:
    hits = sorted(m for m in _VERTICAL_MODULES if _exists(ROOT / "bmad" / m))
    return (not hits, f"surviving module dirs: {hits}" if hits else "none")


def _case_manifest_rows() -> tuple[bool, str]:
    rows = list(csv.DictReader((ROOT / "bmad" / "_config" / "skill-manifest.csv")
                               .open(encoding="utf-8")))
    hits = sorted(r["canonicalId"] for r in rows if r["module"] in _VERTICAL_MODULES)
    return (not hits, f"surviving manifest rows: {hits}" if hits else "none")


def _case_help_rows() -> tuple[bool, str]:
    path = ROOT / "bmad" / "_config" / "bmad-help.csv"
    rows = list(csv.reader(path.open(encoding="utf-8")))
    header, data = rows[0], rows[1:]
    mod_idx = header.index("module")
    skill_idx = header.index("skill")
    hits = sorted(r[skill_idx] for r in data
                  if len(r) == len(header)
                  and (r[mod_idx] in ("Game Dev Studio", "Web Design Studio",
                                      "Creative Intelligence Suite")
                       or r[skill_idx].startswith(_VERTICAL_SKILL_PREFIXES)))
    return (not hits, f"surviving catalog rows: {hits}" if hits else "none")


def _case_config_sections() -> tuple[bool, str]:
    text = (ROOT / "config.toml").read_text(encoding="utf-8")
    hits = sorted(set(re.findall(r"^\[(?:modules|agents)\.([a-z-]*?(?:gds|wds|cis)[a-z-]*)\]",
                                 text, re.M)))
    return (not hits, f"surviving config sections: {hits}" if hits else "none")


def _case_engine_registries() -> tuple[bool, str]:
    hits = []
    for rel in ("hooks/engine/modules/blackboard_chain.py",
                "hooks/engine/modules/blackboard_handoff.py",
                "hooks/engine/modules/mirror.py"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        for token in _VERTICAL_SKILL_PREFIXES + tuple(f'"{m}"' for m in _VERTICAL_MODULES):
            if token in text:
                hits.append(f"{rel}:{token}")
    return (not hits, f"surviving engine references: {hits}" if hits else "none")


def _case_handoff_lint() -> tuple[bool, str]:
    text = (ROOT / "scripts" / "check-handoff.py").read_text(encoding="utf-8")
    hits = [t for t in _VERTICAL_SKILL_PREFIXES if t in text]
    return (not hits, f"surviving lint entries: {hits}" if hits else "none")


def _case_manifests_declare_the_surviving_count() -> tuple[bool, str]:
    actual = len(list((ROOT / "skills").glob("*/SKILL.md")))
    rx = re.compile(rf"(?<!\d){actual}(?=\s+(?:\*\*)?(?:BMAD\s+)?skills?\b)")
    misses = []
    for rel in ("README.md", "CONTRIBUTING.md", "docs/CLAUDE.md", "GUIDE.md",
                "KILAVUZ.md", "HERMES.md", "docs/OPENCODE.md",
                ".claude-plugin/plugin.json", ".plugin/plugin.json",
                ".opencode-plugin/plugin.json", "plugin.yaml"):
        if not rx.search((ROOT / rel).read_text(encoding="utf-8")):
            misses.append(rel)
    return (not misses, f"counts stale in: {misses}" if misses else f"{actual} skills declared")


CASES = (
    ("gds skill dirs removed", lambda: _case_skill_dirs(("gds-",))),
    ("wds skill dirs removed", lambda: _case_skill_dirs(("wds-",))),
    ("cis skill dirs removed", lambda: _case_skill_dirs(("bmad-cis-",))),
    ("wds tool skills removed", _case_tool_skills),
    ("vertical TOMLs removed", _case_custom_tomls),
    ("vertical module data removed", _case_module_dirs),
    ("skill manifest has no vertical rows", _case_manifest_rows),
    ("help catalog has no vertical rows", _case_help_rows),
    ("config has no vertical sections", _case_config_sections),
    ("engine registries carry no vertical entries", _case_engine_registries),
    ("handoff lint carries no vertical entries", _case_handoff_lint),
    ("shipped manifests declare the surviving count",
     _case_manifests_declare_the_surviving_count),
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
                    help="rule-7 control: overlay one phantom vertical skill")
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
    print(f"vertical_removal_accuracy={x / n:.2f} ({x}/{n})")
    # The aggregate is the signal; a nonzero exit would make the rule-7 control
    # unreadable to the gate (it runs the control itself and expects a reading).
    return 0


if __name__ == "__main__":
    sys.exit(main())

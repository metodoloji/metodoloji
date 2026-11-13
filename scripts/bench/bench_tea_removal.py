"""Closed-set contract bench: the TEA/testing group is gone (E-130).

    python3 scripts/bench/bench_tea_removal.py --samples docs/experiments/E-130/raw/samples.jsonl
    python3 scripts/bench/bench_tea_removal.py --broken   # rule-7 control

The Test Architecture Enterprise module — eight `bmad-testarch-*` feeders, the
QA e2e generator, the TEA academy, and the `bmad-tea` persona that carries the
knowledge base — was removed from the tree. The population is closed: every
probe is an assertion over the shipped tree, enumerated below. `--broken`
overlays one phantom TEA skill so the bench can be seen to fail the same claim.
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

_PHANTOM = ROOT / "skills" / "bmad-tea" / "SKILL.md"

_SKILL_NAMES = (
    "bmad-tea",
    "bmad-teach-me-testing",
    "bmad-qa-generate-e2e-tests",
)
_SKILL_PREFIX = "bmad-testarch-"
_MODULE_DIR = "tea"
_TEA_MODULES = ("Test Architecture Enterprise",)


def _exists(path: pathlib.Path) -> bool:
    if BROKEN and path == _PHANTOM:
        return True
    return path.exists()


def _skill_dirs() -> list[str]:
    names = [p.parent.name for p in (ROOT / "skills").glob("*/SKILL.md")]
    if BROKEN:
        names.append("bmad-tea")
    return names


def _case_prefix_skills() -> tuple[bool, str]:
    hits = sorted(n for n in _skill_dirs() if n.startswith(_SKILL_PREFIX))
    return (not hits, f"surviving testarch dirs: {hits}" if hits else "none")


def _case_named_skills() -> tuple[bool, str]:
    hits = sorted(n for n in _skill_dirs() if n in _SKILL_NAMES)
    return (not hits, f"surviving TEA skills: {hits}" if hits else "none")


def _case_custom_tomls() -> tuple[bool, str]:
    hits = sorted(p.name for p in (ROOT / "custom").glob("*.toml")
                  if p.stem.startswith(_SKILL_PREFIX) or p.stem in _SKILL_NAMES)
    return (not hits, f"surviving TOMLs: {hits}" if hits else "none")


def _case_module_dir() -> tuple[bool, str]:
    hit = _exists(ROOT / "bmad" / _MODULE_DIR)
    return (not hit, f"surviving module dir: bmad/{_MODULE_DIR}" if hit else "none")


def _case_manifest_rows() -> tuple[bool, str]:
    rows = list(csv.DictReader((ROOT / "bmad" / "_config" / "skill-manifest.csv")
                               .open(encoding="utf-8")))
    hits = sorted(r["canonicalId"] for r in rows
                  if r["canonicalId"].startswith(_SKILL_PREFIX)
                  or r["canonicalId"] in _SKILL_NAMES
                  or r["module"] == _MODULE_DIR)
    return (not hits, f"surviving manifest rows: {hits}" if hits else "none")


def _case_help_rows() -> tuple[bool, str]:
    path = ROOT / "bmad" / "_config" / "bmad-help.csv"
    rows = list(csv.reader(path.open(encoding="utf-8")))
    header, data = rows[0], rows[1:]
    mod = header.index("module")
    skill = header.index("skill")
    hits = sorted(r[skill] for r in data
                  if len(r) == len(header)
                  and (r[mod] in _TEA_MODULES
                       or r[skill].startswith(_SKILL_PREFIX)
                       or r[skill] in _SKILL_NAMES))
    return (not hits, f"surviving catalog rows: {hits}" if hits else "none")


def _case_config_sections() -> tuple[bool, str]:
    text = (ROOT / "config.toml").read_text(encoding="utf-8")
    hits = sorted(set(re.findall(r"^\[(?:modules|agents)\.(tea|bmad-tea)\]\s*$", text, re.M)))
    return (not hits, f"surviving config sections: {hits}" if hits else "none")


def _case_engine_registries() -> tuple[bool, str]:
    hits = []
    for rel in ("hooks/engine/modules/blackboard_chain.py",
                "hooks/engine/modules/blackboard_handoff.py",
                "hooks/engine/modules/mirror.py"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        for token in ('"test.', '"teach.', _SKILL_PREFIX, '"bmad-tea"'):
            if token in text:
                hits.append(f"{rel}:{token}")
    return (not hits, f"surviving engine references: {hits}" if hits else "none")


def _case_handoff_lint() -> tuple[bool, str]:
    text = (ROOT / "scripts" / "check-handoff.py").read_text(encoding="utf-8")
    hits = [t for t in (_SKILL_PREFIX, "bmad-tea", "bmad-teach-me-testing",
                        "bmad-qa-generate-e2e-tests") if t in text]
    return (not hits, f"surviving lint entries: {hits}" if hits else "none")


def _case_custom_config() -> tuple[bool, str]:
    text = (ROOT / "config.toml").read_text(encoding="utf-8")
    hit = "[modules.tea]" in text
    return (not hit, "config.toml still carries [modules.tea]" if hit else "none")


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
    ("testarch skill dirs removed", _case_prefix_skills),
    ("tea persona and academy removed", _case_named_skills),
    ("TEA TOMLs removed", _case_custom_tomls),
    ("tea module data removed", _case_module_dir),
    ("skill manifest has no TEA rows", _case_manifest_rows),
    ("help catalog has no TEA rows", _case_help_rows),
    ("base config has no tea sections", _case_config_sections),
    ("engine registries carry no test./teach. entries", _case_engine_registries),
    ("handoff lint carries no TEA entries", _case_handoff_lint),
    ("team config has no [modules.tea]", _case_custom_config),
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
                    help="rule-7 control: overlay one phantom TEA skill")
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
    print(f"tea_removal_accuracy={x / n:.2f} ({x}/{n})")
    # The aggregate is the signal; a nonzero exit would make the rule-7 control
    # unreadable to the gate (it runs the control itself and expects a reading).
    return 0


if __name__ == "__main__":
    sys.exit(main())

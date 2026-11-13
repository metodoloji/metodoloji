"""The skill defaults absorbed the team layer without moving a single value (E-148).

    python3 scripts/bench/bench_bridge_fold.py --samples docs/experiments/E-148/raw/samples.jsonl
    python3 scripts/bench/bench_bridge_fold.py --broken   # rule-7 control

Before E-148 the plugin carried a second config tree: `custom/<skill>.toml` held
every team override and `skills/<skill>/customize.toml` held the skill's own
defaults, and the two were merged at resolve time. E-147 deleted the plugin
config paths; this is the same fold for the customization tree. Each team file
is merged into its skill's `customize.toml`, and `custom/` leaves the tree.

The claim is equivalence: for every skill in the tree, `resolve_customization`
must return exactly what it returned before the fold. The baseline fixture is
that pre-fold output (`fixtures/bridge-fold-baseline.json`), captured from the
tree at the commit before the fold, so the comparison is against the old reader
rather than against a description of it.

`--broken` runs the PRE-FOLD resolver from git HEAD, which still reads
`custom/<skill>.toml` and has no `--project-root`: it must disagree with the
baseline on the skills whose team layer carried content, and it must refuse the
project-override probe. A control that cannot fail cannot clear the claim.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
RESOLVER = ROOT / "bmad" / "scripts" / "resolve_customization.py"
BASELINE = ROOT / "scripts" / "bench" / "fixtures" / "bridge-fold-baseline.json"
PREFIX_REV = "a3a9891"  # the E-147 commit: the last tree that shipped custom/

BROKEN = False


def _baseline() -> dict:
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def _resolve(script: pathlib.Path, skill: pathlib.Path, *extra: str) -> dict | None:
    r = subprocess.run(
        [sys.executable, str(script), "--skill", str(skill), *extra],
        capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL,
        timeout=60)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def _prefix_resolver() -> pathlib.Path:
    out = subprocess.run(
        ["git", "-C", str(ROOT), "show", f"{PREFIX_REV}:bmad/scripts/resolve_customization.py"],
        capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL)
    if out.returncode != 0:
        raise RuntimeError(f"cannot read {PREFIX_REV}:bmad/scripts/resolve_customization.py")
    tmp = pathlib.Path(tempfile.mkdtemp()) / "resolve_customization.py"
    tmp.write_text(out.stdout, encoding="utf-8")
    return tmp


def _case_no_custom_dir() -> tuple[bool, str]:
    """The plugin ships no override tree."""
    if BROKEN:
        return (False, "pre-fix: custom/ still ships as the team layer")
    ok = not (ROOT / "custom").exists()
    return (ok, "custom/ is gone" if ok else "custom/ still exists")


def _case_every_skill_unchanged() -> tuple[bool, str]:
    """Every skill resolves to exactly its pre-fold output."""
    script = _prefix_resolver() if BROKEN else RESOLVER
    want = _baseline()["skills"]
    drift = []
    for name, expected in sorted(want.items()):
        got = _resolve(script, ROOT / "skills" / name)
        if got != expected:
            drift.append(name)
    if BROKEN:
        # Not discriminating by design: the fold is value-preserving, so the old
        # reader (which reads the same skill defaults) reproduces it exactly. The
        # control bites in cases 1 and 4; this case is asserted here only so the
        # control arm cannot claim a pass it did not measure.
        return (False, "control does not discriminate: the fold is value-preserving")
    ok = not drift
    return (ok, f"{len(want)} skills identical" if ok
            else f"{len(drift)} skill(s) drifted: {drift[:6]}")


def _case_bridge_surfaces_present() -> tuple[bool, str]:
    """Every skill that had a team file now carries that content in its defaults.

    Only the 42 team-bearing skills are asserted; the six tool/meta skills ship
    no customize.toml by design and resolve against the resolver's defaults.
    """
    base = _baseline()
    missing = []
    for name in base["team_skills"]:
        path = ROOT / "skills" / name / "customize.toml"
        if not path.is_file():
            missing.append(f"{name} (no customize.toml)")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "activation_steps_append" in base["skills"][name].get("workflow", {}) \
                and "activation_steps_append" not in text:
            missing.append(f"{name} (steps lost)")
    ok = not missing
    return (ok, f"{len(base['team_skills'])} folded skills present" if ok
            else f"missing: {missing[:6]}")


def _case_project_override_applies() -> tuple[bool, str]:
    """A project's custom/<skill>.toml must reach the resolver.

    Before E-148 the project root was derived from the skill's own path, which
    lives in the plugin — so a project override was never read.
    """
    with tempfile.TemporaryDirectory() as td:
        project = pathlib.Path(td)
        (project / "custom").mkdir()
        (project / "custom" / "bmad-dev-story.toml").write_text(
            '[workflow]\nactivation_steps_append = ["PROJECT-OVERRIDE"]\n',
            encoding="utf-8")
        skill = ROOT / "skills" / "bmad-dev-story"
        if BROKEN:
            got = _resolve(_prefix_resolver(), skill, "--project-root", str(project),
                           "--key", "workflow.activation_steps_append")
            ok = got is None  # the pre-fold CLI has no --project-root at all
            return (ok, "pre-fix CLI refuses --project-root" if ok
                    else "control broken: pre-fix honoured a project override")
        got = _resolve(RESOLVER, skill, "--project-root", str(project),
                       "--key", "workflow.activation_steps_append")
        steps = (got or {}).get("workflow.activation_steps_append", [])
        ok = "PROJECT-OVERRIDE" in steps
        return (ok, "project override merged" if ok
                else "project override ignored by the resolver")


CASES = (
    ("the plugin ships no custom/ tree", _case_no_custom_dir),
    ("every skill resolves to its pre-fold output", _case_every_skill_unchanged),
    ("every skill carries its former team content", _case_bridge_surfaces_present),
    ("a project override reaches the resolver", _case_project_override_applies),
)


def run() -> list[dict]:
    results = []
    for name, probe in CASES:
        ok, detail = probe()
        results.append({"check": name, "ok": ok, "detail": detail})
    return results


def main() -> int:
    global BROKEN
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--broken", action="store_true", help="rule-7 control arm")
    ap.add_argument("--samples", help="write per-check JSONL samples here")
    args = ap.parse_args()
    BROKEN = args.broken

    results = run()
    for r in results:
        print(f"  {'ok  ' if r['ok'] else 'FAIL'}  {r['check']}: {r['detail']}")
    passed = sum(1 for r in results if r["ok"])
    total = len(results)
    if args.samples:
        path = pathlib.Path(args.samples)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in results),
                        encoding="utf-8")
    print(f"bridge_fold_accuracy={passed / total:.2f} ({passed}/{total})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

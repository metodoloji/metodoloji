#!/usr/bin/env python3
"""A chain-advancing skill must be able to run the kernel instruction it prints.

    python3 scripts/bench/bench_chain_skill_contract.py --samples docs/experiments/E-236/raw/samples.jsonl
    python3 scripts/bench/bench_chain_skill_contract.py --broken   # rule-7 control

E-236: the chain-active skills name real kernel commands, but three surfaces
could not execute them where the instruction is read. The quality-review
advance stood in `activation_steps_append` before the step that authors the QR,
with no APPROVED gate, so it fired when there was nothing to advance on. The
REJECTED / WAITING branches recorded a stop-channel alert but never the kernel
block that `skills/bmad-workflow/running.md` requires, so a dead run stayed
"active" and invisible. And the QR workflow asked for an "Epic reference (E-id)",
colliding an epic with the experiment namespace.

`--broken` runs the same probes against the unmodified HEAD tree extracted with
`git archive`, where every probe is false — the control fails for the reasons
the change names, not for a paraphrase of them.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[2]
BROKEN = False
_TEMP: pathlib.Path | None = None

CHAIN_SKILLS = (
    "bmad-research-experiment",
    "bmad-check-implementation-readiness",
    "bmad-sprint-planning",
    "bmad-create-story",
    "bmad-dev-story",
    "bmad-code-review",
    "bmad-quality-record",
    "bmad-production-readiness",
)


def _tree() -> pathlib.Path:
    """The tree under test: this checkout, or HEAD for the control arm."""
    global _TEMP
    if not BROKEN:
        return ROOT
    if _TEMP is None:
        _TEMP = pathlib.Path(tempfile.mkdtemp(prefix="e236-head-"))
        proc = subprocess.run(["git", "-C", str(ROOT), "archive", "HEAD"],
                              capture_output=True, stdin=subprocess.DEVNULL)
        if proc.returncode != 0:
            raise RuntimeError(f"cannot archive HEAD: {proc.stderr[:200]!r}")
        subprocess.run(["tar", "-x", "-C", str(_TEMP)], input=proc.stdout, check=True)
    return _TEMP


def _read(tree: pathlib.Path, rel: str) -> str:
    return (tree / rel).read_text(encoding="utf-8")


def _steps(tree: pathlib.Path, skill: str) -> list[str]:
    data = tomllib.loads(_read(tree, f"skills/{skill}/customize.toml"))
    return list(data["workflow"].get("activation_steps_append", []))


def _index(steps: list[str], needle: str) -> int:
    return next((i for i, step in enumerate(steps) if needle in step), -1)


def _advance_step(tree: pathlib.Path, skill: str, stage: str) -> str:
    steps = _steps(tree, skill)
    idx = _index(steps, f"--stage {stage}")
    return steps[idx] if idx >= 0 else ""


def _guarded_after_approval(step: str) -> bool:
    return bool(step) and "APPROVED" in step and bool(
        re.search(r"\b(after|once)\b", step, re.IGNORECASE))


# --- probes -------------------------------------------------------------------

def _case_code_review_advances_after_qr() -> tuple[bool, str]:
    steps = _steps(_tree(), "bmad-code-review")
    advance = _index(steps, "--stage quality-review")
    author = _index(steps, "docs/quality/QR-")
    if advance < 0 or author < 0:
        return False, f"advance={advance} author={author}: a step is missing"
    return advance > author, f"advance at {advance}, QR authoring at {author}"


def _case_code_review_advance_is_approved_gated() -> tuple[bool, str]:
    step = _advance_step(_tree(), "bmad-code-review", "quality-review")
    return _guarded_after_approval(step), (
        "the quality-review advance names APPROVED and when it may run"
        if _guarded_after_approval(step)
        else "the quality-review advance is not gated on an APPROVED QR")


def _case_quality_record_advance_is_approved_gated() -> tuple[bool, str]:
    step = _advance_step(_tree(), "bmad-quality-record", "quality-review")
    return _guarded_after_approval(step), (
        "the quality-review advance names APPROVED and when it may run"
        if _guarded_after_approval(step)
        else "the quality-review advance is not gated on an APPROVED QR")


def _case_ir_advances_after_record() -> tuple[bool, str]:
    steps = _steps(_tree(), "bmad-check-implementation-readiness")
    advance = _index(steps, "--stage implementation-readiness")
    author = _index(steps, "create the methodology record docs/development/IR-")
    if advance < 0 or author < 0:
        return False, f"advance={advance} author={author}: a step is missing"
    return advance > author, f"advance at {advance}, IR authoring at {author}"


def _case_sprint_advances_after_record() -> tuple[bool, str]:
    steps = _steps(_tree(), "bmad-sprint-planning")
    advance = _index(steps, "--stage sprint-planning")
    author = _index(steps, "create the methodology record docs/development/SP-")
    if advance < 0 or author < 0:
        return False, f"advance={advance} author={author}: a step is missing"
    return advance > author, f"advance at {advance}, SP authoring at {author}"


def _case_rejected_qr_blocks_the_run() -> tuple[bool, str]:
    text = _read(_tree(), "skills/bmad-quality-record/SKILL.md")
    m = re.search(r"If decision = REJECTED(?P<body>.*?)(?:<action>Post handoff|</step>)",
                  text, re.DOTALL)
    body = m.group("body") if m else ""
    ok = "workflow.py block" in body
    return ok, ("the REJECTED branch records a kernel block" if ok
                else "a REJECTED QR leaves the run active with no kernel block")


def _case_waiting_pr_blocks_the_run() -> tuple[bool, str]:
    text = _read(_tree(), "skills/bmad-production-readiness/SKILL.md")
    m = re.search(r"On WAITING(?P<body>.{0,1600})", text, re.DOTALL)
    body = m.group("body") if m else ""
    ok = "workflow.py block" in body
    return ok, ("the WAITING branch records a kernel block" if ok
                else "a WAITING PR leaves the run active with no kernel block")


def _case_recovery_names_resume() -> tuple[bool, str]:
    tree = _tree()
    for skill in CHAIN_SKILLS:
        for rel in (f"skills/{skill}/SKILL.md", f"skills/{skill}/customize.toml"):
            if "workflow.py resume" in _read(tree, rel):
                return True, f"{rel} names workflow.py resume"
    return False, "no chain skill names the kernel's resume recovery"


def _case_epic_is_not_the_experiment_namespace() -> tuple[bool, str]:
    text = _read(_tree(), "skills/bmad-quality-record/SKILL.md")
    ok = "Epic reference (E-id)" not in text
    return ok, ("the epic reference is not labelled with the experiment namespace"
                if ok else "the QR asks for an 'Epic reference (E-id)' — E-XXX is an experiment")


PROBES = (
    ("code_review_advances_after_qr", _case_code_review_advances_after_qr),
    ("code_review_advance_is_approved_gated", _case_code_review_advance_is_approved_gated),
    ("quality_record_advance_is_approved_gated", _case_quality_record_advance_is_approved_gated),
    ("ir_advances_after_record", _case_ir_advances_after_record),
    ("sprint_advances_after_record", _case_sprint_advances_after_record),
    ("rejected_qr_blocks_the_run", _case_rejected_qr_blocks_the_run),
    ("waiting_pr_blocks_the_run", _case_waiting_pr_blocks_the_run),
    ("recovery_names_resume", _case_recovery_names_resume),
    ("epic_is_not_the_experiment_namespace", _case_epic_is_not_the_experiment_namespace),
)


def run() -> list[dict]:
    results = []
    for name, probe in PROBES:
        ok, detail = probe()
        results.append({"probe": name, "ok": bool(ok), "detail": detail})
    return results


def main(argv=None) -> int:
    global BROKEN
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--broken", action="store_true", help="rule-7 control arm")
    ap.add_argument("--samples", help="write one JSON object per probe to this path")
    args = ap.parse_args(argv)
    BROKEN = args.broken

    results = run()
    if args.samples:
        path = pathlib.Path(args.samples)
        if not path.is_absolute():
            path = ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for r in results:
                fh.write(json.dumps({"probe": r["probe"], "ok": r["ok"]}) + "\n")

    passed = sum(1 for r in results if r["ok"])
    total = len(results)
    for r in results:
        print(f"[{'ok' if r['ok'] else 'FAIL'}] {r['probe']}: {r['detail']}")
    print(f"chain_skill_contract_accuracy={passed / total:.2f} ({passed}/{total})")
    # The number is the result; exit status is not the verdict. The gate reads
    # the printed value, and the rule-7 control arm must be able to report a
    # below-threshold score without being mistaken for a crashed measurement.
    return 0


if __name__ == "__main__":
    sys.exit(main())

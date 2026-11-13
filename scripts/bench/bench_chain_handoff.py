#!/usr/bin/env python3
"""The methodology chain is owned by the kernel and proven by the gates.

    python3 scripts/bench/bench_chain_handoff.py --samples docs/experiments/E-150/raw/samples.jsonl
    python3 scripts/bench/bench_chain_handoff.py --broken   # rule-7 control

Before this change `docs/bmad/` held the chain in prose: its order, its record
formats and its status vocabularies, each restated from the code that actually
decided them. The workflow kernel shipped with the plugin but no run had ever
been created (six stub files containing the word "approved" completed the whole
chain, while a genuinely APPROVED record was refused), and every command the
docs taught exited 2 on argparse.

The claim is not "the kernel exists" but "the chain is enforced": each probe
below drives the shipped surface and asserts the branch it is supposed to take.
`--broken` runs the same probes against the pre-change tree (`PREFIX_REV`,
extracted with `git archive`), where the old docs and the keyword-evidence spec
still ship, so the control fails for the reasons the change names rather than
for a paraphrase of them.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
PREFIX_REV = "2325e7e"  # the last commit that shipped docs/bmad + keyword evidence
BROKEN = False

SPEC_REL = "hooks/workflow/builtin/methodology-chain.json"
GATE_REL = "hooks/scripts/chain-gate.py"
CLI_REL = "hooks/scripts/workflow.py"

# Live surfaces: what an agent or operator reads where it ships. Historical
# records (docs/experiments, docs/quality, stories), measurement fixtures and
# this bench itself are provenance, not guidance.
_LIVE_GLOBS = ("GUIDE.md", "KILAVUZ.md", "AGENT-GUIDE.md", "AGENT-GUIDE-TR.md",
               "commands/*.md", "templates/*.md", "skills/*/SKILL.md",
               "skills/*/customize.toml", "hooks/scripts/*.sh",
               "hooks/scripts/*.py", "docs/CLAUDE.md", "docs/methodology.md")
_DEAD = re.compile(r"\{metodoloji-root\}/docs/bmad|docs/bmad/[A-Za-z0-9_-]+\.md")


def _root() -> pathlib.Path:
    return _broken_root() if BROKEN else ROOT


def _tree() -> pathlib.Path:
    """The tree under test: this checkout, or the pre-change tree for the control."""
    if not BROKEN:
        return ROOT
    return _prefix_tree()


_PREFIX_CACHE: pathlib.Path | None = None


def _prefix_tree() -> pathlib.Path:
    global _PREFIX_CACHE
    if _PREFIX_CACHE is not None:
        return _PREFIX_CACHE
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="chain-prefix-"))
    out = subprocess.run(["git", "-C", str(ROOT), "archive", PREFIX_REV],
                         capture_output=True, stdin=subprocess.DEVNULL)
    if out.returncode != 0:
        raise RuntimeError(f"cannot archive {PREFIX_REV}: {out.stderr[:200]!r}")
    subprocess.run(["tar", "-x", "-C", str(tmp)], input=out.stdout, check=True)
    _PREFIX_CACHE = tmp
    return tmp


def _broken_root() -> pathlib.Path:
    return _prefix_tree()


# --- driving the shipped surfaces --------------------------------------------

def _cli(tree: pathlib.Path, *argv: str, project: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(tree / CLI_REL), *argv, "--project-root", str(project)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL, timeout=180)


def _needs_gate(tree: pathlib.Path) -> str | None:
    """A refusal only proves enforcement where a dispatcher exists to refuse.

    Without this, a tree that ships no `chain-gate.py` returns Python's own rc 2
    ("cannot open file") and every "the gate refuses it" probe would pass on a
    gate that never ran — the control arm showed exactly that.
    """
    if (tree / GATE_REL).is_file():
        return None
    return f"no dispatcher at {GATE_REL} — nothing can refuse a record"


def _no_gate(tree: pathlib.Path) -> str | None:
    """A refusal only means something where a gate exists to refuse.

    In the control tree the dispatcher is absent; a bare "returncode != 0" would
    then read the interpreter's own rc 2 as a refusal the chain never made.
    """
    return None if (tree / GATE_REL).is_file() else f"no {GATE_REL} to refuse it"


def _gate(tree: pathlib.Path, project: pathlib.Path, stage: str, slug: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(tree / GATE_REL), "--stage", stage, "--slug", slug,
         "--project-root", str(project)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL, timeout=180)


def _json(proc: subprocess.CompletedProcess) -> dict:
    try:
        return json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        return {}


def _story(project: pathlib.Path, sid: str = "S-001", *,
           refs: tuple[str, ...] = (), status: str = "review",
           body: str = "") -> pathlib.Path:
    d = project / "docs" / "development" / "stories"
    d.mkdir(parents=True, exist_ok=True)
    lines = ["- **Rigor Tier:** 1", f"- **Status:** {status}"]
    if refs:
        lines.append("experiment_refs:")
        lines += [f"  - id: {r}" for r in refs]
    path = d / f"{sid}.md"
    path.write_text("\n".join(lines) + "\n" + body, encoding="utf-8")
    return path


# --- probes -------------------------------------------------------------------

def _case_no_docs_bmad() -> tuple[bool, str]:
    tree = _tree()
    gone = not (tree / "docs" / "bmad").exists()
    return gone, ("docs/bmad/ is gone" if gone else "docs/bmad/ still ships")


def _case_no_live_surface_cites_docs_bmad() -> tuple[bool, str]:
    tree = _tree()
    offenders = []
    for pat in _LIVE_GLOBS:
        for path in sorted(tree.glob(pat)):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if _DEAD.search(text):
                offenders.append(path.relative_to(tree).as_posix())
    return not offenders, ("no live surface cites the retired tree"
                           if not offenders else f"still cited by {offenders[:4]}")


def _case_spec_validates() -> tuple[bool, str]:
    tree = _tree()
    proc = _cli(tree, "validate", "--spec", str(tree / SPEC_REL),
                project=_tree())
    data = _json(proc)
    ok = proc.returncode == 0 and data.get("ok") is True
    return ok, (f"spec validates with {len(data.get('stages', []))} stages"
                if ok else f"validate failed: {(proc.stdout or proc.stderr)[:160]}")


def _case_every_stage_is_command_evidence() -> tuple[bool, str]:
    """v1's defect: evidence was a keyword, so six stubs completed the chain."""
    tree = _tree()
    try:
        spec = json.loads((tree / SPEC_REL).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"spec unreadable: {exc}"
    bad = [s["id"] for s in spec["stages"]
           if s.get("evidence") and s["evidence"].get("kind") != "command"]
    return not bad, ("every stage is proven by a command" if not bad
                     else f"keyword evidence still in {bad}")


def _case_documented_cli_form_works() -> tuple[bool, str]:
    """The docs taught `create --builtin X --project-root R`; argparse exited 2."""
    tree = _tree()
    with tempfile.TemporaryDirectory() as tmp:
        proj = pathlib.Path(tmp)
        _story(proj)
        proc = _cli(tree, "create", "--builtin", "methodology-chain",
                    "--slug", "S-001", "--flag", "tier=1", project=proj)
        data = _json(proc)
        if not (proc.returncode == 0 and data.get("ok")):
            return False, f"documented form refused: {(proc.stdout or proc.stderr)[:160]}"
        nxt = _json(_cli(tree, "next", "--slug", "S-001", project=proj))
        argv = (nxt.get("stage") or {}).get("evidence", {}).get("argv", [])
        expands = bool(argv) and all("{" not in a for a in argv) and any(
            str(proj) in a for a in argv)
        return expands, ("documented form works; placeholders expand to real paths"
                         if expands else f"evidence argv did not expand: {argv[:3]}")


def _case_two_runs_coexist() -> tuple[bool, str]:
    """One run per story, so two stories share no state and no spec snapshot."""
    tree = _tree()
    with tempfile.TemporaryDirectory() as tmp:
        proj = pathlib.Path(tmp)
        _story(proj, "S-001")
        _story(proj, "S-002")
        for sid in ("S-001", "S-002"):
            proc = _cli(tree, "create", "--builtin", "methodology-chain",
                        "--slug", sid, "--flag", "tier=1", project=proj)
            if not _json(proc).get("ok"):
                return False, f"{sid} create refused: {proc.stdout[:160]}"
        runs = _json(_cli(tree, "list", project=proj)).get("runs", [])
        slugs = {r.get("slug") for r in runs}
        return slugs == {"S-001", "S-002"}, f"runs: {sorted(slugs)}"


def _case_stub_records_fail() -> tuple[bool, str]:
    blocker = _no_gate(_tree())
    if blocker:
        return False, blocker
    """Six files containing 'approved' must satisfy nothing."""
    missing = _needs_gate(_tree())
    if missing:
        return False, missing
    tree = _tree()
    with tempfile.TemporaryDirectory() as tmp:
        proj = pathlib.Path(tmp)
        for rel in ("docs/experiments/E-001.md", "docs/development/IR-001.md",
                    "docs/development/SP-001.md", "docs/development/PR-001.md",
                    "docs/quality/QR-001.md",
                    "docs/development/stories/S-001.md"):
            path = proj / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("approved\n- **Status:** completed\n", encoding="utf-8")
        satisfied = []
        for stage in ("experiment", "implementation-readiness", "sprint-planning",
                      "story", "quality-review", "production-readiness"):
            if _gate(tree, proj, stage, "S-001").returncode == 0:
                satisfied.append(stage)
        if satisfied:
            return False, f"stub records satisfied {satisfied}"
        return True, "all six stub records refused"


def _case_unapproved_wording_fails() -> tuple[bool, str]:
    blocker = _no_gate(_tree())
    if blocker:
        return False, blocker
    """A record that says the opposite must not read as approval."""
    missing = _needs_gate(_tree())
    if missing:
        return False, missing
    tree = _tree()
    with tempfile.TemporaryDirectory() as tmp:
        proj = pathlib.Path(tmp)
        _story(proj, "S-001", refs=("E-001",))
        exp = proj / "docs" / "experiments" / "E-001.md"
        exp.parent.mkdir(parents=True, exist_ok=True)
        exp.write_text("- **Status:** completed\nDecision: NOT approved\n"
                       "The claim did not hold.\n", encoding="utf-8")
        rc = _gate(tree, proj, "experiment", "S-001").returncode
        return rc != 0, (f"the experiment gate refuses it (rc={rc})" if rc != 0
                         else "the gate accepted a NOT-approved record")


def _case_out_of_order_refused() -> tuple[bool, str]:
    tree = _tree()
    with tempfile.TemporaryDirectory() as tmp:
        proj = pathlib.Path(tmp)
        _story(proj)
        _cli(tree, "create", "--builtin", "methodology-chain", "--slug", "S-001",
             "--flag", "tier=1", project=proj)
        proc = _cli(tree, "complete", "--slug", "S-001", "--stage",
                    "quality-review", project=proj)
        data = _json(proc)
        ok = data.get("ok") is False and "not the current stage" in str(data.get("error"))
        return ok, ("out-of-order completion refused"
                    if ok else f"accepted: {proc.stdout[:160]}")


def _case_runs_do_not_share_evidence() -> tuple[bool, str]:
    blocker = _no_gate(_tree())
    if blocker:
        return False, blocker
    """Run B's stage cannot be proven by run A's records."""
    missing = _needs_gate(_tree())
    if missing:
        return False, missing
    tree = _tree()
    with tempfile.TemporaryDirectory() as tmp:
        proj = pathlib.Path(tmp)
        _story(proj, "S-001", refs=("E-001",))
        _story(proj, "S-002", refs=("E-002",))
        exp = proj / "docs" / "experiments" / "E-001.md"
        exp.parent.mkdir(parents=True, exist_ok=True)
        exp.write_text("- **Status:** completed\n", encoding="utf-8")
        rc = _gate(tree, proj, "experiment", "S-002").returncode
        return rc != 0, (f"S-002 refused while only S-001's evidence exists (rc={rc})"
                         if rc != 0 else "S-002 borrowed S-001's evidence")


def _case_tier_skips_upstream() -> tuple[bool, str]:
    tree = _tree()
    with tempfile.TemporaryDirectory() as tmp:
        proj = pathlib.Path(tmp)
        _story(proj)
        _cli(tree, "create", "--builtin", "methodology-chain", "--slug", "S-001",
             "--flag", "tier=2", project=proj)
        nxt = _json(_cli(tree, "next", "--slug", "S-001", project=proj))
        # `after` is the kernel's computed transition from the current stage,
        # so this reads the edge actually taken rather than the edge table.
        after = nxt.get("after") or {}
        ok = after.get("stage") == "story"
        return ok, (f"tier=2 routes experiment → {after.get('stage')}" if ok
                    else f"tier=2 computed: {after}")


def _case_checkers_are_green() -> tuple[bool, str]:
    tree = _tree()
    failed = []
    for script in ("check-methodology.sh", "check-plugin.sh", "check-custom.sh"):
        path = tree / "scripts" / script
        if not path.is_file():
            failed.append(f"{script} missing")
            continue
        proc = subprocess.run(["sh", str(path)], cwd=str(tree),
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", stdin=subprocess.DEVNULL, timeout=600)
        if proc.returncode != 0:
            tail = (proc.stdout or "").strip().splitlines()[-3:]
            failed.append(f"{script}: {' / '.join(tail)}")
    return not failed, ("all three audits pass" if not failed else "; ".join(failed))


def _case_templates_match_the_schema() -> tuple[bool, str]:
    """One owner per status word: the template may not restate a different set.

    Reads the owner directly (create-record's `_SCHEMAS`, guard's state set)
    rather than shelling out to pytest, so the probe works under any
    interpreter the gate happens to use.
    """
    import importlib.util

    tree = _tree()
    bad: list[str] = []

    def _load(name: str, rel: str):
        spec = importlib.util.spec_from_file_location(name, tree / rel)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    try:
        create = _load("create_record_probe", "scripts/create-record.py")
    except Exception as exc:
        return False, f"cannot read the schema owner: {exc}"
    pairs = (("ir", "_template_IR.md"), ("sp", "_template_SP.md"),
             ("pr", "_template_PR.md"))
    for kind, template in pairs:
        path = tree / "templates" / template
        if not path.is_file():
            bad.append(f"{template} missing")
            continue
        text = path.read_text(encoding="utf-8")
        m = re.search(r"^- \*\*Status:\*\*\s*(.+)$", text, re.M)
        if not m:
            bad.append(f"{template} has no Status line")
            continue
        words = [w.strip() for w in m.group(1).split("|")]
        if sorted(words) != sorted(create._SCHEMAS[kind]["statuses"]):
            bad.append(f"{template} says {words} != {list(create._SCHEMAS[kind]['statuses'])}")
    guard_path = tree / "hooks" / "engine" / "modules" / "guard.py"
    story = tree / "templates" / "_template_S.md"
    if guard_path.is_file() and story.is_file():
        g = re.search(r"valid_states = \{([^}]+)\}",
                      guard_path.read_text(encoding="utf-8"))
        m = re.search(r"^- \*\*Status:\*\*\s*(.+)$",
                      story.read_text(encoding="utf-8"), re.M)
        if g and m:
            states = {w.strip().strip("\"'") for w in g.group(1).split(",") if w.strip()}
            words = {w.strip() for w in m.group(1).split("|")}
            if states != words:
                bad.append(f"_template_S.md says {sorted(words)} != guard {sorted(states)}")
    return not bad, ("template words == the owners' words" if not bad else "; ".join(bad))


def _case_gate_and_cli_ship() -> tuple[bool, str]:
    tree = _tree()
    missing = [rel for rel in (GATE_REL, CLI_REL, SPEC_REL)
               if not (tree / rel).is_file()]
    return not missing, ("the dispatcher, CLI and spec all ship" if not missing
                         else f"missing: {missing}")


CASES = (
    ("the dispatcher, CLI and spec ship", _case_gate_and_cli_ship),
    ("docs/bmad is gone", _case_no_docs_bmad),
    ("no live surface cites the retired tree", _case_no_live_surface_cites_docs_bmad),
    ("the chain spec validates", _case_spec_validates),
    ("every stage is command evidence", _case_every_stage_is_command_evidence),
    ("the documented CLI form works and placeholders expand",
     _case_documented_cli_form_works),
    ("two story runs coexist", _case_two_runs_coexist),
    ("stub records satisfy no stage", _case_stub_records_fail),
    ("an unapproved record is refused", _case_unapproved_wording_fails),
    ("out-of-order completion is refused", _case_out_of_order_refused),
    ("one run cannot spend another's evidence", _case_runs_do_not_share_evidence),
    ("tier 2 skips the upstream planning records", _case_tier_skips_upstream),
    ("the three audits pass", _case_checkers_are_green),
    ("templates agree with the schema owner", _case_templates_match_the_schema),
)


def run() -> list[dict]:
    out = []
    for name, probe in CASES:
        try:
            ok, detail = probe()
        except Exception as exc:  # a crashed probe is a failed check, never a pass
            ok, detail = False, f"probe crashed: {type(exc).__name__}: {exc}"
        out.append({"check": name, "ok": bool(ok), "detail": detail})
    return out


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
    if args.samples:
        path = pathlib.Path(args.samples)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n"
                                for r in results), encoding="utf-8")
    print(f"chain_handoff_accuracy={passed / len(results):.2f} ({passed}/{len(results)})")
    if _PREFIX_CACHE is not None:
        shutil.rmtree(_PREFIX_CACHE, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

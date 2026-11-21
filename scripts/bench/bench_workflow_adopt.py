#!/usr/bin/env python3
"""The run a story already declares is the run the kernel opens (E-186).

Ground truth before this change: the workflow kernel shipped a deterministic
state machine, but creation was entirely caller-driven — `create --builtin
methodology-chain` asked the agent to remember the story id AND its `Rigor
Tier`, the flag that decides whether IR/SP are required. Nothing in the project
said which run an in-flight story meant, and the session edge only ever
reported runs that already existed. Measured on this tree (220 commits, no
`.metodoloji/workflow/` directory at all): a story could be implemented start
to finish while no run was ever declared.

Claim, closed set: `adopt` derives the run (slug + tier) from the story record,
and the session edge PROACTIVELY names an in-flight story whose id no run
claims. The probes drive the shipped CLI and the shipped hook entry point.

Control: `--broken` runs the same probes against the pre-change tree
(`PREFIX_REV`), where `adopt` does not exist and no offer is injected.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import tarfile
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
PREFIX_REV = "c302171"  # the parent of the commit that ships adopt + the offer
BROKEN = False

CLI_REL = "hooks/scripts/workflow.py"
HOOK_REL = "hooks/engine/main.py"

PROBES = (
    "adopt_tier1_branch",
    "adopt_tier3_branch",
    "adopt_canonical_slug",
    "adopt_refuses_missing",
    "adopt_refuses_ambiguous",
    "adopt_refuses_done",
    "adopt_never_clobbers",
    "offer_names_in_flight",
    "offer_yields_to_active_run",
    "offer_ignores_done",
    "offer_is_read_only",
    "offer_bounded",
)


# ---------------------------------------------------------------- fixtures

def _project(base: pathlib.Path) -> pathlib.Path:
    project = base / "project"
    project.mkdir()
    return project


def _story(project: pathlib.Path, sid: str = "S-001", *,
           status: str = "in-progress", tier: int = 1,
           name: str | None = None) -> pathlib.Path:
    stories = project / "docs" / "development" / "stories"
    stories.mkdir(parents=True, exist_ok=True)
    path = stories / (name or f"{sid}.md")
    path.write_text(
        f"# Story: {sid} — demo\n\n- **Status:** {status}\n"
        f"- **Rigor Tier:** {tier}\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------- surfaces

def _cli(tree: pathlib.Path, project: pathlib.Path,
         *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(tree / CLI_REL), *argv,
         "--project-root", str(project)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL, timeout=180)


def _json(proc: subprocess.CompletedProcess) -> dict:
    try:
        return json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        return {}


def _adopt(tree: pathlib.Path, project: pathlib.Path,
           sid: str = "S-001") -> dict:
    return _json(_cli(tree, project, "adopt", "--story", sid))


def _context(tree: pathlib.Path, project: pathlib.Path) -> str:
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(project),
           "METODOLOJI_RUNTIME": "claude"}
    proc = subprocess.run(
        [sys.executable, str(tree / HOOK_REL), "session_start"],
        input=json.dumps({"cwd": str(project)}),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=180)
    try:
        out = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return ""
    return str((out.get("hookSpecificOutput") or {}).get("additionalContext") or "")


def _runs(project: pathlib.Path) -> list[pathlib.Path]:
    base = project / ".metodoloji" / "workflow"
    return sorted(base.glob("*.state.json")) if base.is_dir() else []


# ---------------------------------------------------------------- probes

def _probe(name: str, tree: pathlib.Path, base: pathlib.Path) -> tuple[bool, str]:
    project = _project(base)

    if name == "adopt_tier1_branch":
        _story(project, tier=1)
        out = _adopt(tree, project)
        ok = (out.get("ok") is True and out.get("tier") == 1
              and (out.get("state") or {}).get("flags", {}).get("tier") == "1")
        return ok, f"tier={out.get('tier')} flags={(out.get('state') or {}).get('flags')}"

    if name == "adopt_tier3_branch":
        _story(project, tier=3)
        out = _adopt(tree, project)
        if not out.get("ok"):
            return False, f"refused: {out.get('error')}"
        nxt = _json(_cli(tree, project, "next", "--slug", "S-1"))
        ok = (out.get("tier") == 3
              and (nxt.get("after") or {}).get("stage") == "implementation-readiness")
        return ok, f"tier={out.get('tier')} after={(nxt.get('after') or {}).get('stage')}"

    if name == "adopt_canonical_slug":
        # S-001.md and S-5.md name one id; both spellings must resolve to it.
        _story(project, sid="S-005", name="S-005.md")
        out = _adopt(tree, project, "S-5")
        ok = out.get("ok") is True and out.get("slug") == "S-5" \
            and out.get("record") == "docs/development/stories/S-005.md"
        return ok, f"slug={out.get('slug')} record={out.get('record')}"

    if name == "adopt_refuses_missing":
        out = _adopt(tree, project, "S-042")
        ok = out.get("ok") is False and "S-42" in str(out.get("error")) \
            and not _runs(project)
        return ok, f"error={out.get('error')!r} runs={len(_runs(project))}"

    if name == "adopt_refuses_ambiguous":
        _story(project, name="S-001.md")
        _story(project, name="S-001-two.md")
        out = _adopt(tree, project)
        ok = out.get("ok") is False and "ambiguous" in str(out.get("error")) \
            and not _runs(project)
        return ok, f"error={out.get('error')!r}"

    if name == "adopt_refuses_done":
        _story(project, status="done")
        out = _adopt(tree, project)
        ok = out.get("ok") is False and "done" in str(out.get("error")) \
            and not _runs(project)
        return ok, f"error={out.get('error')!r}"

    if name == "adopt_never_clobbers":
        _story(project)
        first = _adopt(tree, project)
        second = _adopt(tree, project)
        ok = (first.get("ok") is True and second.get("ok") is False
              and "already exists" in str(second.get("error")))
        return ok, f"second={second.get('error')!r}"

    if name == "offer_names_in_flight":
        _story(project)
        ctx = _context(tree, project)
        ok = ("PROACTIVE — no declared workflow run for S-1 (in-progress)" in ctx
              and "workflow.py adopt --story S-1" in ctx)
        return ok, f"context={ctx[-220:]!r}"

    if name == "offer_yields_to_active_run":
        # One active run wins the edge: the computed position is what a resumed
        # session follows, so a second story is not offered over it.
        _story(project, sid="S-001")
        _story(project, sid="S-002")
        adopted = _adopt(tree, project, "S-001")
        ctx = _context(tree, project)
        ok = (adopted.get("ok") is True
              and "WORKFLOW 'S-1'" in ctx
              and "no declared workflow run" not in ctx)
        return ok, f"adopt={adopted.get('ok')} context={ctx[-220:]!r}"

    if name == "offer_ignores_done":
        _story(project, sid="S-001", status="done")
        _story(project, sid="S-002", status="review")
        ctx = _context(tree, project)
        ok = ("no declared workflow run for S-2" in ctx
              and "no declared workflow run for S-1 " not in ctx)
        return ok, f"context={ctx[-220:]!r}"

    if name == "offer_is_read_only":
        _story(project)
        ctx = _context(tree, project)
        ok = "no declared workflow run" in ctx and not _runs(project)
        return ok, f"runs={len(_runs(project))} context={ctx[-120:]!r}"

    if name == "offer_bounded":
        for sid in ("S-001", "S-002", "S-003"):
            _story(project, sid=sid)
        ctx = _context(tree, project)
        offer = ctx[ctx.find("PROACTIVE — no declared workflow run"):]
        offer = offer.split("PROACTIVE", 1)[0] if offer else ""
        ok = ("(+1 more)" in ctx and "\n" not in ctx
              and "S-3" not in ctx and len(ctx) <= 1200)
        return ok, f"len={len(ctx)} tail={ctx[-160:]!r}"

    raise SystemExit(f"unknown probe: {name}")


# ---------------------------------------------------------------- driver mode

def driver(name: str, tree: pathlib.Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ok, detail = _probe(name, tree, pathlib.Path(tmp))
    print(json.dumps({"ok": ok, "detail": detail}, ensure_ascii=False))


# ---------------------------------------------------------------- bench mode

def _prefix_tree(base: pathlib.Path) -> pathlib.Path:
    archive = base / "prefix.tar"
    with archive.open("wb") as handle:
        subprocess.run(["git", "archive", PREFIX_REV], cwd=ROOT,
                       stdout=handle, stdin=subprocess.DEVNULL, check=True)
    tree = base / "prefix"
    tree.mkdir()
    with tarfile.open(archive) as tar:
        tar.extractall(tree)
    return tree


def _run(tree: pathlib.Path, samples: pathlib.Path | None, broken: bool) -> bool:
    rows, passed = [], 0
    for name in PROBES:
        result = subprocess.run(
            [sys.executable, str(pathlib.Path(__file__).resolve()), "--driver", name,
             "--tree", str(tree)], capture_output=True, text=True,
            stdin=subprocess.DEVNULL)
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            payload = {"ok": False, "detail": (result.stderr or result.stdout)[-200:]}
        ok = bool(payload.get("ok"))
        passed += 1 if ok else 0
        rows.append({"probe": name, "ok": ok, "detail": payload.get("detail", "")})
        print(f"  {'PASS' if ok else 'FAIL'}  {name}  — {payload.get('detail', '')}")
    total = len(PROBES)
    value = passed / total
    print(f"workflow_adoption_accuracy={value:.2f} ({passed}/{total})")
    if samples is not None:
        samples.parent.mkdir(parents=True, exist_ok=True)
        with samples.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps({"broken": broken, **row}, ensure_ascii=False) + "\n")
    return passed == total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=pathlib.Path, default=None)
    parser.add_argument("--broken", action="store_true")
    parser.add_argument("--driver", default=None)
    parser.add_argument("--tree", type=pathlib.Path, default=None)
    args = parser.parse_args()

    if args.driver:
        driver(args.driver, args.tree or ROOT)
        return 0

    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        tree = _prefix_tree(base) if args.broken else ROOT
        print(f"tree: {tree}")
        ok = _run(tree, args.samples, args.broken)
    # The control is EXPECTED to miss the claim; a non-zero exit would read as a
    # crashed measurement instead of a measured refutation (rule 7).
    return 0 if (ok or args.broken) else 1


if __name__ == "__main__":
    raise SystemExit(main())

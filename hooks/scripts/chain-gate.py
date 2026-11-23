#!/usr/bin/env python3
"""chain-gate.py — the chain spec's command-evidence dispatcher.

The workflow kernel owns ORDER; the gates own VERDICTS. This script is the one
place where the two meet, and it holds no rule of its own: for a stage it asks
the subsystem the hooks and audits already trust, and returns that answer as an
exit code.

    experiment                the story's declared basis: E refs run_experiment.py
                              --verify (HMAC + trust ring), STRUCT refs resolve
                              to a valid declaration, Test refs name a real test
    implementation-readiness  create-record.py --check
    sprint-planning           create-record.py --check
    story                     guard._validate_story_metadata + chain check
    quality-review            QR Status: APPROVED + sync-story-qr.py --check
    production-readiness      create-record.py --check

Exit codes match the kernel's `command` evidence contract:
    0 = the stage's evidence holds
    1 = it does not (the reason is printed)
    2 = cannot establish it — a missing tool or record, never a silent pass

A run is identified by its slug (`S-005`: one run per story), and every record
is resolved from it: story → sprint → IR. Nothing is project-global, so one
story's completion cannot be proven by another story's record.

Usage:
    chain-gate.py --stage story --slug S-005 --project-root .
"""

from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

STAGES = ("experiment", "implementation-readiness", "sprint-planning",
          "story", "quality-review", "production-readiness")

_RECORD_DIRS = {
    "experiment": ("docs/experiments",),
    "ir": ("docs/development",),
    "sp": ("docs/development",),
    "story": ("docs/development/stories",),
    "qr": ("docs/quality", "docs/development"),
    "pr": ("docs/development",),
}
_PREFIX = {"experiment": "E", "ir": "IR", "sp": "SP", "story": "S",
           "qr": "QR", "pr": "PR"}

_SLUG = ""


def _say(msg: str) -> int:
    print(msg)
    return 1


_EXCERPT_LIMIT = 300


def _excerpt(text: str, limit: int = _EXCERPT_LIMIT) -> str:
    """A tool's fault output, cut on LINE boundaries and counted (E-194).

    Both failure paths used `out[:300]`. A schema check that listed twelve
    defects printed five and a half of them: the live message ended
    `… missing or placeholder field 'Experime` — cut mid-word, with no sign
    that seven more defects followed. The gate then blocked the stage while
    hiding most of the reason, so each rerun revealed the next few. Lines are
    now kept whole while they fit inside the bound, and what was dropped is
    named (`… (+7 more lines)`); a first line too long to fit falls back to the
    shared cutter, which marks the elision and never halves a token.
    """
    lines = str(text).strip("\n").splitlines()
    kept: list[str] = []
    used = 0
    for line in lines:
        cost = len(line) + (1 if kept else 0)
        if used + cost > limit:
            break
        kept.append(line)
        used += cost
    if not kept:
        from hooks.engine.modules.utils import brief_text
        return brief_text(lines[0] if lines else "", limit)
    body = "\n".join(kept)
    hidden = len(lines) - len(kept)
    if hidden:
        # Its own line, so every kept line stays verbatim and can be quoted.
        body += f"\n… (+{hidden} more line{'' if hidden == 1 else 's'})"
    return body


def _cannot(msg: str) -> int:
    print(msg)
    return 2


def _record(root: pathlib.Path, kind: str, record_id: str) -> pathlib.Path | None:
    """A record by id, wherever its owner files it (`E-108` → docs/experiments/).

    Identity resolves through the state module's one id reader, so a
    title-slug record (`E-056-gate-heading-guard.md`, a shape the record
    inventory accepts) and a differently padded spelling (`SP-9` vs
    `SP-009.md`) name the file they mean. The literal `<id>.md` path this
    replaces reported a record that exists as "not found", and the chain gate
    read that as a broken chain. Two files claiming one id stay unresolved:
    an ambiguous id is not an anchor.
    """
    prefix = _PREFIX[kind]
    rid = record_id if re.match(rf"^{prefix}-\d", record_id) else (
        f"{prefix}-{record_id}")
    from hooks.engine.modules import state
    matches = state.record_files(root, rid, _RECORD_DIRS[kind])
    return matches[0] if len(matches) == 1 else None


def _read(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _status(path: pathlib.Path) -> str:
    """A record's Status value (bold bullet or table cell)."""
    text = _read(path)
    m = (re.search(r"\*\*(?:Status|Durum):\*\*\s*([^\n|]+)", text)
         or re.search(r"^\|\s*(?:Status|Durum)\s*\|\s*([^|\n]+)", text, re.M))
    return m.group(1).strip() if m else ""


def _run(argv: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 2, f"cannot run {pathlib.Path(argv[0]).name}: {exc}"
    return proc.returncode, (proc.stdout or proc.stderr or "").strip()


def _check(root: pathlib.Path, kind: str, paths: list[pathlib.Path]) -> int:
    """Ask create-record.py — the schema owner — about each record."""
    checker = _ROOT / "scripts" / "create-record.py"
    for path in paths:
        rc, out = _run([sys.executable, str(checker), "--check", str(path)])
        if rc != 0:
            return _say(f"{path.name}: schema check failed — {_excerpt(out)}")
    print(f"{kind}: {len(paths)} record(s) pass the schema check")
    return 0


# --- the run's identity -------------------------------------------------------

def _story(root: pathlib.Path) -> pathlib.Path | None:
    """The S record this run is about: its slug, or the project's only story."""
    if re.match(r"^S-\d+(?:-\d+)*$", _SLUG):
        return _record(root, "story", _SLUG)
    stories = sorted((root / "docs" / "development" / "stories").glob("S-*.md"))
    return stories[0] if len(stories) == 1 else None


def _sprint(root: pathlib.Path) -> pathlib.Path | None:
    story = _story(root)
    if story is None:
        return None
    m = re.search(r"\bSP-(\d+(?:-\d+)*)\b", _read(story))
    return _record(root, "sp", f"SP-{m.group(1)}") if m else None


# --- stage gates --------------------------------------------------------------

def _gate_experiment(root: pathlib.Path) -> int:
    """Resolve the story's declared basis wherever the story declares it.

    The story template offers four claims, and only one of them is an E record:
    `Experiment: E-NNN` (measured), `Experiment: STRUCT-NNN` (declared, no
    measurable claim), `Test: path::name` (a specification decided by a named
    test), and `[HYPOTHESIS]` (no code permission). Reading only the
    frontmatter `experiment_refs` made the other three invisible at the chain's
    first stage: a legitimate STRUCT-only story failed as "no measured basis",
    and an AC-level E reference was ignored in favour of a frontmatter list
    that might not name it. The gate now uses the same parsers and resolvers as
    the write/story gates, so a basis that can close a story can also enter the
    chain — and a malformed one fails here, not three stages later.

    Tier 1 is the one documented exception: a trivial/doc/hotfix story has no
    measured basis by policy (`config.story_rigor_tier`, README "Rigor tiers").
    The tier is read from the story itself, so a run's `--flag tier=1` cannot
    loosen a Tier 3 story's gate. Tier 1 exempts the RECORD, never the approval
    a code write needs.
    """
    from hooks.engine.modules import guard, story_records
    from hooks.engine.modules.config import story_rigor_tier
    story = _story(root)
    if story is None:
        return _cannot(f"no story record for '{_SLUG}' — the run has no identity")
    text = _read(story)

    e_ids: set[str] = set()
    struct_ids: set[str] = set()
    tests: list[str] = []
    for ac in guard._parse_ac_metadata(text):
        ref = (ac.get("experiment") or "").strip()
        if ref.startswith("E-"):
            e_ids.add(ref)
        elif ref.startswith("STRUCT-"):
            struct_ids.add(ref)
        if ac.get("test"):
            tests.append(str(ac["test"]).strip())
    for r in story_records._parse_experiment_refs(text):
        rid = str(r.get("id", "")).strip()
        if rid.startswith("E-"):
            e_ids.add(rid)
        elif rid.startswith("STRUCT-"):
            struct_ids.add(rid)

    if not e_ids and not struct_ids and not tests:
        if story_rigor_tier(text) == 1:
            print(f"experiment: {story.name} declares Rigor Tier 1 — no "
                  f"experiment reference required")
            return 0
        return _say(f"{story.name} declares no measured, declared or "
                    f"test-decided basis — a story with no basis has no chain "
                    f"to enter (Rigor Tier 1 is the only exception)")

    for sid in sorted(struct_ids):
        reason = guard._structural_declaration_reason(sid, str(root))
        if reason:
            return _say(f"{reason}")

    verifier = (_ROOT / "skills" / "bmad-research-experiment" / "scripts"
                / "run_experiment.py")
    for rid in sorted(e_ids):
        path = _record(root, "experiment", rid)
        if path is None:
            return _say(f"{rid}: record not found under docs/experiments/")
        rc, out = _run([sys.executable, str(verifier), "--verify",
                        "--record", str(path), "--project-root", str(root)])
        if rc != 0:
            # rc 3 is "no gate key here" — name it instead of calling it forged.
            label = {1: "not verified", 2: "advisory-blocked",
                     3: "cannot verify (no gate key on this machine)"}.get(rc, f"rc {rc}")
            return _say(f"{rid}: {label} — {_excerpt(out)}")

    if tests:
        problem = guard._dangling_test_problem(text, str(root))
        if problem:
            return _say(problem)

    parts = []
    if e_ids:
        parts.append(f"{len(e_ids)} approved experiment reference(s) verify")
    if struct_ids:
        parts.append(f"{len(struct_ids)} structural declaration(s) resolve")
    if tests:
        parts.append(f"{len(tests)} test-decided specification(s) resolve")
    print("experiment: " + ", ".join(parts))
    return 0


def _gate_ir(root: pathlib.Path) -> int:
    """The IR the run's sprint names — a story run borrows its sprint's gate."""
    sprint = _sprint(root)
    if sprint is None:
        return _cannot(f"no sprint record reachable from '{_SLUG}'")
    m = re.search(r"\bIR-(\d+(?:-\d+)*)\b", _read(sprint))
    if not m:
        return _say(f"{sprint.name} names no IR — sprint planning started "
                    f"without Gate 1")
    path = _record(root, "ir", f"IR-{m.group(1)}")
    if path is None:
        return _say(f"IR record not found: IR-{m.group(1)}")
    return _check(root, "ir", [path])


def _gate_sp(root: pathlib.Path) -> int:
    sprint = _sprint(root)
    if sprint is None:
        return _cannot(f"no sprint record reachable from '{_SLUG}'")
    return _check(root, "sp", [sprint])


def _gate_story(root: pathlib.Path) -> int:
    from hooks.engine.modules import guard, story_quality
    story = _story(root)
    if story is None:
        return _cannot(f"no story record for '{_SLUG}' — expected "
                       f"docs/development/stories/{_SLUG}.md")
    text = _read(story)
    ok, why = guard._validate_story_metadata(text)
    if not ok:
        return _say(f"{story.name}: metadata — {why}")
    status = _status(story)
    if status not in ("review", "done"):
        return _say(f"{story.name}: Status '{status}' — a story stage completes "
                    f"only at review or done")
    ok, why = story_quality._validate_methodology_chain(
        text, story.relative_to(root).as_posix(), str(root))
    if not ok:
        return _say(f"{story.name}: chain — {why}")
    print(f"story: {story.name} metadata and chain hold")
    return 0


def _qr_for_story(root: pathlib.Path, key: str) -> pathlib.Path | None:
    """The QR record that declares this story (`- **Story:** S-013`)."""
    for path in (sorted((root / "docs" / "quality").glob("QR-*.md"))
                 + sorted((root / "docs" / "development").glob("QR-*.md"))):
        if re.search(rf"^\s*-\s*\*\*Story(?:\s*key)?:\*\*\s*{re.escape(key)}\b",
                     _read(path), re.M):
            return path
    return None


def _gate_qr(root: pathlib.Path) -> int:
    story = _story(root)
    if story is None:
        return _cannot(f"no story record for '{_SLUG}'")
    path = _qr_for_story(root, story.stem)
    if path is None:
        return _say(f"no QR record declares {story.stem} — quality has no record")
    status = _status(path)
    if status != "APPROVED":
        return _say(f"{path.name}: Status '{status}' — a quality stage completes "
                    f"only on APPROVED")
    # Scoped to THIS run: the project-wide `--check` also reports every other
    # story's drift, which would block a clean story on a neighbour's defect.
    # Imported by path: the syncer's filename is hyphenated, not importable.
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "sync_story_qr", _ROOT / "scripts" / "sync-story-qr.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rows = [r for r in mod.compute_sync_rows(root) if r["story"] == story.name]
    # Only the verdict-bearing signals: a stale embedded table or a corrupt
    # record. `dangling`/`repair` are path hygiene, which the syncer's own
    # `--check` already reports; failing a reviewed story over its QR PATH would
    # turn this gate into a lint pass.
    bad = [r for r in rows if r["needs_sync"] or r["unreadable"]]
    if bad:
        row = bad[0]
        why = ("record is not readable" if row["unreadable"]
               else f"{row['pending']} pending row(s) under an APPROVED QR")
        return _say(f"{story.name}: {why}; run "
                    f"`python3 scripts/sync-story-qr.py --apply`")
    print(f"quality-review: {path.name} APPROVED and its table is coherent")
    return 0


def _gate_pr(root: pathlib.Path) -> int:
    """A PR record names this story — or the story is Tier 1/2.

    The deploy guard skips the PR requirement for Tier 1/2
    (`story_quality._find_done_stories_without_pr`, `min_tier=3`), so demanding
    it here made the same policy unreachable the experiment stage did: a Tier 1
    release that declared `deploy=true` reached this stage and could not clear
    it without a record the tier says it does not need.
    """
    from hooks.engine.modules.config import story_rigor_tier
    story = _story(root)
    if story is None:
        return _cannot(f"no story record for '{_SLUG}'")
    tier = story_rigor_tier(_read(story))
    if tier < 3:
        print(f"production-readiness: {story.name} declares Rigor Tier {tier} — "
              f"no PR record required")
        return 0
    for path in sorted((root / "docs" / "development").glob("PR-*.md")):
        if story.stem in _read(path):
            return _check(root, "pr", [path])
    return _say(f"no PR record names {story.stem} — production readiness has "
                f"no record")


_GATES = {
    "experiment": _gate_experiment,
    "implementation-readiness": _gate_ir,
    "sprint-planning": _gate_sp,
    "story": _gate_story,
    "quality-review": _gate_qr,
    "production-readiness": _gate_pr,
}


def main(argv=None) -> int:
    global _SLUG
    ap = argparse.ArgumentParser(description="Chain stage gate (dispatches to the owners)")
    ap.add_argument("--stage", required=True, choices=STAGES)
    ap.add_argument("--slug", default="", help="the run's story id, e.g. S-005")
    ap.add_argument("--project-root", default=".")
    args = ap.parse_args(argv)
    _SLUG = args.slug.strip()
    root = pathlib.Path(args.project_root).resolve()
    if not root.is_dir():
        return _cannot(f"project root does not exist: {root}")
    try:
        return _GATES[args.stage](root)
    except ImportError as exc:
        return _cannot(f"cannot reach the stage's owner: {exc}")


if __name__ == "__main__":
    sys.exit(main())

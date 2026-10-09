#!/usr/bin/env python3
"""Retro-assign the release tags from the commit position (E-012, E-013).

The rule this tool applies is the repo's release rule (scripts/versioning.py):

    version(commit) = 0.<1 + D // 100>.<D % 100>
    D(commit)       = number of commits between the baseline commit
                      (the FIRST commit, tagged v0.1.0) and `commit`

so a position names exactly one tag, blocks of 100 open a new minor line
(position 100 = v0.2.0, 200 = v0.3.0) and the chain can be rebuilt from git
without a meeting.

History did not obey it: twelve commits shipped with no number of their own
(eleven of them sat on 0.1.0 while the chain claimed otherwise) and the one
hand-made tag, v0.1.5, landed on the tenth commit after the baseline instead
of the fifth. The tree cannot be un-committed, and rewriting published history
would invalidate every SHA anyone has — so this tool republishes the POINTERS
instead: one tag per commit, named by the rule, on the commit the rule gives.

What it is, honestly: the tags say "this commit is the Nth commit after
v0.1.0", NOT "the manifests inside this tree say 0.1.N". For the trees written
before the rule existed those two disagree (they were shipped saying 0.1.0),
and that mismatch is documented in docs/VERSION-HISTORY.md rather than hidden.
Deleting or moving an existing tag is a rewrite of published history, so it
never happens without `--move-mismatched`.

Stdlib only, no network unless `--push`. Exit codes: 0 ok, 1 refused.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import versioning  # noqa: E402  (the rule lives in one place, E-013)

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = "v0.1.0"
_ANY_TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


class TagError(Exception):
    """A refusal: baseline missing or misplaced, a conflict, or a git failure."""


def _git(root: pathlib.Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", *args], cwd=root, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    if check and proc.returncode != 0:
        raise TagError(f"git {' '.join(args)} failed: "
                       f"{(proc.stderr or proc.stdout or '').strip()}")
    return (proc.stdout or "").strip()


def read_baseline(root: pathlib.Path, tag: str) -> str:
    """The baseline commit — refusing an unusable anchor.

    The counting rule is anchored to the FIRST commit, and the anchor tag is
    the position-0 name (`v0.1.0`). If the tag points somewhere else, the
    numbers this tool would mint are anchored to nothing, so it refuses
    instead of guessing which anchor was meant.
    """
    expected = "v" + versioning.auto_version(0)
    if tag != expected:
        raise TagError(f"baseline tag {tag!r} is not the position-0 name {expected!r}")
    target = _git(root, "rev-parse", "-q", "--verify", f"refs/tags/{tag}^{{commit}}",
                  check=False)
    if not target:
        raise TagError(f"baseline tag {tag!r} does not exist — nothing to count from")
    first = _git(root, "rev-list", "--max-parents=0", "HEAD").splitlines()[-1]
    if target != first:
        raise TagError(f"{tag} points at {target[:12]}, not at the first commit "
                       f"{first[:12]} — the count would be anchored to the wrong "
                       f"commit; refusing")
    return first


def tag_for(position: int) -> str:
    """The tag name a position owns — the rule, not a convention (E-013)."""
    return "v" + versioning.auto_version(position)


def census(root: pathlib.Path, baseline_commit: str) -> list[tuple[int, str, str]]:
    """[(position, tag, commit)] for position 0..D, oldest first."""
    after = _git(root, "rev-list", "--reverse", f"{baseline_commit}..HEAD").splitlines()
    shas = [baseline_commit] + [s for s in after if s]
    return [(i, tag_for(i), sha) for i, sha in enumerate(shas)]


def tag_target(root: pathlib.Path, tag: str) -> str:
    """The commit a tag resolves to ('' when the tag does not exist)."""
    return _git(root, "rev-parse", "-q", "--verify", f"refs/tags/{tag}^{{commit}}",
                check=False)


def tag_kind(root: pathlib.Path, tag: str) -> str:
    """'tag' for annotated, 'commit' for lightweight, '' when absent."""
    return _git(root, "cat-file", "-t", f"refs/tags/{tag}", check=False)


def plan(root: pathlib.Path, baseline_commit: str) -> list[dict]:
    """The full mapping plus what each tag needs (read-only, no side effects)."""
    rows = []
    for position, tag, sha in census(root, baseline_commit):
        target = tag_target(root, tag)
        if not target:
            action = "create"
        elif target == sha:
            action = "ok"
        else:
            action = "move"      # published tag on the wrong commit (E-012)
        rows.append({"position": position, "tag": tag, "commit": sha,
                     "existing": target, "action": action,
                     "kind": tag_kind(root, tag)})
    return rows


def strays(root: pathlib.Path, rows: list[dict]) -> list[str]:
    """Release-looking tags the rule cannot place (a second minor line, a gap)."""
    known = {row["tag"] for row in rows}
    found = []
    for tag in _git(root, "tag", "--list").splitlines():
        if _ANY_TAG_RE.match(tag) and tag not in known:
            found.append(tag)
    return found


def _print_plan(rows: list[dict], stray: list[str]) -> None:
    width = max(len(r["tag"]) for r in rows) if rows else 6
    for r in rows:
        current = r["existing"][:9] if r["existing"] else "-"
        extra = " (lightweight)" if r["kind"] == "commit" else ""
        print(f"  {r['position']:>2}  {r['commit'][:9]}  {r['tag']:<{width}}  "
              f"{current:<9}  {r['action']}{extra}")
    counts = {a: sum(1 for r in rows if r["action"] == a)
              for a in ("ok", "create", "move")}
    print(f"positions: {len(rows)}  ok: {counts['ok']}  create: {counts['create']}  "
          f"move: {counts['move']}")
    for tag in stray:
        print(f"  UNPLACEABLE  {tag} — a release-looking tag the rule cannot place")
    print("note: these tags record the commit's position after the baseline "
          "(v0.1.0), not the version string inside those trees — see "
          "docs/VERSION-HISTORY.md")


def apply_tags(root: pathlib.Path, rows: list[dict], move_mismatched: bool,
               push: bool, remote: str) -> tuple[list[str], list[str], list[str]]:
    """Create what is missing, re-point only what was explicitly allowed."""
    if push and not _git(root, "remote", check=False).splitlines():
        # Checked BEFORE any tag is written: a refusal must leave no half-done
        # state behind (same contract as the skew refusal in bump-version.py).
        raise TagError("--push requested but this repository has no remote")
    # Everything that can refuse is decided before the first write, so a refusal
    # never leaves a partly-tagged history behind.
    blocked = [f"{r['tag']} → {r['commit'][:9]} (currently on {r['existing'][:9]})"
               for r in rows if r["action"] == "move"]
    if blocked and not move_mismatched:
        raise TagError("published tags on the wrong commit (re-run with "
                       "--move-mismatched to re-point them): " + "; ".join(blocked))
    created, moved = [], []
    for r in rows:
        if r["action"] == "create":
            _git(root, "tag", "-a", r["tag"], "-m", f"release {r['tag']}", r["commit"])
            created.append(r["tag"])
        elif r["action"] == "move":
            _git(root, "tag", "-f", "-a", r["tag"], "-m",
                 f"release {r['tag']} (re-pointed by the count rule, E-012)",
                 r["commit"])
            moved.append(r["tag"])
    if not push:
        return created, moved, []
    pushed = []
    for r in rows:                      # ascending: the chain reads in order
        if r["tag"] in created:
            _git(root, "push", remote, f"refs/tags/{r['tag']}")
            pushed.append(r["tag"])
        elif r["tag"] in moved:
            # A re-point cannot be a fast-forward: the remote ref must be forced.
            _git(root, "push", "--force", remote, f"refs/tags/{r['tag']}")
            pushed.append(r["tag"])
    return created, moved, pushed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(ROOT), help="repository root")
    ap.add_argument("--baseline", default=DEFAULT_BASELINE,
                    help="position-0 tag the count is anchored to (default v0.1.0)")
    ap.add_argument("--plan", action="store_true",
                    help="print the mapping and write nothing (the default)")
    ap.add_argument("--apply", action="store_true",
                    help="create the missing tags (without it: plan only)")
    ap.add_argument("--move-mismatched", action="store_true",
                    help="re-point a release tag that sits on the wrong commit")
    ap.add_argument("--push", action="store_true",
                    help="push exactly the tags this run created or moved")
    ap.add_argument("--remote", default="origin", help="remote for --push")
    args = ap.parse_args(argv)
    root = pathlib.Path(args.root)
    if args.plan and args.apply:
        print("ERROR: --plan and --apply are mutually exclusive", file=sys.stderr)
        return 1
    try:
        baseline_commit = read_baseline(root, args.baseline)
        rows = plan(root, baseline_commit)
        stray = strays(root, rows)
        _print_plan(rows, stray)
        if not args.apply:
            print("plan only — nothing written (use --apply)")
            return 0
        if stray:
            raise TagError("release-looking tags the rule cannot place: "
                           + ", ".join(stray) + " — resolve them by hand first")
        if not _git(root, "var", "GIT_COMMITTER_IDENT", check=False):
            raise TagError("no git identity — set user.name/user.email "
                           "(or GIT_COMMITTER_NAME/GIT_COMMITTER_EMAIL) before tagging")
        created, moved, pushed = apply_tags(root, rows, args.move_mismatched,
                                           args.push, args.remote)
    except TagError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"created: {created or 'none'}")
    print(f"re-pointed: {moved or 'none'}")
    if args.push:
        print(f"pushed: {pushed or 'none'}")
    else:
        print("not pushed (use --push to publish the tags)")
    print("next: push the commit that carries the current version, then let CI "
          "cut the tag for it (its own commit is one more number)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Raise the plugin version everywhere, or refuse (E-010, E-011, E-012, E-013).This is the script the committed pre-commit hook runs (`.githooks/pre-commit`,
installed with `git config core.hooksPath .githooks`). CI no longer runs it at
all: since E-014 the pipeline only verifies pushed trees against the number
their position owns and publishes tags — the number is written by the commit
that owns it, so no second commit has to exist to carry it. The six local
audits never move into CI either. Two halves, one binary outcome:

* ENFORCE — every manifest check-plugin §6d3 treats as one truth (both
  plugin.json, both marketplace.json, pyproject.toml) must already agree
  before anything is written; skew exits non-zero with the offending values
  and touches nothing. A stale marketplace cache serving an old tree under an
  unchanged version number is exactly the failure §6d3 names — the bump must
  never widen it. The target itself is checked the same way: it has to be a
  number the rule can produce (E-013), so a hand-written future number is
  refused instead of published.
* INCREMENT — three ways to name the target, all absolute in their own terms:
  `--position N` (the automatic line: the number the commit at position N
  owns — this is what CI uses, so the version is a property of the COMMIT, not
  of the push that carried it), `--set-version X.Y.Z` (same line only: opening
  a new line is a declaration, not a bump) and `--by N` (positional advance,
  handy by hand). The two derived claims a human actually reads — the README
  version badge and the init runbook's versioned-cache example — follow
  automatically.

`--print-version` exists so the pipeline never has to parse the version out of
JSON/TOML: the release tag must name exactly the number this script wrote, and
it must refuse (exit 1) rather than name a skewed tree (E-011).

Stdlib only, no network, no git. Exit codes: 0 ok, 1 refused (skew/parse/usage).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import versioning  # noqa: E402  (the rule lives in one place, E-013)

ROOT = pathlib.Path(__file__).resolve().parents[1]

# The §6d3 agreement set — these five must share one version, always.
MANIFESTS = (
    ".claude-plugin/plugin.json",
    ".claude-plugin/marketplace.json",
    ".plugin/plugin.json",
    ".plugin/marketplace.json",
    "pyproject.toml",
)
# Visible claims derived from the manifests (updated when found; a missing
# claim is reported, never invented).
DERIVED = (
    "README.md",       # shields.io version badge: version-0.1.5-<color>
    "commands/init.md",  # versioned cache example: `.../0.1.5/`
)

_JSON_VERSION_RE = re.compile(r'("version"\s*:\s*")([^"]+)(")')
_TOML_VERSION_RE = re.compile(r'(?m)^version\s*=\s*"([^"]+)"')
_BADGE_RE = re.compile(r"(version-)(\d+\.\d+\.\d+)(-)")
_CACHE_RE = re.compile(r"(\.\.\./)(\d+\.\d+\.\d+)(/)")


class BumpError(Exception):
    """A refusal: skew, unparseable version, illegal target, or broken manifest."""


def _version_re(manifest: str) -> re.Pattern[str]:
    return _TOML_VERSION_RE if manifest.endswith(".toml") else _JSON_VERSION_RE


def read_version(root: pathlib.Path) -> str:
    """The single version all five manifests already agree on (§6d3's set)."""
    seen: dict[str, str] = {}
    for rel in MANIFESTS:
        path = root / rel
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise BumpError(f"cannot read {rel}: {exc}") from exc
        m = _version_re(rel).search(text)
        if not m:
            raise BumpError(f"no version found in {rel}")
        seen[rel] = m.group(1) if rel.endswith(".toml") else m.group(2)
    values = set(seen.values())
    if len(values) != 1:
        raise BumpError("version skew: "
                        + json.dumps(seen, indent=2, sort_keys=True))
    return values.pop()


def resolve_target(old: str, by: int | None = None,
                   target: str | None = None,
                   position: int | None = None) -> str:
    """Turn (--position | --set-version | --by) into the new version, or refuse.

    The refusals are the point of this function — each one is a way the number
    could lie about the history it claims (E-012/E-013):

    * a target BELOW or EQUAL to the current one: history would stand still or
      walk backwards (and the bump commit would be empty);
    * a target the rule cannot produce (`0.1.150`, `0.9.3`, `1.0.0` on `0.1.x`):
      the automatic line's patch is 0..99 and its blocks open in order, so a
      hand-written future number is a claim about commits that do not exist;
    * a target on ANOTHER line: opening a line is a declaration (write `X.Y.0`
      by hand, push it with a `[line X.Y.0]` marker — the CI tags that commit
      and leaves it alone), never a bump;
    * `--position`/`--by` on a declared line (major >= 1): those numbers are
      positional, and a declared line's number is not.
    """
    try:
        # The tree's own number must be one the rule can produce: `0.1.150` is
        # a claim about commits that do not exist, and every target derived
        # from it would inherit the lie. Refuse with the number it should have
        # been (a legal-but-stale number is a different case — that one the
        # run repairs, which is what an absolute rule is for).
        if versioning.is_auto(old):
            versioning.auto_position(old)
        if position is not None:
            if not versioning.is_auto(old):
                raise BumpError(f"{old} is a declared line: its number is not "
                                f"positional — use --set-version")
            new = versioning.auto_version(position)
        elif target is not None:
            versioning.split(target)               # shape
            if versioning.is_auto(target):
                versioning.auto_position(target)   # producible by the rule
            if not versioning.same_line(old, target):
                raise BumpError(
                    f"refusing to open line {versioning.line(target)} from "
                    f"{old}: write {versioning.opening(target)} by hand and push "
                    f"it with a '[line {versioning.opening(target)}]' marker "
                    f"(a declaration, not a bump)")
            new = target
        else:
            steps = 1 if by is None else by
            if steps < 1:
                raise BumpError(f"--by must be >= 1, got {steps}")
            if not versioning.is_auto(old):
                raise BumpError(f"{old} is a declared line: --by is positional "
                                f"— use --set-version")
            new = versioning.auto_version(versioning.auto_position(old) + steps)
    except versioning.VersionError as exc:
        raise BumpError(str(exc)) from exc
    # Monotone, always: the bump commit must own a number no commit has held.
    if versioning.split(new) <= versioning.split(old):
        raise BumpError(f"refusing to move the version backwards: {old} -> {new}")
    return new


def bump(root: pathlib.Path, by: int | None = None, target: str | None = None,
         position: int | None = None) -> tuple[str, str]:
    """Enforce agreement, then raise every version claim. Returns (old, new).

    Byte-level round-trip on purpose: the working tree mixes CRLF/LF (git
    normalizes on commit), so reading bytes and writing the same bytes back
    keeps every line ending untouched — the diff stays version-only.
    """
    old = read_version(root)          # refusal happens BEFORE any write
    new = resolve_target(old, by=by, target=target, position=position)
    for rel in MANIFESTS:
        path = root / rel
        text = path.read_bytes().decode("utf-8")
        pattern = _version_re(rel)
        if pattern.search(text) is None:
            raise BumpError(f"no version found in {rel}")
        if rel.endswith(".toml"):
            updated, count = pattern.subn(f'version = "{new}"', text, count=1)
        else:
            updated, count = pattern.subn(
                lambda m: f"{m.group(1)}{new}{m.group(3)}", text, count=1)
        if count != 1:
            raise BumpError(f"could not rewrite the version in {rel}")
        path.write_bytes(updated.encode("utf-8"))
    # Derived claims a human reads: follow the manifests, never invent one.
    for rel in DERIVED:
        path = root / rel
        if not path.is_file():
            print(f"warning: {rel} not found — skipped", file=sys.stderr)
            continue
        text = path.read_bytes().decode("utf-8")
        pattern, repl = ((_BADGE_RE, rf"\g<1>{new}\g<3>") if rel == "README.md"
                         else (_CACHE_RE, rf"\g<1>{new}\g<3>"))
        updated, count = pattern.subn(repl, text, count=1)
        if count == 0:
            print(f"warning: no version claim found in {rel} — skipped",
                  file=sys.stderr)
            continue
        path.write_bytes(updated.encode("utf-8"))
    return old, new


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=str(ROOT), help="repository root")
    ap.add_argument("--by", type=int, default=None,
                    help="positional advance (automatic line only)")
    ap.add_argument("--position", type=int, default=None, metavar="N",
                    help="the number the commit at position N owns (absolute rule)")
    ap.add_argument("--set-version", default=None, metavar="X.Y.Z",
                    help="set this exact version (same line only; E-013)")
    ap.add_argument("--check", action="store_true",
                    help="verify manifest agreement only; write nothing")
    ap.add_argument("--print-version", action="store_true",
                    help="print the agreed version alone; write nothing (CI tags with it)")
    args = ap.parse_args(argv)
    root = pathlib.Path(args.root)
    given = [name for name, value in (("--by", args.by),
                                      ("--position", args.position),
                                      ("--set-version", args.set_version))
             if value is not None]
    try:
        if args.print_version:
            # Bare and single-line by contract: the workflow composes `v$out`
            # into a tag name, so anything but the number is a bug (E-011).
            print(read_version(root))
            return 0
        if args.check:
            print(f"consistent ({read_version(root)})")
            return 0
        if len(given) > 1:
            raise BumpError("pass at most one of " + ", ".join(given))
        old, new = bump(root, by=args.by, target=args.set_version,
                        position=args.position)
    except BumpError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if read_version(root) != new:  # self-check: the tree must end agreeing
        print(f"ERROR: post-bump verification failed (expected {new})",
              file=sys.stderr)
        return 1
    print(f"bumped {old} -> {new}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

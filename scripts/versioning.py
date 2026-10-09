#!/usr/bin/env python3
"""The release-number rule, in one place (E-013).

Two kinds of line, and the difference is who owns the number:

**Automatic (major 0).** The number is the commit's position — the next block
of releases opens every `AUTO_BLOCK` commits:

    version(position) = 0.<1 + position // 100>.<position % 100>
    v0.1.0 … v0.1.99  (positions 0…99)
    v0.2.0  ← position 100        (86 commits after v0.1.14)
    v0.2.1 … v0.2.99  (positions 101…199)
    v0.3.0  ← position 200

Nothing decides this: it is arithmetic, so a release's number is checkable
from the commit alone (`auto_position` is its exact inverse), the patch never
grows past two digits, and the 1.0.0-style "when do we move to the next
boundary" question has an answer that needs no meeting: at position 100, 200,
300 … The mapping is per-line as well: within a block the patch counts commits
since that block's opening commit, which is what makes `vX.Y.0` the line's
baseline everywhere.

**Declared (major >= 1, or any hand-chosen number).** `0.x` is the counter's
property, so a hand-picked number must say so out loud: a human writes the
version (e.g. `1.0.0`) into the manifests and opens the line with a `[line
1.0.0]` marker in the commit message; the CI verifies the declaration, tags
that commit and leaves it alone. From then on the patch counts commits since
the line's `v1.0.0` tag (`1.0.1`, `1.0.2`, …). Declaring is the only way to
leave the automatic line, and it must be explicit — 1.0.0 is a claim about
maturity, not a number a counter can be trusted to hand out.

Stdlib only, no git, no network — this module owns the arithmetic, the scripts
that touch repositories import it. Exit codes: 0 ok, 1 refused.
"""
from __future__ import annotations

import argparse
import re
import sys

AUTO_MAJOR = 0
# Releases per automatic block: 0.1.x, then 0.2.x, … The patch stays two
# digits, and one block is one release line (E-013).
AUTO_BLOCK = 100

_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


class VersionError(Exception):
    """A refusal: unparseable, not an automatic number, or out of range."""


def split(version: str) -> tuple[int, int, int]:
    m = _SEMVER_RE.match(version)
    if not m:
        raise VersionError(f"version {version!r} is not plain X.Y.Z")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def is_auto(version: str) -> bool:
    """True on the automatic line — major 0 (the counter owns the number)."""
    major, _, _ = split(version)
    return major == AUTO_MAJOR


def auto_version(position: int) -> str:
    """The number the commit at `position` owns (position >= 0)."""
    if position < 0:
        raise VersionError(f"position must be >= 0, got {position}")
    return f"{AUTO_MAJOR}.{1 + position // AUTO_BLOCK}.{position % AUTO_BLOCK}"


def auto_position(version: str) -> int:
    """The exact inverse of auto_version; refuses anything it cannot produce.

    This is what stops a hand-written future number: `0.1.150` (three digits in
    one block), `0.9.3` (a block the counter has not reached — its position
    would be 803) and `1.0.0` (a declared line, not a counted number) all fail
    here instead of being published.
    """
    major, minor, patch = split(version)
    if major != AUTO_MAJOR:
        raise VersionError(f"{version} is a declared line, not an automatic number")
    if not 1 <= minor:
        raise VersionError(f"{version} has no block: the automatic line starts at 0.1.0")
    if patch >= AUTO_BLOCK:
        position = (minor - 1) * AUTO_BLOCK + patch
        raise VersionError(
            f"{version} is not producible: an automatic line's patch is "
            f"0..{AUTO_BLOCK - 1}, so that position is written "
            f"{auto_version(position)} (position {position})")
    return (minor - 1) * AUTO_BLOCK + patch


def line(version: str) -> tuple[int, int]:
    """The major.minor a version belongs to."""
    major, minor, _ = split(version)
    return major, minor


def opening(version: str) -> str:
    """The `X.Y.0` release that opens this line."""
    major, minor, _ = split(version)
    return f"{major}.{minor}.0"


def is_opening(version: str) -> bool:
    _, _, patch = split(version)
    return patch == 0


def same_line(a: str, b: str) -> bool:
    return line(a) == line(b)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--position", type=int, metavar="N",
                    help="print the version that owns position N (0.1.0 = 0)")
    ap.add_argument("--position-of", metavar="VERSION",
                    help="print the position a version owns (its exact inverse)")
    ap.add_argument("--opening", metavar="VERSION",
                    help="print the X.Y.0 tag that opens this version's line")
    ap.add_argument("--is-auto", metavar="VERSION",
                    help="exit 0 iff the version is on the automatic line")
    ap.add_argument("--check", metavar="VERSION",
                    help="exit 0 iff the version is a number the rule can produce")
    ap.add_argument("--block", action="store_true",
                    help="print the automatic block size")
    args = ap.parse_args(argv)
    try:
        if args.block:
            print(AUTO_BLOCK)
        elif args.position is not None:
            print(auto_version(args.position))
        elif args.position_of:
            print(auto_position(args.position_of))
        elif args.opening:
            print(opening(args.opening))
        elif args.is_auto:
            if not is_auto(args.is_auto):
                return 1
            print("auto")
        elif args.check:
            major, minor, patch = split(args.check)
            auto_position(args.check)          # refuses an impossible number
            if major != AUTO_MAJOR:
                return 1
            print(auto_version((minor - 1) * AUTO_BLOCK + patch))
        else:
            ap.error("nothing to do: pass one of --position/--position-of/"
                     "--opening/--is-auto/--check/--block")
    except VersionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

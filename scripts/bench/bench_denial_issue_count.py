#!/usr/bin/env python3
"""A denial counts the issues its bound withheld (E-195).

Found live on this tree, 2026-11-21, by validating a story with eight
acceptance criteria and no metadata::

    AC-001: missing Type field; AC-001: missing Measured field; AC-001: missing
    Verify field; AC-002: missing Type field; AC-002: missing Measured field

Twenty-four issues, five printed, no sign that nineteen existed. The denial read
as a complete explanation, so the agent fixed five, retried, and met the next
five — the same silent-omission shape E-194 removed from the chain gate, on the
message that blocks a write. `story_quality`'s chain validator used `[:3]` the
same way.

Claim, closed set: both joins go through `utils.issue_summary` — the visible
bound is unchanged, the remainder is counted in the repo's `(+N more)` style
with singular/plural intact, a list that fits prints verbatim, and the denial
still denies.

Control: `--broken` runs the same probes against the pre-change tree
(`PREFIX_REV`), whose joins are `"; ".join(issues[:5])` / `[:3]`.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import tarfile
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
PREFIX_REV = "0953f6b"  # the parent of the commit that ships the counted join
LIMIT = 5

PROBES = (
    "hidden_issues_named",
    "hidden_count_arithmetic",
    "visible_bound_unchanged",
    "short_list_verbatim",
    "singular_remainder",
    "plural_remainder",
    "list_at_limit_unmarked",
    "chain_validator_uses_the_count",
    "denial_still_denies",
    "raw_issue_slices_gone",
)


def _engine(tree: pathlib.Path):
    sys.path.insert(0, str(tree / "hooks" / "engine"))
    from modules.guard import _validate_story_metadata
    try:  # the control tree predates the shared join
        from modules.utils import issue_summary
    except ImportError:
        issue_summary = None
    return _validate_story_metadata, issue_summary


def _many_issue_story(acs: int = 8) -> str:
    rows = "\n".join(f"- [AC-{i:03d}] Given X When Y Then Z"
                     for i in range(1, acs + 1))
    return ("## Story: S-001\n## Technical Tasks\n- [ ] task one → AC-001\n"
            f"## Acceptance Criteria\n{rows}\n")


def _two_issue_story() -> str:
    return ("## Story: S-001\n## Technical Tasks\n- [ ] task one → AC-001\n"
            "## Acceptance Criteria\n- [AC-001] Given X When Y Then Z\n")


def _hidden(reason: str) -> int:
    return int(reason.rsplit("(+", 1)[1].split(" ", 1)[0]) if "(+" in reason else 0


def _probe(name: str, tree: pathlib.Path, base: pathlib.Path) -> tuple[bool, str]:
    validate, issue_summary = _engine(tree)

    if issue_summary is None and name in (
            "singular_remainder", "plural_remainder", "list_at_limit_unmarked"):
        return False, "utils.issue_summary absent in this tree"

    if name == "hidden_issues_named":
        valid, reason = validate(_many_issue_story())
        ok = valid is False and reason.endswith("more issues)")
        return ok, f"tail={reason[-40:]!r}"

    if name == "hidden_count_arithmetic":
        valid, reason = validate(_many_issue_story())
        shown = reason.count(": missing")
        hidden = _hidden(reason)
        # 8 ACs x 3 metadata fields, plus any story-level issue the validator adds
        ok = shown == LIMIT and hidden >= 19 and 24 <= shown + hidden <= 26
        return ok, f"shown={shown} hidden={hidden} total={shown + hidden}"

    if name == "visible_bound_unchanged":
        _, reason = validate(_many_issue_story())
        ok = reason.count(": missing") == LIMIT
        return ok, f"shown={reason.count(': missing')}"

    if name == "short_list_verbatim":
        valid, reason = validate(_two_issue_story())
        ok = valid is False and "more issue" not in reason
        return ok, f"reason={reason!r}"

    if name == "singular_remainder":
        text = issue_summary([f"i{n}" for n in range(6)], LIMIT)
        ok = text.endswith("(+1 more issue)") and "more issues" not in text
        return ok, f"text={text[-30:]!r}"

    if name == "plural_remainder":
        text = issue_summary([f"i{n}" for n in range(9)], 3)
        ok = text.endswith("(+6 more issues)")
        return ok, f"text={text[-30:]!r}"

    if name == "list_at_limit_unmarked":
        text = issue_summary([f"i{n}" for n in range(LIMIT)], LIMIT)
        ok = "more issue" not in text and "…" not in text
        return ok, f"text={text!r}"

    if name == "chain_validator_uses_the_count":
        source = (tree / "hooks" / "engine" / "modules" / "story_quality.py"
                  ).read_text(encoding="utf-8")
        ok = "issue_summary(issues, 3)" in source
        return ok, "story_quality joins through issue_summary(issues, 3)"

    if name == "denial_still_denies":
        valid, _ = validate(_many_issue_story())
        return valid is False, "valid=False"

    if name == "raw_issue_slices_gone":
        guard_src = (tree / "hooks" / "engine" / "modules" / "guard.py"
                     ).read_text(encoding="utf-8")
        quality_src = (tree / "hooks" / "engine" / "modules" / "story_quality.py"
                       ).read_text(encoding="utf-8")
        ok = ("join(issues[:5])" not in guard_src
              and "join(issues[:3])" not in quality_src)
        return ok, "no raw issue slice remains"

    raise SystemExit(f"unknown probe: {name}")


def driver(name: str, tree: pathlib.Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ok, detail = _probe(name, tree, pathlib.Path(tmp))
    print(json.dumps({"ok": ok, "detail": detail}, ensure_ascii=False))


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
             "--tree", str(tree)], capture_output=True, text=True, cwd=str(tree),
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
    print(f"denial_issue_count_accuracy={value:.2f} ({passed}/{total})")
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

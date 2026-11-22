#!/usr/bin/env python3
"""Denial details quote the offending line on a boundary, never mid-token (E-193).

Found live on this tree, 2026-11-21, by asking guard to validate a story::

    Task without AC reference: - [ ] wire the release path docs/planning/implementation-rea...

The quote was `task_text[:60] + "..."`, so the denial cut the path mid-word and
dropped whatever followed — the AC id, or a gate token, that the reader needs to
act. The `...` also marked text that sometimes was not shortened at all: a
44-char task printed with a trailing "..." that made a complete quote look
truncated. `utils.dod_issues` carried the same cut for its two DoD messages, and
guard surfaces both in the same denial.

Claim, closed set: every quote those four messages print comes from the shared
cutter — boundary cut, unclosed bracket group dropped whole, elision marked,
token whole or absent, short quote verbatim — and the denial still denies.

Control: `--broken` runs the same probes against the pre-change tree
(`PREFIX_REV`), whose quotes are `text[:60] + "..."`.
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
PREFIX_REV = "0953f6b"  # the parent of the commit that ships the shared cutter here
TOKEN = "GATE-OK-E-192-02810bde1da438c02f9d1bafa129ba50"
BOUNDARY = " \t\n—–-:;,/|("

PROBES = (
    "task_quote_not_cut_mid_word",
    "task_quote_elision_marked",
    "task_quote_cut_on_a_boundary",
    "task_quote_token_whole_or_absent",
    "short_quote_verbatim",
    "dod_quote_bounded_the_same_way",
    "dod_token_whole_or_absent",
    "quote_within_bound",
    "shared_cutter_equality",
    "denial_still_denies",
)

LONG_TASK = ("- [ ] wire the release path "
             "docs/planning/implementation-readiness-report.md "
             f"({TOKEN}) into the gate")
SHORT_TASK = "- [ ] second task with E-900 ref → AC-901"


def _story(long_task: str = LONG_TASK, short_task: str = SHORT_TASK) -> str:
    return ("## Story: S-001\n## Technical Tasks\n"
            f"{long_task}\n{short_task}\n## Acceptance Criteria\n"
            "- [AC-001] Given X When Y Then Z\n")


def _engine(tree: pathlib.Path):
    sys.path.insert(0, str(tree / "hooks" / "engine"))
    from modules.guard import _validate_story_metadata
    from modules.utils import dod_issues
    return _validate_story_metadata, dod_issues


def _quote(reason: str) -> str:
    """The first quoted task line in a denial reason."""
    tail = reason.split("Task without AC reference: ", 1)[-1]
    return tail.split("; ", 1)[0]


def _probe(name: str, tree: pathlib.Path, base: pathlib.Path) -> tuple[bool, str]:
    validate, dod_issues = _engine(tree)

    if name in ("task_quote_not_cut_mid_word", "task_quote_elision_marked",
                "task_quote_cut_on_a_boundary", "task_quote_token_whole_or_absent",
                "quote_within_bound", "shared_cutter_equality"):
        valid, reason = validate(_story())
        quote = _quote(reason)

    if name == "task_quote_not_cut_mid_word":
        ok = valid is False and "implementation-rea" not in reason
        return ok, f"quote={quote!r}"

    if name == "task_quote_elision_marked":
        ok = quote.endswith("…")
        return ok, f"quote={quote!r}"

    if name == "task_quote_cut_on_a_boundary":
        body = quote[:-1]
        ok = (LONG_TASK.startswith(body) and len(body) < len(LONG_TASK)
              and LONG_TASK[len(body)] in BOUNDARY)
        return ok, f"body={body!r}"

    if name == "task_quote_token_whole_or_absent":
        ok = TOKEN[:20] not in quote and TOKEN not in quote
        return ok, f"quote={quote!r}"

    if name == "short_quote_verbatim":
        _, reason = validate(_story(short_task=SHORT_TASK))
        # A quote that fits must carry NO marker of any kind — the old code
        # appended "..." to a 44-char task, making a complete quote look cut.
        ok = (SHORT_TASK in reason and f"{SHORT_TASK}..." not in reason
              and f"{SHORT_TASK}…" not in reason)
        return ok, f"reason={reason[-60:]!r}"

    if name == "dod_quote_bounded_the_same_way":
        issues = dod_issues("- [ ] ship the release bundle docs/planning/"
                            "implementation-readiness-report.md without identifier\n")
        ok = bool(issues) and all(i.endswith("…") for i in issues) \
            and all("implementation-rea" not in i for i in issues) \
            and all("bundle…" in i for i in issues)
        return ok, f"issues={[i[-45:] for i in issues]}"

    if name == "dod_token_whole_or_absent":
        issues = dod_issues("- [ ] ship the bundle " + "w " * 40 + f"({TOKEN})\n")
        ok = all(TOKEN[:20] not in i for i in issues)
        return ok, f"issues={[i[-40:] for i in issues]}"

    if name == "quote_within_bound":
        ok = len(quote) <= 61
        return ok, f"len={len(quote)}"

    if name == "shared_cutter_equality":
        from modules.utils import brief_text
        ok = quote == brief_text(LONG_TASK, 60)
        return ok, "quote == brief_text(task, 60)"

    if name == "denial_still_denies":
        valid, reason = validate(_story())
        ok = valid is False and "Task without AC reference" in reason
        return ok, f"reason={reason[:40]!r}"

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
    print(f"denial_quote_bound_accuracy={value:.2f} ({passed}/{total})")
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

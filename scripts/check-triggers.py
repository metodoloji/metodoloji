#!/usr/bin/env python3
"""Trigger/description collision audit (F4.1) — LLM-free routing determinism.

Two skills sharing an identical description or a bare natural-language
trigger cannot be routed deterministically: the harness must guess the
vertical (bmad vs gds). Slash triggers ("/x") and skill-name triggers
are explicit user choices and exempt; every other shared trigger string
and every byte-identical description is a collision.

Rule (locked by F4): descriptions carry a vertical qualifier
"(BMad Method)" / "(Game Dev Studio)" / ..., and bare generic triggers
belong to at most one skill (bmad keeps the unqualified default; other
verticals qualify theirs, e.g. "game run code review").

Usage: python3 scripts/check-triggers.py [--project-root .]
Exit 0 when clean, 1 with collision list otherwise. Stdlib only.
"""

from __future__ import annotations

import argparse
import collections
import glob
import os
import re
import sys


def _frontmatter(path: str) -> tuple[str, list[str], str]:
    txt = open(path, encoding="utf-8").read()
    m = re.search(r"^---(.*?)---", txt, re.S)
    if not m:
        return "", [], os.path.basename(os.path.dirname(path))
    fm = m.group(1)
    d = re.search(r"description:\s*(.+)", fm)
    desc = d.group(1).strip() if d else ""
    triggers: list[str] = []
    t = re.search(r"triggers:\s*\[(.*?)\]", fm, re.S)
    if t:
        triggers = re.findall(r'"([^"]+)"', t.group(1))
        triggers += re.findall(r"'([^']+)'", t.group(1))
    return desc, triggers, os.path.basename(os.path.dirname(path))


def main() -> int:
    ap = argparse.ArgumentParser(description="trigger collision audit")
    ap.add_argument("--project-root", default=".")
    args = ap.parse_args()
    descs: dict[str, list[str]] = collections.defaultdict(list)
    trigs: dict[str, list[str]] = collections.defaultdict(list)
    n = 0
    unreadable: list[str] = []
    for f in glob.glob(os.path.join(args.project_root, "skills", "*", "SKILL.md")):
        try:
            desc, triggers, name = _frontmatter(f)
        except (OSError, UnicodeDecodeError):
            # A skill file that is not valid UTF-8 cannot be audited: report it
            # instead of crashing the whole audit (E-017 — the E-009 decode
            # seam on the check scripts).
            unreadable.append(os.path.relpath(f, args.project_root))
            continue
        n += 1
        if desc:
            descs[desc].append(name)
        for q in triggers:
            trigs[q].append(name)
    problems = []
    for d, names in sorted(descs.items()):
        if len(names) > 1:
            problems.append(f"identical description shared by {names}: {d[:90]!r}")
    for q, names in sorted(trigs.items()):
        if len(names) < 2 or q.startswith("/"):
            continue
        if all(q in (s, f"/{s}") or q == s.replace("-", " ") for s in names):
            continue  # skill-name-derived aliases are explicit, not generic
        problems.append(f"shared generic trigger {q!r} in {names}")
    print(f"checked skills: {n}")
    for f in unreadable:
        print(f"  UNREADABLE: {f} is not valid UTF-8 (cannot audit)")
    if problems:
        print(f"collisions: {len(problems)}")
        for p in problems:
            print(f"  COLLISION: {p}")
    if problems or unreadable:
        return 1
    print("no trigger/description collisions")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Guard the embedded prompt-quality-canon copy.

The canon ships in workflow-builder's references, with no in-file sync note,
because the loaded file is LLM-facing and a maintenance comment there is paid
on every load. It used to be embedded in three places (workflow-builder plus
two agent-builder copies) and this test kept them byte-identical; the persona
group is gone, so the remaining invariant is that the single shipped copy
exists and is not empty.
Run with: python3 -m pytest test_canon_sync.py
(or plain `python3 test_canon_sync.py` for a lightweight self-check).
"""
import sys
from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parents[3]

CANON_COPIES = [
    SKILLS_DIR / "bmad-workflow-builder" / "references" / "prompt-quality-canon.md",
]


def test_all_copies_exist():
    missing = [str(p) for p in CANON_COPIES if not p.is_file()]
    assert not missing, f"canon copy missing: {missing}"


def test_all_copies_identical():
    """With one shipped copy this pins that it is non-empty, not drift."""
    contents = {p: p.read_bytes() for p in CANON_COPIES if p.is_file()}
    reference = CANON_COPIES[0]
    assert contents, f"canon copy missing: {reference}"
    assert contents[reference].strip(), f"canon copy is empty: {reference}"
    diverged = [str(p) for p, body in contents.items() if body != contents[reference]]
    assert not diverged, (
        "canon copies have drifted from "
        f"{reference}: {diverged} — sync all copies together"
    )


if __name__ == "__main__":
    test_all_copies_exist()
    test_all_copies_identical()
    print(f"ok: {len(CANON_COPIES)} canon copies present and identical")

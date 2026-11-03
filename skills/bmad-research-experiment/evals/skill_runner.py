#!/usr/bin/env python3
"""Run one case through the candidate SKILL.md and print a JSON verdict.

This is the `CommandRunner` end of the optimization kernel
(`optimization/rollout.py`): the loop writes the candidate artifact, sets
``OPTIMIZE_ARTIFACT`` / ``OPTIMIZE_CASE_ID`` / ``OPTIMIZE_CASE_INPUT``, and
reads one JSON object from stdout: ``{"ok": bool, "output": str}``.

The judge is deterministic and lives here, not in the model: an answer passes
when every check in the case's ``checks`` list holds — at least one ``must_any``
token present (case-insensitive) and no ``must_not_any`` token present. That
keeps the measurement about the *skill document* rather than about a second
model's opinion, and it is the same rule `grade_local.py` uses on the skill's
eval runs.

Endpoint (nothing is read from the spec, so the key never lands in a file):

    SKILL_EVAL_BASE_URL   e.g. https://api.reflection.ai/openai/v1
    SKILL_EVAL_API_KEY    bearer token
    SKILL_EVAL_MODEL      e.g. Beam-1.0-501B-A23B
    SKILL_EVAL_MAX_TOKENS optional, default 8192 — a reasoning-first model spends
                          completion budget on its reasoning first, and a reply
                          that is all reasoning has an empty `content`

Usage (the kernel fills the placeholders):

    skill_runner.py --artifact {artifact} --case {case_id}
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
CASES = HERE / "training-cases.jsonl"

SYSTEM_HEAD = (
    "You are the bmad-research-experiment skill. The SKILL.md below is your "
    "full instruction set. There is no shell and no file tool: answer with the "
    "deliverable itself, as text. Do not narrate setup steps.\n\n"
)


def norm(text: str) -> str:
    return " ".join(text.lower().split())


def grade_answer(text: str, checks: list[dict]) -> tuple[bool, list[str]]:
    """All checks must hold; returns (ok, notes about what failed)."""
    body = norm(text)
    notes: list[str] = []
    for index, check in enumerate(checks or []):
        must_any = [norm(token) for token in check.get("must_any", [])]
        must_not = [norm(token) for token in check.get("must_not_any", [])]
        if must_any and not any(token in body for token in must_any):
            notes.append(f"check {index}: missing any of {check['must_any']}")
        hit = next((token for token in must_not if token in body), None)
        if hit is not None:
            notes.append(f"check {index}: forbidden {hit!r}")
    return (not notes), notes


def load_cases(path: pathlib.Path = CASES) -> dict[str, dict]:
    cases: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            cases[str(row["id"])] = row
    return cases


def call_model(system: str, user: str) -> str:
    base = os.environ.get("SKILL_EVAL_BASE_URL", "").rstrip("/")
    key = os.environ.get("SKILL_EVAL_API_KEY", "")
    model = os.environ.get("SKILL_EVAL_MODEL", "")
    if not base or not model:
        raise RuntimeError("SKILL_EVAL_BASE_URL and SKILL_EVAL_MODEL must be set")
    body = json.dumps({
        "model": model,
        "stream": False,
        "temperature": 0.2,
        "max_tokens": int(os.environ.get("SKILL_EVAL_MAX_TOKENS", "8192")),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }).encode("utf-8")
    request = urllib.request.Request(
        base + "/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # surface the provider's reason
        raise RuntimeError(f"{exc.code}: {exc.read()[:300]!r}") from exc
    try:
        return payload["choices"][0]["message"].get("content") or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"unexpected response shape: {exc}") from exc


REPLY_ONLY = "\n\nRespond only with the text you would send the user."


def answer(system: str, user: str) -> str:
    """An empty reply is an infrastructure outcome, never a skill verdict.

    Two ways a reasoning-first model returns ``content: None`` while the call
    itself succeeds: it was cut off mid-thought (``finish_reason: "length"``),
    or it finished thinking and stopped without a user-visible reply
    (``finish_reason: "stop"``). Grading either as a failure would tell the
    optimizer the candidate got worse because of the provider, so retry once
    with more room, then once asking for the user-visible text only, and only
    then raise — the kernel records a runner error instead of a false
    rejection. Both recoveries were observed on the live endpoint: the second
    one is what turns an empty reply into a 1.9k-character answer.
    """
    text = call_model(system, user).strip()
    if text:
        return text
    budget = int(os.environ.get("SKILL_EVAL_MAX_TOKENS", "8192"))
    os.environ["SKILL_EVAL_MAX_TOKENS"] = str(budget * 2)
    text = call_model(system, user + REPLY_ONLY).strip()
    if text:
        return text
    raise RuntimeError("empty completion twice (no user-visible reply)")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--case", required=True)
    args = parser.parse_args()

    artifact = pathlib.Path(args.artifact).read_text(encoding="utf-8")
    case = load_cases().get(args.case)
    if case is None:
        print(f"unknown case {args.case}", file=sys.stderr)
        return 2
    prompt = os.environ.get("OPTIMIZE_CASE_INPUT") or case["input"]
    try:
        text = answer(SYSTEM_HEAD + artifact, prompt)
    except (RuntimeError, OSError) as exc:
        # A provider failure is not a skill failure: the kernel must not read
        # this as "the candidate got worse".
        print(f"runner: {exc}", file=sys.stderr)
        return 3
    ok, notes = grade_answer(text, case.get("checks", []))
    print(json.dumps({"ok": ok, "output": (text[:1200] + (" | " + "; ".join(notes)
                                                         if notes else ""))}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

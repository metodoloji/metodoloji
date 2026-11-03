#!/usr/bin/env python3
"""Propose one bounded edit to SKILL.md: JSON request in, JSON edit out.

This is the `CommandProposer` end of the optimization kernel
(`optimization/proposers.py`). The kernel hands over a request on stdin:

    {"epoch": 2, "artifact": "...", "failing_cases": [...],
     "rejected_directions": "..."}

and expects exactly one edit on stdout, or ``null``:

    {"op": "add" | "replace" | "delete", "anchor": "...", "content": "..."}

Why the shape is this narrow: the kernel enforces the budget on the *output*
(``max_edit_chars``, ``textual_lr``, ``max_hunks``), so an unbounded rewrite is
refused regardless of what the model intended. The model is therefore asked for
the smallest edit that would have taught the failing case, and the anchor must
be a unique literal substring — an ambiguous anchor is refused by the kernel
before a validation run is spent.

Endpoint configuration is the same as `skill_runner.py`
(``SKILL_EVAL_BASE_URL`` / ``SKILL_EVAL_API_KEY`` / ``SKILL_EVAL_MODEL``).
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request

INSTRUCTIONS = """\
You maintain one instruction document (a SKILL.md) for a scientific-methodology
workflow. A batch of cases recently failed against the current document.

Return ONE edit as JSON and nothing else — no prose, no markdown fence:

  {"op": "add", "anchor": "<exact unique substring of the document>", "content": "<text inserted after the anchor>"}
  {"op": "replace", "anchor": "<exact unique substring>", "content": "<replacement text>"}
  {"op": "delete", "anchor": "<exact unique substring>"}

Rules, enforced mechanically before your edit is measured:
- The anchor MUST be copied character-for-character from the "Anchor candidates"
  list in the request. A paraphrased or reconstructed anchor is refused before
  the edit is applied (`anchor occurs 0 times`), so the step is wasted.
- The edit may change at most 900 characters and at most 3 regions.
- Teach the failing behaviour in the smallest way; do not restructure the
  document, do not delete existing rules, do not invent new sections when a
  neighbouring one already fits.
- A direction listed as already rejected must not be repeated.
- If no edit would fix the failures (e.g. the case is broken, or the document
  already says it), return null.
"""


def call_model(system: str, user: str) -> str:
    base = os.environ.get("SKILL_EVAL_BASE_URL", "").rstrip("/")
    key = os.environ.get("SKILL_EVAL_API_KEY", "")
    model = os.environ.get("SKILL_EVAL_MODEL", "")
    if not base or not model:
        raise RuntimeError("SKILL_EVAL_BASE_URL and SKILL_EVAL_MODEL must be set")
    body = json.dumps({
        "model": model,
        "stream": False,
        "temperature": 0.0,
        "max_tokens": int(os.environ.get("SKILL_EVAL_PROPOSER_TOKENS", "4096")),
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
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{exc.code}: {exc.read()[:300]!r}") from exc
    try:
        return payload["choices"][0]["message"].get("content") or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"unexpected response shape: {exc}") from exc


def extract_edit(text: str) -> dict | None:
    """Pull one JSON object out of the reply; a fenced block is tolerated."""
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\}|null)\s*```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1)
    if cleaned.lower().startswith("null"):
        return None
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        payload = json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    op = str(payload.get("op", "")).strip()
    if op not in {"add", "replace", "delete"}:
        return None
    return {"op": op, "anchor": str(payload.get("anchor", "")),
            "content": str(payload.get("content", ""))}


def main() -> int:
    request = json.loads(sys.stdin.read() or "{}")
    failing = request.get("failing_cases") or []
    if not failing:
        print("null")
        return 0
    artifact = str(request.get("artifact", ""))
    anchors = [line for line in artifact.splitlines() if line.startswith("#")]
    user = (
        f"Epoch {request.get('epoch')}.\n\n"
        "Failing cases:\n"
        + "\n".join(
            f"- id {case['id']}: {case['input']}\n"
            f"  required tokens: {case.get('requires')}\n"
            f"  hint: {case.get('hint')}"
            for case in failing
        )
        + "\n\nAnchor candidates (copy one exactly; `add` inserts after it):\n"
        + "\n".join(f"  {line}" for line in anchors)
        + f"\n\n{request.get('rejected_directions', '')}\n\n"
        "Document:\n<<<\n" + artifact + "\n>>>\n"
    )
    try:
        reply = call_model(INSTRUCTIONS, user)
    except (RuntimeError, OSError) as exc:
        print(f"proposer: {exc}", file=sys.stderr)
        return 3
    print(json.dumps(extract_edit(reply)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

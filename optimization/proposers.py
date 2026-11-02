"""Proposers: where the edit comes from.

The kernel does not care whether an edit was written by a rule, a strong model,
or a human. Hermit calls this an ``Evolver``; SkillOpt calls it the optimizer
model that turns scored rollouts into bounded add/delete/replace edits. A
proposer here sees only:

* the current artifact,
* the cases that just failed,
* the rejected-edit buffer (closed directions),
* the epoch.

That is deliberately less than a full transcript. A proposer that cannot see the
validation split cannot overfit to it, and a proposer that cannot write
arbitrarily cannot bypass the budget — the budget is enforced on its output,
not on its intentions.

``RuleProposer`` is deterministic and ships as the default so a run is
reproducible offline. ``CommandProposer`` is the seam for a real model: it
spawns a program, hands it one JSON request on stdin, and expects one JSON edit
(or ``null``) on stdout. Keeping the model behind a process boundary means the
loop has no provider dependency and no API key.
"""
from __future__ import annotations

import json
import shlex
import subprocess
from typing import Sequence

from .cases import Case
from .edits import Edit, RejectedBuffer


class ProposerError(RuntimeError):
    """The proposer failed; the epoch is skipped, not silently accepted."""


class RuleProposer:
    """Turn the first uncovered requirement into one bounded rule bullet."""

    name = "rule"

    def __init__(self, heading: str = "## Learned Rules") -> None:
        self.heading = heading

    def propose(self, artifact_text: str, failing: Sequence[Case],
                buffer: RejectedBuffer, epoch: int) -> Edit | None:
        for case in failing:
            missing = [token for token in case.requires if token not in artifact_text]
            if not missing:
                continue
            subject = case.hint or case.id
            bullet = (
                f"- When the task concerns {subject}, the answer must contain: "
                + ", ".join(f"`{token}`" for token in missing[:4]) + ".\n"
            )
            if self.heading in artifact_text:
                edit = Edit(op="add", anchor=self.heading, content=bullet)
            else:
                edit = Edit(op="add", content=f"\n{self.heading}\n\n{bullet}")
            if buffer.blocks(edit):
                continue
            return edit
        return None


class CommandProposer:
    """Run an external optimizer program: JSON in, one edit or null out."""

    name = "command"

    def __init__(self, template: str, timeout: int = 300) -> None:
        self.argv = shlex.split(template)
        if not self.argv:
            raise ProposerError("empty proposer command")
        self.timeout = timeout

    def propose(self, artifact_text: str, failing: Sequence[Case],
                buffer: RejectedBuffer, epoch: int) -> Edit | None:
        request = {
            "epoch": epoch,
            "artifact": artifact_text,
            "failing_cases": [
                {"id": case.id, "input": case.input,
                 "requires": list(case.requires), "hint": case.hint}
                for case in failing
            ],
            "rejected_directions": buffer.prompt(),
        }
        try:
            proc = subprocess.run(
                self.argv, input=json.dumps(request), capture_output=True,
                text=True, timeout=self.timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ProposerError(f"proposer failed: {exc}") from exc
        if proc.returncode != 0:
            raise ProposerError(
                f"proposer exited {proc.returncode}: {(proc.stderr or proc.stdout)[:400]}"
            )
        try:
            payload = json.loads(proc.stdout.strip() or "null")
        except json.JSONDecodeError as exc:
            raise ProposerError(f"proposer output is not JSON: {exc}") from exc
        if payload is None:
            return None
        if not isinstance(payload, dict):
            raise ProposerError("proposer output must be an edit object or null")
        return Edit(
            op=str(payload.get("op", "")),
            anchor=str(payload.get("anchor", "")),
            content=str(payload.get("content", "")),
        )


class ManualProposer:
    """Queue of edits supplied by a human, in order; empty queue ends the run."""

    name = "manual"

    def __init__(self, edits: Sequence[Edit]) -> None:
        self._queue = list(edits)

    def propose(self, artifact_text: str, failing: Sequence[Case],
                buffer: RejectedBuffer, epoch: int) -> Edit | None:
        while self._queue:
            edit = self._queue.pop(0)
            if not buffer.blocks(edit):
                return edit
        return None

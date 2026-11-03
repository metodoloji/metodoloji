"""Run records in Hermit's documented shape.

Hermit's run-record guide is precise about where evidence goes, and the point is
that a reader can answer four questions without trusting a summary:

    steps.jsonl                     one line per completed step, with rollout
                                    scores, learner status and commit reference
    learning_material/step_NNNN/    exactly what the learner was given
    step_NNNN/output/report.md      what the method did, and why it failed
    summary.json                    final selection, stop reason, checkpoints
    checkout/ADAPTER.md             the current or finalized adapter

This module writes the same artefacts for a metodoloji run, so a run can be
audited with the same commands the Hermit guide teaches. It deliberately does
not fabricate the parts this kernel does not have (a git tree per step, learner
sessions); ``checkout/`` is the accepted version and ``steps.jsonl`` carries the
gate decision instead of a commit hash.

Everything is written atomically except append-only JSONL, which is opened in
append mode: a crash may lose the last line of a log, never a committed state
file.
"""
from __future__ import annotations

import dataclasses
import json
import pathlib
from typing import Iterable, Sequence

from ._io import atomic_write_text, write_json
from .cases import Case
from .rollout import Rollout


@dataclasses.dataclass
class RunRecords:
    work_dir: pathlib.Path

    @property
    def steps_path(self) -> pathlib.Path:
        return self.work_dir / "steps.jsonl"

    @property
    def summary_path(self) -> pathlib.Path:
        return self.work_dir / "summary.json"

    @property
    def report_path(self) -> pathlib.Path:
        return self.work_dir / "output" / "report.md"

    @property
    def checkout_dir(self) -> pathlib.Path:
        return self.work_dir / "checkout"

    def step(self, row: dict[str, object]) -> None:
        self.work_dir.mkdir(parents=True, exist_ok=True)
        with self.steps_path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")

    def learning_material(self, epoch: int, batch: Sequence[Case],
                          rollouts: Sequence[Rollout],
                          note: str = "") -> pathlib.Path:
        """One directory per step: the tasks as the learner saw them, plus feedback.

        Task files carry only id and prompt — the same rule Hermit's judge
        example follows, because task-row fields become learner-visible
        material and a private label there would leak the answer.
        """
        directory = self.work_dir / "learning_material" / f"step_{epoch:04d}"
        tasks_dir = directory / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        by_id = {rollout.case_id: rollout for rollout in rollouts}
        feedback = []
        for case in batch:
            write_json(tasks_dir / f"{case.id}.json",
                       {"id": case.id, "prompt": case.input})
            rollout = by_id.get(case.id)
            feedback.append({
                "id": case.id,
                "ok": bool(rollout.ok) if rollout else None,
                "feedback": (rollout.output if rollout else "not run"),
            })
        write_json(directory / "feedback.json",
                   {"note": note, "items": feedback})
        return directory

    def checkout(self, artifact_text: str) -> pathlib.Path:
        atomic_write_text(self.checkout_dir / "ADAPTER.md", artifact_text)
        return self.checkout_dir / "ADAPTER.md"

    def summary(self, rows: Sequence[dict[str, object]], *, stop_reason: str,
                current_version: str, final_version: str | None = None,
                epochs_done: int = 0) -> pathlib.Path:
        write_json(self.summary_path, {
            "stop_reason": stop_reason,
            "epochs_done": epochs_done,
            "current_version": current_version,
            "final": final_version or current_version,
            "steps": len(rows),
            "accepted": sum(1 for row in rows if row.get("accepted")),
            "rejected": sum(1 for row in rows if not row.get("accepted")),
        })
        return self.summary_path

    def report(self, *, name: str, rows: Iterable[dict[str, object]],
               current_version: str, val: dict[str, object] | None,
               heldout: dict[str, object] | None,
               prediction_mae: float | None,
               carried_forward: str | None = None) -> pathlib.Path:
        rows = list(rows)
        lines = [
            f"# Run report — {name}",
            "",
            f"- **Current version:** {current_version}",
            f"- **Carried forward:** "
            f"{carried_forward or 'not promoted — artifact unchanged'}",
            f"- **Validation rate:** {(val or {}).get('rate', 'unmeasured')} "
            f"(n={(val or {}).get('n', 0)})",
            f"- **Held-out promotion:** {(heldout or {}).get('accepted', 'not run')}",
            f"- **Prediction MAE:** "
            f"{'not measured' if prediction_mae is None else round(prediction_mae, 4)}",
            "",
            "## Steps",
            "",
            "| step | accepted | delta | version | reason |",
            "|---|---|---|---|---|",
        ]
        for index, row in enumerate(rows, start=1):
            lines.append(
                f"| {index} | {row.get('accepted')} | {row.get('delta')} | "
                f"{row.get('version') or '-'} | {str(row.get('reason'))[:120]} |"
            )
        if not rows:
            lines.append("| - | - | - | - | no steps recorded |")
        lines += [
            "",
            "## How to read this",
            "",
            "A failed or rejected step keeps the previous adapter. `accepted` is the",
            "gate's decision, not the method's claim: a method can report success and",
            "still produce no accepted change. `learning_material/` holds exactly what",
            "the proposer was given; `steps.jsonl` holds one line per step.",
        ]
        path = self.report_path
        atomic_write_text(path, "\n".join(lines) + "\n")
        return path

"""The training loop: one Trainer, two method vocabularies.

This is the piece Hermit calls ``Trainer`` and SkillOpt calls the training
loop (rollout → reflect → aggregate → select → update → evaluate):

    evaluate incumbent  ->  roll out a train batch  ->  propose one bounded edit
      ->  screen with the predictor  ->  evaluate the candidate on val
      ->  gate  ->  commit a version, or remember the rejection
      ->  checkpoint  ->  next epoch  ->  held-out promotion check

Design commitments, each of which the tests pin:

* **The incumbent is the baseline.** Every epoch re-measures the current
  artifact on the same validation cases. Comparing a candidate against the
  project's *original* skill would let a run accept a candidate that is worse
  than the version it replaces.
* **One edit per step.** A step is the smallest unit that can be accepted or
  rejected with attribution; multi-edit steps make the gate's verdict
  unattributable.
* **Versions are immutable and chained.** An accepted candidate becomes
  ``vNNNN`` with its artifact, metadata and the sha256 of the previous
  manifest line. Nothing rewrites an old version; `verify()` recomputes the
  chain, which is what makes "roll back" a check rather than a promise.
* **Checkpoint after every epoch, not every step.** A checkpoint is the
  boundary at which the state agrees with the version tree. A crash mid-epoch
  replays that epoch deterministically (the batch is a function of seed and
  epoch) and `commit` is idempotent per (epoch, artifact), so replay cannot
  fork the history.
* **Held-out is promotion, not training.** The loop never reads the held-out
  split; `evaluate_heldout` compares the first and last versions on it and
  records the decision, accepted or not.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import pathlib
from typing import Sequence

from ._io import atomic_write_text, read_json, sha256_text, write_json, write_jsonl
from .cases import Case, SplitSet, assign_splits, leakage, load_cases
from .edits import BudgetExceeded, Edit, EditNotApplicable, RejectedBuffer, apply_edit, cost_of
from .gate import GateDecision, decide
from .predictor import GainPredictor, Prediction, worth_measuring
from .records import RunRecords
from .rollout import Rollout, Runner, run_cases, verdicts
from .spec import RunSpec


class ArtifactTooLarge(ValueError):
    """The deployed artifact exceeds its token budget."""


class StateError(RuntimeError):
    """The on-disk state disagrees with the version tree; refuse to continue."""


def estimate_tokens(text: str) -> int:
    """Character/4 estimate — the convention SkillOpt reports artifact sizes in.

    It is an estimate on purpose: the budget exists to stop a skill drifting
    from ~500 to ~20,000 tokens, and a 10% error does not matter at that scale.
    """
    return max(1, len(text) // 4)


def _atomic_json(path: pathlib.Path, value: object) -> None:
    atomic_write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


@dataclasses.dataclass(frozen=True)
class VersionRecord:
    version: str
    parent: str
    epoch: int
    artifact_sha256: str
    edit: dict[str, str] | None
    gate: dict[str, object] | None
    artifact_tokens: int
    prev_manifest_sha256: str

    def as_dict(self) -> dict[str, object]:
        return dataclasses.asdict(self)


class VersionStore:
    """An append-only tree of accepted artifacts, one directory per version."""

    def __init__(self, root: pathlib.Path) -> None:
        self.root = pathlib.Path(root)

    @property
    def manifest_path(self) -> pathlib.Path:
        return self.root / "MANIFEST.jsonl"

    def entries(self) -> list[dict[str, object]]:
        if not self.manifest_path.is_file():
            return []
        rows = []
        for lineno, line in enumerate(
            self.manifest_path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise StateError(f"manifest line {lineno} is not JSON: {exc}") from exc
        return rows

    def latest(self) -> dict[str, object] | None:
        rows = self.entries()
        return rows[-1] if rows else None

    def _previous_digest(self) -> str:
        rows = self.entries()
        if not rows:
            return "genesis"
        return sha256_text(json.dumps(rows[-1], sort_keys=True))

    def artifact(self, version: str) -> str:
        path = self.root / version / "artifact.md"
        if not path.is_file():
            raise StateError(f"version {version} has no artifact")
        return path.read_text(encoding="utf-8")

    def version_dir(self, version: str) -> pathlib.Path:
        return self.root / version

    def commit(self, artifact_text: str, *, epoch: int, parent: str,
               edit: Edit | None, gate: dict[str, object] | None,
               tokens: int) -> str:
        """Append one version; idempotent for the same (epoch, artifact)."""
        digest = sha256_text(artifact_text)
        rows = self.entries()
        if rows:
            last = rows[-1]
            if (last.get("epoch") == epoch
                    and last.get("artifact_sha256") == digest):
                return str(last["version"])  # replayed epoch, no fork
        version = f"v{len(rows):04d}"
        target = self.root / version
        if target.exists():
            raise StateError(f"{target} already exists — a version is never rewritten")
        atomic_write_text(target / "artifact.md", artifact_text)
        record = VersionRecord(
            version=version, parent=parent, epoch=epoch,
            artifact_sha256=digest,
            edit=(edit.as_dict() if edit is not None else None),
            gate=gate, artifact_tokens=tokens,
            prev_manifest_sha256=self._previous_digest(),
        )
        _atomic_json(target / "meta.json", record.as_dict())
        with self.manifest_path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(record.as_dict(), sort_keys=True) + "\n")
        return version

    def verify(self) -> tuple[bool, str]:
        """Recompute the chain and every artifact digest."""
        rows = self.entries()
        if not rows:
            return False, "manifest is empty"
        previous = "genesis"
        for index, row in enumerate(rows):
            if row.get("prev_manifest_sha256") != previous:
                return False, f"{row.get('version')}: broken chain at row {index + 1}"
            artifact_path = self.root / str(row["version"]) / "artifact.md"
            if not artifact_path.is_file():
                return False, f"{row.get('version')}: artifact missing"
            text = artifact_path.read_text(encoding="utf-8")
            if sha256_text(text) != row.get("artifact_sha256"):
                return False, f"{row.get('version')}: artifact digest mismatch"
            meta_path = self.root / str(row["version"]) / "meta.json"
            if not meta_path.is_file():
                return False, f"{row.get('version')}: meta missing"
            previous = sha256_text(json.dumps(row, sort_keys=True))
        return True, f"{len(rows)} versions, chain intact"


class Proposer:
    name = "proposer"

    def propose(self, artifact_text: str, failing: Sequence[Case],
                buffer: RejectedBuffer, epoch: int) -> Edit | None:
        raise NotImplementedError


@dataclasses.dataclass
class RunReport:
    name: str
    epochs_done: int
    current_version: str
    accepted: int
    rejected: int
    versions: list[dict[str, object]]
    steps: list[dict[str, object]]
    val: dict[str, object]
    heldout: dict[str, object] | None
    prediction_mae: float | None
    metrics_path: str

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name, "epochs_done": self.epochs_done,
            "current_version": self.current_version, "accepted": self.accepted,
            "rejected": self.rejected, "versions": len(self.versions),
            "val": self.val, "heldout": self.heldout,
            "prediction_mae": (None if self.prediction_mae is None
                               else round(self.prediction_mae, 4)),
            "metrics_path": self.metrics_path,
        }


class Optimizer:
    """Train one artifact on one case set, under one gate."""

    def __init__(self, spec: RunSpec, runner: Runner, proposer: Proposer,
                 predictor: GainPredictor | None = None,
                 splits: SplitSet | None = None) -> None:
        self.spec = spec
        self.runner = runner
        self.proposer = proposer
        self.predictor = predictor or GainPredictor()
        self.cases = load_cases(spec.cases_path)
        self.splits = splits or assign_splits(
            self.cases, spec.seed, spec.val_size, spec.heldout_size
        )
        leaked = leakage(self.splits)
        if leaked:
            raise StateError(
                "case leakage between splits (identical prompts): " + ", ".join(leaked[:5])
            )
        self.store = VersionStore(spec.work_dir / "versions")
        self.state_path = spec.work_dir / "state.json"
        self.metrics_path = spec.work_dir / "metrics.jsonl"
        self.report_path = spec.work_dir / "report.json"
        self.records = RunRecords(spec.work_dir)
        self.buffer = RejectedBuffer()
        self._state: dict[str, object] | None = None

    # -- state ---------------------------------------------------------------
    def _blank_state(self) -> dict[str, object]:
        return {
            "epochs_done": 0, "current_version": "v0000", "accepted": 0,
            "rejected": 0, "steps": [], "history": [], "buffer": [],
            "baseline_val": None, "seed": self.spec.seed,
        }

    @property
    def state(self) -> dict[str, object]:
        if self._state is None:
            if self.state_path.is_file():
                self._state = read_json(self.state_path)
                self.buffer = RejectedBuffer(self._state.get("buffer", []))
            else:
                self._state = self._blank_state()
        return self._state

    def _save_state(self) -> None:
        self.state["buffer"] = self.buffer.to_list()
        _atomic_json(self.state_path, self.state)

    # -- setup ---------------------------------------------------------------
    def prepare(self) -> str:
        """Create v0000 from the artifact on disk; validate it against the budget."""
        text = self.spec.artifact_path.read_text(encoding="utf-8")
        tokens = estimate_tokens(text)
        if tokens > self.spec.max_tokens:
            raise ArtifactTooLarge(
                f"{self.spec.artifact_path}: ~{tokens} tokens > max_tokens="
                f"{self.spec.max_tokens}"
            )
        rows = self.store.entries()
        if not rows:
            return self.store.commit(text, epoch=0, parent="none", edit=None,
                                     gate=None, tokens=tokens)
        return str(rows[0]["version"])

    # -- evaluation ----------------------------------------------------------
    def _eval(self, text: str, cases: Sequence[Case]) -> dict[str, bool]:
        return verdicts(run_cases(self.runner, text, cases))

    def _batch(self, epoch: int) -> list[Case]:
        train = list(self.splits.train)
        if len(train) <= self.spec.batch_size:
            return train
        # Deterministic minibatch: a seeded hash per (seed, epoch, case).
        ordered = sorted(
            train,
            key=lambda c: hashlib.sha256(
                f"{self.spec.seed}:{epoch}:{c.id}".encode("utf-8")
            ).hexdigest(),
        )
        return ordered[: self.spec.batch_size]

    # -- the loop ------------------------------------------------------------
    def run(self, epochs: int | None = None) -> RunReport:
        self.prepare()
        total = epochs if epochs is not None else self.spec.epochs
        state = self.state
        done = int(state["epochs_done"])
        if done >= total:
            return self.report()

        baseline_val = state.get("baseline_val")
        for epoch in range(done + 1, total + 1):
            current = str(state["current_version"])
            artifact_text = self.store.artifact(current)
            if not baseline_val:
                baseline_val = self._eval(artifact_text, self.splits.val)

            batch = self._batch(epoch)
            batch_rollouts = run_cases(self.runner, artifact_text, batch)
            failing = [case for case, rollout in zip(batch, batch_rollouts)
                       if not rollout.ok]
            self.records.learning_material(
                epoch, batch, batch_rollouts,
                note=f"{len(failing)} of {len(batch)} tasks failed under the incumbent",
            )
            edit = self.proposer.propose(artifact_text, failing, self.buffer, epoch)
            if edit is None:
                self._record_step(epoch, None, None, False,
                                  "proposer returned no edit", 0.0, None, None)
                state["epochs_done"] = epoch
                baseline_val = baseline_val  # unchanged
                self._save_state()
                continue

            try:
                candidate_text = apply_edit(artifact_text, edit, self.spec.gate)
            except BudgetExceeded as exc:
                self.buffer.add(edit, 0.0, f"budget: {exc}")
                self._record_step(epoch, edit, None, False, f"budget: {exc}",
                                  0.0, None, None)
                state["epochs_done"] = epoch
                self._save_state()
                continue
            except EditNotApplicable as exc:
                self.buffer.add(edit, 0.0, f"inapplicable: {exc}")
                self._record_step(epoch, edit, None, False, f"inapplicable: {exc}",
                                  0.0, None, None)
                state["epochs_done"] = epoch
                self._save_state()
                continue

            cost = cost_of(artifact_text, candidate_text)
            history = [(row["features"], row["delta"]) for row in state["history"]]
            self.predictor.fit(history)
            prior_rate = self._prior_accept_rate()
            prediction = self.predictor.predict_edit(
                edit, artifact_text, cost, failing, self.buffer, prior_rate
            )
            if not worth_measuring(prediction, self.spec.screen_below):
                self._record_step(
                    epoch, edit, prediction, False,
                    f"screened: predicted {prediction.delta:+.3f} "
                    f"(high {prediction.high:+.3f}) < screen_below",
                    0.0, None, cost.as_dict(),
                )
                state["epochs_done"] = epoch
                self._save_state()
                continue

            cand_val = self._eval(candidate_text, self.splits.val)
            decision = decide(baseline_val, cand_val, self.spec.gate,
                              min_cases=self.spec.min_cases)
            tokens = estimate_tokens(candidate_text)
            version: str | None = None
            if tokens > self.spec.max_tokens:
                decision = dataclasses.replace(
                    decision, accepted=False,
                    reason=f"artifact ~{tokens} tokens > max_tokens="
                           f"{self.spec.max_tokens}",
                )
            if decision.accepted:
                version = self.store.commit(
                    candidate_text, epoch=epoch, parent=current, edit=edit,
                    gate=decision.as_dict(), tokens=tokens,
                )
                state["current_version"] = version
                state["accepted"] = int(state["accepted"]) + 1
                baseline_val = cand_val
                self.records.checkout(candidate_text)
            else:
                self.buffer.add(edit, decision.delta, decision.reason)
                state["rejected"] = int(state["rejected"]) + 1

            state["history"].append({"features": prediction.features,
                                     "delta": decision.delta})
            self._record_step(epoch, edit, prediction, decision.accepted,
                              decision.reason, decision.delta, version,
                              cost.as_dict(), decision=decision)
            state["epochs_done"] = epoch
            state["baseline_val"] = baseline_val
            self._save_state()

        return self.report()

    def _record_step(self, epoch: int, edit: Edit | None,
                     prediction: Prediction | None, accepted: bool, reason: str,
                     delta: float, version: str | None,
                     cost: dict | None, decision: GateDecision | None = None) -> None:
        row: dict[str, object] = {
            "epoch": epoch,
            "edit": edit.as_dict() if edit is not None else None,
            "edit_signature": edit.signature() if edit is not None else None,
            "accepted": accepted, "reason": reason, "delta": round(delta, 4),
            "version": version, "cost": cost,
            "prediction": prediction.as_dict() if prediction is not None else None,
        }
        if decision is not None:
            row["gate"] = decision.as_dict()
        self.state["steps"].append(row)
        with self.metrics_path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
        self.records.step(row)

    def _prior_accept_rate(self) -> float:
        steps = [s for s in self.state["steps"] if s.get("delta")]
        if not steps:
            return 0.0
        return sum(1 for s in steps if s.get("accepted")) / len(steps)

    # -- reporting -----------------------------------------------------------
    def evaluate_heldout(self) -> dict[str, object]:
        rows = self.store.entries()
        if not rows:
            raise StateError("no versions to evaluate")
        base_text = self.store.artifact(str(rows[0]["version"]))
        best_text = self.store.artifact(str(rows[-1]["version"]))
        base = self._eval(base_text, self.splits.heldout)
        cand = self._eval(best_text, self.splits.heldout)
        decision = decide(base, cand, self.spec.gate,
                          label="heldout", min_cases=min_cases_for(self.spec))
        return {
            "base_version": rows[0]["version"],
            "candidate_version": rows[-1]["version"],
            "base_rate": round(sum(base.values()) / len(base), 4),
            "candidate_rate": round(sum(cand.values()) / len(cand), 4),
            "n": len(base),
            "accepted": decision.accepted,
            "reason": decision.reason,
        }

    def report(self) -> RunReport:
        state = self.state
        rows = self.store.entries()
        current = str(state["current_version"])
        current_text = self.store.artifact(current) if rows else ""
        val = state.get("baseline_val") or {}
        heldout = None
        if rows and self.spec.gate.require_heldout:
            try:
                heldout = self.evaluate_heldout()
            except Exception as exc:  # a held-out check must not lose the report
                heldout = {"error": str(exc)}
        history = [(row["features"], row["delta"]) for row in state["history"]]
        self.predictor.fit(history)
        report = RunReport(
            name=self.spec.name, epochs_done=int(state["epochs_done"]),
            current_version=current, accepted=int(state["accepted"]),
            rejected=int(state["rejected"]), versions=rows,
            steps=list(state["steps"]), val={
                "rate": round(sum(val.values()) / len(val), 4) if val else None,
                "n": len(val), "tokens": estimate_tokens(current_text),
            }, heldout=heldout,
            prediction_mae=(self.predictor.mae(history) if history else None),
            metrics_path=self.metrics_path.as_posix(),
        )
        write_json(self.report_path, report.as_dict())
        self.records.checkout(current_text)
        self.records.summary(
            report.steps, stop_reason=(
                "completed" if report.epochs_done >= self.spec.epochs else "stopped"
            ), current_version=current, final_version=current,
            epochs_done=report.epochs_done,
        )
        self.records.report(
            name=self.spec.name, rows=report.steps,
            current_version=current, val=report.val, heldout=heldout,
            prediction_mae=report.prediction_mae,
        )
        return report


def min_cases_for(spec: RunSpec) -> int:
    """A held-out split may be smaller than the configured training gate."""
    return max(2, min(spec.min_cases, spec.heldout_size))


def split_files(spec: RunSpec, out_dir: pathlib.Path) -> dict[str, pathlib.Path]:
    """Write the three splits as JSONL, for Hermit/SkillOpt configs."""
    cases = load_cases(spec.cases_path)
    splits = assign_splits(cases, spec.seed, spec.val_size, spec.heldout_size)
    written: dict[str, pathlib.Path] = {}
    for name, group in (("train", splits.train), ("val", splits.val),
                        ("heldout", splits.heldout)):
        path = out_dir / f"{name}.jsonl"
        write_jsonl(path, [
            {"id": case.id, "input": case.input, "requires": list(case.requires),
             "expected": case.expected, "hint": case.hint, **case.meta}
            for case in group
        ])
        written[name] = path
    return written

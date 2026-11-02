"""Predicting whether an edit is worth measuring.

Every rollout costs an agent call; every agent call costs money and time. The
loop can therefore be improved in two independent ways — propose better edits,
or *decline to measure* edits that are predictably worthless. This module does
the second: it forecasts the validation delta of a candidate edit before the
candidate is run, and reports the forecast error afterwards.

The forecaster is deliberately small and inspectable:

* **Features** describe what the edit does (change ratio, size, op, how many
  closed directions it repeats, whether the post-edit artifact actually covers
  cases that currently fail).
* **Model** is ridge regression fitted on the run's own accepted/rejected
  history. With too little history it falls back to a hand-set prior, and says
  so via ``source = "prior"`` rather than pretending to have learned.
* **Interval** is a residual-based band, so ``predict`` states uncertainty
  instead of a single confident number. A forecast outside its own interval
  after the fact is exactly the calibration failure this is meant to expose.

It is not a replacement for the gate: a prediction never accepts an edit. It
only orders and filters work.
"""
from __future__ import annotations

import dataclasses
import math
from typing import Sequence

from .cases import Case
from .edits import BudgetCost, Edit, RejectedBuffer

FEATURES = (
    "changed_ratio", "changed_chars", "added_tokens", "removed_chars",
    "is_add", "is_delete", "buffer_hits", "coverage_gain", "prior_accept_rate",
)


@dataclasses.dataclass(frozen=True)
class Prediction:
    delta: float
    low: float
    high: float
    source: str
    features: dict[str, float]

    def as_dict(self) -> dict[str, object]:
        return {"delta": round(self.delta, 4), "low": round(self.low, 4),
                "high": round(self.high, 4), "source": self.source,
                "features": {k: round(v, 4) for k, v in self.features.items()}}

    def covers(self, realized: float) -> bool:
        return self.low <= realized <= self.high


def _coverage(artifact: str, cases: Sequence[Case], edit: Edit,
              cost: BudgetCost) -> tuple[float, float]:
    """(coverage before, coverage after) over cases the edit could affect.

    Coverage means "the artifact contains every token this case requires" — the
    same contract the scripted runner scores. Using one definition for both the
    forecaster and the runner would be circular if the forecaster *were* the
    gate; it is not, it only orders the queue, and the gate still measures.
    """
    def covered(text: str) -> int:
        hits = 0
        for case in cases:
            if all(token in text for token in case.requires):
                hits += 1
        return hits

    before = covered(artifact)
    # A delete cannot introduce coverage; approximate the post-edit text for
    # adds/replaces without re-running the budget check.
    if edit.op == "add":
        after_text = artifact + edit.content
    elif edit.op == "replace":
        after_text = artifact.replace(edit.anchor, edit.content, 1)
    else:
        after_text = artifact.replace(edit.anchor, "", 1)
    total = max(1, len(cases))
    return before / total, covered(after_text) / total


def features_for(edit: Edit, artifact: str, cost: BudgetCost,
                 failing: Sequence[Case], buffer: RejectedBuffer,
                 prior_accept_rate: float) -> dict[str, float]:
    before, after = _coverage(artifact, failing, edit, cost)
    return {
        "changed_ratio": cost.ratio,
        "changed_chars": min(1.0, cost.changed_chars / 1000.0),
        "added_tokens": min(1.0, len(edit.content.split()) / 100.0),
        "removed_chars": min(1.0, max(0, cost.changed_chars - len(edit.content)) / 1000.0),
        "is_add": 1.0 if edit.op == "add" else 0.0,
        "is_delete": 1.0 if edit.op == "delete" else 0.0,
        "buffer_hits": float(buffer.near_misses(edit)),
        "coverage_gain": max(0.0, after - before),
        "prior_accept_rate": prior_accept_rate,
    }


def _prior(features: dict[str, float]) -> float:
    """Hand-set prior used until the run has its own evidence."""
    return max(-1.0, min(1.0,
        0.55 * features["coverage_gain"]
        + 0.10 * features["prior_accept_rate"]
        - 0.12 * features["buffer_hits"]
        - 0.05 * features["is_delete"]
    ))


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float] | None:
    n = len(vector)
    a = [row[:] + [vector[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            return None
        a[col], a[pivot] = a[pivot], a[col]
        pv = a[col][col]
        for r in range(col + 1, n):
            factor = a[r][col] / pv
            if factor:
                for cc in range(col, n + 1):
                    a[r][cc] -= factor * a[col][cc]
    out = [0.0] * n
    for r in range(n - 1, -1, -1):
        acc = a[r][n] - sum(a[r][cc] * out[cc] for cc in range(r + 1, n))
        out[r] = acc / a[r][r]
    return out


class GainPredictor:
    """Ridge regression over edit features, fitted online from run history."""

    def __init__(self, ridge: float = 1.0, min_rows: int | None = None) -> None:
        self.ridge = ridge
        self.min_rows = min_rows if min_rows is not None else len(FEATURES) + 2
        self.weights: list[float] | None = None
        self.residual_sd: float = 0.18
        self.rows = 0
        self.source = "prior"

    def fit(self, history: Sequence[tuple[dict[str, float], float]]) -> "GainPredictor":
        rows = [(dict(f), float(y)) for f, y in history if isinstance(f, dict)]
        self.rows = len(rows)
        if len(rows) < self.min_rows:
            self.weights = None
            self.source = "prior"
            return self
        n = len(FEATURES)
        means = [sum(row[0].get(name, 0.0) for row in rows) / len(rows)
                 for name in FEATURES]
        targets_mean = sum(y for _, y in rows) / len(rows)
        xtx = [[0.0] * n for _ in range(n)]
        xty = [0.0] * n
        for feats, y in rows:
            x = [feats.get(name, 0.0) - means[i] for i, name in enumerate(FEATURES)]
            for i in range(n):
                xty[i] += x[i] * (y - targets_mean)
                for j in range(n):
                    xtx[i][j] += x[i] * x[j]
        for i in range(n):
            xtx[i][i] += self.ridge
        weights = _solve(xtx, xty)
        if weights is None:
            self.weights = None
            self.source = "prior"
            return self
        self.weights = weights + [0.0]  # intercept is carried separately
        self._means = means
        self._target_mean = targets_mean
        residuals = []
        for feats, y in rows:
            residuals.append(y - self._raw(feats))
        mean_res = sum(residuals) / len(residuals)
        self.residual_sd = math.sqrt(
            sum((r - mean_res) ** 2 for r in residuals) / max(1, len(residuals) - 1)
        )
        self.source = "fitted"
        return self

    def _raw(self, features: dict[str, float]) -> float:
        assert self.weights is not None
        acc = self._target_mean + self.weights[-1]
        for i, name in enumerate(FEATURES):
            acc += self.weights[i] * (features.get(name, 0.0) - self._means[i])
        return acc

    def predict(self, features: dict[str, float]) -> Prediction:
        if self.weights is None:
            delta = _prior(features)
            spread = 0.25
            source = "prior"
        else:
            delta = self._raw(features)
            spread = 1.96 * self.residual_sd
            source = "fitted"
        delta = max(-1.0, min(1.0, delta))
        return Prediction(
            delta=delta,
            low=max(-1.0, delta - spread),
            high=min(1.0, delta + spread),
            source=source,
            features=features,
        )

    def predict_edit(self, edit: Edit, artifact: str, cost: BudgetCost,
                     failing: Sequence[Case], buffer: RejectedBuffer,
                     prior_accept_rate: float = 0.0) -> Prediction:
        return self.predict(features_for(edit, artifact, cost, failing, buffer,
                                         prior_accept_rate))

    def mae(self, history: Sequence[tuple[dict[str, float], float]]) -> float:
        if not history:
            return float("nan")
        errors = []
        for features, realized in history:
            prediction = self.predict(dict(features))
            errors.append(abs(prediction.delta - realized))
        return sum(errors) / len(errors)


def worth_measuring(prediction: Prediction, threshold: float | None) -> bool:
    """Screen out edits whose *optimistic* bound is below the bar."""
    if threshold is None:
        return True
    return prediction.high >= threshold

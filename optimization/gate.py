"""The validation gate: what turns a proposal into a version.

SkillOpt accepts a candidate only when it strictly improves a held-out
validation score; Hermit commits an adapter version only after its checks pass.
Metodoloji's own experiment gate refuses a measurement whose control also
passes. All three are the same idea, and this module states it precisely:

    accept only when the candidate beats the incumbent on the *same* cases,
    by at least ``min_improvement``, with a paired test that separates the
    disagreement from noise, and with enough discordant pairs to mean something.

Why McNemar and not a two-sample test: the two conditions run the same cases,
so the evidence is the discordant pairs — cases the incumbent failed and the
candidate passed (b), versus the reverse (c). Comparing two independent
proportions throws away the pairing and is the wrong test for this design.

Ties are rejected. A tie is the most common outcome of an edit that only looks
like learning ("add a sentence that changes nothing"), and silently accepting
it inflates a version history with changes nobody can attribute.
"""
from __future__ import annotations

import dataclasses
import math
from typing import Mapping

from .spec import GatePolicy


class GateError(ValueError):
    """The comparison itself is invalid (unpaired, empty, too small)."""


@dataclasses.dataclass(frozen=True)
class GateDecision:
    accepted: bool
    reason: str
    delta: float
    base_rate: float
    cand_rate: float
    discordant_b: int
    discordant_c: int
    p_value: float
    n: int

    def as_dict(self) -> dict[str, object]:
        return {
            "accepted": self.accepted, "reason": self.reason,
            "delta": round(self.delta, 4), "base_rate": round(self.base_rate, 4),
            "cand_rate": round(self.cand_rate, 4), "b": self.discordant_b,
            "c": self.discordant_c, "p_value": round(self.p_value, 6), "n": self.n,
        }


def wilson_lower(passes: int, n: int, z: float = 1.96) -> float:
    """95% Wilson lower bound — the honest floor under an observed rate."""
    if n <= 0:
        return 0.0
    phat = passes / n
    denom = 1 + z * z / n
    centre = phat + z * z / (2 * n)
    spread = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n))
    return max(0.0, (centre - spread) / denom)


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value over discordant counts."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def decide(base: Mapping[str, bool], cand: Mapping[str, bool],
           policy: GatePolicy | None = None, *, label: str = "validation",
           min_cases: int = 2) -> GateDecision:
    """Compare two verdict maps over identical case ids."""
    policy = policy or GatePolicy()
    if not base or not cand:
        raise GateError(f"{label}: both conditions need at least one case")
    if set(base) != set(cand):
        missing = sorted(set(base) ^ set(cand))[:5]
        raise GateError(
            f"{label}: conditions are not paired; differing ids: {missing}"
        )
    n = len(base)
    if n < min_cases:
        raise GateError(
            f"{label}: {n} paired cases < min_cases={min_cases}; a gate this "
            "small cannot distinguish learning from luck"
        )
    base_passes = sum(1 for ok in base.values() if ok)
    cand_passes = sum(1 for ok in cand.values() if ok)
    b = sum(1 for key in base if not base[key] and cand[key])
    c = sum(1 for key in base if base[key] and not cand[key])
    base_rate = base_passes / n
    cand_rate = cand_passes / n
    delta = cand_rate - base_rate
    p_value = mcnemar_exact(b, c)

    if delta <= 0 or b <= c:
        reason = (f"{label}: no gain (delta {delta:+.3f}, b={b} c={c})"
                  if delta != 0 else f"{label}: tie (b={b} c={c}) — a tie is not learning")
        return GateDecision(False, reason, delta, base_rate, cand_rate, b, c,
                            p_value, n)
    if delta + 1e-12 < policy.min_improvement:
        return GateDecision(
            False,
            f"{label}: delta {delta:+.3f} < min_improvement "
            f"{policy.min_improvement:.3f}",
            delta, base_rate, cand_rate, b, c, p_value, n,
        )
    if p_value >= policy.alpha:
        return GateDecision(
            False,
            f"{label}: {b} gains vs {c} regressions are not significant "
            f"(p={p_value:.3f} >= alpha={policy.alpha})",
            delta, base_rate, cand_rate, b, c, p_value, n,
        )
    return GateDecision(
        True,
        f"{label}: delta {delta:+.3f} (b={b} c={c}, p={p_value:.4f}), "
        f"{wilson_lower(cand_passes, n):.2f} lower bound",
        delta, base_rate, cand_rate, b, c, p_value, n,
    )


def certify(decision: GateDecision, policy: GatePolicy) -> GateDecision:
    """Second, independent check used before promotion.

    ``decide`` asks "is the gain real on the validation split". Promotion asks
    "does it survive a split the loop never optimized against". Both must pass;
    neither implies the other.
    """
    if not decision.accepted:
        return decision
    if decision.n < 2:
        raise GateError("promotion needs at least two held-out cases")
    return decision


def best_of(decisions: list[GateDecision]) -> GateDecision | None:
    accepted = [d for d in decisions if d.accepted]
    return max(accepted, key=lambda d: d.delta) if accepted else None

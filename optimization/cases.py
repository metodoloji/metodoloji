"""Task cases and their splits.

The single most expensive failure mode in skill optimization is a leak: a case
the optimizer has already seen being used to certify the candidate. SkillOpt
makes the held-out gate the core of its method; Hermit makes the task batch and
the evaluation set separate files. This module makes the separation structural
rather than procedural:

* each case gets exactly one split, assigned by a stable hash of ``seed:id``;
* the split is a function, not a file — two hosts derive the same split;
* :func:`leakage` reports duplicate inputs that straddle splits, because an
  identical prompt in train and val is the same case with two names.

``split`` in an input file is honoured only when it is one of the three known
names; anything else is a typo that would otherwise silently vanish into train.
"""
from __future__ import annotations

import dataclasses
import hashlib
import pathlib
from typing import Any, Iterable

from ._io import read_jsonl

SPLITS = ("train", "val", "heldout")


class CaseError(ValueError):
    """A case file that cannot support a split; refuse it."""


@dataclasses.dataclass(frozen=True)
class Case:
    id: str
    input: str
    requires: tuple[str, ...] = ()
    expected: str | None = None
    hint: str | None = None
    split: str | None = None
    meta: dict[str, Any] = dataclasses.field(default_factory=dict)

    @property
    def signature(self) -> str:
        return hashlib.sha256(self.input.strip().lower().encode("utf-8")).hexdigest()


@dataclasses.dataclass(frozen=True)
class SplitSet:
    train: tuple[Case, ...]
    val: tuple[Case, ...]
    heldout: tuple[Case, ...]

    def all_cases(self) -> tuple[Case, ...]:
        return self.train + self.val + self.heldout

    def by_id(self) -> dict[str, str]:
        return {case.id: name
                for name, group in (("train", self.train), ("val", self.val),
                                    ("heldout", self.heldout))
                for case in group}

    def size(self) -> dict[str, int]:
        return {"train": len(self.train), "val": len(self.val),
                "heldout": len(self.heldout)}


def _requires(value: Any, lineno: int) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise CaseError(f"line {lineno}: 'requires' must be a list of non-empty strings")
    return tuple(value)


def load_cases(path: str | pathlib.Path) -> list[Case]:
    path = pathlib.Path(path)
    rows = read_jsonl(path)
    if not rows:
        raise CaseError(f"no cases in {path}")
    cases: list[Case] = []
    seen: set[str] = set()
    for lineno, row in enumerate(rows, start=1):
        case_id = row.get("id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise CaseError(f"{path}:{lineno}: 'id' must be a non-empty string")
        if case_id in seen:
            raise CaseError(f"{path}:{lineno}: duplicate case id {case_id!r}")
        seen.add(case_id)
        prompt = row.get("input", row.get("prompt"))
        if not isinstance(prompt, str) or not prompt.strip():
            raise CaseError(f"{path}:{lineno}: 'input' must be non-empty text")
        split = row.get("split")
        if split is not None and split not in SPLITS:
            raise CaseError(
                f"{path}:{lineno}: split {split!r} is not one of {SPLITS} — "
                "an unknown split would silently become train"
            )
        cases.append(
            Case(
                id=case_id,
                input=prompt,
                requires=_requires(row.get("requires"), lineno),
                expected=row.get("expected") if isinstance(row.get("expected"), str) else None,
                hint=row.get("hint") if isinstance(row.get("hint"), str) else None,
                split=split,
                meta={k: v for k, v in row.items()
                      if k not in {"id", "input", "prompt", "requires", "expected",
                                   "hint", "split"}},
            )
        )
    return cases


def _rank(seed: int, case_id: str) -> str:
    return hashlib.sha256(f"{seed}:{case_id}".encode("utf-8")).hexdigest()


def assign_splits(cases: Iterable[Case], seed: int, val_size: int,
                  heldout_size: int) -> SplitSet:
    """Deterministic, disjoint assignment; explicit splits win.

    Cases that declare ``split`` keep it. The remainder fill ``heldout`` first,
    then ``val``, then ``train``, ordered by a seeded hash — which keeps the
    split stable when cases are appended at the end of the file.
    """
    cases = list(cases)
    if not cases:
        raise CaseError("cannot split an empty case set")
    fixed = {name: [c for c in cases if c.split == name] for name in SPLITS}
    free = sorted((c for c in cases if c.split is None),
                  key=lambda c: _rank(seed, c.id))
    need_heldout = max(0, heldout_size - len(fixed["heldout"]))
    need_val = max(0, val_size - len(fixed["val"]))
    if len(free) < need_heldout + need_val:
        raise CaseError(
            f"{len(free)} unassigned cases cannot fill {need_heldout} heldout + "
            f"{need_val} val slots; add cases or lower run.val_size/heldout_size"
        )
    heldout = fixed["heldout"] + free[:need_heldout]
    val = fixed["val"] + free[need_heldout:need_heldout + need_val]
    train = fixed["train"] + free[need_heldout + need_val:]
    result = SplitSet(train=tuple(train), val=tuple(val), heldout=tuple(heldout))

    ids = [c.id for c in result.all_cases()]
    if len(ids) != len(set(ids)):
        raise CaseError("a case id appears in more than one split")
    if not result.train or not result.val:
        raise CaseError("both train and val must be non-empty after assignment")
    return result


def leakage(splits: SplitSet) -> list[str]:
    """Ids whose prompt text also occurs in another split.

    A duplicate prompt is the same task under a second name. Measuring a
    candidate on it after training on it is not a held-out measurement.
    """
    owners: dict[str, set[str]] = {}
    labels: dict[str, list[str]] = {}
    for name, group in (("train", splits.train), ("val", splits.val),
                        ("heldout", splits.heldout)):
        for case in group:
            owners.setdefault(case.signature, set()).add(name)
            labels.setdefault(case.signature, []).append(case.id)
    leaked: list[str] = []
    for signature, names in owners.items():
        if len(names) > 1:
            leaked.extend(sorted(labels[signature]))
    return sorted(leaked)


def budget(cases: Iterable[Case]) -> dict[str, int]:
    return {"cases": sum(1 for _ in cases)}

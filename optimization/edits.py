"""Bounded textual edits: the "learning rate" of a frozen-model optimizer.

SkillOpt's ablation is blunt about this: unbounded rewrites destroy a skill.
It bounds each update to add / delete / replace edits under a textual
learning-rate budget, and keeps a buffer of rejected edits so the optimizer
does not steer back into a direction that already lost.

This module implements the same three guarantees, and nothing else:

1. **Bounded op set.** An edit may add text at an anchor, replace a unique
   anchor, or delete a unique anchor. There is no "write the whole document"
   operation, so there is no single update that can discard the artifact.
2. **Two budgets.** Absolute (``max_edit_chars``) and relative
   (``textual_lr``, the fraction of the document one edit may touch), plus a
   hunk limit. All three are measured on the diff rather than trusted from the
   edit's declared size.
3. **Rejected-edit memory.** A rejected edit signature cannot be proposed again
   unchanged; near-misses on the same anchor are counted so the proposer can be
   told which direction is closed.

The budget is checked *before* the candidate is written, and re-checked by the
loop after application, so a buggy proposer cannot smuggle an oversized edit
through a custom code path.
"""
from __future__ import annotations

import dataclasses
import difflib
import hashlib
import json
import pathlib
import re
from typing import Iterable

from ._io import atomic_write_text
from .protected import check_untouched
from .spec import GatePolicy

OPS = ("add", "replace", "delete")


class EditError(ValueError):
    """Base class for a refused edit."""


class EditNotApplicable(EditError):
    """The anchor is missing or ambiguous; the edit cannot mean what it says."""


class BudgetExceeded(EditError):
    """The edit is larger than the textual learning rate allows."""


@dataclasses.dataclass(frozen=True)
class Edit:
    op: str
    anchor: str = ""
    content: str = ""

    def __post_init__(self) -> None:
        if self.op not in OPS:
            raise EditError(f"op must be one of {OPS}, got {self.op!r}")
        if self.op in {"replace", "delete"} and not self.anchor:
            raise EditError(f"'{self.op}' requires a non-empty anchor")
        if self.op in {"add", "replace"} and not self.content:
            raise EditError(f"'{self.op}' requires non-empty content")
        if self.op == "add" and self.anchor and self.anchor not in {"\n"}:
            if self.content.strip() == self.anchor.strip():
                raise EditError("add would duplicate its anchor verbatim")

    def signature(self) -> str:
        """Stable identity of the *direction*, not of the exact bytes.

        Whitespace is normalised so that a proposer cannot re-submit a rejected
        direction with different indentation and claim it is new.
        """
        payload = "\x1f".join(
            (self.op, " ".join(self.anchor.split()),
             " ".join(self.content.split()))
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def as_dict(self) -> dict[str, str]:
        return {"op": self.op, "anchor": self.anchor, "content": self.content}


@dataclasses.dataclass(frozen=True)
class BudgetCost:
    changed_chars: int
    hunks: int
    ratio: float

    def as_dict(self) -> dict[str, float | int]:
        return {"changed_chars": self.changed_chars, "hunks": self.hunks,
                "ratio": round(self.ratio, 4)}


def _changed_chars(old: str, new: str) -> tuple[int, int]:
    matcher = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
    changed = 0
    hunks = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        hunks += 1
        changed += max(i2 - i1, j2 - j1)
    return changed, hunks


def cost_of(old: str, new: str) -> BudgetCost:
    changed, hunks = _changed_chars(old, new)
    return BudgetCost(changed_chars=changed, hunks=hunks,
                      ratio=changed / max(1, len(old)))


def apply_edit(text: str, edit: Edit, policy: GatePolicy) -> str:
    """Apply one edit or raise; never returns a partially-applied document."""
    if edit.op == "add":
        new = text + edit.content if text.endswith("\n") else text + "\n" + edit.content
        if edit.anchor:
            count = text.count(edit.anchor)
            if count != 1:
                raise EditNotApplicable(
                    f"add anchor occurs {count} times (must be exactly 1)"
                )
            new = text.replace(edit.anchor, edit.anchor + edit.content, 1)
    elif edit.op == "replace":
        count = text.count(edit.anchor)
        if count != 1:
            raise EditNotApplicable(
                f"replace anchor occurs {count} times (must be exactly 1)"
            )
        new = text.replace(edit.anchor, edit.content, 1)
    else:  # delete
        count = text.count(edit.anchor)
        if count != 1:
            raise EditNotApplicable(
                f"delete anchor occurs {count} times (must be exactly 1)"
            )
        new = text.replace(edit.anchor, "", 1)

    if new.strip() == text.strip():
        raise EditNotApplicable("edit leaves the artifact unchanged")
    if not new.strip():
        raise EditNotApplicable("edit would empty the artifact")
    # SkillOpt's trainer owns the slow-update and appendix regions; a patch that
    # rewrites one is refused before it can cost a validation run.
    check_untouched(text, new)
    check_budget(text, new, policy)
    return new


def check_budget(old: str, new: str, policy: GatePolicy) -> BudgetCost:
    cost = cost_of(old, new)
    if cost.changed_chars > policy.max_edit_chars:
        raise BudgetExceeded(
            f"edit changes {cost.changed_chars} chars > max_edit_chars="
            f"{policy.max_edit_chars}"
        )
    if cost.ratio > policy.textual_lr:
        raise BudgetExceeded(
            f"edit rewrites {cost.ratio:.1%} of the artifact > textual_lr="
            f"{policy.textual_lr:.0%} — a rewrite is not a learning step"
        )
    if cost.hunks > policy.max_hunks:
        raise BudgetExceeded(
            f"edit touches {cost.hunks} regions > max_hunks={policy.max_hunks}"
        )
    return cost


@dataclasses.dataclass
class RejectedEntry:
    signature: str
    edit: dict[str, str]
    delta: float
    reason: str
    count: int = 1

    def as_dict(self) -> dict[str, object]:
        return {"signature": self.signature, "edit": self.edit,
                "delta": self.delta, "reason": self.reason, "count": self.count}


class RejectedBuffer:
    """Negative feedback for the proposer, persisted with the run state."""

    def __init__(self, entries: Iterable[RejectedEntry | dict] = ()) -> None:
        self._entries: dict[str, RejectedEntry] = {}
        for entry in entries:
            if isinstance(entry, dict):
                entry = RejectedEntry(
                    signature=str(entry["signature"]),
                    edit=dict(entry.get("edit", {})),
                    delta=float(entry.get("delta", 0.0)),
                    reason=str(entry.get("reason", "rejected")),
                    count=int(entry.get("count", 1)),
                )
            self._entries[entry.signature] = entry

    def add(self, edit: Edit, delta: float, reason: str) -> None:
        existing = self._entries.get(edit.signature())
        if existing is None:
            self._entries[edit.signature()] = RejectedEntry(
                signature=edit.signature(), edit=edit.as_dict(), delta=delta,
                reason=reason,
            )
        else:
            existing.count += 1
            existing.delta = delta
            existing.reason = reason

    def blocks(self, edit: Edit) -> bool:
        return edit.signature() in self._entries

    def near_misses(self, edit: Edit) -> int:
        """How often edits on the same anchor plus the same op were rejected."""
        anchor = " ".join(edit.anchor.split())
        return sum(
            entry.count for entry in self._entries.values()
            if " ".join(str(entry.edit.get("anchor", "")).split()) == anchor
            and entry.edit.get("op") == edit.op
        )

    def prompt(self, limit: int = 5) -> str:
        """A compact block of closed directions, for a proposer prompt."""
        if not self._entries:
            return ""
        ordered = sorted(self._entries.values(), key=lambda e: -e.count)[:limit]
        lines = ["Directions already rejected (do not repeat):"]
        for entry in ordered:
            lines.append(
                f"- {entry.edit.get('op', '?')} on {str(entry.edit.get('anchor', ''))[:40]!r}: "
                f"{entry.reason} (delta {entry.delta:+.2f}, {entry.count}x)"
            )
        return "\n".join(lines)

    def to_list(self) -> list[dict[str, object]]:
        return [entry.as_dict() for entry in self._entries.values()]

    def save(self, path: pathlib.Path) -> None:
        atomic_write_text(path, json.dumps(self.to_list(), indent=2, sort_keys=True) + "\n")

    @classmethod
    def load(cls, path: pathlib.Path) -> "RejectedBuffer":
        if not path.is_file():
            return cls()
        rows = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            raise ValueError(f"{path}: rejected buffer must be a JSON list")
        return cls(rows)

    def __len__(self) -> int:
        return len(self._entries)


_ANCHOR_RE = re.compile(r"^#+\s.*$", re.MULTILINE)


def section_anchor(text: str, heading: str) -> str:
    """Return an existing heading line, or '' when the section is absent."""
    for match in _ANCHOR_RE.finditer(text):
        if match.group(0).strip().lower() == heading.strip().lower():
            return match.group(0)
    return ""

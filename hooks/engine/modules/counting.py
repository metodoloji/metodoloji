"""The one bounded-list join, with no dependencies of its own (E-196, E-197).

`counted_list` lives here rather than in `utils` because the config layer needs
it: `config.py` validates itself at import time, and `utils` imports `config`,
so a formatter reached from `config` cannot come from `utils` without a circular
import. This module imports nothing from the engine, and `utils` re-exports the
name, so every caller still asks `modules.utils` for it.
"""

from __future__ import annotations


def counted_list(items, limit: int, *, noun: str = "item", sep: str = ", ") -> str:
    """Join at most `limit` items, counting the ones the bound withheld.

    A bounded list is a claim about the whole list: `E-001, E-002, …` reads as
    "these two" unless it says otherwise. Anything past `limit` is reported as
    `… (+N more <noun>)`, singular when N is 1, and a list that fits prints
    verbatim with no marker. `sep` is the caller's convention — `"; "` in
    denial and config messages, `", "` in report lines, `" "` where the caller
    builds a sentence.

    E-195 wrote this for the story validators' issue lists; E-196 generalised it
    after the stop report and the session inject were found slicing claim lists;
    E-197 moved it here so the config validator can reach it too.
    """
    items = [str(item) for item in items]
    shown = items[:limit]
    hidden = len(items) - len(shown)
    text = sep.join(shown)
    if hidden > 0:
        # A consonant + y takes -ies (`story` → `stories`); every other noun
        # the engine passes is a plain plural (E-211 named a story group).
        if hidden == 1:
            plural = noun
        elif len(noun) > 1 and noun.endswith("y") and noun[-2] not in "aeiou":
            plural = noun[:-1] + "ies"
        else:
            plural = noun + "s"
        text += f"{sep}… (+{hidden} more {plural})"
    return text

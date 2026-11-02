"""Protected regions: the parts of a skill document that a patch may not touch.

SkillOpt's skill-document guide separates the text an optimizer may rewrite from
the text the trainer owns. Two regions are managed by the training loop rather
than by the patch:

    <!-- SLOW_UPDATE_START --> ... <!-- SLOW_UPDATE_END -->
    <!-- APPENDIX_START -->    ... <!-- APPENDIX_END -->

The epoch-end slow update writes the first, the skill-aware reflection appendix
the second. A patch that edits inside one of them, deletes a marker or invents a
new region would either overwrite the trainer's longitudinal record or hide text
from every later patch, so both are refused before the candidate is measured.

This module is deliberately small and model-free: it locates the regions, gives
their bodies, and compares two documents. The edit engine calls
:func:`check_untouched` on every candidate; the export validator calls
:func:`validate` so a skill with unbalanced markers is never shipped.
"""
from __future__ import annotations

import dataclasses
import re

MARKERS: dict[str, tuple[str, str]] = {
    "SLOW_UPDATE": ("<!-- SLOW_UPDATE_START -->", "<!-- SLOW_UPDATE_END -->"),
    "APPENDIX": ("<!-- APPENDIX_START -->", "<!-- APPENDIX_END -->"),
}

_OPEN = {start: name for name, (start, _) in MARKERS.items()}
_CLOSE = {end: name for name, (_, end) in MARKERS.items()}
_TOKEN_RE = re.compile("|".join(re.escape(token) for token in (*_OPEN, *_CLOSE)))


class ProtectedRegionError(ValueError):
    """A candidate edits state the trainer owns, or breaks a marker pair."""


@dataclasses.dataclass(frozen=True)
class Region:
    name: str
    start: int
    end: int
    body: str


def regions(text: str) -> list[Region]:
    """Locate every protected region, or refuse an unbalanced document.

    Nested or duplicated regions are an error as well: the markers are a flat
    pairing, and a document that nests them has no single body to preserve.
    """
    opened: dict[str, int] = {}
    found: list[Region] = []
    for match in _TOKEN_RE.finditer(text):
        token = match.group(0)
        if token in _OPEN:
            name = _OPEN[token]
            if name in opened:
                raise ProtectedRegionError(
                    f"{name} region opened twice before its end marker"
                )
            opened[name] = match.end()
            continue
        name = _CLOSE[token]
        if name not in opened:
            raise ProtectedRegionError(
                f"{name} end marker at offset {match.start()} has no start marker"
            )
        found.append(Region(name=name, start=opened[name], end=match.start(),
                            body=text[opened[name]:match.start()]))
        del opened[name]
    if opened:
        raise ProtectedRegionError(
            "unclosed protected regions: " + ", ".join(sorted(opened))
        )
    return sorted(found, key=lambda region: region.start)


def bodies(text: str) -> dict[str, list[str]]:
    """Region bodies by name, in document order."""
    result: dict[str, list[str]] = {}
    for region in regions(text):
        result.setdefault(region.name, []).append(region.body)
    return result


def validate(text: str) -> list[str]:
    """Non-raising form, for export validation and preflight."""
    try:
        regions(text)
    except ProtectedRegionError as exc:
        return [str(exc)]
    return []


def check_untouched(old: str, new: str) -> None:
    """Refuse a candidate that adds, removes or rewrites a protected region."""
    before = regions(old)
    after = regions(new)
    if {region.name for region in before} != {region.name for region in after}:
        added = sorted({r.name for r in after} - {r.name for r in before})
        removed = sorted({r.name for r in before} - {r.name for r in after})
        raise ProtectedRegionError(
            "an edit may not add or remove protected regions "
            f"(added {added}, removed {removed}); the trainer owns those markers"
        )
    for region, candidate in zip(before, after):
        if region.body != candidate.body:
            raise ProtectedRegionError(
                f"an edit may not change the {region.name} region; it is written "
                "by the training loop, not by a patch"
            )

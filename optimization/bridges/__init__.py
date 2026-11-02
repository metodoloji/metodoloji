"""Export/import bridges for the two upstream frameworks, plus deployment.

Each bridge writes the files its upstream documents as the entry point, from the
optimizer's own versioned state:

* :mod:`hermit` — the 0.1.0 adapter tree (``ADAPTER.md``, ``exports.yaml``,
  ``.hermit/adapter.yaml``, ``.hermit/EVOLVER.md``) and a ``run.yaml`` with the
  documented ``data``/``template``/``evolver``/``agent``/``judge``/``trainer``
  sections, task splits, judge and pipeline.
* :mod:`skillopt` — the same Hermit tree with ``evolver.name: skillopt`` and its
  documented parameter defaults, plus ``best_skill.md`` and a parameter-mapping
  manifest.
* :mod:`deploy` — a host workspace in the shape ``hermit deploy`` produces for a
  context-only adapter.

Native plugin conversion stays Hermit's job: ``hermit convert`` consumes the
adapter directory this package writes.
"""
from __future__ import annotations

import pathlib
from typing import Any

from . import deploy, hermit, skillopt

BRIDGES = {"hermit": hermit, "skillopt": skillopt}

__all__ = ["BRIDGES", "deploy", "export", "hermit", "skillopt"]


def export(method: str, *args: Any, **kwargs: Any) -> dict[str, pathlib.Path]:
    if method not in BRIDGES:
        raise ValueError(f"unknown bridge {method!r}; expected one of {sorted(BRIDGES)}")
    return BRIDGES[method].export(*args, **kwargs)

"""Project-root containment — the ONE place that decides whether a path a run
names is inside the project the run lives in.

Two planes ask the same question about different things:

- the EVIDENCE plane about an artifact a spec declares or a witness recorded
  (E-060: `../outside.md`, an absolute path, a link out);
- the STORAGE plane about the two strings it turns into filenames — the SLUG it
  addresses a run by, and the `spec_file` a state records (E-062).

Both must get the same answer from the same implementation. A second copy would
be a second opinion on the same boundary, and the two planes would drift apart
at the first platform that resolves links differently — the reason E-056 made
the artifact matcher one function, applied to the boundary itself.

Resolution is REAL, not lexical: a symlink inside the project that points out of
it is an escape like any other, and both sides of the comparison are resolved the
same way, or a platform that symlinks its own temp directory (macOS `/var` →
`/private/var`) would make every path look outside.
"""

from __future__ import annotations

import os


def root_of(project_root: str) -> str:
    """The real path a run's files must resolve inside."""
    return os.path.realpath(os.path.abspath(str(project_root or ".")))


def outside(root_real: str, path: str) -> bool:
    """True when ``path`` does not resolve inside ``root_real``.

    A path on a different drive (Windows) or one that cannot be resolved at all
    is outside by definition: what cannot be shown to be inside the project is
    not the project's file.
    """
    try:
        real = os.path.realpath(os.path.abspath(str(path)))
        return os.path.commonpath([root_real, real]) != root_real
    except (OSError, ValueError):
        return True

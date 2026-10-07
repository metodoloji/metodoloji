"""BMAD hooks engine modules.

Import discipline: this package deliberately does NOT re-export the
handler functions (``guard``, ``quality``, ``deploy``, ``audit``,
``session_start``, ``stop``) under the same names as their submodules.
Doing so rebinds ``modules.audit`` / ``modules.guard`` / ``modules.stop``
to function objects, shadowing the submodules and making
``modules.audit.session_start``-style access (importlib, introspection,
tooling) raise ``AttributeError: 'function' object has no attribute ...``.

Always import through the submodule:

    from modules.audit import audit, session_start
    from modules.stop import stop
    from modules.guard import guard, quality, deploy
"""

from . import archive, audit, bash_targets, config, guard, stop, utils  # noqa: F401
from .config import runtime, GATE_DIR, log_file  # noqa: F401
from .utils import norm_path, is_free, is_code_target, repo_root, rel_to_root  # noqa: F401
from .archive import ArchiveLimitError, targets_from_tar, targets_from_unzip  # noqa: F401
from .bash_targets import extract_bash_targets  # noqa: F401

__all__ = [
    # submodules stay addressable: modules.audit, modules.guard, modules.stop, ...
    "archive",
    "audit",
    "bash_targets",
    "config",
    "guard",
    "stop",
    "utils",
    # non-colliding re-exports
    "runtime",
    "GATE_DIR",
    "log_file",
    "norm_path",
    "is_free",
    "is_code_target",
    "repo_root",
    "rel_to_root",
    "ArchiveLimitError",
    "targets_from_tar",
    "targets_from_unzip",
    "extract_bash_targets",
]

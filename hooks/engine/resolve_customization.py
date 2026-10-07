#!/usr/bin/env python3
"""resolve_customization — canonical source lives at
{metodoloji-root}/bmad/scripts/resolve_customization.py.

Thin re-export so hooks/engine/ keeps its stable path (check-plugin.sh asserts
these files exist and compile). CLI behavior is unchanged.
"""
import os
import runpy
import sys

_CANON = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "bmad", "scripts", "resolve_customization.py",
)

if __name__ == "__main__":
    sys.exit(runpy.run_path(_CANON)["main"]())

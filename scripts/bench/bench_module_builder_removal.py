"""Backwards-compatible entry point for E-136's recorded measurement.

E-136 was approved against `bench_module_builder_removal.py`. The bench grew a
`--skill` flag when the next removal needed the same closed set (E-137), so the
old name forwards to it with the module-builder default — the recorded control
command keeps working and the claim keeps reproducing.
"""

from __future__ import annotations

import sys

from bench_skill_removal import main

if __name__ == "__main__":
    sys.exit(main(["--skill", "bmad-module-builder", *sys.argv[1:]]))

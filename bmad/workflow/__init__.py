"""Workflow kernel — the methodology's dynamic execution core.

The kernel turns intent into a *declared* workflow (a spec) and then runs it as
a deterministic state machine: stages, preconditions, evidence, and computed
transitions. The model authors specs and performs the work inside a stage; it
never decides what the next stage is — the kernel computes it from the spec and
the recorded state. State lives on the blackboard (durable, event-sourced),
the spec lives in the project (reviewable), and hooks only ever surface the
current position (read-only).

Public surface:
    spec.load / spec.validate / spec.compute_next
    engine.create / complete / block / resume / status / next_stage / list_runs
    advisory.peek                       # read-only session-edge one-liner
"""

from . import spec  # noqa: F401
from . import engine  # noqa: F401
from . import advisory  # noqa: F401

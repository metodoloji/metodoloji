"""Strip the bmad-agent-* references from bench_consistency.py."""
import pathlib

path = pathlib.Path("scripts/bench/bench_consistency.py")
s = path.read_text(encoding="utf-8")

EDITS = [
    ('    (bmad-agent-builder: BA + AA) — uniqueness is per (module, code), not per\n',
     '    (bmad-workflow-builder: BW + AW) — uniqueness is per (module, code), not per\n'),
    ('''    GUIDE.md promises "Producer BRIDGEs carry a VERIFY step ... (automatic
    anti-skip check; audit #2c)" and names the producers — one of which keeps
    its BRIDGE inside ``[agent].principles``. Before E-018 #2c read only
    ``workflow.activation_steps_append`` for most of them: stripping the VERIFY
    marker from an agent-principles producer (``bmad-agent-dev``) left the
    audit green. This check pins #2c's producer set *and* per-surface key to
    GUIDE's documented set, then re-evaluates the verify-marker invariant on
    each producer TOML — so a surface cannot silently drop out of the audit
    (the "guard is only as wide as this tuple" class E-002/E-017 named).
    """''',
     '''    GUIDE.md promises "Producer BRIDGEs carry a VERIFY step ... (automatic
    anti-skip check; audit #2c)" and names the producers. Before E-018 #2c read
    only ``workflow.activation_steps_append``, so a surface whose BRIDGE lived
    elsewhere (``[agent].principles``) was never read and could drop the marker
    silently. This check pins #2c's producer set *and* per-surface key to
    GUIDE's documented set, then re-evaluates the verify-marker invariant on
    each producer TOML — so a surface cannot silently drop out of the audit
    (the "guard is only as wide as this tuple" class E-002/E-017 named).
    """'''),
    ('    agent_surfaces = {"bmad-agent-dev"}\n',
     '    agent_surfaces: set[str] = set()  # no agent-principles producer ships today\n'),
]

for old, new in EDITS:
    if old not in s:
        print("SKIP:", old.splitlines()[0][:70])
        continue
    s = s.replace(old, new, 1)
    print("OK  :", old.splitlines()[0][:70])

path.write_text(s, encoding="utf-8")

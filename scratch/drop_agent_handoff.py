"""Strip the bmad-agent-* surfaces from scripts/check-handoff.py."""
import pathlib

path = pathlib.Path("scripts/check-handoff.py")
s = path.read_text(encoding="utf-8")

EDITS = [
    # alt_dev: the dev persona branch is gone
    ('    "bmad-dev-auto": ["bmad-code-review"],\n'
     '    "bmad-agent-dev": ["bmad-code-review"],  # dev persona implements too\n',
     '    "bmad-dev-auto": ["bmad-code-review"],\n'),
    # stale comment fragments from the vertical removal + the persona block
    ('    # Terminal phase: it consumes the asset-generation baton and hands nothing\n'
     '    # on (the pipeline is done until an evolution cycle re-enters the brief).\n'
     '    # Registered so the lint checks its peek — an unregistered terminal receiver\n'
     '    # would leave a baton nobody can claim.\n'
     '    # The playtest plan\'s BRIDGE feeds the QR record the review session owns\n'
     '    # ("feed the findings into the QR, do not open a separate record") — the\n'
     '    # Vertical documentation tools — docs, no hop.\n'
     '    # PERSONA / ORCHESTRATION SURFACE — [] = no hop to post. A persona adopts\n'
     '    # an identity, grounds in the board and dispatches the user into a\n'
     '    # workflow; it produces no methodology record of its own, so it never\n'
     '    # signals downstream (see ROUTERS below for the read-side requirement).\n'
     '    "bmad-agent-analyst": [],\n'
     '    "bmad-agent-architect": [],\n'
     '    "bmad-agent-builder": [],\n'
     '    "bmad-agent-pm": [],\n'
     '    "bmad-agent-tech-writer": [],\n'
     '    "bmad-agent-ux-designer": [],\n'
     '    "bmad-tea": [],\n',
     '    "bmad-tea": [],\n'),
    # PEEK_EXEMPT: entry point + routers + tool
    ('    "bmad-agent-dev": "entry point",\n', ''),
    ('    "bmad-agent-analyst": "router",\n'
     '    "bmad-agent-architect": "router",\n'
     '    "bmad-agent-pm": "router",\n'
     '    "bmad-agent-tech-writer": "router",\n'
     '    "bmad-agent-ux-designer": "router",\n'
     '    "bmad-agent-builder": "tool",\n', ''),
    # DIGEST_FIRST: dev alternate + the persona front doors
    ('    "bmad-quick-dev", "bmad-dev-auto", "bmad-agent-dev", "bmad-code-review",\n',
     '    "bmad-quick-dev", "bmad-dev-auto", "bmad-code-review",\n'),
    ('    "bmad-agent-analyst", "bmad-agent-architect", "bmad-agent-pm",\n'
     '    "bmad-agent-tech-writer", "bmad-agent-ux-designer", "bmad-agent-builder",\n'
     '    "bmad-tea", "bmad-party-mode", "bmad-sprint-status",\n',
     '    "bmad-tea", "bmad-party-mode", "bmad-sprint-status",\n'),
    # the "tool" rationale names a skill that no longer ships
    ('#    (bmad-agent-builder emits agent skills, bmad-document-project emits docs).',
     '#    (bmad-document-project emits docs, bmad-index-docs indexes them).'),
]

for old, new in EDITS:
    if old not in s:
        print("SKIP:", old.splitlines()[0][:70])
        continue
    s = s.replace(old, new, 1)
    print("OK  :", old.splitlines()[0][:70])

path.write_text(s, encoding="utf-8")

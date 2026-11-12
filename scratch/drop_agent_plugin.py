"""Strip the bmad-agent-* surfaces from scripts/check-plugin.sh."""
import pathlib
import re

path = pathlib.Path("scripts/check-plugin.sh")
s = path.read_text(encoding="utf-8")

EDITS = [
    # §2 dev wing
    ('    "bmad-retrospective", "bmad-correct-course", "bmad-sprint-status",\n'
     '    "bmad-agent-dev", "bmad-agent-architect", "bmad-agent-pm",\n'
     '    "bmad-agent-ux-designer", "bmad-agent-tech-writer", "bmad-tea",\n',
     '    "bmad-retrospective", "bmad-correct-course", "bmad-sprint-status",\n'
     '    "bmad-tea",\n'),
    # §2 record-path audit
    ('    "bmad-dev-auto": ("S", "docs/development/stories/", "update"),\n'
     '    "bmad-agent-dev": ("S", "docs/development/stories/", "update"),\n',
     '    "bmad-dev-auto": ("S", "docs/development/stories/", "update"),\n'),
    # §2b agent-principles surface list is empty now
    ('# Agent-BRIDGE surfaces: BRIDGE lives in [agent].principles, not workflow steps.\n'
     'AGENT_TOML_SKILLS = [\n'
     '    "bmad-agent-dev",\n'
     ']\n',
     '# Agent-BRIDGE surfaces: BRIDGE lives in [agent].principles, not workflow steps.\n'
     '# Empty: the last one (bmad-agent-dev) left the tree with the persona group.\n'
     'AGENT_TOML_SKILLS = []\n'),
    # §2c producer map
    ('    "bmad-dev-auto": "workflow.activation_steps_append",\n'
     '    "bmad-agent-dev": "agent.principles",\n',
     '    "bmad-dev-auto": "workflow.activation_steps_append",\n'),
]

for old, new in EDITS:
    if old not in s:
        print("SKIP:", old.splitlines()[0][:70])
        continue
    s = s.replace(old, new, 1)
    print("OK  :", old.splitlines()[0][:70])

# Remove negtest stage 3 (the agent-principles probe) and renumber the rest.
start = s.index("# Stage 3/9: BRIDGE removed from an agent-principles surface")
end = s.index("# Stage 4/9: hooks.json locator desynced in ONE hook")
s = s[:start] + s[end:]
print("OK  : negtest stage 3 removed")

s = s.replace("total_stages = 10", "total_stages = 9", 1)
# Renumber the surviving stages 4..10 -> 3..9, in the print labels and comments.
for old_no in range(4, 11):
    new_no = old_no - 1
    s = s.replace(f"[{old_no}/{{total_stages}}]", f"[{new_no}/{{total_stages}}]")
    s = s.replace(f"# Stage {old_no}/9:", f"# Stage {new_no}/9:")
    s = s.replace(f"# Stage {old_no}/10:", f"# Stage {new_no}/9:")
print("OK  : stages renumbered 3..9")

path.write_text(s, encoding="utf-8")

"""Strip the bmad-agent-* surfaces from scripts/check-custom.sh."""
import pathlib

path = pathlib.Path("scripts/check-custom.sh")
s = path.read_text(encoding="utf-8")

EDITS = [
    # §1 development-wing surface list
    ('    "bmad-retrospective", "bmad-correct-course", "bmad-sprint-status",\n'
     '    "bmad-agent-dev", "bmad-agent-architect", "bmad-agent-pm",\n'
     '    "bmad-agent-ux-designer", "bmad-agent-tech-writer", "bmad-tea",\n',
     '    "bmad-retrospective", "bmad-correct-course", "bmad-sprint-status",\n'
     '    "bmad-tea",\n'),
    # §2 + §3 gate-enforcing sets
    ('    "bmad-code-review", "bmad-agent-dev",\n',
     '    "bmad-code-review",\n'),
    # §3 reference-OK set
    ('    # Meta/generator surface: its principles DESCRIBE the gate ("writes without\n'
     '    # a VERIFIED record are mechanically blocked by the guard hook"); it\n'
     '    # produces surfaces, it does not enforce the gate itself. Before this entry\n'
     '    # §3 reported it as a leak on every run — a permanent red that masked the\n'
     '    # next real hard-gate regression.\n'
     '    "bmad-agent-builder",\n',
     ''),
]

for old, new in EDITS:
    count = s.count(old)
    if count == 0:
        print("SKIP:", old.splitlines()[0][:70])
        continue
    s = s.replace(old, new)
    print(f"OK  : {count}x {old.splitlines()[0][:60]}")

path.write_text(s, encoding="utf-8")

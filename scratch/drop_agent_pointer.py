"""Neutralize dangling pointers to the removed bmad-agent-builder skill."""
import pathlib

EDITS = {
    "skills/bmad-loop-setup/scripts/cleanup-legacy.py": [
        ("        List of skill directory names (e.g. ['bmad-agent-builder', 'bmad-builder-setup'])",
         "        List of skill directory names (e.g. ['bmad-module-builder', 'bmad-bmb-setup'])"),
    ],
    "skills/bmad-bmb-setup/scripts/cleanup-legacy.py": [
        ("        List of skill directory names (e.g. ['bmad-agent-builder', 'bmad-builder-setup'])",
         "        List of skill directory names (e.g. ['bmad-module-builder', 'bmad-bmb-setup'])"),
    ],
    "skills/bmad-module-builder/assets/setup-skill-template/scripts/cleanup-legacy.py": [
        ("        List of skill directory names (e.g. ['bmad-agent-builder', 'bmad-builder-setup'])",
         "        List of skill directory names (e.g. ['bmad-module-builder', 'bmad-bmb-setup'])"),
    ],
    "skills/bmad-module-builder/references/module-quality-canon.md": [
        ("Each skill in the module must meet the bar defined in the agent-builder's and workflow-builder's quality canons. At minimum:",
         "Each skill in the module must meet the bar defined in the workflow-builder's quality canon. At minimum:"),
        ("Apply the same test the agent-builder and workflow-builder use:",
         "Apply the same test the workflow-builder uses:"),
    ],
    "skills/bmad-module-builder/references/create-module.md": [
        ("**Also read `customize.toml` if present.** Skills built by the agent-builder ship a `customize.toml` alongside SKILL.md with an `[agent]` metadata block",
         "**Also read `customize.toml` if present.** A persona-shaped skill ships a `customize.toml` alongside SKILL.md with an `[agent]` metadata block"),
    ],
    "skills/bmad-workflow-builder/references/skill-quality-principles.md": [
        ("Fix: point the author at agent-builder and remove it from the workflow surface.",
         "Fix: move those fields to an agent surface (a `customize.toml` carrying an `[agent]` block) and remove them from the workflow surface."),
    ],
    "skills/bmad-workflow-builder/references/scan-customization.md": [
        ("because those are agent-shape fields that belong with agent-builder, not on the workflow surface.",
         "because those are agent-shape fields that belong on an agent surface, not on the workflow surface."),
    ],
    "skills/bmad-workflow-builder/scripts/tests/test_canon_sync.py": [
        ('''"""Guard against drift between the embedded prompt-quality-canon copies.

The canon is embedded in three places — workflow-builder references,
agent-builder references, and the agent-builder asset emitted into built
agents — with no in-file sync note, because the loaded files are LLM-facing
and a maintenance comment there is paid on every load. This test is the
sync mechanism instead: all three copies must be byte-identical.
Run with: python3 -m pytest test_canon_sync.py
(or plain `python3 test_canon_sync.py` for a lightweight self-check).
"""''',
         '''"""Guard the embedded prompt-quality-canon copy.

The canon ships in workflow-builder's references, with no in-file sync note,
because the loaded file is LLM-facing and a maintenance comment there is paid
on every load. It used to be embedded in three places (workflow-builder plus
two agent-builder copies) and this test kept them byte-identical; the persona
group is gone, so the remaining invariant is that the single shipped copy
exists and is not empty.
Run with: python3 -m pytest test_canon_sync.py
(or plain `python3 test_canon_sync.py` for a lightweight self-check).
"""'''),
        ('''CANON_COPIES = [
    SKILLS_DIR / "bmad-workflow-builder" / "references" / "prompt-quality-canon.md",
    SKILLS_DIR / "bmad-agent-builder" / "references" / "prompt-quality-canon.md",
    SKILLS_DIR / "bmad-agent-builder" / "assets" / "prompt-quality-canon.md",
]''',
         '''CANON_COPIES = [
    SKILLS_DIR / "bmad-workflow-builder" / "references" / "prompt-quality-canon.md",
]'''),
        ('''def test_all_copies_identical():
    contents = {p: p.read_bytes() for p in CANON_COPIES if p.is_file()}
    reference = CANON_COPIES[0]
    diverged = [
        str(p)
        for p, body in contents.items()
        if body != contents.get(reference)
    ]
    assert not diverged, (
        "canon copies have drifted from "
        f"{reference}: {diverged} — sync all copies together"
    )''',
         '''def test_all_copies_identical():
    """With one shipped copy this pins that it is non-empty, not drift."""
    contents = {p: p.read_bytes() for p in CANON_COPIES if p.is_file()}
    reference = CANON_COPIES[0]
    assert contents, f"canon copy missing: {reference}"
    assert contents[reference].strip(), f"canon copy is empty: {reference}"
    diverged = [str(p) for p, body in contents.items() if body != contents[reference]]
    assert not diverged, (
        "canon copies have drifted from "
        f"{reference}: {diverged} — sync all copies together"
    )'''),
    ],
    "skills/bmad-workflow-builder/scripts/tests/test_render_report.py": [
        ('''SHELLS = [
    SKILLS_DIR / "bmad-workflow-builder" / "assets" / "report-shell.html",
    SKILLS_DIR / "bmad-agent-builder" / "assets" / "report-shell.html",
]''',
         '''SHELLS = [
    SKILLS_DIR / "bmad-workflow-builder" / "assets" / "report-shell.html",
]'''),
        ('''def test_render_script_copies_identical():
    other = SKILLS_DIR / "bmad-agent-builder" / "scripts" / "render_report.py"
    assert SCRIPT.read_bytes() == other.read_bytes(), (
        "render_report.py copies have drifted between the two builder skills"
    )

''', ''),
        ('''    test_shipped_shells_carry_placeholder_island()
    test_render_script_copies_identical()
    print("ok: render_report self-check passed (run full suite with pytest)")''',
         '''    test_shipped_shells_carry_placeholder_island()
    print("ok: render_report self-check passed (run full suite with pytest)")'''),
    ],
}

for rel, pairs in EDITS.items():
    p = pathlib.Path(rel)
    s = p.read_text(encoding="utf-8")
    for old, new in pairs:
        if old not in s:
            print(f"SKIP {rel}: {old.splitlines()[0][:60]}")
            continue
        s = s.replace(old, new, 1)
        print(f"OK   {rel}")
    p.write_text(s, encoding="utf-8")

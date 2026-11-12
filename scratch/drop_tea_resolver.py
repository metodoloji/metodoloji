"""Neutralize the removed `tea` module name in the resolver docs and its tests."""
import pathlib

EDITS = {
    "bmad/scripts/resolve_config.py": [
        ('written by older installers (e.g. ``{project-root}/bmad/tea/config.yaml``,\n'
         '``{project-root}/bmad/core/config.yaml``)',
         'written by older installers (e.g. ``{project-root}/bmad/<module>/config.yaml``,\n'
         '``{project-root}/bmad/core/config.yaml``)'),
        ('  uv run resolve_config.py --project-root ... --module tea\n'
         '  uv run resolve_config.py --project-root ... --module tea --module bmb\n',
         '  uv run resolve_config.py --project-root ... --module bmb\n'
         '  uv run resolve_config.py --project-root ... --module bmb --module core\n'),
        ('had, so skills can migrate from "read bmad/tea/config.yaml" to\n'
         '"run resolve_config.py --module tea" without restructuring their logic.',
         'had, so skills can migrate from "read bmad/<module>/config.yaml" to\n'
         '"run resolve_config.py --module <module>" without restructuring their logic.'),
    ],
    "bmad/tests/test_resolve_config.py": [
        ("[modules.tea]\nrisk_threshold = \"p1\"\ntest_stack_type = \"auto\"",
         "[modules.sample]\nrisk_threshold = \"p1\"\ntest_stack_type = \"auto\""),
        ('tea_use_playwright_utils: true\ntea_use_pactjs_utils: false',
         'sample_use_playwright_utils: true\nsample_use_pactjs_utils: false'),
        ('    assert data["tea_use_playwright_utils"] is True\n'
         '    assert data["tea_use_pactjs_utils"] is False',
         '    assert data["sample_use_playwright_utils"] is True\n'
         '    assert data["sample_use_pactjs_utils"] is False'),
        ('project / "bmad" / "tea" / "config.yaml"', 'project / "bmad" / "sample" / "config.yaml"'),
        ('"modules.tea.risk_threshold"', '"modules.sample.risk_threshold"'),
        ('"modules.tea"', '"modules.sample"'),
        ("'[modules.tea]\\nrisk_threshold = \"p2\"\\n'", "'[modules.sample]\\nrisk_threshold = \"p2\"\\n'"),
        ('out["modules.tea"]["risk_threshold"]', 'out["modules.sample"]["risk_threshold"]'),
        ('out["modules.tea"]["test_stack_type"]', 'out["modules.sample"]["test_stack_type"]'),
        ('"--module", "tea"', '"--module", "sample"'),
        ('out["tea"]["user_name"]', 'out["sample"]["user_name"]'),
    ],
}

for rel, pairs in EDITS.items():
    p = pathlib.Path(rel)
    s = p.read_text(encoding="utf-8")
    for old, new in pairs:
        if old not in s:
            print(f"SKIP {rel}: {old.splitlines()[0][:60]}")
            continue
        s = s.replace(old, new)
        print(f"OK   {rel}: {old.splitlines()[0][:50]}")
    p.write_text(s, encoding="utf-8")

"""Finish the tea->sample rename in the resolver tests."""
import pathlib

p = pathlib.Path("bmad/tests/test_resolve_config.py")
s = p.read_text(encoding="utf-8")
pairs = [
    ("'[modules.tea]\\nrisk_threshold = \"p0\"\\n'", "'[modules.sample]\\nrisk_threshold = \"p0\"\\n'"),
    ('out["tea"]["risk_threshold"]', 'out["sample"]["risk_threshold"]'),
    ('assert rc._modules_from_key_paths(["modules.sample.risk_threshold", "core"]) == ["tea"]',
     'assert rc._modules_from_key_paths(["modules.sample.risk_threshold", "core"]) == ["sample"]'),
]
for old, new in pairs:
    if old not in s:
        print("SKIP", old[:60])
        continue
    s = s.replace(old, new)
    print("OK  ", old[:60])
p.write_text(s, encoding="utf-8")

q = pathlib.Path("bmad/scripts/resolve_config.py")
t = q.read_text(encoding="utf-8")
t = t.replace('"""Extract module codes from --key paths like modules.tea.risk_threshold."""',
              '"""Extract module codes from --key paths like modules.<code>.risk_threshold."""')
t = t.replace('help="Module code (e.g. tea, bmb) — output a flat core+module object (repeatable)"',
              'help="Module code (e.g. bmb, core) — output a flat core+module object (repeatable)"')
q.write_text(t, encoding="utf-8")
print("resolver docstrings updated")

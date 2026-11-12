"""Align the orient tests with the surviving module set."""
import pathlib

p = pathlib.Path("bmad/tests/test_orient.py")
s = p.read_text(encoding="utf-8")

pairs = [
    ('    assert digest["skill_count"] > 50 and len(digest["modules"]) > 3',
     '    assert digest["skill_count"] > 40 and len(digest["modules"]) > 2'),
    ('    assert set(digest["module_codes"]) >= {"bmm", "tea"}',
     '    assert set(digest["module_codes"]) >= {"bmm", "bmb"}'),
]
for old, new in pairs:
    if old not in s:
        print("SKIP", old[:60])
        continue
    s = s.replace(old, new, 1)
    print("OK  ", old[:60])
p.write_text(s, encoding="utf-8")

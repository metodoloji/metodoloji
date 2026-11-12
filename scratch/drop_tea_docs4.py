import pathlib

for rel, pairs in {
    "GUIDE.md": [("`--module tea` (23 lines / 818 B)", "`--module bmm` (23 lines / 818 B)")],
    "docs/CLAUDE.md": [("`--module tea` is 23 lines / 818 B", "`--module bmm` is 23 lines / 818 B")],
    "scripts/tests/test_check_handoff.py": [
        ("--module tea`", "--module bmm`"),
    ],
}.items():
    p = pathlib.Path(rel)
    s = p.read_text(encoding="utf-8")
    for old, new in pairs:
        if old not in s:
            print("SKIP", rel, old[:40])
            continue
        s = s.replace(old, new)
        print("OK  ", rel)
    p.write_text(s, encoding="utf-8")

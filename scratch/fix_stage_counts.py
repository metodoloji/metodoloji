"""Fix the stage / producer counts the audits pin to the runbooks."""
import pathlib

EDITS = {
    "GUIDE.md": [
        ("# 10 stages: .env/.gitignore, 2 BRIDGE removals (#2b), #1b hooks.json,",
         "# 9 stages: .env/.gitignore, 1 BRIDGE removal (#2b), #1b hooks.json,"),
        ("#2c bridge VERIFY (8 skills)", "#2c bridge VERIFY (7 skills)"),
    ],
    "KILAVUZ.md": [
        ("# 10 aşama: .env/.gitignore, 2 BRIDGE sökme (#2b), #1b hooks.json,",
         "# 9 aşama: .env/.gitignore, 1 BRIDGE sökme (#2b), #1b hooks.json,"),
        ("#2c bridge VERIFY (8 skill)", "#2c bridge VERIFY (7 skill)"),
    ],
    "scripts/check-plugin.sh": [
        ("#            (negative tests, 10 stages: §6a .env inventory, §2b BRIDGE",
         "#            (negative tests, 9 stages: §6a .env inventory, §2b BRIDGE"),
    ],
}

for rel, pairs in EDITS.items():
    p = pathlib.Path(rel)
    s = p.read_text(encoding="utf-8")
    for old, new in pairs:
        if old not in s:
            print(f"SKIP {rel}: {old[:60]}")
            continue
        s = s.replace(old, new, 1)
        print(f"OK   {rel}")
    p.write_text(s, encoding="utf-8")

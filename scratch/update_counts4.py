"""Update shipped counts after the module-builder removal (52 skills, 46 TOMLs)."""
import pathlib

FILES = [
    "CONTRIBUTING.md", "HERMES.md", "README.md", "GUIDE.md", "KILAVUZ.md",
    "docs/CLAUDE.md", "docs/OPENCODE.md", "plugin.yaml",
    ".claude-plugin/plugin.json", ".claude-plugin/marketplace.json",
    ".plugin/plugin.json", ".plugin/marketplace.json",
    ".opencode-plugin/plugin.json",
]
PAIRS = [
    ("53 skills", "52 skills"),
    ("53 skill", "52 skill"),
    ("48 customization TOMLs", "46 customization TOMLs"),
    ("48 bridge TOMLs", "46 bridge TOMLs"),
    ("48 TOMLs", "46 TOMLs"),
    ("48 TOML", "46 TOML"),
]
for rel in FILES:
    p = pathlib.Path(rel)
    s = p.read_text(encoding="utf-8")
    orig = s
    for old, new in PAIRS:
        s = s.replace(old, new)
    if s != orig:
        p.write_text(s, encoding="utf-8")
        print("UPD", rel)

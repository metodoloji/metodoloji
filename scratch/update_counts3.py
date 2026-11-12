"""Update shipped counts after the TEA removal."""
import pathlib

FILES = [
    "CONTRIBUTING.md", "HERMES.md", "README.md", "GUIDE.md", "KILAVUZ.md",
    "docs/CLAUDE.md", "docs/OPENCODE.md", "plugin.yaml",
    ".claude-plugin/plugin.json", ".claude-plugin/marketplace.json",
    ".plugin/plugin.json", ".plugin/marketplace.json",
    ".opencode-plugin/plugin.json",
]
PAIRS = [
    ("64 skills", "53 skills"),
    ("64 skill", "53 skill"),
    ("58 customization TOMLs", "48 customization TOMLs"),
    ("58 bridge TOMLs", "48 bridge TOMLs"),
    ("58 TOMLs", "48 TOMLs"),
    ("58 TOML", "48 TOML"),
    ("16 active BRIDGEs", "7 active BRIDGEs"),
    ("16 BRIDGEs", "7 BRIDGEs"),
    ("16 BRIDGE", "7 BRIDGE"),
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

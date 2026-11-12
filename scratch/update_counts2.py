"""Update the shipped skill / TOML / BRIDGE counts after the persona removal."""
import pathlib

ROOT = pathlib.Path(".")
FILES = [
    "CONTRIBUTING.md", "HERMES.md", "README.md", "GUIDE.md", "KILAVUZ.md",
    "docs/CLAUDE.md", "docs/OPENCODE.md", "plugin.yaml",
    ".claude-plugin/plugin.json", ".claude-plugin/marketplace.json",
    ".plugin/plugin.json", ".plugin/marketplace.json",
    ".opencode-plugin/plugin.json",
]
PAIRS = [
    ("71 skills", "64 skills"),
    ("71 skill", "64 skill"),
    ("65 customization TOMLs", "58 customization TOMLs"),
    ("65 bridge TOMLs", "58 bridge TOMLs"),
    ("65 TOMLs", "58 TOMLs"),
    ("65 TOML", "58 TOML"),
    ("17 active BRIDGEs", "16 active BRIDGEs"),
    ("17 BRIDGEs", "16 BRIDGEs"),
    ("17 BRIDGE", "16 BRIDGE"),
]
for rel in FILES:
    p = ROOT / rel
    s = p.read_text(encoding="utf-8")
    orig = s
    for old, new in PAIRS:
        s = s.replace(old, new)
    if s != orig:
        p.write_text(s, encoding="utf-8")
        print("UPD", rel)
    else:
        print("--- ", rel)

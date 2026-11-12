"""Drop [modules.tea] and [agents.bmad-tea] from bmad/config.toml."""
import re
import pathlib

path = pathlib.Path("bmad/config.toml")
lines = path.read_text(encoding="utf-8").splitlines(True)

blocks = []
cur = None
for line in lines:
    m = re.match(r"^\[([^\]]+)\]\s*$", line)
    if m:
        if cur is not None:
            blocks.append(cur)
        cur = {"hdr": m.group(1), "lines": [line]}
    else:
        if cur is None:
            cur = {"hdr": None, "lines": [line]}
        else:
            cur["lines"].append(line)
if cur is not None:
    blocks.append(cur)

DROP = {"modules.tea", "agents.bmad-tea"}
out = [b for b in blocks if b["hdr"] not in DROP]
text = "".join("".join(b["lines"]) for b in out)
text = re.sub(r"\n{3,}", "\n\n", text)
path.write_text(text, encoding="utf-8")
print("dropped:", sorted(DROP))
print("remaining:", [b["hdr"] for b in out if b["hdr"]])

"""Remove the six bmad-agent-* blocks from bmad/config.toml, keep bmad-tea."""
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

out = []
dropped = []
for b in blocks:
    if b["hdr"] and b["hdr"].startswith("agents.bmad-agent-"):
        dropped.append(b["hdr"])
        continue
    out.append(b)

text = "".join("".join(b["lines"]) for b in out)
text = re.sub(r"\n{3,}", "\n\n", text)
path.write_text(text, encoding="utf-8")
print("dropped:", dropped)
print("remaining sections:", [b["hdr"] for b in out if b["hdr"]])

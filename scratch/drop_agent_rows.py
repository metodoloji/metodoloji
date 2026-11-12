"""Drop bmad-agent-* rows from the catalog/manifest CSVs."""
import csv
import io

FILES = (
    "bmad/_config/skill-manifest.csv",
    "bmad/_config/bmad-help.csv",
    "bmad/bmm/module-help.csv",
    "bmad/bmb/module-help.csv",
    "skills/bmad-bmb-setup/assets/module-help.csv",
)
PREFIX = "bmad-agent-"

for path in FILES:
    text = open(path, encoding="utf-8").read()
    lines = text.splitlines(True)
    rows = list(csv.reader(io.StringIO(text)))
    header = rows[0]
    col = header.index("skill") if "skill" in header else header.index("canonicalId")
    out = [lines[0]]
    dropped = []
    for line, row in zip(lines[1:], rows[1:]):
        if len(row) > col and row[col].startswith(PREFIX):
            dropped.append(row[col])
            continue
        out.append(line)
    open(path, "w", encoding="utf-8").write("".join(out))
    print(f"{path}: dropped {len(dropped)} rows, {len(out) - 1} remain")

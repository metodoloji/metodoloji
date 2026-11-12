import csv
import io

FILES = (
    "bmad/_config/skill-manifest.csv",
    "bmad/_config/bmad-help.csv",
    "bmad/bmm/module-help.csv",
)
NAMES = {"bmad-tea", "bmad-teach-me-testing", "bmad-qa-generate-e2e-tests"}
PREFIX = "bmad-testarch-"

for path in FILES:
    text = open(path, encoding="utf-8").read()
    lines = text.splitlines(True)
    rows = list(csv.reader(io.StringIO(text)))
    header = rows[0]
    name_col = header.index("skill") if "skill" in header else header.index("canonicalId")
    mod_col = header.index("module")
    out = [lines[0]]
    dropped = []
    for line, row in zip(lines[1:], rows[1:]):
        name = row[name_col] if len(row) > name_col else ""
        module = row[mod_col] if len(row) > mod_col else ""
        if name.startswith(PREFIX) or name in NAMES or module == "Test Architecture Enterprise":
            dropped.append(name or module)
            continue
        out.append(line)
    open(path, "w", encoding="utf-8").write("".join(out))
    print(f"{path}: dropped {len(dropped)}, {len(out) - 1} remain")

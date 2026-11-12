"""Strip the TEA/testing group from scripts/check-custom.sh."""
import pathlib

path = pathlib.Path("scripts/check-custom.sh")
s = path.read_text(encoding="utf-8")

EDITS = [
    # §7 negtest 3 retargets to a surviving bridge-citing TOML
    ('# Inject "bolum 99.99" into bmad-testarch-atdd.toml — a section not in the\n'
     '# bridge; §7\'s "bolum" pattern must also catch it.\n'
     'test_toml = PLUGIN / "custom" / "bmad-testarch-atdd.toml"\n',
     '# Inject "bolum 99.99" into a bridge-citing TOML — a section not in the\n'
     '# bridge; §7\'s "bolum" pattern must also catch it.\n'
     'test_toml = PLUGIN / "custom" / "bmad-dev-story.toml"\n'),
    ('            if "bmad-testarch-atdd" in line and "not in bridge" in line:',
     '            if "bmad-dev-story" in line and "not in bridge" in line:'),
    ('    print("[ERROR] bolum drift error visible even in intact testarch TOML — test setup broken")',
     '    print("[ERROR] bolum drift error visible even in the intact TOML — test setup broken")'),
    ('# Inject as a comment line so TOML semantics are not broken. The testarch\n'
     '# file already carries a bridge reference, so §7 audits it.\n',
     '# Inject as a comment line so TOML semantics are not broken. The file\n'
     '# already carries a bridge reference, so §7 audits it.\n'),
    ("print(f\"[OK]   negative test 3/{total_stages}: 'bolum 99.99' injected → §7 MISS caught → testarch TOML restored\")",
     "print(f\"[OK]   negative test 3/{total_stages}: 'bolum 99.99' injected → §7 MISS caught → TOML restored\")"),
    # §1 development-wing list
    ('    "bmad-dev-auto",\n'
     '    "bmad-testarch-atdd", "bmad-testarch-automate", "bmad-testarch-ci",\n'
     '    "bmad-testarch-framework", "bmad-testarch-nfr", "bmad-testarch-test-design",\n'
     '    "bmad-testarch-test-review", "bmad-testarch-trace",\n'
     '    "bmad-code-review",\n'
     '    "bmad-qa-generate-e2e-tests",\n'
     '    "bmad-document-project",\n'
     '    "bmad-retrospective", "bmad-correct-course", "bmad-sprint-status",\n'
     '    "bmad-tea",\n',
     '    "bmad-dev-auto",\n'
     '    "bmad-code-review",\n'
     '    "bmad-document-project",\n'
     '    "bmad-retrospective", "bmad-correct-course", "bmad-sprint-status",\n'),
    # §3 reference-OK comment names a removed skill
    ('    "bmad-tea",  # test strategy documentary: "depends on Mod A mechanical approval"\n',
     ''),
    # §0 docstring
    ('#        GATE_REFERENCE_OK → may carry as reference (e.g. bmad-tea)',
     '#        GATE_REFERENCE_OK → may carry as reference (e.g. bmad-quality-record)'),
]

for old, new in EDITS:
    if old not in s:
        print("SKIP:", old.splitlines()[0][:70])
        continue
    s = s.replace(old, new, 1)
    print("OK  :", old.splitlines()[0][:70])

path.write_text(s, encoding="utf-8")

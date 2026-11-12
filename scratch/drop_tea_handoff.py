"""Strip the TEA/testing group from scripts/check-handoff.py."""
import pathlib

path = pathlib.Path("scripts/check-handoff.py")
s = path.read_text(encoding="utf-8")

EDITS = [
    # EXPECTED_POSTS: the whole testing sub-chain
    ('''    # TESTING SUB-CHAIN (TEA toolbox feeders — each adds evidence to an
    # existing QR record's mechanical checks instead of opening its own
    # record, so all of them signal the quality record they feed)
    "bmad-testarch-atdd": ["bmad-quality-record"],
    "bmad-testarch-automate": ["bmad-quality-record"],
    "bmad-testarch-ci": ["bmad-quality-record"],
    "bmad-testarch-framework": ["bmad-quality-record"],
    "bmad-testarch-nfr": ["bmad-quality-record"],
    "bmad-testarch-test-design": ["bmad-quality-record"],
    "bmad-testarch-test-review": ["bmad-quality-record"],
    "bmad-testarch-trace": ["bmad-quality-record"],
    "bmad-qa-generate-e2e-tests": ["bmad-quality-record"],
    # TEA Academy close-out recommends "start with Framework setup": the
    # learner lands in the chain the academy teaches.
    "bmad-teach-me-testing": ["bmad-testarch-framework"],
''', ''),
    # EXPECTED_POSTS: the TEA persona row
    ('    "bmad-tea": [],\n', ''),
    # PEEK_EXEMPT
    ('    "bmad-teach-me-testing": "entry point",\n'
     '    "bmad-testarch-atdd": "feeder",\n'
     '    "bmad-testarch-automate": "feeder",\n'
     '    "bmad-testarch-ci": "feeder",\n'
     '    "bmad-testarch-nfr": "feeder",\n'
     '    "bmad-testarch-test-design": "feeder",\n'
     '    "bmad-testarch-test-review": "feeder",\n'
     '    "bmad-testarch-trace": "feeder",\n'
     '    "bmad-qa-generate-e2e-tests": "feeder",\n', ''),
    ('    "bmad-tea": "router",\n', ''),
    # DIGEST_FIRST
    ('    "bmad-tea", "bmad-party-mode", "bmad-sprint-status",\n',
     '    "bmad-party-mode", "bmad-sprint-status",\n'),
]

for old, new in EDITS:
    if old not in s:
        print("SKIP:", old.splitlines()[0][:70])
        continue
    s = s.replace(old, new, 1)
    print("OK  :", old.splitlines()[0][:70])

path.write_text(s, encoding="utf-8")

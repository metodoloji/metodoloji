"""Strip the TEA/testing group from scripts/check-plugin.sh."""
import pathlib

path = pathlib.Path("scripts/check-plugin.sh")
s = path.read_text(encoding="utf-8")

EDITS = [
    # §2 dev wing
    ('    "bmad-dev-auto", "bmad-research-experiment",\n'
     '    "bmad-testarch-atdd", "bmad-testarch-automate", "bmad-testarch-ci",\n'
     '    "bmad-testarch-framework", "bmad-testarch-nfr", "bmad-testarch-test-design",\n'
     '    "bmad-testarch-test-review", "bmad-testarch-trace",\n'
     '    "bmad-code-review", "bmad-review-adversarial-general",\n'
     '    "bmad-review-edge-case-hunter", "bmad-qa-generate-e2e-tests",\n'
     '    "bmad-eval-runner", "bmad-document-project",\n'
     '    "bmad-retrospective", "bmad-correct-course", "bmad-sprint-status",\n'
     '    "bmad-tea",\n',
     '    "bmad-dev-auto", "bmad-research-experiment",\n'
     '    "bmad-code-review", "bmad-review-adversarial-general",\n'
     '    "bmad-review-edge-case-hunter",\n'
     '    "bmad-eval-runner", "bmad-document-project",\n'
     '    "bmad-retrospective", "bmad-correct-course", "bmad-sprint-status",\n'),
    # §2 QR feeder TOML audit
    ('QR_FEEDERS_TOML = [\n'
     '    "bmad-qa-generate-e2e-tests",\n'
     '    "bmad-testarch-atdd", "bmad-testarch-automate", "bmad-testarch-ci",\n'
     '    "bmad-testarch-framework", "bmad-testarch-nfr", "bmad-testarch-test-design",\n'
     '    "bmad-testarch-test-review", "bmad-testarch-trace",\n'
     ']\n',
     'QR_FEEDERS_TOML = []\n'),
    # §2b runtime-visibility probe
    ('    "bmad-dev-story", "bmad-quick-dev", "bmad-dev-auto",\n'
     '    "bmad-qa-generate-e2e-tests",\n'
     '    "bmad-testarch-atdd", "bmad-testarch-automate", "bmad-testarch-ci",\n'
     '    "bmad-testarch-framework", "bmad-testarch-nfr", "bmad-testarch-test-design",\n'
     '    "bmad-testarch-test-review", "bmad-testarch-trace",\n'
     ']\n',
     '    "bmad-dev-story", "bmad-quick-dev", "bmad-dev-auto",\n'
     ']\n'),
]

for old, new in EDITS:
    if old not in s:
        print("SKIP:", old.splitlines()[0][:70])
        continue
    s = s.replace(old, new, 1)
    print("OK  :", old.splitlines()[0][:70])

path.write_text(s, encoding="utf-8")

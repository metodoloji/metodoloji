"""Strip the TEA/testing prose from the runbooks."""
import pathlib

EDITS = {
    "GUIDE.md": [
        ("- **bmad-**\\* (core): PRD, architecture, story, sprint, dev-story/quick-dev/dev-auto, code-review, testarch-*, QA e2e, TEA, retrospective, correct-course, loop-*, eval-runner, forge-idea, brainstorming, research skills.",
         "- **bmad-**\\* (core): PRD, architecture, story, sprint, dev-story/quick-dev/dev-auto, code-review, retrospective, correct-course, loop-*, eval-runner, forge-idea, brainstorming, research skills."),
        ("- **Feeder (9, feeds an existing QR):** `bmad-testarch-*` (atdd, automate, ci, framework, nfr, test-design, test-review, trace), `bmad-qa-generate-e2e-tests`.\n",
         ""),
        ("| Quality           | `bmad-code-review`, `bmad-quality-record`, `bmad-testarch-*`, `bmad-qa-generate-e2e-tests`             |",
         "| Quality           | `bmad-code-review`, `bmad-quality-record`                                                             |"),
        ("| Correction route  | `bmad-correct-course`, `bmad-retrospective`, `bmad-teach-me-testing`                                   |",
         "| Correction route  | `bmad-correct-course`, `bmad-retrospective`                                                           |"),
        ("- **testing** — the TEA toolbox feeds the quality record's mechanical checks (`bmad-testarch-atdd`, `-automate`, `-ci`, `-framework`, `-nfr`, `-test-design`, `-test-review`, `-trace` and `bmad-qa-generate-e2e-tests` → `bmad-quality-record`; run-key prefixes `test.<tool>.`); TEA Academy (`bmad-teach-me-testing`, prefix `teach.`) routes its graduate into the chain it taught (`bmad-testarch-framework`). A feeder **adds evidence to the existing QR record**, it does not open its own record and does not stamp a methodology position. The only TEA workflow that peeks is `bmad-testarch-framework` — the academy hands off to it.\n",
         ""),
    ],
    "KILAVUZ.md": [
        ("- **bmad-**\\* (çekirdek): PRD, mimari, story, sprint, dev-story/quick-dev/dev-auto, code-review, testarch-*, QA e2e, TEA, retrospective, correct-course, loop-*, eval-runner, forge-idea, brainstorming, araştırma skill'leri.",
         "- **bmad-**\\* (çekirdek): PRD, mimari, story, sprint, dev-story/quick-dev/dev-auto, code-review, retrospective, correct-course, loop-*, eval-runner, forge-idea, brainstorming, araştırma skill'leri."),
        ("- **Feeder (9, mevcut QR'ı besler):** `bmad-testarch-*` (atdd, automate, ci, framework, nfr, test-design, test-review, trace), `bmad-qa-generate-e2e-tests`.\n",
         ""),
        ("| Kalite            | `bmad-code-review`, `bmad-quality-record`, `bmad-testarch-*`, `bmad-qa-generate-e2e-tests`     |",
         "| Kalite            | `bmad-code-review`, `bmad-quality-record`                                                     |"),
        ("| Düzeltme rotası   | `bmad-correct-course`, `bmad-retrospective`, `bmad-teach-me-testing`                           |",
         "| Düzeltme rotası   | `bmad-correct-course`, `bmad-retrospective`                                                   |"),
        ("- **testing** — TEA araç kutusu kalite kaydının mekanik kontrollerini besler (`bmad-testarch-atdd`, `-automate`, `-ci`, `-framework`, `-nfr`, `-test-design`, `-test-review`, `-trace` ve `bmad-qa-generate-e2e-tests` → `bmad-quality-record`; run-key önekleri `test.<araç>.`); TEA Academy (`bmad-teach-me-testing`, önek `teach.`) mezunu öğrettiği zincire yönlendirir (`bmad-testarch-framework`). Besleyici **mevcut QR kaydına kanıt ekler**, kendi kaydını açmaz ve metodoloji pozisyonu damgalamaz. Peek yapan tek TEA workflow'u `bmad-testarch-framework`'tür — academy ona devreder.\n",
         ""),
    ],
    "AGENT-GUIDE.md": [
        ("| `Add tests / raise coverage / wire CI checks.` | `bmad-testarch-*`, `bmad-qa-generate-e2e-tests` |\n",
         ""),
    ],
    "AGENT-GUIDE-TR.md": [
        ("| `Add tests / raise coverage / wire CI checks.` | `bmad-testarch-*`, `bmad-qa-generate-e2e-tests` |\n",
         ""),
    ],
}

for rel, pairs in EDITS.items():
    p = pathlib.Path(rel)
    s = p.read_text(encoding="utf-8")
    for old, new in pairs:
        if old not in s:
            print(f"SKIP {rel}: {old.splitlines()[0][:60]}")
            continue
        s = s.replace(old, new, 1)
        print(f"OK   {rel}")
    p.write_text(s, encoding="utf-8")

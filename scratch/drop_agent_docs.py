"""Strip the bmad-agent-* prose from the runbooks and the manifesto table."""
import pathlib

EDITS = {
    "GUIDE.md": [
        ("- **Producer (8, creates/updates records):** `bmad-dev-story`, `bmad-quick-dev`, `bmad-dev-auto`, `bmad-agent-dev`, `bmad-code-review`, `bmad-create-story`, `bmad-sprint-planning`, `bmad-check-implementation-readiness`.",
         "- **Producer (7, creates/updates records):** `bmad-dev-story`, `bmad-quick-dev`, `bmad-dev-auto`, `bmad-code-review`, `bmad-create-story`, `bmad-sprint-planning`, `bmad-check-implementation-readiness`."),
        ("- 1 of them is an agent-principles surface (`bmad-agent-dev` — inside the BRIDGE `[agent].principles`).\n",
         ""),
        ("| Write a PRD       | `bmad-prd`, `bmad-create-prd`, `bmad-validate-prd`, `bmad-agent-pm`                                    |",
         "| Write a PRD       | `bmad-prd`, `bmad-create-prd`, `bmad-validate-prd`                                                    |"),
        ("| Architecture      | `bmad-architecture`, `bmad-create-architecture`, `bmad-agent-architect`                                |",
         "| Architecture      | `bmad-architecture`, `bmad-create-architecture`                                                       |"),
        ("| Fast code         | `bmad-quick-dev` (single story), `bmad-dev-auto` (batch), `bmad-agent-dev`                             |",
         "| Fast code         | `bmad-quick-dev` (single story), `bmad-dev-auto` (batch)                                               |"),
        ("- **alt\\_dev** — alternative routes of the development stage: `bmad-quick-dev` (`quickdev.`), `bmad-dev-auto` (`devauto.`) and the dev persona `bmad-agent-dev` (`agentdev.`; its BRIDGE carries S→QR→PR) converge on the delivery relay's terminal gate `bmad-code-review`.",
         "- **alt\\_dev** — alternative routes of the development stage: `bmad-quick-dev` (`quickdev.`) and `bmad-dev-auto` (`devauto.`) converge on the delivery relay's terminal gate `bmad-code-review`."),
        ("- **Routing personas** (`bmad-agent-pm` / `-analyst` / `-architect` / `-ux-designer` / `-tech-writer` — the persona template: identity + menu + dispatch) are the session's front door:",
         "- **Routing personas** (`bmad-tea` — the persona template: identity + menu + dispatch) are the session's front door:"),
        ("- **Tool (`bmad-agent-builder`, `bmad-document-project`,",
         "- **Tool (`bmad-document-project`,"),
        ("produces an output with no chain record (an agent skill, a project document, an installed module, a progress file)",
         "produces an output with no chain record (a project document, an installed module, a progress file)"),
    ],
    "KILAVUZ.md": [
        ("- **Producer (8, kayıt yaratır/günceller):** `bmad-dev-story`, `bmad-quick-dev`, `bmad-dev-auto`, `bmad-agent-dev`, `bmad-code-review`, `bmad-create-story`, `bmad-sprint-planning`, `bmad-check-implementation-readiness`.",
         "- **Producer (7, kayıt yaratır/günceller):** `bmad-dev-story`, `bmad-quick-dev`, `bmad-dev-auto`, `bmad-code-review`, `bmad-create-story`, `bmad-sprint-planning`, `bmad-check-implementation-readiness`."),
        ("- 1'i agent-principles yüzeyi (`bmad-agent-dev` — BRIDGE `[agent].principles` içinde).\n",
         ""),
        ("| PRD yaz           | `bmad-prd`, `bmad-create-prd`, `bmad-validate-prd`, `bmad-agent-pm`                            |",
         "| PRD yaz           | `bmad-prd`, `bmad-create-prd`, `bmad-validate-prd`                                            |"),
        ("| Mimari            | `bmad-architecture`, `bmad-create-architecture`, `bmad-agent-architect`                        |",
         "| Mimari            | `bmad-architecture`, `bmad-create-architecture`                                               |"),
        ("| Hızlı kod         | `bmad-quick-dev` (tek story), `bmad-dev-auto` (toplu), `bmad-agent-dev`                        |",
         "| Hızlı kod         | `bmad-quick-dev` (tek story), `bmad-dev-auto` (toplu)                                         |"),
        ("- **alt\\_dev** — geliştirme aşamasının alternatif yolları: `bmad-quick-dev` (`quickdev.`), `bmad-dev-auto` (`devauto.`) ve dev personası `bmad-agent-dev` (`agentdev.`; BRIDGE'i S→QR→PR taşır) teslim rölesinin terminal kapısı `bmad-code-review`'e yakınsar.",
         "- **alt\\_dev** — geliştirme aşamasının alternatif yolları: `bmad-quick-dev` (`quickdev.`) ve `bmad-dev-auto` (`devauto.`) teslim rölesinin terminal kapısı `bmad-code-review`'e yakınsar."),
        ("- **Yönlendirici personalar** (`bmad-agent-pm` / `-analyst` / `-architect` / `-ux-designer` / `-tech-writer` — persona şablonu: kimlik + menü + dispatch) oturumun ön kapısıdır:",
         "- **Yönlendirici personalar** (`bmad-tea` — persona şablonu: kimlik + menü + dispatch) oturumun ön kapısıdır:"),
        ("- **Araç (`bmad-agent-builder`, `bmad-document-project`,",
         "- **Araç (`bmad-document-project`,"),
        ("zincir kaydı olmayan bir çıktı üretir (ajan skill'i, proje dokümanı, kurulu modül, ilerleme dosyası)",
         "zincir kaydı olmayan bir çıktı üretir (proje dokümanı, kurulu modül, ilerleme dosyası)"),
    ],
    "docs/bmad/research-methodology.md": [
        ("| bmad-agent-dev | ✅ Mode A | Code |\n", ""),
    ],
    "custom/config.toml": [
        ("# [agents.bmad-agent-pm]\n", "# [agents.<your-agent>]\n"),
    ],
    "custom/bmad-quick-dev.toml": [
        ("# verified experiment approval before implementing, exactly like bmad-agent-dev.",
         "# verified experiment approval before implementing, exactly like bmad-dev-story."),
    ],
    "custom/bmad-dev-story.toml": [
        ("# verified experiment approval before implementing, exactly like bmad-agent-dev.",
         "# verified experiment approval before implementing, exactly like bmad-quick-dev."),
    ],
    "custom/bmad-dev-auto.toml": [
        ("# verified experiment approval before implementing, exactly like bmad-agent-dev.",
         "# verified experiment approval before implementing, exactly like bmad-dev-story."),
    ],
    "custom/bmad-market-research.toml": [
        ("# This workflow is directly invocable (/bmad-market-research), independent of bmad-agent-analyst.",
         "# This workflow is directly invocable (/bmad-market-research)."),
    ],
    "custom/bmad-document-project.toml": [
        ("# This workflow is directly invocable (/bmad-document-project), independent of bmad-agent-tech-writer.",
         "# This workflow is directly invocable (/bmad-document-project)."),
    ],
    "skills/bmad-customize/SKILL.md": [
        ("Scope v1: per-skill `[agent]` overrides (`bmad-agent-<role>.toml` / `.user.toml`) and per-skill `[workflow]` overrides",
         "Scope v1: per-skill `[agent]` overrides (`<agent-skill>.toml` / `.user.toml`) and per-skill `[workflow]` overrides"),
        ("- Every workflow a given agent runs → agent surface (e.g. `bmad-agent-pm.toml` with `persistent_facts`, `principles`).",
         "- Every workflow a given agent runs → agent surface (e.g. `<agent-skill>.toml` with `persistent_facts`, `principles`)."),
    ],
    "skills/bmad-party-mode/scripts/resolve_party.py": [
        ('    """Short alias for an installed agent code: bmad-agent-analyst -> analyst."""\n'
         '    for prefix in ("bmad-agent-", "bmad-"):',
         '    """Short alias for an installed agent code: bmad-tea -> tea."""\n'
         '    for prefix in ("bmad-agent-", "bmad-"):'),
    ],
    "skills/bmad-forge-idea/scripts/resolve_personas.py": [
        ('    """Short alias for an installed agent code: bmad-agent-analyst -> analyst."""\n'
         '    for prefix in ("bmad-agent-", "bmad-"):',
         '    """Short alias for an installed agent code: bmad-tea -> tea."""\n'
         '    for prefix in ("bmad-agent-", "bmad-"):'),
    ],
}

for rel, pairs in EDITS.items():
    p = pathlib.Path(rel)
    s = p.read_text(encoding="utf-8")
    for old, new in pairs:
        if old not in s:
            print(f"SKIP {rel}: {old.splitlines()[0][:65]}")
            continue
        s = s.replace(old, new, 1)
        print(f"OK   {rel}")
    p.write_text(s, encoding="utf-8")

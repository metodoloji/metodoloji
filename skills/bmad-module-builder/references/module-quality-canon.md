# Module Quality Canon

The quality bar for BMad modules. Every module produced by the Module Builder must meet these standards. This canon is the authority — do not restate it elsewhere; cite it.

---

## §1 What Makes a Module

A module is a coherent bundle of skills that share a purpose, a configuration surface, and an installation path. A module is NOT:
- A collection of unrelated skills bundled for convenience
- A single skill with a different directory layout
- A wrapper around another module

**Test:** Can you describe the module's purpose in one sentence without using "and"? If not, it is two modules.

---

## §2 Structural Requirements

### §2.1 Multi-skill Module
A multi-skill module MUST have:
- A dedicated `-setup` skill that handles installation and config merging
- `assets/module.yaml` in the setup skill defining module metadata, skills, and agents
- `assets/module-help.csv` for the BMad Help catalog
- `scripts/merge-config.py` and `scripts/merge-help-csv.py` (or underscore equivalents)
- Each skill directory with its own `SKILL.md` and `customize.toml`

### §2.2 Standalone Module
A standalone module (single skill) MUST have:
- `assets/module-setup.md` embedded in the skill
- `assets/module.yaml` defining module metadata
- `assets/module-help.csv` for the catalog
- Merge scripts (same as multi-skill)

### §2.3 Forbidden
- No skill without a `customize.toml` (even if it only has defaults)
- No CSV entry without a matching skill directory
- No orphaned scripts (every script must be referenced from a SKILL.md or setup flow)

---

## §3 Skill Quality Within a Module

Each skill in the module must meet the bar defined in the agent-builder's and workflow-builder's quality canons. At minimum:

### §3.1 SKILL.md
- **Lean** — every line earns its place against: "would a capable model do this without being told?"
- **Outcome-driven** — overview states stance, outcome, and consumer
- **Progressive disclosure** — SKILL.md routes, references load only when needed
- **No ceremony** — no "be helpful", no "ask clarifying questions" boilerplate

### §3.2 Customization
- `customize.toml` exists with sensible defaults
- Three-layer merge respected (skill → team → personal)
- No hardcoded values that belong in config

### §3.3 References
- Every `references/X.md` path resolves to an existing file
- No broken cross-links between reference files
- References are carved by relevance — no file loaded that isn't needed

---

## §4 CSV Quality

Each entry in `module-help.csv` must be:

| Criterion | Standard |
|-----------|----------|
| **Completeness** | Every capability of every skill has its own row |
| **Accuracy** | Description matches what the skill actually does |
| **Action-oriented** | Starts with a verb (Create, Validate, Scaffold, Analyze) |
| **Concise** | One sentence, no filler, no vague language |
| **Menu codes** | Intuitive, memorable, unique across the module |
| **Ordering** | preceded-by/followed-by references form a coherent flow |
| **Required flags** | Required skills marked `required=true`; optional skills `false` |

---

## §5 Agent Roster (if applicable)

If `module.yaml` has an `agents:` block:

- Each `code` matches a skill directory basename
- `title`, `icon`, `description` are non-empty
- `name` is either populated or explicitly empty (valid for First-Breath-named agents)
- A corresponding `customize.toml` exists with an `[agent]` block agreeing with the roster
- No icon drift between roster and customize.toml

---

## §6 Installation Contract

A module must install cleanly:

1. **Setup skill** runs without errors in both interactive and headless modes
2. **Config merge** does not clobber existing user overrides
3. **Help CSV merge** produces valid catalog entries
4. **Idempotency** — running setup twice produces the same result as running once
5. **Teardown** — removing the module does not leave orphaned config in `custom/`

---

## §7 The Leanness Test

Apply the same test the agent-builder and workflow-builder use:

> If a capable model would do this correctly without being told, the line is friction and stays out.

This applies to:
- SKILL.md instructions (the primary leanness surface)
- Reference file content (no restating the canon)
- CSV descriptions (no padding, no boilerplate)
- Template content (no mandatory sections that add no value)

---

## §8 Common Failure Modes

| Failure | Symptom | Fix |
|---------|---------|-----|
| **Bundle without purpose** | Can't describe module in one sentence | Split into separate modules |
| **Missing customization** | Skill has no `customize.toml` | Add with defaults |
| **Broken CSV** | Help catalog shows wrong descriptions | Re-run validate, fix entries |
| **Orphaned references** | `references/X.md` mentioned but doesn't exist | Create or remove reference |
| **Over-specified SKILL.md** | Model ignores instructions because there are too many | Apply the cut test |
| **Drift between roster and agents** | Icon or title mismatch | Regenerate roster from customize.toml |

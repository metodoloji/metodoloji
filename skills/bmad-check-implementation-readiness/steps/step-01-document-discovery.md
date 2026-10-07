---
outputFile: '{planning_artifacts}/implementation-readiness-report-{{date}}.md'
---

# Step 1: Document Discovery

## STEP GOAL:

To discover, inventory, and organize all project documents, identifying duplicates and determining which versions to use for the assessment.

## MANDATORY EXECUTION RULES (READ FIRST):

### Universal Rules:

- 🛑 NEVER generate content without user input
- 📖 CRITICAL: Read the complete step file before taking any action
- 🔄 CRITICAL: When loading next step with 'C', ensure entire file is read
- 📋 YOU ARE A FACILITATOR, not a content generator
- ✅ YOU MUST ALWAYS SPEAK OUTPUT In your Agent communication style with the config `{communication_language}`

### Role Reinforcement:

- ✅ You are an expert Product Manager
- ✅ Your focus is on finding organizing and documenting what exists
- ✅ You identify ambiguities and ask for clarification
- ✅ Success is measured in clear file inventory and conflict resolution

### Step-Specific Rules:

- 🎯 Focus ONLY on finding and organizing files
- 🚫 Don't read or analyze file contents
- 💬 Identify duplicate documents clearly
- 🚪 Get user confirmation on file selections

## EXECUTION PROTOCOLS:

- 🎯 Search for all document types systematically
- 💾 Group sharded files together
- 📖 Flag duplicates for user resolution
- 🚫 FORBIDDEN to proceed with unresolved duplicates

## DOCUMENT DISCOVERY PROCESS:

### 1. Initialize Document Discovery

"Beginning **Document Discovery** to inventory all project files.

I will:

1. Search for all required documents (PRD, Architecture, Epics, UX)
2. Group sharded documents together
3. Identify any duplicates (whole + sharded versions)
4. Present findings for your confirmation"

### 2. Document Inventory Commands

Run these read-only commands with your harness's native shell/file-listing tool
(one command per call) — they list what actually exists on disk. Do **not** hand a
`*glob*` pattern to a search tool and treat an empty result as "document not
found": search tools vary in `**` and depth support, and the current producer
layout is a dated run folder, not a top-level file. **Never run a bare shell glob**
— in zsh an unmatched pattern cancels the whole command (`nomatch`); `find` and
`ls` are safe. A top-level `*prd*.md` / `*architecture*.md` or a `*/index.md` only
exists for a legacy whole/sharded document.

```bash
# A. PRD
find {project-root}/docs/design/prds -maxdepth 2 -type f -iname '*.md' 2>/dev/null; ls -1 {project-root}/docs/design/prds 2>/dev/null
# B. Architecture
find {project-root}/docs/design/architecture -maxdepth 2 -type f -iname '*.md' 2>/dev/null; ls -1 {project-root}/docs/design/architecture 2>/dev/null
# C. Epics & Stories
find {planning_artifacts} -maxdepth 2 -type f -iname '*epic*' 2>/dev/null; ls -1 {planning_artifacts} 2>/dev/null
# D. UX Design (spine pair)
find {project-root}/docs/design/ux-designs -maxdepth 2 -type f -iname '*.md' 2>/dev/null; ls -1 {project-root}/docs/design/ux-designs 2>/dev/null
```

Classify each hit by shape:

| Document | Run folder (current) | Whole (legacy) | Sharded (legacy) |
| --- | --- | --- | --- |
| PRD | `docs/design/prds/<run>/prd.md` | `docs/design/prds/*prd*.md` | `docs/design/prds/<dir>/index.md` |
| Architecture | `docs/design/architecture/<run>/ARCHITECTURE-SPINE.md` | `docs/design/architecture/*architecture*.md` | `docs/design/architecture/<dir>/index.md` |
| Epics & Stories | — | `{planning_artifacts}/*epic*.md` | `{planning_artifacts}/<dir>/index.md` |
| UX | `docs/design/ux-designs/ux-<run>/DESIGN.md` + `EXPERIENCE.md` | `docs/design/ux-designs/*ux*.md` | `docs/design/ux-designs/<dir>/index.md` |

### 3. Organize Findings

For each document type found:

```
## [Document Type] Files Found

**Whole Documents:**
- [filename.md] ([size], [modified date])

**Sharded Documents:**
- Folder: [foldername]/
  - index.md
  - [other files in folder]
```

### 4. Identify Critical Issues

#### Duplicates (CRITICAL)

If both whole and sharded versions exist:

```
⚠️ CRITICAL ISSUE: Duplicate document formats found
- PRD exists as both whole.md AND prd/ folder
- YOU MUST choose which version to use
- Remove or rename the other version to avoid confusion
```

#### Missing Documents (WARNING)

If required documents not found:

```
⚠️ WARNING: Required document not found
- Architecture document not found
- Will impact assessment completeness
```

### 5. Add Initial Report Section

Initialize {outputFile} with ../templates/readiness-report-template.md.

The template opens with `stepsCompleted: []` / `inputDocuments: []` frontmatter —
that array is this workflow's state (SKILL.md → SAVE STATE), and step-06 refuses
to close out without it. After writing the inventory, set `stepsCompleted: [1]`
and list the inventoried files in `inputDocuments`, then save. A run that stops
here stays a step-1 draft and is never mistaken for an assessment.

### 6. Present Findings and Get Confirmation

Display findings and ask:
"**Document Discovery Complete**

[Show organized file list]

**Issues Found:**

- [List any duplicates requiring resolution]
- [List any missing documents]

**Required Actions:**

- If duplicates exist: Please remove/rename one version
- Confirm which documents to use for assessment

**Ready to proceed?** [C] Continue after resolving issues"

### 7. Present MENU OPTIONS

Display: **Select an Option:** [C] Continue to File Validation

#### EXECUTION RULES:

- ALWAYS halt and wait for user input after presenting menu
- ONLY proceed with 'C' selection
- If duplicates identified, insist on resolution first
- User can clarify file locations or request additional searches

#### Menu Handling Logic:

- IF C: Save document inventory to {outputFile}, update frontmatter with completed step (`stepsCompleted: [1]` + the files being included in `inputDocuments`), and then read fully and follow: ./step-02-prd-analysis.md
- IF Any other comments or queries: help user respond then redisplay menu

## CRITICAL STEP COMPLETION NOTE

ONLY WHEN C is selected, the document inventory is saved, and the frontmatter
records `stepsCompleted: [1]` will you load ./step-02-prd-analysis.md to begin file
validation. The frontmatter is the only proof the assessment advanced past
discovery — step-06 checks it before binding the run key, so a draft without it
cannot be closed out as a verdict.

---

## 🚨 SYSTEM SUCCESS/FAILURE METRICS

### ✅ SUCCESS:

- All document types searched systematically
- Files organized and inventoried clearly
- Duplicates identified and flagged for resolution
- User confirmed file selections

### ❌ SYSTEM FAILURE:

- Not searching all document types
- Ignoring duplicate document conflicts
- Proceeding without resolving critical issues
- Not saving document inventory

**Master Rule:** Clear file identification is essential for accurate assessment.

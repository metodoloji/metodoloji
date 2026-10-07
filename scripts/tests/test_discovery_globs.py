"""Contract test: a consumer's discovery globs must match what the producer writes.

The 2026-10-01 graph-engineering-arge session hit this exactly. The epics
skill's step-01 searched `prds/*prd*.md` and `prds/*prd*/index.md`, while
`bmad-prd` writes `prds/prd-{project_name}-{date}/prd.md` — no pattern matched,
so Epics reported the PRD as missing although the run had just produced it. The
spine search failed the same way (`architecture/*architecture*/index.md` vs
`architecture-{project_name}-{date}/ARCHITECTURE-SPINE.md`).

Neither side is hardcoded here: the canonical artifact path is derived from the
producer's OWN `customize.toml` (`*_output_path` + `run_folder_pattern` + the
artifact file name), so changing a producer layout fails the consumers here
instead of in a real run. `*` never crosses `/` (shell-glob semantics) — a
`*/index.md` pattern does not match a run-folder file, which is the whole bug.
"""

import re
import tomllib
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]

# The spine file name is not the test's to invent: `lint_spine.py` reads it by
# that exact constant, so the discovery patterns must find the name the linter
# already enforces (the transcript: "ARCHITECTURE-SPINE.md şablonun kendi verdiği
# isim — lint_spine.py bile o adı arıyor").
_LINTER = PLUGIN / "skills/bmad-architecture/scripts/lint_spine.py"
_ARCH_ARTIFACT = re.search(
    r'^SPINE\s*=\s*["\']([^"\']+)["\']',
    _LINTER.read_text(encoding="utf-8"),
    re.MULTILINE,
).group(1)

# family -> (producer customize.toml, output-path key, artifact file names)
PRODUCERS = {
    "prds": ("skills/bmad-prd/customize.toml", "prd_output_path", ("prd.md",)),
    "architecture": ("skills/bmad-architecture/customize.toml", "spine_output_path",
                     (_ARCH_ARTIFACT,)),
    "ux-designs": ("skills/bmad-ux/customize.toml", "ux_output_path",
                   ("DESIGN.md", "EXPERIENCE.md")),
}

# Files whose patterns must find the produced artifact when they reference the family.
CONSUMERS = (
    "skills/bmad-create-epics-and-stories/steps/step-01-validate-prerequisites.md",
    "skills/bmad-create-epics-and-stories/steps/step-02-design-epics.md",
    "skills/bmad-check-implementation-readiness/steps/step-01-document-discovery.md",
    "skills/bmad-check-implementation-readiness/steps/step-04-ux-alignment.md",
    "skills/gds-create-epics-and-stories/steps/step-01-validate-prerequisites.md",
    "skills/gds-check-implementation-readiness/steps/step-01-document-discovery.md",
    "skills/gds-check-implementation-readiness/steps/step-04-ux-alignment.md",
)

_PROJECT_ROOT_GLOB_RE = re.compile(r"`([^`]*\{project-root\}[^`]*)`")


def _canonical_paths(family: str, project: str = "acme", date: str = "2026-10-01") -> list[str]:
    """Artifact paths the producer creates, relative to the project root."""
    customize, key, files = PRODUCERS[family]
    wf = tomllib.loads((PLUGIN / customize).read_text(encoding="utf-8"))["workflow"]
    base = wf[key].replace("{project-root}/", "")
    folder = wf["run_folder_pattern"].replace("{project_name}", project).replace("{date}", date)
    return [f"{base}/{folder}/{name}" for name in files]


def _glob_matches(pattern: str, rel_path: str) -> bool:
    """Shell-glob semantics: `*` and `?` never cross a `/` separator."""
    out = []
    for ch in pattern:
        if ch == "*":
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(ch))
    return re.fullmatch("".join(out), rel_path) is not None


def _patterns(text: str) -> list[str]:
    """Every `{project-root}/…` pattern the file offers, as project-relative globs."""
    found = []
    for raw in _PROJECT_ROOT_GLOB_RE.findall(text):
        prefix = "{project-root}/"
        if not raw.startswith(prefix):
            continue
        found.append(raw[len(prefix):])
    return found


def test_producers_declare_a_run_folder_pattern():
    """The producers this contract leans on must keep declaring their layout."""
    for family in PRODUCERS:
        paths = _canonical_paths(family)
        assert paths and all("/" in p for p in paths), (family, paths)


def test_architecture_artifact_name_is_shared_with_the_linter():
    """The hardcoded spine name and the skill docs must agree with the linter."""
    assert _ARCH_ARTIFACT == "ARCHITECTURE-SPINE.md"
    headless = (PLUGIN / "skills/bmad-architecture/references/headless.md").read_text(
        encoding="utf-8"
    )
    assert _ARCH_ARTIFACT in headless


def test_every_consumer_referencing_a_family_can_find_it():
    """If a surface lists patterns for a family, one must reach the produced file.

    A surface that does not search a family at all (the GDS epics skill consumes
    a GDD, not a PRD) is skipped — the contract is about surfaces that claim to
    find the document, not about every file mentioning the word.

    Scans EVERY skill file, not the hand-written CONSUMERS tuple. That tuple is
    what let this bug ship twice: the 2026-10-01 fix repaired the files someone
    remembered, while `create-story/discover-inputs.md` (x2) and both
    `generate-project-context/step-01-discover.md` kept the stale pattern because
    nobody re-scanned. A consumer added tomorrow is covered here by default.
    """
    failures = []
    for path in sorted((PLUGIN / "skills").glob("**/*.md")):
        if "__pycache__" in path.parts:
            continue
        rel = str(path.relative_to(PLUGIN))
        patterns = _patterns(path.read_text(encoding="utf-8"))
        for family in PRODUCERS:
            family_patterns = [p for p in patterns if family in p]
            if not family_patterns:
                continue
            canonical = _canonical_paths(family)
            if not any(_glob_matches(p, c) for c in canonical
                       for p in family_patterns):
                failures.append(
                    f"{rel}: no {family} pattern reaches {canonical[0]} "
                    f"(listed: {family_patterns})")
    assert not failures, "\n".join(failures)


def test_the_legacy_patterns_are_still_offered():
    """Run-folder first, legacy whole/sharded retained — no silent removal."""
    epics = (PLUGIN / CONSUMERS[0]).read_text(encoding="utf-8")
    patterns = _patterns(epics)
    assert "docs/design/prds/*prd*.md" in patterns
    assert "docs/design/prds/*prd*/index.md" in patterns
    arch_patterns = [p for p in patterns if "architecture" in p]
    assert "docs/design/architecture/*architecture*.md" in arch_patterns


def test_star_does_not_cross_a_separator():
    # The semantics that made the shipped globs miss: `*/index.md` is one level.
    assert not _glob_matches("docs/design/prds/*prd*/index.md",
                             "docs/design/prds/prd-acme-2026-10-01/prd.md")
    assert _glob_matches("docs/design/prds/*prd*/*.md",
                         "docs/design/prds/prd-acme-2026-10-01/prd.md")

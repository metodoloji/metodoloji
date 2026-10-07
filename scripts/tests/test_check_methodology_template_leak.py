"""Contract test for scripts/check-methodology.sh CHECK 8 (template leakage).

A record is an artifact, not a template. A real Claude Code IR run shipped the
`templates/_template_IR.md` guidance into `docs/development/IR-003.md` — a
`> This template is used for …` banner and a duplicated `## Implementation
Readiness: …` title (B-006). CHECK 8 guards the class: a shipped/init-copied
TEMPLATE carrying meta-text is an ISSUE; a produced RECORD carrying it is a
WARN (visible, never silently rewritten).

These tests run the REAL script against a throwaway project root — the check is
bash, so the only honest test is the program the project runs. Only the CHECK 8
slice is asserted on: a throwaway root legitimately fails earlier checks.
"""

import os
import subprocess
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
SCRIPT = PLUGIN / "scripts" / "check-methodology.sh"


def _run_check8(project_root: Path) -> str:
    env = dict(os.environ)
    env["OPENHANDS_PROJECT_DIR"] = str(project_root)
    env.pop("CLAUDE_PROJECT_DIR", None)
    # Pin plugin-root resolution to this checkout (env vars would win over it).
    env.pop("CLAUDE_PLUGIN_ROOT", None)
    env.pop("METODOLOJI_PLUGIN_ROOT", None)
    r = subprocess.run(["sh", str(SCRIPT)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=180, env=env,
                       cwd=str(PLUGIN), stdin=subprocess.DEVNULL)
    out = (r.stdout or "") + (r.stderr or "")
    i = out.find("CHECK 8: Record Template Hygiene")
    assert i != -1, out
    j = out.find("SUMMARY", i)
    return out[i:j if j != -1 else len(out)]


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _has(section: str, sev: str, name: str) -> bool:
    return f"{sev}: {name} ->" in section


# ─── shipped templates must be clean (the guard scans {metodoloju-root}/templates) ───

def test_shipped_templates_are_meta_text_free(tmp_path):
    # No project-side templates are written: any ISSUE in the slice can only
    # come from the plugin's own canonical templates/ directory.
    section = _run_check8(tmp_path)
    assert "❌ ISSUE" not in section, section
    assert "templates checked: 5" in section, section


def test_clean_template_copy_is_not_an_issue(tmp_path):
    clean = (PLUGIN / "templates" / "_template_IR.md").read_text(encoding="utf-8")
    _write(tmp_path, "docs/development/_template_IR.md", clean)
    section = _run_check8(tmp_path)
    assert not _has(section, "❌ ISSUE", "_template_IR.md"), section


def test_template_copy_with_banner_is_an_issue(tmp_path):
    clean = (PLUGIN / "templates" / "_template_SP.md").read_text(encoding="utf-8")
    _write(tmp_path, "docs/development/_template_SP.md",
           clean + "\n> This template is used for Sprint (SP) records.\n")
    section = _run_check8(tmp_path)
    assert _has(section, "❌ ISSUE", "_template_SP.md"), section


# ─── produced records warn (historical records are visible, not rewritten) ───

def test_clean_record_is_not_warned(tmp_path):
    _write(tmp_path, "docs/development/IR-001.md",
           "# Implementation Readiness: IR-001 — clean\n\n- **Date:** 2026-10-05\n")
    section = _run_check8(tmp_path)
    assert not _has(section, "⚠️  WARN", "IR-001.md"), section


def test_record_with_template_banner_warns(tmp_path):
    _write(tmp_path, "docs/development/IR-002.md",
           "# Implementation Readiness: IR-002 — leaky\n\n"
           "> This template is used for Implementation Readiness (IR) records.\n"
           "> Represents Development Gate 1: checks whether research findings are ready.\n\n"
           "- **Date:** 2026-10-05\n")
    section = _run_check8(tmp_path)
    assert _has(section, "⚠️  WARN", "IR-002.md"), section


def test_record_with_duplicated_title_warns(tmp_path):
    _write(tmp_path, "docs/development/SP-003.md",
           "# Sprint: SP-003 — leaky\n\n## Sprint: SP-003 — leaky\n\n"
           "- **Date:** 2026-10-05\n")
    section = _run_check8(tmp_path)
    assert _has(section, "⚠️  WARN", "SP-003.md"), section


def test_record_with_unfilled_placeholder_warns(tmp_path):
    _write(tmp_path, "docs/development/PR-004.md",
           "# Production Readiness: PR-004 — leaky\n\n- **Date:** 2026-10-05\n"
           "- **Release scope:** [QR-id list — what goes into this release]\n")
    section = _run_check8(tmp_path)
    assert _has(section, "⚠️  WARN", "PR-004.md"), section


def test_quality_record_is_scanned_too(tmp_path):
    _write(tmp_path, "docs/quality/QR-017.md",
           "# Quality Review: QR-017 — leaky\n\n- **Date:** 2026-10-05\n"
           "- **Changed files:** [How many files, how many +/− lines]\n")
    section = _run_check8(tmp_path)
    assert _has(section, "⚠️  WARN", "QR-017.md"), section

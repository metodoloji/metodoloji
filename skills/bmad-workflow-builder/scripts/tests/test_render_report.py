#!/usr/bin/env python3
"""Tests for scripts/render_report.py — the deterministic report renderer.

Covers: valid island injection, refusal on malformed JSON, refusal on the
placeholder subject, the --md archival rendering, and that both shipped
shells carry a parseable placeholder island.
Run with: python3 -m pytest test_render_report.py
(or plain `python3 test_render_report.py` for a lightweight self-check).

Renders are run in-process (import render_report, swap sys.argv, call main())
so the suite is deterministic on Windows — subprocess-heavy tests hit
DuplicateHandle (WinError 6) exhaustion.
"""
import json
import re
import sys
import tempfile
from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parents[3]
SCRIPT = SKILLS_DIR / "bmad-workflow-builder" / "scripts" / "render_report.py"
SHELLS = [
    SKILLS_DIR / "bmad-workflow-builder" / "assets" / "report-shell.html",
    SKILLS_DIR / "bmad-agent-builder" / "assets" / "report-shell.html",
]
ISLAND_RE = re.compile(
    r'<script[^>]*\bid="report-data"[^>]*>(.*?)</script>', re.DOTALL
)

VALID_DATA = {
    "schema_version": 2,
    "subject": "skills/example-skill",
    "generated": "2026-06-10",
    "verdict": "One ceremony section; otherwise sound.",
    "grade": "good",
    "summary": "Solid structure and clean wiring. The main opportunity is one over-scripted reference.",
    "standards": {
        "canon": "/abs/skills/bmad-workflow-builder/references/prompt-quality-canon.md",
        "principles": "/abs/skills/bmad-workflow-builder/references/skill-quality-principles.md",
        "scripts": "/abs/skills/bmad-workflow-builder/references/script-standards.md",
    },
    "themes": [
        {
            "title": "Scripted sequences where goals suffice",
            "root_cause": "Steps are numbered without true ordering dependencies.",
            "finding_ids": ["leanness-1"],
            "action": "Replace ordered lists with goal sentences.",
        }
    ],
    "strengths": ["Frontmatter and routing map are exemplary."],
    "recommendations": [
        {"rank": 1, "action": "De-script the finalize section.", "resolves": ["leanness-1"]}
    ],
    "findings": [
        {
            "id": "leanness-1",
            "lens": "leanness",
            "severity": "high",
            "title": "Numbered finalize steps are decoration",
            "location": "references/build-process.md:finalize",
            "evidence": "No step depends on a prior step's output.",
            "recommendation": "Replace with a single goal sentence.",
        }
    ],
}


def _load_render_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("render_report_mod", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_render(args, capsys):
    """Run render_report.main() in-process; return (rc, stdout).

    fail() raises SystemExit(1), so a failing render surfaces as an exception
    here — the callers assert that via pytest.raises(SystemExit).
    """
    mod = _load_render_module()
    old_argv = sys.argv
    sys.argv = ["render_report.py", *[str(a) for a in args]]
    try:
        rc = mod.main()
    finally:
        sys.argv = old_argv
    return rc, capsys.readouterr().out


def test_valid_island_injection(capsys):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        findings = tmp / "findings.json"
        out = tmp / "report.html"
        findings.write_text(json.dumps(VALID_DATA), encoding="utf-8")

        rc, stdout = run_render([findings, "--shell", SHELLS[0], "-o", out], capsys)
        assert rc == 0
        html = out.read_text(encoding="utf-8")

        match = ISLAND_RE.search(html)
        assert match, "rendered HTML has no report-data island"
        island = json.loads(match.group(1))
        assert island["subject"] == "skills/example-skill"
        assert island["findings"][0]["id"] == "leanness-1"
        assert island["standards"]["canon"].endswith("prompt-quality-canon.md")
        assert "__PLACEHOLDER__" not in match.group(1)

        parsed = json.loads(stdout)
        assert parsed["counts"] == {"critical": 0, "high": 1, "medium": 0, "low": 0}
        assert parsed["grade"] == "good"


def test_refuses_bad_json(capsys):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        findings = tmp / "findings.json"
        out = tmp / "report.html"
        findings.write_text("{ this is not json", encoding="utf-8")

        try:
            run_render([findings, "--shell", SHELLS[0], "-o", out], capsys)
        except SystemExit as exc:
            assert exc.code != 0
        else:
            raise AssertionError("expected refusal (SystemExit)")
        assert "not valid JSON" in capsys.readouterr().err
        assert not out.exists(), "refused render must not write output"


def test_refuses_placeholder_subject(capsys):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        findings = tmp / "findings.json"
        out = tmp / "report.html"
        data = dict(VALID_DATA, subject="__PLACEHOLDER__")
        findings.write_text(json.dumps(data), encoding="utf-8")

        try:
            run_render([findings, "--shell", SHELLS[0], "-o", out], capsys)
        except SystemExit as exc:
            assert exc.code != 0
        else:
            raise AssertionError("expected refusal (SystemExit)")
        assert "placeholder" in capsys.readouterr().err.lower()
        assert not out.exists(), "refused render must not write output"


def test_md_output(capsys):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        findings = tmp / "findings.json"
        out = tmp / "report.html"
        md = tmp / "report.md"
        findings.write_text(json.dumps(VALID_DATA), encoding="utf-8")

        rc, _ = run_render([findings, "--shell", SHELLS[0], "-o", out, "--md", md], capsys)
        assert rc == 0
        text = md.read_text(encoding="utf-8")
        assert "# Analysis Report: skills/example-skill" in text
        assert "**Grade: Good**" in text
        assert "## Themes" in text
        assert "Scripted sequences where goals suffice" in text
        assert "## Strengths" in text
        assert "## Recommendations" in text
        assert "### High (1)" in text
        assert "leanness-1" in text


def test_shipped_shells_carry_placeholder_island():
    for shell in SHELLS:
        match = ISLAND_RE.search(shell.read_text(encoding="utf-8"))
        assert match, f"{shell} has no report-data island"
        island = json.loads(match.group(1))
        assert island["subject"] == "__PLACEHOLDER__", (
            f"{shell} ships a non-placeholder island; a failed injection "
            "would show its contents as real findings"
        )
        assert island["findings"] == []


def test_render_script_copies_identical():
    other = SKILLS_DIR / "bmad-agent-builder" / "scripts" / "render_report.py"
    assert SCRIPT.read_bytes() == other.read_bytes(), (
        "render_report.py copies have drifted between the two builder skills"
    )


if __name__ == "__main__":
    # The capsys-parameterized tests run under pytest; the __main__ self-check
    # covers the two subprocess-free tests.
    test_shipped_shells_carry_placeholder_island()
    test_render_script_copies_identical()
    print("ok: render_report self-check passed (run full suite with pytest)")

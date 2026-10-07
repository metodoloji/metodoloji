"""Contract test for scripts/check-methodology.sh record-status classification.

CHECK 4 (experiments) and CHECK 5 (QR) classify each record as
APPROVED / REJECTED / PENDING. Those patterns must match the CANONICAL field
forms the gate and the templates actually write:

  experiments  - **Status:** completed | REJECTED   (gate-written)
               - **Decision:** APPROVED | REJECTED  (gate-written)
  QR           - **Status:** in-review | APPROVED | REJECTED | REVISED

A pattern that cannot match those forms silently reports every record as
"status unknown" — drift that already shipped once (the old greps looked for
`Status: APPROVED`, which the gate never writes, and `QR Status: pass`, which
the QR template never had). These tests read the shipped script and run its
real patterns against canonical sample lines and the shipped records.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[2]
SCRIPT = PLUGIN / "scripts" / "check-methodology.sh"
TEMPLATES = PLUGIN / "templates"


def _awk_binary() -> str:
    """Resolve awk: PATH first, then Git-for-Windows MSYS dirs (F5/T-08).

    The epic-lag logic lives in awk because check-methodology.sh runs it;
    the tests must run the SAME program, not a reimplementation.
    """
    found = shutil.which("awk")
    if found:
        return found
    for cand in (
        r"C:\Program Files\Git\usr\bin\awk.exe",
        r"C:\Program Files (x86)\Git\usr\bin\awk.exe",
    ):
        if os.path.isfile(cand):
            return cand
    pytest.skip("no awk available (PATH or Git-for-Windows MSYS)")
    raise AssertionError("unreachable")


def _section(start: str, end: str) -> str:
    text = SCRIPT.read_text(encoding="utf-8", errors="replace")
    i = text.index(start)
    j = text.index(end, i)
    return text[i:j]


def _patterns(section: str) -> list[str]:
    """grep patterns in source order (partial-match grep; -qE/-q alike)."""
    return re.findall(r'grep -qE? "([^"]+)"', section)


def _branch(patterns: list[str], text: str):
    """First matching pattern's index (the script's elif order), else None."""
    for i, p in enumerate(patterns):
        if re.search(p, text):
            return i
    return None


CHECK4 = _patterns(_section("CHECK 4: Experiment Records",
                           "CHECK 5: Quality Records"))
CHECK5 = _patterns(_section("CHECK 5: Quality Records", "CHECK 6:"))


def test_check4_has_the_three_classification_branches():
    assert len(CHECK4) == 3, CHECK4


def test_check4_patterns_match_canonical_gate_written_fields():
    approved, rejected, pending = 0, 1, 2
    # the exact shape the gate writes on approval (a real shipped record)
    gate_line = "- **Decision:** APPROVED — H-011: measured=1.0 >= threshold=0.9"
    assert _branch(CHECK4, gate_line) == approved
    # the exact shape the gate writes on rejection
    assert _branch(CHECK4, "- **Status:** REJECTED") == rejected
    assert _branch(CHECK4, "- **Decision:** REJECTED — threshold not met") == rejected
    # never-run / pending records
    assert _branch(CHECK4, "- **Status:** planned") == pending
    assert _branch(CHECK4, "- **Status:** PENDING") == pending


def test_check4_classifies_every_shipped_record():
    """No shipped experiment record may fall through to 'status unknown'.

    Record-less plugin repo (records pruned 2026-10-06): when nothing ships, run
    the classifier over the canonical written forms instead, so the contract
    ("every canonical record classifies") still runs and is not vacuous.
    """
    records = sorted((PLUGIN / "docs" / "experiments").glob("E-*.md"))
    if records:
        bodies = [rec.read_text(encoding="utf-8", errors="replace") for rec in records]
    else:
        bodies = [
            "- **Decision:** APPROVED — H-001: measured=1.0 >= threshold=0.9",
            "- **Status:** REJECTED",
            "- **Status:** planned",
        ]
    assert bodies, "no experiment record forms to classify"
    unknown = [b.splitlines()[0] for b in bodies if _branch(CHECK4, b) is None]
    assert not unknown, f"status unknown for: {unknown}"


def test_check4_reads_the_shipped_records_correctly():
    """An approved record classifies as passed (0); a planned one as pending (2).

    Anchored on what the repo actually ships: the old E-011 (approved) /
    E-008 (planned) pair was pruned along with the historical record set
    (`clean`, 2026-09-26), so the approved anchor comes from any shipped record
    carrying a gate Decision and the pending anchor from the shipped template —
    the exact shape a record is in before its gate runs, and the one
    `bmad-research-experiment` writes the record from.
    """
    exp = PLUGIN / "docs" / "experiments"

    def _read(path: Path) -> str:
        return path.read_text(encoding="utf-8", errors="replace")

    approved = [p for p in sorted(exp.glob("E-*.md"))
                if re.search(r"^\s*-\s*\*\*Decision:\*\*\s*APPROVED\b", _read(p), re.M)]
    # Record-less plugin repo: fall back to the canonical APPROVED line so the
    # classification contract still runs against a real approved shape.
    approved_body = (_read(approved[0]) if approved
                     else "- **Decision:** APPROVED — H-001: measured=1.0 >= threshold=0.9")
    assert _branch(CHECK4, approved_body) == 0
    assert _branch(CHECK4, _read(TEMPLATES / "_template_E.md")) == 2


def test_check5_patterns_match_canonical_qr_status_field():
    assert len(CHECK5) == 2, CHECK5
    assert _branch(CHECK5, "- **Status:** APPROVED") == 0   # passed
    assert _branch(CHECK5, "- **Status:** REJECTED") == 1   # failed
    # an in-review QR (or the unfilled template line) is neither pass nor fail
    assert _branch(CHECK5, "- **Status:** in-review") is None
    unfilled = "- **Status:** in-review | APPROVED | REJECTED | REVISED"
    assert _branch(CHECK5, unfilled) is None


def test_templates_carry_the_fields_the_patterns_expect():
    e_tmpl = (TEMPLATES / "_template_E.md").read_text(encoding="utf-8")
    assert "- **Decision:**" in e_tmpl and "- **Status:**" in e_tmpl
    qr_tmpl = (TEMPLATES / "_template_QR.md").read_text(encoding="utf-8")
    assert "**Status:**" in qr_tmpl


# ─── CHECK 6: story-entry counting ───
#
# CHECK 6 counts sprint-status entries by grep. Two drifts met there: a bare
# `grep -c in-progress` counted the template's own vocabulary legend as
# stories, and a generic `<key>: <status>` pattern counted non-story keys
# (`epic-1: in-progress`). The engine (hooks/engine/modules/stop.py) matches
# the story-key form `N-N-slug`; the check must count exactly the same lines.

def _shipped_sprint_status() -> Path:
    # Seçenek A dual-read: canonical docs/ first, legacy bmad-output/ fallback.
    canonical = PLUGIN / "docs" / "development" / "native" / "sprint-status.yaml"
    if canonical.is_file():
        return canonical
    return PLUGIN / "bmad-output" / "implementation-artifacts" / "sprint-status.yaml"


SPRINT_STATUS = _shipped_sprint_status()


def _story_entry_pattern() -> str:
    section = _section("CHECK 6: Methodology Chain Completeness", "SUMMARY")
    m = re.search(r"_STATUS_ENTRY='([^']+)'", section)
    assert m, "CHECK 6 must define the story-entry pattern once"
    return m.group(1)


def _count(text: str, status: str) -> int:
    entry = _story_entry_pattern().replace("[[:space:]]", r"[ \t]")
    pattern = re.compile(entry + status + r"[ \t]*$")
    return sum(1 for line in text.splitlines() if pattern.search(line))


def test_check6_ignores_the_vocabulary_legend():
    legend = ("# Story Status:\n"
              "#   - in-progress: Developer actively working\n"
              "#   - done: Story completed\n")
    assert _count(legend, "in-progress") == 0
    assert _count(legend, "done") == 0


def test_check6_counts_story_entries_not_epic_keys():
    text = ("development_status:\n"
            "  epic-1: in-progress\n"
            "  1-1-bench-in-ci: in-progress\n"
            "  1-2-canvas-feed: done\n"
            "  1-3-pending-one: ready-for-dev\n")
    assert _count(text, "in-progress") == 1
    assert _count(text, "done") == 1


def test_check6_needs_an_indented_story_key():
    assert _count("1-1-bench-in-ci: in-progress\n", "in-progress") == 0
    assert _count("  bench-in-ci: in-progress\n", "in-progress") == 0


# ─── CHECK 6b: epic lag + retrospective signal ───
#
# The mailjs session (2026-09-23) worked from "epic-6: in-progress" in stale
# memory while every member story read done — the completion roll-up and the
# pending retrospective were invisible to every mechanical check. CHECK 6 now
# runs an awk pass that pairs each epic's claimed status with its member
# stories' real statuses, and counts open retros.

def _awk_section() -> str:
    return _section("CHECK 6: Methodology Chain Completeness", "SUMMARY")


def _run_lag_awk(text: str) -> list[str]:
    """Run CHECK 6's epic-lag awk program against *text* (extracted by name)."""
    section = _awk_section()
    m = re.search(r"EPIC_LAG=\$\(awk '(.*?)' \"\$SPRINT_STATUS\"", section, re.DOTALL)
    assert m, "CHECK 6 must run the epic-lag awk program on the sprint file"
    program = m.group(1).replace("\\", "")
    import subprocess
    r = subprocess.run([_awk_binary(), program], input=text, capture_output=True,
                       text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return [ln for ln in r.stdout.splitlines() if ln.strip()]


def test_check6_lag_flags_epic_in_progress_with_all_stories_done():
    rows = _run_lag_awk(
        "development_status:\n"
        "  epic-1: in-progress\n"
        "  1-1-a: done\n"
        "  1-2-b: done\n"
        "  epic-2: in-progress\n"
        "  2-1-x: done\n"
        "  2-2-y: in-progress\n")
    assert any(ln.startswith("epic-1|") and "retrospective" in ln for ln in rows)
    # epic-2 has a genuinely in-progress story — NOT lag.
    assert not any(ln.startswith("epic-2|") for ln in rows)


def test_check6_lag_flags_epic_done_with_open_stories():
    rows = _run_lag_awk(
        "development_status:\n"
        "  epic-3: done\n"
        "  3-1-a: done\n"
        "  3-2-b: review\n")
    assert any(ln.startswith("epic-3|") and "not done" in ln for ln in rows)


def test_check6_lag_respects_the_digit_boundary():
    rows = _run_lag_awk(
        "development_status:\n"
        "  epic-1: in-progress\n"
        "  1-1-a: done\n"
        "  epic-10: in-progress\n"
        "  10-1-x: done\n")
    assert any(ln.startswith("epic-1|") for ln in rows)   # 1-1 only
    assert any(ln.startswith("epic-10|") for ln in rows)  # 10-1 only


def test_check6_counts_open_retrospectives():
    section = _awk_section()
    m = re.search(r"RETRO_OPEN=\$\(grep -cE '([^']+)'", section)
    assert m, "CHECK 6 must count open retrospectives"
    pattern = re.compile(m.group(1).replace("[[:space:]]", r"[ \t]"))
    text = ("  epic-1-retrospective: optional\n"
            "  epic-2-retrospective: done\n")
    assert sum(1 for ln in text.splitlines() if pattern.search(ln)) == 1


def test_check6_reads_the_shipped_sprint_status():
    """The counter must agree with the LIVING sprint file. The frozen numbers
    below advance WITH the sprint on purpose: any story-status change must
    touch this test in the same change (the same drift rule the bench applies
    to doc counts).

    Record-less plugin repo (the board was pruned 2026-10-06): when no shipped
    board exists, a canonical board with known counts keeps the counting
    contract running.
    """
    if SPRINT_STATUS.is_file():
        text = SPRINT_STATUS.read_text(encoding="utf-8")
        keys = re.findall(r"^\s+(\d+-\d+-[a-z][a-z0-9-]*):", text, re.MULTILINE)
        assert keys, "the shipped sprint status must track story keys"
        # SP-026 closed 2026-09-21 (S-033 TTL enforcement) — no story in-progress; readiness is not progress
        assert _count(text, "in-progress") == 0
        # S-001…S-033 done — exactly thirty-three done stories
        assert _count(text, "done") == 33
        return
    text = ("development_status:\n"
            "  epic-1: in-progress\n"
            "  1-1-bench-in-ci: done\n"
            "  1-2-canvas-feed: in-progress\n"
            "  1-3-pending-one: ready-for-dev\n")
    assert _count(text, "in-progress") == 1
    assert _count(text, "done") == 1


def test_check6_prefers_canonical_docs_path():
    # CHECK 6 must list docs/development/native/sprint-status.yaml BEFORE legacy.
    section = _section("CHECK 6: Methodology Chain Completeness", "SUMMARY")
    candidates = re.findall(r'"\$PROJECT_ROOT/([^"]+sprint-status\.yaml)"', section)
    assert candidates, "CHECK 6 must enumerate sprint-status candidates"
    assert candidates[0] == "docs/development/native/sprint-status.yaml"


# ─── CHECK 6c: sprint-record pointer list ───
#
# The file's "# Methodology record:" comment named ONE record, so after a second
# sprint the board stopped naming the first (2026-10-01 second-sprint run).
# It is now one line per sprint record, and the check must run the SED THE
# SCRIPT RUNS against the forms the writers actually produce.

def _pointer_sed() -> str:
    section = _section("CHECK 6: Methodology Chain Completeness", "SUMMARY")
    m = re.search(r"POINTERS=\$\(sed -nE '([^']+)'", section)
    assert m, "CHECK 6 must extract the sprint-record pointer list with sed"
    return m.group(1)


def _run_pointer_sed(text: str) -> list[str]:
    import subprocess
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(text)
        path = fh.name
    try:
        # stdin=DEVNULL: the inherited pytest capture handle is not a valid
        # handle for a native child on Windows (WinError 6).
        r = subprocess.run(["sed", "-nE", _pointer_sed(), path],
                           capture_output=True, text=True, timeout=30,
                           stdin=subprocess.DEVNULL)
        assert r.returncode == 0, r.stderr
        return [ln for ln in r.stdout.splitlines() if ln.strip()]
    finally:
        os.unlink(path)


def test_check6c_pointer_pattern_reads_one_line_per_sprint_record():
    text = ("# Sprint status — acme\n"
            "# Methodology record: docs/development/SP-001.md\n"
            "# Methodology record: docs/development/SP-002.md\n"
            "development_status:\n"
            "  1-1-a: done\n")
    assert _run_pointer_sed(text) == ["docs/development/SP-001.md",
                                      "docs/development/SP-002.md"]


def test_check6c_pointer_pattern_ignores_prose_and_body_lines():
    # Only the comment form counts: a body mention of another sprint's record
    # (or prose about it) must not enter the list.
    text = ("# See Methodology record: docs/development/SP-999.md for the format\n"
            "# Methodology record: docs/development/SP-001.md\n")
    assert _run_pointer_sed(text) == ["docs/development/SP-001.md"]


# ─── CHECK 7: development gate records (IR / SP / PR) ───
#
# CHECK 3/4/5 looked at stories, experiments and QR records; the IR gate — the
# one that decides whether a sprint may start — had NO check, so a project could
# sit on an INCOMPLETE readiness record with an unchecked mandatory item and
# still read "0 issues / 0 warnings" (graph-engineering-arge design-wing
# session, 2026-10-01). These tests run the REAL script against a throwaway
# project root: the check is bash, so the only honest test is the program the
# project runs.


def _run_gate_check(project_root: Path):
    env = dict(os.environ)
    env["OPENHANDS_PROJECT_DIR"] = str(project_root)
    # Pin plugin-root resolution to this checkout (env vars would win over it).
    env.pop("CLAUDE_PLUGIN_ROOT", None)
    env.pop("METODOLOJI_PLUGIN_ROOT", None)
    r = subprocess.run(["sh", str(SCRIPT)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=180, env=env,
                       cwd=str(PLUGIN), stdin=subprocess.DEVNULL)
    out = (r.stdout or "") + (r.stderr or "")
    i = out.find("CHECK 7: Development Gate Records")
    assert i != -1, out
    # Only the CHECK 7 slice is asserted on: the exit code is global, and a
    # throwaway root legitimately fails earlier checks (no story template).
    return out[i:out.find("SUMMARY", i)]


def _write_ir(root: Path, body: str, name: str = "IR-001.md") -> None:
    dev = root / "docs" / "development"
    dev.mkdir(parents=True, exist_ok=True)
    (dev / name).write_text(body, encoding="utf-8")


_IR_CLEAN_READY = (
    "# Implementation Readiness: IR-001 — judged\n"
    "- **Date:** 2026-10-01\n"
    "- **Status:** READY\n"
    "- **Success criteria:**\n"
    "  - Triage latency — Metric: triage latency; Source: revisions.createdAt; Target: < 1 day\n"
    "- **Decision:** READY → design wing complete\n"
    "## Checklist (Gate 1 Check)\n"
    "- [x] Success criteria clear and measurable\n"
)


def test_check7_clean_ready_record_passes_quietly(tmp_path):
    _write_ir(tmp_path, _IR_CLEAN_READY)
    section = _run_gate_check(tmp_path)
    assert "IR-001" not in section, section


def test_check7_fails_ready_with_unchecked_mandatory_item(tmp_path):
    # The exact shape the session shipped: Status READY while the checklist
    # still says the criterion was never judged.
    _write_ir(tmp_path, _IR_CLEAN_READY.replace(
        "- [x] Success criteria clear and measurable",
        "- [ ] Success criteria clear and measurable"))
    section = _run_gate_check(tmp_path)
    assert "❌ ISSUE" in section
    assert "checklist item(s) are unchecked" in section, section


def test_check7_warns_on_qualitative_only_criteria(tmp_path):
    _write_ir(tmp_path, _IR_CLEAN_READY.replace(
        "  - Triage latency — Metric: triage latency; Source: revisions.createdAt; Target: < 1 day\n",
        "  - The client feels informed and the product feels trustworthy\n"))
    section = _run_gate_check(tmp_path)
    # unfalsifiable criteria are a warning, not a broken record
    assert "❌ ISSUE" not in section, section
    assert "qualitative-only" in section, section


def test_check7_requires_a_gap_plan_on_incomplete(tmp_path):
    _write_ir(tmp_path, _IR_CLEAN_READY.replace(
        "- **Status:** READY", "- **Status:** INCOMPLETE").replace(
        "- **Decision:** READY → design wing complete",
        "- **Decision:** INCOMPLETE → design wing not done"))
    section = _run_gate_check(tmp_path)
    assert "❌ ISSUE" in section
    assert "without a 'Gaps' field" in section, section


def test_check7_flags_an_open_gate_as_a_warning(tmp_path):
    _write_ir(tmp_path, _IR_CLEAN_READY.replace(
        "- **Status:** READY", "- **Status:** INCOMPLETE").replace(
        "- **Decision:** READY → design wing complete",
        "- **Decision:** INCOMPLETE → design wing not done").replace(
        "- **Date:** 2026-10-01",
        "- **Date:** 2026-10-01\n"
        "- **Gaps:**\n  - UX spec → Mode C → next session\n"))
    section = _run_gate_check(tmp_path)
    assert "❌ ISSUE" not in section, section
    assert "Gate 1 is OPEN (INCOMPLETE)" in section, section


def test_check7_flags_dangling_design_input(tmp_path):
    _write_ir(tmp_path, _IR_CLEAN_READY.replace(
        "- **Status:** READY",
        "- **Status:** READY\n"
        "- **Design documents:**\n"
        "  - PRD: docs/design/prds/prd-workpanel-2026-10-01/PRD.md"))
    section = _run_gate_check(tmp_path)
    assert "❌ ISSUE" in section
    assert "names a design input not on disk" in section, section


def test_check7_flags_status_decision_disagreement(tmp_path):
    _write_ir(tmp_path, _IR_CLEAN_READY.replace(
        "- **Decision:** READY → design wing complete",
        "- **Decision:** INCOMPLETE → design wing not done"))
    section = _run_gate_check(tmp_path)
    assert "❌ ISSUE" in section
    assert "Status and Decision disagree" in section, section


def test_check7_tolerates_the_draft_preparing_state(tmp_path):
    _write_ir(tmp_path, _IR_CLEAN_READY.replace("READY", "preparing"))
    section = _run_gate_check(tmp_path)
    assert "❌ ISSUE" not in section, section
    assert "no verdict yet" in section, section


def test_check7_flags_a_project_side_custom_dir_as_an_orphan(tmp_path):
    """custom/ is plugin-global policy — a project copy is never read."""
    (tmp_path / "custom").mkdir(parents=True)
    (tmp_path / "custom" / "config.toml").write_text("[hooks]\n", encoding="utf-8")
    env = dict(os.environ)
    env["OPENHANDS_PROJECT_DIR"] = str(tmp_path)
    env.pop("CLAUDE_PLUGIN_ROOT", None)
    env.pop("METODOLOJI_PLUGIN_ROOT", None)
    r = subprocess.run(["sh", str(SCRIPT)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=180, env=env,
                       cwd=str(PLUGIN), stdin=subprocess.DEVNULL)
    out = (r.stdout or "") + (r.stderr or "")
    assert "project-side custom/config.toml is never read" in out, out


def test_plugin_base_config_is_neutral_and_the_project_layer_names_the_repo():
    """A real project name in the shipped base layer leaks into every install."""
    base = (PLUGIN / "bmad" / "config.toml").read_text(encoding="utf-8")
    m = re.search(r'^project_name\s*=\s*"([^"]*)"', base, re.MULTILINE)
    assert m, "bmad/config.toml must declare project_name"
    assert m.group(1).strip().lower() in {"", "unconfigured", "bmad-project", "project"}
    layer = PLUGIN / "docs" / "config.toml"
    assert layer.is_file(), "this repo must carry its identity in a project layer"
    assert re.search(r'project_name\s*=\s*"metodoloji"',
                     layer.read_text(encoding="utf-8"))


def test_check6c_shipped_sprint_status_names_every_sprint_record():
    """The living board must name every SP record it sits next to.

    Advances WITH the sprints on purpose — a new SP record must add its pointer
    line in the same change (the drift rule the bench applies to doc counts).
    Record-less plugin repo (records pruned 2026-10-06): when no SP records
    ship, a canonical board naming two synthesized records keeps the pointer
    contract running.
    """
    records = sorted(p.name for p in (PLUGIN / "docs" / "development").glob("SP-*.md"))
    if records:
        text = SPRINT_STATUS.read_text(encoding="utf-8")
    else:
        records = ["SP-001.md", "SP-002.md"]
        text = ("# Sprint status — dogfood\n"
                "# Methodology record: docs/development/SP-001.md\n"
                "# Methodology record: docs/development/SP-002.md\n"
                "development_status:\n  1-1-bench-in-ci: done\n")
    pointers = _run_pointer_sed(text)
    named = [p.rsplit("/", 1)[-1] for p in pointers]
    assert len(named) == len(set(named)), f"duplicate pointer lines: {named}"
    assert named == records, f"board names {named}, records on disk {records}"
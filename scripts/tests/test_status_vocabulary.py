"""One owner per status vocabulary — templates, CLI, and audit must agree.

The chain has six record kinds and, before this test, up to three answers per
kind: the template said one thing, `create-record.py` another, and the audit a
third. A record the CLI itself produced then failed the audit (IR default
`NOT-READY` vs CHECK 7's `READY|INCOMPLETE`), and a skill was told to write a
story status (`sprint`) the guard refuses outright.

Each kind's owner is named below, and this test fails if a surface restates a
different set. Adding a status means changing the owner and the template in one
commit — which is the point.
"""

import re
import subprocess
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))

from hooks.engine.modules import guard  # noqa: E402

TEMPLATES = PLUGIN / "templates"
CREATE = PLUGIN / "scripts" / "create-record.py"


def _schema_statuses(kind: str) -> list[str]:
    """The canonical statuses, read from the owner's own module."""
    sys.path.insert(0, str(PLUGIN / "scripts"))
    spec = __import__("importlib.util").util.spec_from_file_location(
        "create_record", CREATE)
    mod = __import__("importlib.util").util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return list(mod._SCHEMAS[kind]["statuses"])


def _template_status(template: str) -> str:
    text = (TEMPLATES / template).read_text(encoding="utf-8")
    m = re.search(r"^- \*\*Status:\*\*\s*(.+)$", text, re.M)
    assert m, f"{template} has no Status line"
    return m.group(1).strip()


def _template_words(template: str) -> list[str]:
    return [w.strip() for w in _template_status(template).split("|")]


def test_ir_template_matches_the_schema():
    assert sorted(_template_words("_template_IR.md")) == sorted(
        _schema_statuses("ir"))


def test_sp_template_matches_the_schema():
    assert sorted(_template_words("_template_SP.md")) == sorted(
        _schema_statuses("sp"))


def test_pr_template_matches_the_schema():
    assert sorted(_template_words("_template_PR.md")) == sorted(
        _schema_statuses("pr"))


def test_story_template_matches_the_guard():
    """The guard is the story-status owner: it refuses a word at write time."""
    text = (PLUGIN / "hooks" / "engine" / "modules" / "guard.py").read_text(
        encoding="utf-8")
    m = re.search(r"valid_states = \{([^}]+)\}", text)
    assert m, "guard no longer declares its story status set"
    guard_states = {w.strip().strip('"\'') for w in m.group(1).split(",") if w.strip()}
    assert set(_template_words("_template_S.md")) == guard_states


def test_no_surface_still_tells_a_writer_to_use_a_refused_story_status():
    """`sprint` was in the story vocabulary of the docs and one skill TOML,
    while the guard refuses it — so following the instruction produced a DENY."""
    offenders = []
    for path in [PLUGIN / "skills" / "bmad-create-story" / "customize.toml"]:
        if path.is_file() and re.search(r"Status:\s*[^\"\n]*\bsprint\b",
                                        path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(PLUGIN)))
    assert offenders == [], f"refused story status offered to a writer: {offenders}"


def test_the_cli_default_status_is_accepted_by_its_own_checker(tmp_path):
    """A record the CLI writes must pass `--check` — the IR default did not."""
    fields = {
        "ir": ("Research inputs=none", "Success criteria=Metric: x >= 1",
               "Decision=READY", "Next step=none"),
        "sp": ("Readiness input=IR-001", "Sprint goal=probe", "Stories=S-001"),
        "pr": ("Release type=patch", "Version=0.0.1", "Stories=S-001"),
    }
    for kind, rec_id in (("ir", "IR-001"), ("sp", "SP-001"), ("pr", "PR-001")):
        argv = [sys.executable, str(CREATE), "--kind", kind, "--id", rec_id,
                "--title", "vocabulary probe", "--project-root", str(tmp_path)]
        for field in fields[kind]:
            argv += ["--field", field]
        out = subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60, stdin=subprocess.DEVNULL)
        assert out.returncode == 0, f"{kind}: write failed — {out.stderr}"
        record = next(tmp_path.rglob(f"{rec_id}.md"))
        check = subprocess.run(
            [sys.executable, str(CREATE), "--check", str(record)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60, stdin=subprocess.DEVNULL)
        assert check.returncode == 0, (
            f"{kind}: the CLI wrote a record its own checker refuses — "
            f"{check.stdout}{check.stderr}")

def test_skill_record_steps_offer_the_templates_words():
    """A BRIDGE step teaches a `Status:` list; it must be the template's.

    The IR/PR/QR/SP steps in four skills each carried their own vocabulary
    (`INCOMPLETE`, `PENDING`, `pass|fail|partial`, `in progress`), so a writer
    following the instruction produced a record the owner's checker rejects.
    Templates are already pinned to the owners above, so comparing against the
    template closes the loop without a third list.
    """
    by_prefix = {"IR-": "_template_IR.md", "SP-": "_template_SP.md",
                 "PR-": "_template_PR.md", "QR-": "_template_QR.md",
                 "S-": "_template_S.md"}
    offenders = []
    for path in sorted((PLUGIN / "skills").glob("*/customize.toml")):
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"(docs/[\w/]*?/([A-Z]{1,2})-<sira>\.md[^\"]*?"
                             r"Status:\s*([A-Za-z][\w|-]*))", text):
            template = by_prefix.get(f"{m.group(2)}-")
            if template is None:
                continue
            wanted = set(_template_words(template))
            got = {w.strip() for w in m.group(3).split("|")}
            if got != wanted:
                offenders.append(
                    f"{path.parent.name} ({template}): {sorted(got)} "
                    f"!= {sorted(wanted)}")
    assert offenders == [], f"skill steps disagree with their template: {offenders}"

"""Structural scope — the third category in guard.py.

Product code needs a VERIFIED experiment; measurement benches stage under an
undecided record; code that carries no empirical claim had neither, so the first
`src/` write of any greenfield project was blocked and the cheapest way through
was a fabricated hypothesis (a subagent in a consuming project wrote a fake E-001
for exactly that reason).

A structural declaration is the honest door: scoped, justified, warned on every
write, and never able to downgrade a measured claim. These tests pin that shape.
"""

import sys
from pathlib import Path

import pytest

_HOOKS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HOOKS))

from modules.guard import guard  # noqa: E402
from modules import config  # noqa: E402


VALID = """# Structural Scope: STRUCT-001 — Application skeleton

- **Status:** declared
- **Justification:** Scaffold only: manifest, TypeScript config and an app root that renders
  an empty map. None of this asserts anything measurable; the measurable claims live in E-004.
- **Scope:** src/**, package.json, tsconfig.json
- **Declared:** 21.10.2026
- **Reviewed-By:**
"""


def _project(tmp_path, declaration=VALID, name="STRUCT-001.md"):
    d = tmp_path / "docs/development/structural"
    d.mkdir(parents=True)
    if declaration is not None:
        (d / name).write_text(declaration, encoding="utf-8")
    return tmp_path


def _hard(monkeypatch, root):
    monkeypatch.setattr(config, "hook_gate_mode", lambda key: "hard")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.delenv("OPENHANDS_PROJECT_DIR", raising=False)


def _write(path):
    return guard({"tool_name": "file_editor",
                  "tool_input": {"path": path, "content": "export const x = 1;\n"}})


def test_declared_scope_allows_the_write_with_a_warning(tmp_path, monkeypatch):
    """1. A valid declaration unlocks the path and says so out loud."""
    _hard(monkeypatch, _project(tmp_path))
    res = _write("src/app.ts")
    assert res["decision"] == "allow"
    assert any("structural scope declared in STRUCT-001" in w
               for w in res.get("methodology_warnings", [])), res


def test_status_draft_is_not_a_declaration(tmp_path, monkeypatch):
    """2. Only declared|ratified counts — a draft is someone thinking aloud."""
    _hard(monkeypatch, _project(tmp_path, VALID.replace("**Status:** declared",
                                                        "**Status:** draft")))
    assert _write("src/app.ts")["decision"] == "deny"


def test_short_justification_is_refused(tmp_path, monkeypatch):
    """3. An unexplained exemption is the abuse case."""
    short = VALID.replace(
        "**Justification:** Scaffold only: manifest, TypeScript config and an app root that renders\n"
        "  an empty map. None of this asserts anything measurable; the measurable claims live in E-004.",
        "**Justification:** scaffold")
    _hard(monkeypatch, _project(tmp_path, short))
    assert _write("src/app.ts")["decision"] == "deny"


def test_benches_are_never_structural(monkeypatch, tmp_path):
    """4. A measurement bench still needs a real experiment — the exemption
    must not become a way around the instrument rule."""
    _hard(monkeypatch, _project(tmp_path, VALID.replace(
        "**Scope:** src/**, package.json, tsconfig.json",
        "**Scope:** scripts/bench/**")))
    assert _write("scripts/bench/bench_x.mjs")["decision"] == "deny"


def test_approved_record_wins_over_a_declaration(tmp_path, monkeypatch):
    """5. A declaration can never downgrade a measured claim: the approval path
    is evaluated first, so the structural warning must not appear."""
    _hard(monkeypatch, _project(tmp_path))

    import modules.guard as g

    monkeypatch.setattr(g, "find_approved",
                        lambda rel, root="": (True, "approved by E-004", None))
    res = _write("src/app.ts")
    assert res["decision"] == "allow"
    assert not any("structural scope" in w for w in res.get("methodology_warnings", [])), res


def test_no_structural_directory_behaves_as_before(tmp_path, monkeypatch):
    """6. A project that never declares anything sees today's behaviour."""
    _hard(monkeypatch, tmp_path)
    assert _write("src/app.ts")["decision"] == "deny"

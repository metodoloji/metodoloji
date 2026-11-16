"""The installer keeps runtime state out of git (E-174).

`.metodoloji/` is runtime state: a sqlite blackboard with WAL, append-only
logs, locks and the relay note. Committing it churned a binary blob every
session, conflicted on locks, and a tracked log made the work tree dirty
enough to STALE unscoped claims (Agently, 2026-11-16).
"""

import pathlib
import sys

import pytest

_SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import skeleton  # noqa: E402


@pytest.fixture()
def project(tmp_path):
    return tmp_path


def test_install_ignores_runtime_state(project):
    skeleton.install(project)
    text = (project / ".gitignore").read_text(encoding="utf-8")
    assert ".metodoloji/" in text


def test_install_is_idempotent_for_the_ignore_line(project):
    skeleton.install(project)
    skeleton.install(project)
    text = (project / ".gitignore").read_text(encoding="utf-8")
    assert text.count(".metodoloji/") == 1


def test_existing_gitignore_rules_survive(project):
    (project / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    skeleton.install(project)
    text = (project / ".gitignore").read_text(encoding="utf-8")
    assert "node_modules/" in text and ".metodoloji/" in text

"""Tests for bmad/scripts/resolve_customization.py — F3 merge-rule goldens.

Locks the three-layer merge contract (base -> team -> user):
scalar override, table deep-merge, keyed array merge (code|id),
append fallback, and no-deletion. Any change to these rules must
update the goldens deliberately — skills, checks, and the engine
all depend on them.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

_SCRIPT = (
    Path(__file__).resolve().parent.parent.parent
    / "bmad" / "scripts" / "resolve_customization.py"
)
_spec = importlib.util.spec_from_file_location("resolve_customization", _SCRIPT)
rc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rc)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# --- deep_merge unit goldens -------------------------------------------------

def test_scalar_override_wins():
    assert rc.deep_merge({"a": "base", "n": 1, "b": True},
                         {"a": "team", "n": 2}) == {"a": "team", "n": 2, "b": True}


def test_table_deep_merge():
    base = {"agent": {"name": "x", "menu": {"a": 1}}}
    over = {"agent": {"menu": {"b": 2}}}
    assert rc.deep_merge(base, over) == {"agent": {"name": "x", "menu": {"a": 1, "b": 2}}}


def test_keyed_array_merge_by_code():
    base = [{"code": "A", "prompt": "old"}, {"code": "B", "prompt": "b"}]
    over = [{"code": "A", "prompt": "new"}, {"code": "C", "prompt": "c"}]
    assert rc.deep_merge({"m": base}, {"m": over}) == {
        "m": [{"code": "A", "prompt": "new"}, {"code": "B", "prompt": "b"},
              {"code": "C", "prompt": "c"}]}


def test_keyed_array_merge_by_id():
    base = [{"id": "1", "v": "old"}]
    over = [{"id": "1", "v": "new"}, {"id": "2", "v": "x"}]
    assert rc.deep_merge({"m": base}, {"m": over}) == {
        "m": [{"id": "1", "v": "new"}, {"id": "2", "v": "x"}]}


def test_mixed_code_id_falls_back_to_append():
    base = [{"code": "A", "v": 1}]
    over = [{"id": "1", "v": 2}]
    assert rc.deep_merge({"m": base}, {"m": over}) == {
        "m": [{"code": "A", "v": 1}, {"id": "1", "v": 2}]}


def test_plain_arrays_append():
    base = {"workflow": {"persistent_facts": ["file:a.md"]}}
    over = {"workflow": {"persistent_facts": ["file:b.md"]}}
    assert rc.deep_merge(base, over) == {
        "workflow": {"persistent_facts": ["file:a.md", "file:b.md"]}}


def test_no_deletion_base_items_survive():
    base = {"workflow": {"append": ["step-1", "step-2"], "keep": "yes"}}
    over = {"workflow": {"append": ["step-3"]}}
    merged = rc.deep_merge(base, over)
    assert merged["workflow"]["append"] == ["step-1", "step-2", "step-3"]
    assert merged["workflow"]["keep"] == "yes"


def test_type_mismatch_override_wins():
    assert rc.deep_merge({"a": {"x": 1}}, {"a": "flat"}) == {"a": "flat"}
    assert rc.deep_merge({"a": [1]}, {"a": "flat"}) == {"a": "flat"}


# --- layer priority end-to-end (CLI, throwaway tree) --------------------------

def _layer_tree(tmp_path: Path, base_toml: str, team_toml: str | None,
                user_toml: str | None) -> Path:
    proj = tmp_path / "proj"
    skill = proj / "skills" / "demo-skill"
    _write(skill / "customize.toml", base_toml)
    if team_toml is not None:
        _write(proj / "custom" / "demo-skill.toml", team_toml)
    if user_toml is not None:
        _write(proj / "custom" / "demo-skill.user.toml", user_toml)
    return skill


def _resolve(skill: Path, *keys: str) -> dict:
    cmd = [sys.executable, str(_SCRIPT), "--skill", str(skill)]
    for k in keys:
        cmd += ["--key", k]
    # stdin=DEVNULL: under pytest capture on Windows the inherited std
    # handles can be invalid (WinError 6) — same race guard.py works around.
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, f"resolver failed: {r.stderr[-300:]}"
    return json.loads(r.stdout)


def test_layer_priority_user_over_team_over_base(tmp_path):
    skill = _layer_tree(
        tmp_path,
        '[workflow]\nname = "base"\nnums = [1]\n',
        '[workflow]\nname = "team"\nnums = [2]\n',
        '[workflow]\nnums = [3]\n',
    )
    out = _resolve(skill, "workflow")
    assert out["workflow"]["name"] == "team"      # team overrides base scalar
    assert out["workflow"]["nums"] == [1, 2, 3]   # arrays append across layers


def test_missing_customize_toml_falls_back_to_stub(tmp_path):
    proj = tmp_path / "proj"
    (proj / "custom").mkdir(parents=True)
    skill = proj / "skills" / "minimal-skill"
    skill.mkdir(parents=True)  # no customize.toml on disk
    out = _resolve(skill, "workflow")
    facts = out["workflow"]["persistent_facts"]
    assert len(facts) == 3 and any("project-context.md" in f for f in facts)


def test_broken_team_toml_warns_and_keeps_base(tmp_path):
    skill = _layer_tree(tmp_path, '[workflow]\nname = "base"\n', '[[[\n', None)
    cmd = [sys.executable, str(_SCRIPT), "--skill", str(skill), "--key", "workflow"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0
    assert json.loads(r.stdout)["workflow"]["name"] == "base"
    assert "warning" in r.stderr.lower()

"""E-159: a `{metodoloji-root}` path a shipped surface names must exist.

The defect these tests pin: bmad-customize's preflight stopped on
`{metodoloji-root}/bmad/`, a tree E-149 retired, so the skill could never
activate; five more shipped surfaces spelled example paths under the same dead
tree or under the plugin root where the PROJECT root is meant. The probes are
driven on throwaway trees so the rule is falsifiable without touching the
shipped ones.
"""

from __future__ import annotations

import pathlib
import sys

BENCH_DIR = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "bench"
if str(BENCH_DIR) not in sys.path:
    sys.path.insert(0, str(BENCH_DIR))

import bench_plugin_path_targets as bench  # noqa: E402

def _tree(tmp_path: pathlib.Path, files: dict[str, str]) -> pathlib.Path:
    for rel, body in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return tmp_path

def test_a_dead_target_is_caught(tmp_path):
    tree = _tree(tmp_path, {"skills/x/SKILL.md":
                            "Stop unless `{metodoloji-root}/bmad/` exists.\n"})
    ok, detail = bench._case_family(tree, "skills", ("skills/**/*",))
    assert not ok and "bmad/" in detail

def test_a_live_target_passes(tmp_path):
    tree = _tree(tmp_path, {"skills/x/SKILL.md":
                            "Read `{metodoloji-root}/hooks/scripts/orient.py`.\n"})
    (tree / "hooks" / "scripts").mkdir(parents=True)
    (tree / "hooks" / "scripts" / "orient.py").write_text("", encoding="utf-8")
    ok, _ = bench._case_family(tree, "skills", ("skills/**/*",))
    assert ok

def test_a_placeholder_names_no_file(tmp_path):
    tree = _tree(tmp_path, {"skills/x/SKILL.md":
                            "Use `{metodoloji-root}/custom/{skill-name}.toml`.\n"})
    ok, _ = bench._case_family(tree, "skills", ("skills/**/*",))
    assert ok

def test_the_broken_arm_fails_every_family(tmp_path):
    tree = _tree(tmp_path, {f"{name}/keep.md": "no reference here\n"
                            for name, _ in bench.FAMILIES})
    bench.BROKEN = True
    try:
        results = bench.run(tree)
    finally:
        bench.BROKEN = False
    assert not any(r["ok"] for r in results)
    assert all("ghost" in r["detail"] for r in results)

def test_the_live_tree_passes_every_probe():
    results = bench.run(pathlib.Path(__file__).resolve().parents[2])
    assert all(r["ok"] for r in results), [r for r in results if not r["ok"]]

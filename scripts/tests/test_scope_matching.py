"""Scope matching: dot-directories must be coverable.

`str.lstrip("./")` strips a character SET, not a prefix, so it ate the leading
dot of `.github/workflows/ci.yml` — the target lost its dot while the pattern
kept its own, and the two could never match. Every dot-directory in a project
was therefore impossible to cover with a Code Scope, including an exact,
fully-spelled path: an approval could say "yes, this exact file" and the guard
would still refuse it.

The same misuse sat in `plan.py` (planned files) and `freshness.py` (scope
digests), so the defect reached the plan layer and the staleness verdict too.
"""

import importlib.util
import pathlib

PLUGIN = pathlib.Path(__file__).resolve().parents[2]
GATE = PLUGIN / "skills/bmad-research-experiment/scripts/run_experiment.py"

J = "/".join


def _gate():
    spec = importlib.util.spec_from_file_location("gate_scope", GATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dot_directory_glob_matches():
    gate = _gate()
    target = J([".github", "workflows", "ci.yml"])
    assert gate.scope_matches(J([".github", "workflows", "**"]), target)


def test_exact_dot_path_matches_itself():
    """The starkest form: a record naming the file exactly must cover it."""
    gate = _gate()
    target = J([".github", "workflows", "ci.yml"])
    assert gate.scope_matches(target, target)


def test_dot_file_matches():
    gate = _gate()
    assert gate.scope_matches(".gitignore", ".gitignore")
    assert gate.scope_matches(".githooks/*", J([".githooks", "pre-commit"]))


def test_matching_is_still_selective():
    """The fix must not turn every dot path into a wildcard."""
    gate = _gate()
    target = J([".github", "workflows", "ci.yml"])
    assert not gate.scope_matches(J(["src", "**"]), target)
    assert not gate.scope_matches(J([".github", "actions", "**"]), target)


def test_leading_dot_slash_still_strips():
    """'./x' keeps working — that is what the original lstrip was for."""
    gate = _gate()
    assert gate.scope_matches("./" + J(["src", "**"]), "./" + J(["src", "main.tsx"]))
    assert gate.scope_matches(J(["src", "**"]), "./" + J(["src", "main.tsx"]))


def test_hook_modules_use_the_same_rule():
    """plan.py and freshness.py carried the same misuse; they must agree."""
    for rel in (("hooks", "engine", "modules", "plan.py"),
                ("hooks", "engine", "modules", "freshness.py")):
        text = (PLUGIN.joinpath(*rel)).read_text(encoding="utf-8")
        # The real misuse was a strip applied to a VALUE, never to a docstring:
        # check the call sites, not the prose that explains them.
        for misuse in ('.replace("\\\\", "/").lstrip("./")',
                       'entry.lstrip("./")',
                       't.lstrip("./")'):
            assert misuse not in text, f"{'/'.join(rel)} still strips a character set"
        assert text.count("_strip_dot_prefix") >= 2, (
            f"{'/'.join(rel)} introduces or uses no shared strip rule")

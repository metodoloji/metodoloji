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

# --- the matcher's memo ------------------------------------------------------
# The gate's matcher is what `check-plugin.sh` §3b and the guard call: §3b runs
# it once per (record, edited file) pair, so on this corpus it was asked 140,318
# questions and re-parsed every glob for each one — 1,184,988 parses, 95 s of a
# 117 s section (E-140). `freshness.py` already carries the same memo (E-132);
# this file's job is to keep the gate's half alive and provably equal.

def test_glob_parser_is_memoised_without_changing_its_answer():
    gate = _gate()
    pattern = "hooks/engine/modules/*.py"

    first = gate.glob_to_regex(pattern)          # the uncached reference
    gate._glob_regex.cache_clear()
    assert gate._glob_regex(pattern) == first    # first lookup parses
    before = gate._glob_regex.cache_info()
    assert gate._glob_regex(pattern) == first    # second lookup is a hit
    after = gate._glob_regex.cache_info()

    assert before.misses == 1 and before.hits == 0, before
    assert after.hits == before.hits + 1, "the second lookup was not served from the memo"
    assert after.misses == before.misses, "a repeat of the same pattern re-parsed it"


def test_scope_patterns_are_memoised_and_immutable():
    gate = _gate()
    scope = "src/** , lib/*.py"
    first = gate._scope_patterns(scope)
    assert first == ("src/**", "lib/*.py")
    assert isinstance(first, tuple), "a shared cached value must not be mutable"
    before = gate._scope_patterns.cache_info()
    gate._scope_patterns(scope)
    assert gate._scope_patterns.cache_info().hits == before.hits + 1


def test_memo_does_not_change_any_verdict():
    """Every scope/target pair answers the same with the memo cold and warm."""
    gate = _gate()
    scopes = ["src/**", "src/*.py", "docs/*.md", "**/*.py", "a?c.py",
              ".github/workflows/**", "hooks/engine/modules/*.py"]
    targets = ["src/a.py", "src/deep/b.py", "docs/x.md", "abc.py", "axc.py",
               ".github/workflows/ci.yml", "hooks/engine/modules/guard.py",
               "other/c.py"]
    gate._glob_regex.cache_clear()
    gate._scope_patterns.cache_clear()
    cold = {(s, t): gate.scope_matches(s, t) for s in scopes for t in targets}
    warm = {(s, t): gate.scope_matches(s, t) for s in scopes for t in targets}
    assert cold == warm
    # A sanity anchor so the test cannot pass by every pair being False.
    assert cold[("src/**", "src/deep/b.py")] is True
    assert cold[("src/*.py", "src/deep/b.py")] is False


def test_corpus_verification_runs_in_one_process():
    """`--verify-corpus` is the audit's single verification pass (E-140).

    It must call the same `verify()` (no second opinion about provenance) and
    emit one record per file, including the message that explains a failure.
    """
    import contextlib
    import io
    import json
    import tempfile

    gate = _gate()
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        (root / "E-001.md").write_text(
            "# Experiment: E-001 — undecided\n\n- **Status:** planned\n",
            encoding="utf-8")
        (root / "_template.md").write_text("# template\n", encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = gate.verify_corpus(str(root))
        assert rc == 0
        payload = json.loads(out.getvalue())
    assert payload["records_total"] == 1          # the template is not a record
    assert payload["records"][0]["id"] == "E-001"
    assert payload["records"][0]["rc"] == 1       # undecided, not a tool failure
    assert "undecided" in payload["records"][0]["message"]


def test_corpus_verification_tsv_is_one_line_per_record():
    import contextlib
    import io
    import tempfile

    gate = _gate()
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        (root / "E-001.md").write_text("- **Status:** planned\n", encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            assert gate.verify_corpus(str(root), "tsv") == 0
    lines = out.getvalue().splitlines()
    assert len(lines) == 1
    fields = lines[0].split("\t")
    assert fields[0] == "E-001" and fields[1] == "1" and fields[2]

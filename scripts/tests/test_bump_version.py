"""Regression tests for the version-only CI's bump script (E-010 … E-014).

Two halves acting as one. The bump script RAISES the number (to the one the
commit's position owns — `--position` on the automatic line, `--set-version`
on a declared one) and REFUSES to act on §6d3 manifest skew, on a target that
would walk backwards, on an empty bump, on a number the rule cannot produce,
and on a line that was never declared. Since E-014 the HOOK calls it
(`.githooks/pre-commit`, committed with the repository) and the workflow does
not: CI's whole value is the other half — it verifies each pushed commit's
tree against the number its position owns and publishes the tags, writes NO
version and creates NO commit, and stays version-only: no audits creep into
CI. E-011 adds the published pointer: the run tags the number it verified,
never one it typed. E-013 adds the boundary: a new minor line opens every 100
commits, a new major line only by explicit human declaration.
"""

import importlib.util
import json
import pathlib

import pytest

PLUGIN = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = PLUGIN / "scripts" / "bump-version.py"
WORKFLOW = PLUGIN / ".github" / "workflows" / "release-numbers.yml"
# The step that verifies the numbers and publishes the pointers. Named once
# so a rename in the workflow fails these tests loudly instead of silently
# testing nothing.
TAG_STEP = "Verify the numbers and tag every commit this push carried"

_spec = importlib.util.spec_from_file_location("bump_version", SCRIPT)
bump = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bump)


def _seed_tree(root: pathlib.Path, version: str = "0.1.3") -> None:
    """A miniature repo carrying every file the bump must keep in agreement."""
    for rel in bump.MANIFESTS:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if rel.endswith(".toml"):
            path.write_text(f'[project]\nname = "metodoloji"\n'
                            f'version = "{version}"\n', encoding="utf-8")
        elif "marketplace" in rel:
            path.write_text(json.dumps(
                {"name": "metodoloji",
                 "plugins": [{"name": "metodoloji", "version": version}]}),
                encoding="utf-8")
        else:
            path.write_text(json.dumps(
                {"name": "metodoloji", "version": version}), encoding="utf-8")
    (root / "README.md").write_text(
        f"![v](https://img.shields.io/badge/version-{version}-0b7285)\n",
        encoding="utf-8")
    (root / "commands").mkdir(parents=True, exist_ok=True)
    (root / "commands" / "init.md").write_text(
        f"versioned cache e.g. `.../{version}/`\n", encoding="utf-8")


def _bytes(root: pathlib.Path) -> dict[str, bytes]:
    files = [root / rel for rel in bump.MANIFESTS]
    files += [root / "README.md", root / "commands" / "init.md"]
    return {str(p.relative_to(root)): p.read_bytes() for p in files}


def test_positional_advance_math():
    """`--by N` advances by N positions, and positions know about blocks (E-013).

    Crossing a block boundary is not an error and not a minor bump someone
    chose: `0.1.99 + 1` IS `0.2.0` (position 100), which is what makes the
    boundary rule arithmetic instead of a meeting.
    """
    assert bump.resolve_target("0.1.5", by=1) == "0.1.6"
    assert bump.resolve_target("0.1.99", by=1) == "0.2.0"
    assert bump.resolve_target("0.1.98", by=3) == "0.2.1"
    with pytest.raises(bump.BumpError):
        bump.resolve_target("1.2", by=1)          # not plain X.Y.Z
    with pytest.raises(bump.BumpError):
        bump.resolve_target("0.1.5", by=0)        # every run bumps at least once
    with pytest.raises(bump.BumpError, match="declared line"):
        bump.resolve_target("1.0.5", by=1)        # positional only on major 0


def test_bump_updates_every_manifest_and_claim(tmp_path):
    _seed_tree(tmp_path)
    old, new = bump.bump(tmp_path, by=3)
    assert (old, new) == ("0.1.3", "0.1.6")
    assert bump.read_version(tmp_path) == new
    # every manifest stays valid JSON/TOML-shaped AND agrees (§6d3's set)
    for rel in bump.MANIFESTS:
        text = (tmp_path / rel).read_text(encoding="utf-8")
        assert new in text, rel
        if rel.endswith(".json"):
            json.loads(text)
    # the two derived human claims follow
    assert f"version-{new}-" in (tmp_path / "README.md").read_text(encoding="utf-8")
    assert f".../{new}/" in (tmp_path / "commands" / "init.md").read_text(encoding="utf-8")


def test_bump_preserves_line_endings(tmp_path):
    """Byte-level round-trip: a CRLF manifest stays CRLF (diff stays minimal)."""
    crlf = tmp_path / "pyproject.toml"
    _seed_tree(tmp_path)
    crlf.write_bytes(b'[project]\r\nversion = "0.1.3"\r\n')
    bump.bump(tmp_path)
    assert b"\r\n" in crlf.read_bytes()
    assert b'version = "0.1.4"' in crlf.read_bytes()


def test_check_mode_is_read_only(tmp_path):
    _seed_tree(tmp_path)
    before = _bytes(tmp_path)
    assert bump.main(["--root", str(tmp_path), "--check"]) == 0
    assert _bytes(tmp_path) == before            # consistent tree: no writes


def test_skew_refuses_before_any_write(tmp_path):
    _seed_tree(tmp_path)
    # one manifest drifts — the enforcement half must fire
    (tmp_path / ".plugin" / "plugin.json").write_text(
        json.dumps({"name": "metodoloji", "version": "9.9.9"}), encoding="utf-8")
    before = _bytes(tmp_path)
    with pytest.raises(bump.BumpError, match="skew"):
        bump.bump(tmp_path)
    assert _bytes(tmp_path) == before            # nothing written, ever
    assert bump.main(["--root", str(tmp_path), "--check"]) == 1
    assert bump.main(["--root", str(tmp_path)]) == 1


def test_missing_version_manifest_refuses(tmp_path):
    _seed_tree(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n',
                                             encoding="utf-8")
    with pytest.raises(bump.BumpError, match="no version"):
        bump.bump(tmp_path)


def test_bump_then_check_agree(tmp_path):
    _seed_tree(tmp_path)
    assert bump.main(["--root", str(tmp_path)]) == 0
    assert bump.main(["--root", str(tmp_path), "--check"]) == 0


def test_set_version_names_the_absolute_number(tmp_path):
    """E-012/E-013: the number is SET, never added to, and the rule has a veto.

    Every dishonest target is refused with nothing written: one below the
    current number (history would walk backwards), one equal to it (the bump
    commit would be empty), one the rule cannot produce (`0.1.150` — a third
    digit inside a 100-commit block), one on another line (`0.2.0` from
    `0.1.6`: opening a line is a declaration, not a bump), and two setters at
    once.
    """
    _seed_tree(tmp_path, version="0.1.6")
    assert bump.main(["--root", str(tmp_path), "--set-version", "0.1.12"]) == 0
    assert bump.read_version(tmp_path) == "0.1.12"
    settled = _bytes(tmp_path)
    assert bump.main(["--root", str(tmp_path), "--set-version", "0.1.12"]) == 1
    assert bump.main(["--root", str(tmp_path), "--set-version", "0.1.5"]) == 1
    assert bump.main(["--root", str(tmp_path), "--set-version", "0.1.150"]) == 1
    assert bump.main(["--root", str(tmp_path), "--set-version", "0.2.0"]) == 1
    assert bump.main(["--root", str(tmp_path), "--set-version", "1.0.0"]) == 1
    assert bump.main(["--root", str(tmp_path), "--set-version", "0.1.9.x"]) == 1
    assert bump.main(["--root", str(tmp_path), "--by", "1",
                      "--set-version", "0.1.13"]) == 1
    assert bump.main(["--root", str(tmp_path), "--position", "13",
                      "--set-version", "0.1.13"]) == 1
    assert _bytes(tmp_path) == settled            # every refusal wrote nothing


def test_position_names_the_number_the_commit_owns(tmp_path):
    """`--position N` is the CI's mode: the target is the rule's output (E-013)."""
    _seed_tree(tmp_path, version="0.1.14")
    assert bump.main(["--root", str(tmp_path), "--position", "15"]) == 0
    assert bump.read_version(tmp_path) == "0.1.15"
    # the block boundary: position 100 is 0.2.0, and that is not a choice
    assert bump.main(["--root", str(tmp_path), "--set-version", "0.1.99"]) == 0
    assert bump.main(["--root", str(tmp_path), "--position", "100"]) == 0
    assert bump.read_version(tmp_path) == "0.2.0"
    settled = _bytes(tmp_path)
    assert bump.main(["--root", str(tmp_path), "--position", "100"]) == 1  # empty bump
    assert bump.main(["--root", str(tmp_path), "--position", "5"]) == 1    # backwards
    assert _bytes(tmp_path) == settled


def test_declared_line_takes_patches_but_not_new_lines(tmp_path):
    """A declared 1.0.x line accepts patches; it can never be bumped elsewhere.

    A declared line's patch is free (the CI checks it against the line's
    opening tag, which is where git lives), but no setter may walk into
    another line: that is a declaration or nothing.
    """
    _seed_tree(tmp_path, version="1.0.0")
    assert bump.main(["--root", str(tmp_path), "--set-version", "1.0.1"]) == 0
    assert bump.main(["--root", str(tmp_path), "--set-version", "1.0.7"]) == 0
    assert bump.main(["--root", str(tmp_path), "--set-version", "1.1.0"]) == 1
    assert bump.main(["--root", str(tmp_path), "--set-version", "2.0.0"]) == 1
    assert bump.main(["--root", str(tmp_path), "--position", "7"]) == 1
    assert bump.read_version(tmp_path) == "1.0.7"


def test_workflow_derives_the_number_absolutely(tmp_path):
    """Every number comes from git, and a tree that disagrees is refused (E-012).

    A relative bump would keep whatever offset the tree had — exactly how
    twelve commits shipped with no number of their own. The workflow must
    compute each commit's expected number from git alone (position after the
    first commit, `--position` on the automatic line, the opening tag on a
    declared one) and go red, naming the number, when the tree disagrees:
    verification, never a repair.
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    for required in ("--position", "rev-list --count", "max-parents=0",
                     "scripts/versioning.py"):
        assert required in text, f"workflow misses {required!r}"
    assert "--by" not in text, "the relative bump must not come back"
    verify = text[text.index(TAG_STEP):]
    assert "exit 1" in verify, "a wrong number must fail the job"
    assert "its position owns" in verify, \
        "the refusal must name the number the position owns"


def test_workflow_requires_an_explicit_line_declaration():
    """Leaving the automatic line is a human act, and it must be declared (E-013).

    A tree claiming `1.0.0` is not enough: the commit must carry the `[line
    1.0.0]` marker — naming the line's OPENING, derived from the tree, never a
    stale number from a previous loop — and then the run tags it and leaves it
    alone (no bump commit, no write: that commit IS the release). An
    undeclared major line is refused before any pointer is published.
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    for required in ("[line ", 'grep -qF "[line $BASE.0]"', "::error::",
                     "never declared"):
        assert required in text, f"workflow misses {required!r}"
    # every number is verified — declarations included — before the first tag
    assert text.index('if [ "$FAIL" -ne 0 ]') < text.index("git tag -a"), \
        "the refusal must come before anything is published"


def test_print_version_is_bare_and_read_only(tmp_path, capsys):
    """CI names the tag from this output: it must be the bare number, no writes.

    A wrapped or decorated line ("version: 1.2.3") would silently become the
    tag name `vversion: 1.2.3`; a write in this mode would tag a tree the
    record never approved.
    """
    _seed_tree(tmp_path)
    before = _bytes(tmp_path)
    assert bump.main(["--root", str(tmp_path), "--print-version"]) == 0
    assert capsys.readouterr().out.strip() == "0.1.3"
    assert _bytes(tmp_path) == before           # read-only, byte for byte
    # skew still refuses: the tag must never name a tree whose manifests lie
    (tmp_path / ".plugin" / "plugin.json").write_text(
        json.dumps({"name": "metodoloji", "version": "9.9.9"}), encoding="utf-8")
    assert bump.main(["--root", str(tmp_path), "--print-version"]) == 1


def test_workflow_tags_the_release():
    """E-011 → E-014: pointers are published for VERIFIED numbers, nothing else.

    Three things keep the pointer honest: the tag name comes from the rule
    (`versioning.py --position` / the line's opening — never typed in YAML),
    every commit's number is checked before the FIRST pointer goes out, and a
    re-run never re-tags an existing name. There is no bump commit left to
    guard — the workflow creates no commit at all, so there is no
    `[skip-version]` and no loop that could feed itself.
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    for required in ("versioning.py --position", "git tag -a", "refs/tags/",
                     'git push origin "refs/tags/$TAG"'):
        assert required in text, f"workflow misses {required!r}"
    tag_at = text.index("git tag -a")
    assert text.index(TAG_STEP) < tag_at, \
        "the numbers are verified before the first pointer is published"
    assert text.index('if [ "$FAIL" -ne 0 ]') < tag_at, \
        "a single wrong number must stop every tag"
    assert "already exists" in text, "a re-run must not re-tag a name"
    code = "\n".join(line for line in text.splitlines()
                     if not line.lstrip().startswith("#"))
    assert "git commit" not in code, "E-014: the pipeline may never create a commit"


def test_workflow_is_version_only():
    """CI is special to versioning (E-010 … E-014): it verifies and tags, nothing else.

    The workflow must derive every number from the rule, and must never grow
    a test/audit step — the six checks stay local by design. Since E-014 it
    also may not write a version or create a commit: those are the hook's job
    (bump-version.py is called by `.githooks`, never by CI), so the workflow
    holds no write mode, no bump step and no guard — there is nothing to loop.
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    for required in ("scripts/versioning.py", "contents: write", "--position",
                     "rev-list"):
        assert required in text, f"workflow misses {required!r}"
    # executable lines only — the header comment NAMES the local audits to say
    # they stay out; the enforcement is that no step RUNS them.
    code = "\n".join(line for line in text.splitlines()
                     if not line.lstrip().startswith("#"))
    for forbidden in ("pytest", "check-plugin", "check-custom",
                      "check-methodology", "check-techdebt", "check-handoff"):
        assert forbidden not in code, f"CI grew an audit step: {forbidden}"
    for forbidden in ("git commit", "bump-version", "--set-version", "--by",
                      "[skip-version]"):
        assert forbidden not in code, f"CI writes or commits: {forbidden}"

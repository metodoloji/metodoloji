"""The committed hooks, against real repositories (E-014).

The pipeline test proves the verifier; these tests prove the writer. Each test
builds a throwaway repository, installs the repository's own hooks exactly like
a developer clone does (`git config core.hooksPath .githooks`), and makes real
commits through them — the hooks are the thing under test, not a re-reading of
them.

The contract under test, one property per test: the number is written by the
commit that owns it; an already-correct tree is left byte-identical; unstaged
work in a version-owned file is refused before any write; a merge commit —
which `git merge` creates with the merged tree and no pre-commit — writes its
number through post-merge the moment it exists, while a conflicted merge takes
the union of both parents' ranges when the author commits it; a declared line
counts its patches from the opening tag; and a clone that bypassed the hook
still gets caught — by the pipeline, which the last test here refuses to call
a loophole.
"""

import json
import os
import pathlib
import shutil
import subprocess

import pytest

PLUGIN = pathlib.Path(__file__).resolve().parents[2]
HOOK = PLUGIN / ".githooks" / "pre-commit"
SCRIPTS = ("bump-version.py", "versioning.py")
MANIFESTS = (".claude-plugin/plugin.json", ".claude-plugin/marketplace.json",
             ".plugin/plugin.json", ".plugin/marketplace.json", "pyproject.toml")

_ENV = {**os.environ,
        "GIT_AUTHOR_NAME": "author", "GIT_AUTHOR_EMAIL": "author@example.invalid",
        "GIT_COMMITTER_NAME": "author", "GIT_COMMITTER_EMAIL": "author@example.invalid"}


def _git(root: pathlib.Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", *args], cwd=root, capture_output=True,
                          text=True, encoding="utf-8", env=_ENV,
                          stdin=subprocess.DEVNULL,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if check:
        assert proc.returncode == 0, f"git {args}: {proc.stdout}{proc.stderr}"
    return proc.stdout.strip()


def _seed_version(root: pathlib.Path, version: str) -> None:
    """Every file the hook must keep in agreement, all saying `version`."""
    for rel in MANIFESTS:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if rel.endswith(".toml"):
            path.write_text(f'[project]\nname = "metodoloji"\nversion = "{version}"\n',
                            encoding="utf-8")
        else:
            path.write_text(json.dumps({"name": "metodoloji", "version": version}),
                            encoding="utf-8")
    (root / "commands").mkdir(exist_ok=True)
    (root / "README.md").write_text(
        f"![v](https://img.shields.io/badge/version-{version}-0b7285)\n", encoding="utf-8")
    (root / "commands" / "init.md").write_text(f"cache e.g. `.../{version}/`\n",
                                               encoding="utf-8")


def _version_at(root: pathlib.Path, rev: str = "HEAD") -> str:
    text = _git(root, "show", f"{rev}:.plugin/plugin.json")
    return json.loads(text)["version"]


def _commit(root: pathlib.Path, message: str, *, no_verify: bool = False,
            check: bool = True) -> subprocess.CompletedProcess:
    args = ["commit", "-q", "--allow-empty", "-m", message]
    if no_verify:
        args.append("--no-verify")
    proc = subprocess.run(["git", *args], cwd=root, capture_output=True,
                          text=True, encoding="utf-8", env=_ENV,
                          stdin=subprocess.DEVNULL,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if check:
        assert proc.returncode == 0, f"commit failed:\n{proc.stdout}\n{proc.stderr}"
    return proc


@pytest.fixture
def repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """A real repository with the repository's own hook wired, first commit in."""
    work = tmp_path / "work"
    _git(tmp_path, "init", "-q", "-b", "main", str(work))
    (work / "scripts").mkdir(parents=True)
    for name in SCRIPTS:
        shutil.copy(PLUGIN / "scripts" / name, work / "scripts" / name)
    shutil.copytree(PLUGIN / ".githooks", work / ".githooks")
    _git(work, "config", "core.hooksPath", ".githooks")
    _seed_version(work, "0.1.0")
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "first commit")     # position 0: 0.1.0, no write
    return work


def test_the_hook_file_is_committed_and_executable():
    """The hooks must ship with the repository — an uncommitted hook is no hook."""
    for rel in ("pre-commit", "post-merge"):
        path = PLUGIN / ".githooks" / rel
        assert path.is_file(), f".githooks/{rel} is missing"
        text = path.read_text(encoding="utf-8")
        assert text.startswith("#!/bin/sh"), f"{rel} must be a sh script"
        assert "\r" not in text, f"CRLF line endings would break sh on checkout ({rel})"
    assert HOOK.read_text(encoding="utf-8").count("--merge-head") >= 1, \
        "post-merge writes the merge's number through the writer's head mode"


def test_position_zero_keeps_the_baseline(repo):
    """The first commit owns 0.1.0 — the baseline needs no writing either.

    The fixture's first commit already held 0.1.0 in its tree, so the hook had
    nothing to write (silent pass), and the NEXT commit is position 1: the
    counter starts at the baseline, not before it.
    """
    assert _version_at(repo) == "0.1.0"
    _commit(repo, "second thought")
    assert _version_at(repo) == "0.1.1"


def test_the_commit_that_owns_the_number_writes_it(repo):
    """Every new commit raises the patch to its own position (E-014's claim)."""
    _commit(repo, "one")                     # position 1
    assert _version_at(repo) == "0.1.1"
    _commit(repo, "two")                     # position 2
    assert _version_at(repo) == "0.1.2"
    # the number is IN the commit, not merely in the working tree
    assert _version_at(repo, "HEAD~1") == "0.1.1"


def test_an_already_correct_tree_is_left_alone(repo):
    """The author's own hand-bump is not second-guessed — bytes stay identical.

    This is the complaint E-014 fixes: the tree already holds the number the
    position owns, so there is nothing to write, nothing to stage, and the
    commit the author made IS the release.
    """
    _seed_version(repo, "0.1.1")             # hand-bump to the position's number
    _git(repo, "add", "-A")
    before = {p: (repo / p).read_bytes() for p in (*MANIFESTS, "README.md",
                                                   "commands/init.md")}
    proc = _commit(repo, "hand-bumped already")
    assert "this commit owns" not in proc.stderr + proc.stdout, \
        "the hook must stay silent when the tree already holds the number"
    after = {p: (repo / p).read_bytes() for p in before}
    assert before == after, "an already-correct tree must not be rewritten"
    assert _version_at(repo) == "0.1.1"


def test_unstaged_work_in_a_version_file_is_refused(repo):
    """The number is never written over work that is not in this commit."""
    readme = repo / "README.md"
    readme.write_text(readme.read_text(encoding="utf-8") + "wip\n", encoding="utf-8")
    proc = _commit(repo, "half-done README", check=False)
    assert proc.returncode != 0, "the hook must refuse unstaged version-owned work"
    assert "unstaged" in proc.stderr, proc.stderr
    assert _version_at(repo) == "0.1.0", "nothing may be written on refusal"


def test_manifest_skew_is_refused_before_any_write(repo):
    """§6d3: five manifests, one number — skew is red before a byte is written."""
    (repo / ".plugin" / "plugin.json").write_text(
        json.dumps({"name": "metodoloji", "version": "9.9.9"}), encoding="utf-8")
    _git(repo, "add", "-A")
    other = (repo / "pyproject.toml").read_bytes()
    proc = _commit(repo, "skewed manifests", check=False)
    assert proc.returncode != 0, "skew must refuse the commit"
    assert (repo / "pyproject.toml").read_bytes() == other, "no write on refusal"


def _parents(work: pathlib.Path) -> int:
    return len(_git(work, "rev-list", "--parents", "-n1", "HEAD").split()) - 1


def test_a_clean_merge_is_written_by_the_merge_commit_itself(repo):
    """`git merge` runs no pre-commit — the merge commit writes itself (E-014).

    Both branches hold the same number (each commit wrote its own position),
    so the merge is CLEAN: git creates the commit with that stale tree, and no
    hook can change it beforehand — the tree is written before pre-merge-commit
    and prepare-commit-msg even run, so a staged write in either is dropped.
    The only moment left is after the commit exists: `.githooks/post-merge`
    computes HEAD's own position and amends the number in, before the commit
    can be pushed. Position 4 = the three commits after the first plus the
    merge itself — exactly `rev-list --count root..M` the pipeline verifies.
    """
    _commit(repo, "one")                     # position 1
    _git(repo, "checkout", "-q", "-b", "side")
    _commit(repo, "side work")               # position 2 on side (0.1.2)
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "main work")               # position 2 on main (0.1.2)
    out = _git(repo, "merge", "--no-ff", "-m", "merge side", "side")
    assert "CONFLICT" not in out, "both trees hold 0.1.2 — the merge must be clean"

    assert _version_at(repo) == "0.1.4"      # union + the merge itself
    assert _parents(repo) == 2, "it must still be a merge commit"
    assert _git(repo, "log", "-1", "--pretty=%s") == "merge side"
    assert _git(repo, "status", "--porcelain", "-uno") == "", \
        "amend leaves no residue"
    root = _git(repo, "rev-list", "--max-parents=0", "HEAD")
    assert _git(repo, "rev-list", "--count", f"{root}..HEAD") == "4", \
        "the number must equal the position the pipeline computes for it"


def test_a_fast_forward_merge_writes_nothing(repo):
    """A fast-forward creates no commit: the commit it lands on owns its number."""
    _git(repo, "checkout", "-q", "-b", "topic")
    _commit(repo, "topic work")              # position 1 -> 0.1.1
    topic = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "main")     # still the position-0 baseline
    _git(repo, "merge", "topic")             # fast-forward, no new commit

    assert _git(repo, "rev-parse", "HEAD") == topic, "no commit was created"
    assert _version_at(repo) == "0.1.1"      # the landed commit's own number


def test_a_conflicted_merge_is_written_when_the_author_commits_it(repo):
    """A merge with conflicts is committed by the author — so pre-commit runs.

    MERGE_HEAD is present in that commit, and the hook gives it the union of
    both parents' ranges plus itself: {one, main work, side A, side B} + 1 =
    position 5 = 0.1.5, the same `rev-list --count root..M` the pipeline
    verifies. Taking either side at the conflicts is fine — the number is
    rewritten to the position the commit owns, not inherited from a parent.
    """
    _commit(repo, "one")                     # position 1
    _git(repo, "checkout", "-q", "-b", "side")
    _commit(repo, "side A")                  # 0.1.2
    _commit(repo, "side B")                  # 0.1.3 — now the trees disagree
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "main work")               # 0.1.2
    before = _git(repo, "rev-parse", "HEAD")
    _git(repo, "merge", "--no-ff", "-m", "merge side", "side", check=False)
    assert _git(repo, "rev-parse", "HEAD") == before, "the conflict must stop the merge"
    assert _git(repo, "rev-parse", "-q", "--verify", "MERGE_HEAD", check=False)

    _git(repo, "checkout", "--ours", "--", *MANIFESTS, "README.md",
         "commands/init.md")                # resolve: either side is fine
    _git(repo, "add", "-A")
    _commit(repo, "merge side")              # pre-commit: union + itself
    assert _version_at(repo) == "0.1.5"
    assert _parents(repo) == 2, "it must still be a merge commit"


def test_a_declared_line_counts_its_patches_from_the_opening(repo):
    """Declaration first (the hook leaves it alone), then positional patches.

    The human writes 1.0.0 by hand with the marker; the pipeline tags it. From
    then on the hook itself writes 1.0.1, 1.0.2 … — counted from v1.0.0, not
    from the automatic counter.
    """
    _seed_version(repo, "1.0.0")
    _git(repo, "add", "-A")
    _commit(repo, "1.0.0 release \\\n[line 1.0.0]")
    assert _version_at(repo) == "1.0.0"      # the declaration passes untouched
    _git(repo, "tag", "-a", "v1.0.0", "-m", "release v1.0.0")

    _commit(repo, "work on the new line")
    assert _version_at(repo) == "1.0.1"      # patch 1 since the opening tag
    _commit(repo, "more work")
    assert _version_at(repo) == "1.0.2"


def test_a_missing_opening_tag_stops_the_declared_line(repo):
    """A declared line whose opening tag was never fetched cannot be counted.

    The hook refuses instead of inventing a patch number — loud, with the
    command that fixes the clone.
    """
    _seed_version(repo, "1.0.4")
    _git(repo, "add", "-A")
    proc = _commit(repo, "claiming an untagged line", check=False)
    assert proc.returncode != 0, "an uncountable line must refuse"
    assert "fetch" in proc.stderr, proc.stderr


def test_no_verify_is_caught_downstream_not_here(repo):
    """The hook is bypassable on purpose; the pipeline closes the loop.

    A `--no-verify` commit keeps the previous number in its tree — which is
    exactly the state `release-numbers.yml` refuses with the number it should
    carry. This test pins the seam: the bypass leaves a stale tree HERE, so
    the pipeline test's red scenario is not testing a fantasy state.
    """
    _commit(repo, "one")                     # tree now says 0.1.1
    _commit(repo, "bypassed", no_verify=True)
    assert _version_at(repo) == "0.1.1", \
        "--no-verify must leave the stale number in place for CI to refuse"
    assert _version_at(repo, "HEAD~1") == "0.1.1"

"""Tests for the retro release-tagging tool (E-012).

The rule under test — release N is the Nth commit after the baseline — is only
worth anything if the tool refuses the three ways it can lie: minting a number
whose anchor is not the first commit, silently re-pointing a published tag,
and leaving a release-looking tag it cannot place. Every refusal is checked
against the repository state afterwards (nothing half-done), and the happy
path is checked against real git objects (annotated, correct commit, remote).
"""

import os
import pathlib
import re
import subprocess
import sys

PLUGIN = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = PLUGIN / "scripts" / "release-tags.py"

# A repo-local identity, so the tests never depend on the machine's git config
# (and never write to it).
_ENV = {**os.environ,
        "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid"}


def _git(root: pathlib.Path, *args: str, check: bool = True):
    # stdin=DEVNULL + CREATE_NO_WINDOW: never inherit a console handle pair the
    # suite cannot duplicate on Windows (the convention in test_check_handoff).
    proc = subprocess.run(["git", *args], cwd=root, capture_output=True,
                          text=True, encoding="utf-8", env=_ENV,
                          stdin=subprocess.DEVNULL,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if check:
        assert proc.returncode == 0, f"git {args}: {proc.stdout}{proc.stderr}"
    return proc.stdout.strip()


def _seed(root: pathlib.Path, commits: int = 4) -> list[str]:
    """A miniature history: `commits` commits, oldest first."""
    _git(root, "init", "-q", "-b", "main")
    shas = []
    for i in range(commits):
        (root / f"f{i}.txt").write_text(f"{i}\n", encoding="utf-8")
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", f"commit {i}")
        shas.append(_git(root, "rev-parse", "HEAD"))
    return shas


def _cli(root: pathlib.Path, *args: str):
    proc = subprocess.run([sys.executable, str(SCRIPT), "--root", str(root), *args],
                          capture_output=True, text=True, encoding="utf-8", env=_ENV,
                          stdin=subprocess.DEVNULL,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return proc.returncode, proc.stdout, proc.stderr


def _target(root: pathlib.Path, tag: str) -> str:
    return _git(root, "rev-parse", "-q", "--verify", f"refs/tags/{tag}^{{commit}}",
                check=False)


def test_plan_maps_every_commit_after_the_baseline(tmp_path):
    shas = _seed(tmp_path, commits=4)
    _git(tmp_path, "tag", "v0.1.0", shas[0])          # lightweight, like the repo
    code, out, err = _cli(tmp_path, "--plan")
    assert code == 0, err
    for position, sha in enumerate(shas):
        line = next(l for l in out.splitlines() if sha[:9] in l)
        assert f"v0.1.{position}" in line, line
    # the row actions, in order — nothing exists but the baseline itself
    assert re.findall(r"(ok|create|move)(?=\s|\()", out) == ["ok"] + ["create"] * 3
    assert "create: 3" in out
    assert "lightweight" in out                        # kind is reported, not hidden


def test_plan_writes_nothing(tmp_path):
    shas = _seed(tmp_path, commits=3)
    _git(tmp_path, "tag", "v0.1.0", shas[0])
    before = {p.name for p in tmp_path.iterdir()}
    assert _cli(tmp_path, "--plan")[0] == 0
    assert _git(tmp_path, "tag", "--list") == "v0.1.0"
    assert {p.name for p in tmp_path.iterdir()} == before
    assert _git(tmp_path, "status", "--porcelain") == ""


def test_apply_creates_annotated_tags_and_no_commit(tmp_path):
    shas = _seed(tmp_path, commits=3)
    _git(tmp_path, "tag", "v0.1.0", shas[0])
    head_before = _git(tmp_path, "rev-parse", "HEAD")
    code, out, err = _cli(tmp_path, "--apply")
    assert code == 0, err
    assert "created: ['v0.1.1', 'v0.1.2']" in out, out
    for position, sha in enumerate(shas):
        tag = f"v0.1.{position}"
        assert _target(tmp_path, tag) == sha, tag
        if position:                                   # the baseline stays as it was
            assert _git(tmp_path, "cat-file", "-t", f"refs/tags/{tag}") == "tag"
    # tagging is metadata only: no commit, no worktree change
    assert _git(tmp_path, "rev-parse", "HEAD") == head_before
    assert _git(tmp_path, "status", "--porcelain") == ""
    assert "not pushed" in out


def test_misplaced_tag_is_refused_then_moved_on_request(tmp_path):
    shas = _seed(tmp_path, commits=4)
    _git(tmp_path, "tag", "v0.1.0", shas[0])
    _git(tmp_path, "tag", "-a", "v0.1.1", "-m", "hand-made, wrong commit", shas[3])
    code, out, err = _cli(tmp_path, "--apply")
    assert code == 1
    assert "move-mismatched" in err, err
    assert _target(tmp_path, "v0.1.1") == shas[3]      # nothing moved,
    assert _target(tmp_path, "v0.1.2") == ""           # and nothing was half-created
    code, out, err = _cli(tmp_path, "--apply", "--move-mismatched")
    assert code == 0, err
    assert "re-pointed: ['v0.1.1']" in out, out
    assert _target(tmp_path, "v0.1.1") == shas[1]      # now on the commit the rule names
    assert _git(tmp_path, "cat-file", "-t", "refs/tags/v0.1.1") == "tag"


def test_baseline_must_be_the_first_commit(tmp_path):
    shas = _seed(tmp_path, commits=3)
    _git(tmp_path, "tag", "v0.1.0", shas[1])           # anchored one commit too late
    code, out, err = _cli(tmp_path, "--apply")
    assert code == 1
    assert "first commit" in err, err
    assert _git(tmp_path, "tag", "--list") == "v0.1.0"


def test_missing_or_malformed_baseline_refuses(tmp_path):
    _seed(tmp_path, commits=2)
    assert _cli(tmp_path, "--apply")[0] == 1           # no v0.1.0 at all
    code, _, err = _cli(tmp_path, "--apply", "--baseline", "v0.2.3")
    assert code == 1 and "position-0 name" in err, err


def test_unplaceable_tag_blocks_apply(tmp_path):
    shas = _seed(tmp_path, commits=2)
    _git(tmp_path, "tag", "v0.1.0", shas[0])
    _git(tmp_path, "tag", "v0.1.9", shas[1])           # outside the history's range
    code, out, err = _cli(tmp_path, "--apply")
    assert code == 1
    assert "UNPLACEABLE  v0.1.9" in out and "cannot place" in err, (out, err)
    assert _target(tmp_path, "v0.1.1") == ""           # refused before creating anything


def test_push_publishes_created_and_moved_tags(tmp_path):
    shas = _seed(tmp_path, commits=3)
    _git(tmp_path, "tag", "v0.1.0", shas[0])
    _git(tmp_path, "tag", "-a", "v0.1.1", "-m", "wrong commit", shas[2])
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    _git(tmp_path, "remote", "add", "origin", str(remote))
    _git(tmp_path, "push", "-q", "origin", "main", "refs/tags/v0.1.0")

    code, out, err = _cli(tmp_path, "--apply", "--move-mismatched", "--push")
    assert code == 0, err
    published = _git(remote, "for-each-ref", "--format=%(refname)", "refs/tags")
    for tag in ("v0.1.0", "v0.1.1", "v0.1.2"):
        assert f"refs/tags/{tag}" in published, (tag, published)
    # the re-pointed tag must reach the remote at the commit the rule names
    assert _git(remote, "rev-parse", "refs/tags/v0.1.1^{commit}") == shas[1]
    assert _git(remote, "rev-parse", "refs/tags/v0.1.2^{commit}") == shas[2]
    assert "pushed: ['v0.1.1', 'v0.1.2']" in out, out


def test_no_remote_is_a_loud_refusal(tmp_path):
    shas = _seed(tmp_path, commits=2)
    _git(tmp_path, "tag", "v0.1.0", shas[0])
    code, out, err = _cli(tmp_path, "--apply", "--push")
    assert code == 1
    assert "no remote" in err, err
    assert _target(tmp_path, "v0.1.1") == ""           # the refusal precedes creation

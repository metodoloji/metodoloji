"""End-to-end tests for the release pipeline's own step (E-012 … E-014).

The workflow is the only place the release rule meets a real repository, and
GitHub Actions cannot run here — so these tests extract the REAL shell body of
the job's single step out of `.github/workflows/release-numbers.yml` and run it
against throwaway repositories with a real remote. What passes here is the
step's own code, not a re-reading of it: the harness emulates only the
`${{ github.* }}` substitutions and refuses to run a body that still contains
an un-emulated template.

Since E-014 the job VERIFIES and TAGS, nothing else — it writes no version and
creates no commit. The number is written at commit time by the repository's
own pre-commit hook (`.githooks/pre-commit`), which these scratch repositories
install too, so each scenario exercises the real end-to-end contract: the
commit that owns the number writes it, and the pipeline refuses any tree that
lies about its position.

Scenarios, one per way the rule can be right or wrong: the ordinary push (the
tree already holds the number, the tag is published, HEAD does not move), the
block boundary (position 100 IS 0.2.0), a declared line opening (tagged, never
rewritten), a declared line taking its next patch (counted from the opening
tag), a stale tree pushed past the hook (refused, naming the number it should
carry), an impossible number, an undeclared major line (refused), and a re-run
after success (idempotent: existing tags are never re-pointed), and a
rewritten history (no fetchable `before`: the range falls back to the newest
tag — never to the whole retro history).
"""

import json
import os
import pathlib
import re
import shutil
import subprocess

import pytest

PLUGIN = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = PLUGIN / ".github" / "workflows" / "release-numbers.yml"
STEP = "Verify the numbers and tag every commit this push carried"
SCRIPTS = ("bump-version.py", "versioning.py")
MANIFESTS = (".claude-plugin/plugin.json", ".claude-plugin/marketplace.json",
             ".plugin/plugin.json", ".plugin/marketplace.json", "pyproject.toml")

_ENV = {**os.environ,
        "GIT_AUTHOR_NAME": "author", "GIT_AUTHOR_EMAIL": "author@example.invalid",
        "GIT_COMMITTER_NAME": "author", "GIT_COMMITTER_EMAIL": "author@example.invalid"}


def _bash() -> str:
    """A real bash (Git Bash on Windows; WSL would not see the scratch trees)."""
    candidates = [r"C:\Program Files\Git\usr\bin\bash.exe",
                  r"C:\Program Files\Git\bin\bash.exe",
                  shutil.which("bash") or "bash"]
    for cand in candidates:
        if pathlib.Path(cand).is_file():
            return cand
    return "bash"


def _step_body() -> str:
    """The `run:` body of the pipeline's single step."""
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    starts = [i for i, line in enumerate(lines)
              if line.startswith("      - name: ") and STEP in line]
    assert len(starts) == 1, f"expected one step named like {STEP!r}, got {len(starts)}"
    body = []
    for line in lines[starts[0] + 1:]:
        if line.startswith("      - name:"):
            break
        if len(line) > 10 and line[:10] == " " * 10:      # deeper than a YAML key
            body.append(line[8:])
    assert body, f"step {STEP!r} has no run body"
    return "\n".join(body)


def _subst(body: str, before: str) -> str:
    filled = body.replace("${{ github.event.before }}", before)
    assert "${{" not in filled, "the harness must emulate every template in the body"
    return filled


def _git(root: pathlib.Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", *args], cwd=root, capture_output=True,
                          text=True, encoding="utf-8", env=_ENV,
                          stdin=subprocess.DEVNULL,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if check:
        assert proc.returncode == 0, f"git {args}: {proc.stdout}{proc.stderr}"
    return proc.stdout.strip()


def _seed_version(root: pathlib.Path, version: str) -> None:
    """Every file the bump must keep in agreement, all saying `version`."""
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


def _bootstrap(tmp_path: pathlib.Path, positions: int) -> tuple[pathlib.Path, pathlib.Path]:
    """A repo whose history holds `positions` release points, plus a real remote.

    The repository's own pre-commit hook is installed here, exactly like a
    developer clone (`git config core.hooksPath .githooks`), so every commit
    below — and every commit a test makes later — owns the number its position
    gives it. Position 0 (the first commit) is tagged v0.1.0 and the tag chain
    is complete, exactly like the real repository after E-012's repair.
    """
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    _git(tmp_path, "init", "-q", "-b", "main", str(work))
    (work / "scripts").mkdir(parents=True)
    for name in SCRIPTS:
        shutil.copy(PLUGIN / "scripts" / name, work / "scripts" / name)
    shutil.copytree(PLUGIN / ".githooks", work / ".githooks")
    _git(work, "config", "core.hooksPath", ".githooks")
    _seed_version(work, "0.1.0")
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "first commit")
    _git(work, "tag", "-a", "v0.1.0", "-m", "release v0.1.0")
    for position in range(1, positions):
        # --allow-empty: the hook stages the version files itself, but a seed
        # commit has no other content — the position math must not care.
        _git(work, "commit", "-q", "--allow-empty", "-m", f"seed {position}")
        _git(work, "tag", "-a", f"v0.1.{position}", "-m", f"release v0.1.{position}")
    _git(work, "remote", "add", "origin", str(remote))
    _git(work, "push", "-q", "-u", "origin", "main", "--tags")
    return work, remote


def _author_commit(work: pathlib.Path, message: str, *, version: str | None = None,
                   no_verify: bool = False) -> str:
    """A human pushes: the hook normally writes the number; `version` fakes a

    hand-written tree and `no_verify` fakes the hook being bypassed — both are
    ways a tree can end up lying to the pipeline, which is exactly what the
    refusal scenarios need. Returns `before` (the push's first carried commit's
    parent), i.e. the `github.event.before` of the resulting push.
    """
    before = _git(work, "rev-parse", "HEAD")
    if version is not None:
        _seed_version(work, version)
    _git(work, "add", "-A")
    args = ["commit", "-q", "--allow-empty", "-m", message]
    if no_verify:
        args.append("--no-verify")
    _git(work, *args)
    _git(work, "push", "-q", "origin", "main")
    return before


class _Job:
    """The job's single step, run the way Actions runs it."""

    def __init__(self, work: pathlib.Path, before: str):
        self.work = work
        self.before = before
        self.log = {}

    def run(self) -> "_Job":
        sha = _git(self.work, "rev-parse", "HEAD")
        del sha  # the step derives everything from git itself, not from $GITHUB_SHA
        # -eo pipefail: Actions' own bash default. A refusal inside the step must
        # fail the step (that IS the enforcement), so the harness must not hide
        # a non-zero exit behind a later successful command.
        proc = subprocess.run([_bash(), "-eo", "pipefail", "-c",
                               _subst(_step_body(), self.before)],
                              cwd=self.work, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=_ENV,
                              stdin=subprocess.DEVNULL,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.log[STEP] = proc
        return self

    def ok(self) -> None:
        proc = self.log[STEP]
        assert proc.returncode == 0, f"the step failed:\n{proc.stdout}\n{proc.stderr}"

    def failed(self) -> str:
        proc = self.log[STEP]
        assert proc.returncode != 0, f"the step unexpectedly succeeded:\n{proc.stdout}"
        return proc.stdout + proc.stderr


def _version_at(work: pathlib.Path, rev: str) -> str:
    text = _git(work, "show", f"{rev}:.plugin/plugin.json")
    return json.loads(text)["version"]


def _tag_target(work: pathlib.Path, tag: str) -> str:
    return _git(work, "rev-parse", "-q", "--verify", f"refs/tags/{tag}^{{commit}}",
                check=False)


def test_ordinary_push_lands_on_the_position_it_owns(tmp_path):
    """The commit owns the number, the pipeline publishes the pointer (E-014).

    The hook wrote 0.1.3 into the tree at commit time, so the pipeline's whole
    job is to check that claim against the commit's position and tag it. No
    bump commit exists to create — HEAD must not move, ever.
    """
    work, _ = _bootstrap(tmp_path, positions=3)              # positions 0…2 tagged
    before = _author_commit(work, "a normal change")
    authored = _git(work, "rev-parse", "HEAD")
    _Job(work, before).run().ok()

    assert _git(work, "rev-parse", "HEAD") == authored       # no commit was added
    assert _version_at(work, "HEAD") == "0.1.3"              # the hook's own number
    assert _tag_target(work, "v0.1.3") == authored           # the chain has no hole


def test_the_pipeline_never_creates_a_commit(tmp_path):
    """The complaint E-014 exists for: a push may not add a version-only commit.

    Two of the last four commits of the old pipeline existed only to carry a
    number. The step's own body must contain no `git commit`, and running it
    must leave the branch and its history byte-identical.
    """
    work, _ = _bootstrap(tmp_path, positions=3)
    before = _author_commit(work, "a normal change")
    head_before = _git(work, "rev-parse", "HEAD")
    log_before = _git(work, "log", "--format=%H %s")

    _Job(work, before).run().ok()

    assert "git commit" not in _step_body(), "the pipeline grew a commit step"
    assert _git(work, "rev-parse", "HEAD") == head_before
    assert _git(work, "log", "--format=%H %s") == log_before


def test_a_rewritten_history_verifies_only_what_the_push_carried(tmp_path):
    """A force-push has no fetchable `before` — the range may not become "all".

    Squashing the day's commits rewrote history, and the run that followed
    found `before` outside the fetched range: falling back to `HEAD` verified
    the WHOLE history and red-flagged the documented retro era — trees written
    before the rule existed, whose tags name the position and whose mismatch
    is listed honestly in docs/VERSION-HISTORY.md. The step must fall back to
    the newest tag (what the chain already accepted) and verify only what came
    after it, exactly like this push did.
    """
    work, _ = _bootstrap(tmp_path, positions=3)          # 0, 1, 2 tagged
    # A pre-rule commit: the hook was bypassed, the tree kept the old number,
    # and the retro tag names the position anyway — the documented convention.
    retro = _author_commit(work, "bypassed", no_verify=True)
    _git(work, "tag", "-a", "v0.1.3", "-m", "release v0.1.3")
    _git(work, "push", "-q", "origin", "refs/tags/v0.1.3")
    assert _version_at(work, retro) == "0.1.2"          # the tree lies, by era
    _author_commit(work, "a normal change")             # the hook writes 0.1.4
    head = _git(work, "rev-parse", "HEAD")

    job = _Job(work, "0" * 40).run()                     # before: unknown range
    job.ok()
    assert "verified and tagged: v0.1.3..HEAD" in job.log[STEP].stdout, \
        "the fallback range must stop at the newest tag, not run to the root"
    assert _tag_target(work, "v0.1.4") == head
    assert _version_at(work, retro) == "0.1.2", "the retro tree stays as documented"


def test_block_boundary_is_arithmetic_not_a_decision(tmp_path):
    """Position 100 IS 0.2.0 — the boundary needs no release meeting (E-013)."""
    work, _ = _bootstrap(tmp_path, positions=100)            # positions 0…99
    before = _author_commit(work, "commit 100")
    authored = _git(work, "rev-parse", "HEAD")
    _Job(work, before).run().ok()

    assert _version_at(work, "HEAD") == "0.2.0"              # the hook's arithmetic
    assert _tag_target(work, "v0.1.99") == _git(work, "rev-parse", "HEAD~1")
    assert _tag_target(work, "v0.2.0") == authored


def test_declared_line_is_tagged_and_never_rewritten(tmp_path):
    """Explicit declaration: the commit that opens 1.0.0 IS the release.

    The hook leaves the author's declaration alone (a declared number is not
    positional); the pipeline verifies the `[line 1.0.0]` marker, tags the
    commit and rewrites nothing — no bump commit, no second tag.
    """
    work, _ = _bootstrap(tmp_path, positions=3)
    before = _author_commit(work, "1.0.0 release \\\n[line 1.0.0]", version="1.0.0")
    authored = _git(work, "rev-parse", "HEAD")
    _Job(work, before).run().ok()

    assert _git(work, "rev-parse", "HEAD") == authored        # nothing was committed
    assert _version_at(work, "HEAD") == "1.0.0"
    assert _tag_target(work, "v1.0.0") == authored


def test_declared_line_takes_its_next_patch(tmp_path):
    """After the declaration, the patch counts commits since v1.0.0.

    The second commit on the line is written by the SAME hook: the tree still
    says 1.0.0, but the line exists now, so the commit owns 1.0.1 — and the
    pipeline checks it against the opening tag, not against the counter.
    """
    work, _ = _bootstrap(tmp_path, positions=3)
    _Job(work, _author_commit(work, "1.0.0 release \\\n[line 1.0.0]",
                              version="1.0.0")).run().ok()    # v1.0.0 is published
    before = _author_commit(work, "work on the new line")
    authored = _git(work, "rev-parse", "HEAD")
    _Job(work, before).run().ok()

    assert _version_at(work, "HEAD") == "1.0.1"              # the hook again
    assert _tag_target(work, "v1.0.1") == authored


def test_undeclared_major_line_is_refused_loudly(tmp_path):
    """A number nobody declared is a claim about commits that do not exist.

    The hook deliberately does not police declarations (it cannot read intent
    from a tree) — the marker check is the pipeline's job, and it is red.
    """
    work, _ = _bootstrap(tmp_path, positions=3)
    before = _author_commit(work, "2.0.0 out of nowhere", version="2.0.0")
    head_before = _git(work, "rev-parse", "HEAD")
    message = _Job(work, before).run().failed()

    assert "::error::" in message and "never declared" in message, message
    assert _git(work, "rev-parse", "HEAD") == head_before     # nothing to undo anyway
    assert _tag_target(work, "v2.0.0") == ""                  # and no tag


def test_a_stale_tree_is_refused_naming_the_number_it_owns(tmp_path):
    """`--no-verify` is not a loophole: the pipeline refuses what the hook skipped.

    The tree says 0.1.2 while the commit's position owns 0.1.3. The run does
    not repair (a fix-up is a rebase or an amend by the author, never another
    commit from a bot) — it goes red naming the exact number it should carry.
    """
    work, _ = _bootstrap(tmp_path, positions=3)
    before = _author_commit(work, "hand-bumped past the hook", version="0.1.2",
                            no_verify=True)
    message = _Job(work, before).run().failed()

    assert "carries 0.1.2" in message and "position owns 0.1.3" in message, message
    assert "core.hooksPath" in message, "the refusal must say how to fix the clone"
    assert _tag_target(work, "v0.1.3") == ""


def test_illegal_auto_number_is_refused_and_never_tagged(tmp_path):
    """`0.1.150` is not a number the rule can produce: refused, with the fix.

    An impossible number is a claim about commits that do not exist; the run
    names the position's real number instead of publishing the lie.
    """
    work, _ = _bootstrap(tmp_path, positions=3)
    before = _author_commit(work, "hand-written future number", version="0.1.150",
                            no_verify=True)
    message = _Job(work, before).run().failed()

    assert "carries 0.1.150" in message and "position owns 0.1.3" in message, message
    assert _tag_target(work, "v0.1.150") == ""
    assert _tag_target(work, "v0.2.50") == ""


def test_rerun_after_success_is_idempotent(tmp_path):
    """Existing tags are never re-pointed: a re-run warns and publishes nothing.

    History is append-only here — re-tagging would be a no-op at best, a
    re-point at worst, so the step must leave the published chain alone.
    """
    work, _ = _bootstrap(tmp_path, positions=3)
    before = _author_commit(work, "a normal change")
    _Job(work, before).run().ok()
    tagged = _tag_target(work, "v0.1.3")

    rerun = _Job(work, before).run()                          # the same push again
    rerun.ok()
    assert "already exists" in rerun.log[STEP].stdout, rerun.log[STEP].stdout
    assert _tag_target(work, "v0.1.3") == tagged              # unchanged


def test_step_body_is_extractable_and_template_free():
    """A renamed or re-indented step must fail here, not in production."""
    body = _step_body()
    assert body.strip(), "the step body is empty"
    assert not body.lstrip().startswith(("if:", "run:", "id:")), body[:80]
    assert "$ {{" not in _subst(body, "0" * 40)
    assert re.search(r"\S", body)

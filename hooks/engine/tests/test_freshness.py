"""Tests for hooks/engine/modules/freshness.py — the freshness plane.

The three-valued verdict is the contract: CURRENT / STALE / UNKNOWN, derived
from a witness rather than stored, scope-sensitive rather than
commit-sensitive, and never an exception on a read path.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import time

import pytest

_HOOKS = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HOOKS))

from modules import freshness as fr  # noqa: E402

_GATE_SCRIPTS = (pathlib.Path(__file__).resolve().parents[3]
                 / "skills" / "bmad-research-experiment" / "scripts")


@pytest.fixture(autouse=True)
def _clear_cache():
    fr.reset_cache()
    yield
    fr.reset_cache()


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=30, stdin=subprocess.DEVNULL)


def _commit(root, message):
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=t@example.invalid", "-c", "user.name=t",
         "commit", "-q", "-m", message)


def _record(root, key, scope, extra=""):
    path = root / "docs" / "experiments" / f"{key}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"## Experiment: {key} — fixture\n"
        f"- **Status:** completed\n"
        f"- **Code Scope:** {scope}\n"
        f"- **Decision:** APPROVED — fixture\n{extra}",
        encoding="utf-8")
    return path


@pytest.fixture()
def repo(tmp_path):
    """A git repo with `src/` + `lib/`, an E-900 record scoped to `src/**`."""
    (tmp_path / "src").mkdir()
    (tmp_path / "lib").mkdir()
    (tmp_path / "src" / "a.py").write_text("# a\n", encoding="utf-8")
    (tmp_path / "lib" / "b.py").write_text("# b\n", encoding="utf-8")
    _record(tmp_path, "E-900", "src/**")
    _git(tmp_path, "init", "-q")
    _commit(tmp_path, "baseline")
    return tmp_path


# --- scope matcher parity with the gate (one source, mechanically pinned) ------
def test_scope_matcher_parity_with_the_gate():
    """This module carries a copy of the gate's matcher to keep the read path
    gate-import-free; the copy must agree with the original on a corpus that
    covers every branch (`*`, `**`, `**/`, `?`, literal, backslash, case)."""
    if str(_GATE_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_GATE_SCRIPTS))
    import run_experiment as gate

    scopes = ["src/**", "hooks/engine/modules/*.py", "**/*.py", "docs/**/*.md",
              "scripts/**", "a?c.py", "src/*.ts", "core", "lib/**/*.py",
              "src/**/deep/*.txt", "C:\\\\proj\\\\src\\\\**"]
    targets = ["src/a.py", "src/deep/nested/a.py", "lib/b.py", "docs/x/y.md",
               "hooks/engine/modules/guard.py", "hooks/engine/main.py",
               "abc.py", "axc.py", "src/a.ts", "core", "core/a.py",
               "src/deep/deep/f.txt", "SRC/A.PY", "proj/src/x.py"]
    for scope in scopes:
        for target in targets:
            assert fr.scope_matches(scope, target) == gate.scope_matches(scope, target), \
                f"matcher drift on scope={scope!r} target={target!r}"


def test_scope_digest_is_normalized_and_stable():
    assert fr.scope_digest("src/** , lib/**") == fr.scope_digest("lib/**  src/**")
    assert fr.scope_digest("none") == "" and fr.scope_digest("") == ""
    assert fr.scope_digest("src/**") != fr.scope_digest("src/*")


# --- verdicts -----------------------------------------------------------------
def test_classify_current_when_code_state_unchanged(repo):
    verdict, detail = fr.classify(repo, commit=fr.head(repo), scope="src/**")
    assert verdict == fr.CURRENT and "unchanged" in detail["reason"]


def test_classify_stale_only_for_in_scope_change(repo):
    old = fr.head(repo)
    (repo / "lib" / "b.py").write_text("# b v2\n", encoding="utf-8")
    _commit(repo, "unrelated change")
    fr.reset_cache()
    assert fr.classify(repo, commit=old, scope="src/**")[0] == fr.CURRENT
    (repo / "src" / "a.py").write_text("# a v2\n", encoding="utf-8")
    _commit(repo, "scoped change")
    fr.reset_cache()
    verdict, detail = fr.classify(repo, commit=old, scope="src/**")
    assert verdict == fr.STALE and detail["changed"] == ["src/a.py"]


def test_uncommitted_scope_edit_is_stale_for_a_stored_witness(repo):
    """The most common real shape: the agent edits code without committing.

    A stored witness carries the tree digest, so the dirty file is caught; a
    derived witness deliberately does not claim it (the dirty state may predate
    the record), which is the documented asymmetry between the two anchors.
    """
    head = fr.head(repo)
    stored = fr.witness_of(repo, scope="src/**")
    (repo / "src" / "a.py").write_text("# a dirty\n", encoding="utf-8")
    fr.reset_cache()
    verdict, detail = fr.classify(repo, commit=head, scope="src/**",
                                  tree=stored["tree"])
    assert verdict == fr.STALE
    assert "uncommitted" in detail["reason"] and detail["changed"] == ["src/a.py"]
    # Derived anchor on the same tree: current (nothing committed moved).
    assert fr.classify(repo, commit=head, scope="src/**")[0] == fr.CURRENT


def test_unknown_is_a_real_answer_not_a_silent_current(repo):
    assert fr.classify(repo, commit="", scope="src/**")[0] == fr.UNKNOWN
    assert fr.classify(repo, commit="0" * 40, scope="src/**")[0] == fr.UNKNOWN
    out_of_git = pathlib.Path(repo).parent / "not-a-repo"
    out_of_git.mkdir(exist_ok=True)
    (out_of_git / "src").mkdir(exist_ok=True)
    assert fr.classify(str(out_of_git), commit="abc123", scope="src/**")[0] == fr.UNKNOWN


def test_missing_root_never_raises(tmp_path):
    ghost = str(tmp_path / "does-not-exist")
    assert fr.head(ghost) is None
    assert fr.dirty_files(ghost) is None
    assert fr.changed_since(ghost, "deadbeef") is None
    assert fr.classify(ghost, commit="deadbeef", scope="src/**")[0] == fr.UNKNOWN
    assert fr.record_scope(ghost, "E-900") == ""
    assert fr.derived_witness(ghost, "E-900") is None


def test_retired_and_disabled(repo, monkeypatch):
    verdict, detail = fr.classify(repo, commit=fr.head(repo), scope="src/**",
                                  retired=True)
    assert verdict == fr.RETIRED and "no longer applies" in detail["reason"]
    monkeypatch.setattr(fr, "mode", lambda: "off")
    assert fr.classify(repo, commit="", scope="src/**")[0] == fr.CURRENT


# --- claims (records, derived witnesses, cascade) ------------------------------
def test_derived_witness_dates_a_claim_to_its_record_commit(repo):
    record_commit = fr.head(repo)
    (repo / "src" / "a.py").write_text("# a v2\n", encoding="utf-8")
    _commit(repo, "scoped change")
    fr.reset_cache()
    witness = fr.derived_witness(repo, "E-900")
    assert witness and witness["commit"] == record_commit and witness["derived"]
    ann = fr.annotate(repo, "E-900")
    assert ann["verdict"] == fr.STALE and ann["anchor"] == "record"
    assert ann["changed"] == ["src/a.py"]


def test_the_decision_commit_dates_the_claim_not_the_posting(repo):
    """A baton posted AFTER the code moved, citing a decision from before it,
    is stale — dating it to the posting would make every baton infallibly
    fresh at birth and empty the plane."""
    (repo / "src" / "a.py").write_text("# moved first\n", encoding="utf-8")
    _commit(repo, "scoped change")
    fr.reset_cache()
    stored = fr.witness_of(repo, scope="src/**")   # posted against the NEW head
    ann = fr.annotate(repo, "E-900", stored=stored)
    assert ann["anchor"] == "record" and ann["verdict"] == fr.STALE


def test_stored_witness_anchors_a_claim_with_no_record(repo):
    """`story.1-1-x` resolves to no record, so the posting time is all the
    anchor there is — and a claim with neither anchor reads UNKNOWN, never
    CURRENT."""
    stored = fr.witness_of(repo, scope="src/**")
    ann = fr.annotate(repo, "story.1-1-x", stored=stored)
    assert ann["anchor"] == "stored" and ann["verdict"] == fr.CURRENT
    (repo / "src" / "a.py").write_text("# moved\n", encoding="utf-8")
    fr.reset_cache()
    assert fr.annotate(repo, "story.1-1-x", stored=stored)["verdict"] == fr.STALE
    assert fr.annotate(repo, "story.1-1-x")["verdict"] == fr.UNKNOWN


def test_re_measured_by_is_a_cascade_invalidation(repo):
    _record(repo, "E-901", "src/**")
    (repo / "docs" / "experiments" / "E-900.md").write_text(
        (repo / "docs" / "experiments" / "E-900.md").read_text(encoding="utf-8")
        + "- **Re-Measured-By:** E-901 (16.10.2026)\n", encoding="utf-8")
    fr.reset_cache()
    assert fr.superseded_by(repo, "E-900") == "E-901"
    verdict, detail = fr.classify(repo, commit=fr.head(repo), scope="src/**",
                                  run_key="E-900")
    assert verdict == fr.STALE and "superseded by E-901" in detail["reason"]
    # A declaration pointing at a record that does not exist is not a supersede.
    fr.reset_cache()
    assert fr.superseded_by(repo, "E-902") is None


def test_measurement_only_record_has_nothing_to_stale(repo):
    _record(repo, "E-903", "none")
    _commit(repo, "record only")
    (repo / "src" / "a.py").write_text("# moved\n", encoding="utf-8")
    _commit(repo, "scoped change")
    fr.reset_cache()
    verdict, _detail = fr.classify(repo, commit=fr.head(repo) + "^",
                                   scope="", run_key="E-903")
    assert verdict == fr.CURRENT


def test_key_of_extracts_only_real_run_keys():
    assert fr.key_of("E-048: Experiment E-048 APPROVED") == "E-048"
    assert fr.key_of("story.1-1-x: dev complete") == "story.1-1-x"
    assert fr.key_of("a note without a key") == ""
    assert fr.key_of("") == ""
    assert fr.key_of(": leading colon") == ""


# --- surfaces helpers ----------------------------------------------------------
def test_tally_and_label():
    entries = [{"verdict": "STALE"}, {"verdict": "CURRENT"}, {"verdict": "STALE"},
               {"verdict": "UNKNOWN"}]
    assert fr.tally(entries) == {"CURRENT": 1, "STALE": 2, "UNKNOWN": 1,
                                 "RETIRED": 0, "total": 4}
    assert fr.label("STALE", 160000) == "STALE (1.9d)"
    assert fr.label("CURRENT") == "CURRENT"
    assert fr.label("", None) == "CURRENT"


def test_batch_folds_repeated_git_reads(repo):
    """A board with many signals performs ONE diff per commit, not one each."""
    calls = {"n": 0}
    original = fr._git

    def counting(root, *args):
        if list(args[:2]) == ["diff", "--name-only"]:
            calls["n"] += 1
        return original(root, *args)

    fr._git = counting
    try:
        head = fr.head(repo)
        with fr.batch():
            for _ in range(5):
                fr.classify(repo, commit=head + "^", scope="src/**")
    finally:
        fr._git = original
    assert calls["n"] <= 1


def test_no_cache_hides_an_in_process_commit(repo):
    """Regression pin (E-053, first bench run): a wall-clock cache answered
    "code state unchanged" for a claim whose scope had just moved — a false
    CURRENT, the one verdict a freshness plane must never invent. Outside a
    batch nothing is cached, so the commit is visible immediately."""
    stored = fr.witness_of(repo, scope="src/**")
    (repo / "src" / "a.py").write_text("# moved in-process\n", encoding="utf-8")
    _commit(repo, "scoped change")
    verdict, detail = fr.classify(repo, commit=stored["commit"], scope="src/**",
                                  tree=stored["tree"])
    assert verdict == fr.STALE, detail
    assert detail["changed"] == ["src/a.py"]


def test_classification_stays_inside_the_git_budget(repo):
    """Bounded: a 200-file scoped change classifies in well under a second."""
    for i in range(200):
        (repo / "src" / f"f{i}.py").write_text(f"# {i}\n", encoding="utf-8")
    _commit(repo, "wide change")
    fr.reset_cache()
    start = time.monotonic()
    verdict, detail = fr.classify(repo, commit=fr.head(repo) + "^", scope="src/**")
    elapsed = time.monotonic() - start
    assert verdict == fr.STALE and detail["changed"]
    assert elapsed < 1.0, f"classify took {elapsed:.3f}s"


# --- the [freshness] table (E-053 §5.5) ----------------------------------------
def test_scope_digest_off_classifies_on_the_commit_only(repo, monkeypatch):
    """`scope_digest = false` is the documented noisy mode: any movement stales.

    The E-900 claim is `src/**` and the commit below touches `lib/` only — the
    scope-sensitive default answers CURRENT (test_classify_stale_only_for_
    in_scope_change pins the other direction). With the switch off the commit
    alone decides, and the answer becomes STALE with the reason naming the
    switch. Never a silent downgrade to CURRENT.
    """
    anchor = fr.derived_witness(repo, "E-900")
    assert anchor and anchor["commit"]
    (repo / "lib" / "b.py").write_text("# lib moves on\n", encoding="utf-8")
    _commit(repo, "unrelated commit")
    fr.reset_cache()
    assert fr.classify(repo, commit=anchor["commit"], scope="src/**")[0] == fr.CURRENT
    monkeypatch.setattr(fr, "scope_digest_enabled", lambda: False)
    verdict, detail = fr.classify(repo, commit=anchor["commit"], scope="src/**")
    assert verdict == fr.STALE
    assert "scope_digest is off" in detail["reason"]


def test_max_digest_files_turns_a_flood_into_unknown(repo, monkeypatch):
    """Past the bound the honest answer is UNKNOWN, not a guess."""
    for i in range(3):
        (repo / "src" / f"extra{i}.py").write_text(f"# {i}\n", encoding="utf-8")
    _commit(repo, "three more files")
    fr.reset_cache()
    monkeypatch.setattr(fr, "max_digest_files", lambda: 2)
    verdict, detail = fr.classify(repo, commit=fr.derived_witness(repo, "E-900")["commit"],
                                 scope="src/**")
    assert verdict == fr.UNKNOWN
    assert "max_digest_files" in detail["reason"]


def test_blocks_is_the_one_place_that_answers_whether_a_verdict_blocks(monkeypatch):
    """guard/quality/deploy all ask `fr.blocks` — UNKNOWN joins STALE only when
    `[freshness] unknown_policy = deny`."""
    monkeypatch.setattr(fr, "mode", lambda: "deny")
    monkeypatch.setattr(fr, "unknown_policy", lambda: "warn")
    assert fr.blocks(fr.STALE) is True
    assert fr.blocks(fr.UNKNOWN) is False
    monkeypatch.setattr(fr, "unknown_policy", lambda: "deny")
    assert fr.blocks(fr.UNKNOWN) is True
    monkeypatch.setattr(fr, "mode", lambda: "warn")
    assert fr.blocks(fr.STALE) is False and fr.blocks(fr.UNKNOWN) is False


# --- TD-016: the dead declarations are gone -------------------------------------
def test_verify_cache_has_no_dead_invalidator():
    """TD-016: the engine is cold-started per call, so the verify cache is
    call-scoped and invalidates itself through its (mtime_ns, size) key. The
    version-counter invalidator that nothing ever called is deleted — the
    check that used to be a docstring claim is now an absence."""
    import pathlib
    from modules import guard as guard_mod
    source = pathlib.Path(guard_mod.__file__).read_text(encoding="utf-8")
    assert "def invalidate_verify_cache" not in source
    assert "_VERIFY_CACHE_VERSION" not in source
    assert not hasattr(guard_mod, "invalidate_verify_cache")

"""Closed-set contract bench for the release step's classification paths (E-126, E-127).

    python3 scripts/bench/bench_release_step_contract.py --samples docs/experiments/E-127/raw/samples.jsonl
    python3 scripts/bench/bench_release_step_contract.py --broken   # rule-7 control

The step in `.github/workflows/release-numbers.yml` derives every number with
`python scripts/versioning.py`. On a python3-only host that command never
starts, `--is-auto` fails for the wrong reason, and the else branch reads the
tree as a DECLARED line — so a valid block-boundary release (position 100,
0.2.0) is refused as "never declared". A missing number line is worse: `set -e`
+ `pipefail` killed the job with an empty log. And because `--is-auto` answers
with one nonzero code for both "valid but declared" and "not a version at
all", `banana.0` was tagged `vbanana.0`. `--broken` runs the pre-fix rules so
rule 7 can see the bench tell them apart.

Each case drives the REAL step body extracted from the workflow (the same
harness the release tests use), not a reimplementation of it.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "tests"))

import test_release_pipeline as rp  # noqa: E402

BROKEN = False

# The pre-fix shape lacks the shape guard too. `if false; then` disables it
# without depending on the body's indentation (the YAML dedent decides that),
# so --broken is the code as it shipped, not a half-fixed mix.
_PREFIX_BODY = rp._step_body().replace(
    'if ! "$PY" scripts/versioning.py --opening "$TREE_VERSION" >/dev/null 2>&1; then',
    "if false; then"
).replace('"$PY" scripts/versioning.py', "python scripts/versioning.py")
# A no-op replace leaves the fix inside the control, so --broken would pass
# the very case it exists to fail. Fail here instead of grading a mixed body.
assert "versioning.py --opening" not in _PREFIX_BODY \
    and "python scripts/versioning.py" in _PREFIX_BODY, \
    "the control body still carries the shipped fix — the bench cannot discriminate"


def _body() -> str:
    """The step body under test: pre-fix when BROKEN, shipped otherwise."""
    return _PREFIX_BODY if BROKEN else rp._step_body()


def _run(work: pathlib.Path, before: str):
    """Run the step body exactly like Actions does."""
    import subprocess
    return subprocess.run([rp._bash(), "-eo", "pipefail", "-c",
                           rp._subst(_body(), before)],
                          cwd=work, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=rp._ENV,
                          stdin=subprocess.DEVNULL)


def _resolves_its_interpreter() -> bool:
    body = _body()
    return ('"$PY" scripts/versioning.py' in body
            and "command -v python3" in body
            and "no python interpreter" in body
            and "python scripts/versioning.py" not in body)


def _block_boundary_release_is_tagged() -> bool:
    """Position 100 owns 0.2.0 and must be tagged, not refused as undeclared."""
    work, _ = rp._bootstrap(pathlib.Path(tempfile.mkdtemp()), positions=100)
    before = rp._author_commit(work, "commit 100")
    authored = rp._git(work, "rev-parse", "HEAD")
    proc = _run(work, before)
    if proc.returncode != 0:
        return False
    return rp._tag_target(work, "v0.2.0") == authored


def _missing_version_is_refused_loudly() -> bool:
    """No readable version line: a named refusal, never an empty red job."""
    work, _ = rp._bootstrap(pathlib.Path(tempfile.mkdtemp()), positions=2)
    (work / ".plugin" / "plugin.json").write_text(
        json.dumps({"name": "metodoloji"}), encoding="utf-8")
    before = rp._git(work, "rev-parse", "HEAD")
    rp._git(work, "add", "-A")
    rp._git(work, "commit", "-qm", "drop the version line", "--no-verify")
    rp._git(work, "push", "-q", "origin", "main")
    proc = _run(work, before)
    if proc.returncode == 0 or rp._tag_target(work, "v0.1.2"):
        return False
    if BROKEN:
        # The pre-fix shape: `set -e` killed the step inside the substitution,
        # so the job went red with nothing to read.
        return not (proc.stdout + proc.stderr).strip()
    return "::error::" in proc.stdout and "no readable version" in proc.stdout


def _unparseable_version_is_refused() -> bool:
    """`banana.0` is not a declaration: refuse it, never tag vbanana.0."""
    work, _ = rp._bootstrap(pathlib.Path(tempfile.mkdtemp()), positions=3)
    (work / ".plugin" / "plugin.json").write_text(
        json.dumps({"name": "metodoloji", "version": "banana.0"}), encoding="utf-8")
    before = rp._git(work, "rev-parse", "HEAD")
    rp._git(work, "add", "-A")
    rp._git(work, "commit", "-qm", "declare the banana line [line banana.0]",
            "--no-verify")
    rp._git(work, "push", "-q", "origin", "main")
    proc = _run(work, before)
    if proc.returncode == 0 or rp._tag_target(work, "vbanana.0"):
        return False
    return "::error::" in proc.stdout and "not a plain X.Y.Z" in proc.stdout


def _normal_release_still_passes() -> bool:
    """The guard must not break the ordinary case it was added for."""
    work, _ = rp._bootstrap(pathlib.Path(tempfile.mkdtemp()), positions=3)
    before = rp._author_commit(work, "a normal change")
    proc = _run(work, before)
    return proc.returncode == 0 and rp._tag_target(work, "v0.1.3") != ""


CASES = (
    ("resolves_its_interpreter", _resolves_its_interpreter,
     "the step resolves python3 → python → py instead of assuming `python`"),
    ("block_boundary_release_is_tagged", _block_boundary_release_is_tagged,
     "position 100 is tagged v0.2.0, not refused as an undeclared line"),
    ("missing_version_is_refused_loudly", _missing_version_is_refused_loudly,
     "a tree with no version line fails with a named ::error::"),
    ("unparseable_version_is_refused", _unparseable_version_is_refused,
     "a version that is not X.Y.Z fails with a named ::error:: and no tag"),
    ("normal_release_still_passes", _normal_release_still_passes,
     "an ordinary positional release still verifies and tags"),
)


def main() -> int:
    global BROKEN
    ap = argparse.ArgumentParser()
    ap.add_argument("--broken", action="store_true",
                    help="run the pre-fix rules (the rule-7 control)")
    ap.add_argument("--samples", default=None,
                    help="JSONL path for per-case evidence")
    args = ap.parse_args()
    BROKEN = args.broken

    results = []
    for name, probe, detail in CASES:
        try:
            ok = bool(probe())
        except Exception as exc:  # a raising probe is a failed case, not a crash
            ok, detail = False, f"{detail} — raised {exc!r}"
        results.append({"check": name, "ok": ok, "detail": detail})

    if args.samples:
        path = pathlib.Path(args.samples)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for r in results:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    x = sum(1 for r in results if r["ok"])
    n = len(results)
    for r in results:
        print(f"[{'OK' if r['ok'] else 'MISS'}] {r['check']}: {r['detail']}")
    print(f"release_step_contract_accuracy={x / n:.2f} ({x}/{n})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

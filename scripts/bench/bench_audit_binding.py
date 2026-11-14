#!/usr/bin/env python3
"""Every shipped audit is bound to a surface CI runs (E-154).

    python3 scripts/bench/bench_audit_binding.py --samples docs/experiments/E-154/raw/samples.jsonl
    python3 scripts/bench/bench_audit_binding.py --broken   # rule-7 control

E-152 retired `docs/runtime-security-coverage.md` and named the class in its
Known Gaps; one member survived. `scripts/check-runtime-coverage.py` shipped,
had no test, was invoked by nothing (its only mention anywhere is a historical
experiment record), and its first read is the retired doc — running it raises
FileNotFoundError. That is TD-016's declared-but-unbound mechanism, one file
large: an audit nobody runs cannot catch the drift it exists for.

Deleting the file is not the hardening. The audit family must be reachable
from the surfaces CI actually runs, and the rule itself must run in CI, or the
next orphan is invisible in exactly the same way. `--broken` evaluates the
pre-change tree (`PREFIX_REV`), which still ships the orphan, so the control
fails for the reasons this change names.

Deterministic, offline, stdlib only.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
CI_REL = ".github/workflows/quality.yml"
RUNNER_REL = "scripts/check-plugin.sh"
BENCH_REL = "scripts/bench/bench_audit_binding.py"
RETIRED_REL = "scripts/check-runtime-coverage.py"
RETIRED_SUBJECT = "docs/runtime-security-coverage.md"
PREFIX_REV = "ac0c05b"  # E-152: the last commit before the E-154 fix
BROKEN = False

_AUDIT_NAME_RE = re.compile(r"check-[a-z0-9]+(?:-[a-z0-9]+)*\.(?:sh|py)")

_PREFIX_CACHE: pathlib.Path | None = None


def _tree() -> pathlib.Path:
    global _PREFIX_CACHE
    if not BROKEN:
        return ROOT
    if _PREFIX_CACHE is not None:
        return _PREFIX_CACHE
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="audit-binding-"))
    out = subprocess.run(["git", "-C", str(ROOT), "archive", PREFIX_REV],
                         capture_output=True, stdin=subprocess.DEVNULL)
    if out.returncode != 0:
        raise RuntimeError(f"cannot archive {PREFIX_REV}: {out.stderr[:200]!r}")
    subprocess.run(["tar", "-x", "-C", str(tmp)], input=out.stdout, check=True)
    _PREFIX_CACHE = tmp
    return tmp


def _text(root: pathlib.Path, rel: str) -> str:
    try:
        return (root / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _active(text: str) -> str:
    """Drop comment lines: a mention in a comment must not bind an audit."""
    return "\n".join(line for line in text.splitlines()
                     if not line.lstrip().startswith("#"))


def _family(root: pathlib.Path) -> list[str]:
    """The audit family DERIVED from disk, never hand-listed."""
    scripts = root / "scripts"
    if not scripts.is_dir():
        return []
    return sorted(path.name for path in scripts.glob("check-*")
                  if path.suffix in (".sh", ".py") and path.is_file())


def _runner_text(root: pathlib.Path) -> str:
    """What CI runs, plus what the CI-run runner itself invokes."""
    return _active(_text(root, CI_REL)) + "\n" + _active(_text(root, RUNNER_REL))


# --- the probes ---------------------------------------------------------------

def _case_family_is_derived(root: pathlib.Path) -> tuple[bool, str]:
    family = _family(root)
    if not family:
        return False, "no shipped scripts/check-* audit found"
    missing = [name for name in family if not (root / "scripts" / name).is_file()]
    if missing:
        return False, f"family names a file that is not there: {missing}"
    return True, f"derived {len(family)} audits from scripts/check-*"


def _case_every_audit_is_reachable(root: pathlib.Path) -> tuple[bool, str]:
    family = _family(root)
    named = set(_AUDIT_NAME_RE.findall(_runner_text(root)))
    orphans = [name for name in family if name not in named]
    if orphans:
        return False, (f"{orphans} ship but no surface CI runs names them — "
                       "an audit nobody runs cannot catch its drift")
    return True, "every shipped audit is named by the quality workflow or check-plugin.sh"


def _case_every_named_audit_ships(root: pathlib.Path) -> tuple[bool, str]:
    named = set(_AUDIT_NAME_RE.findall(_runner_text(root)))
    dead = sorted(name for name in named if not (root / "scripts" / name).is_file())
    if dead:
        return False, f"a runner names audits that do not ship: {dead}"
    return True, f"every one of the {len(named)} named audits exists on disk"


def _case_the_retired_auditor_is_gone(root: pathlib.Path) -> tuple[bool, str]:
    script = (root / RETIRED_REL).is_file()
    subject = (root / RETIRED_SUBJECT).is_file()
    if script and not subject:
        return False, (f"{RETIRED_REL} ships while its subject {RETIRED_SUBJECT} "
                       "is retired — a half-dead pair that crashes when run")
    if script:
        return False, f"{RETIRED_REL} still ships"
    return True, "the retired auditor and its subject are both gone"


def _case_no_audit_names_the_retired_subject(root: pathlib.Path) -> tuple[bool, str]:
    offenders = []
    for name in _family(root):
        if RETIRED_SUBJECT in _text(root, f"scripts/{name}"):
            offenders.append(name)
    if offenders:
        return False, f"{offenders} still name the retired subject {RETIRED_SUBJECT}"
    return True, "no shipped audit names the retired subject"


def _case_the_runner_runs_in_ci(root: pathlib.Path) -> tuple[bool, str]:
    if RUNNER_REL not in _active(_text(root, CI_REL)):
        return False, (f"{CI_REL} does not run {RUNNER_REL} — runner reachability "
                       "is not transitive without it")
    return True, f"{RUNNER_REL} runs in the quality workflow"


def _case_this_bench_runs_in_ci(root: pathlib.Path) -> tuple[bool, str]:
    if BENCH_REL not in _active(_text(root, CI_REL)):
        return False, (f"{CI_REL} does not run {BENCH_REL} — a binding rule "
                       "nobody runs is the same defect one level up")
    return True, f"{BENCH_REL} runs in the quality workflow"


PROBES = (
    ("the audit family is derived from disk", _case_family_is_derived),
    ("every shipped audit is reachable from a CI-run surface",
     _case_every_audit_is_reachable),
    ("every audit a runner names ships", _case_every_named_audit_ships),
    ("the retired auditor and its subject are gone",
     _case_the_retired_auditor_is_gone),
    ("no shipped audit names the retired subject",
     _case_no_audit_names_the_retired_subject),
    ("the runner itself runs in CI", _case_the_runner_runs_in_ci),
    ("this bench runs in CI", _case_this_bench_runs_in_ci),
)


def run(root: pathlib.Path) -> list[dict]:
    results = []
    for name, probe in PROBES:
        try:
            ok, detail = probe(root)
        except Exception as exc:  # a crashed probe is a failed check, never a pass
            ok, detail = False, f"probe crashed: {type(exc).__name__}: {exc}"
        results.append({"check": name, "ok": bool(ok), "detail": detail})
    return results


def main() -> int:
    global BROKEN
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--broken", action="store_true", help="rule-7 control arm")
    parser.add_argument("--samples", help="write per-probe JSONL samples here")
    args = parser.parse_args()
    BROKEN = args.broken

    results = run(_tree())
    for result in results:
        print(f"  {'ok  ' if result['ok'] else 'FAIL'}  {result['check']}: {result['detail']}")
    passed = sum(1 for result in results if result["ok"])
    if args.samples:
        path = pathlib.Path(args.samples)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(result, ensure_ascii=False) + "\n"
                                for result in results), encoding="utf-8")
    print(f"audit_binding_accuracy={passed / len(results):.2f} ({passed}/{len(results)})")
    if _PREFIX_CACHE is not None:
        shutil.rmtree(_PREFIX_CACHE, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

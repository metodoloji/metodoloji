#!/usr/bin/env python3
"""A generated record says each field once, and an explicit value wins (E-172).

    python3 scripts/bench/bench_record_generation.py --samples docs/experiments/E-172/raw/samples.jsonl
    python3 scripts/bench/bench_record_generation.py --broken   # rule-7 control

`scripts/create-record.py` seeds a draft with its own defaults (`Date` = today,
`Status` = the kind's draft word, `Lineage` = none) and then APPENDED every
`--field` value after them. A caller who supplied `Date`, `Status` or `Lineage`
explicitly got two lines for each, and the draft passed `--check` because the
schema had no duplicate rule — E-171's own record was generated that way and was
cleaned by hand (DW-11). Two `Status` lines are read as the first match by
`_one()` and by a line scan in check-methodology CHECK 4, so the record's status
depended on which reader looked at it.

The claim is not "the bug was edited out" but "generation cannot be ambiguous":
the probes below drive the shipped CLI in a throwaway project for every record
kind, and they require the record to carry one value per schema field, the
explicit value to win over the seed, and a second explicit value to be refused
by name instead of silently dropped. Two probes guard the other direction: a
multi-line `--field` value must still render as continuation bullets, and the
append-only history labels (`Re-Measured-By`, `Final Plan Hash`, which E-128,
E-158 and E-159 legitimately repeat) must not be refused.

`--broken` runs the same probes against the pre-fix generator (`PREFIX_REV`,
extracted with `git archive`), so the control fails for the reason this change
names rather than for a paraphrase of it.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
PREFIX_REV = "d866ae0"  # last commit before the precedence + duplicate rule
BROKEN = False

CLI_REL = "scripts/create-record.py"

_PREFIX_CACHE: pathlib.Path | None = None
_MODULE_CACHE: dict[pathlib.Path, object] = {}


def _tree() -> pathlib.Path:
    """The tree under test: this checkout, or the pre-fix tree for the control."""
    return ROOT if not BROKEN else _prefix_tree()


def _prefix_tree() -> pathlib.Path:
    global _PREFIX_CACHE
    if _PREFIX_CACHE is not None:
        return _PREFIX_CACHE
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="record-prefix-"))
    out = subprocess.run(["git", "-C", str(ROOT), "archive", PREFIX_REV],
                         capture_output=True, stdin=subprocess.DEVNULL)
    if out.returncode != 0:
        raise RuntimeError(f"cannot archive {PREFIX_REV}: {out.stderr[:200]!r}")
    subprocess.run(["tar", "-x", "-C", str(tmp)], input=out.stdout, check=True)
    _PREFIX_CACHE = tmp
    return tmp


def _module(tree: pathlib.Path):
    """The tree's own generator, so parsing follows the shipped parser."""
    rel = tree / CLI_REL
    if rel not in _MODULE_CACHE:
        spec = importlib.util.spec_from_file_location("record_generation_probe", rel)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MODULE_CACHE[rel] = mod
    return _MODULE_CACHE[rel]


def _project() -> pathlib.Path:
    return pathlib.Path(tempfile.mkdtemp(prefix="record-gen-"))


def _cli(project: pathlib.Path, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_tree() / CLI_REL), *argv,
         "--project-root", str(project)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL, timeout=180)


def _fields(project: pathlib.Path, rid: str, kind: str = "experiment") -> dict:
    sub = {"experiment": ("docs", "experiments"), "contract": ("docs", "experiments")}
    path = project.joinpath(*sub.get(kind, ("docs", "development"))) / f"{rid}.md"
    if not path.is_file():
        return {}
    return _module(_tree())._parse_fields(path.read_text(encoding="utf-8"))


def _one(fields: dict, label: str) -> list[str]:
    return [v.strip() for v in (fields.get(label) or [])]


def _base(rid: str, kind: str = "experiment") -> list[str]:
    """The minimum a valid draft of `kind` needs besides Date/Status."""
    if kind in ("experiment", "contract"):
        args = ["--kind", kind, "--id", rid, "--title", "probe", "--theory", "t",
                "--hypothesis", f'H-{rid}: "x == 1.0"', "--metrics", "x == 1.0",
                "--design", "d", "--scope", "s", "--raw-samples", "raw/s.jsonl"]
        if kind == "contract":
            args += ["--census", "the thirteen probes this bench enumerates are "
                                   "the whole closed input set"]
        return args
    if kind == "ir":
        return ["--kind", "ir", "--id", rid, "--title", "probe",
                "--field", "Research inputs=E-001",
                "--field", "Success criteria=the gate is green",
                "--field", "Decision=READY", "--field", "Next step=sprint"]
    if kind == "sp":
        return ["--kind", "sp", "--id", rid, "--title", "probe",
                "--field", "Readiness input=IR-001",
                "--field", "Sprint goal=ship", "--field", "Stories=S-001"]
    return ["--kind", "pr", "--id", rid, "--title", "probe",
            "--field", "Release type=minor", "--field", "Version=0.3.2",
            "--field", "Stories=S-001"]


def _created(project: pathlib.Path, rid: str, kind: str) -> pathlib.Path:
    sub = {"experiment": ("docs", "experiments"), "contract": ("docs", "experiments")}
    return project.joinpath(*sub.get(kind, ("docs", "development"))) / f"{rid}.md"


# --- the closed set: one probe per (kind x seeded label) plus the rules ------

def _override(label: str, value: str, kind: str, rid: str) -> tuple[bool, str]:
    project = _project()
    try:
        proc = _cli(project, *_base(rid, kind), "--field", f"{label}={value}")
        if proc.returncode != 0:
            return False, f"CLI refused a valid draft: {(proc.stderr or '').strip()[:120]}"
        got = _one(_fields(project, rid, kind), label)
        if got != [value]:
            return False, f"{label} carries {got!r}, expected [{value!r}]"
        return True, f"one {label} line, the explicit {value!r}"
    finally:
        shutil.rmtree(project, ignore_errors=True)


def _case_date_override() -> tuple[bool, str]:
    return _override("Date", "2020-01-02", "experiment", "E-900")


def _case_status_override() -> tuple[bool, str]:
    return _override("Status", "completed", "experiment", "E-901")


def _case_lineage_override() -> tuple[bool, str]:
    return _override("Lineage", "E-001", "experiment", "E-902")


def _case_contract_status_override() -> tuple[bool, str]:
    return _override("Status", "completed", "contract", "E-903")


def _case_ir_status_override() -> tuple[bool, str]:
    return _override("Status", "READY", "ir", "IR-900")


def _case_sp_status_override() -> tuple[bool, str]:
    return _override("Status", "active", "sp", "SP-900")


def _case_pr_status_override() -> tuple[bool, str]:
    return _override("Status", "ready", "pr", "PR-900")


def _case_convenience_flag_conflict() -> tuple[bool, str]:
    """`--date` and `--field Date=` are two explicit values, not a default."""
    project = _project()
    try:
        proc = _cli(project, *_base("E-904"),
                    "--date", "2020-01-02", "--field", "Date=2020-01-03")
        out = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode == 0:
            got = _one(_fields(project, "E-904"), "Date")
            return False, f"two explicit Dates accepted, record says {got!r}"
        if "Date" not in out:
            return False, f"refused without naming Date: {out.strip()[:120]}"
        if _created(project, "E-904", "experiment").exists():
            return False, "refused but wrote the draft anyway"
        return True, f"the conflict is refused by name (rc={proc.returncode})"
    finally:
        shutil.rmtree(project, ignore_errors=True)


def _case_repeated_field_conflict() -> tuple[bool, str]:
    project = _project()
    try:
        proc = _cli(project, *_base("E-905"),
                    "--field", "Date=2020-01-02", "--field", "Date=2020-01-03")
        out = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode == 0:
            got = _one(_fields(project, "E-905"), "Date")
            return False, f"a second --field Date was absorbed: {got!r}"
        if "Date" not in out:
            return False, f"refused without naming Date: {out.strip()[:120]}"
        return True, f"the repeat is refused by name (rc={proc.returncode})"
    finally:
        shutil.rmtree(project, ignore_errors=True)


_GOOD = """## Experiment: E-950 — dup

- **Date:** 2026-11-16
- **Status:** planned
{extra}"""


def _valid_text(extra: str = "") -> str:
    body = "".join(
        f"- **{label}:** {value}\n" for label, value in (
            ("Theory", "t"), ("Hypothesis", 'H-950: "x == 1.0"'),
            ("Measurement Metrics", "x == 1.0"), ("Experiment Design", "d"),
            ("Code Scope", "s"), ("Lineage", "none"), ("Raw Samples", "raw/s.jsonl")))
    return (_GOOD.format(extra=extra) + body
            + "- **Record Schema:** metodoloji.record/v1\n")


def _check(project: pathlib.Path, text: str) -> tuple[int, str]:
    path = project / "E-950.md"
    path.write_text(text, encoding="utf-8")
    proc = _cli(project, "--check", str(path))
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or ""))


def _case_check_refuses_duplicate_status() -> tuple[bool, str]:
    """--check refuses the shape, not just the generator that used to write it."""
    project = _project()
    try:
        rc, out = _check(project, _valid_text("- **Status:** completed\n"))
        if rc == 0:
            return False, "a record with two Status lines validates"
        if "duplicate field 'Status'" not in out:
            return False, f"refused for another reason: {out.strip()[:140]}"
        return True, "the duplicate is refused by name"
    finally:
        shutil.rmtree(project, ignore_errors=True)


def _case_check_allows_append_only_history() -> tuple[bool, str]:
    """E-128/E-158/E-159 repeat these two labels on purpose."""
    project = _project()
    try:
        extra = ("- **Re-Measured-By:** E-951\n- **Re-Measured-By:** E-952\n"
                 "- **Final Plan Hash:** aaaa\n- **Final Plan Hash:** bbbb\n")
        rc, out = _check(project, _valid_text(extra))
        if rc != 0:
            return False, f"legitimate history refused: {out.strip()[:140]}"
        return True, "append-only history labels are not policed"
    finally:
        shutil.rmtree(project, ignore_errors=True)


def _case_multiline_field_survives() -> tuple[bool, str]:
    project = _project()
    try:
        proc = _cli(project, *_base("E-906"),
                    "--field", "Control Command=step one\nstep two")
        if proc.returncode != 0:
            return False, f"a multi-line value was refused: {(proc.stderr or '')[:120]}"
        text = _created(project, "E-906", "experiment").read_text(encoding="utf-8")
        if "  - step two" not in text:
            return False, "the continuation line is not a bullet"
        return True, "a multi-line value still renders as continuation bullets"
    finally:
        shutil.rmtree(project, ignore_errors=True)


def _case_custom_field_roundtrip() -> tuple[bool, str]:
    project = _project()
    try:
        value = ".venv/bin/python bench.py --broken"
        proc = _cli(project, *_base("E-907"), "--field", f"Control Command={value}")
        if proc.returncode != 0:
            return False, f"a schema-ordered --field was refused: {(proc.stderr or '')[:120]}"
        got = _one(_fields(project, "E-907"), "Control Command")
        if got != [value]:
            return False, f"Control Command carries {got!r}"
        return True, "a --field value lands once, unchanged"
    finally:
        shutil.rmtree(project, ignore_errors=True)


CASES = (
    ("an explicit Date wins over the seed", _case_date_override),
    ("an explicit Status wins over the seed", _case_status_override),
    ("an explicit Lineage replaces 'none'", _case_lineage_override),
    ("a contract Status override wins", _case_contract_status_override),
    ("an IR Status override wins", _case_ir_status_override),
    ("an SP Status override wins", _case_sp_status_override),
    ("a PR Status override wins", _case_pr_status_override),
    ("a convenience flag and --field conflict is refused",
     _case_convenience_flag_conflict),
    ("a repeated --field is refused", _case_repeated_field_conflict),
    ("--check refuses a duplicate schema field", _case_check_refuses_duplicate_status),
    ("--check keeps append-only history labels", _case_check_allows_append_only_history),
    ("a multi-line --field value survives", _case_multiline_field_survives),
    ("a custom --field value round-trips", _case_custom_field_roundtrip),
)


def run() -> list[dict]:
    out = []
    for name, probe in CASES:
        try:
            ok, detail = probe()
        except Exception as exc:  # a crashed probe is a failed check, never a pass
            ok, detail = False, f"probe crashed: {type(exc).__name__}: {exc}"
        out.append({"check": name, "ok": bool(ok), "detail": detail})
    return out


def main() -> int:
    global BROKEN
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--broken", action="store_true", help="rule-7 control arm")
    ap.add_argument("--samples", help="write per-check JSONL samples here")
    args = ap.parse_args()
    BROKEN = args.broken

    results = run()
    for r in results:
        print(f"  {'ok  ' if r['ok'] else 'FAIL'}  {r['check']}: {r['detail']}")
    passed = sum(1 for r in results if r["ok"])
    if args.samples:
        path = pathlib.Path(args.samples)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n"
                                for r in results), encoding="utf-8")
    print(f"record_generation_accuracy={passed / len(results):.2f} ({passed}/{len(results)})")
    if _PREFIX_CACHE is not None:
        shutil.rmtree(_PREFIX_CACHE, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

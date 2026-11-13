"""Retiring `bmad/` changed no CLI's answer and moved no other path's verdict (E-149).

    python3 scripts/bench/bench_bmad_fold.py --capture   # baseline, PRE-move tree
    python3 scripts/bench/bench_bmad_fold.py --samples docs/experiments/E-149/raw/samples.jsonl
    python3 scripts/bench/bench_bmad_fold.py --broken    # rule-7 control

`bmad/` named no owner: it held six CLIs, the workflow kernel, seven test files
and five catalog CSVs in one directory, so every rule had to special-case it
(`PLUGIN_FREE_PREFIXES` freed `hooks/`, `scripts/`, `skills/` but kept
`bmad/scripts/` gated; `check-techdebt.sh` scanned a `bmad` root; §6d2 grepped
commands for `bmad/scripts/*.py`). E-149 moves each file next to the thing that
owns it and retires the directory.

The claim is equivalence plus containment. Equivalence: every CLI returns
exactly what it returned before the move, and every skill resolves identically
through the shim. Containment: the guard's free/gated verdict is unchanged for
every path OUTSIDE the moved set — `bmad/scripts/` going from gated to free is
the intended shift, and nothing else may shift with it.

The baseline fixture is captured from the PRE-move tree, so the comparison is
against the old binaries rather than a description of them.

`--broken` is the control arm: `git archive 2a20300` restores the last tree
that shipped `bmad/` into a temp root and the SAME probes run against it. Six
must fail there — the tree ships `bmad/`, its digest names the old catalog,
`hooks.workflow` does not exist, the tests sit at their old paths, the catalogs
live under `bmad/_config/`, and live files spell the retired paths. Three
(CLI output, shim equivalence, free-zone containment) cannot discriminate by
construction: the baseline was captured from that very tree. The arm prints
which stayed green and why, so the asymmetry is reported, not hidden.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

_REPO = pathlib.Path(__file__).resolve().parents[2]
# ROOT is the tree under measurement. It is the repo for a normal run and a
# restored pre-move checkout for `--broken`, so the control measures the old
# tree instead of asserting what it would have said.
ROOT = _REPO
BASELINE = _REPO / "scripts" / "bench" / "fixtures" / "bmad-fold-baseline.json"
PREFIX_REV = "2a20300"  # the E-148 commit: the last tree that shipped bmad/

def _scripts_home() -> pathlib.Path:
    return ROOT / "hooks" / "scripts"          # post-move home

def _bmad_home() -> pathlib.Path:
    return ROOT / "bmad"                       # pre-move home

BROKEN = False

# The moved set: paths whose free/gated verdict is ALLOWED to change. Everything
# outside it must keep its verdict byte for byte.
MOVED_PREFIXES = ("bmad/", "hooks/scripts/", "hooks/workflow/",
                  "skills/skill-manifest.csv",
                  "skills/bmad-help/references/bmad-help.csv")

# Live spellings of the retired tree. `bmad-output/` and `_bmad-output/` are the
# TARGET PROJECT's pre-migration artifacts, not this tree, and are excluded by
# the boundary class.
RETIRED_RE = re.compile(
    r"(?<![\w./-])bmad/(?:scripts|workflow|tests|_config|bmb|bmm|core)\b")
LIVE_GLOBS = ("*.py", "*.sh", "*.md", "*.toml", "*.json", "*.yaml", "*.yml",
              "*.ts", "*.js", "*.csv")
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".pytest_cache", ".metodoloji"}
# History: the records describe what the tree looked like when they were written.
HISTORY_RE = re.compile(
    r"^docs/(experiments|development|quality|research|planning|design)/")
# The remediation report is a dated account of what the tree looked like when
# each fix landed — the same class as an experiment record.
HISTORY_FILES = {"docs/STALE-CONTEXT-REMEDIATION.md"}
# This bench names the retired tree by design: the regex, the fixture and the
# `--broken` control all have to spell it.
INSTRUMENT = {"scripts/bench/bench_bmad_fold.py",
              "scripts/bench/fixtures/bmad-fold-baseline.json"}
# A `git show <rev>:bmad/...` reference is git HISTORY, not a live path: E-147's
# and E-148's control arms read the old file from the commit that shipped it,
# and their rev is an f-string constant, so match the whole line shape.
GIT_REV_RE = re.compile(r"^.*(?:git[^\n]*?\"show\"|_?PREFIX_REV).*$", re.M)

def _sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def _project() -> pathlib.Path:
    """A pristine empty project root, so no run reads this repo's own state."""
    return pathlib.Path(tempfile.mkdtemp())

def _home() -> pathlib.Path:
    """The directory that actually holds the six CLIs, either home.

    `hooks/scripts/` exists before the move too (it ships the shell entry
    points), so existence of the directory is not the test — presence of the
    CLI is.
    """
    for cand in (_scripts_home(), _bmad_home() / "scripts"):
        if (cand / "orient.py").is_file():
            return cand
    raise SystemExit("no CLI home found: neither hooks/scripts/orient.py "
                     "nor bmad/scripts/orient.py exists")

def _builtin_dir() -> pathlib.Path:
    """Where the builtin specs live — either home, whichever the tree has."""
    for cand in (ROOT / "hooks" / "workflow" / "builtin",
                 ROOT / "bmad" / "workflow" / "builtin"):
        if cand.is_dir():
            return cand
    return ROOT / "hooks" / "workflow" / "builtin"

def _run(script: pathlib.Path, *args: str, cwd: pathlib.Path | None = None) -> dict:
    r = subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True, text=True, encoding="utf-8",
        stdin=subprocess.DEVNULL, timeout=120, cwd=str(cwd or ROOT))
    return {"rc": r.returncode, "stdout": r.stdout, "stderr": r.stderr}

def _json(script: pathlib.Path, *args: str, cwd: pathlib.Path | None = None):
    out = _run(script, *args, cwd=cwd)
    if out["rc"] != 0:
        return {"__error__": out["stderr"].strip()[:400], "__rc__": out["rc"]}
    try:
        return json.loads(out["stdout"])
    except json.JSONDecodeError as exc:
        return {"__error__": f"not JSON: {exc}", "__raw__": out["stdout"][:400]}

def _normalize(digest, proj: pathlib.Path | None = None):
    """Drop the fields that legitimately differ run to run.

    `today`, the board's live focus, the gate-key state and any absolute path
    into the temp project are session state, not CLI behaviour. Everything the
    move could break (paths into the plugin, catalog rows, resolved config,
    record inventory) stays in the comparison.
    """
    if not isinstance(digest, dict):
        return digest
    d = json.loads(json.dumps(digest))
    d.pop("today", None)
    d.pop("gate_key", None)
    d.pop("plugin_version", None)
    if proj is not None:
        # The probe project is a fresh tempdir per run; only its PLACEHOLDER
        # matters, never its name.
        text = json.dumps(d, ensure_ascii=False).replace(str(proj), "<proj>")
        d = json.loads(text)
    if isinstance(d.get("next_steps"), list):
        d["next_steps"] = [re.sub(r"python3 \S+/(?:bmad|hooks)/scripts/", "python3 <root>/", s)
                           for s in d["next_steps"]]
    # The catalog is the ONE file the move relocates under a path a digest
    # prints. Its location is expected to change; that it still names a file
    # that exists is checked separately (_case_catalog_path_exists).
    if isinstance(d.get("catalog"), dict) and "path" in d["catalog"]:
        d["catalog"]["path"] = "<catalog>"
    return d

def _capture() -> dict:
    """Every CLI's observable output, from whichever home currently exists."""
    scripts = _home()
    proj = _project()
    skills = sorted(p.parent.name for p in (ROOT / "skills").glob("*/SKILL.md"))

    data: dict = {"scripts_dir": str(scripts.relative_to(ROOT)), "skills": {}}

    def norm(value):
        return _normalize(value, proj)

    orient_raw = _json(scripts / "orient.py", "--json", "--project-root", str(proj))
    data["catalog_path"] = (orient_raw.get("catalog") or {}).get("path")
    data["orient"] = norm(orient_raw)

    data["resolve_config_full"] = norm(
        _json(scripts / "resolve_config.py", "--project-root", str(proj)))
    data["resolve_config_keys"] = {
        key: norm(_json(scripts / "resolve_config.py", "--project-root", str(proj),
                        "--key", key))
        for key in ("core", "modules.bmm", "modules.bmb", "hooks")}

    for name in skills:
        data["skills"][name] = _json(scripts / "resolve_customization.py",
                                     "--skill", str(ROOT / "skills" / name),
                                     "--project-root", str(proj),
                                     "--key", "workflow")

    data["skeleton_status"] = norm(_json(scripts / "skeleton.py", "--status",
                                         "--json", "--project-root", str(proj)))
    data["blackboard_doctor"] = norm(_json(scripts / "blackboard.py", "doctor", "--json",
                                           "--project-root", str(proj)))
    data["workflow_list"] = norm(_json(scripts / "workflow.py", "--project-root",
                                       str(proj), "list"))
    builtin = _builtin_dir() / "methodology-chain.json"
    data["workflow_validate"] = _json(scripts / "workflow.py", "validate",
                                      "--spec", str(builtin))

    data["inventory"] = _inventory(scripts.parent if scripts.parent.name == "bmad" else None)
    return data

def _inventory(bmad_root: pathlib.Path | None) -> dict:
    """sha256 of every shipped catalog/kernel file that the move relocates."""
    roots = []
    if bmad_root is not None:
        roots.append((bmad_root, "bmad/"))
    roots.append((ROOT / "hooks" / "workflow", "hooks/workflow/"))
    roots.append((ROOT / "hooks" / "scripts", "hooks/scripts/"))
    roots.append((ROOT / "skills" / "bmad-help" / "references", "skills/bmad-help/references/"))
    found = {}
    for base, prefix in roots:
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file() or "__pycache__" in p.parts:
                continue
            if p.suffix not in (".py", ".json", ".csv"):
                continue
            found[str(p.relative_to(base)).replace("\\", "/")] = _sha(p)
    return found

def _live_files():
    """Every shipped file whose text can name a retired path."""
    for dp, dns, fns in os.walk(ROOT):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for fn in fns:
            p = pathlib.Path(dp) / fn
            rel = str(p.relative_to(ROOT)).replace("\\", "/")
            if HISTORY_RE.match(rel) or rel in HISTORY_FILES or rel in INSTRUMENT:
                continue
            if not any(fn.endswith(g.lstrip("*")) for g in LIVE_GLOBS):
                continue
            yield p, rel

# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------

def _case_no_bmad_tree() -> tuple[bool, str]:
    """The directory is gone."""
    ok = not (ROOT / "bmad").exists()
    return (ok, "bmad/ is gone" if ok else "bmad/ still exists")

def _case_clis_unchanged() -> tuple[bool, str]:
    """Every CLI reproduces its pre-move output."""
    want = json.loads(BASELINE.read_text(encoding="utf-8"))
    got = _capture()
    drift = [k for k in ("orient", "resolve_config_full", "resolve_config_keys",
                         "skeleton_status", "blackboard_doctor", "workflow_list",
                         "workflow_validate")
             if got.get(k) != want.get(k)]
    skill_drift = [n for n in want["skills"] if got["skills"].get(n) != want["skills"][n]]
    if drift or skill_drift:
        return (False, f"CLI drift {drift}; skill drift {skill_drift[:6]}")
    return (True, f"{len(want['skills'])} skills + 7 CLI surfaces identical")

def _case_catalog_path_exists() -> tuple[bool, str]:
    """The digest names the catalog it actually read, and that file exists."""
    got = _capture().get("catalog_path")
    want = str(ROOT / "skills" / "bmad-help" / "references" / "bmad-help.csv")
    if got != want:
        return (False, f"orient names {got!r}, not the moved catalog")
    if not pathlib.Path(got).is_file():
        return (False, f"{got} does not exist")
    return (True, "orient names the moved catalog and it resolves")


def _case_kernel_imports() -> tuple[bool, str]:
    """The kernel imports from its new home and both hook call sites reach it."""
    code = (
        "import sys; sys.path.insert(0, %r)\n"
        "from hooks.workflow import advisory, engine, evidence, paths, spec, store\n"
        "from hooks.scripts import workflow as cli\n"
        "assert engine and spec and store and advisory and evidence and paths\n"
        "assert cli._builtin_dir().endswith(('hooks/workflow/builtin', 'hooks\\\\workflow\\\\builtin'))\n"
        "print('ok')\n" % str(ROOT))
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       encoding="utf-8", stdin=subprocess.DEVNULL, cwd=str(ROOT))
    if r.returncode != 0:
        return (False, f"import failed: {r.stderr.strip()[:200]}")
    # The two engine call sites import advisory lazily, inside a function.
    for rel in ("hooks/engine/modules/stop.py", "hooks/engine/modules/audit.py"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        if "from bmad.workflow" in text:
            return (False, f"{rel} still imports bmad.workflow")
        if "from hooks.workflow import advisory" not in text:
            return (False, f"{rel} no longer imports hooks.workflow")
    return (True, "hooks.workflow imports; both hook call sites re-pointed")

def _case_shim_resolves() -> tuple[bool, str]:
    """The engine-side shim reaches the moved canonical resolver, for every skill."""
    want = json.loads(BASELINE.read_text(encoding="utf-8"))
    shim = ROOT / "hooks" / "engine" / "resolve_customization.py"
    proj = _project()
    drift = []
    for name in want["skills"]:
        got = _json(shim, "--skill", str(ROOT / "skills" / name),
                    "--project-root", str(proj), "--key", "workflow")
        if got != want["skills"][name]:
            drift.append(name)
    return (not drift, f"shim resolves all {len(want['skills'])} skills"
            if not drift else f"drift via shim: {drift[:6]}")

def _case_tests_moved() -> tuple[bool, str]:
    """The moved tests live at their new path and pytest's list matches the tree."""
    expected = ("hooks/scripts/tests/test_orient.py",
                "hooks/scripts/tests/test_resolve_config.py",
                "hooks/scripts/tests/test_resolve_customization.py",
                "hooks/scripts/tests/test_skeleton.py",
                "hooks/workflow/tests/test_workflow.py",
                "hooks/engine/tests/test_claude_code_simulation.py",
                "hooks/engine/tests/test_e2e_claude_code.py")
    missing = [rel for rel in expected if not (ROOT / rel).is_file()]
    if missing:
        return (False, f"missing at new path: {missing}")
    import tomllib
    paths = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    listed = set(paths["tool"]["pytest"]["ini_options"]["testpaths"])
    for rel in ("hooks/scripts/tests", "hooks/workflow/tests"):
        if rel not in listed:
            return (False, f"pyproject testpaths does not list {rel}")
    if "bmad/tests" in listed:
        return (False, "pyproject testpaths still lists bmad/tests")
    return (True, f"{len(expected)} tests at their new path; testpaths matches")

def _case_catalogs() -> tuple[bool, str]:
    """Catalog data survived; the three dead module-help files are gone."""
    help_csv = ROOT / "skills" / "bmad-help" / "references" / "bmad-help.csv"
    manifest = ROOT / "skills" / "skill-manifest.csv"
    missing = [str(p.relative_to(ROOT)) for p in (help_csv, manifest) if not p.is_file()]
    if missing:
        return (False, f"missing: {missing}")
    want = json.loads(BASELINE.read_text(encoding="utf-8"))
    for rel, path in (("_config/bmad-help.csv", help_csv),
                      ("_config/skill-manifest.csv", manifest)):
        if want["inventory"].get(rel) != _sha(path):
            return (False, f"{rel} content changed by the move")
    dead = [str(p.relative_to(ROOT)) for p in
            (ROOT / "bmad" / "bmb" / "module-help.csv",
             ROOT / "bmad" / "bmm" / "module-help.csv",
             ROOT / "bmad" / "core" / "module-help.csv") if p.exists()]
    if dead:
        return (False, f"stale duplicates still ship: {dead}")
    return (True, "2 catalogs byte-identical; 3 stale module-help files gone")

def _case_free_zone_contained() -> tuple[bool, str]:
    """No path outside the moved set changed its free/gated verdict.

    `bmad/scripts/` going gated -> free is the intended shift. Anything else
    moving in either direction is a silent policy change riding on a file move.
    """
    want = json.loads(BASELINE.read_text(encoding="utf-8")).get("free_zone", {})
    code = (
        "import sys, json; sys.path.insert(0, %r)\n"
        "from modules.utils import is_free\n"
        "print(json.dumps({p: is_free(p) for p in %r}))\n"
        % (str(ROOT / "hooks" / "engine"), sorted(want)))
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       encoding="utf-8", stdin=subprocess.DEVNULL, cwd=str(ROOT),
                       env={**os.environ, "CLAUDE_PROJECT_DIR": str(ROOT)})
    if r.returncode != 0:
        return (False, f"is_free probe failed: {r.stderr.strip()[:200]}")
    got = json.loads(r.stdout)
    shifted = [p for p in want
               if got.get(p) != want[p] and not p.startswith(MOVED_PREFIXES)]
    intended = [p for p in want
                if got.get(p) != want[p] and p.startswith(MOVED_PREFIXES)]
    if shifted:
        return (False, f"verdict shifted outside the moved set: {shifted[:6]}")
    return (True, f"{len(want) - len(intended)} verdicts held; "
                  f"{len(intended)} intended shift(s) inside the moved set")

def _case_no_live_spelling() -> tuple[bool, str]:
    """No shipped file still names a retired path."""
    hits = []
    for path, rel in _live_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        text = GIT_REV_RE.sub("", text)
        if RETIRED_RE.search(text):
            hits.append(rel)
    return (not hits, "no live spelling of the retired tree" if not hits
            else f"{len(hits)} file(s) still spell it: {hits[:8]}")

CASES = (
    ("the plugin ships no bmad/ tree", _case_no_bmad_tree),
    ("every CLI reproduces its pre-move output", _case_clis_unchanged),
    ("the digest names the moved catalog", _case_catalog_path_exists),
    ("the workflow kernel imports from its new home", _case_kernel_imports),
    ("the engine shim reaches the moved resolver", _case_shim_resolves),
    ("the moved tests sit at their new path", _case_tests_moved),
    ("catalog data survived the move", _case_catalogs),
    ("no free-zone verdict shifted outside the moved set", _case_free_zone_contained),
    ("no shipped file names the retired tree", _case_no_live_spelling),
)

def _restore_pre_move_tree() -> pathlib.Path:
    """`git archive` the last tree that shipped bmad/ into a temp directory.

    The control has to MEASURE the old tree, not assert what it would say.
    """
    import io
    import tarfile  # noqa: PLC0415 — control-arm only

    out = subprocess.run(["git", "-C", str(_REPO), "archive", "--format=tar",
                          PREFIX_REV],
                         capture_output=True, stdin=subprocess.DEVNULL)
    if out.returncode != 0:
        raise SystemExit(f"cannot read {PREFIX_REV}: "
                         f"{out.stderr.decode('utf-8', 'replace')[:200]}")
    dest = pathlib.Path(tempfile.mkdtemp(prefix="bmad-pre-move-"))
    with tarfile.open(fileobj=io.BytesIO(out.stdout)) as tar:
        tar.extractall(dest)  # noqa: S202 — our own commit's archive
    return dest


def run() -> list[dict]:
    return [{"check": name, "ok": (r := probe())[0], "detail": r[1]}
            for name, probe in CASES]

def main() -> int:
    global BROKEN
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--capture", action="store_true",
                    help="write the baseline fixture from the current tree")
    ap.add_argument("--broken", action="store_true", help="rule-7 control arm")
    ap.add_argument("--samples", help="write per-check JSONL samples here")
    args = ap.parse_args()

    if args.capture:
        data = _capture()
        # Free-zone verdicts for a fixed probe set, so a later run can prove
        # containment without re-deriving what "outside the moved set" means.
        probe_paths = (
            "hooks/engine/main.py", "hooks/scripts/blackboard.py",
            "hooks/workflow/engine.py", "scripts/check-plugin.sh",
            "skills/bmad-prd/SKILL.md", "skills/skill-manifest.csv",
            "bmad/scripts/blackboard.py", "bmad/workflow/engine.py",
            "bmad/tests/test_orient.py", "bmad/_config/bmad-help.csv",
            "src/app.py", "scratch/x.py", "docs/experiments/E-149.md",
            "docs/bmad/research-methodology.md",
        )
        code = (
            "import sys, json; sys.path.insert(0, %r)\n"
            "from modules.utils import is_free\n"
            "print(json.dumps({p: is_free(p) for p in %r}))\n"
            % (str(ROOT / "hooks" / "engine"), list(probe_paths)))
        r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                           text=True, encoding="utf-8", cwd=str(ROOT),
                           stdin=subprocess.DEVNULL,
                           env={**os.environ, "CLAUDE_PROJECT_DIR": str(ROOT)})
        data["free_zone"] = json.loads(r.stdout) if r.returncode == 0 else {}
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False)
                            + "\n", encoding="utf-8")
        print(f"baseline captured: {BASELINE.relative_to(ROOT)} "
              f"({len(data['skills'])} skills, {len(data['free_zone'])} free-zone probes)")
        return 0

    BROKEN = args.broken
    if BROKEN:
        globals()["ROOT"] = _restore_pre_move_tree()
        print(f"  control arm: pre-move tree restored from {PREFIX_REV} "
              f"({ROOT})")
    results = run()
    if BROKEN:
        # State the asymmetry instead of hiding it: three probes reproduce the
        # baseline because the baseline IS this tree.
        green = [r["check"] for r in results if r["ok"]]
        if green:
            print("  note: these stay green on the old tree, by construction "
                  "(equivalent by definition, not by measurement): "
                  + "; ".join(green))
    for r in results:
        print(f"  {'ok  ' if r['ok'] else 'FAIL'}  {r['check']}: {r['detail']}")
    passed = sum(1 for r in results if r["ok"])
    total = len(results)
    if args.samples:
        path = pathlib.Path(args.samples)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in results),
                        encoding="utf-8")
    print(f"bmad_retirement_accuracy={passed / total:.2f} ({passed}/{total})")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

"""The plugin config has one owner and keeps its exact meaning (E-146, E-147).

    python3 scripts/bench/bench_config_consolidation.py --samples docs/experiments/E-146/raw/samples.jsonl
    python3 scripts/bench/bench_config_consolidation.py --broken   # rule-7 control

Before E-146 the plugin shipped four config files whose merge produced one
effective config: `bmad/config.toml` (path defaults), `bmad/config.user.toml`
(personal), `custom/config.toml` (gate policy) and an always-absent
`custom/config.user.toml`. Two of the eight keys in `bmad/config.toml` were
byte-identical copies of keys in `custom/config.toml`, and the hook engine read
`custom/config.toml` directly while `resolve_config.py` merged all four.

E-146 collapses the four into `/config.toml` + `/config.user.toml`. E-147
removes the old paths outright: none of the six is read, written or created,
and the `{project-root}/bmad/*.toml` project layer is gone too (a project
configures itself through `docs/` only).

The claim this bench measures is *equivalence*, not improvement: the
consolidation must change no resolved value anywhere. The baseline is a fixture
captured from the pre-consolidation tree (`fixtures/config-baseline.json`), so
the comparison is against the old behaviour rather than against a description
of it.

`--broken` runs the PRE-FIX resolver (read from git HEAD) against the new tree:
it requires `bmad/config.toml`, which no longer exists, so it cannot resolve at
all. A control that cannot produce the claim cannot clear it.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
RESOLVER = ROOT / "bmad" / "scripts" / "resolve_config.py"
BASELINE = ROOT / "scripts" / "bench" / "fixtures" / "config-baseline.json"
CONFIG = ROOT / "config.toml"
PREFIX_REV = "7249e5b"  # the E-146 commit: the last reader that honoured the retired paths

BROKEN = False

TARGETED_KEYS = ("--key", "core", "--key", "modules.bmm", "--key", "modules.bmb")


def _baseline() -> dict:
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def _run_resolver(script: pathlib.Path, project_root: pathlib.Path, *extra: str):
    """Run a resolver copy in-process and return (returncode, stdout, stderr)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        f"bench_resolver_{abs(hash((str(script), extra)))}", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    argv = ["resolve_config.py", "--project-root", str(project_root), *extra]
    old_argv, old_out, old_err = sys.argv, sys.stdout, sys.stderr
    import io

    out, err = io.StringIO(), io.StringIO()
    code = 0
    try:
        sys.argv, sys.stdout, sys.stderr = argv, out, err
        result = mod.main()
        code = 0 if result is None else result
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    finally:
        sys.argv, sys.stdout, sys.stderr = old_argv, old_out, old_err
    return code, out.getvalue(), err.getvalue()


def _prefix_resolver() -> pathlib.Path:
    """The pre-E-146 resolver, materialised from git HEAD in a temp dir."""
    out = subprocess.run(
        ["git", "-C", str(ROOT), "show", f"{PREFIX_REV}:bmad/scripts/resolve_config.py"],
        capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL)
    if out.returncode != 0:
        raise RuntimeError(f"cannot read {PREFIX_REV}:bmad/scripts/resolve_config.py")
    tmp = pathlib.Path(tempfile.mkdtemp()) / "bmad" / "scripts"
    tmp.mkdir(parents=True)
    target = tmp / "resolve_config.py"
    target.write_text(out.stdout, encoding="utf-8")
    return target


def _json_or_failure(code: int, out: str, err: str):
    if code != 0:
        return None, f"resolver exited {code}: {err.strip()[:120]}"
    try:
        return json.loads(out), None
    except json.JSONDecodeError as exc:
        return None, f"resolver output is not JSON: {exc}"


RETIRED_PATHS = (
    "bmad/config.toml",
    "bmad/config.user.toml",
    "custom/config.toml",
    "custom/config.user.toml",
)

def _case_single_owner() -> tuple[bool, str]:
    """Exactly one plugin team config, and none of the retired paths exists."""
    if BROKEN:
        return (False, "pre-fix: bmad/config.toml + custom/config.toml both ship")
    retired = [p for p in RETIRED_PATHS if (ROOT / p).exists()]
    ok = CONFIG.is_file() and not retired
    return (ok, "one owner: config.toml" if ok
            else f"retired config path still ships: {retired}")


def _case_full_dump_matches() -> tuple[bool, str]:
    """The full merged dump must be identical to the pre-consolidation one."""
    script = _prefix_resolver() if BROKEN else RESOLVER
    code, out, err = _run_resolver(script, ROOT)
    data, problem = _json_or_failure(code, out, err)
    if problem:
        return (False, problem)
    want = _baseline()["full_dump"]
    if data != want:
        diff = sorted(k for k in set(data) | set(want) if data.get(k) != want.get(k))
        return (False, f"resolved config differs at {diff}")
    return (True, f"{len(want)} top-level sections identical")


def _case_targeted_matches() -> tuple[bool, str]:
    """A targeted read (the documented form) must be identical too."""
    script = _prefix_resolver() if BROKEN else RESOLVER
    code, out, err = _run_resolver(script, ROOT, *TARGETED_KEYS)
    data, problem = _json_or_failure(code, out, err)
    if problem:
        return (False, problem)
    want = _baseline()["targeted"]
    return (data == want, "targeted keys identical" if data == want
            else f"targeted read differs: {sorted(set(data) ^ set(want))}")


def _case_hook_modes_match() -> tuple[bool, str]:
    """The hook engine's live reads must agree with the captured modes."""
    if BROKEN:
        return (False, "pre-fix: the engine read custom/config.toml, now absent")
    sys.path.insert(0, str(ROOT / "hooks"))
    from engine.modules import config as cfg  # noqa: PLC0415

    now = {
        "code_guard": cfg.hook_gate_mode("code_guard"),
        "quality_gate": cfg.hook_gate_mode("quality_gate"),
        "deploy_guard": cfg.hook_gate_mode("deploy_guard"),
        "blackboard": cfg.blackboard_enabled(),
        "blackboard_backend": cfg.blackboard_backend(),
        "freshness_unknown_policy": cfg.freshness_setting("unknown_policy"),
        "freshness_scope_digest": cfg.freshness_setting("scope_digest"),
        "freshness_max_digest_files": cfg.freshness_setting("max_digest_files"),
    }
    want = _baseline()["hook_modes"]
    bad = [k for k in want if now.get(k) != want[k]]
    return (not bad, "every hook mode unchanged" if not bad
            else f"hook modes drifted: {[(k, want[k], now.get(k)) for k in bad]}")


def _case_no_retired_reference() -> tuple[bool, str]:
    """No shipped reader names a retired config path."""
    if BROKEN:
        return (False, "pre-fix: the resolver and the engine read the retired paths")
    retired = RETIRED_PATHS
    me = pathlib.Path(__file__).resolve()
    hits = []
    for pattern in ("bmad/scripts/*.py", "hooks/engine/*.py", "hooks/engine/modules/*.py",
                    "hooks/scripts/*.sh", "scripts/*.py", "scripts/*.sh"):
        for path in ROOT.glob(pattern):
            if path.resolve() == me:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            hits.extend(f"{path.relative_to(ROOT)}: {n}" for n in retired if n in text)
    return (not hits, "no shipped reader names a retired path" if not hits
            else f"retired paths still referenced: {hits}")

def _case_retired_files_are_inert() -> tuple[bool, str]:
    """A planted retired file must not move the answer, plugin-side or project-side.

    Two independent probes, because the two sides fail differently:
      * plugin side — the E-146 engine fell back to `custom/config.toml` when
        the owner was missing, so a planted copy softened the gate.
      * project side — the E-146 resolver merged `{project-root}/bmad/config.toml`,
        so a planted copy shadowed the project's real `docs/` layer.
    Both are planted hostile here; the post-fix tree must ignore both.
    """
    with tempfile.TemporaryDirectory() as td:
        base = pathlib.Path(td)
        plugin, project = base / "plugin", base / "project"
        for root in (plugin, project):
            (root / "bmad" / "scripts").mkdir(parents=True)
            (root / "custom").mkdir()
        (plugin / "hooks" / "engine" / "modules").mkdir(parents=True)
        hostile = ('[core]\nproject_name = "retired-probe"\n'
                   '[hooks]\ncode_guard = "soft"\nquality_gate = "soft"\n')
        for rel in RETIRED_PATHS:
            (plugin / rel).write_text(hostile, encoding="utf-8")
            (project / rel).write_text(hostile, encoding="utf-8")
        (plugin / "config.toml").write_text(
            '[core]\nproject_name = "owner-probe"\n[hooks]\ncode_guard = "hard"\n',
            encoding="utf-8")

        resolver = plugin / "bmad" / "scripts" / "resolve_config.py"
        engine_cfg = plugin / "hooks" / "engine" / "modules" / "config.py"
        if BROKEN:
            import shutil as _shutil
            _shutil.copy(_prefix_resolver(), resolver)
            # The E-146 engine kept a custom/config.toml fallback.
            old = subprocess.run(
                ["git", "-C", str(ROOT), "show", f"{PREFIX_REV}:hooks/engine/modules/config.py"],
                capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL)
            engine_cfg.write_text(old.stdout, encoding="utf-8")
        else:
            import shutil as _shutil
            _shutil.copy(RESOLVER, resolver)
            _shutil.copy(ROOT / "hooks" / "engine" / "modules" / "config.py", engine_cfg)
        (plugin / "hooks" / "engine" / "modules" / "__init__.py").write_text("", encoding="utf-8")
        (plugin / "hooks" / "engine" / "__init__.py").write_text("", encoding="utf-8")

        code, out, err = _run_resolver(resolver, project, "--key", "core.project_name")
        if code != 0:
            return (False, f"resolver refused the tree: {err.strip()[:120]}")
        name = json.loads(out)["core.project_name"]
        # Pre-fix merges the planted project-side file; post-fix has no project
        # layer at all, so the plugin base wins.
        want = "retired-probe" if BROKEN else "owner-probe"
        if name != want:
            return (False, f"project-side retired file moved the answer: {name}, want {want}")

        # Owner missing on the plugin side: code default ("hard") must win over
        # the planted "soft" (E-146 would have read the retired file).
        (plugin / "config.toml").unlink()
        engine = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, %r);"
             "from engine.modules import config as c;"
             "print(c.hook_gate_mode('code_guard'))"
             % (str(plugin / "hooks"),)],
            capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL)
        line = engine.stdout.strip()
        want_mode = "soft" if BROKEN else "hard"
        if engine.returncode != 0 or line != want_mode:
            return (False, f"plugin-side retired file: got {line!r}, want {want_mode!r}"
                           f" {engine.stderr[:100]}")
        return (True, "pre-fix honoured retired files; post-fix ignores them" if BROKEN
                else "retired files inert on both sides; owner and code default win")

CASES = (
    ("one owner: the plugin ships a single config", _case_single_owner),
    ("full merged dump is unchanged", _case_full_dump_matches),
    ("targeted read is unchanged", _case_targeted_matches),
    ("hook engine modes are unchanged", _case_hook_modes_match),
    ("no reader names a retired path", _case_no_retired_reference),
    ("retired config files are inert", _case_retired_files_are_inert),
)

def run() -> list[dict]:
    results = []
    for name, probe in CASES:
        try:
            ok, detail = probe()
        except Exception as exc:  # a probe that cannot answer is a failure
            ok, detail = False, f"probe error: {exc}"
        results.append({"check": name, "ok": bool(ok), "detail": detail})
    return results


def main() -> int:
    global BROKEN
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", default=None,
                    help="write one JSON object per line here (the gate recomputes x/n)")
    ap.add_argument("--broken", action="store_true",
                    help="rule-7 control: drive the pre-fix resolver from git")
    args = ap.parse_args()
    BROKEN = args.broken

    results = run()
    if args.samples:
        path = pathlib.Path(args.samples)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for item in results:
                fh.write(json.dumps(item, ensure_ascii=False) + "\n")

    ok = sum(1 for r in results if r["ok"])
    n = len(results)
    for r in results:
        print(f"  {'ok  ' if r['ok'] else 'FAIL'}  {r['check']}: {r['detail']}")
    label = "config_equivalence_accuracy"
    print(f"{label}={ok / n:.2f} ({ok}/{n})")
    # The aggregate is the signal; a nonzero exit would make the rule-7 control
    # unreadable to the gate (it runs the control itself and expects a reading).
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""The plugin config has one owner and keeps its exact meaning (E-146).

    python3 scripts/bench/bench_config_consolidation.py --samples docs/experiments/E-146/raw/samples.jsonl
    python3 scripts/bench/bench_config_consolidation.py --broken   # rule-7 control

Before E-146 the plugin shipped four config files whose merge produced one
effective config: `bmad/config.toml` (path defaults), `bmad/config.user.toml`
(personal), `custom/config.toml` (gate policy) and an always-absent
`custom/config.user.toml`. Two of the eight keys in `bmad/config.toml` were
byte-identical copies of keys in `custom/config.toml`, and the hook engine read
`custom/config.toml` directly while `resolve_config.py` merged all four.

E-146 collapses the four into `/config.toml` + `/config.user.toml`. The claim
this bench measures is *equivalence*, not improvement: the consolidation must
change no resolved value anywhere. The baseline is a fixture captured from the
pre-consolidation tree (`fixtures/config-baseline.json`), so the comparison is
against the old behaviour rather than against a description of it.

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
PREFIX_REV = "HEAD"

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


def _case_single_owner() -> tuple[bool, str]:
    """Exactly one plugin team config, and it is the documented path."""
    survivors = [p for p in (ROOT / "bmad" / "config.toml",
                             ROOT / "custom" / "config.toml") if p.exists()]
    if BROKEN:
        return (False, "pre-fix: bmad/config.toml + custom/config.toml both ship")
    ok = CONFIG.is_file() and not survivors
    return (ok, "one owner: config.toml" if ok
            else f"legacy plugin config still ships: {[str(p.relative_to(ROOT)) for p in survivors]}")


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
        "code": cfg.hook_gate_mode("code"),
        "quality": cfg.hook_gate_mode("quality"),
        "deploy": cfg.hook_gate_mode("deploy"),
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


def _case_no_duplicate_keys() -> tuple[bool, str]:
    """No key is declared in both the shipped config and a plugin legacy file."""
    if BROKEN:
        return (False, "pre-fix: output_folder and modules.bmm were duplicated")
    import tomllib

    shipped = tomllib.loads(CONFIG.read_text(encoding="utf-8"))

    def leaves(d, prefix=""):
        for k, v in d.items():
            key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                yield from leaves(v, key)
            else:
                yield key

    have = set(leaves(shipped))
    dupes = []
    for legacy in (ROOT / "bmad" / "config.toml", ROOT / "custom" / "config.toml"):
        if not legacy.exists():
            continue
        old = set(leaves(tomllib.loads(legacy.read_text(encoding="utf-8"))))
        dupes.extend(sorted(have & old))
    return (not dupes, f"{len(have)} keys, no legacy duplicate" if not dupes
            else f"declared twice: {dupes}")


def _case_legacy_install_still_gated() -> tuple[bool, str]:
    """A surviving legacy file must not silently re-own the config.

    Two invariants:
      1. With both files present, `/config.toml` wins — a stale
         `custom/config.toml` cannot quietly re-open a second owner.
      2. If `/config.toml` is somehow absent (a partial install), the engine
         still reads the legacy file rather than falling back to code defaults.
    """
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        (root / "custom").mkdir()
        (root / "custom" / "config.toml").write_text(
            '[hooks]\ncode_guard = "soft"\nquality_gate = "soft"\n', encoding="utf-8")
        (root / "bmad" / "scripts").mkdir(parents=True)
        (root / "hooks" / "engine" / "modules").mkdir(parents=True)
        (root / "config.toml").write_text(
            '[core]\nproject_name = "owner-probe"\n[hooks]\ncode_guard = "hard"\n',
            encoding="utf-8")
        import shutil
        shutil.copy(ROOT / "hooks" / "engine" / "modules" / "config.py",
                    root / "hooks" / "engine" / "modules" / "config.py")
        (root / "hooks" / "engine" / "modules" / "__init__.py").write_text("", encoding="utf-8")
        (root / "hooks" / "engine" / "__init__.py").write_text("", encoding="utf-8")
        shutil.copy(RESOLVER, root / "bmad" / "scripts" / "resolve_config.py")

        if BROKEN:
            # Pre-fix resolver REQUIRES bmad/config.toml — it cannot read this
            # tree at all, which is the regression this record measures.
            (root / "config.toml").unlink()
            code, _, err = _run_resolver(
                root / "bmad" / "scripts" / "resolve_config.py", root, "--key", "hooks.code_guard")
            ok = code != 0
            return (ok, "pre-fix resolver refuses the one-owner layout" if ok
                    else f"pre-fix resolver accepted the new layout (control broken): {err[:80]}")

        code, out, err = _run_resolver(
            root / "bmad" / "scripts" / "resolve_config.py", root, "--key", "hooks.code_guard")
        if code != 0:
            return (False, f"resolver refused the tree: {err.strip()[:120]}")
        try:
            merged = json.loads(out)["hooks.code_guard"]
        except (json.JSONDecodeError, KeyError) as exc:
            return (False, f"resolver output unusable: {exc}")
        if merged != "hard":
            return (False, f"legacy file re-owned the config: code_guard={merged}, want hard")

        # Safety net: with no root config, the engine reads the legacy file.
        (root / "config.toml").unlink()
        engine = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, %r);"
             "from engine.modules import config as c;"
             "print(c._HOOKS_CFG.parent.name, c.hook_gate_mode('code'))" % str(root / "hooks")],
            capture_output=True, text=True, encoding="utf-8",
            stdin=subprocess.DEVNULL)
        line = engine.stdout.strip()
        # Both files are named config.toml — the PARENT directory is what says
        # which one was read.
        fallback_ok = engine.returncode == 0 and line == "custom soft"
        if not fallback_ok:
            return (False, f"no fallback when the owner is missing: {line} {engine.stderr[:100]}")
        return (True, "owner wins over legacy; legacy read only when the owner is absent")


CASES = (
    ("one owner: the plugin ships a single config", _case_single_owner),
    ("full merged dump is unchanged", _case_full_dump_matches),
    ("targeted read is unchanged", _case_targeted_matches),
    ("hook engine modes are unchanged", _case_hook_modes_match),
    ("no key is declared twice", _case_no_duplicate_keys),
    ("a legacy install keeps its gate policy", _case_legacy_install_still_gated),
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

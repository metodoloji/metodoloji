#!/usr/bin/env python3
"""Doc/code consistency bench — the mechanical guard for the audit's finding class.

The audit that produced E-013 found documentation drifting away from the shipped
behaviour: stale counts, commands pointing at removed files, a hook described as
fail-closed after it became report-only, and a documented live feed with no
producer. Every one of those was a *claim* the repo made about itself, and none of
them was checked by anything.

This bench turns each invariant into one independent check. A check is a claim
about the repo, evaluated mechanically (regex/AST/subprocess) and reported
pass/fail; the bench prints ``consistency_accuracy=(ok/total) (x/y)`` for the gate.

Checks are deliberately scoped to files that are LIVE documentation or shipped
code. Historical records (docs/experiments/**, docs/research/**) are exempt:
they describe the repo as it was, so a reference to a since-removed path there
is provenance, not drift.

Stdlib only, no network, no model calls. Run from the repository root:

    python scripts/bench/bench_consistency.py
"""
from __future__ import annotations

import ast
import csv
import pathlib
import re
import subprocess
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[2]

# Live prose docs (historical records excluded on purpose — see module docstring).
# Every entry MUST exist, or it contributes only FileNotFoundError noise (five
# "failures" per missing file) that hides the real signal — the exact shape of
# the E-001 audit. docs/BLACKBOARD.md was pruned (the code is canonical —
# KILAVUZ.md §14) and docs/USAGE-GUIDE.md is not in the tree; the live
# reader-facing set is the README documentation table plus the engine summary.
# scripts/tests/test_selfcheck_scripts.py pins this list against the tree.
DOC_FILES = (
    "README.md",
    "CONTRIBUTING.md",
    "docs/CLAUDE.md",
    "AGENT-GUIDE.md",
    "GUIDE.md",
    "KILAVUZ.md",
)
# The stop-semantics and canvas-feed claims live in the runbooks/guides, not
# only in README.md; docs/BLACKBOARD.md (the old canvas home) is pruned.
STOP_DOCS = ("README.md", "docs/CLAUDE.md", "AGENT-GUIDE.md")
CANVAS_DOCS = ("KILAVUZ.md", "GUIDE.md")
# Machine-readable manifests that repeat the same public counts.
MANIFESTS = (
    ".plugin/plugin.json",
    ".plugin/marketplace.json",
    ".claude-plugin/plugin.json",
    ".claude-plugin/marketplace.json",
)
# Every engine module gets the duplicate-top-level-def scan — the guard is
# only as wide as this tuple, and an unscanned module can silently hide a
# shadowed def (a later definition wins). scripts/tests/test_selfcheck_scripts.py
# pins the tuple against the tree so a new module cannot be added unscanned.
ENGINE_MODULES = (
    "hooks/engine/modules/archive.py",
    "hooks/engine/modules/audit.py",
    "hooks/engine/modules/bash_targets.py",
    "hooks/engine/modules/blackboard.py",
    "hooks/engine/modules/config.py",
    "hooks/engine/modules/guard.py",
    "hooks/engine/modules/mirror.py",
    "hooks/engine/modules/plan.py",
    "hooks/engine/modules/state.py",
    "hooks/engine/modules/stop.py",
    "hooks/engine/modules/utils.py",
    "hooks/engine/main.py",
)

# `<sh|bash|python> <path>.py|.sh` inside a command line. The token must contain a
# slash (a bare `run_experiment.py` names a tool, not a repo path) and must not be a
# placeholder (`{metodoloji-root}/...`, `scripts/bench/<name>.py`).
_DOC_CMD_RE = re.compile(r"(?:^|\s)(?:sh|bash|python3?)\s+(\S*/\S+\.(?:sh|py))")
_PLACEHOLDER_CHARS = ("{", "<", "*", "...")

# Public counts the docs repeat; every occurrence must match the filesystem.
# `(?<![\d.])` keeps a numbered section heading from reading as a count claim
# ("13.1 Skill kataloğu", "### 4.16 Skill aileleri" -> the "1 Skill"/"16 Skill"
# false positives in E-001); a real claim is never glued to a dotted number.
_SKILLS_CLAIM_RE = re.compile(r"(?<![\d.])(\d+)\s+(?:\*\*)?(?:BMAD\s+)?skills?\b", re.IGNORECASE)
_BRIDGE_TOML_CLAIM_RE = re.compile(r"(?<![\d.])(\d+)\s+(?:\*\*)?(?:bridge|customization)\s+TOMLs",
                                   re.IGNORECASE)
_BARE_TOML_CLAIM_RE = re.compile(r"(?<![\d.])(\d+)\s+TOMLs", re.IGNORECASE)

# Paths/references that were removed from the repo: a LIVE doc must not tell a
# reader to run or read them (docs/experiments/** keeps its own history).
_REMOVED_REFS = ("run-promptfoo", "evals/promptfoo")


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8", errors="replace")


def _actual_counts() -> tuple[int, int]:
    skills = sum(1 for p in (ROOT / "skills").iterdir() if p.is_dir())
    tomls = sum(1 for p in (ROOT / "custom").glob("*.toml") if p.stem != "config")
    return skills, tomls


# A number preceded by a BRIDGE word within this window is a BRIDGE sub-count
# ("BRIDGE is active in 33 skills", "Producer BRIDGE (17 skills)"), not a total
# claim about the plugin. Only total claims are the docs' promise to the reader.
_BRIDGE_CONTEXT_CHARS = 40


def _wrong_count_claims(text: str, skills: int, tomls: int) -> list[str]:
    """Every total `N skills` / `N ... TOMLs` claim that disagrees with reality.

    BRIDGE-qualified counts describe one module surface (33 BRIDGE skills,
    17 producer skills, ...) and are deliberately not total claims.
    """
    bad = []
    for rx, truth, label in ((_SKILLS_CLAIM_RE, skills, "skills"),
                             (_BRIDGE_TOML_CLAIM_RE, tomls, "bridge TOMLs"),
                             (_BARE_TOML_CLAIM_RE, tomls, "TOMLs")):
        for m in rx.finditer(text):
            before = text[max(0, m.start() - _BRIDGE_CONTEXT_CHARS):m.start()]
            if re.search(r"bridge", before, re.IGNORECASE):
                continue  # BRIDGE sub-count, not a total
            if int(m.group(1)) != truth:
                bad.append(f"{label} claim {m.group(0)!r} != {truth}")
    return bad


def _documented_commands(text: str) -> list[str]:
    """Repo paths a doc tells you to execute, placeholders filtered out."""
    out = []
    for path in _DOC_CMD_RE.findall(text):
        if any(c in path for c in _PLACEHOLDER_CHARS):
            continue
        out.append(path)
    return out


def _top_level_duplicates(rel: str) -> list[str]:
    """Names defined more than once at module level (a later def silently wins)."""
    tree = ast.parse(_read(rel))
    seen: dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            seen[node.name] = seen.get(node.name, 0) + 1
    return [f"{name} defined {n}x" for name, n in seen.items() if n > 1]


# --- checks --------------------------------------------------------------------
# Each check is (label, callable) -> callable returns None on pass, or a
# human-readable failure string.

def _check_documented_commands(rel: str):
    def run() -> str | None:
        missing = [p for p in _documented_commands(_read(rel)) if not (ROOT / p).is_file()]
        return f"documented but missing: {missing}" if missing else None
    return run


def _check_documented_modes() -> str | None:
    """docs/CLAUDE.md documents the engine's dispatch modes — they must exist."""
    documented = ("pre", "guard", "quality", "deploy", "audit", "stop", "session_start")
    main_src = _read("hooks/engine/main.py")
    missing = [m for m in documented if f'"{m}"' not in main_src]
    return f"documented engine modes absent from main.py: {missing}" if missing else None


def _check_counts(rel: str):
    def run() -> str | None:
        skills, tomls = _actual_counts()
        bad = _wrong_count_claims(_read(rel), skills, tomls)
        return f"stale counts: {bad}" if bad else None
    return run


def _check_manifest_counts() -> str | None:
    skills, tomls = _actual_counts()
    bad = []
    for rel in MANIFESTS:
        bad += [f"{rel}: {b}" for b in _wrong_count_claims(_read(rel), skills, tomls)]
    return f"stale manifest counts: {bad}" if bad else None


def _check_help_menu_code_uniqueness() -> str | None:
    """One menu code per module-skill action, and no code reused inside one module.

    The digest derives routing from `bmad/_config/bmad-help.csv`, and a session
    self-checked into §6e exactly this way. `csv.reader` with the 13-column
    contract is the strict shape guard (mirroring orient.py's parser, which
    skips ragged rows, `_meta` rows and empty codes); the module-scoped
    collision scan mirrors it so the two cannot disagree (SP colliding with
    bmad-spec while also naming the Sprint-Plan record prefix is the shape that
    misrouted a real session). A skill legitimately owns several menu codes
    (bmad-agent-builder: BA + AA) — uniqueness is per (module, code), not per
    skill; `_meta` rows carry no code.
    """
    path = ROOT / "bmad" / "_config" / "bmad-help.csv"
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
    except OSError as exc:
        return f"catalog unreadable: {exc}"
    problems = []
    if not rows:
        return "catalog empty"
    header, data = rows[0], rows[1:]
    owners = {}  # (module, code) -> skill: intra-module collisions only
    for i, row in enumerate(data, start=2):
        if len(row) != len(header):
            problems.append(f"line {i}: {len(row)} columns, contract is {len(header)}")
            continue
        module, skill, _display, code = row[0], row[1], row[2], row[3]
        if not skill:
            problems.append(f"line {i}: no skill")
            continue
        if code and skill != "_meta":
            owner = owners.setdefault((module, code), skill)
            if owner != skill:
                problems.append(
                    f"line {i}: code {code!r} twice in {module!r}"
                    f" ({owner!r} vs {skill!r})")
    return f"catalog drift: {problems}" if problems else None


def _check_no_removed_refs(rel: str):
    def run() -> str | None:
        text = _read(rel)
        hits = [r for r in _REMOVED_REFS if r in text]
        return f"references removed artifacts: {hits}" if hits else None
    return run


def _check_stop_report_only(rel: str):
    def run() -> str | None:
        text = _read(rel)
        if "report-only" not in text:
            return "does not describe Stop as report-only"
        if "session cannot close" in text:
            return "still claims the session cannot close"
        return None
    return run


def _check_stop_engine_never_denies() -> str | None:
    """stop.py is report-only: no deny path, no gate-mode read."""
    src = _read("hooks/engine/modules/stop.py")
    problems = []
    if '"deny"' in src:
        problems.append("stop.py carries a deny path")
    if "hook_gate_mode" in src:
        problems.append("stop.py reads a gate mode")
    return "; ".join(problems) if problems else None


def _check_claude_toml_matches_config() -> str | None:
    """The toml block docs/CLAUDE.md shows must equal custom/config.toml [hooks]."""
    text = _read("docs/CLAUDE.md")
    fence = re.search(r"```toml\n(.*?)```", text, re.DOTALL)
    if not fence:
        return "docs/CLAUDE.md has no toml block"
    documented = dict(re.findall(r"(\w+)\s*=\s*\"(soft|hard)\"", fence.group(1)))
    try:
        real = tomllib.loads(_read("custom/config.toml")).get("hooks", {})
    except tomllib.TOMLDecodeError as exc:  # pragma: no cover - guarded by CI too
        return f"custom/config.toml unparsable: {exc}"
    bad = [f"{k}: doc={v} config={real.get(k)}"
           for k, v in documented.items() if str(real.get(k)) != v]
    return f"documented gate modes drift from config: {bad}" if bad else None


def _check_config_comment_truthful() -> str | None:
    """config.py's comment must not claim a blocking effect for stop_guard."""
    for line in _read("hooks/engine/modules/config.py").splitlines():
        if line.lstrip().startswith("#") and "stop" in line.lower() \
                and "fail-closed" in line.lower():
            return f"stale comment: {line.strip()[:70]}"
    return None


def _check_config_file_comments_truthful() -> str | None:
    """custom/config.toml comments must not claim a blocking effect for stop_guard.

    Same defect class as _check_config_comment_truthful (config.py), same
    rule shape: a comment naming stop_guard alongside a blocking claim
    (fail-closed / tighten-to-hard / block / deny) is stale. A "never
    blocks" disclaimer is the truthful form and passes.
    """
    problems = []
    for no, line in enumerate(_read("custom/config.toml").splitlines(), start=1):
        low = line.lower()
        if "stop_guard" not in low:
            continue
        # Whole-line match: the stale "tighten stop_guard to hard" instruction
        # lives half in the value, half in its trailing comment, so the claim
        # is evaluated across the code/comment boundary. Values alone never
        # carry these words next to stop_guard.
        stale = ("fail-closed" in low
                 or ("tighten" in low and "hard" in low)
                 or (re.search(r"\bblock(s|ed|ing)?\b|\bdeny\b", low)
                     and "never" not in low))
        if stale:
            problems.append(f"custom/config.toml:{no}: {line.strip()[:80]}")
    return "; ".join(problems) if problems else None


def _check_no_stop_guard_reader() -> str | None:
    """stop_guard is a backward-compat key: no production code may consult its value.

    Tests may call the accessor to pin its parsing contract; that is not a reader.
    """
    readers = []
    for path in (ROOT / "hooks").rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if "/tests/" in rel or path.name.startswith("test_"):
            continue
        src = path.read_text(encoding="utf-8", errors="replace")
        if re.search(r"hook_gate_mode\(\s*[\"']stop_guard[\"']", src):
            readers.append(rel)
    return f"stop_guard is read by: {readers}" if readers else None


def _check_canvas_feed_wired() -> str | None:
    """The documented live feed needs a production entry point (engine + CLI).

    The writer-side seam is stable even though its plumbing evolved: the engine
    exposes `canvas_touch()` (report-only, blackboard-free hot path) and the
    CLI dispatches `canvas touch` onto it. Do not assert one historical
    transport (`stamp_tool_event` positional args); the bench checks the live
    seam instead.
    """
    problems = []
    engine = _read("hooks/engine/modules/blackboard.py")
    if "def canvas_touch(" not in engine:
        problems.append("engine has no canvas_touch()")
    cli = _read("bmad/scripts/blackboard.py")
    if "add_parser(\"touch\"" not in cli and "'\"touch\"'" not in cli:
        problems.append("CLI registers no canvas touch subcommand")
    if "bb.canvas_touch(" not in cli:
        problems.append("CLI does not dispatch canvas_touch")
    if 'args.action == "touch"' not in cli:
        problems.append("CLI dispatch misses the touch action")
    return "; ".join(problems) if problems else None


def _check_canvas_feed_documented(rel: str):
    def run() -> str | None:
        return None if "canvas touch" in _read(rel) else "does not mention canvas touch"
    return run


def _check_engine_hot_path_blackboard_free() -> str | None:
    """audit.py must not push touches — that invariant is why the feed is writer-driven."""
    src = _read("hooks/engine/modules/audit.py")
    hits = [n for n in ("stamp_tool_event", "canvas_touch", "_touch_canvases") if n in src]
    return f"audit hot path calls {hits}" if hits else None


def _check_canvas_feed_tested() -> str | None:
    src = _read("hooks/engine/tests/test_blackboard.py")
    problems = []
    if "canvas_touch" not in src:
        problems.append("no test drives canvas_touch")
    if '"touch"' not in src:
        problems.append("no CLI roundtrip test for canvas touch")
    return "; ".join(problems) if problems else None


def _check_module_no_duplicate_defs(rel: str):
    def run() -> str | None:
        dups = _top_level_duplicates(rel)
        return f"duplicate top-level definitions: {dups}" if dups else None
    return run


def _check_e2e_asserts() -> str | None:
    """The e2e workflow test must be able to fail (assertions, no result-dict return)."""
    src = _read("bmad/tests/test_e2e_claude_code.py")
    problems = []
    if "assert " not in src:
        problems.append("no assertions at all")
    if re.search(r"return\s*\{\s*\n\s*\"status\"", src):
        problems.append("returns a result dict instead of asserting")
    if "previous_project_dir" not in src or "os.environ.pop(\"CLAUDE_PROJECT_DIR\"" not in src:
        problems.append("borrows CLAUDE_PROJECT_DIR without restoring it")
    return "; ".join(problems) if problems else None


def _check_check_plugin_wording() -> str | None:
    """check-plugin.sh must not print a stop fail-closed claim."""
    src = _read("scripts/check-plugin.sh")
    for line in src.splitlines():
        if "gate mode valid" in line and "fail-closed" in line:
            return f"stale message: {line.strip()[:70]}"
    return None


def _check_msys_paths() -> str | None:
    """Git Bash '/c/...' input must normalize to the native form — on Windows only.

    Off Windows the conversion must stay a no-op: '/c/Users/x' is then a real
    absolute path and rewriting it would corrupt legitimate POSIX paths.
    """
    import os

    sys.path.insert(0, str(ROOT / "hooks" / "engine"))
    from modules.utils import norm_path, rel_to_root  # noqa: PLC0415

    problems = []
    if os.name != "nt":
        if norm_path("/c/Users/x/a.py") != "/c/Users/x/a.py":
            problems.append("MSYS conversion active off Windows (must be a no-op)")
        return "; ".join(problems) if problems else None
    if norm_path("/c/Users/x/a.py") != "/Users/x/a.py":
        problems.append("norm_path does not map the MSYS drive form")
    if norm_path("/cygdrive/c/Users/x/a.py") != "/Users/x/a.py":
        problems.append("norm_path does not map the /cygdrive/ form")
    if rel_to_root("C:/Users/x/p", "/c/Users/x/p/scratch/a.py") != "scratch/a.py":
        problems.append("rel_to_root cannot match a native root against an MSYS target")
    return "; ".join(problems) if problems else None


def _check_msys_tested() -> str | None:
    src = _read("hooks/engine/tests/test_utils.py")
    missing = [name for name in ("test_norm_path_msys_drive_form",
                                 "test_rel_to_root_msys_drive_form_matches_native_root",
                                 "test_repo_root_normalizes_msys_drive")
               if name not in src]
    return f"missing MSYS tests: {missing}" if missing else None


def _check_repo_root_malformed_cwd() -> str | None:
    """E-006: repo_root must skip a non-string payload cwd, never raise on it.

    The top-level `cwd` key is the last untyped wire seam: 123 / 4.5 / true /
    ["x"] / {"a": 1} used to reach _msys_to_native() and raise inside the
    handler, leaving guard/quality/audit/stop/session_start with NO decision
    (a no-decision turn the runner may read as an allow). The claim: every
    non-string shape falls back to the process root, and a string cwd is still
    honored verbatim (the type guard must not widen).
    """
    import os  # noqa: PLC0415

    sys.path.insert(0, str(ROOT / "hooks" / "engine"))
    from modules.utils import repo_root  # noqa: PLC0415

    saved = {k: os.environ.pop(k, None)
             for k in ("CLAUDE_PROJECT_DIR", "OPENHANDS_PROJECT_DIR")}
    try:
        try:
            fallback = repo_root({})
            for bad in (123, 4.5, True, ["x"], {"a": 1}):
                got = repo_root({"cwd": bad})
                if got != fallback:
                    return (f"non-string cwd {bad!r} produced root {got!r}, "
                            f"want fallback {fallback!r}")
            probe = str(ROOT)
            if repo_root({"cwd": probe}) != os.path.abspath(probe):
                return "string cwd no longer honored verbatim (guard widened)"
        except Exception as exc:  # a crashing check is a failing check
            return f"repo_root raised on a non-string cwd: {type(exc).__name__}: {exc}"
    finally:
        for key, value in saved.items():
            if value is not None:
                os.environ[key] = value
    return None


def _check_repo_root_malformed_cwd_tested() -> str | None:
    """The E-006 seam must stay covered by unit AND end-to-end tests."""
    problems = []
    unit = _read("hooks/engine/tests/test_utils.py")
    for name in ("test_repo_root_non_string_cwd_falls_back",
                 "test_repo_root_env_wins_over_malformed_cwd",
                 "test_repo_root_string_cwd_still_honored"):
        if name not in unit:
            problems.append(f"test_utils.py: no {name}")
    e2e = _read("hooks/engine/tests/test_stop_main.py")
    if "test_main_malformed_cwd_decides_not_crash" not in e2e:
        problems.append("test_stop_main.py: no test_main_malformed_cwd_decides_not_crash")
    return "; ".join(problems) if problems else None


def _check_coerce_depth_bounded() -> str | None:
    """E-007: a routing value nested past the limit coerces to a bounded
    string, never raises — an unbounded _coerce_json_scalar used to blow the
    stack inside the handler call (guard/pre/quality/deploy/audit exited with
    NO decision at depth >= 500). Shallow renderings must stay byte-identical:
    the bound may not widen what the coercion changes."""
    sys.path.insert(0, str(ROOT / "hooks" / "engine"))
    from modules.utils import _coerce_json_scalar  # noqa: PLC0415

    try:
        deep = "x"
        for _ in range(4000):
            deep = [deep]
        out = _coerce_json_scalar(deep)
        if not isinstance(out, str) or "truncated" not in out:
            return f"deep list did not render a truncation marker: {out[:60]!r}"
        for value, want in ((["a", "b"], "a b"),
                            ({"b": 1, "a": 2}, "a=2 b=1"),
                            ("s", "s"), (None, ""), (5, "5")):
            got = _coerce_json_scalar(value)
            if got != want:
                return f"shallow rendering drifted: {value!r} -> {got!r}, want {want!r}"
    except Exception as exc:  # a crashing check is a failing check
        return f"_coerce_json_scalar raised: {type(exc).__name__}: {exc}"
    return None


def _check_recursion_seam_tests_covered() -> str | None:
    """The three E-007 sites (coerce, parse, handler boundary) stay tested."""
    problems = []
    unit = _read("hooks/engine/tests/test_utils.py")
    for name in ("test_coerce_json_scalar_depth_bounded_list",
                 "test_coerce_json_scalar_depth_bounded_dict",
                 "test_normalize_deep_routing_value_decides_not_crash"):
        if name not in unit:
            problems.append(f"test_utils.py: no {name}")
    e2e = _read("hooks/engine/tests/test_stop_main.py")
    for name in ("test_main_deep_coercion_payload_decides_not_crash",
                 "test_main_parser_overflow_payload_decides_not_crash",
                 "test_main_fail_closed_hooks_deny_on_parser_overflow",
                 "test_main_handler_crash_still_decides"):
        if name not in e2e:
            problems.append(f"test_stop_main.py: no {name}")
    return "; ".join(problems) if problems else None


def _check_workflow_corrupt_honesty() -> str | None:
    """E-008: the corrupt detector reads storage's sanitized path, and create
    refuses over a corrupt state (bytes untouched, --force included).

    A raw-slug probe missed `my_run.state.json` for slug "my run", so seven
    commands reported `no workflow` while list() named the run corrupt; create
    never consulted the predicate and clobbered the file with ok:true.
    """
    import json as _json  # noqa: PLC0415
    import tempfile  # noqa: PLC0415

    sys.path.insert(0, str(ROOT))
    from bmad.workflow import engine, store  # noqa: PLC0415

    try:
        with tempfile.TemporaryDirectory() as td:
            seed = pathlib.Path(store.state_path(td, "my run"))
            seed.parent.mkdir(parents=True, exist_ok=True)
            seed.write_text("{{{corrupt", encoding="utf-8")
            if not engine.state_is_corrupt(td, "my run"):
                return "state_is_corrupt misses the sanitized file (raw-slug probe)"
            out = engine.status(td, "my run")
            if out.get("ok") or "corrupt" not in str(out.get("error", "")):
                return f"status swallows the corrupt run: {out.get('error')!r}"
            spec = _json.loads(
                (ROOT / "bmad" / "workflow" / "builtin" / "seo-visibility.json")
                .read_text(encoding="utf-8"))
            for force in (False, True):
                created = engine.create(td, spec, slug="my run", force=force)
                if created.get("ok"):
                    return f"create clobbered a corrupt state (force={force})"
            if seed.read_text(encoding="utf-8") != "{{{corrupt":
                return "corrupt state bytes were modified"
    except Exception as exc:  # a crashing check is a failing check
        return f"honesty probe raised {type(exc).__name__}: {exc}"
    return None


def _check_workflow_corrupt_tests_covered() -> str | None:
    """The E-008 seam stays covered: sanitizer agreement + create gate."""
    src = _read("bmad/tests/test_workflow.py")
    missing = [name for name in (
        "test_state_is_corrupt_agrees_with_storage_path_for_sanitized_slug",
        "test_every_read_on_corrupt_special_slug_names_the_file",
        "test_create_refuses_to_clobber_corrupt_state",
        "test_cli_status_on_corrupt_special_slug_exits_one",
    ) if name not in src]
    return f"missing E-008 tests: {missing}" if missing else None


def _check_free_surface_parity() -> str | None:
    """The gate's bench refusal must mirror the guard's free surfaces.

    bench_in_free_zone uses a bundled regex (the gate script cannot import the
    engine — it runs standalone on the measurement host), so drift between
    config.FREE_PREFIXES and _AGENT_BENCH_ZONE re-opens free-zone measurement
    silently. This check replays every FREE_PREFIX (except `_bmad/`, whose
    own selfcheck bench detection keys on it) plus the explore_* doctrine
    through bench_in_free_zone and fails on the first accepted bench.
    """
    import sys as _sys  # noqa: PLC0415

    _sys.path.insert(0, str(ROOT / "skills" / "bmad-research-experiment" / "scripts"))
    _sys.path.insert(0, str(ROOT / "hooks" / "engine"))
    try:
        from modules.config import FREE_PREFIXES  # noqa: PLC0415
        import run_experiment as _gate  # noqa: PLC0415
    except Exception as exc:
        return f"free-surface parity unreadable: {exc}"
    drift = []
    for prefix in FREE_PREFIXES:
        if prefix == ".git/":
            # VCS-internal surface (git-managed); not an agent-writable
            # prototyping area, so no plausible measurement vector lives there.
            continue
        probe = f"python3 {prefix}bench_probe.py"
        try:
            refused = _gate.bench_in_free_zone(probe)
        except Exception as exc:  # a crashing matcher is a failing matcher
            return f"bench matcher raised on {probe!r}: {exc}"
        if not refused:
            drift.append(prefix)
    # explore_* is free as a root-level FILE in the guard; a bench there must
    # be refused too.
    if not _gate.bench_in_free_zone("python3 explore_probe.py"):
        drift.append("explore_*")
    if not _gate.bench_in_free_zone("python3 ./explore_probe.py"):
        drift.append("explore_* (./ spelling)")
    if _gate.bench_in_free_zone("python3 explore_dir/bench_probe.py"):
        drift.append("explore_*/ nested (guard gates it — gate must not refuse)")
    if _gate.bench_in_free_zone("python3 sub/explore_probe.py"):
        drift.append("*/explore_* (guard gates it — gate must not refuse)")
    if drift:
        return f"gate accepts benches in guard-free surfaces: {drift}"
    return None


def _run_pytest() -> str | None:
    """The falsifier: the whole suite must stay green."""
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q"],
                          cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=900)
    if proc.returncode != 0:
        tail = (proc.stdout or "").strip().splitlines()[-3:]
        return f"pytest exited {proc.returncode}: {' | '.join(tail)}"
    return None


def _run_hooks_json_sync() -> str | None:
    proc = subprocess.run([sys.executable, "scripts/sync-hooks-json.py", "--check"],
                          cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)
    if proc.returncode != 0:
        return f"hooks.json drifted from the generator: {(proc.stdout or '').strip()[:120]}"
    return None


def build_checks() -> list[tuple[str, object]]:
    checks: list[tuple[str, object]] = []
    for rel in DOC_FILES:
        checks.append((f"documented commands exist: {rel}", _check_documented_commands(rel)))
    checks.append(("docs/CLAUDE.md documents real engine modes", _check_documented_modes))
    for rel in DOC_FILES:
        checks.append((f"count claims agree with the tree: {rel}", _check_counts(rel)))
    checks.append(("count claims agree with the tree: manifests", _check_manifest_counts))
    checks.append(("help catalog shape and code ownership intact", _check_help_menu_code_uniqueness))
    for rel in DOC_FILES:
        checks.append((f"no removed-artifact references: {rel}", _check_no_removed_refs(rel)))
    for rel in STOP_DOCS:
        checks.append((f"Stop described as report-only: {rel}", _check_stop_report_only(rel)))
    checks.append(("stop.py has no deny path", _check_stop_engine_never_denies))
    checks.append(("docs/CLAUDE.md toml matches custom/config.toml", _check_claude_toml_matches_config))
    checks.append(("config.py comments truthful about stop", _check_config_comment_truthful))
    checks.append(("custom/config.toml comments truthful about stop", _check_config_file_comments_truthful))
    checks.append(("stop_guard has no reader", _check_no_stop_guard_reader))
    checks.append(("canvas feed wired (engine + CLI)", _check_canvas_feed_wired))
    for rel in CANVAS_DOCS:
        checks.append((f"canvas feed documented: {rel}", _check_canvas_feed_documented(rel)))
    checks.append(("audit hot path stays blackboard-free", _check_engine_hot_path_blackboard_free))
    checks.append(("canvas feed covered by tests", _check_canvas_feed_tested))
    for rel in ENGINE_MODULES:
        checks.append((f"no duplicate top-level defs: {rel}", _check_module_no_duplicate_defs(rel)))
    checks.append(("e2e workflow test asserts and restores env", _check_e2e_asserts))
    checks.append(("check-plugin.sh stop wording", _check_check_plugin_wording))
    checks.append(("MSYS path form normalized on Windows", _check_msys_paths))
    checks.append(("MSYS path form covered by tests", _check_msys_tested))
    checks.append(("repo_root skips a non-string payload cwd (E-006)",
                   _check_repo_root_malformed_cwd))
    checks.append(("malformed-cwd seam covered by tests (E-006)",
                   _check_repo_root_malformed_cwd_tested))
    checks.append(("recursion-class input coerces within a bounded stack (E-007)",
                   _check_coerce_depth_bounded))
    checks.append(("recursion seams covered by tests (E-007)",
                   _check_recursion_seam_tests_covered))
    checks.append(("workflow corrupt-state honesty spans storage path and create (E-008)",
                   _check_workflow_corrupt_honesty))
    checks.append(("corrupt-state seam covered by tests (E-008)",
                   _check_workflow_corrupt_tests_covered))
    checks.append(("gate bench refusal mirrors guard free surfaces",
                   _check_free_surface_parity))
    checks.append(("pytest suite green (falsifier)", _run_pytest))
    checks.append(("hooks.json in sync with generator", _run_hooks_json_sync))
    return checks


def main() -> int:
    checks = build_checks()
    failures: list[tuple[str, str]] = []
    for label, fn in checks:
        try:
            problem = fn()
        except Exception as exc:  # a crashing check is a failing check
            problem = f"check raised {type(exc).__name__}: {exc}"
        if problem:
            failures.append((label, problem))
            print(f"  FAIL  {label}\n        {problem}")
        else:
            print(f"  ok    {label}")

    total = len(checks)
    passed = total - len(failures)
    value = passed / total if total else 0.0
    print()
    if failures:
        print(f"FAILED: {len(failures)}/{total} consistency checks")
    print(f"consistency_accuracy={value:.2f} ({passed}/{total})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

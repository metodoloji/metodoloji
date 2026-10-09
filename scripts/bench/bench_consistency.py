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


def _check_file_decode_honesty() -> str | None:
    """E-009: a BINARY external file yields an honest refusal on every
    production reader — never a UnicodeDecodeError traceback.

    UnicodeDecodeError is a ValueError but neither a JSONDecodeError nor a
    TOMLDecodeError, so it used to escape every `except (…DecodeError, OSError)`
    tuple: workflow state, both config loaders, and the init marker.
    """
    import contextlib as _contextlib  # noqa: PLC0415
    import importlib.util as _importlib_util  # noqa: PLC0415
    import io as _io  # noqa: PLC0415
    import tempfile  # noqa: PLC0415

    binary = b"\xff\xfe\x00 binary \x80 junk"
    sys.path.insert(0, str(ROOT))
    from bmad.workflow import engine, store  # noqa: PLC0415

    def _load(rel, name):
        spec = _importlib_util.spec_from_file_location(name, ROOT / rel)
        mod = _importlib_util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    try:
        with tempfile.TemporaryDirectory() as td:
            # workflow: binary state -> corrupt refusal, detector agrees
            state_file = pathlib.Path(store.state_path(td, "bin"))
            state_file.parent.mkdir(parents=True, exist_ok=True)
            state_file.write_bytes(binary)
            if store.read_state(td, "bin") != {}:
                return "read_state returned data for a binary state file"
            if not engine.state_is_corrupt(td, "bin"):
                return "state_is_corrupt misses a binary (undecodable) state file"
            out = engine.status(td, "bin")
            if out.get("ok") or "corrupt" not in str(out.get("error", "")):
                return f"status swallows a binary state: {out.get('error')!r}"
            # config loaders: warn/exit-policy, never raise
            rc_cfg = _load("bmad/scripts/resolve_config.py", "_bench_resolve_config")
            rc_cus = _load("bmad/scripts/resolve_customization.py",
                           "_bench_resolve_customization")
            sink = _io.StringIO()
            with _contextlib.redirect_stderr(sink):
                f = pathlib.Path(td) / "c.toml"
                f.write_bytes(binary)
                if rc_cfg.load_toml(f) != {} or rc_cus.load_toml(f) != {}:
                    return "load_toml parsed a binary layer"
                pathlib.Path(td, "c.yaml").write_bytes(binary)
                if rc_cfg.load_legacy_yaml(pathlib.Path(td) / "c.yaml") != {}:
                    return "load_legacy_yaml parsed a binary layer"
            # skeleton: binary marker -> unreadable problem, not missing
            sk = _load("bmad/scripts/skeleton.py", "_bench_skeleton")
            marker = pathlib.Path(td) / ".metodoloji" / "initialized"
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_bytes(binary)
            if sk.read_marker(pathlib.Path(td)) != {}:
                return "read_marker parsed a binary marker"
            if not sk.marker_unreadable(pathlib.Path(td)):
                return "marker_unreadable misses a binary marker"
            state = sk.status(pathlib.Path(td))
            if not any("unreadable" in p for p in state["problems"]):
                return f"status hides the unreadable marker: {state['problems']}"
            if any("is missing" in p for p in state["problems"]):
                return "unreadable marker reported as missing"
    except Exception as exc:  # a crashing check is a failing check
        return f"decode probe raised {type(exc).__name__}: {exc}"
    return None


def _check_file_decode_tests_covered() -> str | None:
    """The E-009 seam stays tested across all five surfaces."""
    missing = []
    groups = {
        "bmad/tests/test_workflow.py": (
            "test_read_state_binary_file_is_corrupt_not_crash",
            "test_status_on_binary_state_names_the_file",
            "test_cli_list_marks_binary_state_corrupt",
            "test_load_spec_binary_file_falls_back_to_empty",
            "test_read_events_binary_log_returns_empty",
        ),
        "bmad/tests/test_resolve_config.py": (
            "test_load_toml_binary_layer_warns_not_crash",
            "test_load_legacy_yaml_binary_layer_warns_not_crash",
        ),
        "bmad/tests/test_resolve_customization.py": (
            "test_binary_team_toml_warns_and_keeps_base",
        ),
        "bmad/tests/test_skeleton.py": (
            "test_binary_marker_is_unreadable_not_missing",
            "test_cli_status_binary_marker_reports_unreadable_not_crash",
        ),
    }
    for rel, names in groups.items():
        src = _read(rel)
        missing += [f"{rel}: {n}" for n in names if n not in src]
    return f"missing E-009 tests: {missing}" if missing else None


def _rule():
    """The release rule itself — the bench checks tags against it, not a copy."""
    if not hasattr(_rule, "module"):
        import importlib.util as _ilu

        spec = _ilu.spec_from_file_location("versioning", ROOT / "scripts" / "versioning.py")
        module = _ilu.module_from_spec(spec)
        spec.loader.exec_module(module)
        _rule.module = module
    return _rule.module


def _check_version_ci_scope() -> str | None:
    """E-010 … E-014: the CI exists, is VERSION-ONLY, and no live doc still denies it.

    The workflow must derive every number from the rule
    (scripts/versioning.py) with write permission for tags, must never grow a
    test/audit step (those stay local by design), and since E-014 may write
    no version and create no commit — the hook owns the number, the pipeline
    owns the pointer. The four places that used to claim "no CI workflow"
    must be telling the truth again.
    """
    wf = ROOT / ".github" / "workflows" / "release-numbers.yml"
    if not wf.is_file():
        return ".github/workflows/release-numbers.yml missing"
    text = wf.read_text(encoding="utf-8")
    problems = []
    for required in ("scripts/versioning.py", "contents: write",
                     "--position", "rev-list"):
        if required not in text:
            problems.append(f"workflow misses {required!r}")
    code = "\n".join(line for line in text.splitlines()
                     if not line.lstrip().startswith("#"))
    for forbidden in ("pytest", "check-plugin", "check-custom",
                      "check-methodology", "check-techdebt", "check-handoff"):
        if forbidden in code:
            problems.append(f"CI runs an audit: {forbidden}")
    for forbidden in ("git commit", "bump-version", "--set-version",
                      "--by", "[skip-version]"):
        if forbidden in code:
            problems.append(f"CI writes or commits: {forbidden}")
    for rel, phrase in (("README.md", "no CI workflow in this repo"),
                        ("GUIDE.md", "no CI workflow at the repo root"),
                        ("KILAVUZ.md", "CI workflow'u yok"),
                        ("scripts/tests/test_selfcheck_scripts.py",
                         "This repo has no CI workflow")):
        if phrase in _read(rel):
            problems.append(f"{rel} still claims CI does not exist")
    return "; ".join(problems) if problems else None


def _check_version_bump_tests_covered() -> str | None:
    """The E-010 bump script and workflow stay covered by tests."""
    src = _read("scripts/tests/test_bump_version.py")
    names = ("test_positional_advance_math",
             "test_bump_updates_every_manifest_and_claim",
             "test_bump_preserves_line_endings",
             "test_check_mode_is_read_only",
             "test_skew_refuses_before_any_write",
             "test_missing_version_manifest_refuses",
             "test_bump_then_check_agree",
             "test_workflow_is_version_only")
    missing = [n for n in names if n not in src]
    return f"missing E-010 tests: {missing}" if missing else None


def _check_version_ci_tag() -> str | None:
    """E-011 … E-014: the run publishes a tag for every number it VERIFIED.

    The first CI bump proved the gap: 0.1.6 landed on main while the last tag
    was still v0.1.5, cut by hand — a version nobody can point at is not a
    release. The workflow must name each tag from the rule's own arithmetic
    (never a number typed in YAML), check every commit's tree against its
    position BEFORE the first pointer goes out, publish annotated tags, and
    survive a re-run without re-pointing a name — all of it with no bump
    commit in sight (E-014: the pipeline creates nothing). The three live
    docs must say so instead of leaving the pointer a manual act.
    """
    wf = ROOT / ".github" / "workflows" / "release-numbers.yml"
    if not wf.is_file():
        return ".github/workflows/release-numbers.yml missing"
    text = wf.read_text(encoding="utf-8")
    problems = []
    for required in ("versioning.py --position", "git tag -a",
                     'git push origin "refs/tags/$TAG"'):
        if required not in text:
            problems.append(f"workflow misses {required!r}")
    verify_at = text.find('if [ "$FAIL" -ne 0 ]')
    tag_at = text.find("git tag -a")
    if verify_at < 0 or tag_at < 0:
        problems.append("workflow lost either the verify gate or the tag step")
    elif verify_at > tag_at:
        problems.append("a tag can be published before every number is verified")
    else:
        if "already exists" not in text:
            problems.append("a re-run would re-tag a name that already exists")
        if "git rev-parse" not in text:
            problems.append("the tag step cannot resolve what it publishes")
    for rel, phrase in (("README.md", "publishes `vX.Y.Z`"),
                        ("GUIDE.md", "cuts the tags"),
                        ("KILAVUZ.md", "etiketi keser")):
        if phrase not in _read(rel):
            problems.append(f"{rel} does not say CI publishes the tags")
    return "; ".join(problems) if problems else None


def _check_version_tag_tests_covered() -> str | None:
    """The E-011 release-tag seam stays falsifiable."""
    src = _read("scripts/tests/test_bump_version.py")
    names = ("test_print_version_is_bare_and_read_only",
             "test_workflow_tags_the_release")
    missing = [n for n in names if n not in src]
    if missing:
        return f"missing E-011 tests: {missing}"
    for required in ("--print-version", "git tag -a", "refs/tags/"):
        if required not in src:
            return f"the E-011 tag test does not pin {required!r}"
    return None


def _git_stdout(*args: str) -> tuple[int, str]:
    """(exit code, stdout) for a git call in this repo — never raises."""
    try:
        proc = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=60)
    except Exception as exc:  # git missing is a failing check, not a crash
        return 1, f"{type(exc).__name__}: {exc}"
    return proc.returncode, (proc.stdout or "")


def _check_release_numbering_rule() -> str | None:
    """E-012/E-013: the number is a property of the commit, and the chain proves it.

    Two halves. The workflow must derive the target from git (a relative bump
    preserves whatever drift the tree had — that is exactly how twelve commits
    shipped with no number of their own) and re-check the number against the
    commit's position before publishing a pointer for it. And the tags that
    actually exist must obey the rule scripts/versioning.py owns — a tag the
    chain cannot place is the historical bug (v0.1.5 on the tenth commit)
    coming back.
    """
    wf = ROOT / ".github" / "workflows" / "release-numbers.yml"
    if not wf.is_file():
        return ".github/workflows/release-numbers.yml missing"
    text = wf.read_text(encoding="utf-8")
    problems = []
    for required in ("--position", "scripts/versioning.py", "rev-list --count",
                     "max-parents=0", "EXPECTED", "git tag -a", "refs/tags/"):
        if required not in text:
            problems.append(f"workflow misses {required!r}")
    if "--by " in text:
        problems.append("the relative bump (--by) came back")

    code, first = _git_stdout("rev-list", "--max-parents=0", "HEAD")
    if code != 0:
        return f"git cannot read the history: {first.strip()[:120]}"
    baseline_commit = first.strip().splitlines()[-1]
    code, order = _git_stdout("rev-list", "--reverse", f"{baseline_commit}..HEAD")
    if code != 0:
        return f"git cannot walk the history: {order.strip()[:120]}"
    position = {baseline_commit: 0}
    for i, sha in enumerate(order.split(), start=1):
        position[sha] = i
    code, tag_names = _git_stdout("tag", "--list", "v*")
    if code != 0:
        return f"git cannot list tags: {tag_names.strip()[:120]}"
    rule = _rule()
    rule_tags = [t for t in tag_names.split()
                 if re.fullmatch(r"v\d+\.\d+\.\d+", t)]
    if not rule_tags:
        return "no release tags at all — the chain has no baseline to check"
    if "v" + rule.auto_version(0) not in rule_tags:
        problems.append(f"the position-0 tag v{rule.auto_version(0)} is gone — "
                        "the count is anchored to nothing")
    numbers, newest = [], None
    for tag in rule_tags:
        try:
            wanted = rule.auto_position(tag[1:])
        except rule.VersionError as exc:
            problems.append(f"{tag} is not a number the rule can produce: {exc}")
            continue
        numbers.append(wanted)
        code, target = _git_stdout("rev-parse", "-q", "--verify", f"refs/tags/{tag}^{{commit}}")
        if code != 0 or not target.strip():
            problems.append(f"{tag} does not resolve to a commit")
            continue
        at = position.get(target.strip())
        if at is None:
            problems.append(f"{tag} points outside this history")
        elif at != wanted:
            problems.append(f"{tag} sits on commit #{at} after the baseline, not #{wanted}")
        if at is not None and (newest is None or at > newest[0]):
            newest = (at, tag, target.strip())
    # A hole means a release point whose number nobody published — the shape of
    # the bug this experiment repaired (v0.1.1..v0.1.4 never existed). The
    # chain may stop short of HEAD (tags are pushed separately); it may not
    # skip a number in between.
    if numbers:
        holes = [n for n in range(min(numbers), max(numbers) + 1)
                 if n not in set(numbers)]
        if holes:
            problems.append("the tag chain has holes (positions with no tag): "
                            + ", ".join("v" + rule.auto_version(n) for n in holes))
    # The newest release point must be a tree that holds its own number: a
    # pointer to a tree claiming another version is the E-011 lie. (Older tags
    # may name a position whose tree still says the previous number — that
    # convention is documented in docs/VERSION-HISTORY.md.)
    if newest:
        code, tree = _git_stdout("show", f"{newest[2]}:.plugin/plugin.json")
        if code != 0:
            problems.append(f"cannot read the tree of the newest release {newest[1]}")
        elif f'"version": "{newest[1][1:]}"' not in tree:
            problems.append(f"the newest release {newest[1]} points at a tree whose "
                            f"manifests do not hold {newest[1][1:]}")
    return "; ".join(problems) if problems else None


def _check_release_history_documented() -> str | None:
    """The repair must be readable by a stranger (E-012).

    A retro-assigned tag chain that nobody wrote down is indistinguishable
    from a fabricated one: the rule, the mismatch between those tags and the
    version strings inside the old trees, and the re-pointed v0.1.5 all have
    to be in docs/VERSION-HISTORY.md — and the live docs must point at it.
    """
    problems = []
    rel = "docs/VERSION-HISTORY.md"
    path = ROOT / rel
    if not path.is_file():
        return f"{rel} missing — the repair is undocumented"
    text = _read(rel)
    for phrase in ("commits since the first commit",   # the rule
                   "retro-assigned",                   # how the tags got there
                   "re-pointed",                       # the v0.1.5 correction
                   "v0.1.0"):
        if phrase not in text:
            problems.append(f"{rel} does not state {phrase!r}")
    for doc, phrase in (("README.md", "commits since the first commit"),
                        ("GUIDE.md", "commits since the first commit"),
                        ("KILAVUZ.md", "ilk commit'ten sonraki commit")):
        if phrase not in _read(doc):
            problems.append(f"{doc} does not state the numbering rule")
    if rel not in _read("README.md"):
        problems.append("README.md does not link the version history")
    return "; ".join(problems) if problems else None


def _check_release_repair_tests_covered() -> str | None:
    """The E-012 repair stays falsifiable (tool + absolute rule)."""
    missing = []
    groups = {
        "scripts/tests/test_release_tags.py": (
            "test_plan_maps_every_commit_after_the_baseline",
            "test_plan_writes_nothing",
            "test_apply_creates_annotated_tags_and_no_commit",
            "test_misplaced_tag_is_refused_then_moved_on_request",
            "test_baseline_must_be_the_first_commit",
            "test_missing_or_malformed_baseline_refuses",
            "test_unplaceable_tag_blocks_apply",
            "test_push_publishes_created_and_moved_tags",
            "test_no_remote_is_a_loud_refusal",
        ),
        "scripts/tests/test_bump_version.py": (
            "test_set_version_names_the_absolute_number",
            "test_workflow_derives_the_number_absolutely",
        ),
    }
    for rel, names in groups.items():
        src = _read(rel)
        missing += [f"{rel}: {n}" for n in names if n not in src]
    return f"missing E-012 tests: {missing}" if missing else None


def _check_release_line_boundaries() -> str | None:
    """E-013: the 0.2.0 boundary is arithmetic, and only a human opens a major line.

    "When do we move to 0.2.0?" has to have an answer nobody has to decide: on
    the automatic line the block opens every 100 positions (position 100 IS
    0.2.0), the rule that says so is scripts/versioning.py, and the tree this
    repo ships must itself hold a number that rule can produce. Leaving the
    line (1.0.0) must be an explicit declaration — the workflow checks the
    `[line $BASE.0]` marker in the committing commit's own message (naming the
    line's OPENING, derived from the tree, never a stale number), tags the
    declaring commit and rewrites nothing, and refuses a major line nobody
    declared. One pass, no declare mode: since E-014 the declaration IS the
    release.
    """
    rule = _rule()
    problems = []
    if rule.AUTO_BLOCK != 100:
        problems.append(f"AUTO_BLOCK is {rule.AUTO_BLOCK} — the docs promise 100")
    for position, version in ((0, "0.1.0"), (99, "0.1.99"), (100, "0.2.0"),
                              (199, "0.2.99"), (200, "0.3.0")):
        got = rule.auto_version(position)
        if got != version or rule.auto_position(version) != position:
            problems.append(f"position {position} <-> {version} is broken (got {got})")
    wf = ROOT / ".github" / "workflows" / "release-numbers.yml"
    if not wf.is_file():
        return ".github/workflows/release-numbers.yml missing"
    text = wf.read_text(encoding="utf-8")
    for required in ("--is-auto", "[line ", 'grep -qF "[line $BASE.0]"',
                     'OPENING_TAG="v$BASE.0"', "::error::", "never declared"):
        if required not in text:
            problems.append(f"workflow misses {required!r}")
    bump_src = _read("scripts/bump-version.py")
    for required in ("versioning.auto_position(old)",   # the tree must be producible
                     "versioning.auto_position(target)",  # and so must the target
                     "refusing to open line", "versioning.same_line"):
        if required not in bump_src:
            problems.append(f"bump-version.py misses {required!r}")
    # The live tree must hold a number the rule can produce, and a major line
    # must have been declared (its v<base>.0 tag exists).
    m = re.search(r'"version"\s*:\s*"([^"]+)"', _read(".plugin/plugin.json"))
    if not m:
        problems.append(".plugin/plugin.json has no version")
        return "; ".join(problems)
    current = m.group(1)
    if rule.is_auto(current):
        try:
            rule.auto_position(current)
        except rule.VersionError as exc:
            problems.append(f"the shipped version {current} is not producible: {exc}")
    else:
        base = "%d.%d" % rule.line(current)
        code, target = _git_stdout("rev-parse", "-q", "--verify",
                                   f"refs/tags/v{base}.0^{{commit}}")
        if code != 0 or not target.strip():
            problems.append(f"{current} claims the line {base} but v{base}.0 was "
                            "never declared")
    return "; ".join(problems) if problems else None


def _check_release_boundary_documented() -> str | None:
    """The boundary rule must be readable without opening the code (E-013)."""
    problems = []
    for rel, phrases in (("docs/VERSION-HISTORY.md",
                          ("position 100", "0.2.0", "[line X.Y.0]", "declared")),
                         ("README.md", ("a new minor block opens every 100 commits",)),
                         ("GUIDE.md", ("a new minor block opens every 100 commits",)),
                         ("KILAVUZ.md", ("her 100 commit'te bir yeni minor blo\u011fu a\u00e7\u0131l\u0131r",))):
        text = _read(rel)
        for phrase in phrases:
            if phrase not in text:
                problems.append(f"{rel} does not state {phrase!r}")
    return "; ".join(problems) if problems else None


def _check_release_boundary_tests_covered() -> str | None:
    """The E-013 boundary stays falsifiable (rule, pipeline, setters)."""
    groups = {
        "scripts/tests/test_versioning.py": (
            "test_blocks_open_every_hundred_positions",
            "test_position_is_the_exact_inverse",
            "test_impossible_numbers_are_refused",
            "test_line_helpers",
            "test_cli_answers_the_pipeline_questions",
        ),
        "scripts/tests/test_release_pipeline.py": (
            "test_ordinary_push_lands_on_the_position_it_owns",
            "test_block_boundary_is_arithmetic_not_a_decision",
            "test_declared_line_is_tagged_and_never_rewritten",
            "test_declared_line_takes_its_next_patch",
            "test_undeclared_major_line_is_refused_loudly",
            "test_illegal_auto_number_is_refused_and_never_tagged",
        ),
        "scripts/tests/test_bump_version.py": (
            "test_position_names_the_number_the_commit_owns",
            "test_declared_line_takes_patches_but_not_new_lines",
            "test_workflow_requires_an_explicit_line_declaration",
        ),
    }
    missing = []
    for rel, names in groups.items():
        src = _read(rel)
        missing += [f"{rel}: {n}" for n in names if n not in src]
    return f"missing E-013 tests: {missing}" if missing else None


def _check_commit_time_numbering() -> str | None:
    """E-014: the number is written by the commit that owns it — committed hooks.

    Both hooks must ship with the repository (an uncommitted hook is no hook):
    LF-clean sh scripts that derive the position from the same `rev-list
    --count` the pipeline verifies with, wired together through the writer's
    `--merge-head` mode (a merge commit has no pre-commit of its own, so
    post-merge writes its number and amends). And this clone must have them
    INSTALLED — `core.hooksPath = .githooks` — because a fresh clone that
    never ran the install line produces commits with the previous number;
    better to see it here than as a red push.
    """
    problems = []
    for rel, needles in ((".githooks/pre-commit",
                          ("#!/bin/sh", "rev-list --count", "--merge-head",
                           "bump-version.py", "core.hooksPath")),
                         (".githooks/post-merge",
                          ("#!/bin/sh", "--merge-head", "git commit --amend",
                           "MERGE_HEAD"))):
        path = ROOT / rel
        if not path.is_file():
            problems.append(f"{rel} is missing — a hook that is not committed "
                            "does not exist for anyone but this clone")
            continue
        text = path.read_text(encoding="utf-8")
        if "\r" in text:
            problems.append(f"{rel} has CRLF line endings — sh would refuse it")
        for needle in needles:
            if needle not in text:
                problems.append(f"{rel} misses {needle!r}")
    code, value = _git_stdout("config", "core.hooksPath")
    if code != 0 or value.strip() != ".githooks":
        problems.append(f"this clone is not wired: core.hooksPath={value.strip()!r} "
                        "(git config core.hooksPath .githooks)")
    return "; ".join(problems) if problems else None


def _check_pipeline_verifies_and_tags_only() -> str | None:
    """E-014: CI writes no version and creates no commit; every name comes from git.

    The pipeline's whole output is tags: each number derived from the rule
    (never typed in YAML), every commit verified before the first pointer is
    published, a re-run that never re-points a name — and no `git commit`, no
    bump script, no guard on the executable lines. Two of the last four
    commits of the old pipeline existed only to carry a number; this check is
    what says that out loud. The pipeline's own tests pin the same claim.
    """
    wf = ROOT / ".github" / "workflows" / "release-numbers.yml"
    if not wf.is_file():
        return ".github/workflows/release-numbers.yml missing"
    text = wf.read_text(encoding="utf-8")
    problems = []
    for required in ("versioning.py --position", "rev-list --count",
                     "git tag -a", 'git push origin "refs/tags/$TAG"'):
        if required not in text:
            problems.append(f"workflow misses {required!r}")
    code = "\n".join(line for line in text.splitlines()
                     if not line.lstrip().startswith("#"))
    for forbidden in ("git commit", "bump-version", "--set-version", "--by",
                      "[skip-version]"):
        if forbidden in code:
            problems.append(f"the pipeline writes or commits: {forbidden}")
    verify_at = text.find('if [ "$FAIL" -ne 0 ]')
    tag_at = text.find("git tag -a")
    if verify_at < 0 or tag_at < 0:
        problems.append("the verify gate or the tag step is gone")
    elif verify_at > tag_at:
        problems.append("a tag can be published before every number is verified")
    src = _read("scripts/tests/test_release_pipeline.py")
    for name in ("test_the_pipeline_never_creates_a_commit",
                 "test_ordinary_push_lands_on_the_position_it_owns",
                 "test_a_stale_tree_is_refused_naming_the_number_it_owns"):
        if name not in src:
            problems.append(f"missing E-014 pipeline test: {name}")
    return "; ".join(problems) if problems else None


def _check_commit_time_flow_documented_and_tested() -> str | None:
    """E-014: the commit-time flow is readable in the docs and falsifiable.

    The claim must survive without reading the code: VERSION-HISTORY names
    both hooks and the install line, the experiment record itself names the
    artifacts it touched (the workflow was renamed, the merge path runs
    through post-merge), and both test seams exist — the hooks proven against
    real throwaway repositories (writes, refusals, merges, `--no-verify` left
    for the pipeline to catch) and the pipeline run against its own shell.
    """
    problems = []
    for phrase in (".githooks/pre-commit", "post-merge", "core.hooksPath"):
        if phrase not in _read("docs/VERSION-HISTORY.md"):
            problems.append(f"VERSION-HISTORY misses {phrase!r}")
    record = _read("docs/experiments/E-014.md")
    for phrase in (".githooks/post-merge", "release-numbers.yml",
                   "never creates a commit"):
        if phrase not in record:
            problems.append(f"E-014 record misses {phrase!r}")
    for rel, names in {
        "scripts/tests/test_git_hooks.py": (
            "test_the_commit_that_owns_the_number_writes_it",
            "test_an_already_correct_tree_is_left_alone",
            "test_unstaged_work_in_a_version_file_is_refused",
            "test_a_clean_merge_is_written_by_the_merge_commit_itself",
            "test_a_conflicted_merge_is_written_when_the_author_commits_it",
            "test_no_verify_is_caught_downstream_not_here",
        ),
        "scripts/tests/test_release_pipeline.py": (
            "test_the_pipeline_never_creates_a_commit",
            "test_rerun_after_success_is_idempotent",
        ),
    }.items():
        src = _read(rel)
        missing = [n for n in names if n not in src]
        if missing:
            problems.append(f"missing E-014 tests in {rel}: {missing}")
    return "; ".join(problems) if problems else None


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
    checks.append(("binary files yield honest refusals on every reader (E-009)",
                   _check_file_decode_honesty))
    checks.append(("file-decode seam covered by tests (E-009)",
                   _check_file_decode_tests_covered))
    checks.append(("version CI exists and is version-only; docs agree (E-010)",
                   _check_version_ci_scope))
    checks.append(("version bump seam covered by tests (E-010)",
                   _check_version_bump_tests_covered))
    checks.append(("version CI cuts and publishes the release tag (E-011)",
                   _check_version_ci_tag))
    checks.append(("release-tag seam covered by tests (E-011)",
                   _check_version_tag_tests_covered))
    checks.append(("release numbering is absolute and the tag chain obeys it (E-012)",
                   _check_release_numbering_rule))
    checks.append(("release-numbering repair documented (E-012)",
                   _check_release_history_documented))
    checks.append(("release-numbering repair covered by tests (E-012)",
                   _check_release_repair_tests_covered))
    checks.append(("release-line boundaries are arithmetic and declarable (E-013)",
                   _check_release_line_boundaries))
    checks.append(("release-line boundary documented (E-013)",
                   _check_release_boundary_documented))
    checks.append(("release-line boundary covered by tests (E-013)",
                   _check_release_boundary_tests_covered))
    checks.append(("number written at commit time by committed hooks (E-014)",
                   _check_commit_time_numbering))
    checks.append(("pipeline verifies and tags only — never writes, never commits (E-014)",
                   _check_pipeline_verifies_and_tags_only))
    checks.append(("commit-time flow documented and covered by tests (E-014)",
                   _check_commit_time_flow_documented_and_tested))
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

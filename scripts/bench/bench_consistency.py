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
import os
import pathlib
import re
import shlex
import subprocess
import sys
import tempfile
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

# E-049: the audit family is DERIVED from scripts/check-*, and the reader-facing
# inventory of it (the runnable blocks, the `#0–#6N` ranges, the `# map:`, the
# count sentences) lives in three docs. Those claims are hand-maintained and had
# drifted in four independent ways when this rule was written.
_AUDIT_INVENTORY_DOCS = ("README.md", "GUIDE.md", "KILAVUZ.md")
_AUDIT_BLOCK_ANCHORS = (
    ("README.md", "## Verify your install"),
    ("GUIDE.md", "**Step 6 — Install verification"),
    ("GUIDE.md", "## 16. Auditing"),
    ("KILAVUZ.md", "**Adım 6 — Kurulum doğrulaması"),
    ("KILAVUZ.md", "## 16. Denetim"),
)
_AUDIT_COUNT_CLAIMS = (("README.md", r"[Tt]he (\w+) checks\b"),
                       ("GUIDE.md", r"the (\w+) checks locally"),
                       ("KILAVUZ.md", r"(\d+) kontrolü yerelde"))
# The verify blocks also state how big the suite is. An EXACT number rots on
# every test-adding commit (measured 2026-10-15: README claimed 1,331 while the
# suite collected 1,443 — stale by 112 with nothing noticing), so the honest
# form is a floor, and the floor is what gets measured.
_AUDIT_TEST_COUNT_BLOCKS = (("README.md", "## Verify your install"),
                            ("GUIDE.md", "**Step 6 — Install verification"),
                            ("KILAVUZ.md", "**Adım 6 — Kurulum doğrulaması"))
_TEST_COUNT_RE = re.compile(r"#\s*([\d,]+)(\+?)\s+tests?\b")
_AUDIT_COUNT_SPLIT_RE = re.compile(
    r"pytest \+ (\d+) static checks? \+ handoff lint|pytest \+ (\d+) statik check \+ handoff lint")
_AUDIT_RANGE_RE = re.compile(r"#(\d[0-9a-z]*)\s*[–—−]\s*#(\d[0-9a-z]*)")
_AUDIT_SCRIPT_RE = re.compile(r"check-[a-z]+\.(?:sh|py)")
_AUDIT_MAP_RE = re.compile(r"(?m)^\*\*# (?:map|haritası):\*\* (.+)$")
_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                 "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
                 "eleven": 11, "twelve": 12}


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
    skill; `_meta` rows carry no code. It also pins that every non-`_meta`
    `skill` names a directory that ships (E-042): the Web Design Studio rows
    offered `bmad-wds-*` names for dirs named `wds-*`.
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
    shipped = {p.parent.name for p in (ROOT / "skills").glob("*/SKILL.md")}
    owners = {}  # (module, code) -> skill: intra-module collisions only
    for i, row in enumerate(data, start=2):
        if len(row) != len(header):
            problems.append(f"line {i}: {len(row)} columns, contract is {len(header)}")
            continue
        module, skill, _display, code = row[0], row[1], row[2], row[3]
        if not skill:
            problems.append(f"line {i}: no skill")
            continue
        if skill != "_meta" and skill not in shipped:
            problems.append(f"line {i}: skill {skill!r} has no skills/{skill}/ directory")
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
        # stdin=DEVNULL at EVERY spawn in this file: a child that inherits the
        # parent's stdio handle cannot be launched when that handle is not
        # duplicable (WinError 6 on Windows — the class the suite hit when the
        # gate ran it with no console), and nothing the bench spawns reads
        # stdin.
        proc = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=60, stdin=subprocess.DEVNULL)
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
    own selfcheck bench detection keys on it), the FREE_DOC_MD / FREE_DOC_RAW
    docs surfaces and the explore_* doctrine through bench_in_free_zone and
    fails on the first accepted bench (E-035: an enumeration that stopped at
    FREE_PREFIXES let a fabricated measurement run from docs/*/raw/). It then
    probes the case rule both ways: the refusal must equal guard-free for
    every spelling, so the bundled matcher folds exactly when utils.is_free
    folds (os.name == "nt"), never a blanket (?i).
    """
    import sys as _sys  # noqa: PLC0415

    _sys.path.insert(0, str(ROOT / "skills" / "bmad-research-experiment" / "scripts"))
    _sys.path.insert(0, str(ROOT / "hooks" / "engine"))
    try:
        from modules.config import FREE_PREFIXES  # noqa: PLC0415
        from modules.utils import is_free  # noqa: PLC0415
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
    # The docs surfaces are guard-free too (FREE_DOC_MD documents and
    # FREE_DOC_RAW raw trees): a bench there is the same fabricable
    # measurement the rule refuses everywhere else (E-035).
    for probe in ("python3 docs/e_surface/raw/bench_probe.py",
                  "python3 docs/e_surface/raw/deep/bench_probe.py",
                  "python3 docs/e_surface.md"):
        try:
            refused = _gate.bench_in_free_zone(probe)
        except Exception as exc:
            return f"bench matcher raised on {probe!r}: {exc}"
        if not refused:
            drift.append(probe.removeprefix("python3 "))
    # ...and the reverse direction must hold too: the gate must NOT refuse
    # what the guard gates (a docs path that is neither a document nor a raw
    # tree is protected code).
    if _gate.bench_in_free_zone("python3 docs/e_surface/bench_probe.py"):
        drift.append("docs/<sub>/ guarded path (gate must not refuse)")
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
    # Case-rule parity (E-035): refusal == guard-free for every spelling —
    # the bundled matcher folds on the same OSes utils.is_free folds, so a
    # blanket (?i) (POSIX: refusing guarded `SCRATCH/…`) or a missing fold
    # (Windows: accepting `SCRATCH/…` the guard frees) both surface here.
    # The probe set is limited to the UNCONDITIONAL free surfaces — plugin
    # trees (hooks/scripts/skills) are freed only in self-modification and
    # host the benches themselves, so they are deliberately not gate-refused.
    for probe in ("SCRATCH/bench_probe.py", "Scratch/bench_probe.py",
                  "DOCS/e_surface/raw/bench_probe.py", "EXPLORE_probe.py"):
        try:
            guarded_free = bool(is_free(probe))
            refused = _gate.bench_in_free_zone(f"python3 {probe}")
        except Exception as exc:
            return f"case-parity probe raised on {probe!r}: {exc}"
        if refused != guarded_free:
            drift.append(f"case rule drift on {probe!r}: "
                         f"guard_free={guarded_free} gate_refused={refused}")
    if drift:
        return f"gate accepts benches in guard-free surfaces: {drift}"
    return None


def _check_free_zone_symlink_closure() -> str | None:
    """E-036: a link out of a free zone must not release what it points at.

    norm_path is lexical by design (E-006), so `scratch/link -> ../src`
    used to be freed as a scratch path while every write through the link
    landed in the protected tree, and a `scripts/link -> scratch` bench ran
    a freely fabricable script. The guard now re-checks the
    filesystem-resolved spelling (utils._resolved_project_rel — fail-closed
    when resolution fails or escapes the root) and the gate resolves the
    bench target against its cwd. This check pins the wiring on every host
    (seam present, both consumers consult it, regression tests shipped) and
    adds a live probe where the platform can build a link (symlink, or a
    privilege-less Windows junction).
    """
    problems = []
    utils_src = _read("hooks/engine/modules/utils.py")
    if "def _resolved_project_rel" not in utils_src:
        problems.append("utils.py lost the symlink-resolution seam")
    if "resolved = _resolved_project_rel(p)" not in utils_src:
        problems.append("is_free no longer consults the resolved spelling")
    gate_src = _read("skills/bmad-research-experiment/scripts/run_experiment.py")
    if "os.path.realpath(norm)" not in gate_src:
        problems.append("bench_in_free_zone no longer resolves its target")
    for rel, names in {
        "hooks/engine/tests/test_utils.py": (
            "test_free_zone_symlink_escape_is_not_free",
            "test_free_zone_symlink_closure_wiring",
        ),
        "skills/bmad-research-experiment/scripts/tests/test_run_experiment.py": (
            "test_bench_free_zone_symlink_escape",
            "test_bench_free_zone_symlink_closure_wiring",
        ),
    }.items():
        src = _read(rel)
        missing = [n for n in names if n not in src]
        if missing:
            problems.append(f"missing E-036 tests in {rel}: {missing}")
    # Live probe (best-effort): build a link INSIDE the free zone and demand
    # the guard refuse what it points at. docs/ is the target because it is
    # protected even in this dogfood repo (plugin trees are free here, so a
    # scratch -> hooks link would resolve free and prove nothing).
    import os as _os  # noqa: PLC0415
    import sys as _sys  # noqa: PLC0415
    link = ROOT / "scratch" / "_e036_parity_link"
    target = ROOT / "docs"
    built = False
    try:
        try:
            if link.is_symlink():
                link.unlink()
            elif link.exists():
                # a stale junction dir — best effort
                link.rmdir()
            link.symlink_to(target, target_is_directory=True)
            built = True
        except (OSError, NotImplementedError):
            if _os.name == "nt":
                import subprocess as _sp  # noqa: PLC0415
                try:
                    proc = _sp.run(["cmd", "/c", "mklink", "/J", str(link),
                                    str(target)], capture_output=True)
                    built = proc.returncode == 0 and link.exists()
                except OSError:
                    built = False
        if not built:
            return "; ".join(problems) if problems else None  # host cannot
            # build links — the wiring pins above still falsify the closure
        _sys.path.insert(0, str(ROOT / "hooks" / "engine"))
        _saved = _os.environ.get("CLAUDE_PROJECT_DIR")
        _os.environ["CLAUDE_PROJECT_DIR"] = str(ROOT)
        try:
            from modules.utils import is_free  # noqa: PLC0415
            if is_free("scratch/_e036_parity_link/probe.py") is not False:
                problems.append("is_free still releases a path through a "
                                "free-zone link into a protected tree")
        except Exception as exc:
            problems.append(f"live symlink probe unreadable: {exc}")
        finally:
            if _saved is None:
                _os.environ.pop("CLAUDE_PROJECT_DIR", None)
            else:
                _os.environ["CLAUDE_PROJECT_DIR"] = _saved
    finally:
        try:
            if link.is_symlink():
                link.unlink()
            elif built and link.exists():
                link.rmdir()  # junction: remove the reparse point itself
        except OSError:
            pass  # cleanup is best-effort; a leftover link is re-used next run
    return "; ".join(problems) if problems else None


def _check_remedies_and_session_guidance_pinned() -> str | None:
    """E-037: dead remedies, the stale-root trap, and the citation rule.

    A real consuming-project session (2026-10-12) showed three defects at
    once: the guard's deny text printed `run_experiment.py --record …` and
    `python3 skills/bmad-research-experiment/scripts/run_experiment.py
    --amend-plan …` (neither path exists in the target project — E-029's
    class on shapes its two-literal pin could not see); the resumed session
    opened by running a plugin path cached from an earlier transcript
    (`…/metodoloji/1.2.5/…/orient.py` — gone after the plugin update); and
    a review layer cited lines in the 1500s of a 230-line file. The check
    pins all three closures: every EMITTED engine string that names the
    gate script is built through the plugin-cmd helpers (AST scan,
    docstrings and helper arguments excluded), the session-start context
    carries the stale-root warning, and the review evidence rule stands on
    all three surfaces that ship the review flow.
    """
    problems = []
    # 1. Emitted remedies (AST — the same scan test_guard pins). The module
    # list is DERIVED (E-038): the E-037 tuple was hand-maintained and never
    # contained plan.py/config.py, so the narrow-surface class this check
    # exists to close could regrow behind a whitelist.
    _HELPERS = {"_plugin_script_cmd", "plugin_script_cmd"}
    try:
        _module_paths = sorted(
            (ROOT / "hooks" / "engine" / "modules").glob("*.py"))
    except OSError as exc:
        return f"engine modules unreadable for the remedy scan: {exc}"
    for path in _module_paths:
        name = path.name
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError) as exc:
            problems.append(f"{name}: unreadable for the remedy scan: {exc}")
            continue
        doc_lines = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                body = node.body
                if (body and isinstance(body[0], ast.Expr)
                        and isinstance(body[0].value, ast.Constant)
                        and isinstance(body[0].value.value, str)):
                    doc_lines.update(range(body[0].lineno,
                                           body[0].end_lineno + 1))
        allowed = set()
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and getattr(node.func, "id", "") in _HELPERS):
                for sub in ast.walk(node):
                    allowed.add(id(sub))
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
                for sub in ast.walk(node):
                    allowed.add(id(sub))

        def scan(node):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if ("run_experiment.py" in node.value
                        and node.lineno not in doc_lines
                        and id(node) not in allowed):
                    problems.append(f"{name}:{node.lineno}: bare "
                                    f"run_experiment.py in an emitted string")
                return
            if isinstance(node, ast.JoinedStr):
                rooted = any(
                    isinstance(part, ast.FormattedValue)
                    and isinstance(part.value, ast.Call)
                    and getattr(part.value.func, "id", "") in _HELPERS
                    for part in node.values)
                if not rooted and any(
                        isinstance(part, ast.Constant)
                        and "run_experiment.py" in str(part.value)
                        for part in node.values):
                    problems.append(f"{name}:{node.lineno}: run_experiment.py "
                                    f"not routed through the plugin-cmd helper")
                return
            for child in ast.iter_child_nodes(node):
                scan(child)

        scan(tree)
    # 2. Session-start context: the stale-root warning.
    bootstrap = _read("hooks/scripts/bootstrap.sh")
    # BOTH session-start branches must carry it: the python-built context (the
    # branch that actually runs when python is healthy — where the real stale
    # 1.2.5 path was spent) and the printf fallback. A warning shipped only in
    # the fallback is the same dead remedy E-037 closed on the engine side.
    if bootstrap.count("goes stale on every plugin update") < 2:
        problems.append("bootstrap.sh's session-start context lost the stale-root warning (E-037)")
    # 3. Review evidence rule on every surface that ships the review flow.
    for rel in ("docs/bmad/development-methodology.md",
                "skills/bmad-code-review/SKILL.md",
                "skills/gds-code-review/SKILL.md"):
        if "VERIFIED by reading that file" not in _read(rel):
            problems.append(f"{rel} lost the verified-citation rule (E-037)")
    # 4. The version-literal classifier rule and its falsifiers ship.
    utils_src = _read("hooks/engine/modules/utils.py")
    if 'v?\\d+(?:\\.\\d+)+' not in utils_src:
        problems.append("utils.py lost the version-literal classifier rule")
    for rel, names in {
        "hooks/engine/tests/test_utils.py": (
            "test_version_literal_is_not_a_code_target",),
        "hooks/engine/tests/test_bash_targets.py": (
            "test_inline_eval_ignores_a_quoted_version_literal",
            "test_inline_eval_still_finds_real_inline_writes"),
        "hooks/engine/tests/test_guard.py": (
            "test_emitted_remedies_are_plugin_rooted",),
    }.items():
        src = _read(rel)
        missing = [n for n in names if n not in src]
        if missing:
            problems.append(f"missing E-037 tests in {rel}: {missing}")
    return "; ".join(problems) if problems else None


_GATE_ROOT_TOKENS = ("{metodoloji-root}", "{skill-root}", "$SYNCED",
                      "METODOLOJI_SYNCED")

# A template reference resolves only when rooted at the plugin. A consuming
# project ships the copies under docs/**/_template_*.md (skeleton install), but
# no root-level templates/ dir — so a bare `templates/_template_X.md` dies
# exactly where the bridge steps are read. `{metodoloji-root}/templates/…`
# resolves; the lookbehind skips an already-rooted occurrence.
_TEMPLATE_REF_RE = re.compile(r"(?<![\w}/])templates/_template_[\w.]+")

# The surfaces a CONSUMING project reads or runs (E-038). Plugin-repo-only
# tooling (scripts/, tests, the bench itself) is deliberately excluded: those
# run where skills/ exists at the root, and historical records
# (docs/experiments/E-*.md) are provenance, not guidance.
_SHIPPED_SURFACE_GLOBS = (
    "GUIDE.md", "KILAVUZ.md", "docs/CLAUDE.md", "docs/bmad/*.md",
    "docs/experiments/_template.md", "commands/*.md", "custom/*.toml",
    "templates/*.md", "skills/*/SKILL.md",
    "skills/bmad-research-experiment/plan.md",
    "skills/bmad-research-experiment/experiment-log.md",
    "skills/bmad-research-experiment/scripts/run_experiment.py",
    "hooks/scripts/*.sh",
)


def _plugin_script_basenames() -> frozenset[str]:
    """Every script the plugin ships for a consuming session (E-039, derived).

    EVERY `scripts/` tree — the top-level tooling (create-qr-record,
    sync-story-qr, sync-hooks-json, the check-*.sh gates), the bmad/ CLIs
    (blackboard, workflow, orient, skeleton, …), the skill-side CLIs and the
    hook dispatch scripts. Derived from the filesystem, so a newly shipped
    script joins the rule the day it lands. Deriving from only
    `bmad/scripts/*.py` + `skills/*/scripts/*.py` was itself the E-037
    narrow-surface class one level up: the top-level `scripts/` tree the
    guard's own remedies name, and every `.sh` gate, escaped the scan.
    """
    names: set[str] = set()
    for pat in ("scripts/*", "bmad/scripts/*", "skills/*/scripts/*",
                "hooks/scripts/*"):
        for path in ROOT.glob(pat):
            if path.is_file() and path.suffix in (".py", ".sh"):
                names.add(path.name)
    return frozenset(names)


_PLUGIN_SCRIPT_BASENAMES = _plugin_script_basenames()


def _shipped_gate_invocation_broken(line: str) -> str | None:
    """Why this line teaches a plugin-script invocation that dies where it ships."""
    for name in sorted(_PLUGIN_SCRIPT_BASENAMES):
        # (a) `<name> --flag`: invocation-shaped mention.
        for m in re.finditer(re.escape(name) + r"\s+--", line):
            if not any(r in line[:m.start()] for r in _GATE_ROOT_TOKENS):
                return (f"{name} invoked without a resolving root (no "
                        "{metodoloji-root}/{skill-root}/$SYNCED on the line)")
        # (b) `python3 [<path>/][scripts/]<name>`: a runnable command line.
        # The root token, when present, sits INSIDE the match (between
        # `python3` and the name) — so the prefix check runs up to the name.
        m2 = re.search(r"python3\s+(?:\S+/)?(?:scripts/)?"
                       + re.escape(name) + r"\b", line)
        if m2:
            seg = line[:m2.end() - len(name)]
            if not any(r in seg for r in _GATE_ROOT_TOKENS):
                return f"unrooted python3 invocation of {name}"
    # (c) `templates/_template_X.md`: a template path resolves only at the
    # plugin root; the lookbehind already skipped a rooted occurrence.
    for m in _TEMPLATE_REF_RE.finditer(line):
        if not any(line[:m.start()].endswith(r + "/")
                   for r in _GATE_ROOT_TOKENS):
            return f"unrooted template path: {m.group(0)}"
    if "guard-code.sh" in line:
        return "names guard-code.sh (a script that does not exist)"
    return None


def _check_gate_mentions_resolve_where_shipped() -> str | None:
    """E-038: every shipped invocation of the gate script resolves where it ships.

    A real consuming-project session (2026-10-12) pasted the engine's own
    remedy `python3 skills/bmad-research-experiment/scripts/run_experiment.py
    --amend-plan …` — a path that exists only inside the plugin checkout —
    and was denied 19 times on one target. E-037 rooted the EMITTED strings;
    E-038 rooted the gate script's written half; E-039 widened the same rule
    to EVERY script the plugin ships (bmad/ CLIs + skill CLIs, derived from
    the filesystem): 80 further dead invocations (`python3 bmad/scripts/
    blackboard.py …`, `blackboard.py --help`) lived in GUIDE/KILAVUZ alone.
    The surfaces are the ones a consuming project reads (slash commands,
    workflow tomls, skill files, GUIDE/KILAVUZ, the manifesto, the gate's
    own usage docstring, the session-start shell). A plugin-script invocation
    on a line that carries no resolving root ({metodoloji-root}, {skill-root},
    $SYNCED) is a dead instruction there. It also pins that no shipped
    surface names the phantom `guard-code.sh` (18 references before E-038,
    zero files in this tree's whole history: a script that never existed).
    """
    problems = []
    for pat in _SHIPPED_SURFACE_GLOBS:
        for path in sorted(ROOT.glob(pat)):
            rel = path.relative_to(ROOT).as_posix()
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                problems.append(f"{rel}: unreadable for the E-038 scan: {exc}")
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                why = _shipped_gate_invocation_broken(line)
                if why:
                    problems.append(f"{rel}:{lineno}: {why}")
    if not problems:
        return None
    head = "; ".join(problems[:8])
    return (head + f"; … +{len(problems) - 8} more" if len(problems) > 8
            else head)


def _check_techdebt_audit_covers_the_tree() -> str | None:
    """E-015: the debt audit sees the code it governs, and this tree is clean.

    §5 claimed to catch orphan `TODO: [TD-XXX]` comments but scanned only
    scratch/ and custom/, while the debt standard puts the marker in the code
    (hooks/, scripts/, bmad/, skills/) — a hidden debt marker, which the
    manifesto forbids. §6 adds the reference half: every bare `TD-NNN` token
    must equal an inventory id — an id written without its zero padding is a
    reference to debt the inventory does not contain. The probe runs the real
    audit (it must be HEALTHY here) and pins that the widened scope stays
    falsifiable in scripts/tests/test_check_techdebt.py — a fix without a
    failing test is not a fix.
    """
    script = ROOT / "scripts" / "check-techdebt.sh"
    if not script.is_file():
        return "scripts/check-techdebt.sh missing"
    text = script.read_text(encoding="utf-8")
    problems = []
    if '"$PLUGIN_ROOT/scratch/" "$PLUGIN_ROOT/custom/"' in text:
        problems.append("§5 still scans only scratch/ + custom/")
    for root in ("hooks", "scripts", "bmad", "skills", "custom", "scratch"):
        if f"$PLUGIN_ROOT/{root}" not in text:
            problems.append(f"§5 does not scan {root}/")
    if "not in the inventory" not in text:
        problems.append("§6 (malformed TD reference) is missing")
    tests = ROOT / "scripts" / "tests" / "test_check_techdebt.py"
    if not tests.is_file():
        problems.append("test_check_techdebt.py missing — the scope is not falsifiable")
    else:
        src = tests.read_text(encoding="utf-8")
        for name in ("test_orphan_todo_in_code_is_caught",
                     "test_malformed_td_reference_is_caught",
                     "test_recorded_todo_passes",
                     "test_inventory_example_does_not_false_positive"):
            if name not in src:
                problems.append(f"test_check_techdebt.py: no {name}")
    proc = subprocess.run(["sh", str(script)], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=60,
                          stdin=subprocess.DEVNULL)
    if proc.returncode != 0 or "STATUS: HEALTHY" not in (proc.stdout or ""):
        tail = (proc.stdout or "").strip().splitlines()[-4:]
        problems.append(f"debt audit not healthy: exit {proc.returncode}: {' | '.join(tail)}")
    return "; ".join(problems) if problems else None


def _check_record_decode_refusals() -> str | None:
    """E-016: a non-UTF-8 record is an honest refusal on every record tool.

    E-009 closed the decode seam on the workflow/config/marker readers and
    explicitly left "the gate (run_experiment.py) and the check scripts"
    unprobed. The seam was real: the gate (--run/--verify/--validate/
    --amend-plan), both record generators and the S<->QR sync caught only
    OSError, and UnicodeDecodeError is a ValueError, so a binary external file
    crashed each with a traceback. The probe drives every tool against a
    binary file and demands a named refusal (non-zero exit, no traceback).
    """
    import tempfile  # noqa: PLC0415

    binary = b"\xff\xfe\x00 binary \x80 junk"
    problems: list[str] = []
    with tempfile.TemporaryDirectory() as td:
        bad = pathlib.Path(td) / "bin.md"
        bad.write_bytes(binary)
        stories = pathlib.Path(td) / "docs" / "development" / "stories"
        quality = pathlib.Path(td) / "docs" / "quality"
        stories.mkdir(parents=True)
        quality.mkdir(parents=True)
        (quality / "QR-001.md").write_bytes(binary)
        (stories / "S-001.md").write_bytes(binary)
        cases = (
            (["skills/bmad-research-experiment/scripts/run_experiment.py",
              "--record", str(bad), "--run", "python -c pass"],
             "cannot read record"),
            (["scripts/create-qr-record.py", "--story", str(bad),
              "--project-root", td], "not valid UTF-8"),
            (["scripts/create-methodology-record.py", "--story", str(bad),
              "--project-root", td], "not valid UTF-8"),
            (["scripts/sync-story-qr.py", "--check", "--project-root", td],
             "UNREADABLE"),
        )
        for argv, needle in cases:
            proc = subprocess.run([sys.executable, *argv], cwd=ROOT,
                                  capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=60,
                                  stdin=subprocess.DEVNULL)
            combined = (proc.stdout or "") + (proc.stderr or "")
            if "Traceback" in combined:
                problems.append(f"{argv[0]} crashed: {combined.strip()[-140:]}")
            elif proc.returncode == 0:
                problems.append(f"{argv[0]} accepted a non-UTF-8 file (exit 0)")
            elif needle not in combined:
                problems.append(f"{argv[0]} refusal does not name the cause")
    groups = {
        "skills/bmad-research-experiment/scripts/tests/test_run_experiment.py": (
            "test_verify_binary_record_refuses_not_crash",
            "test_run_on_binary_record_refuses_not_crash",
            "test_validate_doc_binary_file_refuses_not_crash",
            "test_record_scope_binary_file_is_empty_not_crash",
            "test_amend_plan_binary_record_refuses_not_crash",
        ),
        "scripts/tests/test_record_generators.py": (
            "test_qr_generator_refuses_a_non_utf8_story",
            "test_s_generator_refuses_a_non_utf8_story",
            "test_sync_story_qr_reports_a_non_utf8_record_and_check_fails",
        ),
    }
    for rel, names in groups.items():
        src = _read(rel)
        missing = [n for n in names if n not in src]
        if missing:
            problems.append(f"{rel}: missing E-016 tests {missing}")
    return "; ".join(problems) if problems else None


def _check_engine_decode_refusals() -> str | None:
    """E-017: a non-UTF-8 file never kills the engine or the check scripts.

    The E-009 deferment named "the gate and the check scripts"; the gate was
    E-016, and this closes the rest. Three seams were proven crashing:
    `config.py` — the gate-MODE decision path, where a crash leaves the hook
    with NO decision (a runner may read that as an allow, so it must stay
    fail-CLOSED at `code_guard = hard`); `_replay_events` — a binary event log
    raised while being ITERATED, taking `chain_health`/`read_board` with it;
    and `check-triggers.py`, whose whole job is to report.
    """
    import tempfile  # noqa: PLC0415

    problems: list[str] = []
    binary = b"\xff\xfe\x00 binary \x80 junk"

    # 1. The gate-mode decision path must survive a corrupt config and stay fail-closed.
    sys.path.insert(0, str(ROOT / "hooks" / "engine"))
    try:
        from modules import config as mod  # noqa: PLC0415

        saved_path = mod._HOOKS_CFG
        saved_cache = (mod._HOOKS_CACHE, mod._HOOKS_CACHE_KEY)
        try:
            with tempfile.TemporaryDirectory() as td:
                bad = pathlib.Path(td) / "config.toml"
                bad.write_bytes(binary)
                mod._HOOKS_CFG = bad
                mod._HOOKS_CACHE, mod._HOOKS_CACHE_KEY = None, None
                try:
                    if mod.hook_gate_mode("code_guard") != "hard":
                        problems.append("corrupt config no longer fails closed "
                                        "(code_guard != hard)")
                    if mod.hook_gate_mode("quality_gate") != "soft":
                        problems.append("corrupt config does not fall back to "
                                        "the quality default")
                    if mod._validate_hooks_config() != (True, ""):
                        problems.append("_validate_hooks_config raised or "
                                        "misreported on a non-UTF-8 config")
                except Exception as exc:
                    problems.append(f"gate-mode path raised on a corrupt config: "
                                    f"{type(exc).__name__}: {exc}")
        finally:
            mod._HOOKS_CFG = saved_path
            mod._HOOKS_CACHE, mod._HOOKS_CACHE_KEY = saved_cache
    except Exception as exc:
        problems.append(f"config module unreadable: {type(exc).__name__}: {exc}")

    # 2. The board's diagnostics path must fold a corrupt log to an empty board.
    try:
        from modules import blackboard as _bb  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as td:
            paths = _bb.board_paths(td)
            for key in ("snapshot", "events"):
                pathlib.Path(paths[key]).parent.mkdir(parents=True, exist_ok=True)
            pathlib.Path(paths["snapshot"]).write_bytes(binary)
            pathlib.Path(paths["events"]).write_bytes(binary)
            try:
                if _bb.read_board(td)["keys"]:
                    problems.append("a corrupt board did not fold to an empty board")
                if not _bb.chain_health(td).get("ok"):
                    problems.append("chain_health fails on a corrupt board")
            except Exception as exc:
                problems.append(f"board diagnostics raised: {type(exc).__name__}: {exc}")
    except Exception as exc:
        problems.append(f"blackboard module unreadable: {type(exc).__name__}: {exc}")

    # 3. The trigger audit must name an unreadable skill file and exit 1.
    with tempfile.TemporaryDirectory() as td:
        skill = pathlib.Path(td) / "skills" / "binary-skill"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_bytes(binary)
        proc = subprocess.run([sys.executable,
                               str(ROOT / "scripts" / "check-triggers.py"),
                               "--project-root", td],
                              cwd=ROOT, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=60,
                              stdin=subprocess.DEVNULL)
        combined = (proc.stdout or "") + (proc.stderr or "")
        if "Traceback" in combined:
            problems.append(f"check-triggers crashed: {combined.strip()[-140:]}")
        elif proc.returncode != 1:
            problems.append(f"check-triggers exit {proc.returncode}, want 1")
        elif "UNREADABLE" not in combined:
            problems.append("check-triggers does not name the unreadable file")

    groups = {
        "hooks/engine/tests/test_config.py": (
            "test_binary_hooks_config_stays_fail_closed",
        ),
        "hooks/engine/tests/test_blackboard.py": (
            "test_undecodable_snapshot_and_event_log_fold_to_empty_board",
        ),
        "scripts/tests/test_check_triggers.py": (
            "test_binary_skill_file_is_reported_not_crashed",
            "test_clean_skills_still_pass",
        ),
    }
    for rel, names in groups.items():
        src = _read(rel)
        missing = [n for n in names if n not in src]
        if missing:
            problems.append(f"{rel}: missing E-017 tests {missing}")
    return "; ".join(problems) if problems else None


def _check_bridge_producer_verify_audit() -> str | None:
    """E-018: the producer-VERIFY audit must read every producer BRIDGE surface.

    GUIDE.md promises "Producer BRIDGEs carry a VERIFY step ... (automatic
    anti-skip check; audit #2c)" and names 17 producers — three of which keep
    their BRIDGE inside ``[agent].principles``. Before E-018 #2c read only
    ``workflow.activation_steps_append`` for 13 of them: stripping the VERIFY
    marker from an agent-principles producer (``bmad-agent-dev``) left the
    audit green, and ``wds-5-agentic-development`` was never read at all. This
    check pins #2c's producer set *and* per-surface key to GUIDE's documented
    set, then re-evaluates the verify-marker invariant on each producer TOML —
    so a surface cannot silently drop out of the audit (the "guard is only as
    wide as this tuple" class E-002/E-017 named).
    """
    problems: list[str] = []
    guide = _read("GUIDE.md")
    m = re.search(r"\*\*Producer \((\d+),[^)]*\):\*\*(.+?)\.\s*\n",
                  guide, re.DOTALL)
    if not m:
        return "GUIDE.md no longer states a 'Producer (N, ...)' line"
    documented = set(re.findall(r"`([^`]+)`", m.group(2)))
    if len(documented) != int(m.group(1)):
        problems.append(f"GUIDE.md producer count {m.group(1)} != {len(documented)} names")
    script = (ROOT / "scripts" / "check-plugin.sh").read_text(
        encoding="utf-8", errors="replace")
    blk = re.search(r"PRODUCER_KEY = \{(.*?)\}", script, re.DOTALL)
    if not blk:
        return "check-plugin.sh §2c has no PRODUCER_KEY map"
    audited = dict(re.findall(r'"([^"]+)"\s*:\s*"([^"]+)"', blk.group(1)))
    if not audited:
        return "check-plugin.sh §2c PRODUCER_KEY is empty"
    if set(audited) != documented:
        missing = sorted(documented - set(audited))
        extra = sorted(set(audited) - documented)
        problems.append(f"§2c producer set != GUIDE.md (missing {missing}, extra {extra})")
    agent_surfaces = {"bmad-agent-dev", "gds-agent-game-dev", "gds-agent-game-solo-dev"}
    for name, key in audited.items():
        want = "agent.principles" if name in agent_surfaces else "workflow.activation_steps_append"
        if key != want:
            problems.append(f"§2c reads {name} from {key}, want {want}")
        path = ROOT / "custom" / f"{name}.toml"
        if not path.is_file():
            problems.append(f"producer TOML missing: custom/{name}.toml")
            continue
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        sec, field = key.split(".", 1)
        steps = data.get(sec, {}).get(field, []) or []
        if not any(mk in s for s in steps
                   for mk in ("DOGRULAMA", "VERIFICATION", "VERIFY")):
            problems.append(f"{name}: no verify marker in {key}")
    return "; ".join(problems) if problems else None


def _check_bridge_verify_pattern_parity() -> str | None:
    """E-030: §2c must demand the two-part verify pattern §4 already demands.

    ``check-custom.sh`` §4 requires BOTH a verify marker and the "error and
    recreate" phrase on its gate-enforcing set; ``check-plugin.sh`` §2c required
    only the marker on the 17 producers — the same class audited by two surfaces
    at two different strengths. A producer could therefore drop the recreate
    instruction (the half that forces the LLM to re-check the created file via
    the terminal) and §2c stayed green. This pins §2c's source to the two halves
    with §4's exact marker vocabulary, keeps §4 from being weakened in the other
    direction, keeps §4's set inside §2c's, and re-evaluates both halves on every
    producer's own BRIDGE TOML.
    """
    problems: list[str] = []
    plugin = _read("scripts/check-plugin.sh")
    custom = _read("scripts/check-custom.sh")
    blk = re.search(r"PRODUCER_KEY = \{(.*?)\}", plugin, re.DOTALL)
    if not blk:
        return "check-plugin.sh §2c has no PRODUCER_KEY map"
    audited = dict(re.findall(r'"([^"]+)"\s*:\s*"([^"]+)"', blk.group(1)))
    if not audited:
        return "check-plugin.sh §2c PRODUCER_KEY is empty"
    # §2c must run the same two-part gate as §4 …
    if "has_error" not in plugin:
        problems.append("check-plugin.sh §2c dropped the 'error and recreate' half")
    if '("hata ver", "error and recreate")' not in plugin:
        problems.append("check-plugin.sh §2c marker vocabulary drifted from §4")
    # … and §4 must not have been weakened in the other direction.
    if "has_error" not in custom \
            or '("hata ver", "error and recreate")' not in custom:
        problems.append("check-custom.sh §4 no longer demands both halves")
    # §4's gate-enforcing set must stay inside the producer set §2c audits.
    k = re.search(r"KOPRU = \{(.*?)\}", custom, re.DOTALL)
    if not k:
        problems.append("check-custom.sh §4 has no KOPRU set")
    else:
        kopru = set(re.findall(r'"([^"]+)"', k.group(1)))
        outside = sorted(kopru - set(audited))
        if outside:
            problems.append(f"§4 KOPRU not covered by §2c producers: {outside}")
    # Re-evaluate both halves on each producer's own BRIDGE TOML.
    for name, key in audited.items():
        path = ROOT / "custom" / f"{name}.toml"
        if not path.is_file():
            problems.append(f"producer TOML missing: custom/{name}.toml")
            continue
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        sec, field = key.split(".", 1)
        steps = data.get(sec, {}).get(field, []) or []
        blob = "\n".join(steps)
        low = blob.lower()
        if not any(m in blob for m in ("DOGRULAMA", "VERIFICATION", "VERIFY")):
            problems.append(f"{name}: no verify marker in {key}")
        if not any(m in low for m in ("hata ver", "error and recreate")):
            problems.append(f"{name}: no 'error and recreate' phrase in {key}")
    # The runbooks' check-map must state §2c's real strength and width: E-018
    # widened §2c to 17 producers but the map kept saying "13 skills", so the
    # documented claim drifted from the wiring.
    for rel in ("GUIDE.md", "KILAVUZ.md"):
        map_m = re.search(r"#2c bridge VERIFY \((\d+) skill", _read(rel))
        if not map_m:
            problems.append(f"{rel} check-map no longer states a '#2c bridge VERIFY' count")
        elif int(map_m.group(1)) != len(audited):
            problems.append(f"{rel} check-map says #2c audits {map_m.group(1)} skills, "
                            f"but §2c audits {len(audited)}")
    return "; ".join(problems) if problems else None


def _check_bridge_section_refs_resolve() -> str | None:
    """E-019: a live bridge-citing surface must not point at a missing section.

    ``check-custom.sh`` §7 exists to catch a ``§N.N`` the bridge document does
    not contain, but it read only ``custom/*.toml``. The bridge is also cited
    by the QR-feeder ``SKILL.md`` files and the command docs, and one already
    dangled: both review feeders said "§1.1 and §3.1" while the bridge has no
    §1.1 (their sibling TOMLs say "§1 and §3.1"). This check evaluates the
    invariant directly on every surface that names the bridge — custom
    overrides, ``skills/*/SKILL.md`` and ``commands/*.md`` — and pins §7's
    widened scope so it cannot silently narrow back to ``custom/``.
    """
    problems: list[str] = []
    bridge = _read("docs/bmad/dev-skill-to-methodology-bridge.md")
    sections = set(re.findall(r"^(?:##|###) §([0-9]+(?:\.[0-9]+[a-z]?)?)",
                              bridge, re.MULTILINE))
    ref_re = re.compile(
        r"(?:§([0-9]+(?:\.[0-9]+[a-z]?)?)"
        r"|(?:section|bol[uü]m)\s+([0-9]+(?:\.[0-9]+[a-z]?)?))",
        re.IGNORECASE)
    surfaces: list[pathlib.Path] = [
        p for p in (ROOT / "custom").glob("*.toml") if p.name != "config.toml"]
    surfaces += sorted((ROOT / "skills").glob("*/SKILL.md"))
    surfaces += sorted((ROOT / "commands").glob("*.md"))
    for path in sorted(surfaces):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "dev-skill-to-methodology-bridge" not in text:
            continue
        refs = {m.group(1) or m.group(2) for m in ref_re.finditer(text)}
        unknown = sorted(refs - sections)
        if unknown:
            problems.append(f"{path.relative_to(ROOT).as_posix()}: "
                            f"§N.N not in bridge → {', '.join(unknown)}")
    script = _read("scripts/check-custom.sh")
    if 'os.path.join(PLUGIN, "skills", "*", "SKILL.md")' not in script:
        problems.append("check-custom.sh §7 no longer scans skills/*/SKILL.md")
    return "; ".join(problems) if problems else None


def _check_bridge_feeder_audit_covers_every_feeder() -> str | None:
    """E-020: the QR-feeder audit must cover every feeder BRIDGE surface.

    The 33 BRIDGE surfaces partition cleanly: 17 carry a VERIFY step (producers,
    guarded by §2c — E-018) and 16 do not (feeders, whose BRIDGE says "does not
    produce an independent methodology record"). ``check-plugin.sh`` §2 lists
    the TOML feeders but named only 9 of the 16 — the seven gds feeders
    (``gds-test-*``, ``gds-e2e-scaffold``, ``gds-performance-test``,
    ``gds-playtest-plan``) were never checked for the bridge reference + QR
    target. This derives the feeder set from the feeder phrase (cross-checked
    against the absence of a VERIFY step so the two signals agree) and pins §2's
    list to it, then re-checks the bridge-reference + QR-target invariant.
    """
    problems: list[str] = []
    script = _read("scripts/check-plugin.sh")
    blk = re.search(r"QR_FEEDERS_TOML = \[(.*?)\]", script, re.DOTALL)
    if not blk:
        return "check-plugin.sh §2 has no QR_FEEDERS_TOML list"
    audited = set(re.findall(r'"([^"]+)"', blk.group(1)))
    feeder_phrase = "does not produce an independent methodology record"
    verify_markers = ("DOGRULAMA", "VERIFICATION", "VERIFY")
    tree_feeders: set[str] = set()
    for path in sorted((ROOT / "custom").glob("*.toml")):
        if path.name == "config.toml":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "dev-skill-to-methodology-bridge" not in text:
            continue
        is_feeder = feeder_phrase in text
        has_verify = any(mk in text for mk in verify_markers)
        if is_feeder == has_verify:
            problems.append(f"{path.name}: feeder/producer signals disagree "
                            f"(feeder phrase={is_feeder}, verify={has_verify})")
        if is_feeder:
            tree_feeders.add(path.stem)
    if audited != tree_feeders:
        missing = sorted(tree_feeders - audited)
        extra = sorted(audited - tree_feeders)
        problems.append(f"§2 QR_FEEDERS_TOML != tree feeders (missing {missing}, extra {extra})")
    for name in sorted(audited):
        path = ROOT / "custom" / f"{name}.toml"
        if not path.is_file():
            problems.append(f"feeder TOML missing: custom/{name}.toml")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "dev-skill-to-methodology-bridge" not in text:
            problems.append(f"{name}: no bridge reference")
        if "docs/quality/QR" not in text and "docs/development/QR" not in text:
            problems.append(f"{name}: no QR target")
    return "; ".join(problems) if problems else None


def _check_bridge_record_target_audit_covers_every_producer() -> str | None:
    """E-021: every producer's record path must be audited by check-plugin §2.

    §2's ``BRIDGE_SKILLS`` dict demands the bridge reference *and* the record
    path (``docs/development/stories/``, ``docs/quality/QR``, ...) on each
    entry, but it listed only 7 of the 17 producers — the gds, gds-agent and wds
    producers' record path was never verified. This pins the dict's keys to
    GUIDE.md's documented producer set and re-checks the bridge reference plus
    the record target on every producer TOML.
    """
    problems: list[str] = []
    guide = _read("GUIDE.md")
    m = re.search(r"\*\*Producer \((\d+),[^)]*\):\*\*(.+?)\.\s*\n",
                  guide, re.DOTALL)
    if not m:
        return "GUIDE.md no longer states a 'Producer (N, ...)' line"
    documented = set(re.findall(r"`([^`]+)`", m.group(2)))
    script = _read("scripts/check-plugin.sh")
    blk = re.search(r"BRIDGE_SKILLS = \{(.*?)\}", script, re.DOTALL)
    if not blk:
        return "check-plugin.sh §2 has no BRIDGE_SKILLS dict"
    entries = re.findall(r'"([^"\s]+)"\s*:\s*\(\s*"[^"]+"\s*,\s*"([^"]+)"',
                         blk.group(1))
    audited = dict(entries)
    if set(audited) != documented:
        missing = sorted(documented - set(audited))
        extra = sorted(set(audited) - documented)
        problems.append(f"§2 BRIDGE_SKILLS != GUIDE producers (missing {missing}, extra {extra})")
    for name, target in audited.items():
        path = ROOT / "custom" / f"{name}.toml"
        if not path.is_file():
            problems.append(f"producer TOML missing: custom/{name}.toml")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "dev-skill-to-methodology-bridge" not in text:
            problems.append(f"{name}: no bridge reference")
        if target not in text:
            problems.append(f"{name}: record target {target} absent")
    return "; ".join(problems) if problems else None


def _check_qr_feeder_skillmd_audit_covers_every_surface() -> str | None:
    """E-024: the QR-feeder SKILL.md audit must cover every bridge-citing surface.

    §2 audits the QR feed on the ``skills/*/SKILL.md`` feeders through
    ``QR_FEEDERS_SKILLMD``, a hardcoded three-entry list. It is the last
    bridge-audit list in ``check-plugin.sh`` not pinned to a tree-derived set
    (E-020 and E-021 name this exact follow-up): exactly those three SKILL.md
    files cite the bridge today, but a new bridge-citing ``SKILL.md`` would
    drop out of §2's bridge-reference + QR-target check silently while §2 kept
    reporting HEALTHY. This derives the surface set from the tree (every
    ``skills/*/SKILL.md`` that cites the bridge) and pins §2's list to it, then
    re-checks the bridge reference + QR feed target on each surface.
    """
    problems: list[str] = []
    script = _read("scripts/check-plugin.sh")
    blk = re.search(r"QR_FEEDERS_SKILLMD = \[(.*?)\]", script, re.DOTALL)
    if not blk:
        return "check-plugin.sh §2 has no QR_FEEDERS_SKILLMD list"
    audited = set(re.findall(r'"([^"]+)"', blk.group(1)))
    tree: set[str] = set()
    for path in sorted((ROOT / "skills").glob("*/SKILL.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "dev-skill-to-methodology-bridge" in text:
            tree.add(path.parent.name)
    if audited != tree:
        missing = sorted(tree - audited)
        extra = sorted(audited - tree)
        problems.append("§2 QR_FEEDERS_SKILLMD != bridge-citing SKILL.md "
                        f"surfaces (missing {missing}, extra {extra})")
    for name in sorted(audited):
        path = ROOT / "skills" / name / "SKILL.md"
        if not path.is_file():
            problems.append(f"feeder SKILL.md missing: skills/{name}/SKILL.md")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "dev-skill-to-methodology-bridge" not in text:
            problems.append(f"{name}: no bridge reference in SKILL.md")
        if "docs/quality/QR" not in text and "docs/development/QR" not in text:
            problems.append(f"{name}: no QR feed target in SKILL.md")
    return "; ".join(problems) if problems else None


def _check_bridge_runtime_visibility_audit_covers_every_surface() -> str | None:
    """E-027: §2b must probe every bridge-citing TOML override.

    §2b proves the BRIDGE survives the ``resolve_customization`` merge (it is
    visible at runtime) for a hardcoded pair of lists — ``TOML_SKILLS`` (30) and
    ``AGENT_TOML_SKILLS`` (3). E-024 called ``QR_FEEDERS_SKILLMD`` "the last
    bridge-audit list in ``check-plugin.sh`` not pinned to a tree-derived set";
    reading the tree falsifies that — this pair is another. The 33 names equal
    the tree's bridge-citing ``custom/*.toml`` files today, but nothing pins it,
    so a new bridge-citing override (or a rename) drops out of §2b's
    runtime-visibility probe silently while §2b keeps reporting HEALTHY. This
    derives the surface set from the tree (every ``custom/*.toml`` that cites
    the bridge), pins §2b's coverage to it, keeps the agent/workflow
    classification disjoint, and re-checks the root ``customize.toml`` on each
    surface (the invariant §2b actually evaluates).
    """
    problems: list[str] = []
    script = _read("scripts/check-plugin.sh")
    toml_blk = re.search(r"TOML_SKILLS = \[(.*?)\]", script, re.DOTALL)
    agent_blk = re.search(r"AGENT_TOML_SKILLS = \[(.*?)\]", script, re.DOTALL)
    if not toml_blk or not agent_blk:
        return "check-plugin.sh §2b has no TOML_SKILLS/AGENT_TOML_SKILLS lists"
    toml_skills = set(re.findall(r'"([^"]+)"', toml_blk.group(1)))
    agent_skills = set(re.findall(r'"([^"]+)"', agent_blk.group(1)))
    covered = toml_skills | agent_skills
    both = sorted(toml_skills & agent_skills)
    if both:
        problems.append(f"§2b classifies {both} as both workflow and agent surfaces")
    tree: set[str] = set()
    for path in sorted((ROOT / "custom").glob("*.toml")):
        if path.name == "config.toml":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "dev-skill-to-methodology-bridge" in text:
            tree.add(path.stem)
    if covered != tree:
        missing = sorted(tree - covered)
        extra = sorted(covered - tree)
        problems.append("§2b TOML_SKILLS+AGENT_TOML_SKILLS != bridge-citing "
                        f"custom/*.toml (missing {missing}, extra {extra})")
    for name in sorted(covered):
        root_toml = ROOT / "skills" / name / "customize.toml"
        if not root_toml.is_file():
            problems.append(f"{name}: no root customize.toml — team BRIDGE "
                            "override can never merge")
        tname = ROOT / "custom" / f"{name}.toml"
        if not tname.is_file():
            problems.append(f"{name}: no custom/{name}.toml bridge override")
            continue
        if "dev-skill-to-methodology-bridge" not in tname.read_text(
                encoding="utf-8", errors="replace"):
            problems.append(f"{name}: custom/{name}.toml has no bridge reference")
    return "; ".join(problems) if problems else None


def _check_story_title_grammar_agreement() -> str | None:
    """E-028: the record generators and the engine read the shipped story titles.

    Three title forms ship: the story template `# Story: S-XXX — Title`, a
    native draft `# Story: 1-2-slug` (plus `- **Story key:** 1-2-slug`), and a
    generated methodology record `# Methodology Record: S-NNN`.
    ``create-qr-record.py`` matched ONLY the legacy prose form (`# Story 1.1:
    Title`), so for a template story or a methodology record it wrote an EMPTY
    `| Story |` row — a declaration covering nothing, which left the guard
    denying the very story the gate told the operator to generate a record for.
    This drives the real generator and the engine on the shipped template and on
    each form.
    """
    import importlib.util
    problems: list[str] = []

    def _load(name: str, rel: str):
        spec = importlib.util.spec_from_file_location(name, ROOT / rel)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    try:
        qr = _load("bench_create_qr_record", "scripts/create-qr-record.py")
        sgen = _load("bench_create_methodology_record",
                     "scripts/create-methodology-record.py")
        sys.path.insert(0, str(ROOT / "hooks" / "engine"))
        from modules.utils import extract_story_key_from_content  # noqa: PLC0415
    except Exception as exc:
        return f"cannot load a record parser: {type(exc).__name__}: {exc}"

    template = _read("templates/_template_S.md")
    if not qr.extract_story_metadata(template)['story_key']:
        problems.append("create-qr-record.py resolves no story key from the "
                        "shipped story template")
    if not sgen.extract_story_metadata(template)['title']:
        problems.append("create-methodology-record.py resolves no title from "
                        "the shipped story template")

    # `# Story: <key>` forms the engine also reads must agree exactly.
    agree_forms = {
        "# Story: S-002 — Resolve cyan alias\n": "S-002",
        "# Story: 1-2-resolve-cyan-accent-alias\n": "1-2-resolve-cyan-accent-alias",
        "# Story S-002: Legacy prose\n": "S-002",
    }
    for text, expected in agree_forms.items():
        got = qr.extract_story_metadata(text)['story_key']
        if got != expected:
            problems.append(f"create-qr-record.py key {got!r} != {expected!r}")
        eng = extract_story_key_from_content(text)
        if eng != expected:
            problems.append(f"engine story key {eng!r} != {expected!r}")
    if qr.extract_story_metadata("# Methodology Record: S-002\n")['story_key'] != "S-002":
        problems.append("create-qr-record.py does not read a methodology-record "
                        "heading")
    return "; ".join(problems) if problems else None


def _check_gate_remedy_paths_are_runnable() -> str | None:
    """E-029: every printed record-chain remedy resolves inside the plugin.

    The gate runs in a TARGET project, which does not ship the plugin's
    ``scripts/`` tree, so ``python3 scripts/create-qr-record.py`` (the very
    generator the QR gate tells the operator to run) is not runnable where it is
    printed — the E-028 Known Gap. This drives the real gate on a temp target
    project and demands the remedy point at the plugin's own script file and
    name the story the operator actually has (no literal ``S-XXX``), then scans
    the engine + gate sources so no cwd-relative plugin-script remedy can return.
    """
    import tempfile  # noqa: PLC0415

    problems: list[str] = []
    engine_dir = str(ROOT / "hooks" / "engine")
    if engine_dir not in sys.path:
        sys.path.insert(0, engine_dir)
    from modules import guard as guard_mod  # noqa: PLC0415

    qr_script = ROOT / "scripts" / "create-qr-record.py"
    meth_script = ROOT / "scripts" / "create-methodology-record.py"
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        (root / "docs/development/stories").mkdir(parents=True)
        (root / "docs/quality").mkdir(parents=True)
        (root / "docs/development/IR-001.md").write_text("# IR\n", encoding="utf-8")
        (root / "docs/development/stories/S-001.md").write_text(
            "## Story: S-001\n- **Status:** done\n", encoding="utf-8")
        res = guard_mod._check_gate_records(str(root), "git commit")
        reason = res.get("reason", "")
        if res.get("decision") != "deny" or str(qr_script) not in reason:
            problems.append("QR remedy does not name the plugin's own script: "
                            + reason[-180:])
        elif "docs/development/stories/S-001.md" not in reason:
            problems.append("QR remedy does not name the story file: "
                            + reason[-180:])
        ok, write_reason = guard_mod._validate_methodology_chain(
            "## Story: S-001\n- **Status:** done\n",
            "docs/development/stories/S-001.md", str(root))
        if ok or str(qr_script) not in write_reason \
                or str(meth_script) not in write_reason:
            problems.append("story-write remedy does not name runnable scripts: "
                            + write_reason[-220:])

    for rel in ("hooks/engine/modules/guard.py",
                "skills/bmad-research-experiment/scripts/run_experiment.py"):
        src = _read(rel)
        if "python3 scripts/" in src or "python3 run_experiment.py" in src:
            problems.append(f"{rel} prints a cwd-relative plugin-script remedy")
    return "; ".join(problems) if problems else None


def _check_hypothesis_optout_agrees_across_gates() -> str | None:
    """E-031: the [HYPOTHESIS] opt-out must hold at write time AND commit time.

    ``_validate_story_metadata`` treats ``[HYPOTHESIS]`` as an explicit opt-out
    from the Experiment field requirement, but ``_orphan_story_reason`` read the
    raw text and dropped ``Experiment: —`` \u2014 so a story whose ACs are all
    hypotheses passed the write gate and was denied at COMMIT as an orphan (the
    observed session's S-002 stuck on "the methodology's approved hypothesis
    exception"). This drives both gates on a hypothesis story (demanding
    agreement and no orphan denial) and on a non-hypothesis control (demanding
    the orphan rule still fires), so the opt-out cannot silently regress.
    """
    import tempfile  # noqa: PLC0415

    sys.path.insert(0, str(ROOT / "hooks" / "engine"))
    from modules import guard as guard_mod  # noqa: PLC0415

    problems: list[str] = []
    hyp = ("## Story: S-002\n- **Status:** done\n\n"
           "## Acceptance Criteria\n"
           "- [AC-001] Given X When Y Then Z\n"
           "  - Experiment: \u2014\n"
           "  - Type: user-evaluable\n"
           "  - Measured: false\n"
           "  - Verify: manual\n"
           "  - [HYPOTHESIS]\n\n"
           "## Definition of Done\n"
           "- [DoD-001] Done\n  - Verify: manual\n")
    valid, why = guard_mod._validate_story_metadata(hyp)
    if not valid:
        problems.append(f"write gate rejects a [HYPOTHESIS] AC: {why}")
    if guard_mod._orphan_story_reason(hyp):
        problems.append("commit gate still calls an all-hypothesis story an orphan")
    # The control: without the tag the same AC IS an orphan.
    if not guard_mod._orphan_story_reason(hyp.replace("  - [HYPOTHESIS]\n", "")):
        problems.append("commit gate no longer flags an unlinked, non-hypothesis AC")
    # Drive the real commit check end to end on a hypothesis story.
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        (root / "docs/development/stories").mkdir(parents=True)
        (root / "docs/quality").mkdir(parents=True)
        (root / "docs/development/stories/S-002.md").write_text(hyp, encoding="utf-8")
        res = guard_mod._check_all_stories_quality(str(root), "git commit blocked")
        if "experiment validation failed" in res.get("reason", ""):
            problems.append("commit gate denies a hypothesis story as an orphan: "
                            + res["reason"][-160:])
    return "; ".join(problems) if problems else None


def _check_native_methodology_mirror_both_directions() -> str | None:
    """E-032: a QR must cover the native<->methodology pair in EITHER direction.

    A generated methodology record names its native story via `| Native Story |`
    and the two are ONE work unit. The gate read that link only native -> record,
    so a QR declaring the METHODOLOGY key (`| Story | S-002 |` \u2014 exactly what
    `create-qr-record.py` writes) left the done native story looking unrecorded
    and `git commit` denied it (the observed session's native `1-2-\u2026`). This
    drives the real `_find_done_stories_without_qr` on both declaration forms
    (demanding they cover the pair) and on an unrelated control (demanding it
    still flags), so the missing direction cannot silently return.
    """
    import tempfile  # noqa: PLC0415

    sys.path.insert(0, str(ROOT / "hooks" / "engine"))
    from modules import guard as guard_mod  # noqa: PLC0415

    problems: list[str] = []

    def _fixture(qr_story: str):
        td = tempfile.TemporaryDirectory()
        root = pathlib.Path(td.name)
        (root / "docs/development/stories").mkdir(parents=True)
        (root / "docs/development/native").mkdir(parents=True)
        (root / "docs/quality").mkdir(parents=True)
        (root / "docs/development/native/1-2-resolve-cyan-accent-alias.md").write_text(
            "# Story: 1-2-resolve-cyan-accent-alias\n"
            "- **Story key:** 1-2-resolve-cyan-accent-alias\n- **Status:** done\n",
            encoding="utf-8")
        (root / "docs/development/stories/S-002.md").write_text(
            "# Methodology Record: S-002\n\n| Field | Value |\n|------|-------|\n"
            "| Status | done |\n| Story Title | Resolve cyan alias |\n"
            "| Native Story | docs/development/native/"
            "1-2-resolve-cyan-accent-alias.md |\n", encoding="utf-8")
        (root / "docs/quality/QR-002.md").write_text(
            "# Quality Record: QR-002\n\n| Field | Value |\n|------|-------|\n"
            f"| Story | {qr_story} |\n| Status | APPROVED |\n", encoding="utf-8")
        return td, root

    for decl in ("S-002", "1-2-resolve-cyan-accent-alias"):
        td, root = _fixture(decl)
        try:
            missing = guard_mod._find_done_stories_without_qr(str(root))
            if missing:
                problems.append(f"QR declaring {decl!r} left the pair unrecorded: {missing}")
        finally:
            td.cleanup()
    td, root = _fixture("S-999")
    try:
        if not guard_mod._find_done_stories_without_qr(str(root)):
            problems.append("an unrelated QR covered the native/methodology pair")
    finally:
        td.cleanup()
    return "; ".join(problems) if problems else None


def _check_injected_remedies_are_runnable() -> str | None:
    """E-033: a command the engine PRINTS into the session must exist.

    The SessionStart context told the operator "peek `handoffs --skill
    bmad-quality-record`"; a real session ran exactly that and got
    "(eval):1: command not found: handoffs" — `handoffs` is a SUBCOMMAND of the
    plugin's `blackboard.py`, and the plugin tree is not on PATH. This drives
    the real `session_start` on a temp project with a waiting hand-off and
    demands the nudge name the plugin-rooted entry point (and never a bare
    subcommand), then scans the nudge modules' STRING constants (not comments)
    so the class cannot return on another surface.
    """
    import ast  # noqa: PLC0415
    import tempfile  # noqa: PLC0415

    problems: list[str] = []
    sys.path.insert(0, str(ROOT / "hooks" / "engine"))
    from modules import audit as audit_mod  # noqa: PLC0415
    from modules import blackboard as bb  # noqa: PLC0415

    bb_script = str(ROOT / "bmad" / "scripts" / "blackboard.py")
    with tempfile.TemporaryDirectory() as td:
        (pathlib.Path(td) / "docs/development/stories").mkdir(parents=True)
        bb.post_handoff(td, "bmad-quality-record", "e033-bench", "waiting")
        ctx = audit_mod.session_start({"cwd": td}).get("additionalContext", "")
    if "hand-off waiting" not in ctx:
        problems.append("session_start no longer announces a waiting hand-off")
    elif "`handoffs" in ctx or bb_script + " handoffs" not in ctx:
        problems.append("hand-off nudge does not name the runnable command: "
                        + ctx[-200:])

    bare = re.compile(r"`(?:handoffs|blackboard\.py|run_experiment\.py)\b")
    for rel in ("hooks/engine/modules/audit.py", "hooks/engine/modules/stop.py",
                "hooks/engine/modules/mirror.py",
                "hooks/engine/modules/blackboard.py"):
        try:
            tree = ast.parse(_read(rel))
        except SyntaxError as exc:
            problems.append(f"{rel}: {exc}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and bare.search(node.value):
                problems.append(f"{rel}: a printed remedy names a bare plugin "
                                f"subcommand: {node.value[:60]!r}")
    return "; ".join(problems) if problems else None


def _check_documented_gate_invocations_parse() -> str | None:
    """E-034: an invocation the gate DOCUMENTS must be one argparse accepts.

    The gate's module docstring is how an agent learns the command to run; it
    advertised ``run_experiment.py --verify <rec>``, and running exactly that
    form (on this very record's path, while verifying E-033) died on argparse:
    ``error: unrecognized arguments: docs/experiments/E-033.md``. That is the
    dead-instruction class E-029/E-033 closed for deny remedies and session
    nudges, on the one surface they did not read — the gate's own usage text.
    This extracts every ``run_experiment.py <flags>`` invocation from the
    source, fills the placeholders (``<rec>``/``E-001.md`` -> a temp record,
    ``<cmd>`` -> ``true``) and drives the REAL gate with that argv, demanding
    argparse accept the flags: a semantic refusal (undecided record, missing
    Lineage, no measured value) is fine, a parse error is not.
    """
    import tempfile  # noqa: PLC0415

    problems: list[str] = []
    gate = "skills/bmad-research-experiment/scripts/run_experiment.py"
    usages: list[str] = []
    for raw in _read(gate).splitlines():
        m = re.search(r"run_experiment\.py\s+(\S.*)$", raw)
        if not m:
            continue
        usage = re.sub(r"\[[^\]]*\]", "", m.group(1))  # drop [optional] groups
        usage = re.split(r"\s+\(|\s+\u2014", usage)[0].strip()  # drop prose
        if usage:
            usages.append(usage)
    if len(usages) < 3:
        return (f"{gate} documents {len(usages)} invocation(s) — the usage block "
                "shrank or was reworded away; re-point this check at the new one")
    with tempfile.TemporaryDirectory() as td:
        rec = pathlib.Path(td) / "E-999.md"
        rec.write_text("## Experiment: E-999 \u2014 probe\n", encoding="utf-8")
        for usage in usages:
            argv: list[str] = []
            for tok in shlex.split(usage, posix=True):
                if "cmd" in tok and "<" in tok:
                    argv.append("true")
                elif tok.startswith("<") or tok.endswith(".md"):
                    argv.append(str(rec))
                else:
                    argv.append(tok)
            # --no-blackboard: the temp record's project root would otherwise
            # derive to this repo's board. It changes no flag under test.
            proc = subprocess.run(
                [sys.executable, gate, *argv, "--no-blackboard"],
                cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=120, stdin=subprocess.DEVNULL)
            combined = (proc.stdout or "") + (proc.stderr or "")
            if any(sig in combined for sig in
                   ("unrecognized arguments", "are required", "invalid choice")):
                problems.append(
                    f"documented invocation dies on argparse: "
                    f"`run_experiment.py {usage}` -> {combined.strip()[-140:]}")
    return "; ".join(problems) if problems else None


def _check_experiment_lineage_enforced() -> str | None:
    """E-022: a new experiment must declare what the prior ones carry.

    The E corpus is the methodology's memory, but nothing asked a new record
    what the earlier ones already proved: the gate validated Theory/Hypothesis/
    Metrics/Design/Code Scope and then measured, so a draft that opened none of
    its siblings passed exactly as one that read all of them (no record in the
    tree carried a lineage field at all). This check drives the gate on a
    minimal valid draft with no ``Lineage`` line and demands a refusal naming
    the field, pins both E templates to ship it, and re-resolves every
    ``Lineage`` ref declared under ``docs/experiments/`` so a dangling claim
    cannot sit in the corpus (legacy refs-less records are skipped here: the
    gate is what covers a NEW record).
    """
    import tempfile  # noqa: PLC0415

    problems: list[str] = []
    draft = ("## Experiment: E-999 — probe\n\n"
             "- **Date:** 10.10.2026\n"
             "- **Status:** planned\n"
             "- **Theory:** probe\n"
             '- **Hypothesis:** H-999: "consistency_accuracy >= 0.90"\n'
             "- **Measurement Metrics:** consistency_accuracy >= 0.90\n"
             "- **Experiment Design:** probe\n"
             "- **Sample Size n:** 1\n"
             "- **Code Scope:** none\n")
    with tempfile.TemporaryDirectory() as td:
        rec = pathlib.Path(td) / "E-999.md"
        rec.write_text(draft, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable,
             "skills/bmad-research-experiment/scripts/run_experiment.py",
             "--record", str(rec), "--run", "python -c pass"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120,
            # stdin must be an explicit handle: pytest's capture replaces the
            # std handles, and a spawned child then dies on Windows with
            # WinError 6/50 (invalid/unsupported handle) instead of running.
            stdin=subprocess.DEVNULL)
        combined = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 2 or "Lineage" not in combined:
        problems.append(
            f"gate did not refuse a Lineage-less draft (exit {proc.returncode}): "
            f"{combined.strip()[-140:]}")
    for rel in ("templates/_template_E.md", "docs/experiments/_template.md"):
        if "**Lineage:**" not in _read(rel):
            problems.append(f"{rel} does not ship the 'Lineage' field")
    field_re = re.compile(r"^\s*-\s*\*\*Lineage:\*\*\s*(.*)$", re.MULTILINE)
    exp_dir = ROOT / "docs" / "experiments"
    for path in sorted(exp_dir.glob("E-*.md")):
        m = field_re.search(path.read_text(encoding="utf-8", errors="replace"))
        if not m:
            continue  # legacy record: predates the field; the gate covers new ones
        for ref in sorted(set(re.findall(r"\bE-\d+\b", m.group(1)))):
            if ref == path.stem:
                problems.append(f"{path.name}: 'Lineage' cites itself")
            elif not (exp_dir / f"{ref}.md").is_file():
                problems.append(f"{path.name}: 'Lineage' ref {ref} has no record")
    return "; ".join(problems) if problems else None


def _check_negtest_stage_wiring() -> str | None:
    """E-023: the negative test's declared count, its headers and the docs must agree.

    ``check-plugin.sh --negtest`` is the proof that the audit still catches
    breakage, but until now its own count was unfalsified: ``total_stages = 8``
    drove both the ``[N/total_stages]`` banners and the closing "all N …
    successful", so deleting a stage header left the run reporting success for
    a suite that no longer existed — and §3, the forged-token gate that
    produced three FORGED errors in the audit, had no stage at all, while
    GUIDE/KILAVUZ still advertised "7 stages" for an 8-stage script. This pins
    the declared count to the headers that exist (indices 1..N, at least one
    naming §3) and to both runbooks' advertised count.
    """
    problems: list[str] = []
    script = _read("scripts/check-plugin.sh")
    m = re.search(r"total_stages = (\d+)", script)
    if not m:
        return "check-plugin.sh negtest no longer declares total_stages"
    declared = int(m.group(1))
    headers = [int(i) for i in re.findall(
        r'print\(f"\[(\d+)/\{total_stages\}\]', script)]
    if headers != list(range(1, declared + 1)):
        problems.append(
            f"negtest stages mismatch: total_stages={declared}, headers={headers}")
    titles = re.findall(r'print\(f"\[\d+/\{total_stages\}\] ([^"]+)"\)', script)
    if not any("§3" in t for t in titles):
        problems.append("negtest has no §3 stage — the forged-token gate stays unproven")
    for rel, word in (("GUIDE.md", "stages"), ("KILAVUZ.md", "aşama")):
        claim = re.search(
            rf"check-plugin\.sh --negtest\s+#\s*(\d+)\s+{word}", _read(rel))
        if not claim:
            problems.append(f"{rel}: no 'check-plugin.sh --negtest # N {word}' claim")
        elif int(claim.group(1)) != declared:
            problems.append(f"{rel} advertises {claim.group(1)} negtest {word}, "
                            f"the script declares {declared}")
    # The script's OWN header states the count too — that is where §6g and §3
    # were added without anyone bumping it (and it pointed at a docs/ file that
    # is not in the tree at all).
    hdr = re.search(r"negative tests, (\d+) stages", script)
    if not hdr:
        problems.append("check-plugin.sh header no longer states the negtest stage count")
    elif int(hdr.group(1)) != declared:
        problems.append(f"check-plugin.sh header advertises {hdr.group(1)} negtest "
                        f"stages, total_stages={declared}")
    # Every docs/*.md the script names must be a file that exists (E-019 class:
    # a live surface must not point at a missing record). Placeholders
    # (QR-NNN, {token}) are skipped — they are instructions, not references.
    for ref in sorted(set(re.findall(r"docs/[A-Za-z0-9_./-]+\.md", script))):
        if any(c in ref for c in "{}<$") or "NNN" in ref:
            continue
        if not (ROOT / ref).is_file():
            problems.append(f"check-plugin.sh references a file not in the tree: {ref}")
    return "; ".join(problems) if problems else None


def _negtest_suites() -> list[pathlib.Path]:
    """Every shipped scripts/ file that declares a negtest stage counter.

    Derived from the tree rather than a hardcoded tuple (the E-018..E-024
    lesson): a new ``scripts/*.sh``/``.py`` that ships a ``total_stages``
    negtest counter is covered automatically. The regression test
    ``test_negtest_suites_cover_the_known_scripts`` pins the three suites that
    exist today so one cannot silently drop out of the set.
    """
    suites: list[pathlib.Path] = []
    for path in sorted((ROOT / "scripts").glob("*")):
        if not path.is_file() or path.suffix not in (".sh", ".py"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "--negtest" in text and "total_stages" in text:
            suites.append(path)
    return suites


def _check_all_negtest_counts_are_falsifiable() -> str | None:
    """E-026: every negtest suite declares one count that matches its stages.

    E-023 pinned ``check-plugin.sh``'s negtest count to its headers and to the
    runbooks, but left the sibling suites alone — its Known Gaps named this exact
    follow-up ("unifying the two counters under one bench check").
    ``check-techdebt.sh`` still carried the same unfalsified shape:
    ``total_stages = 4`` drove the banners AND the closing success line, so a
    deleted stage would still print "all 4 successful". And ``check-custom.sh``
    had no counter at all — three hardcoded ``N/3`` literals that could silently
    desync while the runbooks advertise "3 tests". This derives every negtest
    suite from the tree and pins, for each, the declared count to the stage
    indices that actually exist (1..N), requires the closing line to use that
    count, and pins the runbooks' advertised check-custom count.
    """
    problems: list[str] = []
    suites = _negtest_suites()
    if not suites:
        return "no negtest suite with a total_stages counter was found"
    for path in suites:
        rel = path.relative_to(ROOT).as_posix()
        script = path.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"total_stages = (\d+)", script)
        if not m:
            problems.append(f"{rel}: no declared 'total_stages'")
            continue
        declared = int(m.group(1))
        headers = [int(i) for i in re.findall(
            r'print\(f"[^"]*?(\d+)/\{total_stages\}', script)]
        if headers != list(range(1, declared + 1)):
            problems.append(
                f"{rel}: total_stages={declared} but stage headers={headers}")
        if "all {total_stages} negtest stages successful" not in script:
            problems.append(f"{rel}: closing line does not use the declared count")
    # The runbooks advertise the counts a reader trusts.
    for rel, script_name, word in (("GUIDE.md", "check-custom.sh", "tests"),
                                   ("KILAVUZ.md", "check-custom.sh", "test")):
        claim = re.search(
            rf"{re.escape(script_name)} --negtest\s+#\s*(\d+)\s+{word}\b", _read(rel))
        if not claim:
            problems.append(f"{rel}: no '{script_name} --negtest # N {word}' claim")
            continue
        declared = int(re.search(r"total_stages = (\d+)",
                                 _read(f"scripts/{script_name}")).group(1))
        if int(claim.group(1)) != declared:
            problems.append(f"{rel} advertises {claim.group(1)} {script_name} "
                            f"{word}, the script declares {declared}")
    return "; ".join(problems) if problems else None


def _check_cross_machine_marker_is_anchored() -> str | None:
    """E-025: §3 must read Re-Measured-By from the canonical bullet line, not prose.

    §3 reports an approved-but-unverifiable record as CROSS-MACHINE only when the
    operator declared a re-measurement with the plain ``Re-Measured-By:`` marker.
    The old guard was an unanchored ``grep -q 'Re-Measured-By:'``, so a record
    that merely DISCUSSES the marker in prose (E-023 documents the P11 repair)
    was read as carrying one: the extractor returned the sentence text as the
    "declared" record, and §3 printed a bogus filename and a wrong CROSS-MACHINE
    classification. This extracts §3's marker pattern and evaluates it against
    both forms — the canonical bullet must match and yield its E-id, a prose
    mention must not match — so the guard cannot silently un-anchor again.
    """
    problems: list[str] = []
    script = _read("scripts/check-plugin.sh")
    m = re.search(r"RE_MEASURED_MARKER='([^']+)'", script)
    if not m:
        return "check-plugin.sh §3 no longer declares RE_MEASURED_MARKER"
    if "grep -q 'Re-Measured-By:'" in script:
        problems.append("§3 fell back to the unanchored 'Re-Measured-By:' guard")
    if 'grep -oE "$RE_MEASURED_MARKER"' not in script:
        problems.append("§3 no longer extracts the E-id from the marker pattern")
    # Evaluate the anchoring semantics (POSIX ERE -> Python re: [[:space:]] -> \s).
    py = m.group(1).replace("[[:space:]]", r"\s")
    prose = ("  `Re-Measured-By:` marker; these three were never re-measured). "
             "The record has")
    bullet = "- **Re-Measured-By:** E-006 (08.10.2026)"
    if re.search(py, prose, re.MULTILINE):
        problems.append("§3 marker pattern matches a prose mention of Re-Measured-By")
    if not re.search(py, bullet, re.MULTILINE):
        problems.append("§3 marker pattern does not match the canonical bullet line")
    return "; ".join(problems) if problems else None


def _run_pytest() -> str | None:
    """The falsifier: the whole suite must stay green."""
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q"],
                          cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=900,
                          stdin=subprocess.DEVNULL)
    if proc.returncode != 0:
        tail = (proc.stdout or "").strip().splitlines()[-3:]
        return f"pytest exited {proc.returncode}: {' | '.join(tail)}"
    return None


def _run_hooks_json_sync() -> str | None:
    proc = subprocess.run([sys.executable, "scripts/sync-hooks-json.py", "--check"],
                          cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120,
                          stdin=subprocess.DEVNULL)
    if proc.returncode != 0:
        return f"hooks.json drifted from the generator: {(proc.stdout or '').strip()[:120]}"
    return None


def _check_tool_contract_names_the_real_harness_failures() -> str | None:
    """E-041: the shipped tool coaching must name the failure the harness emits.

    The 2026-10-12 Claude Code session ran /metodoloji:bmad-research-experiment
    and burned many turns on `InputValidationError: ... The required parameter
    'file_path'/'command' is missing` — arguments dropped on PARALLEL Read/Bash
    calls — plus one `No such tool available: default.Bash`. The shipped
    recovery cue named only `provided as 'unknown'` (a form that never appeared)
    and never forbade batching, so the agent had to rediscover "shift to single
    tool calls" itself. The session line (bootstrap.sh — the python `tools` var
    AND the shell printf fallback) and the two Tool Contract surfaces must name
    the real signature and the one-call-at-a-time rule.
    """
    problems: list[str] = []
    boot = _read("hooks/scripts/bootstrap.sh")
    if boot.count("required parameter") < 2:
        problems.append("bootstrap.sh: 'required parameter' must appear in both "
                        "the python tools var and the printf fallback")
    if boot.count("never batch") < 2:
        problems.append("bootstrap.sh: the 'never batch' rule must be in both "
                        "session-line copies")
    if "No such tool available" not in boot:
        problems.append("bootstrap.sh: does not name the 'No such tool available' "
                        "(namespaced tool) failure")
    for rel in ("skills/bmad-workflow/SKILL.md",
                "skills/bmad-research-experiment/SKILL.md"):
        t = _read(rel)
        if "required parameter" not in t:
            problems.append(f"{rel}: tool contract does not name the real "
                            "'required parameter' signature")
        if "never batch" not in t:
            problems.append(f"{rel}: tool contract has no one-call-at-a-time rule")
    return "; ".join(problems) if problems else None


_SUBSCRIBE_RE = re.compile(
    r'subscribe --watcher (\S+) --pattern "([^"]+)" --channel (\S+)')
# A runbook's change-radar bullet, anchored by its marker phrase so the check
# reads the radar line and not some unrelated `prd.*` mention elsewhere.
_RADAR_MARKERS = ("change radar", "değişim radarı")
_RADAR_SEGMENT_RE = re.compile(
    r"`([a-z][\w-]+)`\s*(?:watches|→)\s*`([^`]+)`\s*\((session|stop)\)")


def _check_change_radar_contract_matches() -> str | None:
    """E-043: the DOCUMENTED change radar must equal the SHIPPED one.

    The last blackboard commit (cc5a02c) added the §14 change-radar bullet to
    GUIDE/KILAVUZ and, next to it, a handoff-lint that checked only that a
    `subscribe --watcher <self>` line existed. The lint could not see a WRONG
    pattern, so the runbooks and the skills drifted on the very commit that
    documented the radar: the bullet claimed `bmad-spec` watches `architecture.*,
    prd.*` (the skill shipped only `architecture.*`) and "all on the session
    channel" (two skills subscribe on `stop`), and it omitted four subscribing
    skills. This check is the independent half: it DERIVES the radar from the
    SKILL.md files themselves (no table import) and requires each runbook's
    radar bullet to enumerate exactly that table — both directions, so neither a
    missing skill nor a phantom one can pass.
    """
    problems: list[str] = []
    shipped: dict[str, set] = {}
    for md in sorted((ROOT / "skills").glob("*/SKILL.md")):
        try:
            txt = md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for watcher, pat, chan in _SUBSCRIBE_RE.findall(txt):
            shipped.setdefault(watcher, set()).add((pat, chan))
    if not shipped:
        return "no skill ships a subscribe line — the change radar vanished"
    for rel in ("GUIDE.md", "KILAVUZ.md"):
        bullet = next((ln for ln in _read(rel).splitlines()
                       if any(m in ln for m in _RADAR_MARKERS)), None)
        if bullet is None:
            problems.append(f"{rel}: no change-radar bullet found")
            continue
        claimed: dict[str, set] = {}
        for skill, pats, chan in _RADAR_SEGMENT_RE.findall(bullet):
            claimed.setdefault(skill, set()).update(
                (p.strip(), chan) for p in pats.split(",") if p.strip())
        for skill, want in sorted(shipped.items()):
            got = claimed.get(skill)
            if got is None:
                problems.append(f"{rel}: change-radar bullet omits `{skill}`")
            elif got != want:
                problems.append(f"{rel}: `{skill}` radar says {sorted(got)} "
                                f"but the skill ships {sorted(want)}")
        for skill in sorted(set(claimed) - set(shipped)):
            problems.append(f"{rel}: change-radar bullet claims `{skill}` "
                            f"which ships no subscription")
    return "; ".join(problems) if problems else None


_EDGE_RE = re.compile(r"link --a (\S+) --b (\S+) --relation (\S+)")
# The relations the §14 Graph bullet documents for hand-written relay edges.
# `owns` is the engine's automatic canvas edge and `supersedes` is a re-run
# marker; neither is ever hand-written on a skill close-out.
_RELATION_VOCAB = ("informs", "constrains", "validates")
_GRAPH_DOC_MARKERS = ("Graph (relay traceability)", "Graph (relay izlenebilirliği)")


def _shipped_relay_edges() -> dict:
    """Every surface that hands the relay graph an edge, derived from the tree."""
    out: dict[str, list] = {}
    for md in sorted((ROOT / "skills").glob("*/SKILL.md")):
        blob = md.read_text(encoding="utf-8", errors="replace")
        for f in sorted(md.parent.rglob("*.md")):
            if f.name != "SKILL.md":
                blob += "\n" + f.read_text(encoding="utf-8", errors="replace")
        edges = _EDGE_RE.findall(blob)
        if edges:
            out[md.parent.name] = edges
    for toml in sorted((ROOT / "custom").glob("*.toml")):
        edges = _EDGE_RE.findall(toml.read_text(encoding="utf-8", errors="replace"))
        if edges:
            out.setdefault(toml.stem, []).extend(edges)
    return out


def _declared_relay_edges() -> dict:
    """The lint's EXPECTED_LINKS table, loaded from check-handoff.py."""
    import importlib.util  # noqa: PLC0415
    spec = importlib.util.spec_from_file_location(
        "check_handoff_for_bench", ROOT / "scripts" / "check-handoff.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # __name__ != "__main__": the CLI block stays shut
    return mod.EXPECTED_LINKS


def _check_relay_graph_edges_match_declared() -> str | None:
    """E-044: the declared relay graph must equal the graph the tree ships.

    The last blackboard commit shipped the §14 Graph bullet plus an
    `EXPECTED_LINKS` entry per producing skill — but the entry was a
    SOURCE-ONLY marker (`"link --a prd."`), so the lint could not see a wrong
    target or a relation the runbook never documents (`link --a prd.acme --b
    WRONG --relation nonsense` passed as [OK]). This check is the independent
    half, the E-043 pattern applied to the graph: it DERIVES every shipped edge
    from `skills/*/SKILL.md` (plus their steps/) and `custom/*.toml`, then
    requires `EXPECTED_LINKS` to equal it both ways — a surface that ships an
    edge without declaring it, and a declaration with no shipped edge, both
    fail — and it requires every shipped relation to be in the runbooks'
    documented vocabulary so a hand-written `owns` cannot corrupt the graph.
    """
    problems: list[str] = []
    shipped = _shipped_relay_edges()
    if not shipped:
        return ("no surface ships a `link --a … --b … --relation …` edge — "
                "the relay graph vanished")
    declared = _declared_relay_edges()
    for surface, edges in sorted(shipped.items()):
        want = declared.get(surface)
        if want is None:
            problems.append(f"{surface}: ships graph edges {sorted(edges)} but "
                            f"is not declared in EXPECTED_LINKS")
            continue
        for a, b, r in edges:
            if not any(a.startswith(s) and b.startswith(d) and r == rel
                       for s, d, rel in want):
                problems.append(f"{surface}: edge {a} -> {b} ({r}) is not the "
                                f"declared edge {want}")
    for surface in sorted(set(declared) - set(shipped)):
        problems.append(f"EXPECTED_LINKS declares {surface} but the tree ships "
                        f"no edge for it")
    for surface, edges in sorted(shipped.items()):
        for a, b, r in edges:
            if r not in _RELATION_VOCAB:
                problems.append(f"{surface}: relation '{r}' is outside the "
                                f"documented {'|'.join(_RELATION_VOCAB)} vocabulary")
    for rel in ("GUIDE.md", "KILAVUZ.md"):
        bullet = next((ln for ln in _read(rel).splitlines()
                       if any(m in ln for m in _GRAPH_DOC_MARKERS)), None)
        if bullet is None:
            problems.append(f"{rel}: no Graph (relay traceability) bullet found")
        elif not all(v in bullet for v in _RELATION_VOCAB):
            problems.append(f"{rel}: Graph bullet does not name every shipped "
                            f"relation ({_RELATION_VOCAB})")
    return "; ".join(problems) if problems else None


# --- E-045: a spawn must hand its child an explicit stdin --------------------
# The suite was green from a console and red from the gate: `subprocess.run([…])`
# with no stdin inherits the parent's STD_INPUT_HANDLE, and DuplicateHandle on a
# stdio handle the host does not have fails the spawn outright (WinError 6 on
# Windows, raised before the child exists — the child never runs). The repo had
# patched this site by site as it noticed it; this check makes it an invariant
# of every shipped python tree, so the next spawn helper cannot reintroduce it.
SPAWN_ROOTS = ("hooks", "scripts", "bmad", "skills")
SPAWN_FUNCS = frozenset({"run", "Popen", "call", "check_call", "check_output"})


def _spawn_calls_without_stdin(source: str, rel: str) -> list[str]:
    """``rel:line`` for every ``subprocess.<spawn>(…)`` in *source* with no stdin.

    ``stdin=…`` and ``input=…`` both detach the child from the parent's stdio
    handle (``input=`` implies ``stdin=PIPE``), so either satisfies the rule. A
    spawn hidden behind ``**kwargs`` cannot be judged from the call site and is
    reported rather than passed: an unverifiable spawn is the defect itself.
    """
    problems: list[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"{rel}: unparsable ({type(exc).__name__})"]
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not isinstance(fn, ast.Attribute) or fn.attr not in SPAWN_FUNCS:
            continue
        base = fn.value
        if not (isinstance(base, ast.Name) and base.id == "subprocess"):
            continue
        keywords = {kw.arg for kw in node.keywords}
        if None in keywords:
            problems.append(f"{rel}:{node.lineno}: spawn behind **kwargs — "
                            "stdin cannot be verified from the call site")
        elif "stdin" not in keywords and "input" not in keywords:
            problems.append(f"{rel}:{node.lineno}")
    return problems


def _check_every_spawn_hands_stdin() -> str | None:
    """E-045: no python in this tree spawns a child without an explicit stdin.

    The class is the repo's recurring one (E-037): an artifact that depends on a
    resource nobody measured — here a launch that depends on the parent's stdio
    handle. Measured on the pre-fix tree, ten tests failed from a shell with no
    stdin and none from a console; the difference was the handle, not the code
    under test. A spawn that cannot start must not be reported as a failed
    assertion, and a check that only passes on the operator's terminal is not a
    check.
    """
    problems: list[str] = []
    scanned = 0
    for root in SPAWN_ROOTS:
        for path in sorted((ROOT / root).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(ROOT).as_posix()
            source = path.read_text(encoding="utf-8", errors="replace")
            if "subprocess." not in source:
                continue
            scanned += 1
            problems.extend(_spawn_calls_without_stdin(source, rel))
    if scanned == 0:
        return "no python file spawns a subprocess — the scan found nothing to check"
    return "; ".join(problems) if problems else None


BOARD_CANDIDATES = (
    "docs/development/native/sprint-status.yaml",
    "bmad-output/implementation-artifacts/sprint-status.yaml",
    "_bmad-output/implementation-artifacts/sprint-status.yaml",
    ".metodoloji/sprint-status.yaml",
)


def _shipped_board() -> pathlib.Path | None:
    """The canonical-first sprint board this tree ships (None = none ships)."""
    for rel in BOARD_CANDIDATES:
        p = ROOT / rel
        if p.is_file():
            return p
    return None


def _check_board_story_keys_resolve() -> str | None:
    """E-046: every sprint-board story key must name a story that exists.

    Every engine chain gate walks story FILES and looks outward, so a board key
    with no story behind it is invisible to all of them: measured 2026-10-15, a
    board carrying `9-9-ghost-story-that-does-not-exist` drew no issue and no
    mention from `check-methodology.sh` — the only trace was an in-progress count
    one too high, i.e. a nudge to verify records for work that does not exist.
    The check derives the board's keys from the tree, asks the ENGINE to resolve
    them (one rule, never a second copy of it), and then drives the audit script
    against a project whose board carries a phantom key, where it must name it.
    """
    problems: list[str] = []
    board = _shipped_board()
    if board is None:
        return None  # a project with no board has no keys to resolve
    board_text = board.read_text(encoding="utf-8", errors="replace")
    keys = re.findall(r"^[ \t]+(\d+-\d+-[a-z][a-z0-9-]*):", board_text, re.MULTILINE)
    if not keys:
        return (f"{board.relative_to(ROOT).as_posix()}: no story entries — "
                "the board tracks nothing")
    sys.path.insert(0, str(ROOT / "hooks" / "engine"))
    try:
        from modules import guard  # noqa: PLC0415
    except Exception as exc:  # a check that cannot load the rule is a failing check
        return f"cannot load the engine: {type(exc).__name__}: {exc}"

    unresolved = guard.board_story_keys_without_records(str(ROOT))
    if unresolved:
        problems.append(f"board keys no story record declares: {unresolved}")

    # The rule must fire whatever the shipped board happens to hold, so it is
    # driven against a phantom both in-process and through the audit script.
    ghost = "9-9-ghost-story-that-does-not-exist"
    with tempfile.TemporaryDirectory() as td:
        proj = pathlib.Path(td) / "proj"
        (proj / "docs" / "development" / "native").mkdir(parents=True)
        (proj / "docs" / "development" / "native" / "sprint-status.yaml").write_text(
            "# Sprint status — phantom probe\n"
            "# Methodology record: docs/development/SP-001.md\n"
            "development_status:\n"
            "  epic-1: in-progress\n"
            f"  {ghost}: in-progress\n", encoding="utf-8")
        if guard.board_story_keys_without_records(str(proj)) != [ghost]:
            problems.append("the engine does not report a phantom board key in a fixture")
        env = dict(os.environ, OPENHANDS_PROJECT_DIR=proj.as_posix(),
                   METODOLOJI_PLUGIN_ROOT=str(ROOT))
        proc = subprocess.run(["sh", str(ROOT / "scripts" / "check-methodology.sh")],
                              cwd=str(ROOT), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120,
                              env=env, stdin=subprocess.DEVNULL)
        out = (proc.stdout or "") + (proc.stderr or "")
        if ghost not in out:
            problems.append("check-methodology.sh does not name a board key that "
                            "has no story record")
        elif not re.search(r"ISSUE:.*" + re.escape(ghost), out):
            problems.append("the audit mentions the phantom board key without "
                            "raising it as an issue")
    return "; ".join(problems) if problems else None


def _shell_tokens(command: str) -> list[str]:
    """The tokens `for t in $(<command>)` yields, run through a real sh.

    A derivation is part of the artifact, not a private detail of the caller:
    a Windows python writes CRLF into a pipe, so a pipeline that does not strip
    CR hands every token but the last a trailing `\r` and the caller's
    `[ -f "$root/$t" ]` can never match it (measured 2026-10-15: check-plugin
    §6d read a project holding the FIRST probe as "no skeleton copy found"
    while §6d3 reported "no version" in four of five manifests). Python's
    ``splitlines`` would eat the CR and hide exactly this, so the split is on
    "\n" alone.
    """
    body = 'for t in $(%s); do printf "%%s\\n" "$t"; done' % command
    env = dict(os.environ, PY=sys.executable, PLUGIN_ROOT=str(ROOT))
    proc = subprocess.run(["sh", "-c", body], cwd=ROOT, capture_output=True,
                          env=env, timeout=60, stdin=subprocess.DEVNULL)
    out = (proc.stdout or b"").decode("utf-8", errors="replace")
    return out.split("\n")[:-1] if out else []


def _check_skeleton_contract_declarations_agree() -> str | None:
    """E-047: every shipped restatement of the skeleton contract must agree.

    The init contract is declared in four places and only `bmad/scripts/
    skeleton.py` is canonical: the init runbook lists the directories and the
    template table a reader trusts (and promised the runbook and the script
    "can never drift"), the session-start hint probes a literal copy of
    `SKELETON_PROBES`, and `check-plugin.sh` §6d carried a third copy while its
    comment still counted "two template paths". Nothing measured any of it.
    Drift is silent by construction and lands where a user looks: a probe set
    that grows leaves the hint advertising a FULL init on a project whose
    skeleton exists, and leaves §6d red on a healthy skeleton missing the old
    trio. This drives the rule (`--audit-declarations`) through a copy of the
    script on each mutated site, so every copy must stay honest.
    """
    import importlib.util as _importlib_util  # noqa: PLC0415

    script = ROOT / "bmad" / "scripts" / "skeleton.py"
    spec = _importlib_util.spec_from_file_location("_bench_skeleton_contract", script)
    sk = _importlib_util.module_from_spec(spec)
    spec.loader.exec_module(sk)
    problems: list[str] = []

    shipped = sk.declaration_problems(ROOT)
    if shipped:
        problems.append("the shipped tree already drifts: " + "; ".join(shipped))
    if "--audit-declarations" not in _read("scripts/check-plugin.sh"):
        problems.append("check-plugin.sh §6d does not run the declaration audit — "
                        "the rule would be dead code")
    for name in ("test_declarations_agree_on_the_shipped_tree",
                 "test_declaration_drift_is_named_per_site"):
        if name not in _read("bmad/tests/test_skeleton.py"):
            problems.append(f"bmad/tests/test_skeleton.py: no {name} — "
                            "the rule is not falsifiable")

    def _cli(root: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(root / "bmad" / "scripts" / "skeleton.py"), *args],
            cwd=str(root), capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120, stdin=subprocess.DEVNULL)

    ok = _cli(ROOT, "--audit-declarations")
    if ok.returncode != 0:
        problems.append(f"--audit-declarations exits {ok.returncode} on the "
                        f"shipped tree: {(ok.stdout or '').strip()[:200]}")
    probes = _cli(ROOT, "--probe-paths")
    if probes.stdout.split() != list(sk.SKELETON_PROBES):
        problems.append(f"--probe-paths prints {probes.stdout.split()} but the "
                        f"module declares {list(sk.SKELETON_PROBES)}")
    # §6d must READ that list, and the read must survive the shell: a token with
    # a trailing CR never matches `[ -f ]`, so only the last probe would count.
    m = re.search(r'"\$PY" "\$PLUGIN_ROOT/bmad/scripts/skeleton\.py" --probe-paths[^)\n]*',
                  _read("scripts/check-plugin.sh"))
    if not m:
        problems.append("§6d no longer asks skeleton.py for the probe set")
    else:
        tokens = _shell_tokens(m.group(0))
        if tokens != list(sk.SKELETON_PROBES):
            problems.append(f"§6d's probe derivation yields {tokens!r} through a "
                            f"real shell, not {list(sk.SKELETON_PROBES)!r} "
                            "(a CR-suffixed token can never match)")

    runbook = _read("commands/init.md")
    bootstrap = _read("hooks/scripts/bootstrap.sh")
    checker = _read("scripts/check-plugin.sh")
    cases = (
        ("commands/init.md", runbook.replace("   - `scratch/`", ""),
         "a dropped directory"),
        ("commands/init.md", runbook.replace(
            "`{metodoloji-root}/templates/README.md` → `docs/development/README.md`",
            "`{metodoloji-root}/templates/README.md` → `docs/development/README-x.md`"),
         "a moved template destination"),
        ("hooks/scripts/bootstrap.sh", re.sub(
            r"for _f in [^;]+; do",
            "for _f in docs/experiments/_template.md; do", bootstrap),
         "a narrowed probe set"),
        ("scripts/check-plugin.sh", checker.replace(
            "SKELETON_PRESENT=0",
            "SKELETON_PRESENT=0\nfor f in docs/experiments/_template.md; do :; done", 1),
         "a restated probe set"),
        ("scripts/check-plugin.sh", checker.replace(" --audit-declarations", ""),
         "the audit being unhooked"),
    )
    with tempfile.TemporaryDirectory() as td:
        base = pathlib.Path(td)
        for site, mutated, label in cases:
            original = _read(site)
            if mutated == original:
                problems.append(f"falsifier setup broken: {label} in {site} "
                                "could not be injected")
                continue
            fixture = base / f"probe{len(list(base.iterdir()))}"
            for rel in ("bmad/scripts/skeleton.py",) + sk.DECLARATION_SITES:
                dest = fixture / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes((ROOT / rel).read_bytes())
            (fixture / site).write_text(mutated, encoding="utf-8")
            proc = _cli(fixture, "--audit-declarations")
            out = (proc.stdout or "") + (proc.stderr or "")
            if proc.returncode == 0:
                problems.append(f"the declaration audit accepts {label} in {site}")
            elif site not in out:
                problems.append(f"the declaration audit rejects {label} in {site} "
                                f"without naming the site: {out.strip()[:200]}")
    return "; ".join(problems) if problems else None


def _check_version_owned_sites_agree() -> str | None:
    """E-048: every shipped restatement of the version-owned set must agree.

    The set decides three things at once — which files the bump rewrites, which
    the pre-commit hook refuses an unstaged edit in and `git add`s afterwards,
    and which the plugin audit checks for skew — and it was restated in four
    places with nothing measuring them: the hook's `FILES` (plus the count in
    its own comment), §6d3's literal manifest list, and the `MANIFESTS` tuples
    of the two release tests. Measured 2026-10-15, the rule fires on the shipped
    tree before the repair: §6d3 carried a second copy and neither derived nor
    audited the set, and both release tests seeded their scratch repos from a
    hand-written tuple. This drives the real `--audit-sites` CLI over a mutated
    copy of each site, and reads §6d3's derivation through a real shell, so a
    list that only looks right (a CR-suffixed token matches nothing) fails too.
    """
    import importlib.util as _importlib_util  # noqa: PLC0415

    script = ROOT / "scripts" / "bump-version.py"
    spec = _importlib_util.spec_from_file_location("_bench_bump_owned", script)
    bump = _importlib_util.module_from_spec(spec)
    spec.loader.exec_module(bump)
    problems: list[str] = []
    owned = list(bump.owned_paths())

    shipped = bump.site_problems(ROOT)
    if shipped:
        problems.append("the shipped tree already drifts: " + "; ".join(shipped))
    if "--audit-sites" not in _read("scripts/check-plugin.sh"):
        problems.append("check-plugin.sh §6d3 does not run the set audit — the "
                        "rule would be dead code")
    for name in ("test_version_owned_sites_agree_on_the_shipped_tree",
                 "test_version_owned_site_drift_is_named_per_site"):
        if name not in _read("scripts/tests/test_bump_version.py"):
            problems.append(f"scripts/tests/test_bump_version.py: no {name} — "
                            "the rule is not falsifiable")

    def _cli(root: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(root / "scripts" / "bump-version.py"), *args],
            cwd=str(root), capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120, stdin=subprocess.DEVNULL)

    ok = _cli(ROOT, "--audit-sites")
    if ok.returncode != 0:
        problems.append(f"--audit-sites exits {ok.returncode} on the shipped "
                        f"tree: {(ok.stdout or '').strip()[:200]}")
    for flag, expected in (("--manifest-paths", list(bump.MANIFESTS)),
                           ("--owned-paths", owned)):
        out = _cli(ROOT, flag).stdout.split()
        if out != expected:
            problems.append(f"{flag} prints {out} but the module declares {expected}")

    # §6d3 must READ the manifest list, through a shell, without CR pollution.
    m = re.search(r'"\$PY" "\$PLUGIN_ROOT/scripts/bump-version\.py" '
                  r'--manifest-paths[^)\n]*', _read("scripts/check-plugin.sh"))
    if not m:
        problems.append("§6d3 no longer asks bump-version.py for the manifest list")
    else:
        tokens = [t.replace(str(ROOT) + "/", "").replace("\\", "/")
                  for t in _shell_tokens(m.group(0))]
        if tokens != list(bump.MANIFESTS):
            problems.append(f"§6d3's manifest derivation yields {tokens!r} "
                            f"through a real shell, not {list(bump.MANIFESTS)!r}")

    hook = _read(".githooks/pre-commit")
    git_hooks = _read("scripts/tests/test_git_hooks.py")
    pipeline = _read("scripts/tests/test_release_pipeline.py")
    checker = _read("scripts/check-plugin.sh")
    cases = (
        (".githooks/pre-commit",
         hook.replace("\nREADME.md commands/init.md", "\ncommands/init.md"),
         "a file the bump writes but the hook no longer stages"),
        (".githooks/pre-commit",
         hook.replace("commands/init.md", "LICENSE"),
         "a staged file the bump never writes"),
        (".githooks/pre-commit", hook.replace("The seven files", "The six files"),
         "a stale count in the hook's own comment"),
        ("scripts/check-plugin.sh",
         checker.replace(
             'VERSION_FILES=""',
             'VERSION_FILES="$PLUGIN_ROOT/.claude-plugin/plugin.json"', 1),
         "a restated manifest list in §6d3"),
        ("scripts/check-plugin.sh", checker.replace(" --audit-sites", ""),
         "the audit being unhooked"),
        ("scripts/tests/test_git_hooks.py",
         git_hooks.replace("MANIFESTS = bump.MANIFESTS",
                           ("MANIFESTS = (\".claude-plugin/plugin.json\", "
                            "\".plugin/plugin.json\")"), 1),
         "a hand-written seed tuple in a release test"),
    )
    with tempfile.TemporaryDirectory() as td:
        base = pathlib.Path(td)
        for site, mutated, label in cases:
            if mutated == _read(site):
                problems.append(f"falsifier setup broken: {label} in {site} "
                                "could not be injected")
                continue
            fixture = base / f"probe{len(list(base.iterdir()))}"
            # `versioning` rides along: bump-version.py imports its sibling.
            for rel in ("scripts/bump-version.py", "scripts/versioning.py") + bump.OWNED_SITES:
                dest = fixture / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes((ROOT / rel).read_bytes())
            (fixture / site).write_text(mutated, encoding="utf-8")
            proc = _cli(fixture, "--audit-sites")
            out = (proc.stdout or "") + (proc.stderr or "")
            if proc.returncode == 0:
                problems.append(f"the set audit accepts {label} in {site}")
            elif site not in out:
                problems.append(f"the set audit rejects {label} in {site} without "
                                f"naming the site: {out.strip()[:200]}")
    # The two release tests must seed from the owner, not from a copy.
    if "bump.MANIFESTS" not in pipeline:
        problems.append("test_release_pipeline.py does not seed from bump.MANIFESTS")
    return "; ".join(problems) if problems else None


def _read_root(root: pathlib.Path, rel: str) -> str:
    try:
        return (root / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _audit_family(root: pathlib.Path) -> list[str]:
    """The shipped audit scripts — derived from the tree, never listed by hand."""
    return sorted(p.name for p in (root / "scripts").glob("check-*")
                  if p.suffix in (".sh", ".py"))


def _audit_section_ids(root: pathlib.Path, script: str) -> list[str]:
    """The `== N)` section headers a check script emits, in order."""
    return re.findall(r'(?m)^echo "== ([0-9a-z]+)\)',
                      _read_root(root, "scripts/" + script))


def _fenced_block_after(text: str, anchor: str) -> str | None:
    """The first ```bash block that follows an anchor phrase."""
    start = text.find(anchor)
    if start < 0:
        return None
    match = re.search(r"```(?:bash|sh)?\n(.*?)```", text[start:], re.DOTALL)
    return match.group(1) if match else None


def _audit_inventory_problems(root: pathlib.Path) -> list[str]:
    """E-049: the documented audit inventory must equal the shipped one.

    The audits a reader may run, the section ranges the docs quote and the maps
    they print are all restatements of what `scripts/check-*` and their
    `== N)` headers already say — and they were maintained by hand. Measured
    2026-10-15, four independent drifts were live: the runnable blocks omitted
    `check-triggers.py` (which ships and runs as §6f), five `#0–#6f` range
    claims named a range ending one section short of the script's `#6g`, both
    `# map:` lines omitted `#6g`, and the "six checks stay local" sentences
    counted a family of seven. This derives both sets from the tree and
    requires every one of those claims to agree.
    """
    problems: list[str] = []
    family = _audit_family(root)
    if not family:
        return ["no shipped scripts/check-* audit found — the family is derived, "
                "not listed"]

    for rel, anchor in _AUDIT_BLOCK_ANCHORS:
        block = _fenced_block_after(_read_root(root, rel), anchor)
        if block is None:
            problems.append(f"{rel}: no audit block under {anchor!r} — the "
                            "reader has no list of runnable checks")
            continue
        named = sorted(set(_AUDIT_SCRIPT_RE.findall(block)))
        missing = [a for a in family if a not in named]
        unknown = [n for n in named if n not in family]
        if missing:
            problems.append(f"{rel}: the audit block under {anchor!r} omits "
                            f"{missing} — they ship and are runnable")
        if unknown:
            problems.append(f"{rel}: the audit block under {anchor!r} names "
                            f"{unknown}, which no script ships")

    for rel in _AUDIT_INVENTORY_DOCS:
        text = _read_root(root, rel)
        last_script, last_line = None, -99
        for lineno, line in enumerate(text.splitlines(), 1):
            if _AUDIT_SCRIPT_RE.search(line):
                last_script, last_line = _AUDIT_SCRIPT_RE.search(line).group(0), lineno
            for claim in _AUDIT_RANGE_RE.finditer(line):
                before = _AUDIT_SCRIPT_RE.findall(line[:claim.start()])
                owner = (before[-1] if before
                         else (last_script if lineno - last_line <= 3 else None))
                if owner is None:
                    problems.append(f"{rel}:{lineno}: the range {claim.group(0)} "
                                    "names no audit script to belong to")
                    continue
                ids = _audit_section_ids(root, owner)
                if not ids:
                    problems.append(f"{rel}:{lineno}: {owner} emits no '== N)' "
                                    "section headers to quote")
                    continue
                if (claim.group(1), claim.group(2)) != (ids[0], ids[-1]):
                    problems.append(
                        f"{rel}:{lineno}: the range {claim.group(0)} claims all of "
                        f"{owner}, whose sections run #{ids[0]}–#{ids[-1]}")

        # A map is anchored by its CONTENT, not by the nearest script name in
        # the file: it must equal some shipped audit's section set exactly.
        shipped_spans = {s: _audit_section_ids(root, s) for s in family}
        shipped_spans = {s: ids for s, ids in shipped_spans.items() if ids}
        for match in _AUDIT_MAP_RE.finditer(text):
            lineno = text[:match.start()].count("\n") + 1
            mapped = re.findall(r"#([0-9a-z]+)\b", match.group(1))
            if len(mapped) != len(set(mapped)):
                problems.append(f"{rel}:{lineno}: the '# map:' repeats a section id")
            if not any(set(ids) == set(mapped) for ids in shipped_spans.values()):
                spans = ", ".join(f"{s}: #{ids[0]}–#{ids[-1]}"
                                  for s, ids in sorted(shipped_spans.items()))
                problems.append(
                    f"{rel}:{lineno}: the '# map:' matches no shipped audit's "
                    f"section set ({len(mapped)} ids) — shipped spans: {spans}")

    # The docs count pytest with the audits ("run the seven checks locally"),
    # so the claim is the family plus one — and a parenthetical that spells the
    # split out must add up to the same number.
    for rel, pattern in _AUDIT_COUNT_CLAIMS:
        text = _read_root(root, rel)
        found = re.findall(pattern, text)
        if not found:
            problems.append(f"{rel}: no audit-count claim ({pattern!r}) — the "
                            "sentence that says how big the local set is is gone")
            continue
        for value in found:
            number = _NUMBER_WORDS.get(value.lower())
            if number is None and value.isdigit():
                number = int(value)
            if number != len(family) + 1:
                problems.append(
                    f"{rel}: the audit-count claim {value!r} is not the shipped "
                    f"set ({len(family)} audits + pytest = {len(family) + 1})")
        for split in _AUDIT_COUNT_SPLIT_RE.finditer(text):
            spelled = int(split.group(1) or split.group(2))
            if spelled + 2 != len(family) + 1:
                problems.append(
                    f"{rel}: '{split.group(0)}' adds up to {spelled + 2}, not "
                    f"{len(family) + 1} (pytest + static checks + handoff lint)")
    return problems


def _test_count_claim_problem(rel: str, block: str, collected: int) -> str | None:
    """The suite-size claim in a verify block, judged against the collected count."""
    claims = _TEST_COUNT_RE.findall(block)
    if not claims:
        return (f"{rel}: the verify block states no suite size — the number a "
                "reader uses to sanity-check the install is unstated")
    for raw, plus in claims:
        number = int(raw.replace(",", ""))
        if plus and number > collected:
            return (f"{rel}: the claim '# {raw}+ tests' is above the suite "
                    f"({collected} collected)")
        if not plus and number != collected:
            return (f"{rel}: the claim '# {raw} tests' is not the suite "
                    f"({collected} collected) — state a floor, or keep the exact "
                    "number true in the same commit that changes it")
    return None


def _check_documented_test_count_is_honest() -> str | None:
    """E-049: the '# N tests' claim in each verify block matches the suite.

    The count is DERIVED by collecting the suite (1.3s here), never copied from
    the docs, so the claim cannot outlive the tree it describes.
    """
    proc = subprocess.run([sys.executable, "-m", "pytest", "--collect-only",
                           "-p", "no:cacheprovider"], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=600,
                          stdin=subprocess.DEVNULL)
    match = re.search(r"(\d+) tests? collected", proc.stdout or "")
    if not match:
        return ("the suite's collected test count could not be derived: "
                + (proc.stdout or proc.stderr or "").strip()[-200:])
    collected = int(match.group(1))
    problems = []
    for rel, anchor in _AUDIT_TEST_COUNT_BLOCKS:
        block = _fenced_block_after(_read_root(ROOT, rel), anchor)
        if block is None:
            problems.append(f"{rel}: no verify block under {anchor!r}")
            continue
        problem = _test_count_claim_problem(rel, block, collected)
        if problem:
            problems.append(problem)
    return "; ".join(problems) if problems else None


def _check_audit_inventory_matches_the_shipped_one() -> str | None:
    """E-049: the documented audit inventory equals the shipped family."""
    problems = _audit_inventory_problems(ROOT)
    return "; ".join(problems) if problems else None


# E-050: the template inventory is DERIVED from templates/ + skeleton.py's
# TEMPLATE_PAIRS, and three shipped surfaces restate it by hand: the folder tree
# the init-copied README draws (plus its `cp _template_X.md` recipes and relative
# links, read by a user inside a project that has ONLY the skeleton), the
# shorthand tree comments in the three runbooks, and CHECK 8's scan scope in
# check-methodology.sh. Measured 2026-10-15: the README promised
# `_template_PM.md` and an `incidents/` directory no installer ever created
# (GUIDE/KILAVUZ and the PR template all pointed readers into that directory),
# and two init-copied docs linked the manifestos as `../bmad/...` — a path init
# deliberately refuses to build (one of them, `../bmad/usage-guide.md`, the
# plugin never shipped at all). Nothing measured any of it.
_SHORTHAND_DOCS = ("README.md", "GUIDE.md", "KILAVUZ.md")
_TEMPLATE_SHORTHAND_RE = re.compile(
    r"_template_([A-Za-z]+(?:/[A-Za-z]+)*)\s*\+\s*([A-Za-z0-9_./-]+)")
_TREE_DIR_RE = re.compile(r"[\u251c\u2514]\u2500\u2500\s+([A-Za-z0-9_.-]+)/")
_MD_LINK_RE = re.compile(r"\]\(([^)\s]+)\)")
_TEMPLATE_NAME_RE = re.compile(r"_template_[A-Za-z0-9]+\.md")


def _bench_skeleton(root: pathlib.Path):
    """skeleton.py is the canonical owner of the skeleton contract (E-047)."""
    import importlib.util as _importlib_util  # noqa: PLC0415

    spec = _importlib_util.spec_from_file_location(
        "_bench_template_inventory", root / "bmad" / "scripts" / "skeleton.py")
    module = _importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _resolve_relative(base: str, target: str) -> str:
    """`base/target` with `.`/`..` applied — no filesystem access."""
    parts: list[str] = []
    for seg in f"{base}/{target}".split("/"):
        if seg == "..":
            if parts:
                parts.pop()
        elif seg not in ("", "."):
            parts.append(seg)
    return "/".join(parts)


def _installed_doc_path_problems(root: pathlib.Path) -> list[str]:
    """E-050: an init-copied doc may only promise paths init itself creates."""
    sk = _bench_skeleton(root)
    dirs = set(sk.DIRECTORIES)
    dests = {dest for _src, dest in sk.TEMPLATE_PAIRS}
    shipped = {p.name for p in (root / "templates").glob("*.md")}
    problems: list[str] = []
    for src, dest in sk.TEMPLATE_PAIRS:
        if not src.endswith(".md"):
            continue
        path = root / "templates" / src
        if not path.is_file():
            problems.append(f"templates/{src}: missing — it is installed as "
                            f"{dest} with no source to copy")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        base = str(pathlib.PurePosixPath(dest).parent)
        for name in sorted(set(_TEMPLATE_NAME_RE.findall(text))):
            if name not in shipped:
                problems.append(f"templates/{src}: names {name}, which does "
                                "not ship in templates/ — the reader cannot copy it")
        for claimed in _TREE_DIR_RE.findall(text):
            if _resolve_relative(base, claimed) not in dirs:
                problems.append(f"templates/{src}: the folder tree draws "
                                f"{claimed}/ — init does not create that directory")
        for target in _MD_LINK_RE.findall(text):
            if target.startswith(("http://", "https://", "#", "mailto:", "{")):
                continue
            resolved = _resolve_relative(base, target)
            if resolved in dests:
                continue
            if not any(resolved == d or resolved.startswith(d + "/") for d in dirs):
                problems.append(f"templates/{src}: the link {target} resolves to "
                                f"{resolved}, which the skeleton does not contain")
    return problems


def _template_inventory_problems(root: pathlib.Path) -> list[str]:
    """E-050: the documented template inventory equals the shipped one."""
    sk = _bench_skeleton(root)
    shipped = {p.name for p in (root / "templates").glob("_template_*.md")}
    all_shipped = {p.name for p in (root / "templates").glob("*.md")}
    problems: list[str] = []
    for rel in _SHORTHAND_DOCS:
        text = _read_root(root, rel)
        matches = _TEMPLATE_SHORTHAND_RE.findall(text)
        if not matches:
            problems.append(f"{rel}: the templates/ tree comment is gone — the "
                            "inventory claim cannot be read")
            continue
        names: set[str] = set()
        for families, tail in matches:
            names |= {f"_template_{fam}.md" for fam in families.split("/")}
            for token in tail.split("/"):
                if not any(name.startswith(token) for name in all_shipped):
                    problems.append(f"{rel}: the templates/ tree comment names "
                                    f"{token}, which matches no shipped file")
        if names != shipped:
            problems.append(
                f"{rel}: the templates/ tree comment lists {sorted(names)} but "
                f"the tree ships {sorted(shipped)}")

    # CHECK 8 is the only audit that scans templates at all: its scope is derived
    # from TEMPLATE_PAIRS — every `_template_*` pair landing under
    # docs/development/ must be scanned at its plugin path and its installed copy
    # (a template it never lists is a template whose leaked guidance ships).
    checker = _read_root(root, "scripts/check-methodology.sh")
    loop = re.search(r"for _tpl in \\\n(.*?); do", checker, re.DOTALL)
    if not loop:
        problems.append("check-methodology.sh: CHECK 8 no longer lists the "
                        "templates it scans — the hygiene rule is dead code")
        return problems
    expected = {src: dest for src, dest in sk.TEMPLATE_PAIRS
                if src.startswith("_template_") and dest.startswith("docs/development/")}
    scanned_plugin = set(re.findall(r"METODOLOJI_ROOT\}?/templates/([A-Za-z0-9_.-]+)",
                                    loop.group(1)))
    scanned_project = set(re.findall(r"PROJECT_ROOT\}?/([A-Za-z0-9_./-]+)",
                                     loop.group(1)))
    if scanned_plugin != set(expected):
        problems.append("check-methodology.sh: CHECK 8 scans "
                        f"{sorted(scanned_plugin)} in the plugin but the "
                        f"shipped development templates are {sorted(expected)}")
    if scanned_project != set(expected.values()):
        problems.append("check-methodology.sh: CHECK 8 scans "
                        f"{sorted(scanned_project)} in the project but init "
                        f"installs them as {sorted(expected.values())}")
    return problems


def _check_installed_doc_paths() -> str | None:
    """E-050: init-copied docs promise only paths init creates."""
    problems = _installed_doc_path_problems(ROOT)
    return "; ".join(problems) if problems else None


def _check_template_inventory_matches_the_shipped_one() -> str | None:
    """E-050: the documented template inventory equals the shipped one."""
    problems = _template_inventory_problems(ROOT)
    return "; ".join(problems) if problems else None


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
    checks.append(("free-zone symlink closure holds on every surface (E-036)",
                   _check_free_zone_symlink_closure))
    checks.append(("remedies rooted and session/review guidance pinned (E-037)",
                   _check_remedies_and_session_guidance_pinned))
    checks.append(("plugin-script invocations resolve where they ship (E-038, E-039)",
                   _check_gate_mentions_resolve_where_shipped))
    checks.append(("tech-debt audit covers the shipped tree and is clean (E-015)",
                   _check_techdebt_audit_covers_the_tree))
    checks.append(("record tooling refuses a non-UTF-8 file (E-016)",
                   _check_record_decode_refusals))
    checks.append(("engine and check scripts refuse a non-UTF-8 file (E-017)",
                   _check_engine_decode_refusals))
    checks.append(("producer VERIFY audit covers every producer surface (E-018)",
                   _check_bridge_producer_verify_audit))
    checks.append(("§2c demands the two-part verify pattern §4 demands (E-030)",
                   _check_bridge_verify_pattern_parity))
    checks.append(("bridge section refs resolve on every citing surface (E-019)",
                   _check_bridge_section_refs_resolve))
    checks.append(("QR feeder audit covers every feeder surface (E-020)",
                   _check_bridge_feeder_audit_covers_every_feeder))
    checks.append(("producer record targets audited (E-021)",
                   _check_bridge_record_target_audit_covers_every_producer))
    checks.append(("QR feeder SKILL.md audit covers every bridge surface (E-024)",
                   _check_qr_feeder_skillmd_audit_covers_every_surface))
    checks.append(("bridge runtime-visibility audit covers every bridge surface (E-027)",
                   _check_bridge_runtime_visibility_audit_covers_every_surface))
    checks.append(("story-key grammar agreed by generator and engine (E-028)",
                   _check_story_title_grammar_agreement))
    checks.append(("record-chain remedies name runnable plugin scripts (E-029)",
                   _check_gate_remedy_paths_are_runnable))
    checks.append(("[HYPOTHESIS] opt-out holds at write and commit time (E-031)",
                   _check_hypothesis_optout_agrees_across_gates))
    checks.append(("native<->methodology mirror hop works both ways (E-032)",
                   _check_native_methodology_mirror_both_directions))
    checks.append(("commands printed into the session are runnable (E-033)",
                   _check_injected_remedies_are_runnable))
    checks.append(("documented gate invocations parse under argparse (E-034)",
                   _check_documented_gate_invocations_parse))
    checks.append(("experiment lineage declared and enforced (E-022)",
                   _check_experiment_lineage_enforced))
    checks.append(("negtest counts and check-plugin doc pointers pinned (E-023)",
                   _check_negtest_stage_wiring))
    checks.append(("every negtest suite's count matches its stages (E-026)",
                   _check_all_negtest_counts_are_falsifiable))
    checks.append(("cross-machine marker read from the canonical bullet (E-025)",
                   _check_cross_machine_marker_is_anchored))
    checks.append(("tool contract names the real harness failures (E-041)",
                   _check_tool_contract_names_the_real_harness_failures))
    checks.append(("documented change radar equals the shipped one (E-043)",
                   _check_change_radar_contract_matches))
    checks.append(("declared relay graph equals the shipped one (E-044)",
                   _check_relay_graph_edges_match_declared))
    checks.append(("every spawn hands its child an explicit stdin (E-045)",
                   _check_every_spawn_hands_stdin))
    checks.append(("every board story key resolves to a story record (E-046)",
                   _check_board_story_keys_resolve))
    checks.append(("skeleton contract declarations agree with the script (E-047)",
                   _check_skeleton_contract_declarations_agree))
    checks.append(("version-owned set agrees across its copies (E-048)",
                   _check_version_owned_sites_agree))
    checks.append(("documented audit inventory equals the shipped family (E-049)",
                   _check_audit_inventory_matches_the_shipped_one))
    checks.append(("documented suite size matches the collected suite (E-049)",
                   _check_documented_test_count_is_honest))
    checks.append(("init-copied docs promise only paths init creates (E-050)",
                   _check_installed_doc_paths))
    checks.append(("documented template inventory equals the shipped one (E-050)",
                   _check_template_inventory_matches_the_shipped_one))
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

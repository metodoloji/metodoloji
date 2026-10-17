"""Configuration constants for BMAD hooks engine."""

import os
import pathlib
import re

# Runtime detection. main.py sets METODOLOJI_RUNTIME from --runtime= AFTER
# imports, so this must stay a function: a module-level constant would freeze
# the pre-flag value. (The old RUNTIME constant was removed — zero readers.)
def runtime() -> str:
    """Live runtime value (main.py may set it after import)."""
    return os.environ.get("METODOLOJI_RUNTIME", "claude")

# Gate script location — resolved inside the methodology root.
# config.py lives at <methodology-root>/hooks/engine/modules/config.py:
#   parent1 = modules/
#   parent2 = engine/
#   parent3 = hooks/
#   parent4 = <methodology-root>
_METHODOLOGY_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent.parent

def _first_existing(cands: list[pathlib.Path]) -> pathlib.Path | None:
    for c in cands:
        if c.exists():
            return c
    return None

GATE_DIR = _first_existing([
    _METHODOLOGY_ROOT / "skills" / "bmad-research-experiment" / "scripts",
])

# Shared story status reader (DRY: guard metadata, chain, and gate-record
# checks all read through this — one dialect contract, not parallel regexes).
_FIELD_STATUS_RE = re.compile(
    r"^(?:[-*]\s+)?\*?\*?Status\s*:\s*\*?\*?\s*(.+)", re.IGNORECASE | re.MULTILINE)
_TABLE_STATUS_RE = re.compile(
    r"^\s*\|\s*Status\s*\|\s*([^|\n]*)\|", re.IGNORECASE | re.MULTILINE)


def story_status(content: str) -> str:
    """Story lifecycle status in either record dialect.

    Field form first (``- **Status:** done`` / ``Status: done`` — the
    native-draft shape, and everything previously detected), bridge
    field-table row (``| Status | done |`` — the methodology-record
    shape) as fallback; ``""`` when absent. Both dialects return the raw
    stripped-lowered value, so validity vocabulary is identical.
    """
    m = _FIELD_STATUS_RE.search(content)
    if m:
        return m.group(1).strip().lower()
    m = _TABLE_STATUS_RE.search(content)
    if m:
        return m.group(1).strip().lower()
    return ""

# Log file location
def log_file() -> str:
    # OpenHands plugin olarak her zaman .metodoloji/logs/ kullan
    return ".metodoloji/logs/hook-audit.log"


# --- Validation Bounds (MEDIUM #11 / ISSUE #70) --------------------------------
# Prevent quadratic validation loops by bounding collection sizes
MAX_STORY_COUNT = 1000            # Max stories to validate in single check
MAX_DUPLICATE_CHECK_RECORDS = 5000 # Max record files to scan for duplicate IDs

# --- Timeout Values (MEDIUM #14 / ISSUE #73) --------------------------------
# Prevent indefinite hangs on corrupted files or locked resources
# Max time for ONE gate.verify() call to complete. Must stay BELOW the
# PreToolUse hook timeout in hooks.json (10s) so a hung verify fails open
# inside the hook budget instead of being killed by the hook runner. Real
# verifies take ~2ms/record; this is only a corrupted-record safety net.
GATE_VERIFY_TIMEOUT_SECONDS = 8

# --- Scope-coverage git envelope (F1/T-09) ---------------------------------
# _check_scope_coverage runs inside quality() on the commit path, i.e. inside
# the SAME 10s PreToolUse budget. A per-call git timeout above the hook
# budget (it was 30s) means the hook runner kills the process first and the
# commit proceeds with NO gate decision (silent allow — the worst FN class).
# Envelope: the whole mirror (both git calls) must finish inside this many
# seconds; each call gets the REMAINING budget, so two hanging gits can
# never jointly exceed it. Warn-only mirror failing open is by design.
SCOPE_GIT_TIMEOUT_SECONDS = 8

# --- Gate strictness ---------------------------------------------------------
# custom/config.toml [hooks]: quality_gate / deploy_guard / code_guard /
# stop_guard (soft|hard). quality/deploy default soft (warn-only); code
# defaults hard (code writes stay mechanical unless a project explicitly
# relaxes them, e.g. brownfield adoption). stop_guard is a backward-compat
# key only: stop() is report-only and never blocks in either mode, and no
# reader consults its value (see modules/stop.py). Read live per-call so
# config edits apply without a reload. Each key is read independently.
#
# NOTE: the shipped custom/config.toml sets code_guard = "hard" (steady state).
# Brownfield adoption may temporarily relax it to "soft" (warn-only) until
# the first VERIFIED scope lands, then returns to hard — see KILAVUZ #7.
#
# NOTE (deliberate): this reads the plugin's own custom/config.toml, NOT the
# bmad resolve_config.py merge (four plugin layers + four {project-root}
# layers, plus the legacy per-module config.yaml bridge — see
# bmad/scripts/resolve_config.py). Hook enforcement is plugin policy, not
# project configuration: the layers above let a PROJECT override its own
# behavior, but a target project must never be able to silently relax the
# guard that watches it. There is currently no per-project or
# config.user.toml override for [hooks]: any softening in custom/config.toml
# applies plugin-globally to every project and must be re-hardened after
# brownfield adoption (see custom/config.toml header).
_HOOKS_CFG = _METHODOLOGY_ROOT / "custom" / "config.toml"

# --- [hooks] section cache (Faz 1) -------------------------------------------
# hook_gate_mode()/blackboard_enabled() are called several times per hook
# invocation; re-reading + re-parsing custom/config.toml on every call wastes
# disk I/O on the hot path. The parsed [hooks] dict is cached per process and
# invalidated by (mtime_ns, size) — a hook process is short-lived, so a stale
# read can only lag a config edit by one process lifetime. Tests that swap
# _HOOKS_CFG keep working: a changed path invalidates the cache too.
_HOOKS_CACHE: dict | None = None
_HOOKS_CACHE_KEY: tuple | None = None


# One parser for every table of custom/config.toml: a second reader would be
# a second opinion about what the file says. Cached per (section, path,
# mtime_ns, size); switching the path (tests, a temp config) is itself a cache
# miss, so no caller has to remember to clear anything.
_SECTION_CACHE: dict[str, tuple[tuple, dict]] = {}


def _read_config_section(section: str) -> dict:
    """Parse one `[<section>]` table of custom/config.toml (cached)."""
    global _HOOKS_CACHE, _HOOKS_CACHE_KEY
    try:
        st = _HOOKS_CFG.stat()
        key = (str(_HOOKS_CFG), st.st_mtime_ns, st.st_size)
    except OSError:
        return {}
    cached = _SECTION_CACHE.get(section)
    if cached is not None and cached[0] == key:
        parsed = cached[1]
    else:
        parsed: dict[str, str] = {}
        try:
            text = _HOOKS_CFG.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # Missing OR not valid UTF-8: there is no override to apply, so the
            # defaults win. That keeps the decision path ALIVE and fail-closed
            # (code_guard stays "hard") — a crash here would leave the hook with
            # NO decision, which a runner may read as an allow (E-006/E-007).
            text = ""
        header = f"[{section}]"
        inside = False
        for line in text.splitlines():
            stripped = line.strip()
            # A comment line is prose, not a setting: without this, a comment
            # that happens to contain '=' inside the table parsed as a key
            # (the freshness table's own explanatory block did exactly that).
            if stripped.startswith("#"):
                continue
            if stripped.startswith(header):
                inside = True
                continue
            if inside and stripped.startswith("[") and not stripped.startswith(header):
                break
            if inside and "=" in stripped:
                k, _, v = stripped.partition("=")
                # Strip a trailing # comment and surrounding quotes.
                parsed[k.strip()] = v.split("#", 1)[0].strip().strip('"').strip("'")
        if len(_SECTION_CACHE) >= 8:
            _SECTION_CACHE.clear()
        _SECTION_CACHE[section] = (key, parsed)
    if section == "hooks":
        # Kept in sync for the callers that reset the legacy pair to force a
        # re-read (tests, bench_consistency): the globals still describe the
        # [hooks] parse they always described.
        _HOOKS_CACHE, _HOOKS_CACHE_KEY = parsed, key
    return dict(parsed)


def _read_hooks_section() -> dict:
    """Parse the [hooks] section once per (path, mtime_ns, size)."""
    return _read_config_section("hooks")


# --- [freshness] table (E-053, plan §5.5) ------------------------------------
# The three knobs the freshness plane exposes. Defaults are the shipping
# behavior: scope-sensitive classification on, UNKNOWN warned about, and a
# bounded changed-file set.
_FRESHNESS_DEFAULTS = {"scope_digest": "true", "unknown_policy": "warn",
                       "max_digest_files": "2000"}
_VALID_FRESHNESS_KEYS = frozenset(_FRESHNESS_DEFAULTS)


def freshness_setting(key: str) -> str:
    """One `[freshness]` value, or its published default when absent."""
    value = _read_config_section("freshness").get(key, "").strip()
    return value if value else _FRESHNESS_DEFAULTS.get(key, "")


def _hook_gate_value(gate_key: str, *args) -> str:
    """Return the value of a [hooks] gate key: 'hard'/'soft' when set, else default.

    Default comes from _GATE_DEFAULTS (soft for quality_gate/deploy_guard,
    hard for code_guard; stop_guard has no reader). A positional default may
    be passed for backward compatibility with single-arg mocks in tests; the
    table wins when no override is given. Only the named key is read, so
    gates stay independent.
    """
    default = args[0] if args else _GATE_DEFAULTS.get(gate_key, "soft")
    val = _read_hooks_section().get(gate_key)
    if val in ("hard", "soft"):
        return val
    return default

# Per-key defaults, used only when the key is absent from custom/config.toml:
# the commit/deploy gates are warn-only unless opted into hard, and the code
# gate defaults to hard. stop_guard is retained for backward compatibility and
# has no reader — stop is report-only whatever its value.
_GATE_DEFAULTS = {
    "quality_gate": "soft",
    "deploy_guard": "soft",
    "code_guard": "hard",
    "stop_guard": "hard",
}

# NEW: Valid [hooks] config keys (MEDIUM #9 / ISSUE #68)
_VALID_HOOKS_KEYS = frozenset({
    "quality_gate",
    "deploy_guard",
    "code_guard",
    "stop_guard",
    "blackboard",
    # Freshness plane (E-053): how a claim whose code state moved is treated.
    #   "off"  → no witness is written and no verdict is derived (pre-E-053
    #            behavior; the board keeps working, it just cannot say stale)
    #   "warn" → derived and REPORTED everywhere, writes still allowed
    #   "deny" → an APPROVED record whose scope moved no longer unlocks code
    # Read live per-call by modules/freshness.py:mode().
    "freshness",
})


def _validate_config_section(section: str, valid_keys: frozenset) -> tuple[bool, str]:
    """Validate one config table against its recognized keys (MEDIUM #9).

    Checks that all keys inside `[<section>]` are recognized, so a typo
    (`code_gard = "hard"`) is reported instead of silently doing nothing.
    Returns (is_valid, error_message).
    """
    try:
        text = _HOOKS_CFG.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        # Missing OR not valid UTF-8: there is no block to police and the
        # defaults still govern (fail-closed) — nothing to report.
        return True, ""  # Config file doesn't exist yet

    header = f"[{section}]"
    inside = False
    invalid_keys = []
    line_no = 0

    for line in text.splitlines():
        line_no += 1
        stripped = line.strip()

        # Skip empty lines and comments
        if not stripped or stripped.startswith("#"):
            continue

        # Check for the section start
        if stripped.startswith(header):
            inside = True
            continue

        # Check for other sections
        if stripped.startswith("[") and not stripped.startswith(header):
            inside = False
            continue

        # Validate keys in this section
        if inside and "=" in stripped:
            key, _, _ = stripped.partition("=")
            key = key.strip()

            if key not in valid_keys:
                invalid_keys.append((line_no, key))

    if invalid_keys:
        error_msg = f"Invalid {header} keys in {_HOOKS_CFG}: "
        error_parts = [f"line {line}: '{key}' (valid: {', '.join(sorted(valid_keys))})"
                      for line, key in invalid_keys[:3]]
        error_msg += "; ".join(error_parts)
        return False, error_msg

    return True, ""


def _validate_hooks_config() -> tuple[bool, str]:
    """Validate [hooks] section in custom/config.toml (MEDIUM #9 / ISSUE #68)."""
    return _validate_config_section("hooks", _VALID_HOOKS_KEYS)


def _validate_freshness_config() -> tuple[bool, str]:
    """Validate [freshness] section in custom/config.toml (E-053)."""
    ok, error = _validate_config_section("freshness", _VALID_FRESHNESS_KEYS)
    if not ok:
        return ok, error
    policy = freshness_setting("unknown_policy")
    if policy not in ("warn", "silent", "deny"):
        return False, (f"Invalid [freshness] unknown_policy '{policy}' in "
                       f"{_HOOKS_CFG}: valid values are warn, silent, deny")
    raw = freshness_setting("max_digest_files")
    try:
        if int(raw) < 1:
            raise ValueError(raw)
    except ValueError:
        return False, (f"Invalid [freshness] max_digest_files '{raw}' in "
                       f"{_HOOKS_CFG}: expected a positive integer")
    digest = freshness_setting("scope_digest").lower()
    if digest not in ("true", "false", "on", "off", "yes", "no"):
        return False, (f"Invalid [freshness] scope_digest '{digest}' in "
                       f"{_HOOKS_CFG}: expected true|false")
    return True, ""


def blackboard_enabled() -> bool:
    """[hooks] blackboard = on|off (default on). Read live per-call.

    off → every engine blackboard integration becomes a no-op; the CLI keeps
    working (fail-open) because skills own their writes.
    """
    return _read_hooks_section().get("blackboard", "on") != "off"

def hook_gate_mode(gate_key: str) -> str:
    """Public per-call accessor for one [hooks] gate mode: 'soft' | 'hard'.

    Each gate key is read INDEPENDENTLY and live from custom/config.toml —
    an import-time constant would go stale, and one gate's mode must never
    leak into the other's semantics.
    """
    return _hook_gate_value(gate_key)

# Code classification
NON_CODE_EXTS = {
    ".md", ".markdown", ".txt", ".rst", ".json", ".jsonc", ".toml", ".yaml", ".yml",
    ".csv", ".tsv", ".log", ".lock",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".bmp", ".avif",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".pdf", ".zip", ".gz", ".tar", ".bz2", ".xz", ".7z", ".rar",
    ".sqlite", ".db", ".sqlite3", ".parquet", ".arrow", ".npy", ".npz", ".h5",
    ".hdf5", ".pkl", ".pickle", ".feather",
    # Toolchain config: real code lives elsewhere; gating these files only
    # produces brownfield false-blocks (e.g. prisma.config.ts).
    ".config.js", ".config.ts", ".config.mjs", ".config.cjs",
}

# Basename-level toolchain config (matched against the full relative path):
# bundler/linter/formatter/ORM configs are not application code.
NON_CODE_CONFIG_RES = (
    re.compile(r"(?i)(?:^|/)(?:prisma|vite|vitest|webpack|rollup|esbuild|babel|eslint|prettier|"
               r"postcss|tailwind|jest|playwright|cypress|next|nuxt|astro)\.config\.[a-z0-9]+$"),
    re.compile(r"(?i)(?:^|/)(?:tsconfig(?:\..*)?|jsconfig(?:\..*)?|package-lock\.json|"
               r"yarn\.lock|pnpm-lock\.yaml|bun\.lockb?)$"),
)

NON_CODE_BASENAMES = {
    ".gitignore", ".gitattributes", ".gitkeep", ".ignore",
    ".dockerignore", ".editorconfig", ".npmrc", "license", "copying",
    "readme", "authors", "notice"
}

CODE_BASENAMES = {
    "makefile", "dockerfile", "cmakelists.txt", "rakefile", "justfile",
    "taskfile.yml", "taskfile.yaml"
}

CODE_DIRS = {"lib", "src", "tools", "bin", "core", "app"}

EXEC_CONFIG_NAME = re.compile(
    r"(?i)(?:^|/)(?:\.github/workflows/|\.gitlab-ci\.yml$|azure-pipelines\.yml$|"
    r"(?:docker-compose|compose)[^/]*\.ya?ml$|package\.json$)"
)

# Free zones — project-relative prefixes that never need experiment approval.
# NOTE: hooks/, scripts/ and skills/ are deliberately NOT here.
# Those are plugin source trees and stay protected by the experiment gate in any
# ordinary project; see PLUGIN_FREE_PREFIXES for the self-modification exemption.
FREE_PREFIXES = (
    "_bmad/", "scratch/", "graft/", ".git/", "tmp/", "temp/",
    "openhands/", ".metodoloji/",
)

# Plugin source trees that are free ONLY when the plugin root resolves to the
# methodology root (i.e. this repository running as its own project). Resolved
# per-call in utils.is_free(); under test it is monkeypatched via config._METHODOLOGY_ROOT.
# bmad/tests/ is test tooling, not methodology content: the gate's own tests
# cannot sit behind experiment approval (circularity — the gate could never be
# tested without the gate's permission). bmad/ workflows stay gated.
#
# opencode-plugin/ is the third runtime's source tree (the same kind of surface
# as hooks//scripts//skills/ — it just arrived later) and .githooks//.github/
# plus assets/ are this repository's own automation and launch surface. E-053's
# dogfood run of check-plugin §3b measured them as "edited files outside every
# approved scope": the check had never run inside this repo (F-05), so the
# omission was invisible. With the check live, the free zone has to name them.
PLUGIN_FREE_PREFIXES = ("hooks/", "scripts/", "skills/", "custom/", "bmad/tests/",
                        "opencode-plugin/", ".githooks/", ".github/", "assets/")

INFRA_FILES = {"scripts/check-methodology.sh"}
# The gate script is DELIBERATELY not listed under its project-relative
# `skills/…` spelling (E-038): that path exists only inside the plugin
# checkout — in an ordinary project it is the dead-remedy shape itself
# (a path the agent can paste but that resolves nowhere), and in this repo
# the skills/ self-modification prefix already frees it. The gate script is
# edited at its PLUGIN root, never at a project-relative one. Removing the
# spelling also closes the E-035 Known Gap that left this policy open.

FREE_DOC_MD = re.compile(r"^docs/.*\.md$")
FREE_DOC_RAW = re.compile(r"^docs/.*/raw(/|$)")
# Case rule (E-035): these patterns carry NO `(?i)`. Free-zone membership
# follows the filesystem's own case semantics — is_free() matches them
# against a case-folded path only where the filesystem itself folds
# (os.name == "nt"). A blanket (?i) freed `DOCS/e/raw/` on a case-SENSITIVE
# host (a different directory — fail-open) while the prefix tuples gated
# `SCRATCH/` on a case-INSENSITIVE one (the same directory — fail-closed).

# Archive limits
ARCHIVE_MAX_FILE = 512 * 1024 * 1024
ARCHIVE_MAX_COMPRESSED = 64 * 1024 * 1024
ARCHIVE_MAX_MEMBERS = 200_000
ARCHIVE_MAX_UNCOMPRESSED = 2 * 1024 * 1024 * 1024
# Per-member cap: without it a single 2GB member passes the total check.
ARCHIVE_MAX_MEMBER = 256 * 1024 * 1024

TAR_ARG_OPTS = frozenset({
    "-C", "--directory", "-f", "--file", "--exclude", "--owner", "--group",
    "--transform", "--to-command", "--strip-components", "--index-file",
    "--record-size", "--blocking-factor", "--use-compress-program",
    "--newer", "--newer-mtime", "--listed-incremental", "--files-from",
    "--checkpoint", "--checkpoint-action", "--warning", "--level",
})

# Secret protection
_BMD_DIR = re.compile(r"(?i)(?:^|[\\/~\s\"'=])\.bmad(?=[\\/\s\"'*?\[\]]|$)")
_AGENT_ZONES = ("scratch/", "tmp/", "temp/")
# Secret access needs an access context (assignment, env read, file open),
# not a bare substring — "secret_env" alone appears in ordinary prose.
_KEY_ACCESS_IN_CONTENT = re.compile(
    r"(?i)(?:"
    r"\.bmad(?=[\\/\s\"'*?\[\]]|$)|"  # .bmad dir reference (path boundary)
    r"gate-key|"                       # key filename — specific enough bare
    r"bmad_gate_key|gate_token|"        # exact key/token identifiers
    r"(?:load_secret|secret_file|secret_env)\s*[(=:\[]"  # call/assign/open context
    r")"
)


# NEW: Validate config on module load (MEDIUM #9 / ISSUE #68)
# Checks the [hooks] and [freshness] tables for invalid keys/values at import
# time. Both are gated policy (the second one decides how a claim whose code
# state moved is treated), so a typo must be visible rather than silent.
_config_valid, _config_error = _validate_hooks_config()
if _config_valid:
    _config_valid, _config_error = _validate_freshness_config()
if not _config_valid:
    import sys
    sys.stderr.write(f"metodoloji: config error: {_config_error}\n")
    # Note: We don't raise here (fail-open) but log the error so deployment tools can catch it

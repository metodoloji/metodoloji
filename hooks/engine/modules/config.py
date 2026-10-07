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


def _read_hooks_section() -> dict:
    """Parse the [hooks] section once per (path, mtime_ns, size)."""
    global _HOOKS_CACHE, _HOOKS_CACHE_KEY
    try:
        st = _HOOKS_CFG.stat()
        key = (str(_HOOKS_CFG), st.st_mtime_ns, st.st_size)
    except OSError:
        return {}
    if _HOOKS_CACHE is not None and _HOOKS_CACHE_KEY == key:
        return _HOOKS_CACHE
    parsed: dict[str, str] = {}
    try:
        text = _HOOKS_CFG.read_text(encoding="utf-8")
    except OSError:
        text = ""
    in_hooks = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[hooks]"):
            in_hooks = True
            continue
        if in_hooks and stripped.startswith("[") and not stripped.startswith("[hooks]"):
            break
        if in_hooks and "=" in stripped:
            k, _, v = stripped.partition("=")
            k = k.strip()
            # Strip a trailing # comment and surrounding quotes.
            v = v.split("#", 1)[0].strip().strip('"').strip("'")
            parsed[k] = v
    _HOOKS_CACHE = parsed
    _HOOKS_CACHE_KEY = key
    return parsed


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
})


def _validate_hooks_config() -> tuple[bool, str]:
    """Validate [hooks] section in custom/config.toml (MEDIUM #9 / ISSUE #68).
    
    Checks that all keys in [hooks] are recognized. Invalid keys raise error.
    Returns (is_valid, error_message).
    """
    try:
        text = _HOOKS_CFG.read_text(encoding="utf-8")
    except OSError:
        return True, ""  # Config file doesn't exist yet
    
    in_hooks = False
    invalid_keys = []
    line_no = 0
    
    for line in text.splitlines():
        line_no += 1
        stripped = line.strip()
        
        # Skip empty lines and comments
        if not stripped or stripped.startswith("#"):
            continue
        
        # Check for [hooks] section start
        if stripped.startswith("[hooks]"):
            in_hooks = True
            continue
        
        # Check for other sections
        if stripped.startswith("[") and not stripped.startswith("[hooks]"):
            in_hooks = False
            continue
        
        # Validate keys in [hooks] section
        if in_hooks and "=" in stripped:
            key, _, _ = stripped.partition("=")
            key = key.strip()
            
            if key not in _VALID_HOOKS_KEYS:
                invalid_keys.append((line_no, key))
    
    if invalid_keys:
        error_msg = f"Invalid [hooks] keys in {_HOOKS_CFG}: "
        error_parts = [f"line {line}: '{key}' (valid: {', '.join(sorted(_VALID_HOOKS_KEYS))})"
                      for line, key in invalid_keys[:3]]
        error_msg += "; ".join(error_parts)
        return False, error_msg
    
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
PLUGIN_FREE_PREFIXES = ("hooks/", "scripts/", "skills/", "custom/", "bmad/tests/")

INFRA_FILES = {"scripts/check-methodology.sh", "skills/bmad-research-experiment/scripts/run_experiment.py"}

FREE_DOC_MD = re.compile(r"(?i)^docs/.*\.md$")
FREE_DOC_RAW = re.compile(r"(?i)^docs/.*/raw(/|$)")

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
# Checks [hooks] section for invalid keys at import time
_config_valid, _config_error = _validate_hooks_config()
if not _config_valid:
    import sys
    sys.stderr.write(f"metodoloji: config error: {_config_error}\n")
    # Note: We don't raise here (fail-open) but log the error so deployment tools can catch it

#!/usr/bin/env python3
"""Shared, stdlib-only primitives for the external-context registry.

Both halves of the registry import this module: the authoring/lifecycle CLI
(`external-context.py`) and the read-only discovery CLI
(`find-external-contexts.py`). Keeping the schema, the registry boundary, the
Turkish-aware text folding, reference matching and the freshness derivation in
one place is what stops the two halves from disagreeing about the same file.

The module never fetches a URL and never parses a PDF. It only reads and writes
UTF-8 text inside the project's `docs/research/external/` tree.
"""
from __future__ import annotations

import contextlib
import math
import os
import re
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

REGISTRY_REL = "docs/research/external"
PROJECT_TEMPLATE_REL = f"{REGISTRY_REL}/_template.md"
PLUGIN_TEMPLATE_REL = "templates/external-context.md"
PLUGIN_README_REL = "templates/external-context-README.md"
PROJECT_README_REL = f"{REGISTRY_REL}/README.md"
ARCHIVE_DIR = ".archive"

SCHEMA_VERSION = 2
DEFAULT_REVALIDATE_DAYS = 180

STATUSES = ("draft", "revalidating", "reviewed", "stale", "superseded")
DISCOVERABLE_STATUSES = ("reviewed", "stale", "revalidating", "unknown")

REQUIRED_FIELDS = (
    "Schema", "Status", "Analyzed on", "Analyzed version/ref", "Revalidate when",
    "Revalidate after", "Project relevance", "Reviewer",
)
OPTIONAL_FIELDS = (
    "Aliases", "Last reviewed on", "Revalidation started on", "Superseded by",
)
REQUIRED_SECTIONS = (
    "Sources inspected", "Summary", "Integration guidance",
    "Maintenance, license, and security",
    "Risks, alternatives, and open questions", "Use in this project",
    "Revision history",
)
INTEGRATION_BULLETS = (
    "Supported runtimes/platforms", "Key interfaces and APIs",
    "Data/control flow and boundary", "Setup/configuration",
    "Failure modes and operational constraints",
    "Compatibility/version constraints",
)
REPO_HOSTS = frozenset({
    "github.com", "gitlab.com", "bitbucket.org", "codeberg.org",
    "gitea.com", "sr.ht",
})
# Public suffixes whose registrable label is the one before the suffix, not the
# last label (example.co.uk -> example, not co). Deliberately a short, stable
# list: the goal is a sensible display name, not a public-suffix database.
MULTI_LABEL_SUFFIXES = frozenset({
    "co.uk", "org.uk", "ac.uk", "gov.uk", "com.tr", "org.tr", "net.tr",
    "co.jp", "or.jp", "ne.jp", "com.au", "net.au", "org.au", "co.nz",
    "com.br", "com.mx", "co.in", "co.za", "com.cn", "com.sg", "com.hk",
    "github.io", "gitlab.io", "pages.dev", "vercel.app", "netlify.app",
})
# Template vocabulary and English/Turkish function words. A query made only of
# these terms must not select an entry: they describe the schema, not content.
STOPWORDS = frozenset({
    # schema / lifecycle vocabulary
    "status", "schema", "relevance", "aliases", "alias", "analyzed", "analyse",
    "analyze", "revalidate", "revalidation", "reviewer", "reviewed", "review",
    "draft", "revalidating", "stale", "superseded", "unknown", "observed",
    "optional", "pending", "sources", "inspected", "summary", "integration",
    "guidance", "maintenance", "license", "licence", "security", "risks",
    "alternatives", "revision", "history", "project", "context", "version",
    "ref", "when", "after", "last", "sections", "section",
    # English function words
    "the", "and", "for", "with", "this", "that", "these", "those", "from",
    "into", "are", "was", "were", "has", "have", "had", "not", "but", "use",
    "used", "using", "when", "what", "where", "which", "who", "how", "all",
    "any", "can", "may", "will", "would", "should", "must", "its", "their",
    "they", "them", "you", "your", "our", "out", "about", "over", "under",
    "than", "then", "also", "such", "only", "some", "more", "most", "other",
    # Turkish function words
    "ve", "ile", "için", "bir", "bu", "şu", "olan", "olarak", "gibi", "daha",
    "çok", "ama", "veya", "de", "da", "ki", "mi", "ne", "nasıl", "hangi",
    "her", "hem", "ancak", "üzere", "kadar", "sonra", "önce", "yok", "var",
})

_TERM_RE = re.compile(r"[^\W_]+(?:[.+#-][^\W_]+)*", re.UNICODE)
_TR_FOLD = str.maketrans({
    "İ": "i", "I": "i", "ı": "i", "Ş": "s", "ş": "s", "Ğ": "g", "ğ": "g",
    "Ü": "u", "ü": "u", "Ö": "o", "ö": "o", "Ç": "c", "ç": "c",
    "Â": "a", "â": "a", "Î": "i", "î": "i", "Û": "u", "û": "u",
})
_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_HEX_RE = re.compile(r"\b[0-9a-fA-F]{7,64}\b")
_SEMVER_RE = re.compile(
    r"(?P<op>\^|~|>=|<=|>|<|=)?\s*"
    r"(?P<major>0|[1-9]\d*)(?:\.(?P<minor>0|[1-9]\d*))?"
    r"(?:\.(?P<patch>0|[1-9]\d*|[xX*]))?"
    r"(?P<pre>-[0-9A-Za-z.-]+)?(?P<build>\+[0-9A-Za-z.-]+)?"
)


class ContextError(Exception):
    """An input or environment problem (exit 2), not a validation finding."""


# --------------------------------------------------------------------------
# Filesystem: boundary, atomic writes, advisory lock
# --------------------------------------------------------------------------

def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ContextError(f"cannot read {path}: {exc.strerror or exc}") from exc


def atomic_write_text(path: Path, text: str) -> None:
    """Write UTF-8 with LF newlines, then replace atomically.

    A crash mid-write must never leave a half-regenerated entry or index. The
    temporary file lives beside the target so `os.replace` stays on one
    filesystem (atomic on POSIX and on Windows for same-volume renames).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


@contextlib.contextmanager
def registry_lock(registry: Path, timeout: float = 10.0):
    """Serialise writers with a sidecar lock file (stdlib, cross-platform).

    The lock is advisory and fails closed after `timeout`: two agents editing
    the same registry must not interleave a read-modify-write of the index.
    A lock older than 60 s is treated as stale so a crashed writer cannot wedge
    the registry forever.
    """
    registry.mkdir(parents=True, exist_ok=True)
    lock = registry / ".registry.lock"
    deadline = time.monotonic() + timeout
    handle = None
    while handle is None:
        try:
            handle = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            with contextlib.suppress(OSError):
                if time.time() - lock.stat().st_mtime > 60:
                    lock.unlink()
                    continue
            if time.monotonic() >= deadline:
                raise ContextError(
                    f"registry is locked by another writer: {lock}")
            time.sleep(0.05)
    try:
        os.write(handle, str(os.getpid()).encode("ascii", "ignore"))
        yield
    finally:
        os.close(handle)
        with contextlib.suppress(OSError):
            lock.unlink()


def registry_root(root: str | Path, *, create: bool = False) -> Path:
    """The registry directory, guaranteed to resolve inside `root`.

    A symlinked registry that escapes the project is refused for writers and
    reported for readers. Without this guard `prepare`/`finalize`/`refresh`/
    `discard` could write or move files outside the project even though the
    discovery half already refused to read them.
    """
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        raise ContextError(f"project root is not a directory: {root}")
    registry = root_path / REGISTRY_REL
    if not registry.exists() and not registry.is_symlink():
        if not create:
            raise ContextError(f"registry is missing: {REGISTRY_REL}")
        registry.mkdir(parents=True, exist_ok=True)
    try:
        resolved = registry.resolve(strict=True)
    except OSError as exc:
        raise ContextError(
            f"registry cannot be resolved: {REGISTRY_REL} ({exc.strerror or exc})"
        ) from exc
    try:
        resolved.relative_to(root_path)
    except ValueError as exc:
        raise ContextError(
            "registry resolves outside the project root; refusing to use it"
        ) from exc
    if not resolved.is_dir():
        raise ContextError(f"registry path is not a directory: {REGISTRY_REL}")
    return resolved


def inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except (OSError, ValueError):
        return False


def require_inside(path: Path, parent: Path, label: str) -> Path:
    """Resolve `path` and refuse it when it escapes `parent`.

    Checking only the registry root is not enough: a symlinked entry folder
    (`registry/<slug>` -> outside) passes the root guard and then every
    per-entry read or write follows it outside the project. Writers and the
    audit both call this before touching the entry.
    """
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ContextError(
            f"cannot resolve {label}: {exc.strerror or exc}") from exc
    if not inside(resolved, parent):
        raise ContextError(
            f"{label} resolves outside the external-context registry; "
            "refusing to use it")
    return resolved


def require_inside_target(path: Path, parent: Path, label: str) -> Path:
    """Prove that writing to a *new* path cannot escape through an ancestor.

    `require_inside` resolves an existing path, but `prepare` creates the entry
    folder and `discard` creates the archive folder, so the leaf does not exist
    yet. Resolving the nearest existing ancestor closes the same boundary for
    creation: an entry folder (or `.archive` directory) that is a symlink or
    junction outside the registry is refused before `mkdir`/`write` follows it.
    A missing path whose existing ancestors are all inside is returned as given.
    """
    resolved_parent = parent.resolve()
    candidate = path
    while not candidate.exists() and not candidate.is_symlink():
        if candidate == candidate.parent:
            break
        candidate = candidate.parent
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ContextError(
            f"cannot resolve {label}: {exc.strerror or exc}") from exc
    if not inside(resolved, resolved_parent):
        raise ContextError(
            f"{label} resolves outside the external-context registry; "
            "refusing to use it")
    return path


def rel(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return str(path)


# --------------------------------------------------------------------------
# Text: folding, terms, tables, metadata
# --------------------------------------------------------------------------

def fold(value: str) -> str:
    """Turkish-aware case folding for search and slug comparisons.

    Python's `casefold("İ")` is `"i" + U+0307`, so a query written with a
    capital dotted I never matched the same word written with a plain i. The
    explicit translation table maps both dotted/dotless I and the other Turkish
    letters onto their ASCII search form before casefolding.
    """
    return value.translate(_TR_FOLD).casefold()


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", fold(value)).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:64].strip("-") or "external-context"


def search_terms(value: str) -> set[str]:
    """Content terms only: folded, length >= 2, template/function words removed."""
    return {
        term for term in (fold(part) for part in _TERM_RE.findall(value))
        if len(term) >= 2 and term not in STOPWORDS
    }


def idf(document_frequency: int, documents: int) -> float:
    """Smoothed inverse document frequency; deterministic and stdlib-only."""
    if documents <= 0:
        return 1.0
    return math.log((documents + 1) / (document_frequency + 0.5)) + 1.0


def table_cells(row: str) -> list[str]:
    """Split a Markdown table row on unescaped pipes and unescape `\\|`.

    `_cell` escapes a literal pipe as `\\|`; splitting on every `|` split that
    cell in two, so a source URL containing a pipe round-tripped as five cells
    and the entry could never validate again.
    """
    body = row.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    cells = re.split(r"(?<!\\)\|", body)
    return [cell.replace("\\|", "|").strip() for cell in cells]


def cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()


def link_text(value: str) -> str:
    return value.replace("[", "(").replace("]", ")").replace("\n", " ").strip()


def parse_fields(text: str) -> dict[str, str]:
    """`- **Label:** value` bullets, keyed case-insensitively by label."""
    fields: dict[str, str] = {}
    for match in re.finditer(r"(?m)^-\s*\*\*([^*]+):\*\*\s*(.*)$", text):
        label = match.group(1).strip().casefold()
        fields[label] = match.group(2).strip()
    return fields


def field(text: str, name: str) -> str:
    return parse_fields(text).get(name.casefold(), "")


def parse_sections(text: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        heading = re.match(r"^##\s+(.+?)\s*$", line)
        if heading:
            current = heading.group(1)
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return {name: "\n".join(lines).strip() for name, lines in sections.items()}


def source_rows(section: str) -> list[str]:
    rows: list[str] = []
    seen_separator = False
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = table_cells(line)
        if not seen_separator:
            if cells and all(re.fullmatch(r":?-{3,}:?", value) for value in cells):
                seen_separator = True
            continue
        rows.append(line)
    return rows if seen_separator else []


def heading_name(text: str, fallback: str = "") -> str:
    heading = next((line[2:].strip() for line in text.splitlines()
                    if line.startswith("# ")), fallback)
    return heading[:-len(" Context")] if heading.endswith(" Context") else heading


def parse_date(value: str) -> date | None:
    match = _DATE_RE.search(value or "")
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def future_date(days: int = DEFAULT_REVALIDATE_DAYS, today: date | None = None) -> str:
    return ((today or date.today()) + timedelta(days=days)).isoformat()


def resolve_today(value: str | date | None = None) -> date:
    """The date a command should use, with an explicit override.

    Freshness used to read `date.today()` directly, so `check --strict` could
    flip on a calendar boundary and a test could not pin the date it asserted.
    An explicit value is validated instead of silently falling back to the
    wall clock.
    """
    if isinstance(value, date):
        return value
    if not value:
        return date.today()
    parsed = parse_date(value)
    if parsed is None:
        raise ContextError(
            f"today must be a YYYY-MM-DD date: {value!r}")
    return parsed


# --------------------------------------------------------------------------
# Sources: classification and display-name derivation
# --------------------------------------------------------------------------

def _registrable_label(host: str) -> str:
    labels = [label for label in host.split(".") if label]
    if len(labels) < 2:
        return labels[0] if labels else host
    suffix = ".".join(labels[-2:])
    if suffix in MULTI_LABEL_SUFFIXES:
        return labels[-3] if len(labels) >= 3 else labels[-2]
    return labels[-2]


def classify_source(source: str) -> tuple[str, str, str]:
    """Return (template type, identity, canonical source) for a URL or path.

    `identity` is the stable display/name seed: the repository name for a repo
    URL, the registrable domain for documentation, and the resolved path for a
    local source. `canonical` is what belongs in the Sources table.
    """
    source = source.strip()
    if not source:
        raise ContextError("--source is required")
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", source):
        parsed = urlparse(source)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ContextError("only http(s) URLs can be analyzed")
        host = parsed.netloc.rsplit("@", 1)[-1].split(":", 1)[0].casefold()
        host = host.removeprefix("www.")
        path = unquote(parsed.path)
        if host in REPO_HOSTS:
            segments = [part for part in path.split("/") if part]
            if not segments:
                return "repository", host, source
            # /owner/repo, /owner/repo.git and /owner/repo/issues/3 all name
            # the repository, never the issue number or the owner.
            repo = segments[1] if len(segments) > 1 else segments[0]
            return "repository", repo.removesuffix(".git"), source
        lowered = path.casefold()
        if any(marker in lowered for marker in
               ("/releases", "/release-notes", "/changelog", "/changes")):
            return "release notes", _registrable_label(host), source
        if any(marker in lowered for marker in ("/docs", "/documentation", "/api")):
            return "official docs", _registrable_label(host), source
        return "official docs", _registrable_label(host), source
    path = Path(source).expanduser()
    if not path.exists():
        raise ContextError(f"source path does not exist: {source}")
    if path.is_file():
        return "supplied file", str(path.resolve()), source
    if path.is_dir():
        return "supplied path", str(path.resolve()), source
    raise ContextError(f"source is neither a file nor a directory: {source}")


_GENERIC_FILE_STEMS = frozenset({
    "readme", "index", "context", "changelog", "changes", "license", "licence",
    "contributing", "notes", "_template", "template",
})
_RECORD_STEM_RE = re.compile(r"^(?:e|q|adr|ir|sp|story)[-_]?\d+$", re.IGNORECASE)


def derive_name(source: str, kind: str, identity: str) -> str:
    """A human display name derived from the source, never a bare number.

    GitHub issue URLs used to yield "3" and `docs/experiments/E-001.md` yielded
    "e-001"; both are meaningless as registry names. A generic file name falls
    back to its parent directory, and a record-like stem keeps the parent's
    subject (`docs/experiments/retrieval/E-001.md` -> `retrieval`).
    """
    if kind == "repository":
        return identity.split("/")[-1] or identity
    if kind in ("official docs", "release notes"):
        return identity
    path = Path(identity)
    if kind == "supplied path":
        return path.name or "external-context"
    stem = path.stem
    folded = fold(stem)
    if (folded in _GENERIC_FILE_STEMS or _RECORD_STEM_RE.match(stem)
            or stem.isdigit()):
        parent = path.parent.name
        if parent and fold(parent) not in _GENERIC_FILE_STEMS:
            return parent
    return stem or path.parent.name or "external-context"


# --------------------------------------------------------------------------
# References: exact, prefix, commit-prefix, compound and semver ranges
# --------------------------------------------------------------------------

def normalize_ref(value: str) -> str:
    normalized = (value or "").strip()
    if re.fullmatch(r"[0-9a-fA-F]{7,64}", normalized):
        return normalized.casefold()
    return normalized[1:] if re.match(r"^v(?=\d)", normalized) else normalized


def _version_parts(value: str) -> tuple[int, int, int, str] | None:
    match = _SEMVER_RE.fullmatch(value.strip())
    if not match or match.group("patch") in ("x", "X", "*"):
        return None
    major = int(match.group("major"))
    minor = int(match.group("minor") or 0)
    patch = int(match.group("patch") or 0)
    pre = match.group("pre") or ""
    return major, minor, patch, pre


def _satisfies(version: str, spec: str) -> bool:
    """True when `version` satisfies the semver-like `spec`."""
    version = normalize_ref(version)
    spec = normalize_ref(spec)
    if version == spec:
        return True
    spec_match = _SEMVER_RE.fullmatch(spec)
    if not spec_match:
        return False
    major, minor, patch, pre = _version_parts(version) or (None, None, None, "")
    if major is None:
        return False
    spec_major = int(spec_match.group("major"))
    spec_minor = spec_match.group("minor")
    spec_patch = spec_match.group("patch")
    operator = spec_match.group("op") or ""
    if spec_patch in ("x", "X", "*"):
        return major == spec_major and (spec_minor is None or minor == int(spec_minor))
    if spec_minor is None:
        # Bare `1` means the 1.x line; bare `1.2` is handled below.
        return major == spec_major
    if spec_patch is None:
        # `1.2` is a compatible line, not an exact ref: 1.2.3 satisfies it,
        # 11.2.0 does not.
        return major == spec_major and minor == int(spec_minor)
    target = (spec_major, int(spec_minor), int(spec_patch))
    current = (major, minor, patch)
    if operator in ("^", "~"):
        if current < target:
            return False
        if operator == "~":
            return current[:2] == target[:2]
        if target[0] > 0:
            return current[0] == target[0]
        if target[1] > 0:
            return current[0] == 0 and current[1] == target[1]
        return current == target
    if operator == ">=":
        return current >= target
    if operator == ">":
        return current > target
    if operator == "<=":
        return current <= target
    if operator == "<":
        return current < target
    if operator == "=":
        return current == target
    if spec_match.group("pre"):
        return current == target and pre == (spec_match.group("pre") or "")
    return current == target


def ref_tokens(value: str) -> list[tuple[str, str]]:
    """Typed tokens from a compound ref (`v1.2.3 (commit abc1234)`).

    Type is one of `version`, `hex`, `name`. Operators/wildcards stay on the
    version token so `^1.2.3` is evaluated as a range, not compared literally.
    """
    tokens: list[tuple[str, str]] = []
    for part in re.split(r"[;,|]|\band\b", value or ""):
        part = part.strip()
        if not part:
            continue
        consumed = False
        for match in _HEX_RE.finditer(part):
            tokens.append(("hex", match.group(0).casefold()))
            consumed = True
        for match in re.finditer(
                r"(?:\^|~|>=|<=|>|<|=)?\s*v?\d+(?:\.\d+){0,3}"
                r"(?:\.(?:x|X|\*))?(?:-[0-9A-Za-z.-]+)?", part):
            tokens.append(("version", normalize_ref(match.group(0))))
            consumed = True
        if not consumed:
            tokens.append(("name", part))
    return tokens


def refs_match(requested: str, analyzed: str) -> bool:
    """Whether the requested project ref and the analyzed ref denote the same thing.

    Accepts exact refs, leading `v`, semver ranges (`^1.2.3`, `~1.2`, `1.2.x`,
    bare `1.2`, `>=1.0.0 <2.0.0`), commit prefixes of at least seven hex
    characters, and compound refs. Case is preserved for named refs and
    prerelease tags; hexadecimal commit IDs are case-insensitive.

    A range made of several version tokens is a conjunction: every bound must
    hold for the single version on the other side. Treating the tokens as
    independent alternatives let `>=1.0.0 <2.0.0` match 3.0.0 through its lower
    bound alone.
    """
    if not requested or not analyzed:
        return False
    left_tokens = ref_tokens(requested)
    right_tokens = ref_tokens(analyzed)
    left_versions = [value for kind, value in left_tokens if kind == "version"]
    right_versions = [value for kind, value in right_tokens if kind == "version"]
    left_is_range = (len(left_versions) > 1
                     and all(kind == "version" for kind, _value in left_tokens))
    right_is_range = (len(right_versions) > 1
                      and all(kind == "version" for kind, _value in right_tokens))
    if left_is_range and len(right_versions) == 1:
        return all(_satisfies(right_versions[0], spec) for spec in left_versions)
    if right_is_range and len(left_versions) == 1:
        return all(_satisfies(left_versions[0], spec) for spec in right_versions)
    for left_kind, left in left_tokens:
        for right_kind, right in right_tokens:
            if left_kind == right_kind == "name":
                if left == right:
                    return True
            elif left_kind == right_kind == "hex":
                if left == right or (min(len(left), len(right)) >= 7 and
                                     (left.startswith(right) or right.startswith(left))):
                    return True
            elif left_kind == right_kind == "version":
                if _satisfies(left, right) or _satisfies(right, left):
                    return True
    return False


# --------------------------------------------------------------------------
# Freshness
# --------------------------------------------------------------------------

def freshness(status: str, revalidate_after: str,
              today: date | None = None) -> str:
    """`fresh` | `stale` | `unknown`, derived from the entry, never stored.

    Storing freshness would let it drift from the dates it summarises. A
    `revalidating` entry is stale by definition: its last reviewed analysis is
    still readable, but it is no longer current evidence.
    """
    status = (status or "").casefold()
    if status in ("draft", "revalidating"):
        return "stale"
    if status == "superseded":
        return "stale"
    parsed = parse_date(revalidate_after)
    if parsed is None:
        return "unknown"
    return "stale" if parsed < (today or date.today()) else "fresh"


def needs_revalidation(status: str, revalidate_after: str,
                       today: date | None = None) -> bool:
    return freshness(status, revalidate_after, today) != "fresh"


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

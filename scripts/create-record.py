#!/usr/bin/env python3
"""Schema-validated record generator (and checker) for methodology records.

Hand-edited Markdown records drift: a missing field is discovered by the gate
minutes later, and a typo in a field label silently changes the record's
meaning. This CLI writes records from a declared schema, validates them before
and after writing, and stamps `- **Record Schema:** metodoloji.record/v1` so a
later check can tell an attested record from a hand-written one.

It does NOT replace the experiment gate: `run_experiment.py --run` is still the
only writer of a decision. This tool only produces the DRAFT.

One id, one file: a new draft is refused when another file already claims the
same canonical record id (`E-001.md`, `E-001-gate.md` and `E-1.md` are one
record). The check IS the write gate's (`modules.guard`), not a local filename
rule — this script writes the file itself, so the guard sees the process, not
the write target, and a second spelling of the identity here would be the
unchecked half.

Usage (experiment / contract):
    python scripts/create-record.py --kind experiment --id E-119 \
        --title "..." --theory "..." --hypothesis 'H-119: "x >= 0.90"' \
        --metrics "x >= 0.90" --design "..." --scope "src/**" \
        --lineage none --raw-samples docs/experiments/E-119/raw/samples.jsonl

Usage (contract):
    ... --kind contract ... --census "the closed input set ..."

Usage (IR/SP/PR):
    python scripts/create-record.py --kind ir --id IR-018 --title "..." \
        --field "Research inputs=E-119" \
        --field "Success criteria=the gate refuses a partial contract"

Usage (validate an existing attested record):
    python scripts/create-record.py --check docs/experiments/E-119.md
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

SCHEMA_MARKER = "metodoloji.record/v1"
SCHEMA_FIELD = "Record Schema"

# The engine carries the identity and duplicate rules; import it lazily so the
# --check/--status-regex paths stay dependency-free.
_ENGINE_DIR = pathlib.Path(__file__).resolve().parent.parent / "hooks" / "engine"

E_REQUIRED = (
    "Date", "Status", "Theory", "Hypothesis", "Measurement Metrics",
    "Experiment Design", "Code Scope", "Lineage", "Raw Samples",
)
E_CONTRACT_REQUIRED = E_REQUIRED + ("Census",)

_SCHEMAS = {
    "experiment": {
        "prefix": "E",
        "dir": ("docs", "experiments"),
        "title": ("## Experiment:", "{id} — {title}"),
        "required": E_REQUIRED,
        "optional": ("Record Kind", "Control Command", "Sample Size n"),
        "statuses": ("planned", "completed", "REJECTED"),
    },
    "contract": {
        "prefix": "E",
        "dir": ("docs", "experiments"),
        "title": ("## Experiment:", "{id} — {title}"),
        "required": E_CONTRACT_REQUIRED,
        "optional": ("Record Kind", "Control Command", "Sample Size n"),
        "statuses": ("planned", "completed", "REJECTED"),
    },
    "ir": {
        "prefix": "IR",
        "dir": ("docs", "development"),
        "title": ("# Implementation Readiness:", "{id} — {title}"),
        "required": ("Date", "Status", "Research inputs", "Success criteria",
                     "Decision", "Next step"),
        "optional": ("Design documents", "Instrumentation gaps",
                     "Technical dependencies", "Risk assessment", "Gaps"),
        "statuses": ("READY", "NOT-READY", "BLOCKED"),
    },
    "sp": {
        "prefix": "SP",
        "dir": ("docs", "development"),
        "title": ("# Sprint:", "{id} — {title}"),
        "required": ("Date", "Status", "Readiness input", "Sprint goal", "Stories"),
        "optional": ("Capacity", "Technical debt", "Blockers", "Dependencies"),
        "statuses": ("planned", "active", "completed", "cancelled"),
    },
    "pr": {
        "prefix": "PR",
        "dir": ("docs", "development"),
        "title": ("# Production Readiness:", "{id} — {title}"),
        "required": ("Date", "Status", "Release type", "Version", "Stories"),
        "optional": ("Release scope",),
        "statuses": ("preparing", "ready", "deployed", "rolled-back"),
    },
}

# --- the status vocabulary every surface reads -------------------------------
# `_SCHEMAS[...]["statuses"]` is the CANONICAL set: a writer is told to use one
# of these, and `validate_text` refuses anything else. `check-methodology.sh`
# CHECK 4 used to carry its own hand-typed alternation (`planned|PENDING|
# BEKLİYOR`), so the two were two copies that only agreed by hand — the same
# shape E-047/E-048 closed for the version manifests. The audit now asks this
# file (`--status-regex`) instead of restating it.
#
# `_DRAFT_ALIASES` are the draft words that are NOT canonical but still mean
# "work in progress". They exist because a non-canonical draft word used to make
# a record unclassifiable, and an unclassifiable record fails the pytest
# falsifier *while it is being measured* — so the record rejected itself and
# could not be re-run once decided (E-110, DW-9). A draft synonym is pending
# work, not an error; the error class stays reserved for a word that is neither
# a draft nor a decision (a typo, or a missing status).
_DRAFT_ALIASES = ("PENDING", "BEKLİYOR", "proposed", "draft", "in-progress",
                  "in progress", "active")

# Words that are a DECISION, never a draft. Kept out of the pending pattern so
# `Status: completed` on a gate-written record is not read as pending.
_TERMINAL_STATUSES = frozenset({"completed", "REJECTED", "READY", "DEPLOYED",
                                "ROLLED-BACK", "CANCELLED", "BLOCKED"})


def status_regex(kind: str) -> str:
    """POSIX-ERE alternation of every PENDING status word for `kind`.

    Derived, never restated: the schema's non-terminal statuses plus the draft
    aliases. A terminal status is excluded so the audit's approved/rejected
    branches keep first refusal (the shell applies them in order).
    """
    spec = _SCHEMAS[kind]
    words: list[str] = []
    for word in tuple(spec["statuses"]) + _DRAFT_ALIASES:
        if word.lower() in {w.lower() for w in _TERMINAL_STATUSES}:
            continue
        if word not in words:
            words.append(word)
    # Longest first so `in progress` is not shadowed by a prefix, and escape
    # nothing: the vocabulary is [A-Za-z -] only, safe in an ERE.
    words.sort(key=len, reverse=True)
    return "|".join(words)


_PLACEHOLDER_RE = re.compile(r"^\s*(?:<.*>|\[.*\]|—\s*\(.*\)|TBD|PLACEHOLDER)\s*$",
                             re.IGNORECASE)
_CONTRACT_CLAIM_RE = re.compile(r"(?:==|>=)\s*1(?:\.0+)?\s*[\"']?\s*$")
_EID_RE = re.compile(r"E-[\w.\-]*\d[\w.\-]*$")


def _field_line(label: str, value: str) -> str:
    return f"- **{label}:** {value}".rstrip()


def _parse_fields(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for line in text.splitlines():
        m = re.match(r"^\s*-\s*\*\*([^*]+):\*\*\s*(.*)$", line)
        if m:
            out.setdefault(m.group(1).strip(), []).append(m.group(2).strip())
    return out


def _one(fields: dict[str, list[str]], key: str) -> str:
    values = fields.get(key) or []
    return values[0].strip() if values else ""


# Labels that legitimately carry more than one line: a re-measurement names
# every later record that measured the same claim (E-128) and an amended plan
# keeps the hash it superseded (E-158, E-159). Everything else is one label,
# one value — two `- **Status:**` lines are read differently by `_one()` (first
# match) and by a line scan such as check-methodology CHECK 4 (E-171, DW-11).
_APPEND_ONLY_LABELS = frozenset({"Re-Measured-By", "Final Plan Hash"})


def _duplicate_labels(kind: str, fields: dict[str, list[str]]) -> list[str]:
    """Schema-owned labels that carry more than one value."""
    spec = _SCHEMAS[kind]
    owned = set(spec["required"]) | set(spec["optional"]) | {SCHEMA_FIELD}
    return sorted(label for label in owned - _APPEND_ONLY_LABELS
                  if len(fields.get(label) or []) > 1)


def validate_text(kind: str, text: str) -> list[str]:
    """Schema issues for a rendered record (empty list = valid)."""
    spec = _SCHEMAS[kind]
    fields = _parse_fields(text)
    issues: list[str] = []
    for label in _duplicate_labels(kind, fields):
        count = len(fields[label])
        issues.append(f"duplicate field '{label}' ({count} values) — one label "
                      f"carries one value; use an indented continuation line "
                      f"for a multi-line value")
    for need in spec["required"]:
        value = _one(fields, need)
        if not value or _PLACEHOLDER_RE.match(value):
            issues.append(f"missing or placeholder field '{need}'")
    status = _one(fields, "Status")
    if status and status not in spec["statuses"]:
        issues.append(f"Status '{status}' is not one of {', '.join(spec['statuses'])}")
    if kind in ("experiment", "contract"):
        rid = _one(fields, "Experiment")
        if not rid:
            m = re.search(r"^##\s+Experiment:\s*([\w.\-]+)", text, re.M)
            rid = m.group(1) if m else ""
        if not _EID_RE.match(rid):
            issues.append("Experiment id must look like E-<number>")
        hypothesis = _one(fields, "Hypothesis")
        if '"' not in hypothesis:
            issues.append("Hypothesis must carry the quoted claim, e.g. H-001: \"x >= 0.90\"")
        declared = (_one(fields, "Record Kind") or "experiment").strip().lower()
        if declared not in ("experiment", "contract"):
            issues.append(f"Record Kind '{declared}' must be experiment or contract")
        if kind == "contract" or declared == "contract":
            if not _CONTRACT_CLAIM_RE.search(hypothesis):
                issues.append("contract Hypothesis must claim 100% ('== 1.0' or '>= 1.0')")
            census = _one(fields, "Census")
            if len(census) < 40:
                issues.append("contract Census must name the closed set (>= 40 chars)")
    return issues


def render(kind: str, rid: str, title: str, values: dict[str, list[str]]) -> str:
    spec = _SCHEMAS[kind]
    head, title_tpl = spec["title"]
    lines = [f"{head} {title_tpl.format(id=rid, title=title)}", ""]
    if kind in ("experiment", "contract"):
        # Canonical E order, with the kind made explicit.
        order = ["Date", "Status", "Record Kind", "Theory", "Hypothesis",
                 "Measurement Metrics", "Experiment Design", "Sample Size n",
                 "Code Scope", "Lineage", "Control Command", "Falsifiability",
                 "Raw Samples", "Raw Sample Evidence", "Measure", "Census",
                 "Raw Results", "Uncertainty", "Metric", "Decision",
                 "Gate Evidence", "Witness", "Next Step"]
        defaults = {
            "Record Kind": kind,
            "Falsifiability": "<gate writes>",
            "Raw Sample Evidence": "<gate writes>",
            "Measure": "categorical",
            "Raw Results": "<gate writes>",
            "Uncertainty": "<gate writes>",
            "Metric": "<gate writes>",
            "Decision": "<gate writes>",
            "Gate Evidence": "<gate writes>",
            "Witness": "<gate writes>",
            "Next Step": "<gate writes>",
        }
    else:
        order = list(spec["required"]) + list(spec["optional"])
        defaults = {}
    seen: set[str] = set()
    for label in order:
        vals = values.get(label) or ([defaults[label]] if label in defaults else [])
        if not vals:
            continue
        seen.add(label)
        # Multi-line values render as indented continuation bullets.
        for value in vals:
            if "\n" in value:
                first, *rest = value.splitlines()
                lines.append(_field_line(label, first))
                for cont in rest:
                    lines.append("  - " + cont.strip())
            else:
                lines.append(_field_line(label, value))
    # Any caller-supplied field the schema does not order explicitly.
    for label, vals in values.items():
        if label in seen:
            continue
        for value in vals:
            lines.append(_field_line(label, value))
    lines.append(_field_line(SCHEMA_FIELD, SCHEMA_MARKER))
    if kind in ("experiment", "contract"):
        lines += ["", "## Implementation Plan (GRP)", "",
                  _field_line("Planned Files", _one(values, "Code Scope")),
                  _field_line("Planned Tests", "—"),
                  _field_line("Known Gaps", "—"),
                  _field_line("Amendments", "none")]
    return "\n".join(lines) + "\n"


def _duplicate_claim(root: pathlib.Path, out: pathlib.Path) -> str:
    """The write gate's duplicate reason for `out`, or '' when the id is free.

    Delegated rather than restated: the guard's `_check_duplicate_record_ids`
    owns the canonical identity (state.record_id_of_stem), the four scan roots
    and the budget, so the generator and the write gate cannot disagree about
    what counts as a second file for one record.
    """
    if str(_ENGINE_DIR) not in sys.path:
        sys.path.insert(0, str(_ENGINE_DIR))
    from modules.guard import _check_duplicate_record_ids
    rel = out.relative_to(root).as_posix()
    unique, reason = _check_duplicate_record_ids(str(root), rel)
    return "" if unique else reason


def _next_id(kind: str, root: pathlib.Path) -> str:
    spec = _SCHEMAS[kind]
    base = root.joinpath(*spec["dir"])
    nums = []
    if base.is_dir():
        for f in base.glob(f"{spec['prefix']}-*.md"):
            m = re.match(rf"{spec['prefix']}-(\d+)", f.name)
            if m:
                nums.append(int(m.group(1)))
    return f"{spec['prefix']}-{max(nums, default=0) + 1:03d}"


def _check(path: pathlib.Path) -> int:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"ERROR: cannot read {path}: {exc}", file=sys.stderr)
        return 2
    fields = _parse_fields(text)
    if _one(fields, SCHEMA_FIELD) != SCHEMA_MARKER:
        print(f"SKIP: {path} has no '{SCHEMA_FIELD}: {SCHEMA_MARKER}' attestation "
              f"(hand-written legacy record)")
        return 0
    if "## Experiment:" in text:
        kind = "contract" if (_one(fields, "Record Kind").lower() == "contract") \
            else "experiment"
    elif "# Implementation Readiness:" in text:
        kind = "ir"
    elif "# Sprint:" in text:
        kind = "sp"
    elif "# Production Readiness:" in text:
        kind = "pr"
    else:
        print(f"ERROR: {path}: cannot determine record kind from its heading",
              file=sys.stderr)
        return 1
    issues = validate_text(kind, text)
    if issues:
        print(f"FAIL: {path} ({kind})")
        for issue in issues:
            print(f"  - {issue}")
        return 1
    print(f"OK: {path} ({kind}) satisfies {SCHEMA_MARKER}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Schema-validated methodology record CLI")
    ap.add_argument("--check", metavar="PATH", help="validate an attested record")
    ap.add_argument("--status-regex", metavar="KIND", choices=sorted(_SCHEMAS),
                    help="print the pending-status ERE for KIND (the audit's "
                         "single source for that vocabulary)")
    ap.add_argument("--kind", choices=sorted(_SCHEMAS), default="experiment")
    ap.add_argument("--id", default="", help="record id (default: next free)")
    ap.add_argument("--title", default="", help="record title")
    ap.add_argument("--field", action="append", default=[],
                    metavar="NAME=VALUE", help="field value (repeatable)")
    ap.add_argument("--project-root", default=".")
    # Convenience flags for the E-record fields.
    for flag in ("date", "theory", "hypothesis", "metrics", "design", "scope",
                 "lineage", "raw-samples", "census", "control"):
        ap.add_argument("--" + flag, default="")
    ap.add_argument("--force", action="store_true", help="overwrite an existing file")
    args = ap.parse_args()

    if args.status_regex:
        print(status_regex(args.status_regex))
        return 0
    if args.check:
        return _check(pathlib.Path(args.check))

    root = pathlib.Path(args.project_root).resolve()
    kind = args.kind
    if not args.title.strip():
        print("ERROR: --title is required", file=sys.stderr)
        return 2
    rid = args.id.strip() or _next_id(kind, root)
    spec = _SCHEMAS[kind]
    if not re.fullmatch(rf"{spec['prefix']}-[\w.\-]*\d[\w.\-]*", rid):
        print(f"ERROR: --id must look like {spec['prefix']}-<number>", file=sys.stderr)
        return 2

    values: dict[str, list[str]] = {}
    explicit: set[str] = set()
    conflicts: list[str] = []

    def seed(label: str, value: str) -> None:
        """The CLI's own default for a label the caller did not supply."""
        if value and value.strip() and label not in values:
            values[label] = [value.strip()]

    def put(label: str, value: str) -> None:
        """A caller-supplied value: it OWNS its label.

        `--field Date=2020-01-02` used to be APPENDED after the auto-seeded
        Date, so the record carried two Date lines, two Status lines and a
        `Lineage: none` ahead of the real lineage — and passed --check, because
        the schema had no duplicate rule (E-171's generated draft, DW-11).
        An explicit value now replaces the seed, and a second explicit value
        for the same label is refused instead of silently dropped: keeping
        either one would be the same ambiguity under a quieter rule.
        """
        label = label.strip()
        if not (value and value.strip()):
            return
        if label in explicit:
            conflicts.append(label)
            return
        values[label] = [value.strip()]
        explicit.add(label)

    from datetime import date
    if args.date:
        put("Date", args.date)
    else:
        seed("Date", date.today().isoformat())
    default_status = {"experiment": "planned", "contract": "planned",
                      "ir": "NOT-READY", "sp": "planned", "pr": "preparing"}[kind]
    seed("Status", default_status)
    if kind in ("experiment", "contract"):
        put("Theory", args.theory)
        put("Hypothesis", args.hypothesis)
        put("Measurement Metrics", args.metrics)
        put("Experiment Design", args.design)
        put("Code Scope", args.scope)
        if args.lineage:
            put("Lineage", args.lineage)
        else:
            seed("Lineage", "none")
        put("Raw Samples", args.raw_samples)
        put("Census", args.census)
        put("Control Command", args.control)
    for raw in args.field:
        if "=" not in raw:
            print(f"ERROR: --field must be NAME=VALUE, got {raw!r}", file=sys.stderr)
            return 2
        name, value = raw.split("=", 1)
        put(name, value)

    if conflicts:
        for label in dict.fromkeys(conflicts):
            print(f"ERROR: field '{label}' was supplied more than once "
                  f"(--field and/or a convenience flag); a record carries one "
                  f"value per field", file=sys.stderr)
        return 2

    text = render(kind, rid, args.title.strip(), values)
    issues = validate_text(kind, text)
    if issues:
        print("REFUSED: the draft does not satisfy the schema:", file=sys.stderr)
        for issue in issues:
            print(f"  - {issue}", file=sys.stderr)
        return 1

    out = root.joinpath(*spec["dir"]) / f"{rid}.md"
    if out.exists() and not args.force:
        print(f"ERROR: {out} already exists (use --force to overwrite)", file=sys.stderr)
        return 2
    if not out.exists():
        claimed = _duplicate_claim(root, out)
        if claimed:
            print(f"ERROR: {claimed} Pick a free id with --id; --force only "
                  f"overwrites this exact path.", file=sys.stderr)
            return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"created {out} ({kind}, {SCHEMA_MARKER})")
    if kind in ("experiment", "contract"):
        print("next: run the gate with --dry-run, then --run (the gate writes the decision)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

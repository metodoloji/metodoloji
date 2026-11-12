#!/usr/bin/env python3
"""Schema-validated record generator (and checker) for methodology records.

Hand-edited Markdown records drift: a missing field is discovered by the gate
minutes later, and a typo in a field label silently changes the record's
meaning. This CLI writes records from a declared schema, validates them before
and after writing, and stamps `- **Record Schema:** metodoloji.record/v1` so a
later check can tell an attested record from a hand-written one.

It does NOT replace the experiment gate: `run_experiment.py --run` is still the
only writer of a decision. This tool only produces the DRAFT.

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


def validate_text(kind: str, text: str) -> list[str]:
    """Schema issues for a rendered record (empty list = valid)."""
    spec = _SCHEMAS[kind]
    fields = _parse_fields(text)
    issues: list[str] = []
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
    def put(label: str, value: str) -> None:
        if value and value.strip():
            values.setdefault(label, []).append(value.strip())

    from datetime import date
    put("Date", args.date or date.today().isoformat())
    default_status = {"experiment": "planned", "contract": "planned",
                      "ir": "NOT-READY", "sp": "planned", "pr": "preparing"}[kind]
    put("Status", default_status)
    if kind in ("experiment", "contract"):
        put("Theory", args.theory)
        put("Hypothesis", args.hypothesis)
        put("Measurement Metrics", args.metrics)
        put("Experiment Design", args.design)
        put("Code Scope", args.scope)
        put("Lineage", args.lineage or "none")
        put("Raw Samples", args.raw_samples)
        put("Census", args.census)
        put("Control Command", args.control)
    for raw in args.field:
        if "=" not in raw:
            print(f"ERROR: --field must be NAME=VALUE, got {raw!r}", file=sys.stderr)
            return 2
        name, value = raw.split("=", 1)
        put(name.strip(), value.strip())

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
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"created {out} ({kind}, {SCHEMA_MARKER})")
    if kind in ("experiment", "contract"):
        print("next: run the gate with --dry-run, then --run (the gate writes the decision)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Deferred-work ledger CLI — check, migrate, and append entries mechanically.

The ledger (`{implementation_artifacts}/deferred-work.md`) is the one place a
review, a quick-dev run or a dev-auto run records a finding it deliberately did
not fix. Three skills write to it and a human closes entries, so the format has
exactly one owner: `./references/deferred-work-format.md` next to this script.
This CLI is that owner's mechanical half — the part that must not depend on an
LLM remembering a shape.

Usage:
    python3 deferred-work.py --check [--json] [--project-root R]
    python3 deferred-work.py --migrate [--write] [--project-root R]
    python3 deferred-work.py --add --title T --origin O [--location L]
                             [--severity S] [--reason X] [--project-root R]

`--check` prints the inventory and every problem it can prove: malformed
entries, duplicate DW numbers, an unterminated fence, and un-migrated legacy
sections. Exit 0 when the ledger is sound (legacy is a warning, not an error),
1 when it is not.

`--migrate` converts `## Deferred from: <origin>` bullet sections into canonical
`DW-N` entries. It is deterministic and idempotent: the same input always
yields the same output, and a second run changes nothing because the legacy
heading is gone. Nothing is lost — the original bullet text is preserved
verbatim in `reason:` when the title is a prefix of it. Dry-run by default;
`--write` performs the rewrite.

`--add` appends one entry with the next free number and the documented dedupe
check: an existing entry with the same normalized title gains a `seen-again:`
line instead of a duplicate.

Read-only by default, stdlib only, no network.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "hooks" / "engine"))

from modules import state  # noqa: E402  (the single parser every surface reads)

_LEGACY_HEADING = re.compile(r"^##\s+Deferred from:\s*(.+?)\s*$")
_MIGRATED_HEADING = re.compile(r"^##\s+Migrated from:\s*(.+?)\s*$")
_ANY_H2 = re.compile(r"^#{1,2}\s")
_BULLET = re.compile(r"^\s*-\s+(\S.*)$")
_BACKTICK_PATH = re.compile(r"`([^`\s]+:\d+(?:-\d+)?)`")
_DATE_TAIL = re.compile(r"\((\d{4}-\d{2}-\d{2})\)\s*$")
_DW_TITLE = re.compile(r"^###\s+DW-(\d+)\s*:\s*(.*?)\s*$")
_TITLE_MAX = 72


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _strip_path_paren(title: str) -> tuple[str, str]:
    r"""Drop a trailing `(path, path)` group from a title, returning it.

    Legacy bullets routinely end with their file references in parentheses
    (`... drops a channel (watcher, pattern) (\`blackboard_fold.py:224-226\`)`).
    The first reference already lands in `location:`; leaving the group in the
    title makes a 100-character heading. The group is handed back so the caller
    can keep it in `reason:` — the entry stays lossless.
    """
    match = re.search(r"\s*\(([^()]*`[^()]*)\)\s*$", title)
    if not match:
        return title, ""
    return title[:match.start()].rstrip(), match.group(1).strip()


def _split_title(text: str) -> tuple[str, str]:
    """(title, remainder) for one legacy bullet — deterministic, lossless.

    Preference order keeps titles readable without guessing: a short clause
    before a semicolon, else the first sentence, else a word-boundary cut.
    The caller stores `title + remainder` back into the entry, so no text is
    dropped whichever branch fires.
    """
    text = _collapse(text)
    semi = text.find("; ")
    if 0 < semi <= 60:
        return text[:semi].rstrip(".,;:"), text[semi + 2:].strip()
    dot = text.find(". ")
    if 0 < dot <= 120:
        return text[:dot].rstrip("."), text[dot + 2:].strip()
    if len(text) > _TITLE_MAX:
        window = text[:_TITLE_MAX]
        # Prefer a clause boundary inside the window: cutting at a word
        # boundary alone split "paths, so the recorded" from "sha256 cannot be
        # re-derived", which reads as two half-sentences.
        for separator in (", ", " — ", ": "):
            cut = window.rfind(separator)
            if cut >= 30:
                return window[:cut].rstrip(".,;:"), text[cut + len(separator):].strip()
        cut = window.rsplit(" ", 1)[0]
        return cut.rstrip(".,;:"), text[len(cut):].strip()
    return text.rstrip(".,;:"), ""


def _location(text: str) -> str:
    match = _BACKTICK_PATH.search(text)
    return match.group(1) if match else "n/a"


def _origin(heading: str) -> tuple[str, str]:
    """(origin, date) from `## Deferred from: code review of S-001 (2026-11-03)`."""
    label = _collapse(heading)
    date = ""
    match = _DATE_TAIL.search(label)
    if match:
        date = match.group(1)
        label = label[:match.start()].strip()
    return (f"{label}, {date}" if date else label), date


def _entry_block(seq: int, title: str, origin: str, location: str,
                 reason: str, severity: str | None = None) -> list[str]:
    block = [f"### DW-{seq}: {title}", "", f"origin: {origin}",
             f"location: {location}"]
    if severity:
        block.append(f"severity: {severity}")
    block.extend([f"reason: {reason}", "status: open", ""])
    return block


def migrate_text(text: str) -> tuple[str, int]:
    """Return (new_text, entries_written). Deterministic and idempotent."""
    lines = text.splitlines()
    body, _ = state._strip_fences(text)
    existing = [int(m.group(1)) for m in state._DW_ENTRY_RE.finditer(body)]
    next_seq = (max(existing) + 1) if existing else 1

    out: list[str] = []
    written = 0
    index = 0
    while index < len(lines):
        heading = _LEGACY_HEADING.match(lines[index])
        if not heading:
            out.append(lines[index])
            index += 1
            continue
        origin, _date = _origin(heading.group(1))
        # Collect this section's bullets; stop at the next level 1-2 heading.
        end = index + 1
        bullets: list[str] = []
        while end < len(lines) and not _ANY_H2.match(lines[end]):
            bullet = _BULLET.match(lines[end])
            if bullet:
                bullets.append(bullet.group(1))
            end += 1
        if not bullets:
            out.append(lines[index])
            index += 1
            continue
        out.append(f"## Migrated from: {origin}")
        out.append("")
        for raw in bullets:
            title, remainder = _split_title(raw)
            title, path_note = _strip_path_paren(title)
            # Lossless: whatever the split dropped (a trailing path group, or
            # the whole bullet when the title consumed it) is carried in
            # `reason:`, so title + reason always reconstructs the original.
            tail = " ".join(part for part in (path_note, remainder) if part)
            reason = tail or _collapse(raw)
            out.extend(_entry_block(next_seq, title, origin,
                                    _location(raw), reason))
            next_seq += 1
            written += 1
        index = end
    if not written:
        return text, 0
    return "\n".join(out).rstrip("\n") + "\n", written


def check(project_root: pathlib.Path, as_json: bool) -> int:
    info = state.deferred_work_inventory(project_root)
    problems: list[str] = []
    if info["error"]:
        problems.append(f"ledger unreadable: {info['error']}")
    if info["malformed"]:
        problems.append(f"{info['malformed']} malformed entr"
                        f"{'y' if info['malformed'] == 1 else 'ies'} "
                        "(no status, or a status that is neither open nor done)")
    if info["duplicate_seq"]:
        problems.append(f"{info['duplicate_seq']} duplicate DW number(s) — "
                        "entries are never renumbered, so two findings share an id")
    warnings: list[str] = []
    if info["legacy_items"]:
        warnings.append(f"{info['legacy_items']} un-migrated legacy item(s) — "
                        "run `--migrate --write`")
    if as_json:
        print(json.dumps({"inventory": info, "problems": problems,
                          "warnings": warnings}, indent=2, sort_keys=True))
    else:
        print(f"ledger   : {info['path'] or '(none)'}")
        print(f"entries  : {info['total']} total, {info['open']} open, "
              f"{info['done']} done, {info['malformed']} malformed")
        print(f"legacy   : {info['legacy_items']} un-migrated item(s)")
        print(f"open work: {info['open_total']}")
        print(f"newest   : DW-{info['newest_seq']}" if info["newest_seq"]
              else "newest   : (none)")
        for line in warnings:
            print(f"[WARN] {line}")
        for line in problems:
            print(f"[ERROR] {line}")
        if not problems and not warnings:
            print("[OK] ledger is canonical and sound")
    return 1 if problems else 0


def migrate(project_root: pathlib.Path, write: bool) -> int:
    path = state.deferred_work_path(project_root)
    if path is None:
        print("[OK] no ledger — nothing to migrate")
        return 0
    text = path.read_text(encoding="utf-8")
    new_text, written = migrate_text(text)
    if not written:
        print(f"[OK] nothing to migrate — {path} carries no legacy section")
        return 0
    before = state.deferred_work_inventory(project_root)
    if not write:
        print(f"[DRY RUN] would convert {written} legacy item(s) in {path}")
        for line in new_text.splitlines()[:40]:
            print(f"  | {line}")
        print("  | ...")
        print("  pass --write to apply")
        return 0
    path.write_text(new_text, encoding="utf-8")
    after = state.deferred_work_inventory(project_root)
    print(f"[OK] migrated {written} item(s) in {path}")
    print(f"     before: {before['total']} entries / {before['legacy_items']} legacy"
          f" · after: {after['total']} entries / {after['legacy_items']} legacy")
    if after["legacy_items"] or after["malformed"] or after["duplicate_seq"]:
        print("[WARN] the ledger still reports problems — run --check")
        return 1
    return 0


def _normalized(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def add(project_root: pathlib.Path, title: str, origin: str, location: str,
        severity: str | None, reason: str) -> int:
    path = state.deferred_work_path(project_root)
    if path is None:
        print("[ERROR] no ledger — run init, or create "
              "docs/development/native/deferred-work.md from the template")
        return 1
    text = path.read_text(encoding="utf-8")

    # Dedupe (format §"Before appending"): the same normalized title is the
    # same finding, whatever the wording of the body. Offsets are computed on
    # the raw text, never on the fence-stripped body — the two only coincide
    # when the ledger carries no fenced example.
    target = _normalized(title)
    blocks = list(state._DW_ENTRY_RE.finditer(text))
    for index, match in enumerate(blocks):
        heading = _DW_TITLE.match(text[match.start():text.find("\n", match.start())]
                                  if text.find("\n", match.start()) > 0 else "")
        if not heading or _normalized(heading.group(2)) != target:
            continue
        end = blocks[index + 1].start() if index + 1 < len(blocks) else len(text)
        status = re.search(r"^status:.*$", text[match.end():end], re.MULTILINE)
        if not status:
            break  # a status-less entry cannot take a seen-again line
        insert_at = match.end() + status.end()
        seen = f"\nseen-again: {_dt.date.today().isoformat()} ({origin})"
        text = text[:insert_at] + seen + text[insert_at:]
        path.write_text(text, encoding="utf-8")
        print(f"[OK] duplicate — added `seen-again` to DW-{match.group(1)}")
        return 0

    existing = [int(m.group(1)) for m in blocks]
    seq = (max(existing) + 1) if existing else 1
    block = _entry_block(seq, _collapse(title), _collapse(origin),
                         _collapse(location), _collapse(reason), severity)
    text = text.rstrip("\n") + "\n\n" + "\n".join(block)
    path.write_text(text, encoding="utf-8")
    print(f"[OK] appended DW-{seq}: {title}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Check, migrate and append deferred-work ledger entries.")
    action = ap.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true",
                        help="validate the ledger and print the inventory")
    action.add_argument("--migrate", action="store_true",
                        help="convert legacy sections into canonical entries")
    action.add_argument("--add", action="store_true", help="append one entry")
    ap.add_argument("--write", action="store_true",
                    help="--migrate: apply (default is a dry run)")
    ap.add_argument("--json", action="store_true", help="--check: JSON output")
    ap.add_argument("--project-root", default=".",
                    help="project root (default: .)")
    ap.add_argument("--title", help="--add: one-line title")
    ap.add_argument("--origin", help="--add: workflow + artifact + date")
    ap.add_argument("--location", default="n/a",
                    help="--add: file:line, component, or n/a")
    ap.add_argument("--severity", choices=("critical", "high", "medium", "low"),
                    help="--add: severity (optional)")
    ap.add_argument("--reason", default="",
                    help="--add: why it was deferred")
    args = ap.parse_args(argv)

    root = pathlib.Path(args.project_root)
    if args.check:
        return check(root, args.json)
    if args.migrate:
        return migrate(root, args.write)
    missing = [name for name in ("title", "origin")
               if not getattr(args, name)]
    if missing:
        print(f"[ERROR] --add needs {', '.join('--' + m for m in missing)}",
              file=sys.stderr)
        return 1
    return add(root, args.title, args.origin, args.location, args.severity,
               args.reason or f"deferred from {args.origin}")


if __name__ == "__main__":
    sys.exit(main())

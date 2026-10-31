#!/usr/bin/env python3
"""Index-transaction bench for the external-context registry (E-095).

E-092 made `finalize` validate a candidate before writing and claimed that a
registry command either completes and is indexed or leaves the entry untouched.
The index rebuild, however, still ran last: a malformed README made `finalize`
leave review evidence behind and `discard` archive the entry before the command
reported failure. This bench measures the other half — the index is preflighted
before any mutation, and an unexpected index error rolls the entry (or the
move) back.

Six independent cases — four failures and the two paired success controls that
a command which refuses everything would also fail:

    finalize-bad-index   a README with no anchor fails before the entry changes
    discard-bad-index    the same failure leaves the entry in the registry
    finalize-index-error an unexpected index error restores the entry bytes
    discard-index-error  an unexpected index error moves the entry back
    finalize-happy       a normal finalize still registers and indexes
    discard-happy        a normal discard still archives and indexes

`--broken` is the rule-7 control: it disables the preflight and the three
restore helpers in memory, so the four failure cases mutate state while the
two happy paths still pass. Stdlib only, offline, deterministic.
"""
from __future__ import annotations

import argparse
import importlib.util
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _project(base: pathlib.Path) -> pathlib.Path:
    root = base / "project"
    registry = root / "docs/research/external"
    registry.mkdir(parents=True)
    (registry / "_template.md").write_text(
        (ROOT / "templates/external-context.md").read_text(encoding="utf-8"),
        encoding="utf-8")
    (registry / "README.md").write_text(
        (ROOT / "templates/external-context-README.md").read_text(
            encoding="utf-8"), encoding="utf-8")
    return root


def _complete(cli, root: pathlib.Path) -> pathlib.Path:
    """A prepared entry with every required field and placeholder filled."""
    prepared = cli.prepare(root, "https://github.com/mastra-ai/mastra")
    entry = root / prepared["entry"]
    text = entry.read_text(encoding="utf-8")
    values = {
        "Status": "reviewed",
        "Analyzed version/ref": "v0.31.0",
        "Revalidate when": "a new minor release is published",
        "Revalidate after": "2099-01-01",
        "Last reviewed on": "2026-10-30",
        "Reviewer": "bench-reviewer",
        "Project relevance": "AI agent runtime for the research workflow",
        "Aliases": "mastra-ai",
    }
    for label, value in values.items():
        text = cli._set_field(text, label, value)
    for label in cli.INTEGRATION_BULLETS:
        text = cli._set_field(text, label, "observed in the upstream documentation")
    text = cli._set_field(text, "Known gaps", "none observed")
    text = text.replace("| <ref>; ", "| v0.31.0; ").replace(
        "| <what was inspected> |", "| README, docs and examples |")
    text = text.replace(
        "| YYYY-MM-DD | <ref> | initial analysis | <reviewer> |",
        "| 2026-10-30 | v0.31.0 | initial analysis | bench-reviewer |")
    text = re.sub(
        r"<[^<>\n]+>",
        "Observed evidence recorded during the analysis pass; see the registry.",
        text)
    entry.write_text(text, encoding="utf-8")
    return entry


def _break_readme(root: pathlib.Path) -> pathlib.Path:
    readme = root / "docs/research/external/README.md"
    readme.write_text("# Registry\n\nNo generated section here.\n", encoding="utf-8")
    return readme


def _raising_writer(cli, monkey: dict):
    def boom(*_args, **_kwargs):
        raise RuntimeError("index write failed")
    monkey["original"] = cli._write_index
    cli._write_index = boom


def _restore_writer(cli, monkey: dict) -> None:
    if "original" in monkey:
        cli._write_index = monkey.pop("original")


def _finalize_bad_index(base: pathlib.Path, cli) -> bool:
    root = _project(base)
    entry = _complete(cli, root)
    _break_readme(root)
    before = entry.read_bytes()
    try:
        cli.finalize(root, "mastra", today="2026-10-31")
    except cli.ContextError:
        return entry.read_bytes() == before
    except Exception:
        return False
    return False


def _discard_bad_index(base: pathlib.Path, cli) -> bool:
    root = _project(base)
    prepared = cli.prepare(root, "https://github.com/mastra-ai/mastra")
    entry = root / prepared["entry"]
    _break_readme(root)
    try:
        cli.discard(root, "mastra", today="2026-10-31")
    except cli.ContextError:
        return entry.is_file() and not any(
            (root / "docs/research/external/.archive").glob("*"))
    except Exception:
        return False
    return False


def _finalize_index_error(base: pathlib.Path, cli) -> bool:
    root = _project(base)
    entry = _complete(cli, root)
    before = entry.read_bytes()
    monkey: dict = {}
    _raising_writer(cli, monkey)
    try:
        cli.finalize(root, "mastra", today="2026-10-31")
    except RuntimeError:
        return entry.read_bytes() == before
    except Exception:
        return False
    finally:
        _restore_writer(cli, monkey)
    return False


def _discard_index_error(base: pathlib.Path, cli) -> bool:
    root = _project(base)
    prepared = cli.prepare(root, "https://github.com/mastra-ai/mastra")
    entry = root / prepared["entry"]
    before = entry.read_bytes()
    monkey: dict = {}
    _raising_writer(cli, monkey)
    try:
        cli.discard(root, "mastra", today="2026-10-31")
    except RuntimeError:
        return (entry.is_file() and entry.read_bytes() == before
                and not any((root / "docs/research/external/.archive")
                            .glob("*-mastra*")))
    except Exception:
        return False
    finally:
        _restore_writer(cli, monkey)
    return False


def _finalize_happy(base: pathlib.Path, cli) -> bool:
    root = _project(base)
    _complete(cli, root)
    try:
        result = cli.finalize(root, "mastra", today="2026-10-31")
    except Exception:
        return False
    readme = (root / "docs/research/external/README.md").read_text(encoding="utf-8")
    return bool(result.get("ok")) and "mastra/context.md" in readme


def _discard_happy(base: pathlib.Path, cli) -> bool:
    root = _project(base)
    cli.prepare(root, "https://github.com/mastra-ai/mastra")
    try:
        result = cli.discard(root, "mastra", today="2026-10-31")
    except Exception:
        return False
    readme = (root / "docs/research/external/README.md").read_text(encoding="utf-8")
    return (bool(result.get("ok"))
            and any((root / "docs/research/external/.archive").glob("*-mastra/context.md"))
            and "mastra/context.md" not in readme)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--broken", action="store_true",
                        help="rule-7 control: disable the preflight and rollback")
    args = parser.parse_args(argv)

    cli = _load("external_context_transaction", ROOT / "scripts/external-context.py")
    if args.broken:
        # The pre-E-095 behavior: mutate first, rebuild the index last, and
        # leave whatever happened in place when the rebuild fails.
        cli._index_precheck = lambda root: (root / cli.PROJECT_README_REL, None)
        cli._restore_index = lambda index, before: None
        cli._restore_text = lambda path, before: None
        cli._restore_move = lambda path, source: None

    cases = (
        ("finalize-bad-index", _finalize_bad_index),
        ("discard-bad-index", _discard_bad_index),
        ("finalize-index-error", _finalize_index_error),
        ("discard-index-error", _discard_index_error),
        ("finalize-happy", _finalize_happy),
        ("discard-happy", _discard_happy),
    )
    passed = evaluated = skipped = 0
    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        for label, case in cases:
            try:
                result = case(base / label, cli)
            except Exception as exc:  # a traceback is a failed case, not a skip
                print(f"  FAIL  {label}\n        raised {type(exc).__name__}: {exc}")
                evaluated += 1
                continue
            if result is None:
                skipped += 1
                print(f"  skip  {label}")
                continue
            evaluated += 1
            if result:
                passed += 1
                print(f"  ok    {label}")
            else:
                print(f"  FAIL  {label}")

    total = len(cases)
    accuracy = passed / evaluated if evaluated else 0.0
    coverage = evaluated / total if total else 0.0
    print(f"consistency_accuracy={accuracy:.2f} ({passed}/{evaluated})")
    print(f"consistency_coverage={coverage:.2f} ({evaluated}/{total})")
    print(f"checks_skipped={skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

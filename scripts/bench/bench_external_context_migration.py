#!/usr/bin/env python3
"""Migration-transaction bench for the external-context registry (E-096).

E-095 closed the index half of the registry's transaction and named the opening
that was left: `migrate` still wrote entries one at a time, so a failure halfway
through a schema upgrade left earlier entries migrated and later ones not. A
probe on this host upgraded a two-entry registry to schema 2 for the first entry
and then raised `OSError: [Errno 28] No space left on device` on the second,
leaving the registry half migrated while the command reported failure.

Six independent cases — two rollback cases and four controls that a migration
which writes nothing at all would also fail:

    migrate-error-second   two legacy entries, the second write fails: both unchanged
    migrate-error-third    three legacy entries, the third write fails: all unchanged
    migrate-error-first    the first write fails: nothing was written yet
    migrate-dry-run        --dry-run plans two upgrades and writes neither
    migrate-current-entry  a schema-2 entry stays byte-identical, the legacy one moves
    migrate-happy          both entries upgrade, and a second run is a no-op

`--broken` is the rule-7 control: it disables the rollback helper in memory,
reproducing the pre-E-096 order, so the two rollback cases keep a half-migrated
registry while the four controls still pass. Stdlib only, offline, deterministic.
"""
from __future__ import annotations

import argparse
import importlib.util
import pathlib
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


def _entry(root: pathlib.Path, slug: str, schema: str = "1") -> pathlib.Path:
    """One entry at a given schema, with the fields a legacy one carries."""
    entry = root / "docs/research/external" / slug / "context.md"
    entry.parent.mkdir(parents=True)
    entry.write_text(
        f"# {slug.title()} Context\n\n"
        f"- **Schema:** {schema}\n"
        "- **Status:** reviewed\n"
        "- **Analyzed on:** 2026-09-01\n"
        "- **Analyzed version/ref:** 1.0\n"
        "- **Revalidate when:** 2026-12-01\n"
        "- **Project relevance:** agents\n\n"
        f"## Summary\n\nObserved evidence recorded for the {slug} entry.\n",
        encoding="utf-8")
    return entry


def _raise_at(cli, monkey: dict, index: int) -> None:
    """Make the `index`-th write fail, as a full disk or a permission would."""
    original = cli.atomic_write_text
    monkey["original"] = original
    calls: list[pathlib.Path] = []

    def write(path, text):
        calls.append(path)
        if len(calls) == index:
            raise OSError(28, "No space left on device")
        return original(path, text)

    cli.atomic_write_text = write


def _restore_writer(cli, monkey: dict) -> None:
    if "original" in monkey:
        cli.atomic_write_text = monkey.pop("original")


def _failed_migration(base: pathlib.Path, cli, entries: int,
                      fail_at: int) -> bool:
    root = _project(base)
    paths = [_entry(root, name) for name in ("alpha", "beta", "gamma")[:entries]]
    before = {path: path.read_bytes() for path in paths}
    monkey: dict = {}
    _raise_at(cli, monkey, fail_at)
    try:
        cli.migrate(root, today="2026-10-31")
    except OSError:
        return all(path.read_bytes() == data for path, data in before.items())
    except Exception:
        return False
    finally:
        _restore_writer(cli, monkey)
    return False


def _error_second(base: pathlib.Path, cli) -> bool:
    return _failed_migration(base, cli, entries=2, fail_at=2)


def _error_third(base: pathlib.Path, cli) -> bool:
    return _failed_migration(base, cli, entries=3, fail_at=3)


def _error_first(base: pathlib.Path, cli) -> bool:
    return _failed_migration(base, cli, entries=2, fail_at=1)


def _dry_run(base: pathlib.Path, cli) -> bool:
    root = _project(base)
    paths = [_entry(root, name) for name in ("alpha", "beta")]
    before = {path: path.read_bytes() for path in paths}
    try:
        result = cli.migrate(root, dry_run=True, today="2026-10-31")
    except Exception:
        return False
    return (result.get("count") == 2
            and all(path.read_bytes() == data for path, data in before.items()))


def _current_entry(base: pathlib.Path, cli) -> bool:
    root = _project(base)
    legacy = _entry(root, "alpha")
    current = _entry(root, "beta", schema="2")
    before = current.read_bytes()
    try:
        result = cli.migrate(root, today="2026-10-31")
    except Exception:
        return False
    return (result.get("changed") == ["docs/research/external/alpha/context.md"]
            and current.read_bytes() == before
            and "- **Schema:** 2" in legacy.read_text(encoding="utf-8"))


def _happy(base: pathlib.Path, cli) -> bool:
    root = _project(base)
    paths = [_entry(root, name) for name in ("alpha", "beta")]
    try:
        result = cli.migrate(root, today="2026-10-31")
        again = cli.migrate(root, today="2026-10-31")
    except Exception:
        return False
    return (result.get("count") == 2 and again.get("count") == 0
            and all("- **Schema:** 2" in path.read_text(encoding="utf-8")
                    for path in paths))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--broken", action="store_true",
                        help="rule-7 control: disable the migration rollback")
    args = parser.parse_args(argv)

    cli = _load("external_context_migration", ROOT / "scripts/external-context.py")
    if args.broken:
        # The pre-E-096 order: write each entry as it is planned and leave the
        # ones already written in place when a later write fails.
        cli._restore_text = lambda path, before: None

    cases = (
        ("migrate-error-second", _error_second),
        ("migrate-error-third", _error_third),
        ("migrate-error-first", _error_first),
        ("migrate-dry-run", _dry_run),
        ("migrate-current-entry", _current_entry),
        ("migrate-happy", _happy),
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

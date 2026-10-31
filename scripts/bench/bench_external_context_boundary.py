#!/usr/bin/env python3
"""Write-boundary bench for the external-context registry (E-093).

E-092 proved every *existing* entry path resolves inside the registry before a
read or write. This bench measures the other half: a path the registry is about
to create — a new entry folder or an archive folder — must not follow an
existing symlink/junction outside the project.

Six independent cases — three escapes and the three paired false-alarm
controls that a guard which refuses everything would also fail:

    path-outside-ancestor     the shared guard refuses a not-yet-created target
                              whose nearest existing ancestor is outside
    prepare-linked-entry      `prepare` refuses a linked entry folder and
                              writes no scaffold outside
    discard-linked-archive    `discard` refuses a linked `.archive` and leaves
                              the entry in place
    path-safe-leaf            a missing target inside the registry is accepted
    prepare-safe-entry        `prepare` still writes a normal entry inside
    discard-safe-entry        `discard` still archives a normal entry inside

`--broken` is the rule-7 control: it removes the guard in memory, so the three
escape cases fail while the three safe cases still pass. Stdlib only, offline, deterministic; link cases are skipped
only when the host refuses both a directory symlink and a Windows junction.
"""
from __future__ import annotations

import argparse
import importlib.util
import pathlib
import subprocess
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


def _dir_link(link: pathlib.Path, target: pathlib.Path) -> bool:
    """A directory symlink, or a Windows junction when symlinks need a privilege."""
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(target, target_is_directory=True)
        return True
    except OSError:
        pass
    if sys.platform == "win32":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        return result.returncode == 0
    return False


def _project(base: pathlib.Path, cli) -> tuple[pathlib.Path, pathlib.Path]:
    root = base / "project"
    registry = root / "docs/research/external"
    registry.mkdir(parents=True)
    (registry / "_template.md").write_text(
        (ROOT / "templates/external-context.md").read_text(encoding="utf-8"),
        encoding="utf-8")
    (registry / "README.md").write_text(
        (ROOT / "templates/external-context-README.md").read_text(
            encoding="utf-8"), encoding="utf-8")
    return root, registry


def _path_case(base: pathlib.Path, cli) -> bool:
    root, registry = _project(base, cli)
    outside = base / "outside"
    outside.mkdir()
    try:
        cli.require_inside_target(
            outside / "new" / "context.md", registry, "entry new")
    except cli.ContextError:
        return True
    except Exception:
        return False
    return False


def _prepare_case(base: pathlib.Path, cli) -> bool | None:
    root, registry = _project(base, cli)
    outside = base / "outside-prepare"
    outside.mkdir()
    if not _dir_link(registry / "escaped", outside):
        return None
    try:
        cli.prepare(root, "https://github.com/mastra-ai/mastra", slug="escaped")
    except cli.ContextError:
        return not (outside / "context.md").exists()
    except Exception:
        return False
    return False


def _safe_path_case(base: pathlib.Path, cli) -> bool:
    root, registry = _project(base, cli)
    safe = registry / "new" / "context.md"
    try:
        return cli.require_inside_target(safe, registry, "entry new") == safe
    except Exception:
        return False


def _prepare_safe_case(base: pathlib.Path, cli) -> bool:
    root, registry = _project(base, cli)
    try:
        result = cli.prepare(root, "https://github.com/mastra-ai/mastra")
    except Exception:
        return False
    entry = registry / "mastra" / "context.md"
    return bool(result.get("ok")) and entry.is_file()


def _discard_safe_case(base: pathlib.Path, cli) -> bool:
    root, registry = _project(base, cli)
    try:
        cli.prepare(root, "https://github.com/mastra-ai/mastra")
        result = cli.discard(root, "mastra")
    except Exception:
        return False
    archived = registry / ".archive"
    return (bool(result.get("ok")) and not (registry / "mastra").exists()
            and any(archived.glob("*-mastra/context.md")))


def _discard_case(base: pathlib.Path, cli) -> bool | None:
    root, registry = _project(base, cli)
    outside = base / "outside-archive"
    outside.mkdir()
    cli.prepare(root, "https://github.com/mastra-ai/mastra")
    if not _dir_link(registry / ".archive", outside):
        return None
    try:
        cli.discard(root, "mastra")
    except cli.ContextError:
        return ((registry / "mastra/context.md").is_file()
                and not any(outside.iterdir()))
    except Exception:
        return False
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--broken", action="store_true",
                        help="rule-7 control: remove the guard and expect the cases to fail")
    args = parser.parse_args(argv)

    cli = _load("external_context_boundary", ROOT / "scripts/external-context.py")
    if args.broken:
        # The same seed the consistency bench uses: the guard stops refusing.
        cli.require_inside_target = lambda path, parent, label: path

    cases = (
        ("path-outside-ancestor", _path_case),
        ("prepare-linked-entry", _prepare_case),
        ("discard-linked-archive", _discard_case),
        ("path-safe-leaf", _safe_path_case),
        ("prepare-safe-entry", _prepare_safe_case),
        ("discard-safe-entry", _discard_safe_case),
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
                print(f"  skip  {label}\n        this host cannot create a directory link")
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

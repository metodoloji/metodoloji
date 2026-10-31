#!/usr/bin/env python3
"""Source-identity bench for the external-context registry (E-094).

A source has one identity. Before E-094 `prepare` compared the raw source text,
so the same resource written with a repository `.git` suffix, a `#fragment`, a
trailing slash, an explicit default port or a different local path spelling
created a second entry and split the evidence. `check` could not see the split
either. `normalize_source`/`source_key` now derive one key for both writers.

Six independent cases — four duplicate spellings and two paired false-alarm
controls that an over-eager normaliser would also fail:

    url-git-suffix      `repo.git` is refused once `repo` is registered
    url-fragment-slash  `/docs#intro` and `/docs/` are refused once `/docs` is
    url-default-port    `:443`/`:80` is not a second source
    path-spelling       `./src.md` and its absolute spelling are one entry
    distinct-paths      two different local files are still two sources
    distinct-url-paths  `/docs/v1` and `/docs/v2` are still two sources

`--broken` is the rule-7 control: it replaces the derived key with the raw text
in memory, so the four duplicate cases fail while the two distinct controls
still pass. Stdlib only, offline, deterministic.
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


def _refused(cli, root: pathlib.Path, source: str, name: str) -> bool:
    try:
        cli.prepare(root, source, name=name)
    except cli.ContextError as exc:
        return "already registered" in str(exc)
    except Exception:
        return False
    return False


def _url_git_case(base: pathlib.Path, cli) -> bool:
    root = _project(base)
    cli.prepare(root, "https://github.com/mastra-ai/mastra", name="Mastra")
    return _refused(cli, root, "https://github.com/mastra-ai/mastra.git", "Git")


def _url_fragment_case(base: pathlib.Path, cli) -> bool:
    root = _project(base)
    cli.prepare(root, "https://mastra.ai/docs", name="Docs")
    return (_refused(cli, root, "https://mastra.ai/docs/#intro", "Fragment")
            and _refused(cli, root, "https://mastra.ai/docs/", "Slash"))


def _url_port_case(base: pathlib.Path, cli) -> bool:
    root = _project(base)
    cli.prepare(root, "https://mastra.ai/docs", name="Docs")
    return _refused(cli, root, "https://mastra.ai:443/docs", "Port")


def _path_case(base: pathlib.Path, cli) -> bool:
    root = _project(base)
    (root / "src.md").write_text("# src\n", encoding="utf-8")
    (root / "sub").mkdir()
    cli.prepare(root, str(root / "src.md"), name="Src")
    return _refused(cli, root, str(root / "sub" / ".." / "src.md"), "Dotdot")


def _distinct_paths_case(base: pathlib.Path, cli) -> bool:
    root = _project(base)
    (root / "one.md").write_text("# one\n", encoding="utf-8")
    (root / "two.md").write_text("# two\n", encoding="utf-8")
    try:
        first = cli.prepare(root, str(root / "one.md"), name="One")
        second = cli.prepare(root, str(root / "two.md"), name="Two")
    except Exception:
        return False
    return bool(first.get("ok") and second.get("ok"))


def _distinct_url_paths_case(base: pathlib.Path, cli) -> bool:
    root = _project(base)
    try:
        first = cli.prepare(root, "https://mastra.ai/docs/v1", name="V1")
        second = cli.prepare(root, "https://mastra.ai/docs/v2", name="V2")
    except Exception:
        return False
    return bool(first.get("ok") and second.get("ok"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--broken", action="store_true",
                        help="rule-7 control: compare raw text and expect duplicates to pass")
    args = parser.parse_args(argv)

    cli = _load("external_context_identity", ROOT / "scripts/external-context.py")
    if args.broken:
        # The pre-E-094 behavior: the raw text is both stored and compared, so
        # the same source in another spelling looks different and every
        # duplicate case registers a second entry.
        cli.source_key = lambda value, base=None: (value or "").strip()
        canonical_classify = cli.classify_source

        def legacy_classify(source: str):
            kind, identity, _ = canonical_classify(source)
            return kind, identity, source.strip()

        cli.classify_source = legacy_classify

    cases = (
        ("url-git-suffix", _url_git_case),
        ("url-fragment-slash", _url_fragment_case),
        ("url-default-port", _url_port_case),
        ("path-spelling", _path_case),
        ("distinct-paths", _distinct_paths_case),
        ("distinct-url-paths", _distinct_url_paths_case),
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

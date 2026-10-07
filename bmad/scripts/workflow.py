#!/usr/bin/env python3
"""workflow.py — CLI for the methodology's dynamic workflow kernel.

A workflow is a declared spec; the kernel owns ordering. This CLI is the
executor's single entry point: it validates specs, creates runs, reports what
to do now, and — only with real evidence — records a stage complete and
computes the next one.

Usage:
    python3 bmad/scripts/workflow.py validate --spec FILE
    python3 bmad/scripts/workflow.py create  (--spec FILE | --builtin ID) [--slug S] [--force]
    python3 bmad/scripts/workflow.py list
    python3 bmad/scripts/workflow.py status   [--slug S]
    python3 bmad/scripts/workflow.py next      [--slug S]
    python3 bmad/scripts/workflow.py complete --stage ID [--note TEXT] [--force] [--slug S]
    python3 bmad/scripts/workflow.py block    --reason TEXT [--slug S]
    python3 bmad/scripts/workflow.py resume    [--slug S]
    python3 bmad/scripts/workflow.py flag     --key K --value V [--slug S]
    python3 bmad/scripts/workflow.py history   [--slug S]

Every command prints JSON and exits 0 on success, 1 on a refusal. All commands
accept --project-root R (default: cwd).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))  # repo root
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from bmad.workflow import engine  # noqa: E402
from bmad.workflow import spec as spec_mod  # noqa: E402


def _emit(obj: dict) -> int:
    print(json.dumps(obj, ensure_ascii=False, indent=1))
    return 0 if obj.get("ok") else 1


def _builtin_dir() -> str:
    return os.path.join(_ROOT, "bmad", "workflow", "builtin")


def _resolve_slug(root: str, slug: str | None) -> str | None:
    """Explicit slug, or the single existing run; None when ambiguous/absent."""
    if slug:
        return slug
    runs = engine.list_runs(root).get("runs", [])
    return runs[0]["slug"] if len(runs) == 1 else None


def _load_spec_arg(args) -> dict:
    path = args.spec
    if not path and getattr(args, "builtin", None):
        path = os.path.join(_builtin_dir(), f"{args.builtin}.json")
    if not path:
        raise spec_mod.SpecError("provide --spec FILE or --builtin ID")
    return spec_mod.load(path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Dynamic workflow kernel CLI")
    ap.add_argument("--project-root", default=".")
    sub = ap.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate")
    v.add_argument("--spec", required=True)

    c = sub.add_parser("create")
    c.add_argument("--spec")
    c.add_argument("--builtin")
    c.add_argument("--slug")
    c.add_argument("--force", action="store_true")

    sub.add_parser("list")

    for name in ("status", "next", "resume"):
        p = sub.add_parser(name)
        p.add_argument("--slug")

    comp = sub.add_parser("complete")
    comp.add_argument("--slug", required=True)
    comp.add_argument("--stage", required=True)
    comp.add_argument("--note", default="")
    comp.add_argument("--force", action="store_true")

    blk = sub.add_parser("block")
    blk.add_argument("--slug", required=True)
    blk.add_argument("--reason", required=True)

    flg = sub.add_parser("flag")
    flg.add_argument("--slug", required=True)
    flg.add_argument("--key", required=True)
    flg.add_argument("--value", required=True)

    hist = sub.add_parser("history")
    hist.add_argument("--slug")
    hist.add_argument("--limit", type=int, default=50)

    args = ap.parse_args(argv)
    root = os.path.abspath(args.project_root)

    try:
        if args.cmd == "validate":
            data = spec_mod.load(args.spec)
            problems = spec_mod.validate(data)
            if problems:
                return _emit({"ok": False, "error": "spec invalid", "problems": problems})
            return _emit({"ok": True, "id": data.get("id"),
                          "stages": [s["id"] for s in data["stages"]]})
        if args.cmd == "create":
            data = _load_spec_arg(args)
            return _emit(engine.create(root, data, slug=args.slug, force=args.force))
        if args.cmd == "list":
            return _emit(engine.list_runs(root))
        if args.cmd in ("status", "next", "resume", "history"):
            slug = _resolve_slug(root, args.slug)
            if not slug:
                return _emit({"ok": False,
                              "error": "no --slug given and no single run to resolve"})
            args.slug = slug
        if args.cmd == "status":
            return _emit(engine.status(root, args.slug))
        if args.cmd == "next":
            return _emit(engine.next_stage(root, args.slug))
        if args.cmd == "complete":
            return _emit(engine.complete(root, args.slug, args.stage,
                                         note=args.note, force=args.force))
        if args.cmd == "block":
            return _emit(engine.block(root, args.slug, args.reason))
        if args.cmd == "resume":
            return _emit(engine.resume(root, args.slug))
        if args.cmd == "flag":
            return _emit(engine.flag(root, args.slug, args.key, args.value))
        if args.cmd == "history":
            return _emit(engine.history(root, args.slug, limit=args.limit))
    except spec_mod.SpecError as exc:
        return _emit({"ok": False, "error": str(exc)})
    return _emit({"ok": False, "error": f"unknown command {args.cmd}"})


if __name__ == "__main__":
    sys.exit(main())

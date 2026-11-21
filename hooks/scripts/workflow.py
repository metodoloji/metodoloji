#!/usr/bin/env python3
"""workflow.py — CLI for the methodology's dynamic workflow kernel.

A workflow is a declared spec; the kernel owns ordering. This CLI is the
executor's single entry point: it validates specs, creates runs, reports what
to do now, and — only with real evidence — records a stage complete and
computes the next one.

Usage:
    python3 hooks/scripts/workflow.py validate --spec FILE
    python3 hooks/scripts/workflow.py create  (--spec FILE | --builtin ID) [--slug S] [--force]
    python3 hooks/scripts/workflow.py adopt   --story S-005
    python3 hooks/scripts/workflow.py list
    python3 hooks/scripts/workflow.py status   [--slug S]
    python3 hooks/scripts/workflow.py next      [--slug S]
    python3 hooks/scripts/workflow.py complete --stage ID [--note TEXT] [--force] [--slug S]
    python3 hooks/scripts/workflow.py block    --reason TEXT [--slug S]
    python3 hooks/scripts/workflow.py resume    [--slug S]
    python3 hooks/scripts/workflow.py flag     --key K --value V [--slug S]
    python3 hooks/scripts/workflow.py history   [--slug S]

Every command prints JSON and exits 0 on success, 1 on a refusal. All commands
accept --project-root R (default: cwd).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))  # repo root
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from hooks.workflow import engine  # noqa: E402
from hooks.workflow import spec as spec_mod  # noqa: E402


def _emit(obj: dict) -> int:
    print(json.dumps(obj, ensure_ascii=False, indent=1))
    return 0 if obj.get("ok") else 1


def _builtin_dir() -> str:
    return os.path.join(_ROOT, "hooks", "workflow", "builtin")


def _resolve_slug(root: str, slug: str | None) -> str | None:
    """Explicit slug, or the single existing run; None when ambiguous/absent.

    Delegates to the kernel: a name question is answered from names, never by
    classifying every run (E-068).
    """
    return engine.resolve_slug(root, slug)


def _load_spec_arg(args) -> dict:
    path = args.spec
    if not path and getattr(args, "builtin", None):
        path = os.path.join(_builtin_dir(), f"{args.builtin}.json")
    if not path:
        raise spec_mod.SpecError("provide --spec FILE or --builtin ID")
    return spec_mod.load(path)


def _story_record(root: str, story: str):
    """Resolve `S-005` to THE story record, or name why it cannot be.

    Identity goes through the state module's one id reader (E-074/E-170), so
    `S-5` and `S-005.md` name the same story; a zero-match and a two-match id
    are different refusals and neither is guessed from a filename. The walk is
    returned, not repeated by the caller.
    """
    from hooks.engine.modules import state as state_mod
    sid = state_mod.canonical_record_id(story.strip())
    if not re.fullmatch(r"S-\d+", sid):
        return sid, None, f"--story must name a story id (S-NNN), got {story!r}"
    matches = state_mod.record_files(root, sid, ("docs/development/stories",))
    if not matches:
        return sid, None, (f"no story record for {sid} under "
                           f"docs/development/stories — create the story record first")
    if len(matches) > 1:
        names = ", ".join(p.name for p in matches)
        return sid, None, f"story id {sid} is ambiguous: {names} — one id, one file"
    return sid, matches[0], ""


def _adopt(root: str, story: str) -> dict:
    """Open the methodology-chain run a story already implies.

    `create --builtin methodology-chain` makes the caller remember two things
    the story record already declares: its id (the run's slug) and its `Rigor
    Tier` (the branch flag that decides whether IR/SP are required). Forgetting
    the flag turned a Tier 1 story into the full chain and made the run demand
    planning records its own tier says it does not need. This command derives
    both from the record, then hands the validated builtin spec to `create` —
    the kernel still decides everything after the first stage.
    """
    sid, path, problem = _story_record(root, story)
    if problem:
        return {"ok": False, "error": problem}
    from hooks.engine.modules.config import story_rigor_tier, story_status
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"ok": False, "error": f"cannot read {sid}: {exc}"}
    status = story_status(text)
    if status == "done":
        return {"ok": False,
                "error": (f"{sid} is done — a finished story has no run to open; "
                          f"adopt the next story instead")}
    tier = story_rigor_tier(text)
    spec = spec_mod.load(os.path.join(_builtin_dir(), "methodology-chain.json"))
    result = engine.create(root, spec, slug=sid, flags={"tier": str(tier)})
    if not result.get("ok"):
        return result
    result.update({
        "story": sid,
        "tier": tier,
        "story_status": status,
        "record": os.path.relpath(str(path), root).replace(os.sep, "/"),
    })
    return result


def _add_root(p) -> None:
    """Accept --project-root after the subcommand, the form every doc example uses.

    argparse only knew the global position, so the documented
    `create --builtin X --project-root .` exited 2 ("unrecognized arguments").
    SUPPRESS keeps a value given before the subcommand intact.
    """
    p.add_argument("--project-root", default=argparse.SUPPRESS)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Dynamic workflow kernel CLI")
    ap.add_argument("--project-root", default=".")
    sub = ap.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate")
    v.add_argument("--spec")
    v.add_argument("--builtin")
    _add_root(v)

    c = sub.add_parser("create")
    c.add_argument("--spec")
    c.add_argument("--builtin")
    c.add_argument("--slug")
    c.add_argument("--flag", action="append", default=[], metavar="KEY=VALUE",
                   help="branch flag read by a conditional edge (e.g. tier=1)")
    c.add_argument("--force", action="store_true")
    _add_root(c)

    a = sub.add_parser("adopt")
    a.add_argument("--story", required=True,
                   help="story id whose record declares the run (e.g. S-005)")
    _add_root(a)

    _add_root(sub.add_parser("list"))

    for name in ("status", "next", "resume", "history"):
        p = sub.add_parser(name)
        p.add_argument("--slug")
        if name == "history":
            p.add_argument("--limit", type=int, default=50)
        _add_root(p)

    comp = sub.add_parser("complete")
    comp.add_argument("--slug", required=True)
    comp.add_argument("--stage", required=True)
    comp.add_argument("--note", default="")
    comp.add_argument("--force", action="store_true")
    _add_root(comp)

    blk = sub.add_parser("block")
    blk.add_argument("--slug", required=True)
    blk.add_argument("--reason", required=True)
    _add_root(blk)

    flg = sub.add_parser("flag")
    flg.add_argument("--slug", required=True)
    flg.add_argument("--key", required=True)
    flg.add_argument("--value", required=True)
    _add_root(flg)

    args = ap.parse_args(argv)
    root = os.path.abspath(args.project_root)

    try:
        if args.cmd == "validate":
            data = _load_spec_arg(args)
            problems = spec_mod.validate(data)
            if problems:
                return _emit({"ok": False, "error": "spec invalid", "problems": problems})
            return _emit({"ok": True, "id": data.get("id"),
                          "stages": [s["id"] for s in data["stages"]]})
        if args.cmd == "create":
            data = _load_spec_arg(args)
            flags = {}
            for raw in args.flag:
                if "=" not in raw:
                    return _emit({"ok": False,
                                  "error": f"--flag must be KEY=VALUE, got {raw!r}"})
                key, value = raw.split("=", 1)
                flags[key.strip()] = value.strip()
            return _emit(engine.create(root, data, slug=args.slug,
                                       force=args.force, flags=flags))
        if args.cmd == "adopt":
            return _emit(_adopt(root, args.story))
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
    except engine.store.WorkflowStoreError as exc:
        return _emit({"ok": False, "error": str(exc)})
    except OSError as exc:
        return _emit({"ok": False, "error": f"workflow storage error: {exc}"})
    return _emit({"ok": False, "error": f"unknown command {args.cmd}"})


if __name__ == "__main__":
    sys.exit(main())

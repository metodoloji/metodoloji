"""Subscription identity is (watcher, pattern, channel) — DW-2 paid (E-141).

    python3 scripts/bench/bench_subscription_channels.py --samples docs/experiments/E-141/raw/samples.jsonl
    python3 scripts/bench/bench_subscription_channels.py --broken   # rule-7 control

The blackboard's subscription plane deduped on (watcher, pattern) and ignored
the channel. A skill that watched one pattern on SessionStart could not also
watch it at Stop: the second `subscribe` returned `{"ok": true}` and the watch
never existed. `unsubscribe` had no channel argument either, so removing one
channel meant removing the pair.

The defect sat in the repository's own deferred ledger (DW-2) unpaid, because
nothing measured it: the plane read looks healthy, the ack looks successful,
and only a routing test that checks BOTH channels sees the hole.

`--broken` imports the PRE-FIX modules from git (HEAD's `blackboard.py` and
`blackboard_fold.py` under a separate package name) and runs the same probes
against them — the control is the old code, not a reimplementation of it.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hooks" / "engine"))

from modules import blackboard as bb  # noqa: E402

BROKEN = False
_PREFIX_REV = "HEAD"          # the commit that still carries the channel-blind dedupe
_CHANGED = ("blackboard.py", "blackboard_fold.py")


def _prefix_module():
    """The pre-fix blackboard, imported under its own package name.

    Copying the module tree (not just two files) keeps the relative imports
    working; renaming the package keeps it from shadowing the live one.
    """
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="dw2-prefix-"))
    dest = tmp / "prefix_modules"
    shutil.copytree(ROOT / "hooks" / "engine" / "modules", dest,
                    ignore=shutil.ignore_patterns("__pycache__"))
    for rel in _CHANGED:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "show", f"{_PREFIX_REV}:hooks/engine/modules/{rel}"],
            capture_output=True, text=True, encoding="utf-8",
            stdin=subprocess.DEVNULL)
        if out.returncode != 0:
            raise RuntimeError(f"cannot read {_PREFIX_REV}:{rel}: {out.stderr[:200]}")
        (dest / rel).write_text(out.stdout, encoding="utf-8")
    sys.path.insert(0, str(tmp))
    import importlib
    return importlib.import_module("prefix_modules.blackboard"), tmp


def _api():
    """(subscribe, unsubscribe, read_board, write_key, pending_alerts, rebuild)."""
    if not BROKEN:
        return (bb.subscribe, bb.unsubscribe, bb.read_board, bb.write_key,
                bb.pending_alerts, bb.rebuild)
    module, _tmp = _prefix_module()
    return (module.subscribe, module.unsubscribe, module.read_board,
            module.write_key, module.pending_alerts, module.rebuild)


def _channels(read_board, root: str) -> list[str]:
    return sorted(s.get("channel", "session")
                  for s in read_board(root)["subscriptions"])


def _case_two_channels_one_watcher() -> tuple[bool, str]:
    sub, _unsub, read_board, *_ = _api()
    with tempfile.TemporaryDirectory() as root:
        sub(root, "w", "prd.*", channel="session")
        sub(root, "w", "prd.*", channel="stop")
        got = _channels(read_board, root)
    return (got == ["session", "stop"],
            f"channels={got}" + (" (pre-fix: second channel silently dropped)"
                                 if BROKEN else ""))


def _case_both_channels_route() -> tuple[bool, str]:
    sub, _unsub, _read_board, write_key, pending, _rebuild = _api()
    with tempfile.TemporaryDirectory() as root:
        sub(root, "w", "prd.*", channel="session")
        sub(root, "w", "prd.*", channel="stop")
        write_key(root, "prd.x", "v")
        session = len(pending(root, "session"))
        stop = len(pending(root, "stop"))
    return (session == 1 and stop == 1, f"session={session} stop={stop}")


def _case_repeat_triple_is_idempotent() -> tuple[bool, str]:
    sub, _unsub, read_board, *_ = _api()
    with tempfile.TemporaryDirectory() as root:
        first = sub(root, "w", "prd.*", channel="stop")
        again = sub(root, "w", "prd.*", channel="stop")
        count = len(read_board(root)["subscriptions"])
    # The pre-fix ack carried no `created` flag at all, so a caller could not
    # tell a new watch from a deduped one.
    ok = (first.get("created") is True and again.get("created") is False
          and count == 1)
    return (ok, f"created={first.get('created')}->{again.get('created')} count={count}")


def _case_unsubscribe_one_channel() -> tuple[bool, str]:
    sub, unsub, read_board, *_ = _api()
    with tempfile.TemporaryDirectory() as root:
        for ch in ("session", "stop", "release"):
            sub(root, "w", "prd.*", channel=ch)
        try:
            removed = unsub(root, "w", "prd.*", channel="stop")
            narrowed = True
        except TypeError:
            # Pre-fix: the parameter does not exist, so the caller can only
            # clear the whole pair.
            removed = unsub(root, "w", "prd.*")
            narrowed = False
        got = _channels(read_board, root)
    ok = narrowed and removed["removed"] == 1 and got == ["release", "session"]
    return (ok, f"narrowed={narrowed} removed={removed['removed']} left={got}")


def _case_unsubscribe_all_is_still_the_default() -> tuple[bool, str]:
    sub, unsub, read_board, *_ = _api()
    with tempfile.TemporaryDirectory() as root:
        for ch in ("session", "stop"):
            sub(root, "w", "prd.*", channel=ch)
        removed = unsub(root, "w", "prd.*")
        left = _channels(read_board, root)
    return (removed["removed"] == 2 and left == [],
            f"removed={removed['removed']} left={left}")


def _case_replay_agrees() -> tuple[bool, str]:
    """The board is rebuilt from the event log; the fold must apply the same rule.

    This is the check a PARTIAL revert fails: if the direct write learns the
    channel but the fold keeps deduping channel-blind (or the reverse), the
    snapshot and a replay disagree and a live watch disappears on rebuild.
    """
    sub, unsub, read_board, _write, _pending, rebuild = _api()
    with tempfile.TemporaryDirectory() as root:
        for ch in ("session", "stop", "release"):
            sub(root, "w", "prd.*", channel=ch)
        try:
            unsub(root, "w", "prd.*", channel="stop")
        except TypeError:
            unsub(root, "w", "prd.*")
        before = read_board(root)["subscriptions"]
        rebuild(root)
        after = read_board(root)["subscriptions"]
    # Agreement alone is not the claim: a board that lost every watch replays
    # perfectly. The surviving channels must also be the ones asked for.
    survived = sorted(s.get("channel", "session") for s in after)
    return (before == after and survived == ["release", "session"],
            f"before={[s['channel'] for s in before]} "
            f"after={[s['channel'] for s in after]}")


def _case_cli_exposes_channel_on_unsubscribe() -> tuple[bool, str]:
    if BROKEN:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "show", f"{_PREFIX_REV}:bmad/scripts/blackboard.py"],
            capture_output=True, text=True, encoding="utf-8",
            stdin=subprocess.DEVNULL)
        text = out.stdout if out.returncode == 0 else ""
    else:
        text = (ROOT / "bmad" / "scripts" / "blackboard.py").read_text(encoding="utf-8")
    hit = 'us.add_argument("--channel"' in text
    return (hit, "unsubscribe --channel present" if hit
            else "unsubscribe has no --channel (cannot narrow the removal)")


CASES = (
    ("one watcher holds one pattern on two channels", _case_two_channels_one_watcher),
    ("both channels actually route", _case_both_channels_route),
    ("repeating the same triple is idempotent", _case_repeat_triple_is_idempotent),
    ("unsubscribe narrows to one channel", _case_unsubscribe_one_channel),
    ("unsubscribe without a channel still clears the pair",
     _case_unsubscribe_all_is_still_the_default),
    ("an event-log replay agrees with the live board", _case_replay_agrees),
    ("the CLI exposes --channel on unsubscribe", _case_cli_exposes_channel_on_unsubscribe),
)


def run() -> list[dict]:
    results = []
    for name, probe in CASES:
        try:
            ok, detail = probe()
        except Exception as exc:  # a probe that cannot answer is a failure
            ok, detail = False, f"probe error: {exc}"
        results.append({"check": name, "ok": bool(ok), "detail": detail})
    return results


def main() -> int:
    global BROKEN
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", default=None,
                    help="write one JSON object per line here (the gate recomputes x/n)")
    ap.add_argument("--broken", action="store_true",
                    help="rule-7 control: run the probes against the pre-fix modules")
    args = ap.parse_args()
    BROKEN = args.broken

    results = run()
    if args.samples:
        path = pathlib.Path(args.samples)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for r in results:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    x = sum(1 for r in results if r["ok"])
    n = len(results)
    for r in results:
        print(f"[{'OK' if r['ok'] else 'MISS'}] {r['check']}: {r['detail']}")
    print(f"subscription_channel_accuracy={x / n:.2f} ({x}/{n})")
    # The aggregate is the signal; a nonzero exit would make the rule-7 control
    # unreadable to the gate (it runs the control itself and expects a reading).
    return 0


if __name__ == "__main__":
    sys.exit(main())

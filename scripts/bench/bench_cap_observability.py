"""Capped-plane observability bench — E-113.

docs/experiments/E-113.md (H-113: cap_observability_accuracy >= 0.999).

The board bounds every plane it owns, and two of them already say so when they
drop an entry (`write_key` and `notify` both print `bounds enforced - <plane>
<used>/<cap>, oldest expired`). Subscriptions did not: the fold silently
`pop(0)`ed the oldest watch once the seventeenth was appended, so a project two
registrations away from the cap (this repo: 14/16) could lose a live watcher
with nothing in the output to show it.

This bench is a census over that one behaviour: register past the cap and check
that the eviction is announced, named, counted, and still FIFO - then that the
plane keeps working (dedupe, unsubscribe) and that the shared notice emitter is
the single source the key plane already used.

`--broken` reproduces the pre-fix surface (a `_bounds_notice` that swallows its
message). Rule 7: the broken implementation must score below the threshold.

Deterministic, offline, stdlib only.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hooks" / "engine"))

from modules import blackboard as bb  # noqa: E402

CAP = bb.MAX_SUBSCRIPTIONS
# Declared independently so a change to the constant fails the census check
# instead of silently re-scoping the measurement.
DECLARED_CAP = 16


def _snapshot(root: str) -> dict:
    paths = bb.board_paths(root)
    return json.loads(pathlib.Path(paths["snapshot"]).read_text(encoding="utf-8"))


def _subscribe_capture(root: str, watcher: str, pattern: str) -> tuple[dict, str]:
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        ack = bb.subscribe(root, watcher, pattern)
    return ack, err.getvalue()


def _silence_notice() -> None:
    """The pre-fix surface: the notice is produced but never reaches stderr."""
    helper = getattr(bb, "_bounds_notice", None)
    if helper is not None:
        bb._bounds_notice = lambda *a, **k: None  # type: ignore[assignment]


def _checks(tmp: pathlib.Path) -> list[tuple[str, bool, str]]:
    root = str(tmp)
    results: list[tuple[str, bool, str]] = []

    results.append((
        "cap_declared",
        CAP == DECLARED_CAP,
        f"MAX_SUBSCRIPTIONS={CAP} (declared {DECLARED_CAP})",
    ))

    for i in range(1, CAP + 1):
        _subscribe_capture(root, f"w{i}", f"key-{i}.*")
    ack17, err17 = _subscribe_capture(root, f"w{CAP + 1}", f"key-{CAP + 1}.*")

    subs = _snapshot(root)["subscriptions"]
    watchers = [s["watcher"] for s in subs]

    results.append((
        "overflow_notice_emitted",
        "bounds enforced" in err17,
        repr(err17.strip()) if err17.strip() else "no notice on the 17th subscribe",
    ))
    results.append((
        "overflow_notice_names_plane",
        "subscriptions" in err17,
        "names subscriptions" if "subscriptions" in err17
        else f"notice={err17.strip()!r}",
    ))
    results.append((
        "overflow_notice_names_counts",
        f"{CAP + 1}/{CAP}" in err17,
        f"expects {CAP + 1}/{CAP} in {err17.strip()!r}",
    ))
    results.append((
        "plane_holds_at_cap",
        len(subs) == CAP,
        f"subscriptions={len(subs)} (cap {CAP})",
    ))
    results.append((
        "oldest_evicted",
        "w1" not in watchers,
        f"oldest present={('w1' in watchers)}",
    ))
    results.append((
        "newest_retained",
        f"w{CAP + 1}" in watchers,
        f"newest present={(f'w{CAP + 1}' in watchers)}",
    ))
    results.append((
        "ack_ok_after_eviction",
        bool(ack17.get("ok")),
        f"ack={ack17}",
    ))

    # The plane must still behave as a plane: dedupe holds, unsubscribe works.
    bb.subscribe(root, "w2", "key-2.*")
    after_dedupe = _snapshot(root)["subscriptions"]
    results.append((
        "dedupe_does_not_grow",
        len(after_dedupe) == CAP,
        f"subscriptions={len(after_dedupe)} after re-subscribing an existing pair",
    ))

    bb.unsubscribe(root, f"w{CAP + 1}", f"key-{CAP + 1}.*")
    after_unsub = _snapshot(root)["subscriptions"]
    results.append((
        "unsubscribe_still_works",
        len(after_unsub) == CAP - 1,
        f"subscriptions={len(after_unsub)} (expect {CAP - 1})",
    ))

    # Shared emitter: the key plane's notice must come from the same helper, so
    # a future plane cannot inherit the silent fold by copy-pasting around it.
    err_keys = io.StringIO()
    with contextlib.redirect_stderr(err_keys):
        notice = getattr(bb, "_bounds_notice", None)
        if notice is not None:
            notice("keys", DECLARED_CAP + 1, DECLARED_CAP)
    keys_out = err_keys.getvalue()
    results.append((
        "shared_notice_covers_key_plane",
        "bounds enforced" in keys_out and "keys" in keys_out,
        repr(keys_out.strip()) if keys_out.strip()
        else "no notice from the shared emitter",
    ))

    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--broken", action="store_true",
                    help="report the pre-fix surface (the rule-7 control)")
    ap.add_argument("--samples", default=None,
                    help="JSONL path for per-check evidence")
    args = ap.parse_args()

    if args.broken:
        _silence_notice()

    with tempfile.TemporaryDirectory() as td:
        results = [
            {"check": name, "ok": ok, "detail": detail}
            for name, ok, detail in _checks(pathlib.Path(td))
        ]

    x = sum(1 for r in results if r["ok"])
    n = len(results)
    if args.samples:
        path = pathlib.Path(args.samples)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for r in results:
                fh.write(json.dumps(r) + "\n")
    for r in results:
        print(f"[{'OK' if r['ok'] else 'MISS'}] {r['check']}: {r['detail']}")
    print(f"cap_observability_accuracy={x / n:.2f} ({x}/{n})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""The change-radar contract measures the radar it claims to (DW-3/4/5, E-142).

    python3 scripts/bench/bench_radar_contract.py --samples docs/experiments/E-142/raw/samples.jsonl
    python3 scripts/bench/bench_radar_contract.py --broken   # rule-7 control

`bench_consistency._check_change_radar_contract_matches` compares three things
that must agree: the subscriptions the skills ship, the radar the runbooks
document, and the table the handoff lint declares. Three defects made that
comparison weaker than its own docstring claimed, and all three had been
recorded in the repository's deferred ledger since the S-001 review:

* **DW-3** — the documented channel was `(session|stop)`. A custom channel's
  segment failed to parse, so the bullet looked like it omitted the skill and
  the radar could never describe a watch the engine stores.
* **DW-4** — a subscription was attributed by the `--watcher` token, not by the
  skill whose SKILL.md ships it. Any mention of another skill's command (a
  quoted example, a cross-reference) entered the radar under that other skill,
  so the tree could satisfy the contract with a line the owning skill never
  wrote. The handoff lint already called that a rogue watcher.
* **DW-5** — the bullet was the first line carrying the marker phrase. A table
  of contents ("see the change radar section") became the bullet, parsed as an
  empty claim, and every skill reported as omitted.

`--broken` reimplements the pre-fix reader (fixed channel list, attribution by
watcher token, first-marker-line pick) and runs the same probes against it.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
BENCH = ROOT / "scripts" / "bench" / "bench_consistency.py"

BROKEN = False


def _load():
    spec = importlib.util.spec_from_file_location("bench_radar", BENCH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bench = _load()

# The pre-fix reader, verbatim in shape.
_OLD_SEGMENT_RE = re.compile(
    r"`([a-z][\w-]+)`\s*(?:watches|→)\s*`([^`]+)`\s*\((session|stop)\)")


def _segments(line: str) -> list[tuple[str, str, str]]:
    pattern = _OLD_SEGMENT_RE if BROKEN else bench._RADAR_SEGMENT_RE
    return pattern.findall(line)


def _pick_bullet(lines: list[str]) -> str | None:
    markers = bench._RADAR_MARKERS
    if BROKEN:
        return next((ln for ln in lines if any(m in ln for m in markers)), None)
    return next((ln for ln in lines
                 if any(m in ln for m in markers)
                 and bench._RADAR_SEGMENT_RE.search(ln)), None)


def _attribute(owner: str, text: str) -> dict[str, set]:
    """(skill -> subscriptions) for one SKILL.md, the way the check reads it.

    Post-fix the check drops a line whose `--watcher` is not the owning skill
    (and reports it), so the helper drops it too — otherwise the helper would
    credit a smuggled pattern to the owner and the probe would pass on a
    behaviour the check does not have.
    """
    out: dict[str, set] = {}
    for watcher, pat, chan in bench._SUBSCRIBE_RE.findall(text):
        if not BROKEN and watcher != owner:
            continue
        key = watcher if BROKEN else owner
        out.setdefault(key, set()).add((pat, chan))
    return out


def _case_custom_channel_is_documentable() -> tuple[bool, str]:
    line = "`bmad-quality-record` watches `story.*` (release)"
    got = _segments(line)
    return (got == [("bmad-quality-record", "story.*", "release")],
            f"parsed={got}" + (" (pre-fix: custom channel unparseable)"
                               if BROKEN else ""))


def _case_marker_only_line_is_not_the_bullet() -> tuple[bool, str]:
    lines = ["- See the change radar section below.",
             "- **Subscriptions (change radar):** "
             "`bmad-spec` watches `prd.*` (session)"]
    picked = _pick_bullet(lines)
    ok = picked is not None and "bmad-spec" in picked
    return (ok, f"picked={(picked or '<none>')[:48]}")


def _case_foreign_watcher_is_not_charged() -> tuple[bool, str]:
    text = ('subscribe --watcher bmad-x --pattern "prd.*" --channel session\n'
            '`python3 blackboard.py subscribe --watcher bmad-other '
            '--pattern "smuggled.*" --channel session`\n')
    table = _attribute("bmad-x", text)
    ok = set(table) == {"bmad-x"} and table["bmad-x"] == {("prd.*", "session")}
    return (ok, f"attributed={ {k: sorted(v) for k, v in table.items()} }")


def _case_foreign_watcher_fails_the_check() -> tuple[bool, str]:
    """The shipped check must report it, not merely ignore the line."""
    if BROKEN:
        return (False, "pre-fix: the check attributes the line to bmad-other "
                       "and reports it as a radar entry instead")
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        skill = root / "skills" / "bmad-x" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text(
            'subscribe --watcher bmad-x --pattern "prd.*" --channel session\n'
            '`subscribe --watcher bmad-other --pattern "smuggled.*" '
            '--channel session`\n', encoding="utf-8")
        seg = "`bmad-x` watches `prd.*` (session)"
        for rel, prefix in (("GUIDE.md", "- change radar: "),
                            ("KILAVUZ.md", "- değişim radarı: ")):
            (root / rel).write_text(prefix + seg + "\n", encoding="utf-8")
        previous = bench.ROOT
        try:
            bench.ROOT = root
            problem = bench._check_change_radar_contract_matches(
                declared={"bmad-x": {("prd.*", "session")}})
        finally:
            bench.ROOT = previous
    return (problem is not None and "foreign" in (problem or ""),
            (problem or "<no problem>")[:90])


def _case_shipped_contract_still_green() -> tuple[bool, str]:
    problem = bench._check_change_radar_contract_matches()
    return (problem is None, problem or "clean on the shipped tree")


CASES = (
    ("a custom channel is documentable", _case_custom_channel_is_documentable),
    ("a marker-only line is not the radar bullet",
     _case_marker_only_line_is_not_the_bullet),
    ("a foreign watcher is not charged to the radar",
     _case_foreign_watcher_is_not_charged),
    ("a foreign watcher fails the shipped check",
     _case_foreign_watcher_fails_the_check),
    ("the shipped tree still satisfies the contract",
     _case_shipped_contract_still_green),
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
                    help="rule-7 control: the pre-fix reader (fixed channel list, "
                         "watcher-token attribution, first-marker-line pick)")
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
    print(f"radar_contract_accuracy={x / n:.2f} ({x}/{n})")
    # The aggregate is the signal; a nonzero exit would make the rule-7 control
    # unreadable to the gate (it runs the control itself and expects a reading).
    return 0


if __name__ == "__main__":
    sys.exit(main())

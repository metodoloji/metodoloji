#!/usr/bin/env python3
"""The published release chain obeys the count rule (E-155).

    python3 scripts/bench/bench_release_tags.py --samples docs/experiments/E-155/raw/samples.jsonl
    python3 scripts/bench/bench_release_tags.py --broken   # rule-7 control

`scripts/versioning.py` owns the number: version(position) =
0.<1 + position // 100>.<position % 100>, anchored at the first commit, and the
tag chain publishes it. Measured 2026-11-14 at HEAD (position 191, every
manifest says 0.2.91): seven tags sat one commit late, positions 130 and 140
carried no tag of their own, and positions 148..191 were never published — the
newest pointer, v0.2.47, was 44 commits behind a tree that already said 0.2.91.

The tree cannot be un-committed, and rewriting commits would invalidate every
SHA; what was wrong is where the published POINTERS sit. `--broken` evaluates
the same five probes against the tag map captured before the repair
(docs/experiments/E-155/raw/control-tags.json), so the control shows the drift
this repair removes.

Stdlib + git, offline, deterministic.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import versioning  # noqa: E402  (the rule lives in one place, E-013)

SNAPSHOT_REL = "docs/experiments/E-155/raw/control-tags.json"
RELEASE_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
VERSION_FILES = ("pyproject.toml", "plugin.yaml", ".plugin/plugin.json",
                 ".claude-plugin/plugin.json", ".opencode-plugin/plugin.json")
BROKEN = False


def _git(*args: str):
    proc = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          stdin=subprocess.DEVNULL)
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


def _history() -> list[str]:
    """Every commit from the baseline (position 0) to HEAD, oldest first."""
    code, first, err = _git("rev-list", "--max-parents=0", "HEAD")
    if code != 0:
        raise RuntimeError(f"git cannot read the history: {err[:160]}")
    baseline = first.splitlines()[-1]
    code, order, err = _git("rev-list", "--reverse", f"{baseline}..HEAD")
    if code != 0:
        raise RuntimeError(f"git cannot walk the history: {err[:160]}")
    return [baseline] + [sha for sha in order.split() if sha]


def _live_tags() -> dict[str, str]:
    code, out, err = _git(
        "for-each-ref",
        "--format=%(refname:short)|%(objecttype)|%(objectname)|%(*objectname)",
        "refs/tags")
    if code != 0:
        raise RuntimeError(f"git cannot list tags: {err[:160]}")
    tags: dict[str, str] = {}
    for row in out.splitlines():
        name, _kind, obj, peeled = row.split("|")
        tags[name] = peeled or obj  # annotated: the peeled commit
    return tags


def _snapshot_tags() -> dict[str, str]:
    data = json.loads((ROOT / SNAPSHOT_REL).read_text(encoding="utf-8"))
    tags = (data or {}).get("tags") or {}
    if not tags:
        raise RuntimeError(f"{SNAPSHOT_REL} carries no tags to measure")
    return {name: sha for name, sha in tags.items()}


def _release_tags(tags: dict[str, str]) -> dict[str, str]:
    return {name: sha for name, sha in tags.items() if RELEASE_RE.match(name)}


def _tag_for(position: int) -> str:
    return "v" + versioning.auto_version(position)


def _state() -> dict:
    shas = _history()
    return {"shas": shas,
            "positions": {sha: i for i, sha in enumerate(shas)},
            "tags": _snapshot_tags() if BROKEN else _live_tags()}


def _versions_at(sha: str) -> dict[str, str] | None:
    """The version each version-owned manifest holds in that commit's tree."""
    found: dict[str, str] = {}
    for rel in VERSION_FILES:
        code, text, _ = _git("show", f"{sha}:{rel}")
        if code != 0:
            return None
        # pyproject/plugin.yaml write `version = 0.2.91`; the JSON manifests
        # write `"version": "0.2.91",` — one pattern must read all five.
        match = re.search(r'^[ \t]*"?version"?\s*[:=]\s*"?([^"\s,]+)"?',
                          text, re.MULTILINE)
        if not match:
            return None
        found[rel] = match.group(1)
    return found


# --- the probes ---------------------------------------------------------------

def _case_positions(state: dict) -> tuple[bool, str]:
    """The PUBLISHED run must be contiguous from 0; a shorter tail is legal.

    The owner rule (bench_consistency._check_release_numbering_rule) says the
    chain "may stop short of HEAD (tags are pushed separately); it may not skip
    a number in between" (E-013). Requiring HEAD made this probe red on every
    commit after approval, measuring the absence of a CI run rather than the
    chain — the E-155 instrument's defect, corrected here (E-157).
    """
    published = [state["positions"][sha]
                 for sha in _release_tags(state["tags"]).values()
                 if sha in state["positions"]]
    if not published:
        return False, "no release tag resolves inside this history"
    end = max(published)
    missing, misplaced = [], []
    for position in range(end + 1):
        name = _tag_for(position)
        at = state["tags"].get(name)
        if at is None:
            missing.append(name)
        elif at != state["shas"][position]:
            misplaced.append((name, state["positions"].get(at), position))
    if missing or misplaced:
        bits = []
        if missing:
            shown = ", ".join(missing[:3]) + ("…" if len(missing) > 3 else "")
            bits.append(f"{len(missing)} gap(s) inside the published run ({shown})")
        if misplaced:
            shown = ", ".join(f"{n} at #{a} not #{w}" for n, a, w in misplaced[:3])
            bits.append(f"{len(misplaced)} misplaced ({shown}"
                        + ("…" if len(misplaced) > 3 else "") + ")")
        return False, "; ".join(bits)
    return True, (f"positions 0..{end} carry their rule tag "
                  f"({end + 1} published; the tail is legal to leave unpublished)")


def _case_containment(state: dict) -> tuple[bool, str]:
    outside = sorted(name for name, sha in _release_tags(state["tags"]).items()
                     if sha not in state["positions"])
    if outside:
        return False, f"{outside} point outside this history"
    return True, "every release tag resolves inside the enumerated history"


def _case_producible(state: dict) -> tuple[bool, str]:
    bad = []
    for name in sorted(state["tags"]):
        if not RELEASE_RE.match(name):
            continue
        try:
            versioning.auto_position(name[1:])
        except versioning.VersionError as exc:
            bad.append(f"{name}: {exc}")
    if bad:
        return False, "; ".join(bad[:3])
    return True, "every release tag name is a number the rule can produce"


def _case_newest_tree(state: dict) -> tuple[bool, str]:
    placed = [(state["positions"][sha], name, sha)
              for name, sha in _release_tags(state["tags"]).items()
              if sha in state["positions"]]
    if not placed:
        return False, "no release tag resolves inside this history"
    _position, name, sha = max(placed)
    versions = _versions_at(sha)
    if versions is None:
        return False, f"cannot read the manifests of the newest release {name}"
    wrong = {rel: version for rel, version in versions.items() if version != name[1:]}
    if wrong:
        return False, f"the newest release {name} points at manifests holding {wrong}"
    return True, f"the newest release {name} holds its own number in all five files"


PROBES = (
    ("the published run is contiguous and correctly placed", _case_positions),
    ("no release tag points outside this history", _case_containment),
    ("every release tag name is producible by the rule", _case_producible),
    ("the newest release's tree holds its own number", _case_newest_tree),
)


def run(state: dict) -> list[dict]:
    results = []
    for name, probe in PROBES:
        try:
            ok, detail = probe(state)
        except Exception as exc:  # a crashed probe is a failed check, never a pass
            ok, detail = False, f"probe crashed: {type(exc).__name__}: {exc}"
        results.append({"check": name, "ok": bool(ok), "detail": detail})
    return results


def main() -> int:
    global BROKEN
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--broken", action="store_true", help="rule-7 control arm")
    parser.add_argument("--samples", help="write per-probe JSONL samples here")
    args = parser.parse_args()
    BROKEN = args.broken

    results = run(_state())
    for result in results:
        print(f"  {'ok  ' if result['ok'] else 'FAIL'}  {result['check']}: {result['detail']}")
    passed = sum(1 for result in results if result["ok"])
    if args.samples:
        path = pathlib.Path(args.samples)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(result, ensure_ascii=False) + "\n"
                                for result in results), encoding="utf-8")
    print(f"release_tag_accuracy={passed / len(results):.2f} ({passed}/{len(results)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

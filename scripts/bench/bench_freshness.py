#!/usr/bin/env python3
"""bench_freshness — does a hand-off signal whose claim has moved on get
delivered as LIVE work?

The failure mode this measures (the report that opened E-052): "Yesterday's
decision is still in the handoff file, and the agent obeys it after the code
has already moved on." A board signal (`handoff.<skill>` alert) is a *claim*
about the code state at the moment it was posted. The engine's read surfaces
(`pending_handoffs`, the session-start inject) deliver it without ever asking
whether that claim still holds.

Two delivery surfaces are probed per signal, and the expected verdict comes
from an ORACLE that never imports the module under test — plain `git` plus the
referenced record's own `Code Scope`:

    expected STALE  ⇔  a file matching the record's scope changed after the
                       commit that last touched the record
    expected CURRENT ⇔  no scoped file moved

Trial verdicts:

    stale_delivery_rate = delivered-as-live while expected STALE  (the defect)
    false_alarm_rate    = delivered-as-stale while expected CURRENT (noise)

Baseline (before E-053's fix) both surfaces carry no verdict at all, so every
stale signal is delivered as live → stale_delivery_rate = 1.0. After the fix
the peek surface carries an explicit `verdict` and the inject labels each key,
so the rate drops to 0.0 while the false-alarm rate stays 0.0.

`--baseline` reproduces the pre-fix delivery semantics (a surface that carries
no verdict at all: every claim reads as live work) so E-052's recorded red
stays reproducible on a fixed tree instead of living only in a commit message.
It is also this bench's own negative test: flipping the switch must turn the
measured rate back to 1.0, otherwise the bench would "pass" without the
freshness dimension it claims to measure.

Self-contained: a throwaway git repo per scenario, one per way the code state
can move — A: a COMMITTED change after the claims were recorded; B: an
UNCOMMITTED edit (the dirty tree the witness digest covers, and the most
common shape in an agent session, where edits sit uncommitted for a while).
Stdlib + `git` only, no network, no dependency on the checkout or gate key.
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

_PLUGIN = pathlib.Path(__file__).resolve().parents[2]
_ENGINE = _PLUGIN / "hooks" / "engine"
if str(_ENGINE) not in sys.path:
    sys.path.insert(0, str(_ENGINE))

from modules import audit as audit_mod  # noqa: E402
from modules import blackboard as bb  # noqa: E402

STALE_RECEIVER = "bmad-check-implementation-readiness"
CURRENT_RECEIVER = "bmad-architecture"

_GIT_TIMEOUT = 30

# Pre-fix delivery semantics: the read surfaces carried no freshness dimension,
# so a claim could only arrive as live work. See the module docstring.
BASELINE = False


# --- fixture plumbing ---------------------------------------------------------
def _git(root: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=_GIT_TIMEOUT,
        stdin=subprocess.DEVNULL)


def _commit(root: pathlib.Path, message: str) -> None:
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=bench@example.invalid",
         "-c", "user.name=bench", "commit", "-q", "-m", message)


def _record_text(key: str, scope: str) -> str:
    return (
        f"## Experiment: {key} — freshness bench fixture\n"
        f"- **Status:** completed\n"
        f"- **Theory:** fixture\n"
        f"- **Hypothesis:** H-900: \"fixture_score >= 0.9\"\n"
        f"- **Measurement Metrics:** fixture_score >= 0.9\n"
        f"- **Experiment Design:** fixture\n"
        f"- **Code Scope:** {scope}\n"
        f"- **Lineage:** none\n"
        f"- **Raw Results:** measured=1.0; n=1\n"
        f"- **Uncertainty:** none\n"
        f"- **Metric:** consistent\n"
        f"- **Decision:** APPROVED — H-900: measured=1.0 >= threshold=0.9\n"
        f"- **Gate Evidence:** measured=1.0 claim=\"fixture_score >= 0.9\" "
        f"GATE-OK-{key}-benchfixture0000000000000000000000\n"
        f"- **Next Step:** Proceed to Code\n")


def _project(tmp: pathlib.Path, records: dict[str, str]) -> pathlib.Path:
    """A git repo: baseline commit with the records + two scope trees."""
    root = tmp / "project"
    (root / "docs" / "experiments").mkdir(parents=True)
    (root / "src").mkdir(parents=True)
    (root / "lib").mkdir(parents=True)
    (root / "src" / "engine.py").write_text("# engine v1\n", encoding="utf-8")
    (root / "lib" / "util.py").write_text("# util v1\n", encoding="utf-8")
    for key, scope in records.items():
        (root / "docs" / "experiments" / f"{key}.md").write_text(
            _record_text(key, scope), encoding="utf-8")
    _git(root, "init", "-q")
    _commit(root, "baseline")
    return root


def _scope_prefix(scope: str) -> str:
    """`src/**` → `src/` (the fixture scopes are prefix globs by construction)."""
    return scope.strip().strip(",").replace("**", "").rstrip("/") + "/"


def _scope_of(root: pathlib.Path, key: str) -> str:
    text = (root / "docs" / "experiments" / f"{key}.md").read_text(
        encoding="utf-8", errors="replace")
    m = re.search(r"^-\s*\*\*Code Scope:\*\*\s*(.+?)\s*$", text, re.MULTILINE)
    return m.group(1).strip() if m else ""


def _oracle_stale(root: pathlib.Path, key: str) -> bool:
    """Independent truth: did a scoped file move after the record's own commit?

    Deliberately does NOT import the freshness module: the expected verdict
    must be derivable without the code under test (or the bench would measure
    itself). Plain git + the record's declared scope.
    """
    rel = f"docs/experiments/{key}.md"
    out = _git(root, "log", "-1", "--format=%H", "--", rel)
    commit = out.stdout.strip()
    if not commit:
        return False
    changed = [l.strip() for l in _git(root, "diff", "--name-only",
                                       f"{commit}..HEAD").stdout.splitlines()
               if l.strip()]
    # Uncommitted work counts too: a modified tracked file and an untracked one
    # are both "the code moved" (scenario B is exactly that shape).
    for line in _git(root, "status", "--porcelain", "--untracked-files=all"
                     ).stdout.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip().strip('"')
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path and path not in changed:
            changed.append(path)
    prefix = _scope_prefix(_scope_of(root, key))
    return any(c.replace("\\", "/").startswith(prefix) for c in changed)


def _post(root: pathlib.Path, receiver: str, key: str, note: str = "bench probe") -> None:
    ack = bb.post_handoff(root, receiver, key, note=note)
    if not ack.get("ok"):
        raise RuntimeError(f"post_handoff failed: {ack}")


# --- delivery surfaces --------------------------------------------------------
def _peek_verdict(root: pathlib.Path, receiver: str, key: str) -> str:
    """The verdict the peek surface delivers (`CURRENT` when it carries none)."""
    if BASELINE:
        # The pre-E-053 surface had no verdict to deliver at all.
        return "CURRENT"
    for item in bb.pending_handoffs(root, receiver):
        if str(item.get("text", "")).startswith(f"{key}:"):
            return str(item.get("verdict") or "CURRENT").upper()
    return "CURRENT"


_LABEL_RE_TMPL = r"{key}\s*\((STALE|CURRENT|UNKNOWN|RETIRED)"


def _inject_verdict(root: pathlib.Path, key: str) -> str:
    """The verdict the session-start inject delivers for `key`.

    Unlabelled keys read as CURRENT: an inject that never names the claim's
    state delivers it as live work, which is exactly the defect being measured.
    """
    if BASELINE:
        return "CURRENT"  # pre-E-053 inject: no label, so the claim reads LIVE
    env_backup = {k: os.environ.pop(k, None)
                  for k in ("CLAUDE_PROJECT_DIR", "OPENHANDS_PROJECT_DIR")}
    try:
        text = str(audit_mod.session_start({"cwd": str(root)})
                   .get("additionalContext", ""))
    finally:
        for k, v in env_backup.items():
            if v is not None:
                os.environ[k] = v
    m = re.search(_LABEL_RE_TMPL.format(key=re.escape(key)), text)
    return m.group(1).upper() if m else "CURRENT"


# --- scenarios ----------------------------------------------------------------
def _scenario_a(tmp: pathlib.Path) -> list[tuple[str, str, bool]]:
    """Signals posted BEFORE the code moved; the record is the only dated anchor.

    E-900/E-902/E-903 declare `src/**` (the tree the commit below touches) →
    every one of them is stale afterwards. E-901/E-904/E-905 declare `lib/**`
    (untouched) → the same commit must NOT stale them.
    """
    records = {"E-900": "src/**", "E-901": "lib/**", "E-902": "src/**",
               "E-903": "src/**", "E-904": "lib/**", "E-905": "lib/**"}
    root = _project(tmp / "A", records)
    for key in ("E-900", "E-902", "E-903"):
        _post(root, STALE_RECEIVER, key)
    for key in ("E-901", "E-904", "E-905"):
        _post(root, CURRENT_RECEIVER, key)
    # The code moves on — the decision did not.
    (root / "src" / "engine.py").write_text("# engine v2\n", encoding="utf-8")
    _commit(root, "engine moves on")
    trials = []
    for key in ("E-900", "E-901", "E-902", "E-903", "E-904", "E-905"):
        receiver = STALE_RECEIVER if _scope_of(root, key) == "src/**" else CURRENT_RECEIVER
        expected = _oracle_stale(root, key)
        trials.append((key, _peek_verdict(root, receiver, key), expected))
        trials.append((key + "/inject", _inject_verdict(root, key), expected))
    return trials


def _scenario_b(tmp: pathlib.Path) -> list[tuple[str, str, bool]]:
    """The code moves WITHOUT a commit: nothing but the witness digest can see it.

    E-910 (scope `src/**`) is edited in the work tree and left uncommitted
    while E-911's scope (`lib/**`) is untouched — a plane that only watched
    commits would deliver both as live work.
    """
    records = {"E-910": "src/**", "E-911": "lib/**"}
    root = _project(tmp / "B", records)
    for key in ("E-910", "E-911"):
        _post(root, STALE_RECEIVER if _scope_of(root, key) == "src/**"
              else CURRENT_RECEIVER, key)
    # Uncommitted edit inside the claim's scope: the decision is already wrong.
    (root / "src" / "engine.py").write_text("# engine v3 (uncommitted)\n",
                                             encoding="utf-8")
    trials = []
    for key in ("E-910", "E-911"):
        receiver = STALE_RECEIVER if _scope_of(root, key) == "src/**" else CURRENT_RECEIVER
        expected = _oracle_stale(root, key)
        trials.append((key, _peek_verdict(root, receiver, key), expected))
        trials.append((key + "/inject", _inject_verdict(root, key), expected))
    return trials


def measure() -> tuple[int, int, int, int, list[str]]:
    """Run both scenarios; return (leaks, n_stale, false_alarms, n_current,
    report lines)."""
    kept = os.environ.pop("METODOLOJI_NO_BLACKBOARD", None)
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="bench-freshness-"))
    lines: list[str] = []
    leaks = stale_n = false_alarms = current_n = 0
    try:
        for name, builder in (("A(committed change)", _scenario_a),
                             ("B(uncommitted edit)", _scenario_b)):
            trials = builder(tmp)
            for label, delivered, expected in trials:
                if expected:
                    stale_n += 1
                    if delivered != "STALE":
                        leaks += 1
                        lines.append(f"  LEAK   {name} {label}: stale claim "
                                     f"delivered as {delivered}")
                else:
                    current_n += 1
                    if delivered == "STALE":
                        false_alarms += 1
                        lines.append(f"  NOISE  {name} {label}: current claim "
                                     f"delivered as STALE")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        if kept is not None:
            os.environ["METODOLOJI_NO_BLACKBOARD"] = kept
    if not stale_n or not current_n:
        raise RuntimeError("fixture produced no trials — build broken")
    return leaks, stale_n, false_alarms, current_n, lines


def main() -> int:
    global BASELINE
    BASELINE = "--baseline" in sys.argv[1:]
    try:
        leaks, stale_n, false_alarms, current_n, lines = measure()
    except Exception as exc:  # a bench that cannot run must not read as a pass
        print(f"BENCH ERROR: {exc}", file=sys.stderr)
        return 1
    for line in lines:
        print(line)
    print(f"trials: stale={stale_n} current={current_n} "
          f"surfaces=peek+inject"
          + (" mode=baseline(pre-E-053 delivery)" if BASELINE else ""))
    print(f"stale_delivery_rate={leaks / stale_n:.4f} ({leaks}/{stale_n})")
    print(f"false_alarm_rate={false_alarms / current_n:.4f} "
          f"({false_alarms}/{current_n})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Bounded auto-iterate loop: scan → eval → propose → apply/revert.

Turns the self-improvement.md 4-beat loop into a runnable harness. The loop's
MECHANICS are enforced in code: round bound, one change per round, revert on
regression, and a full typed trail file. The CONTENT of each proposed fix is
delegated to an external "improver" command (an LLM invocation) because writing
a skill fix is a language act, not a mechanical one — the harness guarantees the
discipline around it.

The evaluator is a command that, given a skill path, prints a JSON score:
  {"score": 0.0..1.0, "modes": {...}}   higher is better.

Usage:
  python3 auto_iterate.py --skill SKILL.md --eval CMD --improve CMD \\
      --rounds 5 --pass-threshold 0.9 --trail PATH

  --eval CMD      shell command; {skill} is substituted with the skill path
  --improve CMD   shell command; {skill} is substituted, receives the finding
                  on stdin, and must write the improved skill to stdout (or to
                  a path printed on stdout). The harness applies it.
  --rounds N      hard stop (default 5)
  --pass-threshold F  stop early when score >= F (default 0.9)
  --trail PATH    where to write the typed trail (default: run dir)

Exit: 0 when the pass condition was met, 1 when rounds ran out without passing,
2 on usage error.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path


def _log(trail: Path, entry_type: str, text: str) -> None:
    """Append a typed line to the trail (append-only, plain markdown)."""
    trail.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%dT%H:%M")
    with open(trail, "a", encoding="utf-8") as fh:
        fh.write(f"- ({entry_type}) {text}  ({stamp})\n")


def _run(cmd: str, skill: str, stdin_text: str = "") -> tuple[int, str]:
    """Run a command with {skill} substituted. Returns (rc, stdout)."""
    real = cmd.replace("{skill}", skill)
    try:
        proc = subprocess.run(
            real, shell=True, input=stdin_text,
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=600,
        )
        return proc.returncode, proc.stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


def _parse_score(stdout: str) -> float:
    """Best-effort parse of a score from eval stdout (JSON or bare float)."""
    stdout = stdout.strip()
    try:
        return float(stdout)
    except ValueError:
        pass
    try:
        data = json.loads(stdout)
        if isinstance(data, dict):
            return float(data.get("score", 0.0))
        return float(data)
    except (ValueError, json.JSONDecodeError):
        return 0.0


def _apply_improvement(skill: Path, improve_cmd: str) -> tuple[bool, str]:
    """Run the improver; apply its stdout as the new skill. Returns (ok, detail)."""
    rc, out = _run(improve_cmd, str(skill))
    if rc != 0 or not out.strip():
        return False, f"improver failed (rc={rc})"
    # The improver may print a path (to a file it wrote) or the content directly.
    maybe_path = out.strip().splitlines()[-1].strip()
    candidate = Path(maybe_path)
    if candidate.is_file() and ".md" in maybe_path.lower():
        content = candidate.read_text(encoding="utf-8")
    else:
        content = out
    skill.write_text(content, encoding="utf-8")
    return True, f"applied (len={len(content)})"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--skill", required=True, type=Path)
    p.add_argument("--eval", required=True, help="shell cmd; {skill} substituted; prints score")
    p.add_argument("--improve", required=True, help="shell cmd; {skill} substituted; reads finding on stdin, writes new skill to stdout")
    p.add_argument("--rounds", type=int, default=5)
    p.add_argument("--pass-threshold", type=float, default=0.9)
    p.add_argument("--trail", type=Path, default=None)
    p.add_argument("--finding", default="improve the skill",
                   help="finding passed to the improver each round")
    args = p.parse_args(argv)

    if not args.skill.is_file():
        print(f"skill not found: {args.skill}", file=sys.stderr)
        return 2
    trail = args.trail or Path(f".auto-iterate-{args.skill.stem}.trail.md")

    _log(trail, "event", f"auto-iterate start: skill={args.skill}, rounds={args.rounds}, threshold={args.pass_threshold}")

    best_score = -1.0
    best_content = args.skill.read_text(encoding="utf-8")
    for rnd in range(1, args.rounds + 1):
        # Beat 1: eval the CURRENT skill.
        rc, out = _run(args.eval, str(args.skill))
        score = _parse_score(out) if rc == 0 else 0.0
        _log(trail, "event", f"round {rnd}: eval score={score} (rc={rc})")
        if score >= args.pass_threshold:
            _log(trail, "decision", f"round {rnd}: PASS (score {score} >= {args.pass_threshold}) — stop")
            print(f"PASS at round {rnd} (score {score})")
            return 0
        if score > best_score:
            best_score = score
            best_content = args.skill.read_text(encoding="utf-8")

        # Beat 2: propose + apply ONE change.
        _log(trail, "decision", f"round {rnd}: propose fix — {args.finding}")
        ok, detail = _apply_improvement(args.skill, args.improve)
        if not ok:
            _log(trail, "note", f"round {rnd}: improver failed ({detail}) — revert")
            args.skill.write_text(best_content, encoding="utf-8")
            continue

        # Beat 3: re-eval; revert if regression.
        rc2, out2 = _run(args.eval, str(args.skill))
        new_score = _parse_score(out2) if rc2 == 0 else 0.0
        _log(trail, "event", f"round {rnd}: re-eval score={new_score} (was {score})")
        if new_score < score:
            _log(trail, "note", f"round {rnd}: regression ({new_score} < {score}) — revert")
            args.skill.write_text(best_content, encoding="utf-8")
            continue
        # Improved: keep, continue to next round.
        best_score = new_score
        best_content = args.skill.read_text(encoding="utf-8")
        _log(trail, "direction", f"round {rnd}: kept improvement (score {new_score})")

    _log(trail, "direction",
         f"auto-iterate end: rounds exhausted, best score {best_score}, "
         f"threshold {args.pass_threshold} NOT met")
    print(f"ROUNDS EXHAUSTED (best {best_score})")
    return 1


if __name__ == "__main__":
    sys.exit(main())

"""Rollouts: run the frozen agent against the current artifact.

A rollout is the unit of evidence. It carries the case it ran, whether it
passed, and the agent's output, so a later reader can re-grade without re-running
the agent. The runner is a seam, exactly like Hermit's ``acting agent`` and
SkillOpt's backend: the loop never knows whether the score came from a scripted
offline task or a Codex process.

Two runners ship:

``ScriptedRunner``
    Deterministic, offline, no model. A case passes when all of its
    ``requires`` tokens appear in the artifact. This is not a benchmark of a
    model; it is a contract check for the *optimizer* — the loop, the gate, the
    budget, the version chain — used by the tests and by the E-097 bench.

``CommandRunner``
    Runs an external agent per case with the artifact path, case id and prompt
    in the environment, and reads one JSON verdict from stdout. No shell
    interpolation of the prompt is needed, which keeps a prompt from becoming
    shell syntax.
"""
from __future__ import annotations

import dataclasses
import json
import os
import pathlib
import shlex
import subprocess
from typing import Protocol, Sequence

from .cases import Case


class RunnerError(RuntimeError):
    """The runner could not produce a verdict; never treat that as a fail."""


@dataclasses.dataclass(frozen=True)
class Rollout:
    case_id: str
    ok: bool
    output: str = ""
    score: float | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id, "ok": self.ok, "output": self.output,
            "score": self.score, "error": self.error,
        }


class Runner(Protocol):
    name: str

    def run(self, artifact_text: str, case: Case) -> Rollout: ...


class ScriptedRunner:
    """Token-coverage runner: the artifact must teach the case's requirements."""

    name = "scripted"

    def __init__(self, noise: float = 0.0, seed: int = 0) -> None:
        if not 0.0 <= noise <= 1.0:
            raise ValueError("noise must be within [0, 1]")
        self.noise = noise
        self.seed = seed

    def _flip(self, case_id: str) -> bool:
        if self.noise <= 0.0:
            return False
        import hashlib

        digest = hashlib.sha256(f"{self.seed}:{case_id}".encode("utf-8")).hexdigest()
        return int(digest[:8], 16) / 0xFFFFFFFF < self.noise

    def run(self, artifact_text: str, case: Case) -> Rollout:
        required = case.requires or ((case.expected,) if case.expected else ())
        missing = [token for token in required if token not in artifact_text]
        ok = not missing
        if self._flip(case.id):
            ok = not ok
        output = "missing: " + ", ".join(missing) if missing else "requirements covered"
        return Rollout(case_id=case.id, ok=ok, output=output)


class CommandRunner:
    """Run a real agent as ``cmd {artifact} {case_id}`` and read a JSON verdict.

    The prompt is exported as ``OPTIMIZE_CASE_INPUT`` rather than substituted
    into the command line, so a prompt containing quotes, ``$`` or newlines
    cannot change what is executed. A non-zero exit, a timeout or unparsable
    stdout raise :class:`RunnerError`: an infrastructure failure must not be
    scored as a candidate failure, or the gate would reject good edits for the
    wrong reason.
    """

    name = "command"

    def __init__(self, template: str, timeout: int = 300) -> None:
        self.argv = shlex.split(template, posix=os.name != "nt")
        if not self.argv:
            raise RunnerError("empty runner command")
        self.timeout = timeout

    def run(self, artifact_text: str, case: Case) -> Rollout:
        artifact = pathlib.Path(f".optimization-case-{case.id}.artifact.md")
        artifact.write_text(artifact_text, encoding="utf-8")
        env = dict(os.environ)
        env.update({
            "OPTIMIZE_ARTIFACT": str(artifact.resolve()),
            "OPTIMIZE_CASE_ID": case.id,
            "OPTIMIZE_CASE_INPUT": case.input,
            "OPTIMIZE_CASE_REQUIRES": json.dumps(list(case.requires)),
        })
        argv = [part.format(artifact=str(artifact.resolve()), case_id=case.id)
                for part in self.argv]
        try:
            proc = subprocess.run(argv, capture_output=True, text=True,
                                  timeout=self.timeout, env=env)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RunnerError(f"runner failed for {case.id}: {exc}") from exc
        finally:
            artifact.unlink(missing_ok=True)
        if proc.returncode != 0:
            raise RunnerError(
                f"runner exited {proc.returncode} for {case.id}: "
                f"{(proc.stderr or proc.stdout)[:400]}"
            )
        try:
            verdict = json.loads(proc.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError) as exc:
            raise RunnerError(
                f"runner stdout for {case.id} is not a JSON verdict: "
                f"{proc.stdout[:200]!r}"
            ) from exc
        if not isinstance(verdict.get("ok"), bool):
            raise RunnerError(f"runner verdict for {case.id} has no boolean 'ok'")
        return Rollout(
            case_id=case.id,
            ok=verdict["ok"],
            output=str(verdict.get("output", "")),
            score=(float(verdict["score"]) if isinstance(verdict.get("score"), (int, float))
                   else None),
        )


def run_cases(runner: Runner, artifact_text: str,
              cases: Sequence[Case]) -> list[Rollout]:
    """Run every case once, in the given order, without deduplicating."""
    return [runner.run(artifact_text, case) for case in cases]


def verdicts(rollouts: Sequence[Rollout]) -> dict[str, bool]:
    """Case id -> pass, refusing a set with a duplicate or missing id."""
    out: dict[str, bool] = {}
    for rollout in rollouts:
        if rollout.case_id in out:
            raise RunnerError(f"duplicate rollout for case {rollout.case_id!r}")
        out[rollout.case_id] = rollout.ok
    if not out:
        raise RunnerError("no rollouts to score")
    return out


def rate(rollouts: Sequence[Rollout]) -> tuple[float, int, int]:
    """(rate, passes, total), rounded for reporting only — never for the gate."""
    total = len(rollouts)
    passed = sum(1 for r in rollouts if r.ok)
    if total == 0:
        raise RunnerError("cannot compute a rate over zero rollouts")
    return passed / total, passed, total

"""Measure the promotion surface (E-106).

    python scripts/bench/bench_promotion_integrity.py            # healthy
    python scripts/bench/bench_promotion_integrity.py --broken   # rule-7 control

Four groups of ten samples, one JSON object each with a boolean ``ok``:

1. runner wiring — ``eval``/``report`` must hand the Optimizer the runner the
   command was given (scripted or ``command:``), not an offline stub;
2. stored evidence — ``report(heldout=<stored>)`` must return the stored
   decision without re-measuring (the measurement is patched to raise);
3. single-version trees — ``evaluate_heldout`` must report ``applicable:
   false`` instead of comparing v0000 with itself;
4. two-version trees — the same call must report ``applicable: true``.

The control emulates the pre-fix surface: scripted runner for every command,
``report()`` re-measuring, and no ``applicable`` field at all. Deterministic,
offline, stdlib only.
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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from optimization import cli  # noqa: E402
from optimization.loop import Optimizer  # noqa: E402
from optimization.proposers import RuleProposer  # noqa: E402
from optimization.rollout import CommandRunner, ScriptedRunner  # noqa: E402
from optimization.spec import load_spec  # noqa: E402

TEMPLATES = (
    "scripted",
    "command:python --version",
    "command:python -c pass",
    "command:python runner.py --artifact {artifact} --case {case_id}",
    "command:python skills/example/evals/runner.py",
)
PAYLOADS = [{"applicable": True, "accepted": bool(i % 2),
             "reason": f"stored decision {i}"} for i in range(10)]
SHAPES = [(6, 6), (6, 8), (7, 6), (7, 8), (8, 6),
          (8, 8), (6, 7), (7, 7), (8, 7), (6, 9)]
ARTIFACT = (
    "# Skill under test\n\n"
    "A neutral document used only to exercise the promotion surface. It makes "
    "no claim about any case and is never trained here. "
) * 3


def build_project(root: pathlib.Path, val: int, heldout: int,
                  seed: int) -> pathlib.Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(ARTIFACT, encoding="utf-8")
    rows = [{"id": f"v{i}", "input": f"val prompt {i}", "requires": []}
            for i in range(val)]
    rows += [{"id": f"h{i}", "input": f"heldout prompt {i}", "requires": []}
             for i in range(heldout)]
    rows += [{"id": f"t{i}", "input": f"train prompt {i}", "requires": []}
             for i in range(6)]
    (root / "cases.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    (root / "optimization.toml").write_text(
        "[run]\nname='promotion'\nseed=7\nepochs=1\nbatch_size=1\n"
        f"val_size={val}\nheldout_size={heldout}\nmin_cases=6\n"
        "[artifact]\nkind='skill'\npath='SKILL.md'\nmax_tokens=2000\n"
        "[cases]\npath='cases.jsonl'\n"
        "[gate]\nrequire_heldout=true\n",
        encoding="utf-8")
    return root / "optimization.toml"


class _FakeStore:
    def entries(self) -> list[dict[str, object]]:
        return [{"version": "v0000"}, {"version": "v0001"}]


class _FakeReport:
    def as_dict(self) -> dict[str, object]:
        return {"carried_forward": "v0001"}


class _Recorder:
    """Stands in for Optimizer so the CLI's runner argument can be inspected."""

    last: "_Recorder | None" = None

    def __init__(self, spec, runner, proposer=None, predictor=None, splits=None):
        self.runner = runner
        _Recorder.last = self
        self.store = _FakeStore()

    def evaluate_heldout(self) -> dict[str, object]:
        return {"applicable": True, "accepted": True}

    def report(self, heldout=None) -> _FakeReport:
        return _FakeReport()


def runner_group(spec_path: pathlib.Path, broken: bool) -> list[bool]:
    original = cli.Optimizer
    cli.Optimizer = _Recorder
    outcomes: list[bool] = []
    try:
        for command in ("eval", "report"):
            for template in TEMPLATES:
                _Recorder.last = None
                namespace = type("Args", (), {
                    "spec": str(spec_path), "runner": template,
                    "json": True,
                })()
                with contextlib.redirect_stdout(io.StringIO()):
                    if command == "eval":
                        cli.cmd_eval(namespace)
                    else:
                        cli.cmd_report(namespace)
                expected = ScriptedRunner if broken else (
                    CommandRunner if template.startswith("command:")
                    else ScriptedRunner)
                outcomes.append(bool(_Recorder.last)
                                and isinstance(_Recorder.last.runner, expected))
    finally:
        cli.Optimizer = original
    return outcomes


def reuse_group(spec_path: pathlib.Path, broken: bool) -> list[bool]:
    optimizer = Optimizer(load_spec(spec_path), ScriptedRunner(), RuleProposer())
    optimizer.prepare()

    def must_not_measure() -> dict[str, object]:
        raise AssertionError("report() re-measured the held-out split")

    optimizer.evaluate_heldout = must_not_measure  # type: ignore[method-assign]
    outcomes: list[bool] = []
    for payload in PAYLOADS:
        try:
            if broken:
                result = optimizer.report()  # pre-fix: ignore the stored decision
            else:
                result = optimizer.report(heldout=payload)
        except AssertionError:
            outcomes.append(False)
            continue
        outcomes.append(result.heldout == payload)
    return outcomes


def version_group(spec_path: pathlib.Path, two_versions: bool,
                  broken: bool) -> list[bool]:
    optimizer = Optimizer(load_spec(spec_path), ScriptedRunner(), RuleProposer())
    optimizer.prepare()
    if two_versions:
        optimizer.store.commit(optimizer.store.artifact("v0000"), epoch=1,
                               parent="v0000", edit=None, gate=None, tokens=1)
    try:
        result = optimizer.evaluate_heldout()
    except Exception:
        return [False]
    applicable = result.get("applicable")
    if broken:
        # Pre-fix: no `applicable` field, so neither verdict is supported.
        return [False]
    return [applicable is True] if two_versions else [applicable is False]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--broken", action="store_true",
                        help="emulate the pre-fix promotion surface")
    parser.add_argument("--samples",
                        default="docs/experiments/E-106/raw/samples.jsonl")
    args = parser.parse_args()
    samples = ROOT / args.samples
    samples.parent.mkdir(parents=True, exist_ok=True)

    outcomes: list[bool] = []
    labels: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = pathlib.Path(tmp)
        wiring_spec = build_project(tmp_path / "wiring", 6, 6, 7)
        outcomes += runner_group(wiring_spec, args.broken)
        labels += [f"wiring:{i}" for i in range(10)]

        reuse_spec = build_project(tmp_path / "reuse", 6, 6, 7)
        outcomes += reuse_group(reuse_spec, args.broken)
        labels += [f"reuse:{i}" for i in range(10)]

        for index, (val, held) in enumerate(SHAPES):
            spec_path = build_project(tmp_path / f"one-{index}", val, held, 7)
            outcomes += version_group(spec_path, False, args.broken)
            labels.append(f"single:{index}")
        for index, (val, held) in enumerate(SHAPES):
            spec_path = build_project(tmp_path / f"two-{index}", val, held, 7)
            outcomes += version_group(spec_path, True, args.broken)
            labels.append(f"two:{index}")

    passed = sum(1 for ok in outcomes if ok)
    total = len(outcomes)
    with samples.open("w", encoding="utf-8", newline="\n") as fh:
        for index, (label, ok) in enumerate(zip(labels, outcomes)):
            fh.write(json.dumps({"i": index, "group": label, "ok": bool(ok)},
                                sort_keys=True) + "\n")
    print(f"promotion_integrity_accuracy={passed / total:.2f} ({passed}/{total})")
    if args.broken:
        return 0
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())

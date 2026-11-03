"""Command line for the optimization kernel.

    python -m optimization init    DIR [--name N] [--method M] [--kind K]
    python -m optimization methods [--json]
    python -m optimization split   --spec optimization.toml [--json]
    python -m optimization check   --spec ... [--json]
    python -m optimization train   --spec ... [--epochs N] [--runner ...] [--proposer ...]
    python -m optimization eval    --spec ... [--json]
    python -m optimization verify  --spec ... [--json]
    python -m optimization predict --spec ... [--json]
    python -m optimization report  --spec ... [--json]
    python -m optimization export  --spec ... --target hermit|skillopt --out DIR [--lane hermit|native]
    python -m optimization deploy  --adapter DIR --out DIR --host codex|claude-code|pi
    python -m optimization ingest  --target hermit|skillopt --path RESULT.json

Exit codes: 0 success, 2 usage/spec/preflight error, 3 the run completed but the
held-out promotion did not pass, 1 unexpected failure. A non-zero exit for an
unpromoted candidate is a result, not a crash.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

from . import preflight
from .bridges import deploy, hermit, skillopt
from .cases import assign_splits, leakage, load_cases
from .env import load_dotenv
from .loop import Optimizer, VersionStore
from .methods import MethodError, method_names, resolve
from .predictor import GainPredictor
from .proposers import CommandProposer, RuleProposer
from .rollout import CommandRunner, ScriptedRunner
from .spec import SpecError, init_spec, load_spec


def _runner(value: str):
    if value.startswith("command:"):
        return CommandRunner(value.split(":", 1)[1])
    return ScriptedRunner()


def _proposer(value: str):
    if value.startswith("command:"):
        return CommandProposer(value.split(":", 1)[1])
    return RuleProposer()


def _print(value: object, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, indent=2, sort_keys=True, default=str))
    else:
        print(value)


def cmd_init(args: argparse.Namespace) -> int:
    path = init_spec(args.directory, name=args.name, method=args.method,
                     kind=args.kind)
    print(f"wrote {path}")
    print(f"Next: add cases, then `python -m optimization check --spec {path}`")
    return 0


def cmd_methods(args: argparse.Namespace) -> int:
    rows = [resolve(name).as_dict() for name in method_names()]
    if args.json:
        _print(rows, True)
    else:
        for row in rows:
            print(f"{row['name']:<22} template={row['template']:<24} "
                  f"validation={row['needs_validation']}")
    return 0


def cmd_split(args: argparse.Namespace) -> int:
    spec = load_spec(args.spec)
    cases = load_cases(spec.cases_path)
    splits = assign_splits(cases, spec.seed, spec.val_size, spec.heldout_size)
    leaked = leakage(splits)
    payload = {
        "sizes": splits.size(),
        "leakage": leaked,
        "train": [c.id for c in splits.train],
        "val": [c.id for c in splits.val],
        "heldout": [c.id for c in splits.heldout],
    }
    if leaked:
        _print(payload, args.json)
        print(f"refused: {len(leaked)} cases share a prompt across splits",
              file=sys.stderr)
        return 2
    _print(payload if args.json else
           f"train={payload['sizes']['train']} val={payload['sizes']['val']} "
           f"heldout={payload['sizes']['heldout']} leakage=none", args.json)
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    spec = load_spec(args.spec)
    report = preflight.check(spec, _runner(args.runner))
    _print(report.as_dict(), args.json)
    if not report.ok:
        for problem in report.problems:
            print(f"problem: {problem}", file=sys.stderr)
        return 2
    return 0


def cmd_train(args: argparse.Namespace) -> int:
    spec = load_spec(args.spec)
    optimizer = Optimizer(spec, _runner(args.runner), _proposer(args.proposer))
    report = optimizer.run(epochs=args.epochs)
    _print(report.as_dict(), args.json)
    heldout = report.heldout or {}
    if heldout.get("accepted") is False:
        return 3
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    spec = load_spec(args.spec)
    optimizer = Optimizer(spec, ScriptedRunner(), RuleProposer())
    if not optimizer.store.entries():
        raise SpecError("no versions — run `train` first")
    result = optimizer.evaluate_heldout()
    _print(result, args.json)
    return 0 if result.get("accepted") else 3


def cmd_verify(args: argparse.Namespace) -> int:
    spec = load_spec(args.spec)
    ok, detail = VersionStore(spec.work_dir / "versions").verify()
    _print({"ok": ok, "detail": detail}, args.json)
    return 0 if ok else 1


def cmd_predict(args: argparse.Namespace) -> int:
    spec = load_spec(args.spec)
    optimizer = Optimizer(spec, ScriptedRunner(), RuleProposer())
    state = optimizer.state
    history = [(row["features"], row["delta"]) for row in state.get("history", [])]
    predictor = GainPredictor().fit(history)
    result = {
        "rows": len(history),
        "source": predictor.source,
        "mae": None if not history else round(predictor.mae(history), 4),
        "coverage": (
            None if not history else
            round(sum(1 for f, y in history
                      if predictor.predict(dict(f)).covers(y)) / len(history), 4)
        ),
        "interval_sd": round(predictor.residual_sd, 4),
    }
    _print(result, args.json)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    spec = load_spec(args.spec)
    optimizer = Optimizer(spec, ScriptedRunner(), RuleProposer())
    if not optimizer.store.entries():
        raise SpecError("no versions — run `train` first")
    report = optimizer.report()
    _print(report.as_dict(), args.json)
    return 0


def _report_dict(spec) -> dict[str, object] | None:
    path = spec.work_dir / "report.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def cmd_export(args: argparse.Namespace) -> int:
    spec = load_spec(args.spec)
    if args.lane == "native" and args.target != "skillopt":
        print("error: --lane native is only available for --target skillopt",
              file=sys.stderr)
        return 2
    module = hermit if args.target == "hermit" else skillopt
    if module is skillopt:
        written = module.export(spec, args.out, report=_report_dict(spec),
                                lane=args.lane)
    else:
        written = module.export(spec, args.out, report=_report_dict(spec))
    problems = module.validate(args.out)
    if problems:
        print("export invalid: " + "; ".join(problems), file=sys.stderr)
        return 1
    _print({name: str(path) for name, path in written.items()}, args.json)
    return 0


def cmd_deploy(args: argparse.Namespace) -> int:
    try:
        marker = deploy.deploy(args.adapter, args.out, args.host,
                               context_limit=args.context_limit)
    except deploy.DeployError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    problems = deploy.validate(args.out, args.host)
    if problems:
        print("deploy invalid: " + "; ".join(problems), file=sys.stderr)
        return 1
    _print(marker, args.json)
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    module = hermit if args.target == "hermit" else skillopt
    _print(module.ingest(args.path), args.json)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m optimization",
                                     description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="write a spec, artifact and case skeleton")
    init.add_argument("directory")
    init.add_argument("--name", default="example")
    init.add_argument("--method", default="skillopt")
    init.add_argument("--kind", default="skill")
    init.set_defaults(func=cmd_init)

    methods = sub.add_parser("methods", help="list the documented Hermit methods")
    methods.add_argument("--json", action="store_true")
    methods.set_defaults(func=cmd_methods)

    for name, func, help_text in (
        ("split", cmd_split, "show the deterministic train/val/heldout split"),
        ("check", cmd_check, "preflight the spec, data, method and pipeline"),
        ("train", cmd_train, "run the optimization loop"),
        ("eval", cmd_eval, "held-out promotion check on the current tree"),
        ("verify", cmd_verify, "recompute the version chain"),
        ("predict", cmd_predict, "report predictor calibration for this run"),
        ("report", cmd_report, "rebuild the run record and summary"),
    ):
        sub_parser = sub.add_parser(name, help=help_text)
        sub_parser.add_argument("--spec", required=True)
        sub_parser.add_argument("--json", action="store_true")
        if name in {"train", "check"}:
            sub_parser.add_argument(
                "--runner", default="scripted",
                help="scripted | command:<shell command>",
            )
        if name == "train":
            sub_parser.add_argument("--epochs", type=int, default=None)
            sub_parser.add_argument(
                "--proposer", default="rule",
                help="rule | command:<shell command>",
            )
        sub_parser.set_defaults(func=func)

    export = sub.add_parser("export", help="write a Hermit or SkillOpt export")
    export.add_argument("--spec", required=True)
    export.add_argument("--target", choices=("hermit", "skillopt"), required=True)
    export.add_argument("--out", required=True)
    export.add_argument(
        "--lane", choices=("hermit", "native"), default="hermit",
        help="skillopt only: Hermit Evolver tree, or SkillOpt's own bundle",
    )
    export.add_argument("--json", action="store_true")
    export.set_defaults(func=cmd_export)

    deploy_parser = sub.add_parser("deploy", help="generate a host workspace")
    deploy_parser.add_argument("--adapter", required=True)
    deploy_parser.add_argument("--out", required=True)
    deploy_parser.add_argument("--host", choices=("codex", "claude-code", "pi"),
                               required=True)
    deploy_parser.add_argument("--context-limit", type=int, default=32768,
                               help="Codex project_doc_max_bytes (0 skips the check)")
    deploy_parser.add_argument("--json", action="store_true")
    deploy_parser.set_defaults(func=cmd_deploy)

    ingest = sub.add_parser("ingest", help="read an upstream result JSON")
    ingest.add_argument("--target", choices=("hermit", "skillopt"), required=True)
    ingest.add_argument("--path", required=True)
    ingest.add_argument("--json", action="store_true")
    ingest.set_defaults(func=cmd_ingest)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        # Training variables live in a .env beside the spec (or the cwd): the
        # runner and proposer are child processes and inherit what is loaded
        # here, so `train` needs no shell exports.
        starts = [pathlib.Path.cwd()]
        spec = getattr(args, "spec", None)
        if spec:
            starts.insert(0, pathlib.Path(spec).resolve().parent)
        load_dotenv(*starts)
        return int(args.func(args))
    except (SpecError, MethodError, ValueError, RuntimeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

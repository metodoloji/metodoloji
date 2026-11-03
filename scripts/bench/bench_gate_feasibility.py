"""Measure the preflight feasibility guard (E-105).

    python scripts/bench/bench_gate_feasibility.py            # healthy
    python scripts/bench/bench_gate_feasibility.py --broken   # rule-7 control

A spec whose validation or held-out split is smaller than the exact McNemar
minimum for its alpha cannot promote anything. This bench builds 40 specs over
that boundary and records whether `optimization.preflight.check` refuses
exactly the infeasible ones.

The control disables the guard (the pre-fix behaviour), so infeasible specs
pass falsely and the control must fall below the threshold. Deterministic,
offline, stdlib only.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from optimization import preflight  # noqa: E402
from optimization.gate import min_discordant_pairs as exact_minimum  # noqa: E402
from optimization.spec import load_spec  # noqa: E402

VAL_SIZES = (4, 5, 6, 7, 8)
HELDOUT_SIZES = (3, 4, 6, 8)
ALPHAS = (0.05, 0.01)
ARTIFACT = (
    "# Skill under test\n\n"
    "A neutral document used only to exercise the preflight arithmetic. It "
    "makes no claim about any case and is never trained here. "
) * 3


def build_project(root: pathlib.Path, val: int, heldout: int,
                  alpha: float) -> pathlib.Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(ARTIFACT, encoding="utf-8")
    rows = [{"id": f"v{i}", "input": f"val prompt {i}", "requires": [],
             "split": "val"} for i in range(val)]
    rows += [{"id": f"h{i}", "input": f"heldout prompt {i}", "requires": [],
              "split": "heldout"} for i in range(heldout)]
    rows += [{"id": f"t{i}", "input": f"train prompt {i}", "requires": [],
              "split": "train"} for i in range(2)]
    (root / "cases.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    (root / "optimization.toml").write_text(
        "[run]\nname='feasibility'\nseed=7\nepochs=1\nbatch_size=1\n"
        f"val_size={val}\nheldout_size={heldout}\nmin_cases=2\n"
        "[artifact]\nkind='skill'\npath='SKILL.md'\nmax_tokens=2000\n"
        "[cases]\npath='cases.jsonl'\n"
        f"[gate]\nalpha={alpha}\nrequire_heldout=true\n",
        encoding="utf-8")
    return root / "optimization.toml"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--broken", action="store_true",
                        help="disable the feasibility guard (pre-fix behaviour)")
    parser.add_argument("--samples",
                        default="docs/experiments/E-105/raw/samples.jsonl")
    args = parser.parse_args()
    samples = ROOT / args.samples
    samples.parent.mkdir(parents=True, exist_ok=True)

    if args.broken:
        # The pre-fix preflight had no feasibility clause: it behaved as if
        # every split could reach alpha.
        preflight.min_discordant_pairs = lambda alpha: 0

    passed = total = 0
    with samples.open("w", encoding="utf-8", newline="\n") as fh, \
            tempfile.TemporaryDirectory() as tmp:
        tmp_path = pathlib.Path(tmp)
        for val in VAL_SIZES:
            for heldout in HELDOUT_SIZES:
                for alpha in ALPHAS:
                    spec = load_spec(build_project(
                        tmp_path / f"{val}-{heldout}-{alpha}", val, heldout, alpha))
                    needed = exact_minimum(alpha)
                    feasible = val >= needed and heldout >= needed
                    report = preflight.check(spec)
                    refused = not report.ok
                    ok = refused != feasible
                    total += 1
                    passed += int(ok)
                    fh.write(json.dumps({
                        "i": total - 1, "val": val, "heldout": heldout,
                        "alpha": alpha, "min_pairs": needed,
                        "feasible": feasible, "refused": refused, "ok": ok,
                        "problems": report.problems[:2],
                    }, sort_keys=True) + "\n")

    print(f"spec_guard_accuracy={passed / total:.2f} ({passed}/{total})")
    if args.broken:
        return 0
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())

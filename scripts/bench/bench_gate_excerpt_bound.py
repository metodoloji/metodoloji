#!/usr/bin/env python3
"""The chain gate's fault excerpt keeps lines whole and says what it hid (E-194).

Found live on this tree, 2026-11-21, by pointing chain-gate's schema stage at an
attested record with twelve defects. The stage blocked — and printed::

    E-900.md: schema check failed — FAIL: … (contract)
      - missing or placeholder field 'Status'
      … five defects …
      - missing or placeholder field 'Experime

Five and a half of twelve defects, cut mid-word, with no sign that anything was
withheld. The agent fixes what it can see, reruns, sees the next few: the gate
knows the whole reason and shows half of it. The second failure path (the
experiment verifier's label plus its output) carried the same `out[:300]`.

Claim, closed set: both paths excerpt through `_excerpt` — whole lines only, a
counted marker line naming what was dropped, the bound respected, a short fault
list verbatim, no mid-word fragment, and the gate still fails closed.

Control: `--broken` runs the same probes against the pre-change tree
(`PREFIX_REV`), whose excerpt is `out[:300]`.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import pathlib
import subprocess
import sys
import tarfile
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
PREFIX_REV = "0953f6b"  # the parent of the commit that ships the excerpt here

PROBES = (
    "shown_lines_are_verbatim",
    "hidden_count_named",
    "total_lines_accounted",
    "marker_is_its_own_line",
    "excerpt_within_bound",
    "short_fault_list_verbatim",
    "no_mid_word_fragment",
    "check_fails_closed",
    "both_failure_paths_use_the_excerpt",
    "raw_slice_gone",
)


def _module(tree: pathlib.Path):
    spec = importlib.util.spec_from_file_location(
        "chain_gate", tree / "hooks" / "scripts" / "chain-gate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _record(tmp: pathlib.Path, tree: pathlib.Path, *,
            many_defects: bool) -> pathlib.Path:
    """A copy of a committed record, so both trees can read the same source.

    `many_defects` strips every required field but the heading and the schema
    attestation, so the checker prints a long fault list; otherwise only the
    Status value is made invalid, giving exactly one short defect.
    """
    source = (tree / "docs" / "experiments" / "E-190.md").read_text(encoding="utf-8")
    if many_defects:
        keep = ("## Experiment:", "- **Record Schema:", "- **Date:",
                "- **Record Kind:")
        source = "\n".join(line for line in source.splitlines()
                           if line.startswith(keep)) + "\n"
    else:
        source = source.replace("- **Status:** completed", "- **Status:** —", 1)
    record = tmp / "E-900.md"
    record.write_text(source, encoding="utf-8")
    return record


def _schema_failure(gate, tree: pathlib.Path, tmp: pathlib.Path,
                    *, many_defects: bool) -> tuple[int, str, str]:
    """(exit, raw checker output, the gate's printed message)."""
    record = _record(tmp, tree, many_defects=many_defects)
    rc, out = gate._run([sys.executable,
                         str(tree / "scripts" / "create-record.py"),
                         "--check", str(record)])
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exit_code = gate._check(tmp, "experiment", [record])
    printed = buf.getvalue().strip()
    message = printed.split("schema check failed — ", 1)[1] if "— " in printed else printed
    return exit_code, out, message


def _probe(name: str, tree: pathlib.Path, base: pathlib.Path) -> tuple[bool, str]:
    gate = _module(tree)
    tmp = base / "wt"
    tmp.mkdir()

    if name in ("both_failure_paths_use_the_excerpt", "raw_slice_gone"):
        source = (tree / "hooks" / "scripts" / "chain-gate.py").read_text(encoding="utf-8")
        if name == "both_failure_paths_use_the_excerpt":
            ok = source.count("_excerpt(out)") == 2
            return ok, f"sites={source.count('_excerpt(out)')}"
        ok = "{out[:300]}" not in source
        return ok, f"raw_slice={'present' if not ok else 'gone'}"

    if name == "short_fault_list_verbatim":
        exit_code, out, message = _schema_failure(
            gate, tree, tmp, many_defects=False)
        ok = exit_code == 1 and message == out and "more line" not in message
        return ok, f"message={message[-60:]!r}"

    if name == "check_fails_closed":
        exit_code, _, _ = _schema_failure(gate, tree, tmp, many_defects=False)
        return exit_code == 1, f"rc={exit_code}"

    exit_code, out, message = _schema_failure(gate, tree, tmp, many_defects=True)
    lines = message.splitlines()
    raw_lines = out.splitlines()

    if name == "shown_lines_are_verbatim":
        shown = lines[:-1] if lines and lines[-1].startswith("… (+") else lines
        ok = bool(shown) and all(line in raw_lines for line in shown)
        return ok, f"shown={len(shown)}"

    if name == "hidden_count_named":
        marker = lines[-1] if lines else ""
        hidden = int(marker.split("(+", 1)[1].split(" ", 1)[0]) if "(+" in marker else -1
        ok = hidden > 0
        return ok, f"marker={marker!r}"

    if name == "total_lines_accounted":
        marker = lines[-1] if lines else ""
        hidden = int(marker.split("(+", 1)[1].split(" ", 1)[0]) if "(+" in marker else -1
        ok = hidden > 0 and (len(lines) - 1) + hidden == len(raw_lines)
        return ok, f"shown={len(lines) - 1} hidden={hidden} raw={len(raw_lines)}"

    if name == "marker_is_its_own_line":
        last = lines[-1] if lines else ""
        ok = (bool(lines) and last.startswith("… (+")
              and not lines[-2].endswith("more lines)"))
        return ok, f"last={last!r}"

    if name == "excerpt_within_bound":
        body = lines[:-1] if lines and lines[-1].startswith("… (+") else lines
        ok = len("\n".join(body)) <= 300
        return ok, f"body_len={len(chr(10).join(body))}"

    if name == "no_mid_word_fragment":
        # The old cut stopped inside "Experiment"; every line the gate now shows
        # must be a complete checker line (the marker is the only extra line).
        content = [line for line in lines if not line.startswith("… (+")]
        ok = bool(content) and content[-1] in raw_lines
        return ok, f"tail={content[-1] if content else ''!r}"

    raise SystemExit(f"unknown probe: {name}")


def driver(name: str, tree: pathlib.Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ok, detail = _probe(name, tree, pathlib.Path(tmp))
    print(json.dumps({"ok": ok, "detail": detail}, ensure_ascii=False))


def _prefix_tree(base: pathlib.Path) -> pathlib.Path:
    archive = base / "prefix.tar"
    with archive.open("wb") as handle:
        subprocess.run(["git", "archive", PREFIX_REV], cwd=ROOT,
                       stdout=handle, stdin=subprocess.DEVNULL, check=True)
    tree = base / "prefix"
    tree.mkdir()
    with tarfile.open(archive) as tar:
        tar.extractall(tree)
    return tree


def _run(tree: pathlib.Path, samples: pathlib.Path | None, broken: bool) -> bool:
    rows, passed = [], 0
    for name in PROBES:
        result = subprocess.run(
            [sys.executable, str(pathlib.Path(__file__).resolve()), "--driver", name,
             "--tree", str(tree)], capture_output=True, text=True, cwd=str(tree),
            stdin=subprocess.DEVNULL)
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            payload = {"ok": False, "detail": (result.stderr or result.stdout)[-200:]}
        ok = bool(payload.get("ok"))
        passed += 1 if ok else 0
        rows.append({"probe": name, "ok": ok, "detail": payload.get("detail", "")})
        print(f"  {'PASS' if ok else 'FAIL'}  {name}  — {payload.get('detail', '')}")
    total = len(PROBES)
    value = passed / total
    print(f"gate_excerpt_bound_accuracy={value:.2f} ({passed}/{total})")
    if samples is not None:
        samples.parent.mkdir(parents=True, exist_ok=True)
        with samples.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps({"broken": broken, **row}, ensure_ascii=False) + "\n")
    return passed == total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=pathlib.Path, default=None)
    parser.add_argument("--broken", action="store_true")
    parser.add_argument("--driver", default=None)
    parser.add_argument("--tree", type=pathlib.Path, default=None)
    args = parser.parse_args()

    if args.driver:
        driver(args.driver, args.tree or ROOT)
        return 0

    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        tree = _prefix_tree(base) if args.broken else ROOT
        print(f"tree: {tree}")
        ok = _run(tree, args.samples, args.broken)
    # The control is EXPECTED to miss the claim; a non-zero exit would read as a
    # crashed measurement instead of a measured refutation (rule 7).
    return 0 if (ok or args.broken) else 1


if __name__ == "__main__":
    raise SystemExit(main())

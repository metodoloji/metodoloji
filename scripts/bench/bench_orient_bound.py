#!/usr/bin/env python3
"""The orient digest bounds values without cutting a token in half (E-191).

Found live on this tree, 2026-11-21, reading the digest the session edge tells
an agent to run first::

    chain: E: E-190: APPROVED: measured=1.0 == threshold=1.0 (GATE-OK-E-190-c112175a6c7fee89850430f3f0f…

The gate token was sliced mid-hash. E-187 removed exactly this from three
surfaces by adding `utils.brief_text` (whole token or absent, elision marked),
but `hooks/scripts/orient.py` kept its own `_clip` that cut at `limit - 1` and
appended `…`, and `handoff_digest` was still a raw `[:90]` slice. So the surface
a router reads first re-introduced the string E-187 called "not evidence": a
half token cannot be verified, and a path cut mid-name cannot be opened.

Claim, closed set: every bounded value the digest prints is bounded by the ONE
shared cutter — no partial gate token, no path cut mid-word when a boundary
exists, elisions marked, and `--json` keeps the untruncated values.

Control: `--broken` runs the same probes against the pre-change tree
(`PREFIX_REV`), whose `_clip` and handoff slice cut at a fixed offset.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import pathlib
import subprocess
import sys
import tarfile
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
PREFIX_REV = "2f75581"  # the parent of the commit that ships the shared cutter here
TOKEN = "GATE-OK-E-190-c112175a6c7fee89850430f3f0f0fd61"
LONG_PATH = "docs/planning/implementation-readiness-report-2026-09-30.md"

PROBES = (
    "gate_token_whole_or_absent",
    "full_value_when_it_fits",
    "path_dropped_whole_at_a_boundary",
    "handoff_preview_matches_the_cutter",
    "handoff_preview_token_whole_or_absent",
    "shared_cutter_equality",
    "json_keeps_full_chain_values",
    "one_giant_token_marked",
    "elision_never_silent",
    "handoff_preview_bounded",
)


# ---------------------------------------------------------------- fixtures
def _imports(tree: pathlib.Path) -> None:
    sys.path.insert(0, str(tree / "hooks" / "engine"))
    sys.path.insert(0, str(tree / "hooks" / "scripts"))
    sys.path.insert(0, str(tree))


def _engine(tree: pathlib.Path):
    _imports(tree)
    from modules import blackboard as bb
    import orient
    return bb, orient


def _chain_line(orient, digest) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        orient._print_text(digest)
    lines = [ln for ln in buf.getvalue().splitlines()
             if ln.strip().startswith("chain:")]
    return lines[0] if lines else ""


def _project(base: pathlib.Path) -> pathlib.Path:
    root = base / "project"
    root.mkdir(parents=True)
    return root


# ---------------------------------------------------------------- probes
def _probe(name: str, tree: pathlib.Path, base: pathlib.Path) -> tuple[bool, str]:
    bb, orient = _engine(tree)
    root = _project(base)

    if name == "gate_token_whole_or_absent":
        bb.write_key(str(root), "E-190",
                     f"E-190: APPROVED: measured=1.0 == threshold=1.0 ({TOKEN})")
        line = _chain_line(orient, orient.build(root))
        ok = TOKEN not in line and TOKEN[:20] not in line and line.endswith("…")
        return ok, f"line={line.strip()[-60:]!r}"

    if name == "full_value_when_it_fits":
        bb.write_key(str(root), "E-190", "E-190: APPROVED: measured=1.0")
        line = _chain_line(orient, orient.build(root))
        ok = "measured=1.0" in line and "…" not in line
        return ok, f"line={line.strip()[-50:]!r}"

    if name == "path_dropped_whole_at_a_boundary":
        bb.write_key(str(root), "IR-2026-09-30",
                     f"IR-2026-09-30: readiness: NOT READY — report: {LONG_PATH}")
        line = _chain_line(orient, orient.build(root))
        ok = (LONG_PATH not in line and "implementation-readi" not in line
              and "readiness: NOT READY" in line and line.endswith("…"))
        return ok, f"line={line.strip()[-60:]!r}"

    if name == "handoff_preview_matches_the_cutter":
        raw = "PRD final — " + "path/segment/" * 8 + f"report.md ({TOKEN})"
        bb.post_handoff(str(root), "bmad-ux", "prd.big", raw)
        preview = orient.handoff_digest(root)["waiting"]["bmad-ux"]["preview"]
        full = bb.pending_handoffs(str(root), "bmad-ux")[0]["text"]
        ok = preview == orient._clip(full, orient._PREVIEW_CHARS) != full
        return ok, f"preview={preview[-40:]!r}"

    if name == "handoff_preview_token_whole_or_absent":
        raw = "batons: " + "x" * 70 + f" ({TOKEN})"
        bb.post_handoff(str(root), "bmad-ux", "prd.big", raw)
        preview = orient.handoff_digest(root)["waiting"]["bmad-ux"]["preview"]
        ok = TOKEN[:20] not in preview and preview.endswith("…")
        return ok, f"preview={preview[-40:]!r}"

    if name == "shared_cutter_equality":
        from modules.utils import brief_text
        samples = ["short", "two words " * 20, f"value ({TOKEN})",
                   "word " * 18 + TOKEN, "y" * 300]
        bad = [s[:20] for s in samples
               if orient._clip(s, 90) != brief_text(s, 90)]
        return not bad, f"mismatched={bad or 'none'}"

    if name == "json_keeps_full_chain_values":
        bb.write_key(str(root), "E-190",
                     f"E-190: APPROVED: measured=1.0 == threshold=1.0 ({TOKEN})")
        digest = orient.build(root)
        value = digest["board"]["chain_values"]["E"]
        ok = TOKEN in value
        return ok, f"len={len(value)}"

    if name == "one_giant_token_marked":
        # No boundary exists inside one giant token, so the marked hard cut is
        # the only option; the marker costs one char beyond the bound.
        out = orient._clip("z" * 300, 90)
        ok = out.endswith("…") and len(out) <= 91
        return ok, f"len={len(out)}"

    if name == "elision_never_silent":
        bb.write_key(str(root), "E-190",
                     f"E-190: APPROVED: measured=1.0 == threshold=1.0 ({TOKEN})")
        line = _chain_line(orient, orient.build(root))
        value_part = line.split("chain: E: ", 1)[-1]
        ok = value_part.endswith("…") and len(value_part) <= orient._PREVIEW_CHARS
        return ok, f"len={len(value_part)}"

    if name == "handoff_preview_bounded":
        bb.post_handoff(str(root), "bmad-ux", "prd.big", "w " * 200)
        preview = orient.handoff_digest(root)["waiting"]["bmad-ux"]["preview"]
        ok = preview.endswith("…") and len(preview) <= orient._PREVIEW_CHARS
        return ok, f"len={len(preview)}"

    raise SystemExit(f"unknown probe: {name}")


# ---------------------------------------------------------------- driver mode
def driver(name: str, tree: pathlib.Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ok, detail = _probe(name, tree, pathlib.Path(tmp))
    print(json.dumps({"ok": ok, "detail": detail}, ensure_ascii=False))


# ---------------------------------------------------------------- bench mode
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
    print(f"orient_bound_accuracy={value:.2f} ({passed}/{total})")
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

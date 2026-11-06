"""Ledger reporting bench — E-114.

docs/experiments/E-114.md (H-114: techdebt_reporting_accuracy >= 0.95).

TD-017: §1 of ``scripts/check-techdebt.sh`` demanded that the shipped template
and the live ledger be byte-identical, so the template could not keep its
placeholders and the live ledger could not drop them. The audit stayed HEALTHY
while the metrics block carried ``[X items]`` and the trend table stopped at
SP-003.

The bench measures the CANDIDATE contract, because the guard rightly refuses a
product write before the record is approved: it takes the shipped checker and
applies the two blocks the fix will add — §1 compares the ledger's SHAPE
(heading sequence + table columns) instead of its bytes, §7 refuses a template
placeholder in the live ledger and refuses a shipped template that has stopped
being one — then runs the candidate against isolated fixture trees. Once the
shipped script carries the §1 marker the transform is a no-op, so the same bench
re-measures the real tree after the write.

``--broken`` reproduces the pre-fix decision contract (byte identity, no
placeholder check) against the same fixtures. Rule 7: the control must fail.

Deterministic, offline, stdlib + POSIX shell only.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHECK = ROOT / "scripts" / "check-techdebt.sh"
TEMPLATE = ROOT / "templates" / "tech-debt.md"
LIVE = ROOT / "docs" / "development" / "tech-debt.md"

PLACEHOLDER_RE = (
    r"\[(X|Y|Z|W|V) [^]]*\]"          # [X items], [Y weeks], [X - Y = Z]
    r"|\[(TBD|PLACEHOLDER)[^]]*\]"    # [TBD], [PLACEHOLDER ...]
    r"|\[YYYY-MM-DD\]"                # date slots
    r"|\[20% or [^]]*\]"              # allocation slots
)

OLD_SECTION_1 = """echo "== 1) Template identity (templates/ \u2194 docs/development/) =="
if [ ! -f "$TEMPLATE" ]; then
    echo "[ERROR] $TEMPLATE not found"
    PROBLEMS=$((PROBLEMS + 1))
elif [ ! -f "$LIVE" ]; then
    echo "[ERROR] $LIVE not found"
    PROBLEMS=$((PROBLEMS + 1))
elif diff -q "$TEMPLATE" "$LIVE" >/dev/null 2>&1; then
    echo "[OK]   templates/tech-debt.md \u2194 docs/development/tech-debt.md identical"
else
    echo "[ERROR] templates/tech-debt.md \u2194 docs/development/tech-debt.md DRIFT (files differ)"
    PROBLEMS=$((PROBLEMS + 1))
fi
"""

NEW_SECTION_1 = """echo "== 1) Ledger structure matches the shipped template =="
if [ ! -f "$TEMPLATE" ]; then
    echo "[ERROR] $TEMPLATE not found"
    PROBLEMS=$((PROBLEMS + 1))
elif [ ! -f "$LIVE" ]; then
    echo "[ERROR] $LIVE not found"
    PROBLEMS=$((PROBLEMS + 1))
else
    # TD-017 / E-114: the template owns the SHAPE, the live ledger owns the
    # CONTENT. Comparing bytes forced the shipped template to carry this repo's
    # debt history (or the live ledger to keep the template's placeholders), so
    # the audit could stay HEALTHY while the metrics block read `[X items]`.
    # Structure = the heading sequence plus every table's header row (which is
    # what makes a column set drift visible); the prose and the rows are free.
    structure_of() {
        awk '
            BEGIN { pending = "" }
            {
                if (pending != "") {
                    if ($0 ~ /^\\|[[:space:]:|-]+\\|[[:space:]]*$/) { print "T:" pending }
                    pending = ""
                }
                if ($0 ~ /^#/) { print "H:" $0; next }
                if ($0 ~ /^\\|/) { pending = $0 }
            }
        ' "$1"
    }
    TEMPLATE_STRUCT=$(mktemp)
    LIVE_STRUCT=$(mktemp)
    structure_of "$TEMPLATE" > "$TEMPLATE_STRUCT"
    structure_of "$LIVE" > "$LIVE_STRUCT"
    if diff -q "$TEMPLATE_STRUCT" "$LIVE_STRUCT" >/dev/null 2>&1; then
        echo "[OK]   docs/development/tech-debt.md follows templates/tech-debt.md (headings + table columns)"
    else
        echo "[ERROR] ledger structure drifted from templates/tech-debt.md:"
        diff "$TEMPLATE_STRUCT" "$LIVE_STRUCT" | sed -n '1,20p' | sed 's/^/         /'
        PROBLEMS=$((PROBLEMS + 1))
    fi
    rm -f "$TEMPLATE_STRUCT" "$LIVE_STRUCT"
fi
"""

NEW_SECTION_7 = """echo "== 7) Live ledger carries no template placeholders =="
# E-114: the template is a template, so it keeps `[X items]`-style slots; the
# live ledger has to replace every one of them with a real number. Before this
# check the reporting half was never read by anything.
PLACEHOLDER_RE='\\[(X|Y|Z|W|V) [^]]*\\]|\\[(TBD|PLACEHOLDER)[^]]*\\]|\\[YYYY-MM-DD\\]|\\[20% or [^]]*\\]'
LEFTOVERS=$(grep -nE "$PLACEHOLDER_RE" "$LIVE" 2>/dev/null || true)
if [ -z "$LEFTOVERS" ]; then
    echo "[OK]   no template placeholders in the live ledger"
else
    echo "[ERROR] live ledger still carries template placeholders:"
    printf '%s\\n' "$LEFTOVERS" | sed -n '1,10p' | sed 's/^/         /'
    PROBLEMS=$((PROBLEMS + 1))
fi
if grep -qE "$PLACEHOLDER_RE" "$TEMPLATE" 2>/dev/null; then
    echo "[OK]   templates/tech-debt.md keeps its placeholders"
else
    echo "[ERROR] templates/tech-debt.md carries no placeholder — it is a filled ledger, not a template"
    PROBLEMS=$((PROBLEMS + 1))
fi
"""

NEGTEST_STAGE = """# Stage 5/5: a template placeholder injected into the live ledger must fail §7.
print(f"[5/{total_stages}] does §7 catch a template placeholder injected into the live ledger")
td.write_text(orig + "\\n- **Total active debt:** [X items]\\n", encoding="utf-8")
try:
    r = run_check()
    if "placeholder" in r.stdout.lower() and r.returncode == 1:
        print("  [OK] §7 placeholder caught, exit=1")
    else:
        print(f"  [ERROR] §7 placeholder expected, output end: ...{r.stdout[-400:]!r}")
        sys.exit(1)
finally:
    td.write_text(orig, encoding="utf-8")

"""

TAIL_MARKER = '\necho\nif [ "$PROBLEMS" -eq 0 ]; then'


def _candidate(text: str) -> str:
    """The fix as the bench measures it, applied to the shipped script.

    Idempotent: once the shipped script carries the §1 marker the transform is
    a no-op, so a post-write re-run measures the real tree.
    """
    if "== 1) Ledger structure matches the shipped template ==" in text:
        return text
    if OLD_SECTION_1 not in text:
        raise SystemExit("cannot locate §1 in scripts/check-techdebt.sh")
    text = text.replace(OLD_SECTION_1, NEW_SECTION_1, 1)
    if TAIL_MARKER not in text:
        raise SystemExit("cannot locate the STATUS tail in scripts/check-techdebt.sh")
    text = text.replace(TAIL_MARKER, "\n" + NEW_SECTION_7 + TAIL_MARKER, 1)
    text = text.replace("total_stages = 4", "total_stages = 5", 1)
    text = text.replace('print(f"[OK] all {total_stages} negtest stages successful")',
                        NEGTEST_STAGE +
                        'print(f"[OK] all {total_stages} negtest stages successful")', 1)
    return text


# Minimal fixture pair. The shipped tree is measured by the advisory checks;
# these two files only need the shape §1 compares and the rows §2-§4 count.
TABLE = (
    "| ID | Description | Why Added | Date Added | Impact | Owner | Target Sprint |\n"
    "|----|-------|---------------|---------------|------|--------|--------------|\n"
)

BASE_TEMPLATE = f"""# Technical Debt Tracking

**Last updated:** [YYYY-MM-DD]

---

## Active Technical Debts

### Critical Priority (P0)

{TABLE}| TD-XXX | [Description] | [Why added] | [YYYY-MM-DD] | [Impact] | [@owner] | [Target sprint] |

## Paid Debts (Completed)

| ID | Description | Solution | Completion Date | Sprint | PR/QR |
|----|-------|-------|-------------------|--------|-------|
| TD-XXX | [Description] | [Solution] | [YYYY-MM-DD] | [Sprint] | [PR/QR] |

## Technical Debt Metrics

### Current State
- **Total active debt:** [X items]

## Notes

- Technical debt cannot be hidden.
"""

BASE_LIVE = f"""# Technical Debt Tracking

**Last updated:** 2026-11-06

---

## Active Technical Debts

### Critical Priority (P0)

{TABLE}| TD-001 | A real debt | Found in review | 2026-11-06 | Low | @owner | SP-016 |

## Paid Debts (Completed)

| ID | Description | Solution | Completion Date | Sprint | PR/QR |
|----|-------|-------|-------------------|--------|-------|
| TD-016 | An old debt | Fixed | 2026-11-05 | SP-015 | QR-030 |

## Technical Debt Metrics

### Current State
- **Total active debt:** 1 item

## Notes

- Technical debt cannot be hidden.
"""


def _shell() -> str:
    candidates: list[str] = []
    if os.name == "nt":
        git = shutil.which("git")
        if git:
            candidates.append(str(pathlib.Path(git).parent.parent
                                  / "usr" / "bin" / "sh.exe"))
    for name in ("sh", "bash"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    candidates.append(r"C:\Program Files\Git\usr\bin\sh.exe")
    for candidate in candidates:
        if not pathlib.Path(candidate).is_file():
            continue
        try:
            probe = subprocess.run([candidate, "-c", "echo bench-shell-ok"],
                                   capture_output=True, text=True, timeout=15,
                                   stdin=subprocess.DEVNULL)
        except OSError:
            continue
        if "bench-shell-ok" in (probe.stdout or ""):
            return candidate
    raise SystemExit("no working POSIX shell found (sh/bash)")


SHELL = _shell()


def _env() -> dict[str, str]:
    """A shell without its own bin dir on PATH cannot find dirname/awk/mktemp.

    Git's sh.exe is exactly that when invoked from Python: PowerShell's bash
    sets the directory up, a bare spawn does not.
    """
    env = dict(os.environ)
    bindir = str(pathlib.Path(SHELL).parent)
    env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
    return env


def _fixture(tmp: pathlib.Path, template_text: str, live_text: str) -> pathlib.Path:
    root = pathlib.Path(tempfile.mkdtemp(dir=tmp)) / "plugin"
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "templates").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "development").mkdir(parents=True, exist_ok=True)
    (root / "hooks").mkdir(parents=True, exist_ok=True)
    (root / "scripts" / "check-techdebt.sh").write_text(
        _candidate(CHECK.read_text(encoding="utf-8")), encoding="utf-8")
    (root / "templates" / "tech-debt.md").write_text(template_text, encoding="utf-8")
    (root / "docs" / "development" / "tech-debt.md").write_text(
        live_text, encoding="utf-8")
    return root


def _run(root: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [SHELL, str(root / "scripts" / "check-techdebt.sh"), *args],
        cwd=str(root), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=60, stdin=subprocess.DEVNULL, env=_env())


class _Verdict:
    """The verdict the checker under test would print, plus its exit code."""

    def __init__(self, code: int, out: str) -> None:
        self.returncode = code
        self.stdout = out


def _legacy(template_text: str, live_text: str) -> _Verdict:
    """The pre-fix contract: byte identity, and nothing about placeholders."""
    if template_text != live_text:
        return _Verdict(1, "[ERROR] templates/tech-debt.md <-> "
                           "docs/development/tech-debt.md DRIFT (files differ)\n")
    return _Verdict(0, "[OK]   templates/tech-debt.md <-> "
                       "docs/development/tech-debt.md identical\n"
                       "STATUS: HEALTHY (tech-debt inventory intact)\n")


def _verdict(mode: str, root: pathlib.Path, template_text: str,
             live_text: str) -> _Verdict:
    if mode == "broken":
        return _legacy(template_text, live_text)
    proc = _run(root)
    return _Verdict(proc.returncode, (proc.stdout or "") + (proc.stderr or ""))


def _sample(mode: str, name: str, template_text: str, live_text: str,
            tmp: pathlib.Path) -> tuple[str, _Verdict, str]:
    root = _fixture(tmp, template_text, live_text)
    result = _verdict(mode, root, template_text, live_text)
    return name, result, ((result.stdout or "") + "").lower()


def _placeholder_hit(line: str) -> bool:
    return re.search(PLACEHOLDER_RE, line) is not None


def _checks(mode: str, tmp: pathlib.Path) -> list[dict]:
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "ok": ok, "detail": detail})

    # 1. Shape agrees while the content differs: the template is a template,
    #    the live ledger is a ledger, and neither is the other's twin.
    name, r, _ = _sample(mode, "shape_only_passes", BASE_TEMPLATE, BASE_LIVE, tmp)
    add(name, r.returncode == 0 and "STATUS: HEALTHY" in r.stdout,
        f"exit={r.returncode}")

    # 2. A missing section is named as a structure drift, not as a byte diff.
    live = BASE_LIVE.replace("## Notes\n", "")
    name, r, low = _sample(mode, "missing_section_fails", BASE_TEMPLATE, live, tmp)
    add(name, r.returncode == 1 and "structure" in low,
        f"exit={r.returncode}, says structure={'structure' in low}")

    # 3. A changed table column is drift even when the file keeps its size.
    live = BASE_LIVE.replace("| Owner |", "| Approver |")
    name, r, low = _sample(mode, "changed_table_column_fails", BASE_TEMPLATE,
                           live, tmp)
    add(name, r.returncode == 1 and "structure" in low,
        f"exit={r.returncode}, says structure={'structure' in low}")

    # 4. A placeholder left in the live ledger is a failure named as such.
    live = BASE_LIVE.replace("- **Total active debt:** 1 item",
                             "- **Total active debt:** [X items]")
    name, r, low = _sample(mode, "live_placeholder_fails", BASE_TEMPLATE, live, tmp)
    add(name, r.returncode == 1 and "placeholder" in low,
        f"exit={r.returncode}, names placeholder={'placeholder' in low}")

    # 5. The same placeholder is legitimate in the template.
    name, r, _ = _sample(mode, "template_placeholder_allowed", BASE_TEMPLATE,
                         BASE_LIVE, tmp)
    add(name, r.returncode == 0, f"exit={r.returncode}")

    # 6. A template with no placeholder has stopped being a template.
    name, r, low = _sample(mode, "template_without_placeholder_fails",
                           BASE_LIVE, BASE_LIVE, tmp)
    add(name, r.returncode == 1 and "placeholder" in low,
        f"exit={r.returncode}, names placeholder={'placeholder' in low}")

    # 7. The live ledger may carry this repo's history and prose; the template
    #    stays generic. That difference is the contract, not drift.
    live = BASE_LIVE.replace(
        "## Notes\n",
        "## Notes\n\n- A long history the template does not need.\n"
        "- Another line of it.\n")
    name, r, _ = _sample(mode, "live_history_is_not_drift", BASE_TEMPLATE, live, tmp)
    add(name, r.returncode == 0, f"exit={r.returncode}")

    return checks


def _advisory() -> None:
    """Shipped-tree observations, printed but not scored (pre-fix they miss)."""
    shipped_template = TEMPLATE.read_text(encoding="utf-8")
    shipped_live = LIVE.read_text(encoding="utf-8")
    print("[advisory] shipped_template_is_a_template: "
          + ("yes" if any(_placeholder_hit(line)
                          for line in shipped_template.splitlines())
             else "no"))
    print("[advisory] shipped_ledger_is_clean: "
          + ("yes" if not any(_placeholder_hit(line)
                              for line in shipped_live.splitlines())
             else "no"))
    real = subprocess.run([SHELL, str(CHECK)], cwd=str(ROOT),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=120,
                          stdin=subprocess.DEVNULL, env=_env())
    print(f"[advisory] shipped_tree_healthy: exit={real.returncode}")
    neg = subprocess.run([SHELL, str(CHECK), "--negtest"], cwd=str(ROOT),
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=180,
                         stdin=subprocess.DEVNULL, env=_env())
    print(f"[advisory] negtest_has_placeholder_stage: exit={neg.returncode}, "
          f"mentioned={'placeholder' in (neg.stdout + neg.stderr).lower()}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--broken", action="store_true",
                    help="report the pre-fix contract (the rule-7 control)")
    ap.add_argument("--samples", default=None,
                    help="JSONL path for per-check evidence")
    args = ap.parse_args()

    mode = "broken" if args.broken else "fixed"
    with tempfile.TemporaryDirectory() as td:
        results = _checks(mode, pathlib.Path(td))

    x = sum(1 for r in results if r["ok"])
    n = len(results)
    if args.samples:
        path = pathlib.Path(args.samples)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for r in results:
                fh.write(json.dumps(r) + "\n")
    for r in results:
        print(f"[{'OK' if r['ok'] else 'MISS'}] {r['check']}: {r['detail']}")
    print(f"techdebt_reporting_accuracy={x / n:.2f} ({x}/{n})")
    if mode == "fixed":
        _advisory()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

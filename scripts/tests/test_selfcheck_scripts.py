"""Regression guards for the plugin's own self-check scripts (E-001).

Two false positives made the self-audit cry wolf on a healthy tree:

1. ``check-plugin.sh`` §6b guarded a *sibling* check with ``[ -x ... ]``. The
   self-check scripts ship in git as mode 100644 (non-executable) and are
   invoked through ``sh``, so the test was always false: §6b skipped itself and
   forced the whole audit to exit 1 on a clean checkout.
2. ``bench_consistency.py`` listed two docs that no longer exist
   (``docs/BLACKBOARD.md`` was pruned — the code is canonical; ``docs/USAGE-GUIDE.md``
   is not in the tree), so five checks per missing file failed as
   ``FileNotFoundError`` noise and buried the real signal.

This repo has no CI workflow, so ``pytest`` is the only automatic gate; these
tests pin the invariants so the instruments cannot silently go stale again.
"""

import importlib.util
import re
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
BENCH = PLUGIN / "scripts" / "bench" / "bench_consistency.py"
CHECK_PLUGIN = PLUGIN / "scripts" / "check-plugin.sh"


def _load_bench():
    spec = importlib.util.spec_from_file_location("bench_consistency", BENCH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bench_doc_targets_exist():
    """Every doc the bench checks must exist (a stale entry = five false failures)."""
    bench = _load_bench()
    targets = list(bench.DOC_FILES) + list(bench.STOP_DOCS) + list(bench.CANVAS_DOCS)
    missing = [rel for rel in targets if not (PLUGIN / rel).is_file()]
    assert not missing, (
        f"bench_consistency.py targets docs that do not exist: {missing}. "
        "Point them at live docs."
    )


def test_selfcheck_siblings_discovered_by_presence_not_exec_bit():
    """check-plugin.sh must not gate a sibling self-check on the executable bit."""
    text = CHECK_PLUGIN.read_text(encoding="utf-8", errors="replace")
    offenders = re.findall(r'\[\s+-x\s+"\$SELF/[^"]+"\s+\]', text)
    assert not offenders, (
        "check-plugin.sh tests the executable bit on a sibling self-check "
        f"({offenders}); those scripts ship non-executable and run via `sh`, so "
        "the test is always false — use `[ -f ... ]`."
    )


def test_bench_engine_module_scan_covers_every_module():
    """Every engine module must be listed in the bench's duplicate-def scan.

    A module left out of ENGINE_MODULES silently escapes the guard; the scan is
    only as wide as the tuple (E-002). Adding a module to hooks/engine/modules/
    without adding it here is the drift this pins.
    """
    bench = _load_bench()
    listed = set(bench.ENGINE_MODULES)
    missing = [rel for rel in listed if not (PLUGIN / rel).is_file()]
    assert not missing, f"bench_consistency.py lists non-existent modules: {missing}"
    on_disk = {
        p.relative_to(PLUGIN).as_posix()
        for p in (PLUGIN / "hooks" / "engine" / "modules").glob("*.py")
        if p.name != "__init__.py"
    }
    unlisted = sorted(on_disk - listed)
    assert not unlisted, (
        f"engine modules missing from bench ENGINE_MODULES: {unlisted}. "
        "Add them so the duplicate-def guard covers them."
    )

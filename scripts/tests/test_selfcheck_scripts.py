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

This repo's CI only bumps the version (E-010 — it never runs audits), so
``pytest`` remains the only automatic quality gate; these tests pin the
invariants so the instruments cannot silently go stale again.
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


def test_bridge_producer_verify_audit_covers_every_producer():
    """check-plugin.sh §2c must audit the VERIFY step on every producer surface.

    E-018: GUIDE.md promises "Producer BRIDGEs carry a VERIFY step ... (audit
    #2c)" and names 17 producers, three of which keep their BRIDGE in
    ``[agent].principles``. #2c used to read only the workflow key for 13 of
    them — a probe that stripped the VERIFY marker from ``bmad-agent-dev``
    still left #2c green. This calls the bench's own check, which pins #2c's
    producer set and key map to GUIDE.md and re-evaluates the marker on each
    producer TOML.
    """
    bench = _load_bench()
    problem = bench._check_bridge_producer_verify_audit()
    assert problem is None, problem


def test_bridge_producer_audit_reads_agent_principles_key():
    """The agent-principles producers must be read from ``agent.principles``.

    Stripping the marker from ``bmad-agent-dev`` must be visible to #2c; if the
    audit read ``workflow.activation_steps_append`` for it, the invariant would
    silently pass on a surface with no workflow steps (E-018).
    """
    text = CHECK_PLUGIN.read_text(encoding="utf-8", errors="replace")
    blk = re.search(r"PRODUCER_KEY = \{(.*?)\}", text, re.DOTALL)
    assert blk, "check-plugin.sh §2c has no PRODUCER_KEY map"
    audited = dict(re.findall(r'"([^"]+)"\s*:\s*"([^"]+)"', blk.group(1)))
    for name in ("bmad-agent-dev", "gds-agent-game-dev", "gds-agent-game-solo-dev"):
        assert audited.get(name) == "agent.principles", (
            f"{name} must be audited via agent.principles, got {audited.get(name)!r}"
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

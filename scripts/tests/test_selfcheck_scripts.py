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

import csv
import importlib.util
import re
import tomllib
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
BENCH = PLUGIN / "scripts" / "bench" / "bench_consistency.py"
CHECK_PLUGIN = PLUGIN / "scripts" / "check-plugin.sh"


def test_session_context_warns_against_stale_cached_plugin_roots():
    """E-037: the session-start context must name the stale-root trap.

    Real session (2026-10-12): a resumed session ran the plugin path it
    had cached in memory (`…/metodoloji/1.2.5/…/orient.py`) after the
    plugin had been updated — file gone, first command of the session
    dead. The injected context now says the root above is THIS install's
    and a cached one goes stale on every update.
    """
    src = (PLUGIN / "hooks" / "scripts" / "bootstrap.sh").read_text(
        encoding="utf-8")
    assert "goes stale on every plugin update" in src, (
        "session-start context lost the stale-root warning")


def test_review_layers_must_cite_verified_file_lines():
    """E-037: every review surface demands verified file:line evidence.

    Real session (2026-10-12): one review layer reported citations into
    the 1500s of a 230-line file; the triage had to re-derive every
    citation by hand. The rule now lives on all three surfaces the review
    flows read: the methodology chapter and both code-review skills.
    """
    for rel in ("docs/bmad/development-methodology.md",
                "skills/bmad-code-review/SKILL.md",
                "skills/gds-code-review/SKILL.md"):
        src = (PLUGIN / rel).read_text(encoding="utf-8")
        assert "VERIFIED by reading that file" in src, (
            f"{rel} lost the verified-citation rule (E-037)")


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


def _audit_inventory_fixture(tmp_path: Path) -> Path:
    """A copy of the audit inventory's two sides, so the rule can be driven."""
    for rel in ("README.md", "GUIDE.md", "KILAVUZ.md",
                "scripts/check-plugin.sh", "scripts/check-custom.sh",
                "scripts/check-handoff.py", "scripts/check-methodology.sh",
                "scripts/check-techdebt.sh", "scripts/check-triggers.py"):
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((PLUGIN / rel).read_bytes())
    return tmp_path


def test_documented_audit_inventory_equals_the_shipped_family():
    """E-049: the docs' audit inventory is derived, not hand-maintained."""
    bench = _load_bench()
    assert bench._audit_inventory_problems(PLUGIN) == []
    assert bench._check_audit_inventory_matches_the_shipped_one() is None
    # the suite size the verify block states is derived by collecting the suite
    assert bench._check_documented_test_count_is_honest() is None


def test_verify_block_suite_size_claims_are_honest():
    """A floor is stable; an exact number must be true today (E-049)."""
    bench = _load_bench()
    assert bench._test_count_claim_problem("README.md", "# 1,400+ tests", 1443) is None
    assert bench._test_count_claim_problem("KILAVUZ.md", "# 1,400+ test", 1443) is None
    for claim, why in (("# 1,331 tests", "an exact number that is not the suite"),
                       ("# 2,000+ tests", "a floor above the suite"),
                       ("no claim here", "an unstated suite size")):
        assert bench._test_count_claim_problem("GUIDE.md", claim, 1443), why
    # the family is DERIVED — a script added to scripts/ joins it the day it lands
    globbed = [p for p in (PLUGIN / "scripts").glob("check-*")
               if p.suffix in (".sh", ".py")]
    assert bench._audit_family(PLUGIN) == sorted(p.name for p in globbed)
    assert len(globbed) >= 6


def test_audit_inventory_drift_is_named_per_surface(tmp_path):
    """Each documented claim, drifted in turn, must be rejected BY NAME."""
    readme = (PLUGIN / "README.md").read_text(encoding="utf-8")
    guide = (PLUGIN / "GUIDE.md").read_text(encoding="utf-8")
    kilavuz = (PLUGIN / "KILAVUZ.md").read_text(encoding="utf-8")
    triggers_line = "python scripts/check-triggers.py   # trigger/description collision audit\n"
    cases = (
        ("README.md", readme.replace(triggers_line, ""),
         "a verify block that omits a shipped audit"),
        ("README.md", readme.replace("sh scripts/check-custom.sh         # bridge TOML static audit",
                                     "sh scripts/check-phantom.sh        # bridge TOML static audit"),
         "a block naming an audit that does not ship"),
        ("README.md", readme.replace("(#0–#6g)", "(#0–#6f)"),
         "a range ending one section short"),
        ("GUIDE.md", guide.replace(
            "#6e help catalog integrity · #6f trigger/description collision · "
            "#6g no namespaced-tool priming tokens.",
            "#6e help catalog integrity · #6f trigger/description collision."),
         "a '# map:' missing a shipped section"),
        ("GUIDE.md", guide.replace(
            "python scripts/check-triggers.py  # trigger/description collision "
            "(also runs as #6f)\n", ""),
         "a §16 block that omits a shipped audit"),
        ("KILAVUZ.md", kilavuz.replace("7 kontrolü yerelde", "6 kontrolü yerelde"),
         "a stale count claim"),
    )
    for index, (rel, mutated, label) in enumerate(cases):
        assert mutated != (PLUGIN / rel).read_text(encoding="utf-8"), \
            f"setup broken: could not inject {label} in {rel}"
        root = _audit_inventory_fixture(tmp_path / f"case{index}")
        (root / rel).write_text(mutated, encoding="utf-8")
        problems = _load_bench()._audit_inventory_problems(root)
        assert problems, f"the rule accepts {label} in {rel}"
        assert any(rel in p for p in problems), (label, problems)


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


def test_bridge_verify_pattern_parity_with_the_custom_audit():
    """check-plugin.sh §2c must demand the two-part verify pattern §4 demands (E-030).

    ``check-custom.sh`` §4 required BOTH a verify marker and the "error and
    recreate" phrase on its gate-enforcing set, while §2c required only the
    marker on the 17 producers — so a producer could drop the recreate
    instruction and §2c stayed green. The runbooks' check-map also still said
    "#2c bridge VERIFY (13 skills)" although E-018 had widened the audit to 17.
    This calls the bench's check, which pins §2c's two-part gate (with §4's
    marker vocabulary), keeps §4 from weakening, keeps §4's set inside §2c's,
    re-evaluates both halves on each producer, and pins the check-map count.
    """
    bench = _load_bench()
    problem = bench._check_bridge_verify_pattern_parity()
    assert problem is None, problem


def test_bridge_section_refs_resolve_on_every_citing_surface():
    """Every live surface citing the bridge must point at a real section (E-019).

    §7 used to audit only ``custom/*.toml``; the two review ``SKILL.md`` files
    cited a non-existent ``§1.1`` and went unnoticed. This calls the bench's
    check, which evaluates the invariant across custom overrides,
    ``skills/*/SKILL.md`` and ``commands/*.md``.
    """
    bench = _load_bench()
    problem = bench._check_bridge_section_refs_resolve()
    assert problem is None, problem


def test_bridge_drift_audit_scans_skill_md_surfaces():
    """§7 must keep reading ``skills/*/SKILL.md`` (the E-019 widening)."""
    text = (PLUGIN / "scripts" / "check-custom.sh").read_text(
        encoding="utf-8", errors="replace")
    assert 'os.path.join(PLUGIN, "skills", "*", "SKILL.md")' in text, (
        "check-custom.sh §7 narrowed back to custom/ — SKILL.md refs go unaudited"
    )


def test_qr_feeder_audit_covers_every_feeder():
    """check-plugin.sh §2 QR_FEEDERS_TOML must equal the tree's feeder surfaces (E-020).

    The 33 BRIDGE surfaces partition into 17 producers (VERIFY step) and 16
    feeders ("does not produce an independent methodology record"). §2 named
    only 9 of the 16 TOML feeders; the seven gds feeders went unread. This
    calls the bench's check, which derives the feeder set and pins §2 to it.
    """
    bench = _load_bench()
    problem = bench._check_bridge_feeder_audit_covers_every_feeder()
    assert problem is None, problem


def test_qr_feeder_audit_lists_gds_feeders():
    """The gds feeder TOMLs must be in §2's QR_FEEDERS_TOML (the E-020 widening)."""
    text = (PLUGIN / "scripts" / "check-plugin.sh").read_text(
        encoding="utf-8", errors="replace")
    blk = re.search(r"QR_FEEDERS_TOML = \[(.*?)\]", text, re.DOTALL)
    assert blk, "check-plugin.sh §2 has no QR_FEEDERS_TOML list"
    listed = set(re.findall(r'"([^"]+)"', blk.group(1)))
    for name in ("gds-test-automate", "gds-test-design", "gds-test-framework",
                 "gds-test-review", "gds-e2e-scaffold", "gds-performance-test",
                 "gds-playtest-plan"):
        assert name in listed, f"§2 QR_FEEDERS_TOML is missing the gds feeder {name}"


def test_producer_record_targets_audited():
    """check-plugin.sh §2 BRIDGE_SKILLS must hold every producer (E-021).

    §2 demands the bridge reference + record path on each entry but listed only
    7 of the 17 producers. This calls the bench's check, which pins the dict's
    keys to GUIDE.md's producer set and re-checks the target per TOML.
    """
    bench = _load_bench()
    problem = bench._check_bridge_record_target_audit_covers_every_producer()
    assert problem is None, problem


def test_producer_record_targets_list_gds_and_wds_producers():
    """§2's BRIDGE_SKILLS must contain the previously-unaudited producers (E-021)."""
    text = (PLUGIN / "scripts" / "check-plugin.sh").read_text(
        encoding="utf-8", errors="replace")
    blk = re.search(r"BRIDGE_SKILLS = \{(.*?)\}", text, re.DOTALL)
    assert blk, "check-plugin.sh §2 has no BRIDGE_SKILLS dict"
    listed = set(re.findall(r'"([^"\s]+)"\s*:', blk.group(1)))
    for name in ("gds-check-implementation-readiness", "gds-sprint-planning",
                 "gds-create-story", "gds-code-review", "gds-dev-story",
                 "gds-quick-dev", "gds-agent-game-dev", "gds-agent-game-solo-dev",
                 "wds-5-agentic-development", "bmad-agent-dev"):
        assert name in listed, f"§2 BRIDGE_SKILLS is missing the producer {name}"


def test_qr_feeder_skillmd_audit_covers_every_surface():
    """E-024: §2's QR_FEEDERS_SKILLMD must equal the bridge-citing SKILL.md set.

    §2 audits the SKILL.md QR feeders through a hardcoded three-entry list — the
    last bridge-audit list not pinned to a tree-derived set. This calls the
    bench's check, which derives the bridge-citing ``skills/*/SKILL.md`` set and
    pins §2's list to it.
    """
    bench = _load_bench()
    problem = bench._check_qr_feeder_skillmd_audit_covers_every_surface()
    assert problem is None, problem


def test_qr_feeder_skillmd_audit_lists_every_bridge_skill():
    """§2's QR_FEEDERS_SKILLMD must list every bridge-citing SKILL.md (E-024)."""
    text = (PLUGIN / "scripts" / "check-plugin.sh").read_text(
        encoding="utf-8", errors="replace")
    blk = re.search(r"QR_FEEDERS_SKILLMD = \[(.*?)\]", text, re.DOTALL)
    assert blk, "check-plugin.sh §2 has no QR_FEEDERS_SKILLMD list"
    listed = set(re.findall(r'"([^"]+)"', blk.group(1)))
    tree = {
        p.parent.name
        for p in (PLUGIN / "skills").glob("*/SKILL.md")
        if "dev-skill-to-methodology-bridge" in p.read_text(
            encoding="utf-8", errors="replace")
    }
    assert listed == tree, (
        f"§2 QR_FEEDERS_SKILLMD {sorted(listed)} != bridge-citing SKILL.md {sorted(tree)}"
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


def test_documented_gate_invocations_parse_under_argparse():
    """E-034: the gate's own usage text must not advertise a rejected command.

    The docstring taught `run_experiment.py --verify <rec>`; running it verbatim
    exited 2 on argparse ("unrecognized arguments"). This calls the bench's
    check, which extracts every documented invocation and drives the real gate
    with it.
    """
    bench = _load_bench()
    problem = bench._check_documented_gate_invocations_parse()
    assert problem is None, problem


def test_experiment_lineage_declared_and_enforced():
    """E-022: the gate must refuse a draft with no Lineage, templates must ship it.

    A new experiment must declare what it carries from the corpus ('none' for a
    genuinely new line of inquiry, otherwise the prior E-ids) — nothing asked
    before this, so a draft that opened no sibling passed as one that read all
    of them. This calls the bench's check, which drives the gate on a
    Lineage-less draft, pins both E templates, and re-resolves every declared
    ref under docs/experiments/.
    """
    bench = _load_bench()
    problem = bench._check_experiment_lineage_enforced()
    assert problem is None, problem


def test_negtest_covers_the_provenance_gate():
    """The negtest must break §3 and its count must match its own headers (E-023).

    ``--negtest`` is the audit's proof that it still catches breakage, yet
    ``total_stages`` drove the banners AND the closing success line, so a
    deleted stage would still print "all N successful" — and §3 (the
    forged-token gate) had no stage while the runbooks advertised 7 for an
    8-stage script. This calls the bench's check, which pins the declared count
    to the headers, demands a §3 stage, and pins both runbooks to that count.
    """
    bench = _load_bench()
    problem = bench._check_negtest_stage_wiring()
    assert problem is None, problem


def test_e_templates_carry_the_lineage_field():
    """Both E template copies must ship the Lineage field (E-022)."""
    for rel in ("templates/_template_E.md", "docs/experiments/_template.md"):
        text = (PLUGIN / rel).read_text(encoding="utf-8", errors="replace")
        assert "**Lineage:**" in text, f"{rel} is missing the Lineage field"


def test_all_negtest_counts_match_their_stages():
    """E-026: every negtest suite's declared count must match its stages.

    E-023 pinned only check-plugin.sh's count; check-techdebt.sh kept the same
    unfalsified shape and check-custom.sh had no counter at all. This calls the
    bench's check, which pins each suite's total_stages to its 1..N headers and
    closing line, and the runbooks' advertised check-custom count.
    """
    bench = _load_bench()
    problem = bench._check_all_negtest_counts_are_falsifiable()
    assert problem is None, problem


def test_check_custom_negtest_has_one_counter():
    """check-custom.sh --negtest must declare total_stages and use it (E-026)."""
    text = (PLUGIN / "scripts" / "check-custom.sh").read_text(
        encoding="utf-8", errors="replace")
    assert re.search(r"total_stages = \d+", text), (
        "check-custom.sh --negtest has no total_stages counter"
    )
    assert "{total_stages}" in text, "check-custom.sh does not use its counter"


def test_negtest_suites_cover_the_known_scripts():
    """The derived negtest-suite set must include the three shipped suites (E-026).

    The bench derives the suites from the tree so a new counter is covered
    automatically; this pins the three that exist so one cannot silently drop
    out of that set (e.g. if its ``total_stages`` line is deleted).
    """
    bench = _load_bench()
    names = {p.name for p in bench._negtest_suites()}
    for name in ("check-plugin.sh", "check-custom.sh", "check-techdebt.sh"):
        assert name in names, (
            f"negtest suite {name} is no longer derived from the tree — "
            "its total_stages counter was removed or renamed"
        )


def test_bridge_runtime_visibility_audit_covers_every_surface():
    """E-027: §2b's bridge-merge probe must cover every bridge-citing override.

    §2b proves the BRIDGE survives the ``resolve_customization`` merge through a
    hardcoded ``TOML_SKILLS``/``AGENT_TOML_SKILLS`` pair. E-024 called
    ``QR_FEEDERS_SKILLMD`` the last unpinned bridge-audit list; the tree
    falsifies that — this pair is another. This calls the bench's check, which
    derives the bridge-citing ``custom/*.toml`` set and pins §2b's coverage to it.
    """
    bench = _load_bench()
    problem = bench._check_bridge_runtime_visibility_audit_covers_every_surface()
    assert problem is None, problem


def test_bridge_runtime_visibility_audit_lists_every_bridge_override():
    """§2b's coverage must equal the tree's bridge-citing custom/*.toml (E-027)."""
    text = (PLUGIN / "scripts" / "check-plugin.sh").read_text(
        encoding="utf-8", errors="replace")
    def _listed(name):
        blk = re.search(name + r" = \[(.*?)\]", text, re.DOTALL)
        assert blk, f"check-plugin.sh §2b has no {name} list"
        return set(re.findall(r'"([^"]+)"', blk.group(1)))
    covered = _listed("TOML_SKILLS") | _listed("AGENT_TOML_SKILLS")
    tree = {
        p.stem
        for p in (PLUGIN / "custom").glob("*.toml")
        if p.name != "config.toml"
        and "dev-skill-to-methodology-bridge" in p.read_text(
            encoding="utf-8", errors="replace")
    }
    assert covered == tree, (
        f"§2b coverage {sorted(covered)} != bridge-citing custom/*.toml {sorted(tree)}"
    )


def test_cross_machine_marker_is_anchored():
    """E-025: §3 must read Re-Measured-By from the canonical bullet, not prose.

    §3's guard was an unanchored ``grep 'Re-Measured-By:'``, so E-023's prose
    mention of the marker was misparsed into a bogus filename and a wrong
    CROSS-MACHINE classification. This calls the bench's check, which evaluates
    §3's marker pattern against a prose line (must not match) and the canonical
    bullet (must match and yield its E-id).
    """
    bench = _load_bench()
    problem = bench._check_cross_machine_marker_is_anchored()
    assert problem is None, problem


def test_cross_machine_marker_guard_is_anchored_in_script():
    """§3 must carry the anchored marker pattern and no unanchored guard (E-025)."""
    text = (PLUGIN / "scripts" / "check-plugin.sh").read_text(
        encoding="utf-8", errors="replace")
    assert "RE_MEASURED_MARKER=" in text, "§3 dropped the anchored marker pattern"
    assert "grep -q 'Re-Measured-By:'" not in text, (
        "§3 regressed to the unanchored 'Re-Measured-By:' guard"
    )


def test_shipped_gate_invocations_resolve_where_they_ship():
    """E-038/E-039: shipped text may not teach an invocation that dies there.

    The 2026-10-12 consuming session pasted the engine's own
    ``python3 skills/bmad-research-experiment/scripts/run_experiment.py
    --amend-plan …`` remedy (a path that exists only inside the plugin
    checkout) and was denied 19 times; the same session's shipped docs also
    taught bare ``run_experiment.py --verify`` and named ``guard-code.sh`` —
    a script with zero files in this tree's whole history. E-039 widened the
    rule from the gate script to EVERY shipped plugin script (derived from
    the filesystem): GUIDE/KILAVUZ alone carried 80 dead ``python3
    bmad/scripts/blackboard.py …`` invocations. Every shipped invocation must
    carry a resolving root ({metodoloji-root}, {skill-root}, $SYNCED) on its
    own line. The scan is independent of the bench so a drifted bench cannot
    bury the closure; the bench is then pinned to keep its own check.
    """
    roots = ("{metodoloji-root}", "{skill-root}", "$SYNCED",
             "METODOLOJI_SYNCED")
    globs = ("GUIDE.md", "KILAVUZ.md", "docs/CLAUDE.md", "docs/bmad/*.md",
             "docs/experiments/_template.md", "commands/*.md",
             "custom/*.toml", "templates/*.md", "skills/*/SKILL.md",
             "skills/bmad-research-experiment/plan.md",
             "skills/bmad-research-experiment/experiment-log.md",
             "skills/bmad-research-experiment/scripts/run_experiment.py",
             "hooks/scripts/*.sh")
    # Every `scripts/` tree, every shipped script — .py and .sh alike, so the
    # top-level tooling (create-qr-record, sync-story-qr, the check-*.sh gates)
    # cannot drop out of the rule the way it did under the old two-glob
    # derivation (E-037's narrow-surface class one level up).
    names = set()
    for pat in ("scripts/*", "bmad/scripts/*", "skills/*/scripts/*",
                "hooks/scripts/*"):
        for p in PLUGIN.glob(pat):
            if p.is_file() and p.suffix in (".py", ".sh"):
                names.add(p.name)
    assert "run_experiment.py" in names and "blackboard.py" in names, (
        "the shipped-script derivation lost a known CLI")
    assert "create-qr-record.py" in names and "check-custom.sh" in names, (
        "the shipped-script derivation lost a top-level CLI or a .sh gate")
    # A template reference resolves only at the plugin root ({metodoloji-root});
    # a bare `templates/_template_X.md` dies in a consuming project, which
    # ships the copies under docs/**/ but no root-level templates/ dir.
    template_re = re.compile(r"(?<![\w}/])templates/_template_[\w.]+")
    problems = []
    for pat in globs:
        for path in sorted(PLUGIN.glob(pat)):
            rel = path.relative_to(PLUGIN).as_posix()
            for lineno, line in enumerate(
                    path.read_text(encoding="utf-8").splitlines(), 1):
                for name in names:
                    for m in re.finditer(re.escape(name) + r"\s+--", line):
                        if not any(r in line[:m.start()] for r in roots):
                            problems.append(f"{rel}:{lineno}: bare {name}")
                    m2 = re.search(r"python3\s+(?:\S+/)?(?:scripts/)?"
                                   + re.escape(name) + r"\b", line)
                    if m2:
                        seg = line[:m2.end() - len(name)]
                        if not any(r in seg for r in roots):
                            problems.append(f"{rel}:{lineno}: unrooted "
                                            f"python3 {name}")
                for m in template_re.finditer(line):
                    if not any(r in line[:m.start()] for r in roots):
                        problems.append(f"{rel}:{lineno}: unrooted template "
                                        f"path {m.group(0)}")
                if "guard-code.sh" in line:
                    problems.append(f"{rel}:{lineno}: phantom guard-code.sh")
    assert not problems, f"dead plugin instructions where they ship: {problems}"
    src = BENCH.read_text(encoding="utf-8")
    assert ("plugin-script invocations resolve where they ship "
            "(E-038, E-039)") in src, (
        "the bench dropped its shipped-invocation check"
    )


_ACTIVATION_MARKERS = (
    ":", "before ", "after ", "during ", "per ", "bridge",
    "methodology manifesto", "record chain", "mod a", "do not ", "never ",
    "error and recreate", "approved", "rejected", "verify", "compliance",
    "manifesto",
)


def test_activation_steps_append_carries_the_methodology_contract():
    """E-040: every activation_steps_append entry must be a compliance step.

    The field is executed at Step 7 of every skill ("execute each entry in
    order ... before starting the work"). A bare workflow-stage command there
    makes the skill run the whole flow DURING activation — the 2026-10-12
    Claude Code session ran ``/metodoloji:bmad-research-experiment for story
    2-1`` and never left activation (manifesto + lineage + record format +
    gate mechanics read, then the user interrupted) because the team override
    shipped "Define the hypothesis" / "Create the experiment design" /
    "Analyze the results" as its activation steps, on top of the shipped two.
    The same scaffolding shape recurred in bmb/index/loop/module/shard/wds.

    Independent derivation (tomllib, no check-custom import): every entry must
    carry at least one frame marker — a structured check, a temporal/compliance
    frame, or a named contract token.
    """
    violations = []
    for path in sorted((PLUGIN / "custom").glob("*.toml")):
        if path.name == "config.toml":
            continue
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        for step in (data.get("workflow", {})
                     .get("activation_steps_append", []) or []):
            if not any(m in step.lower() for m in _ACTIVATION_MARKERS):
                violations.append(f"{path.name}: {step!r}")
    assert not violations, (
        "bare workflow steps in activation_steps_append (E-040): "
        + "; ".join(violations)
    )


def test_help_catalog_skills_resolve_to_shipped_dirs():
    """E-042: every /bmad-help menu entry must name a skill that ships.

    The Web Design Studio rows offered `bmad-wds-*` names while the shipped
    directories are `wds-*`, so help recommended skills that do not exist (an
    offer to run `bmad-wds-project-brief` dies). Independent derivation here
    (csv + the tree); the bench check is pinned to keep owning it.
    """
    shipped = {p.parent.name for p in (PLUGIN / "skills").glob("*/SKILL.md")}
    unresolved = []
    for rel in ("bmad/_config/bmad-help.csv", "bmad/wds/module-help.csv"):
        with open(PLUGIN / rel, newline="", encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        header, data = rows[0], rows[1:]
        idx = header.index("skill")
        for i, row in enumerate(data, start=2):
            if len(row) != len(header):
                continue
            skill = row[idx]
            if skill and skill != "_meta" and skill not in shipped:
                unresolved.append(f"{rel}:{i}: {skill}")
    assert not unresolved, (
        "/bmad-help offers skills that do not ship: " + "; ".join(unresolved)
    )
    bench = _load_bench()
    assert bench._check_help_menu_code_uniqueness() is None


def test_tool_contract_names_the_real_harness_failures():
    """E-041: the coaching must name the failure the harness actually emits.

    A real Claude Code session lost many turns to
    ``InputValidationError: ... The required parameter 'file_path' is missing``
    (params dropped on PARALLEL Read/Bash calls); the shipped cue named only
    ``provided as 'unknown'`` and never forbade batching. Independent derivation
    here, then the bench check is pinned to keep owning it.
    """
    boot = (PLUGIN / "hooks" / "scripts" / "bootstrap.sh").read_text(
        encoding="utf-8", errors="replace")
    assert boot.count("required parameter") >= 2, (
        "bootstrap.sh must name the real 'required parameter' signature in both "
        "the python tools var and the printf fallback"
    )
    assert boot.count("never batch") >= 2, (
        "bootstrap.sh must carry the one-call-at-a-time rule in both copies"
    )
    for rel in ("skills/bmad-workflow/SKILL.md",
                "skills/bmad-research-experiment/SKILL.md"):
        text = (PLUGIN / rel).read_text(encoding="utf-8", errors="replace")
        assert "required parameter" in text and "never batch" in text, (
            f"{rel}: tool contract lost the real failure signature / batching rule"
        )
    bench = _load_bench()
    assert bench._check_tool_contract_names_the_real_harness_failures() is None


def test_change_radar_bullet_matches_the_shipped_subscriptions():
    """E-043: the DOCUMENTED change radar must equal the SHIPPED radar.

    The blackboard commit that added the §14 radar bullet also added a lint
    that checked only that a `subscribe --watcher <self>` line existed, so a
    WRONG pattern slipped through: the runbooks and the skills drifted on the
    very commit that documented the radar (`bmad-spec` was documented to watch
    `prd.*` but shipped only `architecture.*`; two skills were called "all on
    the session channel" while they subscribe on `stop`). Independent
    derivation here (regex over the skills + the runbooks, no bench import),
    then the bench check is pinned to keep owning it.
    """
    sub_re = re.compile(
        r'subscribe --watcher (\S+) --pattern "([^"]+)" --channel (\S+)')
    shipped: dict[str, set] = {}
    for md in sorted((PLUGIN / "skills").glob("*/SKILL.md")):
        for watcher, pat, chan in sub_re.findall(
                md.read_text(encoding="utf-8", errors="replace")):
            shipped.setdefault(watcher, set()).add((pat, chan))
    assert shipped, "no skill ships a subscribe line — the change radar vanished"
    seg_re = re.compile(
        r"`([a-z][\w-]+)`\s*(?:watches|→)\s*`([^`]+)`\s*\((session|stop)\)")
    for rel in ("GUIDE.md", "KILAVUZ.md"):
        text = (PLUGIN / rel).read_text(encoding="utf-8", errors="replace")
        bullet = next((ln for ln in text.splitlines()
                       if "change radar" in ln or "değişim radarı" in ln), None)
        assert bullet is not None, f"{rel}: no change-radar bullet found"
        claimed: dict[str, set] = {}
        for skill, pats, chan in seg_re.findall(bullet):
            claimed.setdefault(skill, set()).update(
                (p.strip(), chan) for p in pats.split(",") if p.strip())
        assert claimed == shipped, (
            f"{rel}: documented radar {sorted(claimed.items())} != shipped "
            f"{sorted(shipped.items())}")
    bench = _load_bench()
    assert bench._check_change_radar_contract_matches() is None


def test_relay_graph_edges_match_the_declared_table():
    """E-044: the declared relay graph must equal the graph the tree ships.

    `EXPECTED_LINKS` once carried a source-only marker (`link --a prd.`), so the
    lint could not see a wrong target or an undocumented relation. Independent
    derivation here (regex over skills + custom TOMLs, no bench import), then the
    bench check is pinned to keep owning it.
    """
    edge_re = re.compile(r"link --a (\S+) --b (\S+) --relation (\S+)")
    shipped: dict[str, list] = {}
    for md in sorted((PLUGIN / "skills").glob("*/SKILL.md")):
        blob = md.read_text(encoding="utf-8", errors="replace")
        for f in sorted(md.parent.rglob("*.md")):
            if f.name != "SKILL.md":
                blob += "\n" + f.read_text(encoding="utf-8", errors="replace")
        edges = edge_re.findall(blob)
        if edges:
            shipped[md.parent.name] = edges
    for toml in sorted((PLUGIN / "custom").glob("*.toml")):
        edges = edge_re.findall(toml.read_text(encoding="utf-8", errors="replace"))
        if edges:
            shipped.setdefault(toml.stem, []).extend(edges)
    assert shipped, "no relay graph edge ships — the graph vanished"
    spec = importlib.util.spec_from_file_location(
        "check_handoff_edges", PLUGIN / "scripts" / "check-handoff.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    declared = mod.EXPECTED_LINKS
    for surface, edges in sorted(shipped.items()):
        want = declared.get(surface)
        assert want is not None, f"{surface} ships graph edges but is undeclared"
        for a, b, r in edges:
            assert any(a.startswith(s) and b.startswith(d) and r == rel
                       for s, d, rel in want), \
                f"{surface}: edge {a} -> {b} ({r}) is not the declared edge {want}"
            assert r in ("informs", "constrains", "validates"), \
                f"{surface}: relation '{r}' outside the documented vocabulary"
    assert set(declared) == set(shipped), (
        f"declared-only {sorted(set(declared) - set(shipped))}; "
        f"shipped-only {sorted(set(shipped) - set(declared))}")
    bench = _load_bench()
    assert bench._check_relay_graph_edges_match_declared() is None


def test_every_spawn_hands_its_child_an_explicit_stdin():
    """E-045: a spawn that inherits the parent's stdio handle never launches.

    `python -m pytest` under the gate runs with no stdin of its own, and
    `subprocess.run([\u2026])` hands the child the parent's STD_INPUT_HANDLE \u2014 a
    handle the host then cannot duplicate, so the spawn dies with WinError 6
    BEFORE the child exists and the test reports a failed assertion for a
    process that never ran. The repo had already applied the remedy (DEVNULL)
    at thirty call sites with a comment naming this failure; twenty sites were
    still unprotected, including the bench's own pytest falsifier and the
    gate's measurement runner. Before the fix, `python -m pytest scripts/tests`
    with stdin closed failed ten tests; after it the same command is green. The
    rule is now an invariant: the check is driven red on a bare spawn, green on
    both accepted forms, and the shipped tree is pinned clean.
    """
    bench = _load_bench()
    bare = 'subprocess.run(["git", "status"], capture_output=True)'
    assert bench._spawn_calls_without_stdin(bare, "x.py") == ["x.py:1"]
    for accepted in ('subprocess.run(cmd, stdin=subprocess.DEVNULL)',
                     'subprocess.run(cmd, input=text)'):
        assert bench._spawn_calls_without_stdin(accepted, "x.py") == []
    hidden = bench._spawn_calls_without_stdin("subprocess.run(**kw)", "x.py")
    assert hidden and "**kwargs" in hidden[0], hidden
    assert bench._check_every_spawn_hands_stdin() is None
    # the sites that were unprotected stay protected (the falsifier's own paths)
    for rel in ("scripts/bench/bench_consistency.py",
                "scripts/tests/test_check_techdebt.py",
                "scripts/tests/test_check_triggers.py",
                "scripts/tests/test_record_generators.py",
                "skills/bmad-research-experiment/scripts/run_experiment.py"):
        text = (PLUGIN / rel).read_text(encoding="utf-8", errors="replace")
        assert "subprocess.DEVNULL" in text, f"{rel} lost its explicit stdin"


def test_activation_compliance_rule_is_wired_and_audited():
    """check-custom.sh §2b must own the rule and ship a negtest stage for it."""
    text = (PLUGIN / "scripts" / "check-custom.sh").read_text(
        encoding="utf-8", errors="replace")
    assert "2b) activation_steps_append compliance frame" in text, (
        "check-custom.sh lost its §2b activation compliance-frame audit"
    )
    assert re.search(r"total_stages = 4", text), (
        "check-custom.sh must declare 4 negtest stages (incl. the §2b stage)"
    )
    assert "bare workflow step" in text, (
        "§2b no longer names the violation it reports"
    )


# --- E-050: the template inventory, judged in the INSTALLED layout -------------
# The init-copied README is read by a user inside a project that has ONLY the
# skeleton (init copies templates/ and creates skeleton.DIRECTORIES, nothing
# else), and the runbooks plus CHECK 8's scope restate the same inventory by
# hand. Measured 2026-10-15: the README promised `_template_PM.md` and an
# `incidents/` directory no installer created, its manifesto links resolved only
# inside the plugin dogfood (dead in every project), and neither runbook listed
# the Post-Mortem template at all.

def _rewrite(path: Path, mutate) -> None:
    """Mutate a fixture file through LF, refusing a no-op (setup must bite)."""
    text = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
    mutated = mutate(text)
    assert mutated != text, f"fixture setup broken: {path.name} was not mutated"
    path.write_text(mutated, encoding="utf-8")


def _inventory_fixture(tmp_path: Path) -> Path:
    """A miniature tree carrying every file the E-050 checks read."""
    for rel in ("bmad/scripts/skeleton.py", "README.md", "GUIDE.md", "KILAVUZ.md",
                "scripts/check-methodology.sh"):
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((PLUGIN / rel).read_bytes())
    (tmp_path / "templates").mkdir(exist_ok=True)
    for src in (PLUGIN / "templates").glob("*.md"):
        (tmp_path / "templates" / src.name).write_bytes(src.read_bytes())
    return tmp_path


def test_init_copied_docs_only_promise_paths_init_creates(tmp_path):
    """E-050: a copied doc may only name paths init itself creates.

    Independent derivation first (the copied README's template names vs the
    shipped templates/ set), then the bench check is pinned to keep owning it,
    then three falsifiers: the promised template removed, a relative link into
    the plugin, and a tree directory init does not create.
    """
    bench = _load_bench()
    assert bench._installed_doc_path_problems(PLUGIN) == []

    readme = (PLUGIN / "templates" / "README.md").read_text(
        encoding="utf-8", errors="replace")
    named = set(re.findall(r"_template_[A-Za-z0-9]+\.md", readme))
    shipped = {p.name for p in (PLUGIN / "templates").glob("*.md")}
    assert named <= shipped, (
        f"the init-copied README names templates that do not ship: {sorted(named - shipped)}"
    )

    # falsifier 1: the shipped bug — the promised template does not exist
    root = _inventory_fixture(tmp_path)
    (root / "templates" / "_template_PM.md").unlink()
    problems = bench._installed_doc_path_problems(root)
    assert any("_template_PM.md" in p and "templates/README.md" in p
               for p in problems), problems

    # falsifier 2: a relative link to a plugin-canonical doc (init never copies it)
    root = _inventory_fixture(tmp_path / "links")
    _rewrite(root / "templates" / "README.md", lambda t: t.replace(
        "`{metodoloji-root}/docs/bmad/development-methodology.md`",
        "[Development Methodology](../bmad/development-methodology.md)"))
    problems = bench._installed_doc_path_problems(root)
    assert any("../bmad/development-methodology.md" in p for p in problems), problems

    # falsifier 3: the tree draws a directory init does not create
    root = _inventory_fixture(tmp_path / "dirs")
    _rewrite(root / "bmad" / "scripts" / "skeleton.py",
             lambda t: t.replace('    "docs/development/incidents",\n', ""))
    problems = bench._installed_doc_path_problems(root)
    assert any("incidents" in p for p in problems), problems


def test_documented_template_inventory_equals_the_shipped_one(tmp_path):
    """E-050: the three runbooks and CHECK 8 must list what the tree ships."""
    bench = _load_bench()
    assert bench._template_inventory_problems(PLUGIN) == []

    # falsifier 1: a runbook shorthand that drops a shipped template
    root = _inventory_fixture(tmp_path)
    _rewrite(root / "README.md", lambda t: t.replace(
        "_template_E/IR/SP/QR/PR/S/PM/BD/C", "_template_E/IR/SP/QR/PR/S/BD/C"))
    problems = bench._template_inventory_problems(root)
    assert any("README.md" in p and "_template_PM.md" in p for p in problems), problems

    # falsifier 2: a shipped template no runbook lists (the other direction)
    root = _inventory_fixture(tmp_path / "extra")
    (root / "templates" / "_template_ZZ.md").write_text("x", encoding="utf-8")
    problems = bench._template_inventory_problems(root)
    assert any("_template_ZZ.md" in p for p in problems), problems

    # falsifier 3: CHECK 8 silently narrowing its scan scope
    root = _inventory_fixture(tmp_path / "scope")
    _rewrite(root / "scripts" / "check-methodology.sh", lambda t: t.replace(
        ' "$METODOLOJI_ROOT/templates/_template_PM.md"', "").replace(
        '\n    "$PROJECT_ROOT/docs/development/incidents/_template_PM.md"', ""))
    problems = bench._template_inventory_problems(root)
    assert any("check-methodology.sh" in p for p in problems), problems


def test_postmortem_template_ships_with_its_install_destination():
    """E-050: the Post-Mortem record has both a template and a home.

    GUIDE/KILAVUZ told readers to write `docs/development/incidents/PM-XXX.md`
    and the PR template called that path its "Template:" while no
    `_template_PM.md` existed anywhere and no installer created the directory.
    Independent derivation here (the skeleton module + the runbook surfaces), so
    the family cannot be half-shipped again.
    """
    spec = importlib.util.spec_from_file_location(
        "skeleton_pm_pin", PLUGIN / "bmad" / "scripts" / "skeleton.py")
    sk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sk)
    assert ("_template_PM.md", "docs/development/incidents/_template_PM.md") \
        in sk.TEMPLATE_PAIRS
    assert "docs/development/incidents" in sk.DIRECTORIES

    src = PLUGIN / "templates" / "_template_PM.md"
    assert src.is_file(), "templates/_template_PM.md does not ship"
    copy = PLUGIN / "docs" / "development" / "incidents" / "_template_PM.md"
    assert copy.is_file() and copy.read_bytes() == src.read_bytes(), (
        "the dogfood copy drifted from templates/_template_PM.md (check-plugin §6c)"
    )

    init = (PLUGIN / "commands" / "init.md").read_text(encoding="utf-8", errors="replace")
    assert "docs/development/incidents/" in init
    assert "_template_PM.md` → `docs/development/incidents/_template_PM.md`" in init

    for rel in ("GUIDE.md", "KILAVUZ.md"):
        text = (PLUGIN / rel).read_text(encoding="utf-8", errors="replace")
        assert re.search(
            r"docs/development/incidents/PM-XXX\.md`?[^\n]*_template_PM\.md", text), (
            f"{rel}: the post-mortem guidance no longer names its template")
        assert re.search(
            r"\|\s*\*\*PM\*\*[^\n]*docs/development/incidents/PM-NNN\.md", text), (
            f"{rel}: the record table does not list the Post-Mortem record")

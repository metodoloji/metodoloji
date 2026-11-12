"""Strip the TEA/testing group from the test suites and the bridge doc."""
import pathlib

EDITS = {
    "hooks/engine/tests/test_blackboard.py": [
        ('''def test_testing_sub_chain_feeders_fan_in_on_the_quality_record(root):
    """The TEA toolbox feeds ONE record: nine feeder hops into the quality
    record, each attributed to its own run-key namespace and none of them
    leaking into 'extra'."""
    bb.post_handoff(root, "bmad-quality-record", "test.trace.R-1",
                    "traceability gate: PASS")
    bb.post_handoff(root, "bmad-quality-record", "test.e2e.checkout", "tests generated")
    h = bb.chain_health(root)
    hops = {(hop["from"], hop["to"]): hop for hop in h["sub_chains"]["testing"]}
    assert hops[("bmad-testarch-trace", "bmad-quality-record")]["waiting"] == 1
    assert hops[("bmad-qa-generate-e2e-tests", "bmad-quality-record")]["waiting"] == 1
    assert h["extra"] == []
    assert bb._signal_sender("test.trace.R-1: gate PASS") == "bmad-testarch-trace"
    assert bb._signal_sender("test.e2e.checkout: done") == "bmad-qa-generate-e2e-tests"

def test_teach_me_testing_routes_into_the_tea_chain(root):
    """TEA Academy's close-out is a real baton, not decoration: the framework
    run is a routed receiver, so the signal surfaces in the start nudge."""
    bb.post_handoff(root, "bmad-testarch-framework", "teach.ada", "academy complete")
    h = bb.chain_health(root)
    hops = {(hop["from"], hop["to"]): hop for hop in h["sub_chains"]["testing"]}
    assert hops[("bmad-teach-me-testing", "bmad-testarch-framework")]["waiting"] == 1
    assert h["extra"] == []
    assert bb.pending_handoff_channels(root) == {"bmad-testarch-framework": 1}
    assert bb._signal_sender("teach.ada: academy complete") == "bmad-teach-me-testing"

''', ''),
        ('assert {"discovery", "alt_dev", "testing", "governance"} <= set(bb.SUB_CHAINS)',
         'assert {"discovery", "alt_dev", "governance"} <= set(bb.SUB_CHAINS)'),
    ],
    "scripts/tests/test_check_handoff.py": [
        ('        "bmad-tea", "bmad-party-mode", "bmad-sprint-status",\n',
         '        "bmad-party-mode", "bmad-sprint-status",\n'),
        ('    assert set(routers) >= {\n'
         '        "bmad-tea",\n'
         '    }\n',
         '    assert set(routers) >= set()\n'),
    ],
    "docs/bmad/dev-skill-to-methodology-bridge.md": [
        ('| bmad-testarch-atdd | ✅ Feeder | Adds ATDD tests/checklist to the existing QR record\'s Mechanical checks — opens no record of its own |\n'
         '| bmad-testarch-automate / -ci / -framework / -nfr / -test-design / -test-review / -trace | ✅ Feeder | Same feeder contract as atdd: evidence lands in the QR\'s Mechanical checks (`custom/bmad-testarch-*.toml` BRIDGE) |\n'
         '| bmad-qa-generate-e2e-tests | ✅ Feeder | Generated API/E2E tests → QR Mechanical checks |\n'
         '| bmad-teach-me-testing | ❌ N/A | Learning workflow — produces a certificate, not a methodology record |\n'
         '| TEA feeders (all of the above except teach-me-testing) | ✅ Chain | Each posts one `test.<tool>.<slug>` baton to `handoff.bmad-quality-record` on close-out (sub-chain `testing`); feeders never stamp an E→IR→SP→S→QR→PR position |\n',
         ''),
    ],
    "skills/bmad-party-mode/scripts/resolve_party.py": [
        ('    """Short alias for an installed agent code: bmad-tea -> tea."""',
         '    """Short alias for an installed skill code: bmad-agent-x -> agent-x."""'),
    ],
    "skills/bmad-forge-idea/scripts/resolve_personas.py": [
        ('    """Short alias for an installed agent code: bmad-tea -> tea."""',
         '    """Short alias for an installed skill code: bmad-agent-x -> agent-x."""'),
    ],
}

for rel, pairs in EDITS.items():
    p = pathlib.Path(rel)
    s = p.read_text(encoding="utf-8")
    for old, new in pairs:
        if old not in s:
            print(f"SKIP {rel}: {old.splitlines()[0][:60]}")
            continue
        s = s.replace(old, new, 1)
        print(f"OK   {rel}")
    p.write_text(s, encoding="utf-8")

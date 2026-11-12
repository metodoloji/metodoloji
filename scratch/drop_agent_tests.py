"""Strip the bmad-agent-* expectations from the test suites."""
import pathlib

EDITS = {
    "hooks/engine/tests/test_blackboard.py": [
        ('''def test_dev_persona_is_the_third_alt_dev_branch(root):
    """The dev persona implements and carries the S→QR→PR bridge itself, so it
    converges on the same formal review gate as quick-dev and dev-auto — and
    like them it stamps no methodology position (a branch, not a stage)."""
    bb.post_handoff(root, "bmad-code-review", "agentdev.3-2-digest",
                    "dev persona complete")
    h = bb.chain_health(root)
    hops = {(hop["from"], hop["to"]): hop for hop in h["sub_chains"]["alt_dev"]}
    assert hops[("bmad-agent-dev", "bmad-code-review")]["waiting"] == 1
    assert hops[("bmad-quick-dev", "bmad-code-review")]["status"] == "idle"
    assert h["extra"] == []
    assert bb._signal_sender("agentdev.3-2-digest: done") == "bmad-agent-dev"

''', ''),
    ],
    "scripts/tests/test_check_handoff.py": [
        ('        "bmad-quick-dev", "bmad-dev-auto", "bmad-agent-dev",\n',
         '        "bmad-quick-dev", "bmad-dev-auto",\n'),
        ('        "bmad-agent-analyst", "bmad-agent-architect", "bmad-agent-pm",\n'
         '        "bmad-agent-tech-writer", "bmad-agent-ux-designer", "bmad-agent-builder",\n'
         '        "bmad-tea", "bmad-party-mode", "bmad-sprint-status",\n',
         '        "bmad-tea", "bmad-party-mode", "bmad-sprint-status",\n'),
        ('    assert set(routers) >= {\n'
         '        "bmad-agent-pm", "bmad-tea",\n'
         '    }\n',
         '    assert set(routers) >= {\n'
         '        "bmad-tea",\n'
         '    }\n'),
        ('''    Two legitimate persona shapes are allowed: a pure ROUTER (posts nothing),
    and a producing persona registered as an ENTRY POINT (it dispatches a menu
    AND signals its hop). The producing personas are locked in here because
    they were filed as tools while shipping exactly the router template.
    """''',
         '''    Two legitimate persona shapes are allowed: a pure ROUTER (posts nothing),
    and a producing persona registered as an ENTRY POINT (it dispatches a menu
    AND signals its hop).
    """'''),
    ],
    "scripts/tests/test_selfcheck_scripts.py": [
        ('''    them — a probe that stripped the VERIFY marker from ``bmad-agent-dev``
    left the audit green.''',
         '''    them — a probe that stripped the VERIFY marker from an
    ``agent.principles`` producer left the audit green.'''),
        ('''    Stripping the marker from ``bmad-agent-dev`` must be visible to #2c; if the
    audit read ``workflow.activation_steps_append`` for it, the invariant would
    silently pass on a surface with no workflow steps (E-018).''',
         '''    A surface that keeps its BRIDGE in ``[agent].principles`` must be read
    from that key; reading ``workflow.activation_steps_append`` instead would
    silently pass on a skill with no workflow steps (E-018). No such producer
    ships today, so the map is pinned as empty rather than dropped.'''),
        ('    for name in ("bmad-agent-dev",):\n'
         '        assert audited.get(name) == "agent.principles", (\n'
         '            f"{name} must be audited via agent.principles, got {audited.get(name)!r}"\n'
         '        )\n',
         '    agent_principle_names = [n for n, k in audited.items()\n'
         '                             if k == "agent.principles"]\n'
         '    assert not agent_principle_names, (\n'
         '        f"unexpected agent.principles producer(s): {agent_principle_names}"\n'
         '    )\n'),
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
        print(f"OK   {rel}: {old.splitlines()[0][:60]}")
    p.write_text(s, encoding="utf-8")

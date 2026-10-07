"""check-handoff wiring lint — the chain contract must hold under pytest too.

The lint predates this test and CI calls it directly; wiring it into the suite
as well means a broken hop fails the bench's pytest falsifier, not only a
manual audit run. The falsification tests point the SAME check functions at a
throwaway tree so the red path is proven without mutating the real skill tree
(the deliberate `--negtest` mutation stays a manual/CI step).
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = ROOT / "scripts" / "check-handoff.py"


def _load_lint():
    """Import the lint script (its filename has a hyphen, so no plain import)."""
    spec = importlib.util.spec_from_file_location("check_handoff", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # __name__ != "__main__": the CLI block stays shut
    return mod


def _run_cli(*args):
    # stdin=DEVNULL + CREATE_NO_WINDOW: the suite must not inherit a console
    # handle pair it cannot duplicate on Windows (see test_mirror's CLI test).
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          timeout=120, cwd=str(ROOT),
                          stdin=subprocess.DEVNULL,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def test_handoff_wiring_is_clean():
    r = _run_cli()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[ERROR]" not in r.stdout
    assert "match the chain contract" in r.stdout


def test_lint_flags_a_missing_hop(tmp_path, capsys, monkeypatch):
    """A green linter that cannot go red proves nothing: an upstream that peeks
    but never posts must be reported as a broken hop."""
    mod = _load_lint()
    skill = tmp_path / "bmad-upstream"
    skill.mkdir()
    (skill / "SKILL.md").write_text("peek: handoffs --skill bmad-upstream\n",
                                    encoding="utf-8")
    monkeypatch.setattr(mod, "PLUGIN", tmp_path)   # no custom/ layer here
    monkeypatch.setattr(mod, "SKILLS", tmp_path)
    monkeypatch.setattr(mod, "EXPECTED_POSTS", {"bmad-upstream": ["bmad-downstream"]})
    assert mod.check() != 0  # nonzero = the lint went red (it reports the count)
    out = capsys.readouterr().out
    assert "missing post 'handoff --to bmad-downstream'" in out
    assert "peeks handoffs --skill bmad-upstream" in out  # the half that IS wired


def test_run_openers_start_from_the_digest(tmp_path, capsys, monkeypatch):
    """The chain's run openers must open from the one small digest.

    A real session ran `resolve_config.py` with the full module dump and then
    re-ran it repeatedly, because a merged dump does not survive the transport
    (docs/research/B-001). The lint refuses both halves of that pattern: an
    activation without the digest, and an activation that still calls the dump.
    """
    mod = _load_lint()
    skill = tmp_path / "bmad-opener"
    skill.mkdir()
    monkeypatch.setattr(mod, "PLUGIN", tmp_path)
    monkeypatch.setattr(mod, "SKILLS", tmp_path)
    monkeypatch.setattr(mod, "DIGEST_FIRST", ("bmad-opener",))

    # 1) no digest in the activation → red
    (skill / "SKILL.md").write_text("## On Activation\n\n1. greet the user\n",
                                    encoding="utf-8")
    assert mod.check_digest_first() != 0
    assert "does not run the orientation digest" in capsys.readouterr().out

    # 2) digest present, but the activation still dumps a whole module: the
    # digest half is satisfied here and the dump half (its own check) goes red
    (skill / "SKILL.md").write_text(
        "## On Activation\n\n1. Run orient.py --project-root {project-root}\n"
        "2. resolve_config.py --project-root {project-root} --module bmm\n",
        encoding="utf-8")
    assert mod.check_digest_first() == 0
    assert mod.check_targeted_config_reads() != 0
    out = capsys.readouterr().out
    assert "activation starts from the orientation digest" in out
    assert "module dump" in out

    # 3) digest + targeted --key only → green
    (skill / "SKILL.md").write_text(
        "## On Activation\n\n1. Run orient.py --project-root {project-root}\n"
        "2. resolve_config.py --project-root {project-root} --key core.project_name\n",
        encoding="utf-8")
    assert mod.check_digest_first() == 0


def test_digest_first_covers_the_chain_run_openers():
    """Wiring the openers one at a time leaves the next one to re-derive its own
    state — the real session opened three skills from the full dump, not one.
    So the contract is a set: pin its members, and run it against the real tree,
    so dropping an opener from DIGEST_FIRST (or unwiring its activation) goes red
    here instead of in the next long session.
    """
    mod = _load_lint()
    assert set(mod.DIGEST_FIRST) >= {
        # planning chain, discovery entry points and what they feed
        "bmad-prd", "bmad-ux", "bmad-spec", "bmad-architecture",
        "bmad-create-epics-and-stories", "bmad-brainstorming",
        "bmad-forge-idea", "bmad-prfaq", "bmad-product-brief",
        "bmad-domain-research", "bmad-technical-research",
        "bmad-market-research", "bmad-cis-design-thinking",
        "bmad-cis-innovation-strategy", "bmad-cis-problem-solving",
        "bmad-cis-storytelling",
        # every stage opener of E → IR → SP → S → dev → review → QR → PR
        "bmad-research-experiment", "bmad-check-implementation-readiness",
        "bmad-sprint-planning", "bmad-create-story", "bmad-dev-story",
        "bmad-quick-dev", "bmad-dev-auto", "bmad-agent-dev",
        "bmad-code-review", "bmad-quality-record", "bmad-production-readiness",
        # the governance openers that re-enter the chain
        "bmad-retrospective", "bmad-correct-course",
        # GDS sub-chain (its own module config, the same chain shape)
        "gds-domain-research", "gds-brainstorm-game", "gds-create-game-brief",
        "gds-create-narrative", "gds-gdd", "gds-prd", "gds-ux",
        "gds-game-architecture", "gds-create-epics-and-stories",
        "gds-check-implementation-readiness", "gds-sprint-planning",
        "gds-create-story", "gds-dev-story", "gds-quick-dev",
        "gds-code-review", "gds-retrospective", "gds-correct-course",
        # WDS sub-chain (nine phase openers; thin shells whose run's step 1 is
        # mirrored in each phase's workflow.md)
        "wds-0-project-setup", "wds-0-alignment-signoff",
        "wds-1-project-brief", "wds-2-trigger-mapping", "wds-3-scenarios",
        "wds-4-ux-design", "wds-5-agentic-development",
        "wds-6-asset-generation", "wds-7-design-system",
        "wds-8-product-evolution",
        # front doors: personas/routers, the contributor and the reporter whose
        # read side is a promise (their grounding IS the digest)
        "bmad-agent-analyst", "bmad-agent-architect", "bmad-agent-pm",
        "bmad-agent-tech-writer", "bmad-agent-ux-designer", "bmad-agent-builder",
        "bmad-tea", "bmad-party-mode", "bmad-sprint-status",
        "bmad-cis-agent-brainstorming-coach",
        "bmad-cis-agent-creative-problem-solver",
        "bmad-cis-agent-design-thinking-coach",
        "bmad-cis-agent-innovation-strategist",
        "bmad-cis-agent-presentation-master", "bmad-cis-agent-storyteller",
        "gds-agent-game-architect", "gds-agent-game-designer",
        "gds-agent-tech-writer", "gds-agent-game-dev",
        "gds-agent-game-solo-dev", "gds-sprint-status",
        "wds-agent-freya-ux", "wds-agent-saga-analyst",
        # a human-decision entry point: opens a review session, so it needs the
        # relay position and the waiting batons (one call, where it made three)
        "bmad-checkpoint-preview",
    }
    assert mod.check_digest_first() == 0
    assert mod.check_targeted_config_reads() == 0
    assert mod.check_no_module_dump_in_skills() == 0
    assert mod.check_bounded_board_reads() == 0


def test_front_door_grounding_is_the_digest(tmp_path, capsys, monkeypatch):
    """A board-grounded front door grounds in ONE call, and that call is the
    digest. Three shapes prove the rule: an activation without the digest goes
    red, the same activation with it goes green, and a surface that has no
    activation section at all (its grounding sits in a reference it loads on
    demand) may keep the legacy raw read.
    """
    mod = _load_lint()
    monkeypatch.setattr(mod, "PLUGIN", tmp_path)
    monkeypatch.setattr(mod, "SKILLS", tmp_path)
    monkeypatch.setattr(mod, "FORWARDERS", {})
    monkeypatch.setattr(mod, "DIGEST_FIRST", ())
    monkeypatch.setattr(mod, "EXPECTED_POSTS", {"bmad-door": []})
    monkeypatch.setattr(mod, "PEEK_EXEMPT", {"bmad-door": "router"})
    skill = tmp_path / "bmad-door"
    skill.mkdir()

    # 1) an activation that promises grounding but never reads anything → red
    (skill / "SKILL.md").write_text(
        "## On Activation\n\n1. greet the user\n", encoding="utf-8")
    assert mod.check() != 0
    assert "must ground in the board via one call" in capsys.readouterr().out

    # 2) the raw pair alone is no longer enough for an activation → still red
    (skill / "SKILL.md").write_text(
        "## On Activation\n\n1. blackboard.py read --context\n", encoding="utf-8")
    assert mod.check() != 0
    assert "run 'orient.py'" in capsys.readouterr().out

    # 3) the digest in the activation → green
    (skill / "SKILL.md").write_text(
        "## On Activation\n\n1. orient.py --project-root {project-root}\n",
        encoding="utf-8")
    assert mod.check() == 0
    assert "grounds in the board (read-only, via 'orient.py')" in capsys.readouterr().out

    # 4) no activation section: the legacy raw read still counts
    (skill / "SKILL.md").write_text("blackboard.py read --context\n",
                                    encoding="utf-8")
    assert mod.check() == 0
    assert "no activation section" in capsys.readouterr().out

    # 5) a guest contributor grounds with ONE bounded read — and the lowercase
    #    header counts as an activation (it used to hide the whole section)
    monkeypatch.setattr(mod, "PEEK_EXEMPT", {"bmad-door": "contributor"})
    (skill / "SKILL.md").write_text(
        "## On activation\n\n1. blackboard.py read --context\n", encoding="utf-8")
    assert mod.check() == 0
    assert "one bounded read" in capsys.readouterr().out

    # 6) …but the same shell that reads nothing is still a broken promise
    (skill / "SKILL.md").write_text("## On activation\n\n1. greet\n",
                                    encoding="utf-8")
    assert mod.check() != 0
    assert "must ground in the board via one call" in capsys.readouterr().out


def test_config_dumps_are_flagged_everywhere(tmp_path, capsys, monkeypatch):
    """The digest-first set is the openers' rule; the targeted read is every
    skill's.

    The measured difference is the reason: the same script with neither flag
    prints every key of every layer (240 lines / 13.7 KB here) — the shape that
    truncated in a real session — and `--module bmm` (449 B) hands a surface
    keys it never reads. Both are refused now; the extractor is case-tolerant
    too, because `## On activation` (bmad-eval-runner's spelling until
    2026-09-23) hid a whole activation from this check.
    """
    mod = _load_lint()
    skill = tmp_path / "bmad-feeder"
    skill.mkdir()
    monkeypatch.setattr(mod, "SKILLS", tmp_path)
    header = "## On Activation\n\n"

    # 1) neither flag: every key of every layer
    (skill / "SKILL.md").write_text(
        header + "1. Resolve config: `python3 x/resolve_config.py "
        "--project-root {project-root}`\n", encoding="utf-8")
    assert mod.check_targeted_config_reads() != 0
    assert "unbounded dump" in capsys.readouterr().out

    # 2) a module dump is a dump too — it feeds keys this run never reads
    (skill / "SKILL.md").write_text(
        header + "1. Resolve config: `python3 x/resolve_config.py "
        "--project-root {project-root} --module bmm`\n", encoding="utf-8")
    assert mod.check_targeted_config_reads() != 0
    assert "module dump" in capsys.readouterr().out

    # 3) the lowercase header no longer hides the activation from the rule
    (skill / "SKILL.md").write_text(
        "## On activation\n\n1. Resolve config: `python3 x/resolve_config.py "
        "--project-root {project-root} --module tea`\n", encoding="utf-8")
    assert mod.check_targeted_config_reads() != 0
    capsys.readouterr()

    # 4) named keys → green
    (skill / "SKILL.md").write_text(
        header + "1. Resolve config: `python3 x/resolve_config.py "
        "--project-root {project-root} --key core.project_name`\n",
        encoding="utf-8")
    assert mod.check_targeted_config_reads() == 0


def test_module_dump_is_banned_across_the_skill_package(tmp_path, capsys,
                                                        monkeypatch):
    """A dump in a step file or a template reaches a run just as well — and a
    template writes it into every skill generated afterwards. 25 such lines sat
    outside the activation bodies until 2026-09-23 (TEA workflow YAMLs, the WDS
    phase steps and XML manifest, the builder templates)."""
    mod = _load_lint()
    skill = tmp_path / "bmad-tool"
    (skill / "steps-c").mkdir(parents=True)
    monkeypatch.setattr(mod, "PLUGIN", tmp_path)
    monkeypatch.setattr(mod, "SKILLS", tmp_path)
    (skill / "SKILL.md").write_text("# tool\n", encoding="utf-8")
    (skill / "steps-c" / "step-01.md").write_text(
        "Resolve: `python3 x/resolve_config.py --project-root . --module tea`\n",
        encoding="utf-8")
    assert mod.check_no_module_dump_in_skills() != 0
    assert "module dump" in capsys.readouterr().out

    # a long flag that merely starts with `--module` is not a dump
    (skill / "steps-c" / "step-01.md").write_text(
        "python3 x/scaffold.py --module-yaml a --module-csv b\n", encoding="utf-8")
    assert mod.check_no_module_dump_in_skills() == 0


def test_board_reads_are_bounded_to_one_call(tmp_path, capsys, monkeypatch):
    """The two-call board read is exactly what the digest replaced: no
    activation may spell the pair again, and a digest-first surface may not
    re-read the context its step 1 already carries. Two shapes stay legal — the
    bare baton listing as a front door's fallback, and a guest's single bounded
    `read --context` — and the targeted `handoffs --skill` peek is never confused
    with the raw listing."""
    mod = _load_lint()
    skill = tmp_path / "bmad-door"
    skill.mkdir()
    monkeypatch.setattr(mod, "SKILLS", tmp_path)
    monkeypatch.setattr(mod, "DIGEST_FIRST", ("bmad-door",))

    # 1) the raw pair → red, even with the digest in place
    (skill / "SKILL.md").write_text(
        "## On Activation\n\n1. orient.py --project-root {project-root}\n"
        "2. blackboard.py read --context --project-root {project-root}\n"
        "3. blackboard.py handoffs --project-root {project-root}\n",
        encoding="utf-8")
    assert mod.check_bounded_board_reads() != 0
    assert "raw two-call board read" in capsys.readouterr().out

    # 2) digest-first + a context re-read → red (step 1 carried that read)
    (skill / "SKILL.md").write_text(
        "## On Activation\n\n1. orient.py --project-root {project-root}\n"
        "2. blackboard.py read --context --project-root {project-root}\n",
        encoding="utf-8")
    assert mod.check_bounded_board_reads() != 0
    assert "still spells" in capsys.readouterr().out

    # 3) the baton listing alone is the sanctioned fallback → green, and the
    #    targeted peek is not mistaken for it
    (skill / "SKILL.md").write_text(
        "## On Activation\n\n1. orient.py --project-root {project-root}\n"
        "2. blackboard.py handoffs --project-root {project-root}\n"
        "3. blackboard.py handoffs --skill bmad-door --project-root {project-root}\n",
        encoding="utf-8")
    assert mod.check_bounded_board_reads() == 0

    # 4) a guest keeps its one bounded context read → green
    monkeypatch.setattr(mod, "DIGEST_FIRST", ())
    (skill / "SKILL.md").write_text(
        "## On Activation\n\n1. blackboard.py read --context --project-root "
        "{project-root}\n", encoding="utf-8")
    assert mod.check_bounded_board_reads() == 0


def test_router_must_ground_in_the_board(tmp_path, capsys, monkeypatch):
    """A persona's read side is mandatory: the reason string alone is a promise
    the lint refuses to take on faith."""
    mod = _load_lint()
    skill = tmp_path / "bmad-persona"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# persona, no board read\n", encoding="utf-8")
    monkeypatch.setattr(mod, "PLUGIN", tmp_path)
    monkeypatch.setattr(mod, "SKILLS", tmp_path)
    monkeypatch.setattr(mod, "PEEK_EXEMPT", {"bmad-persona": "router"})
    monkeypatch.setattr(mod, "EXPECTED_POSTS", {"bmad-persona": []})
    assert mod.check() != 0
    assert "a router must ground in the board" in capsys.readouterr().out


def test_grounded_reason_alone_is_not_enough(tmp_path, capsys, monkeypatch):
    """Every exempt reason that promises a read side is verified the same way —
    a new one cannot be added as an unenforced label."""
    mod = _load_lint()
    # routers/reporters ground in the digest; guests may ground with the single
    # bounded read — either way the reason string alone is not enough
    reasons = mod.BOARD_GROUNDED_REASONS + mod.BOARD_GROUNDED_GUESTS
    for reason in reasons:
        skill_dir = tmp_path / f"bmad-{reason}-thing"
        skill_dir.mkdir(exist_ok=True)
        (skill_dir / "SKILL.md").write_text("# no board read\n", encoding="utf-8")
    monkeypatch.setattr(mod, "PLUGIN", tmp_path)
    monkeypatch.setattr(mod, "SKILLS", tmp_path)
    monkeypatch.setattr(mod, "FORWARDERS", {})  # keep the count about the read side
    monkeypatch.setattr(mod, "DIGEST_FIRST", ())  # synthetic tree holds no run openers
    monkeypatch.setattr(mod, "EXPECTED_POSTS",
                        {f"bmad-{r}-thing": [] for r in reasons})
    monkeypatch.setattr(mod, "PEEK_EXEMPT",
                        {f"bmad-{r}-thing": r for r in reasons})
    assert mod.check() != 0
    out = capsys.readouterr().out
    assert f"handoff wiring: {len(reasons)} problem(s)" in out
    for reason in reasons:
        assert f"a {reason} must ground in the board" in out


def test_entry_point_reasons_that_review_humans_must_ground(tmp_path, capsys, monkeypatch):
    """The human-review entry points (checkpoint-preview, loop-resolve) promise
    a board read — a verdict made without chain context is a vacuum verdict."""
    mod = _load_lint()
    routes = (mod.BOARD_GROUNDED_MARKER,) + tuple(mod.BOARD_GROUNDED_LEGACY)
    for skill in ("bmad-checkpoint-preview", "bmad-loop-resolve"):
        assert mod.PEEK_EXEMPT.get(skill) == "entry point", skill
        text = (mod.SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
        # Not run openers (they review, they don't start runs), so either route
        # counts: the digest once wired, the raw read until then.
        assert any(m in text for m in routes), skill


def test_gds_wds_verticals_are_lint_members(tmp_path, capsys, monkeypatch):
    """The verticals are first-class chain members: every producing GDS/WDS
    skill is lint-checked, advisors/personas are registered with a reason, and
    the producing dev personas converge on the review gate like bmad-agent-dev."""
    mod = _load_lint()
    # producers post their hop; QA feeders and dev branches converge on the review
    for skill, target in (
        ("gds-dev-story", "gds-code-review"),
        ("gds-agent-game-dev", "gds-code-review"),
        ("gds-agent-game-solo-dev", "gds-code-review"),
        ("gds-test-design", "gds-code-review"),
        # the playtest plan's BRIDGE feeds the same QR: it is a feeder, not a
        # chain-adjacent document tool
        ("gds-playtest-plan", "gds-code-review"),
        ("wds-4-ux-design", "wds-5-agentic-development"),
        ("wds-8-product-evolution", "wds-1-project-brief"),
    ):
        assert mod.EXPECTED_POSTS[skill] == [target], skill
    # registered, relay-free surfaces with a reason
    assert mod.PEEK_EXEMPT["gds-playtest-plan"] == "feeder"
    assert mod.PEEK_EXEMPT["gds-sprint-status"] == "reporter"
    assert mod.PEEK_EXEMPT["wds-agent-freya-ux"] == "router"
    # the GDS advisor personas ship the same persona template as the WDS ones
    # (identity + menu + Step 8 dispatch), so they route too — and a router
    # promises the read side, which the lint enforces
    for skill in ("gds-agent-game-architect", "gds-agent-game-designer",
                  "gds-agent-tech-writer"):
        assert mod.PEEK_EXEMPT[skill] == "router", skill
    routes = (mod.BOARD_GROUNDED_MARKER,) + tuple(mod.BOARD_GROUNDED_LEGACY)
    for skill in ("gds-agent-game-architect", "gds-agent-game-designer",
                  "gds-agent-tech-writer", "wds-agent-freya-ux",
                  "wds-agent-mimir-builder", "wds-agent-saga-analyst"):
        text = (mod.SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
        # The digest where the router has an activation (all but mimir, a shell
        # whose grounding it loads on demand) — either route reads the board.
        assert any(m in text for m in routes), skill
    # gds-code-review receives the fan-in: it must peek its own channel
    text = (mod.SKILLS / "gds-code-review" / "SKILL.md").read_text(encoding="utf-8")
    assert "handoffs --skill gds-code-review" in text


def test_verticals_terminal_phase_is_registered_and_claimable():
    """A hop receiver must be a registered member whose peek exists: the WDS
    pipeline's last phase receives the asset-generation baton, so it cannot be
    an unregistered terminal (a baton nobody could claim)."""
    mod = _load_lint()
    assert mod.EXPECTED_POSTS["wds-7-design-system"] == []
    assert "wds-7-design-system" not in mod.PEEK_EXEMPT  # receivers peek
    text = (mod.SKILLS / "wds-7-design-system" / "SKILL.md").read_text(encoding="utf-8")
    assert "handoffs --skill wds-7-design-system" in text
    # the loop's return edge (retro → experiment) is claimable too
    assert "bmad-research-experiment" not in mod.PEEK_EXEMPT
    exp = (mod.SKILLS / "bmad-research-experiment" / "SKILL.md").read_text(encoding="utf-8")
    assert "handoffs --skill bmad-research-experiment" in exp


def test_discovery_entry_points_promise_no_unclaimable_peek():
    """Ideation and research start from user intent and no hop targets them, so
    they are entry points — like their CIS siblings. The dead peek is gone
    (an instruction nothing could ever satisfy)."""
    mod = _load_lint()
    for skill in ("bmad-brainstorming", "bmad-forge-idea", "bmad-prfaq",
                  "bmad-market-research", "bmad-domain-research",
                  "bmad-technical-research"):
        assert mod.PEEK_EXEMPT.get(skill) == "entry point", skill
        text = (mod.SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
        assert f"handoffs --skill {skill}" not in text, skill


def test_receiver_may_not_claim_nothing_hands_off_to_it(tmp_path, capsys, monkeypatch):
    """The baton-rot bug, locked: a hop receiver marked 'entry point' would
    never look at its channel, so the declared signal waits until it goes
    stale. check_receivers() must catch it (and an unregistered receiver)."""
    mod = _load_lint()
    monkeypatch.setattr(mod, "EXPECTED_POSTS", {
        "bmad-upstream": ["bmad-downstream", "bmad-ghost"],
        "bmad-downstream": [],
    })
    monkeypatch.setattr(mod, "PEEK_EXEMPT", {"bmad-downstream": "entry point"})
    monkeypatch.setattr(mod, "SKILLS", tmp_path)
    assert mod.check_receivers() == 2
    out = capsys.readouterr().out
    assert "declared 'entry point' but ['bmad-upstream'] hand off to it" in out
    assert "not a registered chain member" in out


def test_shared_run_key_namespace_must_be_stamped(tmp_path, capsys, monkeypatch):
    """Two stages write `story.`; the prefix credits the first, so the other
    must stamp `--sender`. Unstamped, the diagnostic silently mis-credits it."""
    mod = _load_lint()
    skill = tmp_path / "bmad-dev-story"
    skill.mkdir()
    (skill / "SKILL.md").write_text("mirror --key story.x --to bmad-code-review\n",
                                    encoding="utf-8")
    monkeypatch.setattr(mod, "PLUGIN", tmp_path)
    monkeypatch.setattr(mod, "SKILLS", tmp_path)
    assert mod.check_sender_attribution() == 1
    assert "without '--sender bmad-dev-story'" in capsys.readouterr().out
    (skill / "SKILL.md").write_text(
        "mirror --key story.x --to bmad-code-review --sender bmad-dev-story\n",
        encoding="utf-8")
    assert mod.check_sender_attribution() == 0
    assert "stamps --sender" in capsys.readouterr().out


def test_persona_menus_dispatch_into_registered_skills():
    """A router's whole job is its menu, so every menu target must be a real,
    known skill — a typo, a deleted target or an unregistered surface would make
    the front door point at nothing, and no hop lint would notice it (a router
    posts nothing).

    Two legitimate persona shapes are allowed: a pure ROUTER (posts nothing),
    and a producing persona registered as an ENTRY POINT (it dispatches a menu
    AND signals its hop — gds-agent-game-dev / -solo-dev). The GDS advisor
    personas are locked in here because they were filed as tools while shipping
    exactly the router template.
    """
    import tomllib
    mod = _load_lint()
    routers = {}
    for cfg in sorted((mod.SKILLS).glob("*/customize.toml")):
        agent = (tomllib.loads(cfg.read_text(encoding="utf-8")).get("agent") or {})
        if agent.get("menu"):
            routers[cfg.parent.name] = agent["menu"]

    assert set(routers) >= {
        "gds-agent-game-architect", "gds-agent-game-designer", "gds-agent-tech-writer",
        "wds-agent-saga-analyst", "bmad-agent-pm", "bmad-cis-agent-storyteller",
        "bmad-tea",
    }
    for skill, menu in sorted(routers.items()):
        reason = mod.PEEK_EXEMPT.get(skill)
        assert reason in ("router", "entry point"), f"{skill}: {reason}"
        if reason == "entry point":
            assert mod.EXPECTED_POSTS[skill], f"{skill}: dispatching but posts nothing"
        for item in menu:
            target = item.get("skill")
            if not target:
                continue  # prompt-only item: no skill to resolve
            assert (mod.SKILLS / target / "SKILL.md").is_file(), \
                f"{skill} → {target} (not installed)"
            assert target in mod.EXPECTED_POSTS, f"{skill} → {target} (not registered)"
    # the repaired advisors: a dispatch menu AND the read-side promise
    for skill in ("gds-agent-game-architect", "gds-agent-game-designer",
                  "gds-agent-tech-writer"):
        assert mod.PEEK_EXEMPT[skill] == "router", skill
        text = (mod.SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
        assert mod.BOARD_GROUNDED_MARKER in text, skill


def test_forwarder_must_name_its_canonical_skill(tmp_path, capsys, monkeypatch):
    # check_forwarders() is exercised directly here, so the real FORWARDERS table
    # is replaced wholesale rather than the tree being made to grow shims.
    """A shim that forwards nowhere is a dead end: DEPRECATED alone is not a
    forwarding contract."""
    mod = _load_lint()
    shim = tmp_path / "bmad-old-thing"
    shim.mkdir()
    (shim / "SKILL.md").write_text("DEPRECATED — forwards somewhere\n",
                                   encoding="utf-8")
    monkeypatch.setattr(mod, "SKILLS", tmp_path)
    monkeypatch.setattr(mod, "FORWARDERS", {"bmad-old-thing": "bmad-thing"})
    assert mod.check_forwarders() == 1
    assert "never names its canonical skill bmad-thing" in capsys.readouterr().out

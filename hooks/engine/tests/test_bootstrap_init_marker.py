"""Contract test for hooks/scripts/bootstrap.sh — init marker behavior.

Init is one-time: `/metodoloji:init` writes `.metodoloji/initialized`, and the
SessionStart bootstrap reads it so every downstream skill stops being told to
re-run init. The regression this pins down: bootstrap re-ran the skeleton step
and re-advertised `/metodoloji:init` on every session, so each skill in the
chain replayed the whole init flow.

The tests run the real bootstrap.sh in a throwaway project with a throwaway
HOME (bootstrap creates the gate-key there), so no host state is touched.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

# tests → engine → hooks → plugin root
PLUGIN = Path(__file__).resolve().parents[3]
BOOTSTRAP = PLUGIN / "hooks" / "scripts" / "bootstrap.sh"


def _shell() -> tuple[str, str]:
    """Return (shell binary, flavor); flavor is "msys" or "posix".

    Prefer Git-for-Windows bash: it executes Windows checkouts in place.
    A bare PATH sh/bash is used only when it is NOT the WSL launcher —
    WSL bash cannot execute a Windows-path script (F5/T-08), so that
    configuration skips instead of failing on path translation.
    """
    for cand in (r"C:\Program Files\Git\bin\bash.exe",
                 r"C:\Program Files (x86)\Git\bin\bash.exe"):
        if os.path.isfile(cand):
            return cand, "msys"
    found = shutil.which("sh") or shutil.which("bash")
    if not found:
        pytest.skip("no POSIX shell (sh/bash) available")
    if "system32" in found.replace("/", os.sep).lower():
        pytest.skip("only WSL bash found — cannot run Windows-path scripts (F5/T-08)")
    return found, "posix"


def _posix(p: Path, flavor: str) -> str:
    """Render a Windows path for the chosen shell (MSYS needs /c/x form)."""
    s = str(p)
    if flavor != "msys":
        return s
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", s)
    if m:
        return "/" + m.group(1).lower() + "/" + m.group(2).replace("\\", "/")
    return s.replace("\\", "/")


def _run_bootstrap(project: Path, home: Path) -> dict:
    """Run bootstrap.sh against *project* with *home* and return its JSON context."""
    project.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    shell, flavor = _shell()
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = _posix(project, flavor)
    env["HOME"] = _posix(home, flavor)  # isolate gate-key creation from the real HOME
    env.pop("OPENHANDS_PROJECT_DIR", None)
    r = subprocess.run(
        [shell, _posix(BOOTSTRAP, flavor)],
        # DEVNULL stdin: under pytest's fd capture the inherited stdin handle is
        # invalid on Windows, and subprocess.run would fail duplicating it
        # (WinError 6) unless stdin is a real pipe/devnull. bootstrap reads none.
        stdin=subprocess.DEVNULL,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60, env=env,
    )
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    assert lines, f"bootstrap produced no output (stderr: {r.stderr[-400:]!r})"
    return json.loads(lines[-1])


def _ctx(out: dict) -> str:
    return out.get("additionalContext", "")


def test_bootstrap_without_marker_advertises_one_time_init(tmp_path):
    out = _run_bootstrap(tmp_path / "proj", tmp_path / "home")
    ctx = _ctx(out)
    assert "run /metodoloji:init once" in ctx
    # …and the hint is executable: only Claude Code exposes the slash command,
    # so the real OpenHands session read this line for the whole planning chain
    # and never ran init. The script path must ride along.
    assert "bmad/scripts/skeleton.py\" --install" in ctx
    assert "already installed" not in ctx
    # The old always-on hint is gone for good.
    assert "Record templates: /metodoloji:init" not in ctx
    # Fresh project: the base skeleton dir is created for the upcoming init.
    assert (tmp_path / "proj" / "docs" / "experiments").is_dir()


def test_bootstrap_mentions_the_record_chain_exactly_once(tmp_path):
    """The engine's session_start sentence carries the chain itself; bootstrap
    prefixes its own. The real session's context line said
    "…Record chain: E → IR → SP → S → QR → PR. … METODOLOJI session started.
    Record chain: E → IR → SP → S → QR → PR. …" — the same sentence twice."""
    ctx = _ctx(_run_bootstrap(tmp_path / "proj", tmp_path / "home"))
    assert ctx.count("Record chain: E → IR → SP → S → QR → PR") == 1


def test_bootstrap_names_the_plugin_root(tmp_path):
    """Both roots must be resolvable from the context alone.

    The real session burned turns on `find / -name research-methodology.md`
    and on guessing between ~/.metodoloji, /root/bmad and the plugin dir while
    the root was already printed on this line. The project root is named too:
    the fixed sentence claims roots are "named in this context", so a literal
    `{project-root}` placeholder with no concrete value was a broken promise.
    """
    out = _run_bootstrap(tmp_path / "proj", tmp_path / "home")
    ctx = _ctx(out)
    assert "{metodoloji-root}" in ctx
    assert "{project-root}" in ctx
    assert "never search the filesystem" in ctx
    # Concrete values, not just the placeholder tokens.
    assert "{metodoloji-root} = " in ctx
    assert "{project-root} = " in ctx


def test_bootstrap_context_is_platform_neutral(tmp_path):
    r"""The win32 session copied a `C:\...` root into a POSIX shell and every
    probe collapsed (`ls C:\Users\me\proj` read as `ls C:Usersmeproj`). The
    context must (a) inject roots in forward-slash form, (b) name the forward-
    slash rule and the two shell dialects, and (c) never prime the agent with a
    namespaced-tool token such as `default.X` (the failure it was meant to
    prevent)."""
    ctx = _ctx(_run_bootstrap(tmp_path / "proj", tmp_path / "home"))
    assert "forward slashes" in ctx
    assert "PowerShell" in ctx
    assert "default." not in ctx
    # The tool contract must SURVIVE the engine-sentence dedupe: the old
    # `ctx = head + tail` dropped it on every session (this phrase is
    # bootstrap-only, so it proves the `tools` clause was kept).
    assert "the exact bare name your harness lists" in ctx
    # Both concrete root values are injected in forward-slash form.
    root_m = re.search(r"\{metodoloji-root\} = (\S+)", ctx)
    proj_m = re.search(r"\{project-root\} = (\S+)", ctx)
    assert root_m and "\\" not in root_m.group(1)
    assert proj_m and "\\" not in proj_m.group(1)


def test_bootstrap_with_marker_does_not_readvertise_init(tmp_path):
    project = tmp_path / "proj"
    marker = project / ".metodoloji" / "initialized"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("initialized_at: 2026-01-01T00:00:00Z\nplugin_version: 1.0.0\n",
                      encoding="utf-8")

    out = _run_bootstrap(project, tmp_path / "home")
    ctx = _ctx(out)
    assert "already installed" in ctx
    assert "do NOT re-run /metodoloji:init" in ctx
    assert "run /metodoloji:init once" not in ctx
    # Marker present → the skeleton step is skipped entirely.
    assert not (project / "docs" / "experiments").exists()


def test_bootstrap_skeleton_without_marker_names_the_drift(tmp_path):
    """Skeleton present + marker absent = BROKEN init, not a missing one.

    The mailjs OpenHands session (2026-09-23) had every template in place but
    no marker, so every session read "Record skeleton not installed" and
    re-advertised a full init although only the one-byte marker was missing.
    The hint must name the drift and point at the idempotent repair.
    """
    project = tmp_path / "proj"
    tpl = project / "docs" / "development"
    tpl.mkdir(parents=True, exist_ok=True)
    (tpl / "_template_IR.md").write_text("x", encoding="utf-8")

    out = _run_bootstrap(project, tmp_path / "home")
    ctx = _ctx(out)
    # The lie is gone…
    assert "Record skeleton not installed" not in ctx
    # …and the truth names both the problem and the repair.
    assert "marker is MISSING" in ctx
    assert "broken init" in ctx
    assert 'bmad/scripts/skeleton.py" --install' in ctx
    assert "idempotent" in ctx
    # A repair hint must never run init itself: bootstrap still writes nothing.
    assert not (project / ".metodoloji" / "initialized").exists()


def test_bootstrap_never_writes_the_marker(tmp_path):
    """Only /metodoloji:init writes the marker — bootstrap must not."""
    project = tmp_path / "proj"
    _run_bootstrap(project, tmp_path / "home")
    assert not (project / ".metodoloji" / "initialized").exists()


def test_bootstrap_always_ensures_runtime_log_dir(tmp_path):
    """`.metodoloji/logs/` is runtime state (audit trail), not init state."""
    for with_marker in (False, True):
        project = tmp_path / ("proj-marker" if with_marker else "proj-fresh")
        if with_marker:
            marker = project / ".metodoloji" / "initialized"
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("initialized_at: 2026-01-01T00:00:00Z\n", encoding="utf-8")
        _run_bootstrap(project, tmp_path / ("home-m" if with_marker else "home-f"))
        assert (project / ".metodoloji" / "logs").is_dir()

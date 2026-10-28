# Contributing to Metodoloji

Thank you for your interest in contributing to **Metodoloji**! We welcome contributions across our 129 skills, hook engine, workflow kernel, and documentation.

---

## 🛠️ Development Setup & Quality Checks

1. **Clone the Repository:**
   ```bash
   git clone https://github.com/metodoloji/metodoloji.git
   cd metodoloji
   ```

2. **Python Environment:**
   Python 3.11+ is required (uses standard library `tomllib`).
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   pip install pytest
   ```

3. **Run the Full Test Suite locally:**
   All 1,400+ unit and integration tests must pass:
   ```bash
   pytest
   ```

4. **Run Plugin Health Checks & Negative Tests:**
   ```bash
   # Verify gate key, engine drift, bridge runtime visibility, and tech debt:
   sh scripts/check-plugin.sh

   # Run automated negative tests (ensures detection catches missing markers):
   sh scripts/check-plugin.sh --negtest
   ```

Pull requests also run `.github/workflows/quality.yml`: the Python suite and
plugin/record audits run on Python 3.11 and 3.12, and the OpenCode bridge suite
runs separately on Node.js 22. The release-number workflow remains independent
and only verifies/publishes version tags.

---

## 📐 Contribution Guidelines

### Adding or Updating Skills
- Skills live under `skills/<skill-name>/` with a valid `SKILL.md`.
- Customization and BRIDGE instructions live under `custom/<skill-name>.toml`.
- If a skill produces or feeds into methodology records (IR/SP/S/QR/PR), define the appropriate `BRIDGE` and `VERIFY` steps.

### Hook Engine Modifications
- The hook engine lives in `hooks/engine/` (`main.py` and `modules/`).
- Gate orchestration is separated by decision path: `modules/guard.py` keeps
  the stable hook surface, `modules/quality_gate.py` handles commit checks,
  `modules/deploy_gate.py` handles deployment checks, and
  `modules/record_chain_gate.py` owns the shared IR/QR/SP/PR chain;
  `modules/scope_gate.py` handles approval scope, staged instruments, and
  structural declarations; `modules/story_quality.py` owns story and record
  chain validation, while `modules/story_records.py` holds the shared parsers.
- Blackboard canvases, handoffs, chain health, diagnostics, and event-log
  rotation live in `modules/blackboard_canvas.py`,
  `modules/blackboard_handoff.py`, `modules/blackboard_chain.py`,
  `modules/blackboard_diagnostics.py`, and `modules/blackboard_archive.py`;
  `blackboard.py` remains the stable API and event/state core. Keep host
  payload normalization and shared verification helpers behind these surfaces;
  do not duplicate gate decisions in runtime adapters.
- Must support four-runtime execution (**Claude Code**, **OpenHands**, **OpenCode** and **Hermes**). Adapters share the methodology decision engine, while their interception coverage remains runtime-specific; document any coverage changes in `docs/runtime-security-coverage.md`.
- Fail-closed dispatch for `pre`/`guard` (a missing engine denies, exit 2); fail-open for `audit` and dispatcher root discovery (`run-hook.sh`). `stop` is report-only and never blocks; an unavailable report is surfaced as context. Inside the engine, gate strictness is per-config-key (`code_guard`/`quality_gate`/`deploy_guard`).
- Every engine change must include unit tests in `hooks/engine/tests/`.

---

## 🚀 Pull Request Process

1. Fork the repository and create your branch from `master`.
2. Ensure all tests pass (`pytest` and `check-plugin.sh --negtest`).
3. Commit with conventional commit messages (e.g. `feat(skill): ...`, `fix(hooks): ...`).
4. Open a pull request against `master`.


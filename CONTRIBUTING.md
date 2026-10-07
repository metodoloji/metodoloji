# Contributing to Metodoloji

Thank you for your interest in contributing to **Metodoloji**! We welcome contributions across our 125 BMAD skills, hook engine, workflow kernel, and documentation.

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

3. **Run the Full Test Suite:**
   All 700+ unit and integration tests must pass:
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

---

## 📐 Contribution Guidelines

### Adding or Updating Skills
- Skills live under `skills/<skill-name>/` with a valid `SKILL.md`.
- Customization and BRIDGE instructions live under `custom/<skill-name>.toml`.
- If a skill produces or feeds into methodology records (IR/SP/S/QR/PR), define the appropriate `BRIDGE` and `VERIFY` steps.

### Hook Engine Modifications
- The hook engine lives in `hooks/engine/` (`main.py` and `modules/`).
- Must support dual-runtime execution (**OpenHands** and **Claude Code**).
- Fail-closed dispatch for `pre`/`guard` (a missing engine denies, exit 2); fail-open for `audit` and dispatcher root discovery (`run-hook.sh`). `stop` is report-only and never blocks (loop-safe envelope: `block` + exit 0). Inside the engine, gate strictness is per-config-key (`code_guard`/`quality_gate`/`deploy_guard`).
- Every engine change must include unit tests in `hooks/engine/tests/`.

---

## 🚀 Pull Request Process

1. Fork the repository and create your branch from `master`.
2. Ensure all tests pass (`pytest` and `check-plugin.sh --negtest`).
3. Commit with conventional commit messages (e.g. `feat(skill): ...`, `fix(hooks): ...`).
4. Open a pull request against `master`.


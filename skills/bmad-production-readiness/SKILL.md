---
name: bmad-production-readiness
description: 'Generate Production Readiness (PR) record from approved quality records. Use when the user says "prepare for production" or "generate PR for release"'
triggers: ["bmad-production-readiness", "/bmad-production-readiness", "production-readiness", "prepare for production", "generate PR"]
---

# Production Readiness Generation Workflow

**Goal:** Generate a Production Readiness (PR) record from approved quality records (QR), verifying staging deployment, rollback plan, monitoring, feature flags, runbook, and operational readiness. PR is Gate 4 in the methodology chain.

**Your Role:** You are a DevOps/Release Engineer generating production readiness records. Parse approved QRs, verify staging tests, define rollback plans, set up monitoring and alerts, and produce a complete PR record to gate production deployment.

## Conventions

- Bare paths (e.g. `checklist.md`) resolve from the skill root.
- `{skill-root}` resolves to this skill's installed directory (where `customize.toml` lives).
- `{project-root}`-prefixed paths resolve from the project working directory.
- `{skill-name}` resolves to the skill directory's basename.

## On Activation

### Step 1: Run the Orientation Digest

Run: `python3 {metodoloji-root}/bmad/scripts/orient.py --project-root {project-root}` — one read-only call, before anything else. It carries both roots, the core config this run reads (`{user_name}`, `{communication_language}`, `{document_output_language}`, `{project_name}`, `{date}` as `today`), every resolved output path, the record inventory, the board's live focus (with a `STALE` flag when a hot run still claims `complete`) and **waiting hand-offs addressed to you** (the digest names the sender and the peek command), and skeleton/gate state. Read it once; never re-run it "for clean output" — it is small by construction. On failure, fall through to the config step's neutral defaults.

### Step 2: Resolve the Workflow Block

Run: `python3 {metodoloji-root}/hooks/engine/resolve_customization.py --skill {skill-root} --key workflow` — use your harness-native shell tool with the command as given (no extra wrapper params)

**If the script fails**, resolve the `workflow` block yourself by reading these three files in base → team → user order and applying the same structural merge rules as the resolver:

1. `{skill-root}/customize.toml` — defaults
2. `{metodoloji-root}/custom/{skill-name}.toml` — team overrides
3. `{metodoloji-root}/custom/{skill-name}.user.toml` — personal overrides

Any missing file is skipped. Scalars override, tables deep-merge, arrays of tables keyed by `code` or `id` replace matching entries and append new entries, and all other arrays append.

### Step 3: Execute Prepend Steps

Execute each entry in `{workflow.activation_steps_prepend}` in order before proceeding.

### Step 4: Load Persistent Facts

Treat every entry in `{workflow.persistent_facts}` as foundational context you carry for the rest of the workflow run. Entries prefixed `file:` are paths or globs (`{metodoloji-root}/…` resolves against the plugin root; other paths under `{project-root}`) — load the referenced contents as facts. All other entries are facts verbatim.

### Step 5: Load Config

**Config comes from the digest** (Step 1). Only a key it genuinely lacks needs a targeted read: `python3 {metodoloji-root}/bmad/scripts/resolve_config.py --project-root {project-root} --key <dotted.path>` — the targeted shape survives the transport; **never the full merged dump**. Resolve:

- `project_name`, `user_name`
- `communication_language`, `document_output_language`
- `implementation_artifacts`
- `planning_artifacts`
- `quality_artifacts` (where QR records live)
- `deployment_artifacts` (where PR records live, typically `docs/development`)
- `date` as system-generated current datetime
- `project_context` = `**/project-context.md` (load if exists)
- YOU MUST ALWAYS SPEAK OUTPUT in your Agent communication style with the config `{communication_language}`
- Generate all documents in `{document_output_language}`

### Step 6: Greet the User

Greet `{user_name}`, speaking in `{communication_language}`. Ask about release scope: which QR records (approved quality records) are going into this production release?

### Step 7: Execute Append Steps

Execute each entry in `{workflow.activation_steps_append}` in order.

### Step 8: Chain Handshake

Check for signals addressed to you before the workflow begins: `python3 {metodoloji-root}/bmad/scripts/blackboard.py handoffs --skill bmad-production-readiness --project-root {project-root}` — the canonical sender is a `bmad-quality-record` run whose note names the approved QR records to pull from. Read the named QR records first (step 1 re-verifies each decision is APPROVED), then complete the handshake: `python3 {metodoloji-root}/bmad/scripts/blackboard.py consume --channel handoff.bmad-production-readiness --project-root {project-root}` (consume only after the named QR records are located — an unconsumed signal keeps the hand-off waiting, which is correct when the user routes elsewhere).

Activation is complete. If `activation_steps_prepend` or `activation_steps_append` were non-empty, confirm every entry was executed in order before proceeding. Do not begin the main workflow until all activation steps have been completed.

## Paths

- `tracking_system` = `file-system`
- `project_key` = `NOKEY`
- `quality_location` = `{quality_artifacts}`
- `deployment_location` = `{deployment_artifacts}`
- `pr_pattern` = `PR-*.md`

## Input Files

| Input | Path | Load Strategy |
|-------|------|---------------|
| Approved QRs | `{quality_location}/QR-*.md` (identified by user) | FULL_LOAD |
| Release notes | User or auto-generated from QRs | PROVIDED_BY_USER |
| Staging access | Environment details from user | PROVIDED_BY_USER |
| Monitoring setup | Dashboards and alert definitions | PROVIDED_BY_USER |

## Execution

<workflow>

<step n="1" goal="Identify release scope and gather approved QRs">
<action>Ask user for:</action>
  - Release type (Major / Minor / Patch / Hotfix)
  - Version number (e.g., v1.2.3)
  - Release name or codename
  - Which QR records are going into this release (QR-001, QR-002, ...)

<action>For each QR identified:</action>
  - Load `{quality_location}/QR-{id}.md`
  - Extract: story reference (S-XXX), decision status (must be APPROVED)
  - Verify decision = APPROVED (if not, exclude from release scope)
  - Extract: AC verification status, DoD verification status, code review approval
  - Extract: any breaking changes or tech debt

<action>Compile release scope:</action>

```
Release: v1.2.3 (Minor Release)
Approved QRs in scope:
  - QR-001: S-001 — User authentication (APPROVED)
  - QR-002: S-002 — Account management (APPROVED)
  - QR-003: S-003 — Settings page (APPROVED)
Total: 3 QRs, 3 stories
```

<action>Check for issues:</action>
  - [ ] Any QR not APPROVED? → Exclude from release
  - [ ] Any breaking changes? → Plan migration
  - [ ] Any tech debt? → Document and plan remediation
</step>

<step n="2" goal="Gather staging environment and deployment details">
<action>Ask user for:</action>
  - Staging environment URL or name
  - CI/CD pipeline name or deployment method (e.g., GitHub Actions, Jenkins, manual script)
  - Estimated deployment duration (e.g., 5 minutes)
  - Who will perform the deployment (user role/name)
  - Any pre-deployment steps (database backup, config update, etc.)

<action>Ask about release timeline:</action>
  - Planned deploy date/time (UTC)
  - Deploy window duration (typically 1-2 hours for monitoring)
  - Any blackout periods or freeze times (e.g., no deploys on Friday evening)

<action>Record deployment plan:</action>

```
Deployment Method: GitHub Actions (push to main branch)
Staging Deployment: https://staging.myapp.com
Pre-deploy: database backup script (backup.sh)
Estimated duration: 10 minutes
Planned deploy time: 2025-02-20 14:00 UTC
Deploy window: 2 hours (includes 1 hour post-deploy monitoring)
```
</step>

<step n="3" goal="Plan and document staging tests">
<action>Define smoke tests (quick critical path tests):</action>
  - For each story in QR scope, identify 1-2 critical user flows
  - Example: "User can log in and create a post" (tests auth + API)
  - Example: "Settings change persists" (tests persistence)

<action>Ask user to provide:</action>
  - Manual test scenarios (if any) or accept auto-generated list
  - E2E test commands (if automated tests exist)
  - Critical path definition

<action>Create smoke test plan:</action>

```
Smoke Tests:
1. User authentication:
   - Test: POST /api/login with valid credentials
   - Expect: 200 OK, JWT token returned
   - Method: curl or pytest

2. Account settings:
   - Test: PATCH /api/user/settings (change timezone)
   - Expect: 200 OK, timezone updated in response
   - Method: curl or pytest

3. UI smoke test (if web frontend):
   - Test: Load homepage, login, navigate to dashboard
   - Expect: all pages load without errors
   - Method: manual or Selenium

End-to-end integration test:
  - Full user workflow: signup → verify email → create post → logout
  - Method: E2E test suite (e.g., Cypress, Playwright)
```

<action>Record planned smoke test status as "⏳ planned" initially</action>
</step>

<step n="4" goal="Plan rollback strategy">
<action>Ask user or determine:</action>
  - [ ] Is this a backward-compatible release?
  - [ ] Does this require database migrations?
  - [ ] Are any APIs being deprecated or changed?
  - [ ] Are there feature flags that can be toggled off?

<action>Choose rollback method based on answers:</action>

| Scenario | Rollback Method |
|----------|-----------------|
| Backward-compatible + no DB change | Rolling (instant per-instance) |
| Backward-compatible + reversible DB | Blue-green (toggle traffic) |
| Breaking change + DB destructive | Requires pre-planning + flag rollout |

<action>Define rollback triggers:</action>

```
Rollback triggers:
1. Error rate > 5% (alerting threshold) for > 2 minutes
2. Latency p95 > 500ms (baseline: 200ms)
3. Crash rate > 1% of requests
4. Database connection failures detected
5. Manual rollback (initiated by SRE)
```

<action>Plan rollback steps:</action>

```
Rollback Steps (Blue-green):
1. Monitor metrics first 5 minutes post-deploy
2. If trigger detected, notify on-call team
3. Stop routing new traffic to green deployment
4. Keep green deployment up for 1 hour (debugging)
5. Route traffic back to blue (previous version)
6. Run smoke test on blue to confirm
7. Blue confirmed working → green can be terminated
Estimated rollback time: < 2 minutes
```

<action>If database migration needed:</action>
  - [ ] Is migration reversible?
  - [ ] If yes: document rollback SQL
  - [ ] If no: document impact and prevent rollback-without-data-loss
  - [ ] Test migration rollback in staging

<action>Record rollback readiness: "⏳ planned, tested in staging" or "✓ ready"</action>
</step>

<step n="5" goal="Set up monitoring and alerting">
<action>Ask user for or identify:</action>
  - Which metrics are most critical for this release?
  - Existing monitoring dashboard (e.g., Datadog, New Relic, CloudWatch)
  - Alerting system (e.g., Slack, PagerDuty, email)

<action>Define critical metrics:</action>

```
Business metrics:
- Daily Active Users (DAU)
- Transaction success rate
- Revenue (if applicable)

Technical metrics:
- Response time (p50, p95, p99)
- Error rate (5xx, 4xx by endpoint)
- CPU usage
- Memory usage
- Database connection pool usage
- Queue depth (if async tasks exist)
```

<action>Define alerting rules:</action>

```
Critical Alerts (page on-call):
- Error rate > 5% for > 2 min → PagerDuty + Slack #incidents
- Latency p95 > 500ms for > 5 min → PagerDuty + Slack #incidents
- CPU > 90% for > 10 min → PagerDuty + Slack #incidents

Warning Alerts (Slack only):
- Error rate > 2% for > 5 min → Slack #monitoring
- Memory > 80% → Slack #monitoring
- Disk usage > 85% → Slack #monitoring
```

<action>Test alerts:</action>
  - [ ] Generate test alert in staging
  - [ ] Confirm alert fires to correct channel
  - [ ] Confirm on-call receives notification
  - Status: "✓ alerts tested" or "⏳ pending test"

<action>Configure logging:</action>

```
Log aggregation: ELK Stack (ElasticSearch)
Log retention: 30 days for debugging window
Structured logging: all logs JSON with timestamp, service, level, trace-id
Query: "service:myapp AND level:error" (to find all errors post-deploy)
```

<action>Record monitoring status: "✓ dashboard + alerts ready" or "⏳ pending setup"</action>
</step>

<step n="6" goal="Plan feature flags and gradual rollout (if applicable)">
<action>Ask user:</action>
  - [ ] Are any features behind feature flags?
  - [ ] Do we want gradual rollout (canary/rollout)?

<action>If yes, define feature flag plan:</action>

```
Feature Flags:
1. new-dashboard-ui (new experience)
   - Initial rollout: 0% (disabled)
   - Staged rollout: 10% (1 hour) → 50% (2 hours) → 100% (4 hours)
   - Kill switch: Slack /ff new-dashboard-ui off
   
2. new-api-endpoint (backward-compatible)
   - Rollout: 100% immediately (no canary needed)
```

<action>Define kill switch:</action>

```
Kill switch: which features can be toggled off instantly?
- new-dashboard-ui: YES (no data loss, users see old UI)
- new-api-endpoint: YES (falls back to old endpoint)
- database-schema-change: NO (would require data migration)

On-call runbook:
  1. Detected issue with new-dashboard-ui
  2. Run: /ff new-dashboard-ui off
  3. All users revert to old UI instantly (no reload needed)
  4. Investigate root cause
```

<action>If no feature flags:</action>
  - Record: "No feature flags used. Rollback method: {blue-green/rolling/instant}"
</step>

<step n="7" goal="Create runbook and incident response plan">
<action>Document deploy runbook:</action>

```
PRODUCTION DEPLOY RUNBOOK

PRE-DEPLOY (1 hour before):
1. Notify team: "Deploy v1.2.3 starting at 14:00 UTC in #ops"
2. Run database backup: ./scripts/backup-db.sh
3. Confirm staging smoke tests passed
4. Confirm no ongoing incidents

DEPLOY (at 14:00 UTC):
1. Trigger CI/CD: git push origin main → GitHub Actions kicks off
2. Monitor deployment in real-time
3. Wait for all instances to be healthy (< 5 min)

POST-DEPLOY (30-60 minutes):
1. Run smoke tests on production
2. Monitor error rate, latency, CPU
3. Check dashboard for anomalies
4. Keep team on standby

POST-MONITORING (1 hour):
1. If no issues → mark deploy SUCCESS
2. If issues detected → initiate rollback
3. Document lessons learned

TROUBLESHOOTING:
- Deployment stuck: check CI/CD logs in GitHub Actions
- High error rate: check error logs in ELK
- Database migration failed: check migration logs
  Rollback DB: ./scripts/rollback-db.sh
```

<action>Document incident response:</action>

```
INCIDENT RESPONSE PLAN

Incident Lead: @devops-engineer (on-call)
Escalation: @engineering-manager if needed

Communication:
- Team: Slack #incidents
- Stakeholders: Slack #status
- Public status: status page (if applicable)

Severity Levels:
- SEV1 (Critical): Service down, revenue impact
  SLA: 5 min response, 1 hour resolution target
  
- SEV2 (Major): Service degraded, high error rate
  SLA: 15 min response, 4 hour resolution target
  
- SEV3 (Minor): Low error rate, non-critical feature down
  SLA: 1 hour response, 1 day resolution target

Post-mortem:
- Required for SEV1/SEV2
- Template: docs/development/incidents/PM-{date}.md
- Timeline + root cause + action items
```

<action>Record runbook status: "✓ documented" or "⏳ pending"</action>
</step>

<step n="8" goal="Gather approvals and finalize release decision">
<action>Collect approvals:</action>
  - [ ] Release approved by Release Manager / Tech Lead
  - [ ] Change approval ticket created (e.g., JIRA, GitHub issue)
  - [ ] Stakeholders informed (Product, Support, Operations)

<action>Verify checklist before decision:</action>

**Staging Tests:**
  - [ ] Smoke tests planned and documented
  - [ ] E2E tests passed (if applicable)
  - [ ] Integration tests passed

**Rollback Plan:**
  - [ ] Rollback method decided (blue-green / rolling / instant)
  - [ ] Rollback triggers defined
  - [ ] Rollback steps documented
  - [ ] Tested in staging (or dry-run plan)
  - [ ] Database rollback plan (if applicable)

**Monitoring:**
  - [ ] Dashboard accessible
  - [ ] Critical alerts configured and tested
  - [ ] Logging configured

**Feature Flags:**
  - [ ] Feature flag plan (if applicable)
  - [ ] Kill switch defined
  - [ ] Gradual rollout strategy (if applicable)

**Runbook:**
  - [ ] Deploy steps documented
  - [ ] Troubleshooting guide ready
  - [ ] Incident response plan ready

**Approval:**
  - [ ] Change approval obtained
  - [ ] Deploy window scheduled
  - [ ] Team and stakeholders informed

<action>Decision logic:</action>

| Condition | Decision |
|-----------|----------|
| All checklist items ✓ | **READY** → proceed to production deploy |
| Rollback plan missing | **WAITING** → complete rollback plan first |
| Monitoring incomplete | **WAITING** → complete monitoring setup first |
| Critical blocker found | **WAITING** → resolve blocker before deploy |
| All ready + approved | **READY** → can proceed to production |

<action>Record decision:</action>

```
## Decision

- **Decision:** READY | WAITING → {Rationale}
- **Blockers (if any):**
  - {Blocker 1}
  - {Blocker 2}
- **Next step:** production deploy | complete the gaps | schedule for later
```
</step>

<step n="9" goal="Generate PR file">
<action>Assign PR-id (next available, e.g., PR-001, PR-002)</action>
<action>Create PR record file: `{deployment_location}/PR-{id}.md`</action>
<action>Fill in complete PR template with all gathered information:</action>
  - Release type and version
  - Release scope (list of QRs and stories)
  - Staging test plan and results
  - Rollback plan and tests
  - Monitoring and alerting setup
  - Feature flags and gradual rollout (if applicable)
  - Runbook and incident response plan
  - Approvals and change order
  - Final decision and next steps

<action>Ensure PR file includes:</action>
  - [ ] Frontmatter with date, status, release info
  - [ ] Release scope section with QR references
  - [ ] Staging deployment and smoke test section
  - [ ] Rollback plan with triggers and steps
  - [ ] Monitoring and alerting section
  - [ ] Runbook with detailed deploy/troubleshoot steps
  - [ ] Incident response plan
  - [ ] Deploy window and approval section
  - [ ] Decision section with clear READY/WAITING status
  - [ ] Gate 4 checklist (all items reviewed)

<action>Write PR file to: {deployment_location}/PR-{id}.md</action>
</step>

<step n="10" goal="Validate, report, and prepare for deploy">
<action>Perform validation:</action>
  - [ ] PR file created and readable
  - [ ] All QRs in release scope reference existing approved stories
  - [ ] Rollback plan documented with clear steps
  - [ ] Monitoring dashboard accessible
  - [ ] Alerts tested and firing correctly
  - [ ] Runbook has complete pre/during/post-deploy steps
  - [ ] Change approval obtained
  - [ ] Decision clearly stated (READY/WAITING)

<action>Display completion summary to {user_name} in {communication_language}:</action>

**Production Readiness Record Generated Successfully**

- **PR ID:** PR-{id}
- **Release:** v{version} ({release_type})
- **Decision:** READY | WAITING
- **File Location:** {deployment_location}/PR-{id}.md

**Release Scope:**
- Total QRs: {{qr_count}}
- Total stories: {{story_count}}
- Breaking changes: {{breaking_count}}

**Staging Tests:**
- Smoke tests: {{test_status}}
- Integration tests: {{integration_status}}

**Rollback Plan:**
- Method: {{rollback_method}}
- Duration: < {{rollback_duration}} minutes
- Tested: {{tested_status}}

**Monitoring:**
- Dashboard: {{dashboard_ready}}
- Alerts: {{alerts_ready}}
- Logging: {{logging_ready}}

**Deployment Timeline:**
- Planned deploy: {deploy_date_time} UTC
- Deploy window: {{window_duration}} hours
- Post-deploy monitoring: {{monitoring_duration}} hours

**Next Steps (if READY):**
1. Obtain final approval from Change Manager
2. Schedule deploy window (if not already scheduled)
3. Notify team and stakeholders
4. Monitor alerts during deploy window
5. Execute deploy runbook
6. Capture post-deploy metrics

**Next Steps (if WAITING):**
1. Complete the noted gaps
2. Re-run this workflow to regenerate PR
3. Obtain approval once all items ✓

<action>Close the board run: bind the readiness run key (`python3 {metodoloji-root}/bmad/scripts/blackboard.py mirror --key PR-{id} --value "<READY|WAITING>: v{version} — record: {deployment_location}/PR-{id}.md" --project-root {project-root}` — the engine mirrors `methodology.last_pr` from it automatically), mirror completion onto the intent bridge (`write --key status --value complete --type state --project-root {project-root}`), and run the close-out check (`python3 {metodoloji-root}/bmad/scripts/blackboard.py doctor --json --project-root {project-root}` — waiting hand-off signals are the designed post-close state, not a failure: name them from `signal_warnings`, chain verdict `SIGNAL`; on `NEEDS ATTENTION`, surface the health warnings and resolve or disclose them before exiting).</action>

<action>Run: `python3 {metodoloji-root}/hooks/engine/resolve_customization.py --skill {skill-root} --key workflow.on_complete` — use your harness-native shell tool with the command as given (no extra wrapper params) — if the resolved value is non-empty, follow it as the final terminal instruction before exiting.</action>
</step>

</workflow>

## Gate 4 Checklist

Production Readiness generation enforces **Gate 4** of the methodology chain:
- All approved quality records (QRs) are in scope
- Staging deployment tests planned and ready
- Rollback plan defined and preferably tested
- Monitoring and alerting configured
- Runbook prepared with pre/during/post-deploy steps
- Incident response plan ready
- Change approval obtained
- Only then: PR is marked READY and deployment can proceed

This gate ensures operational readiness and minimizes production incident risk.

## Status Values for PR

- **preparing**: PR being created or reviewed
- **READY**: All checks passed, ready for production deployment
- **WAITING**: Gaps identified, complete before deploying
- **deployed** (post-deploy section): Deployment completed

## Release Type Guidelines

- **Major** (v1.0 → v2.0): Breaking changes, significant feature additions
- **Minor** (v1.2 → v1.3): New backward-compatible features
- **Patch** (v1.2.3 → v1.2.4): Bug fixes, security patches
- **Hotfix** (v1.2.3-hotfix): Emergency production fix

## See Also

- Template: `templates/_template_PR.md`
- Workflow chain: E → IR → SP → S → QR → PR
- Gates: Gate 1 (IR), Gate 2 (SP), Gate 3 (QR), **Gate 4 (PR)**
- Previous skill: `bmad-quality-record` (QR generation)
- No next skill — PR is terminal (deployment follows)

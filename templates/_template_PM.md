<!-- Record template — Development wing: Post-Mortem (blameless incident
     learning). /metodoloji:init copies this file to
     docs/development/incidents/_template_PM.md; copy THAT to
     docs/development/incidents/PM-NNN.md and replace every [bracketed]
     placeholder. Written within 24–48 hours of a SEV1/SEV2 (optional for
     SEV3). Blameless: the system, not a person — instruction text that
     survives into the record means the field was never judged. -->

# Post-Mortem: PM-XXX — [Incident Title]

- **Date:** [YYYY-MM-DD]
- **Status:** draft | reviewed | actions-closed
- **Severity:** SEV1 (critical) | SEV2 (major) | SEV3 (minor)
- **Triggered by:** [PR-id that shipped the change / external event]
- **Incident window:** [YYYY-MM-DD HH:MM → HH:MM UTC]
- **Incident lead:** [name / role]
- **Detected by:** [alert name | human report]

---

## Executive Summary

[3–5 sentences: what broke, for whom, for how long, and the single cause worth
fixing. No timeline detail here — that belongs below.]

---

## Timeline (UTC)

| Time (UTC) | Event |
| --- | --- |
| [HH:MM] | [deploy / first symptom / alert fired / mitigation started / recovered] |

---

## Impact

- **Users:** [how many, which segment, for how long]
- **Business:** [revenue / SLA / trust cost — or "none measured"]
- **Technical:** [data loss, partial writes, backlog — or "none observed"]

---

## Root Cause (5 Whys)

1. Why did [symptom] happen? → [cause]
2. Why did [cause] happen? → [cause]
3. Why did [cause] happen? → [cause]
4. Why did [cause] happen? → [cause]
5. → [the cause this post-mortem fixes]

**Contributing factors:** [missing test / missing alert / unclear runbook / none]

---

## Detection and Response

- **Detected by:** [alert vs report — the rule, not the person]
- **Time to detect:** [X minutes]
- **Time to mitigate:** [Y minutes]
- **What worked:**
- **What did not:**

---

## Lessons Learned

- [lesson that changes a default — an alert, a test, or a rule]

---

## Action Items

| # | Action | Owner | Deadline | Status |
| --- | --- | --- | --- | --- |
| 1 | [action] | [name] | [YYYY-MM-DD] | open |

Debt that comes out of this post-mortem goes into `tech-debt.md` as a TD record
and is repaid in the next sprint; the PR's "Deploy Result" section names this
PM id, so the chain stays `PR → PM → TD → SP`.

---

## Review

- **Reviewed by:** [name(s)]
- **Review date:** [YYYY-MM-DD]
- **Decision:** actions accepted | reopened → [rationale]

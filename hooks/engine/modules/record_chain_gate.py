"""Shared IR → QR → SP → PR record-chain checks for commit and deploy gates."""

from __future__ import annotations

from types import ModuleType


def check_gate_records(root: str, blocked_action: str, *, include_pr: bool = False,
                       guard_module: ModuleType) -> dict:
    """Return a deny result for the first missing required record, if any."""
    g = guard_module
    missing_ir = g._find_done_stories_without_ir(root)
    if missing_ir:
        return {
            "decision": "deny",
            "reason": (
                f"{blocked_action}: {len(missing_ir)} done story(s) exist but no Implementation Readiness (IR) record. "
                f"Stories: {', '.join(missing_ir)}. "
                "Run bmad-check-implementation-readiness to create IR record."
            ),
        }

    missing_qr = g._find_done_stories_without_qr(root)
    if missing_qr:
        return {
            "decision": "deny",
            "reason": (
                f"{blocked_action}: {len(missing_qr)} story(s) marked 'done' lack Quality Record (QR). "
                f"Stories: {', '.join(missing_qr)}. "
                "A QR covers a story only when it DECLARES it (a `| Story | … |` row "
                "or a `- **Story:** …` field; a mention in the body is not a record). "
                f"Create QR with: {g._qr_remedy(root, missing_qr)}"
            ),
        }

    missing_sp = g._find_done_stories_without_sp(root)
    if missing_sp:
        return {
            "decision": "deny",
            "reason": (
                f"{blocked_action}: {len(missing_sp)} story(s) reference SP but lack Sprint Planning record. "
                f"Stories: {', '.join(missing_sp)}. "
                "Run bmad-sprint-planning to create SP record."
            ),
        }

    if include_pr:
        missing_pr = g._find_done_stories_without_pr(root)
        if missing_pr:
            return {
                "decision": "deny",
                "reason": (
                    f"{blocked_action}: {len(missing_pr)} story(s) lack Production Readiness (PR) record. "
                    f"Stories: {', '.join(missing_pr)}. "
                    "A PR record covers a release's stories when it DECLARES them "
                    "(`- **Stories:** S-001, S-002` or a `| Story | … |` row). "
                    "Create PR record before deploying."
                ),
            }

    return {"decision": "allow"}

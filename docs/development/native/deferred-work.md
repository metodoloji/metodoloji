# Deferred work

Findings a review or a run acknowledged but deliberately did not fix now.

## Migrated from: code review of S-001, 2026-11-03

### DW-1: 16-subscription cap unmeasured

origin: code review of S-001, 2026-11-03
location: hooks/engine/modules/blackboard.py:109
reason: a 17th watch silently evicts the oldest (`hooks/engine/modules/blackboard.py:109`, `blackboard_fold.py:230-231`). Pre-existing engine cap; today 14/16 used.
status: done 2026-11-13
resolution: E-113 made the eviction announce itself (`_bounds_notice`, "bounds enforced — subscriptions N/cap, oldest expired"), and E-141 pinned the case that made it invisible: a second channel on one pattern is a NEW entry, so on a full plane it evicts the oldest and must say so (test_a_second_channel_is_a_new_entry_and_can_evict). The cap is announced and measured; only raising it is a design choice, not debt.

### DW-2: Channel-blind dedupe drops a second channel for the same (watcher, pattern)

origin: code review of S-001, 2026-11-03
location: blackboard_fold.py:224-226
reason: `blackboard_fold.py:224-226` Pre-existing engine behaviour; no current pair repeats.
status: done 2026-11-13
resolution: E-141 — the watch's identity is (watcher, pattern, channel) in both the direct write and the fold, `subscribe` reports `created`, and `unsubscribe` gained `--channel` (omitting it still clears the pair).

### DW-3: Runbook radar parser accepts only `(session|stop)` channels

origin: code review of S-001, 2026-11-03
location: bench_consistency.py:3254-3255
reason: `bench_consistency.py:3254-3255` Deliberate anchor recorded in E-043 Known Gaps; revisit when a skill ships a custom channel.
status: done 2026-11-13
resolution: E-142 — the segment pattern accepts any channel the plane stores (`[a-z][\w.-]*`), so a custom channel is documentable instead of unparseable.

### DW-4: Bench charges any subscribe line to the radar regardless of owning skill

origin: code review of S-001, 2026-11-03
location: bench_consistency.py:3280-3281
reason: `bench_consistency.py:3280-3281` The lint's rogue-watcher warning covers the same input today.
status: done 2026-11-13
resolution: E-142 — a subscription is attributed to the skill whose SKILL.md ships it; a foreign watcher is a reported problem, so the tree can no longer satisfy the contract with a line the owning skill never wrote.

### DW-5: First "change radar" marker line wins

origin: code review of S-001, 2026-11-03
location: bench_consistency.py:3285-3286
reason: (`bench_consistency.py:3285-3286`, `test_selfcheck_scripts.py:1021-1022`); a TOC mention earlier in the file would validate the wrong bullet.
status: done 2026-11-13
resolution: E-142 — the bullet is the line that ENUMERATES the radar (marker plus at least one parseable segment), so a table-of-contents mention can no longer stand in for it.

## Migrated from: code review of SP-014, 2026-11-03

### DW-6: The runner used by `optimization train` is not persisted

origin: code review of SP-014, 2026-11-03
location: n/a
reason: `eval` defaults to `scripted` unless the operator repeats `--runner` (`optimization/cli.py:39-42,249-256`). Design change, not a wiring fix.
status: open

### DW-7: DoD-001…004 in S-011/S-012/S-013 omit the canonical `Evidence:` field

origin: code review of SP-014, 2026-11-03
location: n/a
reason: `docs/development/stories/S-01*.md` Cosmetic record metadata.
status: open

### DW-8: E-108's recorded samples embed random temp paths

origin: code review of SP-014, 2026-11-03
location: n/a
reason: so the recorded sha256 cannot be re-derived byte-for-byte (`scripts/bench/bench_deferred_work_integration.py:232,262-274`). Determinism gap in the evidence file, not in the `ok` verdicts.
status: open

## Migrated from: consistency repair, 2026-11-03

### DW-9: `check-methodology.sh` CHECK4 does not classify the status `proposed`

origin: consistency repair, 2026-11-03
location: n/a
reason: a record opened with that word fails `test_check4_classifies_every_shipped_record` during its own gate measurement, so the record makes the suite red and rejects itself (E-110/E-112 evidence). The canonical pending form is `planned`; either teach CHECK4 the word or normalize the status at write time.
status: done 2026-11-13
resolution: E-145 — CHECK4 no longer restates the pending vocabulary: it asks scripts/create-record.py --status-regex experiment, so the schema owns the words (planned plus the draft aliases proposed/draft/PENDING/BEKLİYOR/in-progress/active). A non-canonical draft word now classifies as pending instead of unknown, so a record can no longer reject itself during its own gate measurement; a genuine non-status (typo, empty) still reads unknown, which keeps the diagnostic.

## Deferred from: change-radar hardening (2026-11-13)

### DW-10: The ledger CLI cannot close an entry

origin: E-142, 2026-11-13
location: skills/bmad-code-review/scripts/deferred-work.py
severity: low
reason: `--check`, `--migrate` and `--add` are mechanical, but closing an entry is still a hand edit of `status:` plus a `resolution:` line. The format documents that shape and three entries were closed by hand in E-142, which is exactly the "an LLM must remember a shape" cost the CLI exists to remove.
status: done 2026-11-13
resolution: E-144 — the CLI gained --close (status done + resolution in place, resolution required, re-close refused, keep-open decision refused), documented in the format owner and the template; 11 tests pin the shape

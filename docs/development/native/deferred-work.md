# Deferred work

Findings a review or a run acknowledged but deliberately did not fix now.

## Migrated from: code review of S-001, 2026-11-03

### DW-1: 16-subscription cap unmeasured

origin: code review of S-001, 2026-11-03
location: hooks/engine/modules/blackboard.py:109
reason: a 17th watch silently evicts the oldest (`hooks/engine/modules/blackboard.py:109`, `blackboard_fold.py:230-231`). Pre-existing engine cap; today 14/16 used.
status: open

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
status: open

### DW-4: Bench charges any subscribe line to the radar regardless of owning skill

origin: code review of S-001, 2026-11-03
location: bench_consistency.py:3280-3281
reason: `bench_consistency.py:3280-3281` The lint's rogue-watcher warning covers the same input today.
status: open

### DW-5: First "change radar" marker line wins

origin: code review of S-001, 2026-11-03
location: bench_consistency.py:3285-3286
reason: (`bench_consistency.py:3285-3286`, `test_selfcheck_scripts.py:1021-1022`); a TOC mention earlier in the file would validate the wrong bullet.
status: open

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
status: open

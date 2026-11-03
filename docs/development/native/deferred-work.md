# Deferred work

Findings a review or a run acknowledged but deliberately did not fix now.

## Deferred from: code review of S-001 (2026-11-03)

- 16-subscription cap unmeasured; a 17th watch silently evicts the oldest (`hooks/engine/modules/blackboard.py:109`, `blackboard_fold.py:230-231`). Pre-existing engine cap; today 14/16 used.
- Channel-blind dedupe drops a second channel for the same (watcher, pattern) (`blackboard_fold.py:224-226`). Pre-existing engine behaviour; no current pair repeats.
- Runbook radar parser accepts only `(session|stop)` channels (`bench_consistency.py:3254-3255`). Deliberate anchor recorded in E-043 Known Gaps; revisit when a skill ships a custom channel.
- Bench charges any subscribe line to the radar regardless of owning skill (`bench_consistency.py:3280-3281`). The lint's rogue-watcher warning covers the same input today.
- First "change radar" marker line wins (`bench_consistency.py:3285-3286`, `test_selfcheck_scripts.py:1021-1022`); a TOC mention earlier in the file would validate the wrong bullet.

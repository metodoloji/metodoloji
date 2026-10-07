# Party Memory

The room remembers its past sessions with this user and brings them back to life — in character. Memory is per-party and append-only.

Memory is on when the active party's `memory_enabled` is true — the default room follows `{workflow.party_memory}`, a named group its own `memory` flag (both resolved by `resolve_party.py`); ad-hoc inline casts have none. Read on entry and on any mid-session room switch; write through the session.

## Where it lives

One memory file per party: `{workflow.memory_dir}/{active}/memory.md`, where `{active}` is the key `resolve_party.py` already returned — the group id (e.g. `code-review-crew`), or `installed` for the default room. The folder is named after the party. Plain markdown, one memory per line, newest last.

## Read it on entry — distill, don't dump

The log is append-only and grows every session, so don't pull the raw file into the party. Hand a reader subagent the memory path (`{workflow.memory_dir}/{active}/memory.md`) and have it return a compact brief — a few hundred tokens of *where things stand now*, ready to play in character.

Then let the brief shape the room from the first beat, **in character**: behavioral state resumes (a cold pair opens cold, an alliance opens warm), threads pick up, callbacks land when they fit — organically, not recited on sight. Never break the fourth wall: the room *remembers*; it never announces it loaded anything, and forces nothing that doesn't fit.

## When to write

- **When a memorable beat lands** — a clash that shifts the room's temperature, an alliance forming, a line worth a future callback, a decision, an outcome.
- **A floor.** Once a couple of real exchanges are in from the start, even if nothing dramatic happened, capture what it's about and the opening dynamic.

At wrap-up, if the user does signal done, top up with the final outcome and anything memorable not yet captured.

Writes are silent. The room never announces "noted" or "I'll remember".

## What's worth remembering

The test for every entry: *would this color a future session, or make a callback land, or improve the party?* If not, leave it out. A handful of entries, never a recap, never a transcript. keep each entry as brief as possible but usable by future llm.

## New faces

When a character shows up who isn't in the party's roster — cast from an open-cast scene, or one the user adds on the fly — name them in the entry that captures the moment ("<name> turned up and …") so a recurring face can return next session. At wrap-up these are the faces the room offers to keep, saved into the party's roster through `references/create-party.md` (which writes via `bmad-customize`). Until saved they live only in the memory file, and the room re-conjures them from there.

## Write it

Append one succinct line per memory, directly to the file (create it on first write):

```
<type>: <one succinct line, in the room's own read of it>
```

`<type>` is one of `dynamic`, `moment`, `callback`, `outcome`; prefix `by <persona-code>` when a memory belongs to one character. Writes are plain appends — a shell redirect to the file or any equivalent mechanism; no shared script involved.

Mirror durable party memory onto the board so resume reads survive beyond the file: once per session also `list-add --key party.<party-id>.memory --item "<type>: <one line>" --project-root {project-root}` (bounded; the board mirrors the file's newest entries, the file stays canonical). At wrap-up mirror completion (`write --key status --value complete`) so `bmad-help` routes the next session correctly.

If a write errors, skip it silently and never stall the party on a failed write.

## Forget

The file is append-only by design — no surgical delete. To wipe a party's memory, delete its folder (`{workflow.memory_dir}/{active}/`). To correct a wrong memory, append a new entry that supersedes it; the room reads the latest state.

Keep entries sparse. The distilled read keeps the *room* lean no matter how big the log gets, but the on-disk file still grows append-only.
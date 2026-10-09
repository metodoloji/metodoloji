# Version history — the numbering rule and its repair

## The rule

```
n          = number of commits since the first commit (its position)
version(n) = 0.<1 + n // 100>.<n % 100>      # 0.1.x for n = 0…99, then 0.2.x …
```

The first commit (`7762d28`, "first commit") is the baseline, tagged **v0.1.0**.
Every commit after it owns exactly one number — `v0.1.1` is the first block,
position 100 opens `v0.2.0` (see *Lines and boundaries*) — and the release tag
names it: `v<version>` sits on the commit the rule gives that number. The number
is therefore a property of the **commit**, not of the push that carried it —
which is what `scripts/bump-version.py --position N` (the pre-commit hook's
flag) derives from git the moment a commit is created (absolute rule, E-012,
boundary E-013, commit-time E-014), with `scripts/versioning.py` owning the
arithmetic itself.

## Why the chain had to be repaired

The rule had no owner until E-010 built the instrument, and the history was
written before it: **twelve commits shipped with no number of their own**. The
trees of commits #1..#9 still say `0.1.0` and #10 says `0.1.5`; the CI's
first bump left `0.1.6` on what was then the newest commit — six behind the
position it actually held. (#11 and #12, the last two of that era, no longer
exist: they were folded into the squash described under *The chain* — the
evidence for this repair stays what their trees said at the time.) The only tag that existed by hand,
`v0.1.5`, sat on the **tenth** commit after the baseline instead of the fifth.

Two repairs were possible:

1. **Rewrite the commits** — rebase the history so every tree carries its own
   number, then re-tag. Fully consistent, but every SHA anyone has ever seen
   changes, and the published tags have to be re-pointed as well.
2. **Republish the pointers** — leave the commits untouched, and put the tags
   where the rule puts them (this is the option taken, E-012).

The tags below are therefore **retro-assigned**: they record each commit's
position after the baseline. For the trees written before the rule existed, the
version *string inside the tree* is not the number the tag names — that
discrepancy is listed honestly in the "tree says" column instead of being
hidden, and it is the price of not rewriting published history. From the repair
commit onward the two agree by construction, because the hooks write the
number into the tree at commit time and the CI only tags a tree that already
matches it.

## The chain

| # | commit | tag | tree says |
|---|--------|-----|-----------|
| 0 | `7762d28` | **v0.1.0** | 0.1.0 |
| 1 | `bb430d7` | v0.1.1 | 0.1.0 |
| 2 | `9c8e06d` | v0.1.2 | 0.1.0 |
| 3 | `d706940` | v0.1.3 | 0.1.0 |
| 4 | `0441417` | v0.1.4 | 0.1.0 |
| 5 | `03c4354` | v0.1.5 | 0.1.0 |
| 6 | `5cc069e` | v0.1.6 | 0.1.0 (merge commit) |
| 7 | `049cf48` | v0.1.7 | 0.1.0 |
| 8 | `7750898` | v0.1.8 | 0.1.0 |
| 9 | `cc9ef33` | v0.1.9 | 0.1.0 |
| 10 | `111efc7` | v0.1.10 | 0.1.5 |
| 11 | this commit — 09.10.2026 squashed (E-010 … E-014) | **v0.1.11** | 0.1.11 |

Commit #11 is the commit that carries this file, so its number is known before
it exists. It replaces the nine commits of 09.10.2026 (four of them empty
`chore(release): version bump` commits — the cost of the push-time pipeline
E-014 replaced): they were squashed into it and their tags **v0.1.11 …
v0.1.19** were deleted with them — v0.1.11 is then re-cut on this commit by
the pipeline, the rest name no position anymore. Positions #0…#10 are
untouched, so the chain stays hole-free under the same rule that built it.

## The one tag that moved

`v0.1.5` was created by hand on commit #10 (`111efc7`). The rule places it on
commit #5 (`03c4354`), so it was **re-pointed** there by
`scripts/release-tags.py --apply --move-mismatched --push` and published with
`git push --force`. Consequences, stated plainly:

- the remote no longer serves the old target; a clone that fetched **v0.1.5**
  before the move still holds the old tag object locally until it is
  re-fetched (`git fetch --tags --force`);
- the release named v0.1.5 never shipped with that number inside its tree: the
  tree at `03c4354` says `0.1.0`, the tree at `111efc7` says `0.1.5`. The
  number now names the position, as the rule says.

The baseline `v0.1.0` is a **lightweight** tag (it predates the rule); every
other tag in the chain is annotated. The rule checks a tag's target, not its
kind — converting the baseline to an annotated tag would be another rewrite of
published history, so it was left alone.

## Lines and boundaries — when the next line opens

`0.x` numbers belong to the counter, so the boundaries are arithmetic
(`scripts/versioning.py` owns both directions):

| position | release |
|----------|---------|
| 0 … 99   | v0.1.0 … v0.1.99 |
| **100**  | **v0.2.0** |
| 101 … 199 | v0.2.1 … v0.2.99 |
| **200**  | **v0.3.0** |

In words: **the next minor line opens every 100 commits** — `v0.2.0` is
position 100, i.e. the commit that is the 100th one after the first commit,
and `v0.3.0` is position 200; nothing has to be decided or scheduled for it to
happen. Inside a line the
patch counts commits since that line's `X.Y.0` release, which is why one rule
covers every block.

**1.0.0 is different, and deliberately so.** `0.x` means "anything may change"
in semver; 1.0.0 is a statement about maturity, and a counter cannot make it.
So a major line is **declared**: a human writes the version by hand **and**
opens the line with the marker in the commit message —

```
1.0.0 release \\
[line X.Y.0]
```

— and the CI then verifies the declaration, tags that commit `v1.0.0` and
leaves it alone (nothing is rewritten, no bump commit is created: that commit
*is* the release). From then on the patch counts commits since `v1.0.0`
(`1.0.1`, `1.0.2`, …), until a human declares the next line. A commit that
claims a major line without the marker and without the opening tag is refused
loudly, and a number the rule cannot produce (`0.1.150`, `0.9.3`) is refused
by `bump-version.py` itself, with the number it should have been.

## How the number is kept honest from here

- **The commit that owns the number writes it.** `.githooks/pre-commit`
  (installed once per clone: `git config core.hooksPath .githooks`) computes
  the position of the commit being created — `count(first commit..HEAD) + 1`,
  the union of both parents' ranges for a merge — writes that number into the
  seven version-owned files and stages them, unless the tree already holds it
  (then: nothing written, nothing staged). It refuses over unstaged work in
  those files, on §6d3 skew, on an undeclared line and on a number the rule
  cannot produce, so no commit ever carries a number it has not earned.
  A merge commit is the one commit created without that hook: `git merge`
  builds it from the tree it merged, and the tree is written before any hook
  can touch it — so `.githooks/post-merge` calls the same writer with the
  merge's own HEAD position and amends the number in the moment the commit
  exists, before it can be pushed. A merge with conflicts needs no amend: it
  stops, and the author's own `git commit` runs `pre-commit` with `MERGE_HEAD`
  present, which writes the union — either way the commit that owns the
  number is the commit that carries it.
- **CI** (`.github/workflows/release-numbers.yml`): on every push to `main` it
  reads the tree of each commit the push carried, compares it with the number
  that commit's position owns, and publishes the tags — it writes no version
  and creates **no commit** (E-014 removed the bot commits: two of the last
  four existed only to carry a number). A mismatch is a red job naming the
  number the commit should carry and how to install the hook; `--no-verify`
  is not a loophole, because the pipeline refuses what the hook skipped.
- **`scripts/bump-version.py`** is what the hook calls to write, and `--check`
  still refuses on §6d3 manifest skew before any write, so the five manifests
  can never disagree about the number.
- **`scripts/versioning.py`** is the rule (blocks, inverses, refusals), and
  **`scripts/release-tags.py`** is the audit/repair of the same rule: `--plan`
  prints the mapping, every mismatch and any release-looking tag the rule
  cannot place, and writes nothing; `--apply [--move-mismatched] [--push]` is
  the repair. It never rewrites a commit.
- **Tags record positions.** Since E-014 every new commit's tree holds its own
  number (the hook writes it before the commit exists), so a tag and its tree
  agree — except for the retro-assigned history above, whose tags name the
  position while the tree still says the old number. That is the convention
  `release-tags.py` uses to keep the chain hole-free, and it is why this page
  exists at all.

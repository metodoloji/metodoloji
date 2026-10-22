# Experiment integrity — what the gate proves, and what it cannot

**Status: implemented.** Companion to `structural-scope.md`. This document is the audit that
produced the changes, kept because the reasoning matters more than the diff.

## The question

A methodology whose approvals rest on fabricated numbers is worse than no methodology: it
manufactures confidence. So the question is not "does the gate check things" but **what does an
APPROVED token actually certify?**

Before this work, the answer was narrower than the ceremony suggested.

## The demonstrated hole

The gate ran the measurement itself and parsed a printed aggregate — `rate=1.00 (200/200)` — from
stdout. It then compared that number to the threshold. It never asked where the number came from.

In a live session this passed:

```bash
--run "node -e \"console.log('demo_rate=1.00 (200/200)')\""
```

→ `DECISION: APPROVED  token=GATE-OK-…`

A one-line string that never touched the repository minted a genuine, HMAC-signed token. The
attribution check (`_bench_target`) existed but knew only Python's `-c`/`-m`, so `node -e` slipped
through: its **code string** was returned as if it were a script path.

That is the whole disease. Everything below is treatment.

## Surface by surface

| # | Attack surface | Before | Now | Pinned by |
| --- | --- | --- | --- | --- |
| 1 | Operator declares the number by hand | closed earlier (`--measured` removed) | closed | existing |
| 2 | Bench lives in a free zone (`scratch/`, `tmp/`) | closed (E-035) | closed | existing |
| 3 | **Inline eval / shell chain** — no artifact to attribute | **open** | **closed** — refused before execution | `test_inline_eval_is_refused_before_it_can_measure` |
| 4 | **Printed aggregate with no evidence behind it** | **open** | **closed** — must be recomputable from raw samples | `test_missing_raw_samples_field_is_refused` |
| 5 | **Aggregate disagrees with its own samples** | **open** | **closed** — gate recounts x and n and refuses a mismatch | `test_printed_aggregate_must_match_the_samples` |
| 6 | **Sample file the run never wrote** | **open** | **closed** — existence + mtime ≥ run start | `test_stale_sample_file_cannot_evidence_a_fresh_run` |
| 7 | Sample file outside the project root | **open** | **closed** — resolved path must stay inside | (same resolver) |
| 8 | Sample without a verdict (`{"i": 3}`) | **open** | **closed** — every line must carry a boolean `ok` | `test_sample_without_a_verdict_is_refused` |
| 9 | Unfalsifiable test (control also passes) | **open** | **closed** — rule 7 runs the control itself | `test_structural_scope.py`, gate tests |
| 10 | Declining the evidence silently | **open** | **closed** — `none — <reason ≥40 chars>`, written into the record | `test_declining_samples_requires_a_real_reason` |
| 11 | Metric redefinition (measure A, claim B) | warned | warned | existing |
| 12 | Reusing a committed sample file for a new claim | **open** | **closed** — mtime binding | #6 |

## What is *not* closed, and must not be papered over

**A bench can fabricate samples that agree with a fabricated aggregate.** The gate now forces the
number to be derivable, inspectable, hash-bound and fresh. It cannot force it to be *true*. A
fabricator who writes `{"ok": true}` two hundred times and prints `1.00 (200/200)` passes every
mechanical check above.

This is not a gap to be closed by more checking — it is the limit of what a gate can do from
inside the process it is checking. Three partial answers exist, and only one of them is
mechanical:

1. **Independence (`--reproduce`).** Re-executes the recorded command and compares. A value typed
   once cannot survive it if the bench is honest; if the bench is dishonest, the re-run reproduces
   the lie, so this detects *drift and staleness*, not fraud. It is still the single most useful
   check a sceptical reader can run, because it costs nothing and needs no key.
2. **Inspectability.** Two hundred JSONL lines are reviewable in a way a scalar never was. A QR
   that reads samples can notice "every sample took 0.000 ms" or "all 200 are identical".
3. **Review.** Someone has to look. No mechanism replaces this, and claiming otherwise is how
   methodologies rot.

**Semantic validity is outside the gate entirely.** The gate can prove a bench is reproducible; it
cannot prove the metric *means* what the record says it means. A perfectly honest bench measuring
the wrong thing produces a perfectly genuine token. That judgement lives in the QR, and the QR
should be told so explicitly.

**Threshold shopping across records is not detectable per record.** E-002 rejected at 2,000 pins and
E-003 would have "passed" at 1,500 by changing the operating point. That was correct there — the
product ships 1,500 — but the same move is indistinguishable from goalpost-moving when done in bad
faith. E-003's record carries the distinction in prose precisely because no mechanism can.

## The design principle that came out of this

> A gate should make fabrication **expensive, visible and re-runnable** — and then say out loud
> that it has not made it impossible.

Every closure above adds cost to lying: you must write a bench file, declare it, emit consistent
per-sample evidence with a fresh mtime, survive a control run, and leave a hash-addressed artifact
in the repository. That is enough to stop the casual shortcut — the one that actually happens,
which is an agent under time pressure taking the cheapest exit. It is not enough to stop a
determined forger, and any methodology that claims otherwise has confused its paperwork with
reality.

## What a QR must now do

1. Read `Raw Sample Evidence:` — does the hash exist, does the file have the sample count claimed?
2. Open the samples. Are they plausible for the measurement described (durations, variance,
   distinct inputs)?
3. Run `--reproduce`. Same value?
4. Ask the question the gate cannot: **is this metric the one the hypothesis needed?**

A QR that checks only that a token verifies has verified nothing but arithmetic.

---

# Corpus audit — does the methodology actually decide well?

The rules above govern *how* a number is produced. They say nothing about whether the
corpus's decisions are sound. This section is an audit of the 67 records already on disk,
because a gate is only as good as the claims it is asked to judge.

## What holds up

**The measurements are real and re-runnable.** `--reproduce` on E-062 re-ran
`bench_workflow_evidence.py` and returned exactly `measured=0.0 n=6`, matching the record.
The benches are files in the repository, not shell strings; their scope is gated like product
code. 58 APPROVED / 9 REJECTED, with the rejections concentrated in fix-and-retry pairs
(E-061 rejected at 0.83 → E-062 approved at 0.00 on the same claim) — that is a working
test loop, not a rubber stamp.

## What does not hold up

**1. The uncertainty rule was applied in the wrong direction.** `uncertainty_note` returned
"none (lower-bound claim: no small-sample risk)" for *every* `<=` claim. So E-062 could assert
`storage_escape_rate <= 0.0` on 6 trials and be told it carried no small-sample risk — when
0 of 6 only bounds the true rate below **0.39**. Fixed: `wilson_upper` now computes the
matching upper bound for `<=`/`<` claims, and the note reports the bound instead of denying
there is one.

**2. Nineteen of 67 records claim `<= 0.0` — now declarable.** No finite sample establishes an
exact-zero rate; the upper bound only shrinks. Such a claim is either (a) a *census* over a
defined scenario set, in which case Wilson does not apply and 0/n is exact, or (b) a *sample*,
in which case the honest claim is `<= 0.05`. The records do not say which, so the gate now
demands the declaration: `- **Census:** <the population and why it is complete>` (≥40 chars)
makes `0/n` the rate itself and drops the sampling bound; its absence keeps the Wilson warning
that says the evidence is weaker than the claim. A short justification is ignored, and the
record says it was ignored.

**3. Sample sizes are small where the claims are strongest.** n=2, 6, 8 recur. A claim that
the system never does X is exactly the claim that needs the most evidence, and it is being
carried by the fewest trials.

**4. One metric dominates.** 48 of 67 hypotheses are `consistency_accuracy >= 0.90`. A corpus
that measures one thing measures what is easy to measure. Nothing here is wrong; something is
missing — and `--corpus-report` now prints exactly this number, so the habit is visible instead
of silent.

**5. There was no comparison mechanism at all — closed by the comparison primitive.**
Every record used to be a single threshold claim about one implementation: no record measured
condition A against condition B in one run, and there was no effect size, no pairing and no
test of whether a difference exceeded noise. "Baseline" in this corpus meant *the code state
before a fix*, i.e. two separate records with the same threshold — not a comparison.

The gate now has a comparison claim form: `H: "a_rate > b_rate"` with both metrics printed by
one run and samples paired per case (`{"a": …, "b": …}` per line). PASS requires the direction
to favour the left condition **and** an exact McNemar test on the discordant pairs at p < 0.05.
A 22-vs-20 result is rejected with "not distinguishable from noise". The record gains a
`Comparison:` line carrying both rates, the delta, the relative change, the discordant counts
and the p-value, so an effect size exists in the corpus for the first time.

What this does not do: it compares two conditions on per-case evidence, categorical or
numeric. It still cannot judge whether the alternative you chose was the right one to compare
against, and it cannot rescue a metric that measures the wrong thing. Those remain judgement,
and the gate lends them no authority beyond the arithmetic.

This matters because the questions that decide architecture are comparative: "is int8/128
better than float16/384 at the same budget?", "does this ordering beat that one?". The gate
used to approve "X meets T" and could not express "X beats Y", so it could not adjudicate the
choice between them. In the arge-one session this showed up directly: E-002 rejected at 2,000
pins and E-003 would have passed at 1,500 pins, and the difference between "the operating point
is different" and "the goalpost moved" existed only as prose in the record. A comparison claim
with a fixed operating point is now the mechanical answer to that shape.

## The honest summary

The methodology reliably answers *"did the implementation meet a stated threshold, or beat a
named alternative, under a procedure someone can re-run?"* It still does not answer *"is this
the right threshold?"*, *"is this metric the one that matters?"*, or *"is the alternative I
picked the right one to compare against?"* Those are questions of judgement, and today they are
answered by whoever writes the record — with the gate lending its authority to the answer
without examining it.

The comparison primitive closes the mechanical half of that gap: a difference now has to
survive a paired test before it becomes a decision, and the effect size lands in the record.
The judgement half — choosing what to measure and what to measure it against — remains where it
belongs, with the reviewer, and the corpus is honest about that only as long as this document
is read alongside the records.

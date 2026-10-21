# Structural Scope: STRUCT-NNN — <what this covers>

Copy to `docs/development/structural/STRUCT-NNN.md`. This record is **not** an approval.
It says one thing, publicly: these paths make no claim that could be measured, here is why,
and a QR must ratify the statement before the story closes.

Use it for: package manifests, build and CI configuration, an app root that renders nothing
yet, layout shells, wiring modules, type declarations. Do **not** use it for anything whose
behaviour a user could observe or a bench could measure — that is an experiment, and an
experiment is the only thing that produces an APPROVED token.

- **Status:** declared | ratified
  <!-- 'declared' = someone asserts it; 'ratified' = a QR looked and agreed. -->
- **Justification:** <at least 40 characters. What makes these paths non-empirical?
  Name the experiment that owns the measurable claims of this slice, if one exists.>
- **Scope:** <globs, comma or space separated, e.g. src/**, package.json, tsconfig.json>
  <!-- Never scripts/bench/** — a measurement bench needs a real experiment. -->
- **Declared:** <DD.MM.YYYY>
- **Reviewed-By:** <QR id — filled when a QR ratifies this declaration; empty while declared>

## What a reviewer must check

1. Does every path in `Scope` really make no measurable claim? A component that computes
   user-visible ranking is an experiment, not a scaffold.
2. Is the justification about the code, or about the schedule? "We needed to move" is not a
   justification.
3. Has the declaration grown since the last review? Scope creep is the failure mode: a
   scaffold record that quietly acquires the product is the exemption eating the rule.
4. If a claim moved into these paths, the declaration is wrong and the story fails. Open an
   experiment instead.

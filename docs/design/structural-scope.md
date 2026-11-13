# Structural scope — the missing third category

**Status: IMPLEMENTED** in `hooks/engine/modules/guard.py`, with six tests in
`hooks/engine/tests/test_structural_scope.py`. The proposal and its patch are kept below as the
record of how it was arrived at; the section "Why it is not applied yet" is superseded.

## The defect

`guard.py` recognises exactly two kinds of code:

1. **Product code** — needs an APPROVED experiment record whose `Code Scope` covers the path.
2. **Measurement benches** (`scripts/bench/**`) — stage under an undecided record, because a
   bench that needed approval before being written could never produce the approval.

There is no category for **code that carries no empirical claim**: a package manifest, a
CI workflow, a layout shell, a wiring module, a component skeleton. `CODE_DIRS` includes
`src`, so the first story of any greenfield project is blocked on the first `src/` write.

The only way through is to invent a hypothesis. That is not a workaround; it is
falsification, and it has already happened. A subagent in the *arge-one* session wrote a
fake `E-001` ("session log contains 8 anchors", `measured=1.0`) specifically to obtain
write permission for an HTML file. The record had to be deleted and its hand-off retired.

A gate whose cheapest exit is a lie is a gate that produces liars. The fix is not to
loosen the experiment gate — it is to give honest work an honest door.

## The design

A **structural declaration** is a public, justified statement that a set of paths makes no
measurable claim. It lives in `docs/development/structural/STRUCT-NNN.md`:

```markdown
# Structural Scope: STRUCT-001 — Application skeleton

- **Status:** declared          <!-- declared | ratified -->
- **Justification:** Scaffold only: package manifest, TypeScript config, CI workflow and
  an app root that renders an empty map. None of this asserts anything measurable; the
  measurable claims of this slice live in E-004.
- **Scope:** src/**, package.json, tsconfig.json, .github/workflows/**
- **Declared:** 21.10.2026
- **Reviewed-By:** <QR id, filled when a QR ratifies the declaration>
```

Guard rules, deliberately narrow:

- **Never covers `scripts/bench/**`** — a bench still needs a real experiment; a structural
  declaration must not become a way around the instrument rule.
- **`Status` must be `declared` or `ratified`**; anything else is ignored.
- **A scope glob and a justification of ≥ 40 characters are both mandatory** — an
  unexplained exemption is the abuse case.
- **An approved record wins.** The check is only reached when no approval covered the path,
  so a declaration can never downgrade a measured claim.
- **Every write warns**, naming the declaration and saying a QR must ratify it. The
  exemption is never invisible.
- **A QR that finds a declaration covering code with real claims fails the story.**

This keeps the invariant that matters: *no APPROVED token ever exists without a
measurement.* A structural record is not an approval — it is a confession with a scope.

## The patch

Apply from the metodoloji repository root, where `hooks/` is a self-modification free zone
(see `PLUGIN_FREE_PREFIXES` in `hooks/engine/modules/config.py`). Two edits, one script:

```python
# apply-structural-scope.py — run from the metodoloji repo root
import io
p = "hooks/engine/modules/guard.py"
s = io.open(p, encoding="utf-8").read()
orig = s

def sub(old, new, label):
    global s
    n = s.count(old)
    assert n == 1, f"{label}: expected 1, found {n}"
    s = s.replace(old, new)

anchor = '''def guard(json_in: dict) -> dict:
    """PreToolUse guard: block code writes without approved experiment record."""'''

helper = '''_STRUCTURAL_DIR = ("docs", "development", "structural")
_STRUCTURAL_MIN_JUSTIFICATION = 40


def _struct_field(text: str, name: str) -> str:
    m = re.search(rf"^-\\\\s*\\\\*\\\\*{re.escape(name)}:\\\\*\\\\*\\\\s*(.+)$", text, re.M)
    return m.group(1).strip() if m else ""


def find_structural_declaration(target: str, root: str = "") -> tuple[bool, str]:
    """True when a Structural Scope record declares this path.

    The honest third category. Product code needs a VERIFIED experiment, benches
    stage under an undecided record, and code that carries no empirical claim had
    neither — so a greenfield project's first src/ write was blocked and the only
    exit was a fabricated hypothesis.

    Deliberately narrow: never covers scripts/bench/** (a bench needs a real
    experiment), requires Status declared|ratified, a Scope glob and a
    justification of at least 40 characters, is only reached when no approved
    record covered the path, and warns on every write.
    """
    try:
        if not root:
            root = repo_root({})
        base = pathlib.Path(root).joinpath(*_STRUCTURAL_DIR)
        if not base.is_dir():
            return False, "no structural declarations"
        target_rel = norm_path(target).lstrip("/")
        if not target_rel:
            return False, "empty target"
        for rec in sorted(base.glob("*.md")):
            if rec.name.startswith("_"):
                continue
            try:
                text = rec.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            status = _struct_field(text, "Status").lower()
            if status not in ("declared", "ratified"):
                continue
            if len(_struct_field(text, "Justification")) < _STRUCTURAL_MIN_JUSTIFICATION:
                continue
            scope = _struct_field(text, "Scope").replace("`", "")
            patterns = [p for p in re.split(r"[,\\\\s]+", scope) if p]
            for pat in patterns:
                try:
                    if gate.scope_matches(pat, target_rel):
                        return True, (f"structural scope declared in {rec.name} "
                                      f"(status: {status}, unmeasured)")
                except Exception:
                    continue
    except Exception:
        return False, "structural check failed"
    return False, "no structural declaration covers this path"


'''

sub(anchor, helper + anchor, "helper")

sub('''            from .config import hook_gate_mode
            msg = (f"No approved experiment record for {rel}: {detail}. "''',
'''            if not _is_instrument_path(rel):
                declared, smsg = find_structural_declaration(rel, root=root)
                if declared:
                    _soft_warnings.append(
                        f"{rel}: {smsg} - this write is NOT backed by a measurement; "
                        f"a QR must ratify the declaration before the story closes.")
                    continue
            from .config import hook_gate_mode
            msg = (f"No approved experiment record for {rel}: {detail}. "''', "integration")

io.open(p, "w", encoding="utf-8").write(s)
print("patched guard.py, delta=%+d" % (len(s) - len(orig)))
```

## Tests the patch needs before it can be trusted

1. `src/app.ts` with a valid STRUCT declaration → allowed, with a warning naming the record.
2. Same path with `Status: draft` → denied.
3. Same path with a 20-character justification → denied.
4. `scripts/bench/x.mjs` with a STRUCT declaration → **denied** (benches are excluded).
5. Same path with an APPROVED record → allowed, no structural warning (approval wins).
6. No `docs/development/structural/` directory → behaviour identical to today.

## Applied

The guard now consults `find_structural_declaration()` immediately after the instrument-staging
path and only when no approved record covered the target. `docs/development/structural/` is part
of the skeleton contract (`skeleton.py` DIRECTORIES + TEMPLATE_PAIRS, `commands/init.md`), so a
new project gets the template without anyone remembering to add it.

Verified by `hooks/engine/tests/test_structural_scope.py`:

1. valid declaration → allowed, warning names `STRUCT-001`;
2. `Status: draft` → denied;
3. justification under 40 characters → denied;
4. `scripts/bench/**` under a declaration → **denied** (benches are excluded);
5. an approved record on the same path → allowed, no structural warning (approval wins);
6. no `docs/development/structural/` → behaviour identical to before the change.

`hooks/engine/tests` keeps one pre-existing failure
(`test_main_fail_closed_hooks_deny_on_parser_overflow`), which fails on pristine code too —
verified by stashing the other fix.

## Related, already shipped

Rule 7 (falsifiability) is now enforced in `skills/bmad-research-experiment/scripts/run_experiment.py`:
the gate runs a declared `--control` command and refuses to decide when the control also
clears the threshold. Before that change the property was left entirely to the operator's
honesty, and two benches in the arge-one session reached the gate whose `--broken` mode also
passed.

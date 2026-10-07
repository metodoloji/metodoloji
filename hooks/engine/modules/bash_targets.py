"""Bash command target extraction for BMAD hooks engine."""

import os
import pathlib
import re
import shlex

from .archive import targets_from_tar, targets_from_unzip
from .config import CODE_BASENAMES, CODE_DIRS, TAR_ARG_OPTS
from .utils import is_code_target, norm_path


def _space_out_redirects(command: str) -> str:
    """Separate redirect and separator operators without spaces.

    `>` and its append spelling `>>` (kept as ONE token, like `&&`/`||`)
    plus the segment separators (`;`, `&`, `|`) are spaced out quote-aware,
    so destinations never arrive glued to a terminator (`> out;` used to
    yield the phantom target `out;`, which the shell-junk filter then
    discarded — F1/T-03: the write escaped the gate entirely).

    Unquoted newlines become `;` separators for the same reason: without
    them the whole multi-line command is one segment and per-command
    branches vacuum trailing lines as filenames (`sed -i …` on line 1
    swallowed `#` comments and heredoc bodies from lines below as
    targets — the guard denied "for #"). Backslash-newline continuations
    and quoted newlines are preserved (no split).

    `#` comments are stripped here (before the newline fold — afterwards
    the text is one line and a single `#` would eat the rest). Shell
    rule: `#` starts a comment iff unquoted and at the start or after a
    separator/whitespace; mid-word `#` (`out#1.txt`) stays literal.
    """
    out: list[str] = []
    in_s = in_d = False
    i, n = 0, len(command)
    while i < n:
        c = command[i]
        if c == "'" and not in_d:
            in_s = not in_s
            out.append(c)
            i += 1
        elif c == '"' and not in_s:
            in_d = not in_d
            out.append(c)
            i += 1
        elif c == "\\" and not in_s and not in_d:
            out.append(c)
            if i + 1 < n:
                out.append(command[i + 1])
                i += 2
            else:
                i += 1
        elif c == "#" and not in_s and not in_d and (
            i == 0 or command[i - 1] in " \t\r\n;&|()"
        ):
            while i < n and command[i] != "\n":
                i += 1
        elif c == "\n" and not in_s and not in_d:
            out.append(" ; ")
            i += 1
        elif c == ">" and not in_s and not in_d:
            # `>>` (append) is spaced as ONE unit: two bare `>` markers make
            # the second a remnant every consumer has to reassemble and skip,
            # and the operator itself must never arrive glued to a filename
            # (`>>log` would reach shlex as a single `>>log` token). The old
            # split (`cat f  >  > log.txt`) is the defect documented by
            # test_space_out_redirects_double_gt.
            if command[i + 1:i + 2] == ">":
                out.append(" >> ")
                i += 2
            else:
                out.append(" > ")
                i += 1
        elif c in (";", "&", "|") and not in_s and not in_d:
            nxt = command[i + 1] if i + 1 < n else ""
            if (c == "&" and nxt == "&") or (c == "|" and nxt == "|"):
                out.append(f" {c}{nxt} ")
                i += 2
            else:
                out.append(f" {c} ")
                i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _read_patch_targets(path: str, prefix: str = "") -> list[str]:
    """Extract target paths from patch/diff file."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
    except OSError:
        return []
    out: list[str] = []
    for m in re.finditer(r"(?m)^diff --git a/[^\t]+ b/(.+?)(?:[\t ]|$)", content):
        out.append(prefix + m.group(1))
    for m in re.finditer(r"(?m)^\+\+\+ (?:b/)?(.+?)(?:[\t ]|$)", content):
        out.append(prefix + m.group(1))
    return out


def extract_bash_targets_ex(command: str) -> tuple[list[str], bool]:
    """Return (file paths the command may write to, had_variable_drop).

    had_variable_drop is True when a write DESTINATION was collected and
    then dropped for containing an unexpanded shell variable ($var): the
    caller gated nothing for that destination (total blind spot). Plain
    `$` mentions without a write operator (e.g. `echo $HOME`) never set
    the flag — only dropped destinations do.

    Two dialects feed this: the POSIX/shell token walk (`_extract_raw`) and
    the PowerShell write-cmdlet scan (`_extract_powershell_targets`). Both run
    on every command — a cmdlet name is distinctive enough that the scan is
    inert for ordinary shell input.
    """
    targets, dropped = _extract_raw(command)
    ps_targets = _extract_powershell_targets(command)
    if ps_targets:
        targets = targets + ps_targets
        if not dropped:
            dropped = any(t and "$" in t for t in ps_targets)
    kept = [t for t in targets if t and "$" not in t]
    # Order-preserving dedupe: more than one branch (or two interpreter
    # tokens in different segments) can report the same destination; guard
    # and stop iterate this list, so duplicates were pure re-work and could
    # inflate per-target deny messaging.
    return list(dict.fromkeys(kept)), dropped


def extract_bash_targets(command: str) -> list[str]:
    """Return file paths the command may write to.

    Unexpanded shell variables ($var, ${var}) are caller-resolved or skipped:
    individual targets containing '$' are dropped rather than dropping the
    entire command, so static targets in compound commands are still caught
    while literal '$spool_file' paths never leak into guard decisions.
    See extract_bash_targets_ex for the drop signal (F1/T-03).
    """
    targets, _ = extract_bash_targets_ex(command)
    return targets


# --- interpreter inline-eval detection ---------------------------------------
#
# An interpreter evaluates inline code only when an EVAL FLAG appears among the
# interpreter's OWN options — i.e. before the first non-flag operand.
# `python script.py -c …` passes -c to the script; it does not evaluate code.
_INTERPRETER_BASENAME_RE = re.compile(
    r"(?i)^(?:python\d*(?:\.\d+)?|node|nodejs|perl|ruby|php|deno|bun|lua|rscript)"
    r"(?:\.exe)?$"
)
# Exact flags only: single-letter shorts (python -c, node/ruby/perl -e, php -r,
# Rscript -E), optionally bundled (-ce), plus the long --eval form. The previous
# pattern `-[a-zA-Z]*[cEer]\b` accepted ANY long flag ending in c/E/e/r —
# --value, --type, --scope, --clear — so a blackboard write command was treated
# as an eval whose quoted script path (blackboard.py) became a code-write
# target and the board write was DENIED (real opencode session, 2026-09-30).
_EVAL_OPTION_RE = re.compile(r"^(?:-[cEer]+|--eval(?:=.*)?)$")


# Write-shaped markers for inline eval code. The eval branch below is
# deliberately broad (it cannot parse the code, so every quoted path is a
# candidate), but "broad" must mean "could write", not "mentions a path": a
# READ-ONLY one-liner had its quoted path treated as a write destination and
# the guard denied it — `python3 -c "print(open('src/config.ts').read())"`
# (real session, graph-engineering-arge, 2026-10-01: "salt-okunur bir python3
# komutunu yazma sanıp blokladı").
#
# Fail-closed is preserved by inverting the test: the branch fires unless the
# code is RECOGNIZABLY read-only. An unknown call like `run("scripts/gen.py")`
# is not a read and keeps taking the broad path, so a dynamic writer still
# cannot slip through. Dynamic writers that use os.system/subprocess/popen/
# exec/eval are also write markers here, so they never qualify as reads.
#
# A read is only recognized from explicit file-read shapes (single-arg
# `open(x)` = default read mode, an explicit 'r'/'rb'/'rt' mode, or a read
# method). `print(...)` is deliberately NOT a read marker: `print(foo(path))`
# says nothing about whether foo reads or writes.
_INLINE_WRITE_RE = re.compile(
    r"""(?ix)
        \bopen\s*\([^)]*?,\s*['"](?!r['"]|rb['"]|rt['"])[^'"]*['"]
      | \.write(?:_text|_bytes)?\s*\(
      | \b(?:write_text|write_bytes|writelines|truncate|unlink|rmtree|rmdir
            |makedirs?|mkdir|rename|replace|remove|touch|dump|savefig|copytree
            |writefile|writefilesync|appendfile|appendfilesync|createwritestream)\s*\(
      | \b(?:shutil|os)\.(?:copy2?|copyfile|move|rename|replace|remove|unlink
            |mkdir|makedirs|rmdir|removedirs)\b
      | \b(?:os\.system|subprocess|popen|exec|eval)\b
    """
)


_INLINE_READ_RE = re.compile(
    r"""(?ix)
        # single-argument open() uses the default read mode; a second argument
        # means the mode is explicit and is judged by the write/read regexes.
        \bopen\s*\(\s*['"][^'"]*['"]\s*\)
      | \bopen\s*\([^)]*,\s*['"](?:r|rb|rt|r\+|rb\+|rt\+)['"]
      | \.read(?:_text|_bytes|lines)?\s*\(
      | \b(?:readFileSync|readFile|readlink|readlinkSync|listdir|scandir|walk)\s*\(
    """
)


def _inline_readonly(command: str) -> bool:
    """True only when inline code is recognizably a pure file read.

    Requires a read shape and the absence of any write/dynamic-exec marker, so
    anything unrecognized stays on the fail-closed broad path.
    """
    if _INLINE_WRITE_RE.search(command):
        return False
    return bool(_INLINE_READ_RE.search(command))


def _interpreter_eval_hit(seg: list[str]) -> bool:
    """True when some interpreter in this segment carries an eval flag.

    The scan starts at each interpreter token and stops at that interpreter's
    first non-flag token (the operand): flags positioned after a script
    argument belong to the script and never count as eval flags.
    """
    for i, tok in enumerate(seg):
        base = tok.replace("\\", "/").rsplit("/", 1)[-1]
        if not _INTERPRETER_BASENAME_RE.match(base):
            continue
        for opt in seg[i + 1:]:
            if opt.startswith("-"):
                if _EVAL_OPTION_RE.match(opt):
                    return True
                continue
            break
    return False


# --- PowerShell write-cmdlet detection (Windows shell tool) -------------------
# On Windows, Claude Code exposes a `PowerShell` shell tool. Its write cmdlets
# are not POSIX commands, so the token walk in _extract_raw never sees a
# destination: `Set-Content src/a.py 'x'` yielded zero targets and the guard
# allowed the write unconditionally. This bounded scanner covers the cmdlets
# that create or modify files. Redirection (`>` / `>>`) is shared with POSIX
# and is already handled by _extract_raw; aliases (sc/ni/oh/…) are deliberately
# NOT matched — they collide with ordinary words, and the canonical names plus
# redirection cover the documented failure.
# NOTE: `Remove-Item` is deliberately ABSENT. Deletion is not a gated write in
# this methodology — POSIX `rm`/`rm -rf` yields no target and is allowed — so
# gating Remove-Item alone would make the guard stricter on Windows than on
# macOS for the same intent, the platform asymmetry this scanner exists to
# remove.
_PS_WRITE_CMDLETS = frozenset({
    "set-content", "add-content", "clear-content", "out-file", "tee-object",
    "set-item", "new-item", "copy-item", "move-item",
})
# Cmdlets whose first positional (or -Path/-FilePath/-LiteralPath) is the write
# destination.
_PS_DEST_FIRST = frozenset({
    "set-content", "add-content", "clear-content", "out-file", "tee-object",
    "set-item", "new-item",
})
# Cmdlets whose destination is -Destination or the SECOND positional
# (`Copy-Item src dst`).
_PS_DEST_SECOND = frozenset({"copy-item", "move-item"})
# Flags whose following token is a path (never content).
_PS_PATH_FLAGS = frozenset({
    "-path", "-filepath", "-literalpath", "-outfile", "-destination",
})


def _ps_word_split(s: str) -> list[str]:
    r"""Whitespace split honouring PowerShell quotes, backslashes kept literal.

    shlex is unusable here: posix mode eats the backslash, so `C:\Users\x`
    collapses to `C:Usersx` — the exact corruption the guard must avoid.
    """
    out: list[str] = []
    cur: list[str] = []
    quote = ""
    for ch in s:
        if quote:
            if ch == quote:
                quote = ""
            else:
                cur.append(ch)
        elif ch in ("'", '"'):
            quote = ch
        elif ch.isspace():
            if cur:
                out.append("".join(cur))
                cur = []
        else:
            cur.append(ch)
    if cur:
        out.append("".join(cur))
    return out


def _extract_powershell_targets(command: str) -> list[str]:
    """Paths a PowerShell command may write to (bounded cmdlet scan).

    Best-effort: an empty result means "no cmdlet destination recognized",
    never "proven safe" — the caller still applies the normal free/code checks
    to whatever is returned.
    """
    if not command:
        return []
    targets: list[str] = []
    for stmt in re.split(r"[;\r\n]", command):
        words = _ps_word_split(stmt)
        if not words:
            continue
        # A cmdlet runs only at the HEAD of a pipeline segment. Matching a
        # cmdlet name anywhere would let prose fabricate a target
        # (`git commit -m 'Set-Content'` / `echo 'Set-Content' notes`) and the
        # extensionless path would then fail is_code_target CLOSED.
        segments: list[list[str]] = [[]]
        for w in words:
            if w == "|":
                segments.append([])
            else:
                segments[-1].append(w)
        for seg in segments:
            if not seg:
                continue
            cmd = seg[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
            if cmd not in _PS_WRITE_CMDLETS:
                continue
            args = seg[1:]
            # Directory/container creation is not a code write — and an
            # extensionless path fails is_code_target CLOSED, so extracting
            # `New-Item -ItemType Directory docs/foo` would deny a
            # mkdir-equivalent (`mkdir` yields no target and is allowed).
            if cmd == "new-item":
                lowered = [a.lower() for a in args]
                if "-itemtype" in lowered:
                    ti = lowered.index("-itemtype")
                    if ti + 1 < len(args) and args[ti + 1].lower() in (
                            "directory", "container", "junction",
                            "symboliclink", "hardlink"):
                        continue
            flag_vals: list[str] = []
            positionals: list[str] = []
            i = 0
            while i < len(args):
                tok = args[i]
                if tok.startswith("-") and len(tok) > 1:
                    if tok.lower() in _PS_PATH_FLAGS and i + 1 < len(args):
                        flag_vals.append(args[i + 1])
                        i += 2
                        continue
                    i += 1
                    continue
                positionals.append(tok)
                i += 1
            if cmd in _PS_DEST_SECOND:
                if flag_vals:
                    targets.append(flag_vals[0])
                elif len(positionals) >= 2:
                    targets.append(positionals[1])
            elif cmd in _PS_DEST_FIRST:
                if flag_vals:
                    targets.append(flag_vals[0])
                elif positionals:
                    targets.append(positionals[0])
    return targets


# --- bounded per-command output/operand scanners -----------------------------
#
# Forms the token walk above has no POSIX keyword for, each a real write to a
# code path that used to extract nothing (E-063):
#   wget -O FILE / --output-document=FILE   (only lowercase -o was matched,
#                                            which is wget's LOG flag)
#   curl --output FILE / --output=FILE      (and any -o past the first token)
#   sed --in-place[=bak]                    (long form does not start with -i)
#   touch FILE / truncate -s N FILE         (create/truncate by coreutils)
#   sort -o FILE / --output=FILE            (output redirection by flag)
#   perl -i -pe '…' FILE / ruby -i -e '…'   (in-place one-liners)
#
# They are per-command rather than a generic "-o means output" rule on
# purpose: a generic rule re-opens the false-positive class that once denied a
# legitimate blackboard write (2026-09-30 session, long flags read as eval).

_OP_TOKENS = frozenset({">", ">>", "<", "|"})


def _attached_value(tok: str, short: str, longs: tuple) -> str | None:
    """Value carried INSIDE one token (`-oX`, `--output=X`), else None.

    Separate-token forms (`-o X`) are the caller's job; this only reads the
    attached spelling so both spellings name the same destination.
    """
    if not tok.startswith("--") and tok.startswith(short) and len(tok) > len(short):
        return tok[len(short):]
    for lp in longs:
        if tok.startswith(lp + "="):
            return tok[len(lp) + 1:]
    return None


def _output_flag_targets(args: list[str], value_flags: tuple, short: str) -> list[str]:
    """Destinations named by an output flag: `-o X`, `-oX`, `--output=X`.

    `-` means stdout and yields no target. Scans the WHOLE segment, so a
    destination is found even when other flags precede the output flag.
    """
    out: list[str] = []
    longs = tuple(f for f in value_flags if f.startswith("--"))
    i = 0
    while i < len(args):
        tok = args[i]
        if tok in value_flags:
            if i + 1 < len(args) and args[i + 1] != "-":
                out.append(args[i + 1])
            i += 2
            continue
        val = _attached_value(tok, short, longs)
        if val is not None:
            if val and val != "-":
                out.append(val)
            i += 1
            continue
        i += 1
    return out


def _fd_prefix(args: list[str], i: int) -> bool:
    """True when `args[i]` is a file-descriptor prefix, not a path.

    The spacing pass separates `2>` / `2>>` into `2` + `>`, so the bare digit
    would otherwise arrive as an operand (`sed … 2> err.log` reported `2`,
    and an extensionless token is code by the fail-closed tail).
    """
    return args[i].isdigit() and i + 1 < len(args) and args[i + 1] in _OP_TOKENS


def _write_operands(args: list[str]) -> list[str]:
    """File operands of a write command: non-flag tokens before the first
    redirect operator.

    The stop is where the flat "last non-flag token" rule broke: everything
    from `>`/`>>`/`<` on belongs to that operator (the redirect branch
    collects its own destination; `<` names a read), and without it
    `cp a.py src/b.py > /dev/null` took `/dev/null` as cp's DESTINATION — the
    real copy target never reached the guard (probe, 2026-10-04). A `2`
    glued to an operator is an fd prefix, never a path.
    """
    out: list[str] = []
    i = 0
    while i < len(args):
        tok = args[i]
        if tok in _OP_TOKENS:
            break
        if _fd_prefix(args, i):
            i += 1
            continue
        if tok and not tok.startswith("-"):
            out.append(tok)
        i += 1
    return out


def _operand_targets(args: list[str], value_flags: tuple = ()) -> list[str]:
    """Non-flag operands, skipping the value of each value-taking flag.

    Used where the file operands ARE the targets (`touch`, `truncate`). The
    value-skip keeps a `-d DATE` argument out of the list; the walk stops at
    the first redirect operator (its destination belongs to the redirect
    branch and a `<` operand is a read), an fd prefix (`2>`) is skipped, and
    a stray non-code token is harmless downstream (is_code_target filters it).
    """
    out: list[str] = []
    i = 0
    while i < len(args):
        tok = args[i]
        if tok in value_flags:
            i += 2
            continue
        if tok.startswith("-") and tok != "-":
            i += 1
            continue
        if tok in _OP_TOKENS:
            break
        if _fd_prefix(args, i):
            i += 1
            continue
        out.append(tok)
        i += 1
    return out


def _inplace_edit_targets(args: list[str]) -> list[str]:
    """Files an in-place `perl`/`ruby` one-liner rewrites.

    Requires BOTH an in-place flag (`-i`, `-i.bak`, `--in-place`) among the
    interpreter's own options AND an inline program (a short option bundle
    carrying `e`, e.g. `-e`/`-pe`/`-ne`). Without the inline program the
    following tokens are the SCRIPT file, not operands, and guessing there
    would flag a read as a write — so an unknown shape extracts nothing.
    """
    opts: list[str] = []
    k = 0
    while k < len(args) and args[k].startswith("-") and args[k] != "-":
        opts.append(args[k])
        k += 1
    # `-i` may sit inside a short bundle (`perl -pi -e …`), so look for the
    # letter in the bundle rather than only a leading `-i`. Long `--in-place`
    # is matched exactly (its letters are not bundle flags).
    inplace = any(
        o == "--in-place" or o.startswith("--in-place=")
        or (not o.startswith("--") and "i" in o[1:])
        for o in opts
    )
    if not inplace:
        return []
    # Only short bundles count for `e`: `--in-place` contains an 'e' but is
    # not a program flag.
    has_program = any(
        not o.startswith("--") and "e" in o[1:] for o in opts
    )
    if not has_program or k >= len(args):
        return []
    # args[k] is the inline program string; operands follow it.
    return _operand_targets(args[k + 1:])


def _extract_raw(command: str) -> tuple[list[str], bool]:
    if not command:
        return [], False
    targets: list[str] = []
    command = _space_out_redirects(command)
    try:
        # No comments=True: `#` comments are already stripped line-wise in
        # _space_out_redirects (after the newline fold the text is one
        # line — a single `#` would eat everything past it).
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return [], False

    # Split command on && / || / ; / | / & — every per-command branch below
    # must scan its own segment, never the flat token stream, or operators
    # leak in as targets (2026-09-07: `git checkout -- a.py && git status |
    # head` yielded ['a.py', '&&', 'git', 'status', '|', 'head'] and the stop
    # hook denied on the phantom `&&`).
    _SHELL_OPS = ("&&", "||", ";", "|", "&")
    seg_tokens: list[list[str]] = []
    _cur: list[str] = []
    for _t in tokens:
        if _t in _SHELL_OPS:
            if _cur:
                seg_tokens.append(_cur)
                _cur = []
        else:
            _cur.append(_t)
    if _cur:
        seg_tokens.append(_cur)

    def _seg_after(idx: int) -> list[str]:
        """Tokens after idx up to the next shell operator (this segment only)."""
        out: list[str] = []
        for t in tokens[idx + 1 :]:
            if t in _SHELL_OPS:
                break
            out.append(t)
        return out


    for i, tok in enumerate(tokens):
        if tok in _SHELL_OPS:
            continue
        if tok in (">", ">>"):
            # `>>` arrives as ONE token (the spacing pass keeps it whole); the
            # skip only guards a hand-spaced `> >` pair, whose second marker
            # would re-report the destination the first already claimed.
            if i and tokens[i - 1] in (">", ">>"):
                continue
            after = [t for t in _seg_after(i) if t not in (">", ">>")]
            if after and not after[0].startswith("&"):
                targets.append(after[0])
        elif tok == "tee":
            for t in _seg_after(i):
                if not t.startswith("-"):
                    targets.append(t)
                    break
        elif tok == "sed":
            after = _seg_after(i)
            has_i = any(
                t == "--in-place" or t.startswith("--in-place=")
                or t.startswith("-i")
                for t in after[:2]
            )
            if has_i:
                # Operand walk: stop at the first redirect operator (its
                # destination belongs to the redirect branch, `<` is a read),
                # skip an fd prefix, and consume the program exactly once —
                # an explicit `-e`/`-f` (or attached `-es…`/`-f…`) carries it,
                # else the first non-flag token is the inline `s/…/…/` string.
                # The old "tail[1:] / else tail[-1]" rule leaked operator
                # tokens as paths (`>`, `>>` once `>>` became one token) and
                # reported the script itself when no file followed
                # (`sed -i 's/a/b/'` → `s/a/b/`, which is_code_target accepts
                # by the extensionless fail-closed tail).
                program_taken = False
                files: list[str] = []
                j = 0
                while j < len(after):
                    t = after[j]
                    if t in _OP_TOKENS:
                        break
                    if t in ("-e", "-f"):
                        program_taken = True
                        j += 2  # skip the program / script file that follows
                        continue
                    if t.startswith("-"):
                        # attached program form: `-es/…/…/` / `-fprog.sed`
                        if (not t.startswith("--") and len(t) > 2
                                and t[1] in "ef"):
                            program_taken = True
                        j += 1
                        continue
                    if _fd_prefix(after, j):
                        j += 1
                        continue
                    if not program_taken:
                        # the inline program string — never a file operand
                        program_taken = True
                        j += 1
                        continue
                    files.append(t)
                    j += 1
                targets.extend(files)
        elif tok in ("cp", "mv", "install"):
            for st in seg_tokens:
                if st and st[0] == tok:
                    # Destination = last operand BEFORE a redirect: the flat
                    # "last non-flag token" rule made `cp a.py b.py >
                    # /dev/null` report /dev/null as the destination, so the
                    # real copy target never reached the guard (probe,
                    # 2026-10-04 — an E-063-class write blindness).
                    operands = _write_operands(st[1:])
                    if operands:
                        targets.append(operands[-1])
                    break
        elif tok == "curl":
            targets.extend(_output_flag_targets(_seg_after(i), ("-o", "--output"), "-o"))
        elif tok == "wget":
            # -O / --output-document write the fetched document; -o / --output-file
            # / --append-output write the log. Both are writes to the named path.
            _wget_args = _seg_after(i)
            targets.extend(_output_flag_targets(
                _wget_args, ("-O", "--output-document"), "-O"))
            targets.extend(_output_flag_targets(
                _wget_args, ("-o", "--output-file", "--append-output"), "-o"))
        elif tok == "sort":
            targets.extend(_output_flag_targets(_seg_after(i), ("-o", "--output"), "-o"))
        elif tok == "touch":
            targets.extend(_operand_targets(
                _seg_after(i),
                ("-d", "-t", "-r", "--date", "--reference", "--time",
                 "--timestamp", "--file")))
        elif tok == "truncate":
            targets.extend(_operand_targets(
                _seg_after(i), ("-s", "--size", "-r", "--reference")))
        elif tok in ("perl", "ruby"):
            targets.extend(_inplace_edit_targets(_seg_after(i)))
        elif tok.startswith("of="):  # dd of=/path
            targets.append(tok[3:])
        elif tok == "git":
            rest = _seg_after(i)
            if rest and rest[0] in ("apply", "am"):
                args_after = rest[1:]
                prefix = ""
                for t in args_after:
                    m2 = re.match(r"--directory=(.+)$", t)
                    if m2:
                        prefix = m2.group(1).rstrip("/") + "/"
                patch_path = next((t for t in args_after if not t.startswith("-")), None)
                if patch_path:
                    targets.extend(_read_patch_targets(patch_path, prefix))
            elif rest and rest[0] == "checkout" and "--" in rest:
                # Operands before any redirect only: `>`/`>>` (and a glued fd
                # prefix) are operators, never checked-out paths.
                targets.extend(_write_operands(rest[rest.index("--") + 1 :]))
        elif tok == "patch":
            rest = _seg_after(i)
            nonflags = [t for t in rest if not t.startswith("-")]
            if "<" in rest:
                j = rest.index("<")
                if j + 1 < len(rest):
                    targets.extend(_read_patch_targets(rest[j + 1]))
            elif len(nonflags) == 1:
                targets.extend(_read_patch_targets(nonflags[0]))
            elif len(nonflags) >= 2:
                targets.append(nonflags[0])
                targets.extend(_read_patch_targets(nonflags[1]))
        elif tok == "tar":
            args_after = _seg_after(i)
            _extract = False
            _skip_next = False
            _n = 0
            for t in args_after:
                _n += 1
                if _n > 32:
                    break
                if _skip_next:
                    _skip_next = False
                    continue
                if t in TAR_ARG_OPTS:
                    _skip_next = True
                    continue
                if t.startswith("-"):
                    if t.startswith("--extract") or t.startswith("-x"):
                        _extract = True
                    continue
                if t.startswith("x"):
                    _extract = True
                break
            if _extract:
                targets.extend(targets_from_tar(args_after))
        elif tok == "unzip":
            targets.extend(targets_from_unzip(_seg_after(i)))
        elif tok.startswith("python"):
            for m in re.finditer(
                r"""\bopen\(\s*['"]([^'"]+)['"]\s*,\s*['"](?:w|a|w\+|a\+)['"]\s*\)""",
                command,
            ):
                targets.append(m.group(1))
            for m in re.finditer(
                r"""\b(?:write_text|write_bytes|touch)\(\s*['"]([^'"]+)['"]""",
                command,
            ):
                targets.append(m.group(1))
            for m in re.finditer(
                r"""\b(?:copy|copyfile|copy2|move|rename|replace)\s*\([^,]+,\s*['"]([^'"]+)['"]""",
                command,
            ):
                targets.append(m.group(1))
    # Interpreter inline-eval branch — evaluated ONCE for the whole command
    # (the old per-token elif re-scanned the flat command text for every token
    # that fell through, appending the same quoted candidates N times).
    # Candidates are the quote-delimited paths in the command: inline eval code
    # is passed as a quoted string, so a dotted quoted path inside it is a
    # likely write destination. The invoked script operand itself is never a
    # candidate here: eval mode requires the flag BEFORE the operand.
    if any(_interpreter_eval_hit(seg) for seg in seg_tokens) and not _inline_readonly(command):
        for m in re.finditer(r"""['"]([^'"]+\.[A-Za-z0-9]+)['"]""", command):
            if is_code_target(m.group(1)):
                targets.append(m.group(1))
        for m in re.finditer(r"""['"](?=([^'"]*)['"])""", command):
            s = m.group(1)
            if not s or re.search(r"\.[A-Za-z0-9]+$", s):
                continue
            base = pathlib.PurePosixPath(s).name.lower()
            first = s.split("/", 1)[0].lstrip(".")
            if (first in CODE_DIRS and ("/" in s or "\\" in s)) or base in CODE_BASENAMES:
                if is_code_target(s):
                    targets.append(s)
    # Drop unexpanded shell variable targets (e.g. $spool_file, ${OUT_DIR}/x)
    kept = [t for t in targets if t and "$" not in t]
    dropped = any(t and "$" in t for t in targets)
    return kept, dropped


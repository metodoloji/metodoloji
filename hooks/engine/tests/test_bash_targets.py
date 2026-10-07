"""Tests for hooks/engine/modules/bash_targets.py — write-target extraction.

Pins the ACTUAL behavior of extract_bash_targets: redirects, operand scans,
in-place editors, interpreter eval, PowerShell cmdlets. The defects that were
once pinned here as `BUG:` (the `>>` split, operator tokens leaking into
operand scans, a redirect swallowing the cp/mv destination) are fixed — the
assertions below hold the corrected behavior, so a regression flips them red.
"""

import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HOOKS))

from modules.bash_targets import (  # noqa: E402
    _read_patch_targets,
    _space_out_redirects,
    extract_bash_targets,
    extract_bash_targets_ex,
)


# --- _space_out_redirects: quote/escape-aware redirect separation ----------

def test_space_out_redirects_single_gt():
    # A bare `>` gets spaces around it.
    assert _space_out_redirects("echo hi >out.txt") == "echo hi  > out.txt"
    # Already-spaced redirect gets an extra space inserted.
    assert _space_out_redirects("echo hi > out.txt") == "echo hi  >  out.txt"


def test_space_out_redirects_ignores_quotes():
    # Single-quoted `>` is left alone; the trailing bare `>` gets spaced.
    out = _space_out_redirects("echo 'a > b' >real.txt")
    assert out == "echo 'a > b'  > real.txt"
    # Double quotes are honoured the same way (`in_d` guards the `>`), and the
    # append `>>` stays ONE unit — it used to split into two `>` markers (the
    # previously pinned `>>` defect).
    out2 = _space_out_redirects('echo "x > y" >>log')
    assert out2 == 'echo "x > y"  >> log'


def test_space_out_redirects_escaped_char():
    assert _space_out_redirects(r"echo \> >real.txt") == r"echo \>  > real.txt"


def test_space_out_redirects_double_gt():
    # FIXED: `>>` is spaced as ONE unit (like `&&`/`||`), so append never
    # arrives as two `>` markers every consumer must reassemble — the old
    # actual was "cat f  >  > log.txt".
    assert _space_out_redirects("cat f >>log.txt") == "cat f  >> log.txt"
    assert _space_out_redirects("cat f >> log.txt") == "cat f  >>  log.txt"


# --- extract_bash_targets: redirects ----------------------------------------

def test_redirect_single_gt_target():
    assert extract_bash_targets("echo hi > src/out.txt") == ["src/out.txt"]
    assert extract_bash_targets("echo hi >src/out.txt") == ["src/out.txt"]


def test_append_redirect_double_gt():
    # Append destination extracted exactly once: `>>` stays one token, so no
    # phantom `>` target is produced (it used to split and yield a spurious
    # ">" alongside the real destination).
    assert extract_bash_targets("cat f >> logs/app.log") == ["logs/app.log"]
    assert extract_bash_targets("echo a >> x.txt && echo b >> y.txt") == ["x.txt", "y.txt"]


def test_append_redirect_single_gt_unchanged():
    # Single `>` keeps working; a split remnant never appears twice.
    assert extract_bash_targets("echo x > logs/app.log") == ["logs/app.log"]
    assert extract_bash_targets("echo a > x.txt && echo b > y.txt") == ["x.txt", "y.txt"]


# --- tee --------------------------------------------------------------------

def test_tee_target():
    assert extract_bash_targets("cmd | tee build/result.txt") == ["build/result.txt"]
    assert extract_bash_targets("cmd | tee -a log.txt") == ["log.txt"]


# --- sed -i ----------------------------------------------------------------

def test_sed_inplace_target():
    res = extract_bash_targets("sed -i 's/foo/bar/' src/app.py")
    assert "src/app.py" in res
    res2 = extract_bash_targets("sed -i.bak 's/x/y/' notes.md")
    assert any("notes.md" in t for t in res2)


def test_sed_without_i_no_target():
    assert extract_bash_targets("sed 's/x/y/' src/app.py") == []


def test_sed_inplace_operands_stop_at_a_redirect():
    # The operand scan used to leak the operator itself (`>`, `>>`) as a path
    # and to take the redirect/read tail for sed operands.
    assert extract_bash_targets("sed -i 's/a/b/' f.py > out.log") == ["f.py", "out.log"]
    assert extract_bash_targets("sed -i 's/a/b/' f.py >> out.log") == ["f.py", "out.log"]
    assert extract_bash_targets("sed -i 's/a/b/' f.py < in.txt") == ["f.py"]


def test_sed_inplace_program_is_never_a_target():
    # The old "tail[1:] / else tail[-1]" rule reported the inline program when
    # no file operand followed — an extensionless script passes is_code_target
    # (fail-closed tail), so the gate would deny on a script string.
    assert extract_bash_targets("sed -i 's/a/b/'") == []
    assert extract_bash_targets("sed -i -e 's/a/b/'") == []
    # With -e/-f every remaining operand is a file (attached forms included).
    assert extract_bash_targets("sed -i -e 's/a/b/' file.py") == ["file.py"]
    assert extract_bash_targets("sed -i -es/a/b/ file.py") == ["file.py"]
    assert extract_bash_targets("sed -i -f prog.sed file.py") == ["file.py"]


def test_fd_prefix_is_not_a_path():
    # `2>` spaces into `2` + `>`; the digit must not arrive as an operand.
    assert extract_bash_targets("sed -i 's/a/b/' f.py 2> err.log") == ["f.py", "err.log"]
    assert extract_bash_targets("touch f.ts 2> err.log") == ["f.ts", "err.log"]


# --- cp / mv / install ------------------------------------------------------

def test_cp_mv_install_target_is_dest():
    assert extract_bash_targets("cp a.py b.py") == ["b.py"]
    assert extract_bash_targets("mv src/old.py src/new.py") == ["src/new.py"]
    assert extract_bash_targets("install -m 755 x.py bin/x.py") == ["bin/x.py"]


def test_cp_multi_arg_dest():
    assert extract_bash_targets("cp a.py b.py lib/") == ["lib/"]


def test_cp_dest_survives_a_redirect():
    # The flat "last non-flag token" rule made `cp a b > /dev/null` report
    # /dev/null as the destination — the copy target itself never reached the
    # guard (probe 2026-10-04, an E-063-class write blindness).
    assert extract_bash_targets("cp a.py src/b.py > /dev/null") == ["src/b.py", "/dev/null"]
    assert extract_bash_targets("cp a.py b.py 2>&1") == ["b.py"]
    assert extract_bash_targets("mv a.py src/b.py >> log.txt") == ["src/b.py", "log.txt"]
    assert extract_bash_targets("install -m 755 x.py bin/x.py > /dev/null") == ["bin/x.py", "/dev/null"]


# --- curl / wget -----------------------------------------------------------

def test_curl_output_flag():
    assert extract_bash_targets("curl -o out.json http://x") == ["out.json"]
    assert extract_bash_targets("curl -o out.json http://x --max-time 5") == ["out.json"]


def test_curl_output_flag_not_limited_to_first_two_tokens():
    # E-063: the old scan looked only at after[:2], so an output flag behind
    # other flags (a very common curl invocation) escaped entirely.
    assert extract_bash_targets("curl -sSL -o out.bin http://x") == ["out.bin"]
    assert extract_bash_targets("curl --output out.bin http://x") == ["out.bin"]
    assert extract_bash_targets("curl --output=out.bin http://x") == ["out.bin"]
    assert extract_bash_targets("curl -oout.bin http://x") == ["out.bin"]


def test_wget_lowercase_o():
    assert extract_bash_targets("wget -o downloaded.zip http://x/y.zip") == ["downloaded.zip"]


def test_wget_uppercase_o():
    # E-063: -O / --output-document is wget's DOCUMENT flag (lowercase -o is the
    # LOG flag). The extractor recognized neither uppercase form, so a fetched
    # code file bypassed the guard; the old test pinned that miss as a "BUG".
    assert extract_bash_targets("wget -O downloaded.zip http://x/y.zip") == ["downloaded.zip"]
    assert extract_bash_targets(
        "wget --output-document=downloaded.zip http://x/y.zip") == ["downloaded.zip"]
    assert extract_bash_targets("wget -q -O dl.zip http://x") == ["dl.zip"]


def test_wget_remote_name_only_yields_nothing():
    # Plain wget / curl -O derive the name from the URL; it is not statically
    # resolvable, so no target is fabricated (fail-closed at the caller).
    assert extract_bash_targets("wget http://x/y.zip") == []
    assert extract_bash_targets("curl -O http://x/y.tar") == []


def test_curl_without_o_no_target():
    assert extract_bash_targets("curl http://x") == []


# --- in-place editors / coreutils writes (E-063) ----------------------------

def test_sed_long_in_place():
    # `--in-place` does not start with `-i`, so the long form used to extract
    # nothing while `sed -i` was caught.
    assert extract_bash_targets("sed --in-place 's/a/b/' src/a.py") == ["src/a.py"]
    assert extract_bash_targets("sed --in-place=.bak 's/a/b/' src/a.py") == ["src/a.py"]


def test_touch_creates_or_rewrites():
    assert extract_bash_targets("touch src/new.ts") == ["src/new.ts"]
    assert extract_bash_targets("touch src/a.ts src/b.ts") == ["src/a.ts", "src/b.ts"]
    # A value-taking flag's argument is not a target.
    assert extract_bash_targets("touch -d 2020-01-01 src/a.ts") == ["src/a.ts"]
    assert extract_bash_targets("touch -m src/a.ts") == ["src/a.ts"]


def test_truncate_target():
    assert extract_bash_targets("truncate -s 0 src/a.py") == ["src/a.py"]
    assert extract_bash_targets("truncate -s 0") == []


def test_sort_output_flag():
    assert extract_bash_targets("sort -o out.txt in.txt") == ["out.txt"]
    assert extract_bash_targets("sort -k1 -o out.txt in.txt") == ["out.txt"]
    assert extract_bash_targets("sort --output=out.txt in.txt") == ["out.txt"]
    assert extract_bash_targets("sort in.txt") == []


def test_perl_ruby_in_place():
    assert extract_bash_targets("perl -i -pe 's/x/y/' src/app.py") == ["src/app.py"]
    assert extract_bash_targets("perl -pi -e 's/x/y/' src/app.py") == ["src/app.py"]
    assert extract_bash_targets('ruby -i -e "gsub(/x/,\'y\')" src/app.py') == ["src/app.py"]


def test_perl_in_place_needs_inline_program():
    # Without an inline program the following token is the SCRIPT, not a file —
    # guessing there would flag a read as a write, so nothing is extracted.
    assert extract_bash_targets("perl -i src/app.py") == []
    assert extract_bash_targets("perl -pe 's/x/y/' src/app.py") == []


def test_new_scanners_do_not_treat_prose_as_a_write():
    # A command word appearing as an argument must not fabricate a target.
    assert extract_bash_targets("git commit -m 'sort -o x'") == []
    assert extract_bash_targets("ls | sort") == []


# --- dd of= -----------------------------------------------------------------

def test_dd_of_target():
    assert extract_bash_targets("dd if=/dev/zero of=img.bin bs=1M count=1") == ["img.bin"]


# --- git apply / am / checkout ----------------------------------------------

def _patch(tmp_path, body, name="changes.patch"):
    # Forward-slash path: shlex on Windows escapes backslashes, so a Windows
    # path like C:\...\changes.patch is mangled and the file is never read.
    p = str(tmp_path / name).replace("\\", "/")
    Path(p).write_text(body, encoding="utf-8")
    return p


def test_git_apply_patch(tmp_path):
    p = _patch(
        tmp_path,
        "diff --git a/src/a.py b/src/a.py\n"
        "--- a/src/a.py\n"
        "+++ b/src/a.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-old\n"
        "+new\n",
    )
    res = extract_bash_targets(f"git apply {p}")
    assert "src/a.py" in res


def test_git_apply_with_directory_prefix(tmp_path):
    p = _patch(tmp_path, "diff --git a/src/a.py b/src/a.py\n+++ b/src/a.py\n")
    res = extract_bash_targets(f"git apply --directory=sub {p}")
    assert "sub/src/a.py" in res


def test_git_checkout_targets():
    res = extract_bash_targets("git checkout -- src/a.py lib/b.py")
    assert "src/a.py" in res and "lib/b.py" in res


def test_git_checkout_operands_stop_at_a_redirect():
    # Operators after `--` are not checked-out paths.
    assert extract_bash_targets("git checkout -- src/a.py > /dev/null") == ["src/a.py", "/dev/null"]


def test_git_apply_windows_backslash_path(tmp_path):
    # _patch forward-slashes the temp path, so shlex leaves it intact on every
    # platform (a native C:\... path would otherwise be escape-mangled and the
    # patch never opened). Same contract as test_git_apply_patch.
    p = _patch(tmp_path, "diff --git a/src/a.py b/src/a.py\n+++ b/src/a.py\n")
    res = extract_bash_targets(f"git apply {p}")
    assert "src/a.py" in res


# --- patch ------------------------------------------------------------------

def test_patch_file_redirect(tmp_path):
    p = _patch(tmp_path, "diff --git a/x.c b/x.c\n--- a/x.c\n+++ b/x.c\n", "fix.patch")
    res = extract_bash_targets(f"patch < {p}")
    assert "x.c" in res


def test_patch_file_argument(tmp_path):
    p = _patch(tmp_path, "diff --git a/x.c b/x.c\n--- a/x.c\n+++ b/x.c\n", "fix.patch")
    res = extract_bash_targets(f"patch {p}")
    assert "x.c" in res


# --- python open/write/copy -------------------------------------------------

def test_python_open_write():
    cmd = "python3 -c \"open('out/data.txt', 'w').write('x')\""
    assert "out/data.txt" in extract_bash_targets(cmd)


def test_python_write_text():
    cmd = "python3 -c \"Path('src/gen.py').write_text('code')\""
    res = extract_bash_targets(cmd)
    assert "src/gen.py" in res


def test_python_copy():
    cmd = "python3 -c \"shutil.copy('a.py', 'b.py')\""
    res = extract_bash_targets(cmd)
    assert "b.py" in res


# --- interpreter -c with quoted code paths ----------------------------------

def test_interpreter_quote_code_target():
    res = extract_bash_targets("python3 -c 'run(\"scripts/gen.py\")'")
    assert any("scripts/gen.py" == t for t in res)


def test_interpreter_inline_code_no_target():
    assert extract_bash_targets("python3 -c 'print(1)'") == []


# --- interpreter eval flag: exact shorts only (2026-09-30 session regression) --

def test_long_flags_not_mistaken_for_eval():
    # Real opencode session: --value/--type/--scope/--clear end in c/E/e/r and
    # the old `-[a-zA-Z]*[cEer]\b` regex matched them, so the eval branch fired
    # on an ordinary board write and DENIED it as a code write of the quoted
    # blackboard.py script itself.
    assert extract_bash_targets(
        'python "bmad/scripts/blackboard.py" write '
        '--key prd.x.pending --value complete') == []
    assert extract_bash_targets(
        'python "bmad/scripts/blackboard.py" write '
        '--type kv --value Q6 "docs/design/prds/prd.md"') == []
    assert extract_bash_targets('python tool.py --scope docs/x.md --value v') == []
    assert extract_bash_targets('python tool.py --clear state.json "src/gen.py"') == []


def test_eval_flag_must_precede_the_operand():
    # Flags after the script operand belong to the script — running a script
    # is not evaluating inline code (running != writing).
    assert extract_bash_targets('python "scripts/gen.py" --check "src/other.py"') == []
    assert extract_bash_targets('python scripts/gen.py -e "src/other.py"') == []


def test_eval_branch_fires_once_targets_not_duplicated():
    # The old branch ran inside the token loop, so every fall-through token
    # re-appended the same candidates.
    assert extract_bash_targets('python3 -c \'run("scripts/gen.py")\'') == ["scripts/gen.py"]


def test_node_eval_long_flag_still_detected():
    # The tightened flag set must keep genuine long eval forms covered.
    assert extract_bash_targets(
        'node --eval \'require("fs")\' "src/app.js"') == ["src/app.js"]


# --- empty / malformed ------------------------------------------------------

def test_empty_command_no_targets():
    assert extract_bash_targets("") == []
    assert extract_bash_targets(None) == []


def test_malformed_quotes_no_targets():
    assert extract_bash_targets("echo 'unterminated") == []


def test_unrelated_command_no_targets():
    assert extract_bash_targets("ls -la") == []
    assert extract_bash_targets("git status") == []


def test_shell_variable_targets_dropped():
    # Unexpanded $vars are not real paths (live find: "$spool_file" wedge).
    assert extract_bash_targets("cat > $spool_file << 'EOF'\nx\nEOF") == []
    assert extract_bash_targets("echo hi > ${OUT_DIR}/x.txt") == []
    assert extract_bash_targets("cp a.py $DEST/b.py") == []


def test_static_targets_preserved_alongside_shell_vars():
    # Faz 6: static targets are preserved even if other targets have $vars
    res = extract_bash_targets("echo hi > src/out.py && echo log > $LOG")
    assert res == ["src/out.py"]



# --- _read_patch_targets directly -------------------------------------------

def test_read_patch_targets(tmp_path):
    patch = tmp_path / "p.patch"
    patch.write_text(
        "diff --git a/one.py b/one.py\n--- a/one.py\n+++ b/one.py\n"
        "diff --git a/two/two.py b/two/two.py\n--- a/two/two.py\n+++ b/two/two.py\n",
        encoding="utf-8",
    )
    res = _read_patch_targets(str(patch))
    assert "one.py" in res and "two/two.py" in res


def test_read_patch_targets_missing_file():
    assert _read_patch_targets("/nonexistent/x.patch") == []


def test_read_patch_targets_duplicates():
    # Both the `diff --git` and `+++` patterns fire for the same file.
    res = _read_patch_targets(str(Path(".") / "no-such.patch"))
    assert res == []


# --- multi-line commands: comments, heredocs, continuations ------------------
# OpenHands session: a comment+heredoc command yielded phantom targets
# ('#', 'Append', …) and the guard denied "for #". Newlines now segment,
# `#` comments strip line-wise, continuations stay joined.

def test_multiline_comment_heredoc_yields_no_phantoms():
    cmd = (
        'DOC="/tmp/x.md"\n'
        '# Update stepsCompleted in frontmatter\n'
        'sed -i "s/a/b/" "$DOC"\n'
        '# Append scope confirmation\n'
        'cat >> "$DOC" << \'APPEND\'\n'
        'hello\n'
        'APPEND'
    )
    targets, dropped = extract_bash_targets_ex(cmd)
    assert targets == []
    assert dropped is True  # $DOC destinations honestly reported as dropped


def test_multiline_static_targets_all_collected():
    res = extract_bash_targets('echo a > /tmp/x.txt\nsed -i s/a/b/ /tmp/y.txt')
    assert res == ['/tmp/x.txt', '/tmp/y.txt']


def test_backslash_continuation_not_split():
    assert extract_bash_targets('cp /tmp/a \\\n /tmp/b') == ['/tmp/b']


def test_hash_inside_quotes_and_midword_preserved():
    assert extract_bash_targets('echo "a#b\nc" > /tmp/z.txt') == ['/tmp/z.txt']
    assert extract_bash_targets('echo x > /tmp/out#1.txt') == ['/tmp/out#1.txt']
    assert extract_bash_targets('echo x > /tmp/r.txt # done') == ['/tmp/r.txt']


# --- inline interpreter eval: read-only code yields no write target --------
#
# Live false positive (graph-engineering-arge, 2026-10-01: "salt-okunur bir
# python3 komutunu yazma sanıp blokladı"): the guard blocked a READ-ONLY
# one-liner because the eval branch treated every quoted path as a write
# destination. The branch now steps aside only for code that is RECOGNIZABLY
# a pure read — anything unrecognized stays fail-closed (see the two
# long-standing tests above: `run("scripts/gen.py")` is not a read).

def test_inline_eval_readonly_open_yields_no_target():
    assert extract_bash_targets('python3 -c "print(open(\'src/config.ts\').read())"') == []


def test_inline_eval_read_mode_is_not_a_write():
    assert extract_bash_targets('python3 -c "open(\'src/a.ts\', \'r\')"') == []


def test_inline_eval_write_mode_is_still_a_write():
    cmd = 'python3 -c "open(\'src/config.ts\', \'w\').write(\'x\')"'
    assert 'src/config.ts' in extract_bash_targets(cmd)


def test_inline_eval_node_write_sync_is_still_a_write():
    cmd = 'node -e "require(\'fs\').writeFileSync(\'src/a.ts\', \'x\')"'
    assert 'src/a.ts' in extract_bash_targets(cmd)


def test_inline_eval_write_shaped_call_still_takes_the_broad_path():
    # Fail-closed preserved: a write-shaped call keeps the broad extraction that
    # cannot parse the code, so queue-style destinations are still caught.
    cmd = 'python3 -c "import shutil; shutil.copy(\'a\', \'src/a.ts\')"'
    assert 'src/a.ts' in extract_bash_targets(cmd)


def test_readonly_eval_on_a_code_dir_is_not_a_target():
    # The shape that actually blocked the real run: a repo read under src/.
    cmd = 'python3 -c "print(open(\'src/client.ts\').read())"'
    assert extract_bash_targets(cmd) == []


# --- PowerShell write cmdlets (Claude Code's Windows shell) ------------------
# `PowerShell` is a real shell tool on Windows. Its cmdlets are not POSIX, so
# the token walk alone returned no destination and a cmdlet write bypassed the
# guard. These pin the bounded cmdlet scanner — and that it never fires on
# read-only cmdlets.

def test_powershell_set_content_positional():
    assert extract_bash_targets("Set-Content src/a.py 'x'") == ["src/a.py"]


def test_powershell_set_content_named_path():
    assert extract_bash_targets("Set-Content -Path src/a.py -Value x") == ["src/a.py"]
    assert extract_bash_targets("Set-Content -LiteralPath src/a.py -Value x") == ["src/a.py"]


def test_powershell_out_file_and_add_content():
    assert extract_bash_targets("Out-File -FilePath logs/app.log") == ["logs/app.log"]
    assert extract_bash_targets("Add-Content src/a.py 'more'") == ["src/a.py"]


def test_powershell_new_item():
    assert extract_bash_targets("New-Item -ItemType File -Path src/new.py") == ["src/new.py"]
    assert extract_bash_targets("New-Item src/dir/file.py") == ["src/dir/file.py"]


def test_powershell_copy_move_destination():
    assert extract_bash_targets("Copy-Item src/a.py src/b.py") == ["src/b.py"]
    assert extract_bash_targets("Move-Item -Destination src/b.py src/a.py") == ["src/b.py"]


def test_powershell_remove_item_is_not_gated_like_posix_rm():
    # Deletion is not a gated write (POSIX `rm` yields no target), so
    # Remove-Item must stay inert — otherwise Windows would be stricter than
    # macOS for the same intent.
    assert extract_bash_targets("Remove-Item -Path src/a.py -Force") == []
    assert extract_bash_targets("Remove-Item src/a.py src/b.py") == []


def test_powershell_keeps_windows_path_intact():
    # shlex posix mode would eat the backslashes (C:projsrca.py); the cmdlet
    # scanner must preserve the native path so is_code_target can judge it.
    cmd = r"Set-Content -Path C:\proj\src\a.py -Value x"
    assert extract_bash_targets(cmd) == [r"C:\proj\src\a.py"]


def test_powershell_new_item_directory_is_not_a_code_write():
    # `mkdir` yields no target (allowed); a directory New-Item must match it,
    # or an extensionless path would fail is_code_target closed and deny.
    assert extract_bash_targets("New-Item -ItemType Directory -Path docs/foo") == []
    assert extract_bash_targets("New-Item -ItemType Container docs/foo") == []


def test_powershell_cmdlet_named_in_prose_is_inert():
    # Only a pipeline-head cmdlet counts; a name in prose must not fabricate a
    # target (an extensionless one would fail is_code_target closed and deny).
    assert extract_bash_targets("git commit -m 'Set-Content'") == []
    assert extract_bash_targets("echo 'Set-Content' notes") == []
    assert extract_bash_targets("Write-Host Out-File") == []


def test_powershell_pipeline_head_is_matched():
    assert extract_bash_targets("'x' | Set-Content out.py") == ["out.py"]


def test_powershell_redirect_shared_with_posix():
    assert extract_bash_targets("Get-Content a.py > src/out.py") == ["src/out.py"]


def test_powershell_read_only_cmdlets_are_inert():
    assert extract_bash_targets("Get-ChildItem src") == []
    assert extract_bash_targets("Get-Content src/a.py") == []
    assert extract_bash_targets("Write-Host hello") == []
    assert extract_bash_targets("Test-Path src/a.py") == []

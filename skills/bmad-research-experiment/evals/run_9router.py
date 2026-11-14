#!/usr/bin/env python3
"""9Router chat shim for run_evals.py (stdlib only, no deps).

Usage: run_9router.py "<prompt>" <cwd> — the skill under test is staged by
run_evals.py at <cwd>/.claude/skills/<name>/; the shim reads its SKILL.md and
sends it as the system prompt (truncated to SKILL_CHARS), then the case input
as the user message. What is measured is the skill document itself — a fail is
a SKILL.md bug, fixed in SKILL.md, never in this shim.

Prints a two-line JSONL transcript on stdout:
  {"type":"assistant","message":{"content":[{"type":"text","text":"..."}]}}
  {"type":"result","usage":{"input_tokens":N,"output_tokens":M}}
run_evals.py's account_transcript() understands both lines, so token counts
survive. Exit non-zero (→ "error" status) when env is missing or the call fails.
"""
import json
import os
import sys
import urllib.request

SKILL_CHARS = 26000  # ~6.5k tokens: full SKILL.md + references fit


def fail(msg: str) -> int:
    print(f"9router shim: {msg}", file=sys.stderr)
    return 1


def load_skill(cwd: str) -> tuple[str, str]:
    """Read the staged skill dir (SKILL.md + references/*.md + assets/*).

    Returns (system_prompt, error). A fail here is a harness bug, never graded.
    """
    root = os.path.join(cwd, ".claude", "skills")
    try:
        names = sorted(os.listdir(root))
    except OSError as e:
        return "", f"no staged skill in {root}: {e}"
    for name in names:
        sdir = os.path.join(root, name)
        sk = os.path.join(sdir, "SKILL.md")
        if not os.path.isfile(sk):
            continue
        try:
            text = open(sk, encoding="utf-8").read()
        except OSError as e:
            return "", f"cannot read {sk}: {e}"
        parts = [text]
        refd = os.path.join(sdir, "references")
        try:
            refs = sorted(f for f in os.listdir(refd) if f.endswith(".md"))
        except OSError:
            refs = []
        for rf in refs:
            try:
                parts.append(f"\n\n# Reference: references/{rf}\n"
                             + open(os.path.join(refd, rf),
                                    encoding="utf-8").read())
            except OSError:
                pass
        full = "\n".join(parts)
        if len(full) > SKILL_CHARS:
            full = full[:SKILL_CHARS] + "\n[... truncated for context budget ...]"
        try:
            staged = os.path.realpath(sdir)
            repo_skill = os.path.join(os.environ.get("METODOLOJI_REPO", ""),
                                      "skills", name)
            src = repo_skill if os.path.isdir(repo_skill) else staged
        except Exception:
            src, staged = sdir, sdir
        plugin_real = os.path.dirname(os.path.dirname(staged))
        if os.environ.get("METODOLOJI_REPO", ""):
            plugin_real = os.environ["METODOLOJI_REPO"]
        head = (f"You are the {name} skill. The SKILL.md below plus its "
                f"references/ files are your full instructions. Follow them "
                f"exactly. There is no shell, no file tool, no subprocess — "
                f"you cannot run commands or read files; produce the "
                f"deliverable as your reply text. Do NOT emit tool calls, "
                f"do NOT narrate setup steps — write the artifact directly. "
                f"Placeholder values: {{skill-root}}={src}, "
                f"{{project-root}}={cwd}, {{metodoloji-root}}={plugin_real}, "
                f"{{doc_workspace}}={cwd}, {{date}}=today.\n\n")
        return head + full, ""
    return "", f"no SKILL.md under {root}"


def main() -> int:
    if len(sys.argv) < 3:
        return fail("usage: run_9router.py <prompt> <cwd>")
    url = os.environ.get("NINEROUTER_URL", "").rstrip("/")
    key = os.environ.get("NINEROUTER_KEY", "")
    model = os.environ.get("NINEROUTER_MODEL", "")
    if not url or not model:
        return fail("NINEROUTER_URL and NINEROUTER_MODEL must be set")
    prompt = sys.argv[1]
    system, err = load_skill(sys.argv[2])
    if err:
        return fail(err)
    body = json.dumps({
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
    }).encode()
    req = urllib.request.Request(
        url + "/v1/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=280) as r:
            res = json.loads(r.read().decode())
    except Exception as e:  # noqa: BLE001 — shim reports, runner records
        return fail(f"chat call failed: {e}")
    try:
        text = res["choices"][0]["message"]["content"] or ""
        usage = res.get("usage", {})
    except (KeyError, IndexError, TypeError) as e:
        return fail(f"unexpected response shape: {e}")
    print(json.dumps({"type": "assistant",
                      "message": {"content": [{"type": "text",
                                               "text": text}]}}))
    print(json.dumps({"type": "result", "usage": {
        "input_tokens": usage.get("prompt_tokens", 0),
        "output_tokens": usage.get("completion_tokens", 0)}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

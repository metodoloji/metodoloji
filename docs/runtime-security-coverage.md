# Runtime security coverage

Metodoloji shares its Python decision engine across runtimes. This page
separates that shared decision logic from the runtime-specific interception
surface: an engine can only decide on calls the host adapter sends to it.

| Runtime | Interception surface | Write and shell coverage | Known limits |
| --- | --- | --- | --- |
| Claude Code | `PreToolUse` and `PostToolUse` hooks from `hooks/hooks.json` | Manifest matchers: `Write`, `Edit`, `MultiEdit`, `Bash`, `PowerShell`, `file_editor`, and `terminal`. | Coverage depends on the host emitting a matching hook event and payload. Direct filesystem writes outside those tool calls are outside the gate. |
| OpenHands | The shared hook manifest and `file_editor` / `terminal` matchers | `file_editor` and `terminal` calls are routed through the same engine. | Coverage depends on the installed OpenHands version and its hook payloads. Direct filesystem writes outside the hook are outside the gate. |
| OpenCode | V2 `execute.before`, plus optional permission fallback; `execute.after` for audit | Write aliases: `write`, `edit`, `multiedit`, `patch`, `apply_patch`; shell aliases: `bash`, `shell`. | Unknown tools are allowed with a warning. The fallback can be disabled. OpenCode 1.x requires the separate V1 entry. See [OpenCode setup and coverage](OPENCODE.md). |
| Hermes | In-process `pre_tool_call`; `on_session_end` is report-only | `write_file`, `patch`, `multi_edit`, `notebook_edit`, `terminal`, `execute_code`, and conditional `process_manage` writes/submits are routed through the adapter. | Dynamic `execute_code` targets may not be resolved; the default is warning, with an optional deny setting. `computer_use` does not pass through the file-level gate and is allowed by default; `block_gui_tools=true` disables it. See [Hermes settings and limits](../HERMES.md). |

## Shared policy

The Python engine owns record verification, scope decisions, commit/deploy
checks, and the normalized decision contract. Adapter code maps host tools and
payloads into that contract and maps the result back to the host. Therefore,
shared decision rules do not imply identical tool coverage or identical
fail-open behavior for every advisory feature.

The shipped `custom/config.toml` sets `code_guard`, `quality_gate`, and
`deploy_guard` to `hard`, and freshness to `warn`. Hook behavior is still
bounded by the host's registered interception points. Use the runtime's
configuration and this matrix when assessing enforcement in a particular
installation; this plugin is not an operating-system sandbox.

## Enforcement boundary (read this before calling it "100%")

The guard is a **hook-level policy engine**, not a syscall sandbox. Its
enforcement claim is precise:

- A mapped tool call whose write destination the engine can resolve is gated
  mechanically: no covering VERIFIED record → `DENY` in hard mode.
- Interpreter inline evaluation (`python -c`, `node -e`, `sh -c`, …) is
  inspected for write markers. When the code contains a write/dynamic-exec
  marker but no complete destination can be resolved statically (a
  runtime-built path, for example), the engine reports an **unresolved write**
  and fails closed in `hard` mode. Recognizably read-only one-liners remain
  allowed.
- A compiled binary or an external process that writes files internally is
  **outside** the extraction boundary: the hook sees the command, not the
  syscalls the child makes. Direct filesystem writes made outside a mapped tool
  call are likewise outside the gate.
- Host coverage varies (see the matrix above). An event the host never emits
  cannot be decided.

So "100% mechanical" means "every resolvable mapped write is decided by the
gate", not "no write can escape the operating system". The distinction is
deliberate: an engine that claimed the second would be lying, and a methodology
that cannot state its own boundary cannot be audited.

## Maintenance rule

When adding a runtime or changing a tool mapping, update this matrix and the
runtime-specific installation guide in the same change. Document which calls
are denied, warned, or not observed, including any default-off or opt-in layer.
Run `python scripts/check-runtime-coverage.py` to ensure the mapped tool names
remain present in this matrix. This static check catches documentation drift; it
does not exercise a live host runtime.

# Runtime security coverage

Metodoloji shares its Python decision engine across runtimes. This page
separates that shared decision logic from the runtime-specific interception
surface: an engine can only decide on calls the host adapter sends to it.

| Runtime | Interception surface | Write and shell coverage | Known limits |
| --- | --- | --- | --- |
| Claude Code | `PreToolUse` and `PostToolUse` hooks from `hooks/hooks.json` | The manifest matches `Write`, `Edit`, `MultiEdit`, `Bash`, and `PowerShell`. | Coverage depends on the host emitting a matching hook event and payload. Direct filesystem writes outside those tool calls are outside the gate. |
| OpenHands | The shared hook manifest and `file_editor` / `terminal` matchers | `file_editor` and `terminal` calls are routed through the same engine. | Coverage depends on the installed OpenHands version and its hook payloads. Direct filesystem writes outside the hook are outside the gate. |
| OpenCode | V2 `execute.before`, plus optional permission fallback; `execute.after` for audit | `write`, `edit`, `patch`, and shell tools are mapped to the engine. The permission fallback is enabled by default to catch some nested writes. | Unknown tools are allowed with a warning. The fallback can be disabled. OpenCode 1.x requires the separate V1 entry. See [OpenCode setup and coverage](OPENCODE.md). |
| Hermes | In-process `pre_tool_call`; `on_session_end` is report-only | `write_file`, `patch`, `multi_edit`, `notebook_edit`, `terminal`, and `execute_code` are routed to the engine. | Dynamic `execute_code` targets may not be resolved; the default is warning, with an optional deny setting. `computer_use` does not pass through the file-level gate and is allowed by default; `block_gui_tools=true` disables it. See [Hermes settings and limits](../HERMES.md). |

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

## Maintenance rule

When adding a runtime or changing a tool mapping, update this matrix and the
runtime-specific installation guide in the same change. Document which calls
are denied, warned, or not observed, including any default-off or opt-in layer.

// map.ts — OpenCode's tool call shape → the engine's normalize_hook_input shape.
//
// Deliberately thin: the engine's `opencode` vocabulary branch owns tool-name
// normalization (`write`/`edit`/`patch` → file_editor, `shell` → terminal,
// `filePath`/`patchText` extraction). Duplicating that mapping here would create
// a second source of truth; the bridge only forwards the raw name and input.

export function toEngine(
  tool: string,
  input: unknown,
  cwd: string,
  extra: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    tool_name: typeof tool === "string" ? tool : "",
    tool_input: input && typeof input === "object" ? input : {},
    cwd,
    ...extra,
  }
}

// The `permission.evaluate` fallback layer only receives an action and its
// resources (not the tool input), so a payload is rebuilt from those: the `edit`
// action becomes a write to EVERY resource path (OpenCode lists one per file a
// patch touches, so targeting only the first would re-open the gap the guard
// exists to close), and the `bash` action becomes the joined command. Every
// other action is non-gated and returns undefined (the hook then no-ops).
export function permissionToEngine(
  action: string,
  resources: readonly string[],
  cwd: string,
): Record<string, unknown> | undefined {
  if (action === "edit") {
    const paths = resources.map((r) => String(r)).filter((p) => p.trim())
    return {
      tool_name: "write",
      tool_input: { path: paths[0] ?? "", paths },
      cwd,
    }
  }
  if (action === "bash" || action === "shell") {
    return { tool_name: "shell", tool_input: { command: resources.join(" ") }, cwd }
  }
  return undefined
}

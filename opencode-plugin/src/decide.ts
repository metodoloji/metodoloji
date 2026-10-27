// decide.ts — turn the engine's JSON contract into an OpenCode-shaped decision.
//
// The engine prints Claude Code's `hookSpecificOutput` envelope; OpenCode wants
// a throw (deny) or a system message (context). This module is the only
// translation layer, and it is pure so it can be tested without a live session.

import { FAIL_NO_ENGINE, FAIL_NO_ROOT, type RawRun } from "./runtime.ts"

export interface EngineResult {
  effect: "allow" | "deny"
  reason?: string
  context?: string
  warnings: string[]
}

// Only the write gate fails closed. Audit and session_start cannot block, and
// stop is report-only — mirroring hooks/scripts/hook-entry.sh's policy exactly.
export function isFailClosed(mode: string): boolean {
  return mode === "pre" || mode === "guard"
}

function failure(mode: string, raw: RawRun): EngineResult {
  if (!isFailClosed(mode)) return { effect: "allow", warnings: [] }
  const reason = raw.stderr.includes("plugin root not found") ? FAIL_NO_ROOT : FAIL_NO_ENGINE
  return { effect: "deny", reason, warnings: [] }
}

export function interpret(mode: string, raw: RawRun): EngineResult {
  if (raw.failed || raw.status !== 0 || !raw.stdout.trim()) return failure(mode, raw)

  let parsed: unknown
  try {
    parsed = JSON.parse(raw.stdout)
  } catch {
    return failure(mode, raw)
  }
  const root = (parsed ?? {}) as Record<string, unknown>
  const hso = (root.hookSpecificOutput ?? {}) as Record<string, unknown>

  if (mode === "stop") {
    const reason = root.reason ?? hso.additionalContext
    return { effect: "allow", reason: typeof reason === "string" ? reason : undefined, warnings: [] }
  }
  if (mode === "audit" || mode === "session_start") {
    const context = hso.additionalContext
    return { effect: "allow", context: typeof context === "string" ? context : undefined, warnings: [] }
  }

  const decision = hso.permissionDecision
  const reason = hso.permissionDecisionReason
  const extra = hso.additionalContext
  const warnings = typeof extra === "string" && extra ? extra.split("\n") : []
  if (decision === "deny") {
    return {
      effect: "deny",
      reason: typeof reason === "string" ? reason : FAIL_NO_ENGINE,
      warnings,
    }
  }
  if (decision === "allow") return { effect: "allow", warnings }

  // The Python engine always emits an explicit permissionDecision for gated
  // hooks. Missing or unknown values are a broken contract, not an allow.
  if (isFailClosed(mode)) {
    return { effect: "deny", reason: FAIL_NO_ENGINE, warnings: [] }
  }
  return { effect: "allow", warnings: [] }
}

// --- per-session warning queue ----------------------------------------------
// OpenCode's `execute.before` hook has no `additionalContext` channel
// (Claude Code's PreToolUse does), so soft-gate / quality / deploy warnings are
// buffered here and flushed into the next `session.hook("context")` system
// prompt — where the model actually reads them. Keyed by session ID: one
// OpenCode server can run several sessions, and a warning raised in one must
// never surface in another's system prompt.
const pendingWarnings = new Map<string, string[]>()
const MAX_PENDING = 50
const MAX_SESSIONS = 64

export function queueWarnings(sessionID: string | undefined, warnings: readonly string[]): void {
  if (!sessionID || warnings.length === 0) return
  const list = pendingWarnings.get(sessionID) ?? []
  for (const warning of warnings) if (warning) list.push(warning)
  if (list.length > MAX_PENDING) {
    list.splice(0, list.length - MAX_PENDING)
  }
  if (!pendingWarnings.has(sessionID) && pendingWarnings.size >= MAX_SESSIONS) {
    const oldest = pendingWarnings.keys().next().value
    if (oldest !== undefined) pendingWarnings.delete(oldest)
  }
  pendingWarnings.set(sessionID, list)
}

export function flushWarnings(sessionID: string | undefined): string[] {
  if (!sessionID) return []
  const list = pendingWarnings.get(sessionID)
  if (!list) return []
  pendingWarnings.delete(sessionID)
  return list
}

// v1/index.ts — OpenCode 1.x adapter for the metodoloji gate chain.
//
// Why this exists: OpenCode 1.x loads a plugin whose default export is a
// factory returning the `Hooks` object (`tool.execute.before`, `event`, …).
// The V2 adapter in ../src/index.ts uses `Plugin.define` / `ctx.tool.hook`,
// which OpenCode 1.x does not load (it is skipped silently). This file maps the
// V1 hooks onto the SAME Python engine, so both adapters reach one decision
// source and cannot drift apart.
//
// Hook map (metodoloji hook → OpenCode 1.x):
//   PreToolUse pre        → "tool.execute.before"       throw = deny
//   PostToolUse audit     → "tool.execute.after"        log-only
//   SessionStart context  → "experimental.chat.system.transform" (once/session)
//   Stop (report-only)    → "event" (session.idle)
//   deny fallback         → "permission.ask"
//   metodoloji_verify /
//   metodoloji_audit      → "tool"

import { tool } from "@opencode-ai/plugin/tool"

import { flushWarnings, queueWarnings } from "../src/decide.ts"
import { run } from "../src/engine.ts"
import { permissionToEngine, toEngine } from "../src/map.ts"
import { runPluginScript } from "../src/runtime.ts"

const GUARD_FAIL =
  "Methodology guard blocked this tool (fail-closed): the hook engine returned no decision."

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Ctx = any

export default async function metodolojiOpenCodeV1(ctx: Ctx) {
  const cwd: string = ctx?.directory ?? process.cwd()
  const started = new Set<string>()
  const stopped = new Set<string>()
  const reports = new Map<string, string>()

  return {
    // 1. Guard — fail-closed. A throw blocks the call and the message reaches
    //    the model, exactly like PreToolUse's deny.
    "tool.execute.before": async (input: Ctx, output: Ctx) => {
      const decision = run("pre", toEngine(input.tool, output.args, cwd))
      queueWarnings(input.sessionID, decision.warnings)
      if (decision.effect === "deny") {
        throw new Error(decision.reason ?? GUARD_FAIL)
      }
    },

    // 2. Audit — fail-open, log-only (appends one redacted line to the trail).
    "tool.execute.after": async (input: Ctx, output: Ctx) => {
      const decision = run(
        "audit",
        toEngine(input.tool, input.args, cwd, { tool_response: output.output }),
      )
      queueWarnings(input.sessionID, decision.warnings)
    },

    // 3. Session context — inject once per session, then flush buffered
    //    warnings and a pending Stop report into the next system prompt.
    "experimental.chat.system.transform": async (input: Ctx, output: Ctx) => {
      const sessionID: string | undefined = input.sessionID
      if (sessionID && !started.has(sessionID)) {
        started.add(sessionID)
        const decision = run("session_start", { cwd })
        if (decision.context) output.system.push(decision.context)
      }
      if (sessionID) {
        const report = reports.get(sessionID)
        if (report) {
          reports.delete(sessionID)
          output.system.push(report)
        }
      }
      for (const warning of flushWarnings(sessionID)) {
        output.system.push(warning)
      }
    },

    // 4. Stop — report-only, once per session (loop-safe). V1 has no synthetic
    //    message channel, so the report is queued for the next system prompt
    //    and surfaced immediately as a toast when a TUI is attached.
    event: async ({ event }: Ctx) => {
      if (event?.type !== "session.idle") return
      const sessionID: string | undefined = event?.properties?.sessionID
      if (!sessionID || stopped.has(sessionID)) return
      stopped.add(sessionID)
      const decision = run("stop", { cwd })
      if (!decision.reason) return
      reports.set(sessionID, decision.reason)
      try {
        await ctx.client?.tui?.showToast?.({
          body: { message: decision.reason, variant: "info", duration: 10_000 },
        })
      } catch {
        // no TUI attached (e.g. `opencode run`); the report still reaches the
        // next system prompt through the queue above.
      }
    },

    // 5. Fallback deny layer — catches writes `tool.execute.before` may not
    //    observe. Non-gated permission types are a no-op.
    "permission.ask": async (input: Ctx, output: Ctx) => {
      const raw = input.pattern
      const resources = Array.isArray(raw)
        ? raw.map((r: unknown) => String(r))
        : raw
          ? [String(raw)]
          : []
      const payload = permissionToEngine(String(input.type ?? ""), resources, cwd)
      if (!payload) return
      const decision = run("pre", payload)
      if (decision.effect === "deny") {
        output.status = "deny"
      }
    },

    // 6. Custom tools — thin wrappers over plugin-owned Python scripts.
    tool: {
      metodoloji_verify: tool({
        description:
          "Verify an experiment record is APPROVED and its gate token is not forged.",
        args: {
          record: tool.schema
            .string()
            .describe("Path to the experiment record (.md) to verify"),
        },
        async execute(args: Ctx) {
          const result = runPluginScript(
            "skills/bmad-research-experiment/scripts/run_experiment.py",
            ["--verify", "--record", String(args?.record ?? "")],
          )
          return { output: result.output }
        },
      }),
      metodoloji_audit: tool({
        description:
          "Run the methodology blackboard health check (doctor --json) for the current project.",
        args: {},
        async execute() {
          const result = runPluginScript("bmad/scripts/blackboard.py", [
            "doctor",
            "--json",
            "--project-root",
            cwd,
          ])
          return { output: result.output }
        },
      }),
    },
  }
}

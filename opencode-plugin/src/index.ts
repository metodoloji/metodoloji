// index.ts — the OpenCode plugin entrypoint.
//
// It registers the same gate chain the Claude Code and OpenHands adapters do,
// but expressed through the OpenCode V2 plugin API. Every hook delegates to the
// shared Python engine; nothing about the methodology decision is reimplemented
// in TypeScript.
//
// Hook map (metodoloji hook → OpenCode V2):
//   PreToolUse pre        → ctx.tool.hook("execute.before")   throw = deny
//   PostToolUse audit     → ctx.tool.hook("execute.after")    log-only
//   SessionStart context  → ctx.session.hook("context")       injected once
//                           (the hook runs per model request; a storage key
//                            makes the session_start call fire once)
//   Stop (report-only)    → ctx.event.subscribe() session.idle
//   deny fallback         → ctx.permission.hook("evaluate")
//   metodoloji_verify /
//   metodoloji_audit      → ctx.tool.transform (custom tools)

import { Plugin } from "@opencode/plugin"

import { flushWarnings, queueWarnings } from "./decide.ts"
import { run } from "./engine.ts"
import { permissionToEngine, toEngine } from "./map.ts"
import { runPluginScript } from "./runtime.ts"

const GUARD_FAIL =
  "Methodology guard blocked this tool (fail-closed): the hook engine returned no decision."

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Ctx = any

export default Plugin.define({
  id: "metodoloji",
  async setup(ctx: Ctx) {
    const cwd: string = ctx?.location?.directory ?? process.cwd()
    const options = (ctx?.options ?? {}) as { permissionFallback?: boolean }
    const permissionFallback = options.permissionFallback !== false

    // 1. Guard — fail-closed. Runs before every tool; a throw blocks the call
    //    and its message reaches the model, exactly like PreToolUse's deny.
    await ctx.tool.hook("execute.before", (event: Ctx) => {
      const decision = run("pre", toEngine(event.tool, event.input, cwd))
      queueWarnings(event.sessionID, decision.warnings)
      if (decision.effect === "deny") {
        throw new Error(decision.reason ?? GUARD_FAIL)
      }
    })

    // 2. Audit — fail-open, log-only (appends one redacted line to the trail).
    await ctx.tool.hook("execute.after", (event: Ctx) => {
      const response = event.status === "completed" ? event.result : event.error
      const decision = run(
        "audit",
        toEngine(event.tool, event.input, cwd, { tool_response: response }),
      )
      queueWarnings(event.sessionID, decision.warnings)
    })

    // 3. Session context — inject once per session, then flush buffered warnings.
    await ctx.session.hook("context", async (event: Ctx) => {
      const key = `context/${event.sessionID}`
      let text: string | undefined
      try {
        if (!(await ctx.storage.get(key))) {
          text = run("session_start", { cwd }).context
          await ctx.storage.set(key, Date.now())
        }
      } catch {
        text = undefined
      }
      if (text) event.system.push({ type: "text", text })
      for (const warning of flushWarnings(event.sessionID)) {
        event.system.push({ type: "text", text: warning })
      }
    })

    // 4. Stop — report-only, once per session (loop-safe). A synthetic note is
    //    the OpenCode surface for Stop's additionalContext.
    const controller = new AbortController()
    void (async () => {
      try {
        for await (const event of ctx.event.subscribe({ signal: controller.signal })) {
          if (event?.type !== "session.idle") continue
          const sessionID = event?.properties?.sessionID ?? event?.sessionID
          if (!sessionID) continue
          const key = `stop/${sessionID}`
          try {
            if (await ctx.storage.get(key)) continue
            await ctx.storage.set(key, Date.now())
          } catch {
            // storage is best-effort; the engine also dedupes via the audit log
          }
          const decision = run("stop", { cwd })
          if (decision.reason) {
            await ctx.session.synthetic({ sessionID, text: decision.reason })
          }
        }
      } catch {
        // the subscription aborts on plugin unload
      }
    })()

    // 5. Fallback deny layer — belt-and-suspenders for writes `execute.before`
    //    may not observe (e.g. Code Mode nested edits). Opt out with
    //    `{ "permissionFallback": false }` in the plugin options.
    if (permissionFallback) {
      await ctx.permission.hook("evaluate", (event: Ctx) => {
        const payload = permissionToEngine(event.action, event.resources ?? [], cwd)
        if (!payload) return
        const decision = run("pre", payload)
        if (decision.effect === "deny") {
          event.effect = "deny"
          event.message = decision.reason ?? GUARD_FAIL
        }
      })
    }

    // 6. Custom tools — thin wrappers over plugin-owned Python scripts.
    await ctx.tool.transform((editor: Ctx) => {
      editor.add({
        name: "metodoloji_verify",
        description:
          "Verify an experiment record is APPROVED and its gate token is not forged.",
        input: {
          type: "object",
          properties: {
            record: {
              type: "string",
              description: "Path to the experiment record (.md) to verify",
            },
          },
          required: ["record"],
          additionalProperties: false,
        },
        async execute(input: Ctx) {
          const record = String(input?.record ?? "")
          const result = runPluginScript(
            "skills/bmad-research-experiment/scripts/run_experiment.py",
            ["--verify", "--record", record],
          )
          return { content: result.output }
        },
      })

      editor.add({
        name: "metodoloji_audit",
        description:
          "Run the methodology blackboard health check (doctor --json) for the current project.",
        input: { type: "object", properties: {}, additionalProperties: false },
        async execute() {
          const result = runPluginScript("bmad/scripts/blackboard.py", [
            "doctor",
            "--json",
            "--project-root",
            cwd,
          ])
          return { content: result.output }
        },
      })
    })

    return () => controller.abort()
  },
})

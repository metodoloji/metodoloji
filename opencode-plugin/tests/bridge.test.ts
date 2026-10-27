import assert from "node:assert/strict"
import { test } from "node:test"

import { flushWarnings, interpret, isFailClosed, queueWarnings } from "../src/decide.ts"
import { permissionToEngine, toEngine } from "../src/map.ts"
import type { RawRun } from "../src/runtime.ts"

function raw(stdout: string, overrides: Partial<RawRun> = {}): RawRun {
  return { stdout, stderr: "", status: 0, failed: false, ...overrides }
}

function envelope(body: Record<string, unknown>): string {
  return JSON.stringify({ hookSpecificOutput: body })
}

// --- map.ts -----------------------------------------------------------------

test("toEngine forwards the raw tool name and input with cwd", () => {
  const payload = toEngine("patch", { patchText: "x" }, "/proj")
  assert.deepEqual(payload, {
    tool_name: "patch",
    tool_input: { patchText: "x" },
    cwd: "/proj",
  })
})

test("toEngine never lets a non-object input through", () => {
  assert.deepEqual(toEngine("read", "nonsense", "/proj").tool_input, {})
  assert.deepEqual(toEngine("read", null, "/proj").tool_input, {})
})

test("permissionToEngine rebuilds edit and bash payloads, ignores the rest", () => {
  assert.deepEqual(permissionToEngine("edit", ["src/a.py"], "/p"), {
    tool_name: "write",
    tool_input: { path: "src/a.py", paths: ["src/a.py"] },
    cwd: "/p",
  })
  assert.deepEqual(permissionToEngine("bash", ["git", "push"], "/p"), {
    tool_name: "shell",
    tool_input: { command: "git push" },
    cwd: "/p",
  })
  assert.equal(permissionToEngine("read", ["src/a.py"], "/p"), undefined)
})

test("permissionToEngine targets every path a multi-file patch lists", () => {
  const payload = permissionToEngine("edit", ["src/a.py", "src/b.py"], "/p")
  assert.deepEqual(payload?.tool_input, {
    path: "src/a.py",
    paths: ["src/a.py", "src/b.py"],
  })
})

// --- decide.ts --------------------------------------------------------------

test("only the write gate fails closed", () => {
  assert.equal(isFailClosed("pre"), true)
  assert.equal(isFailClosed("guard"), true)
  assert.equal(isFailClosed("audit"), false)
  assert.equal(isFailClosed("stop"), false)
  assert.equal(isFailClosed("session_start"), false)
})

test("a deny envelope becomes a deny decision with its reason", () => {
  const result = interpret(
    "pre",
    raw(envelope({ permissionDecision: "deny", permissionDecisionReason: "no record" })),
  )
  assert.equal(result.effect, "deny")
  assert.equal(result.reason, "no record")
})

test("an allow envelope with additionalContext becomes warnings", () => {
  const result = interpret(
    "pre",
    raw(envelope({ permissionDecision: "allow", additionalContext: "w1\nw2" })),
  )
  assert.equal(result.effect, "allow")
  assert.deepEqual(result.warnings, ["w1", "w2"])
})

test("an engine failure denies the write gate but allows the rest", () => {
  const failed: RawRun = { stdout: "", stderr: "boom", status: null, failed: true }
  assert.equal(interpret("pre", failed).effect, "deny")
  assert.equal(
    interpret(
      "pre",
      raw(envelope({ permissionDecision: "allow" }), { status: 1 }),
    ).effect,
    "deny",
  )
  assert.equal(interpret("audit", failed).effect, "allow")
  assert.equal(interpret("stop", failed).effect, "allow")
})

test("missing or invalid decisions deny the write gate", () => {
  assert.equal(interpret("pre", raw("")).effect, "deny")
  assert.equal(interpret("pre", raw(envelope({}))).effect, "deny")
  assert.equal(
    interpret("pre", raw(envelope({ permissionDecision: "maybe" }))).effect,
    "deny",
  )
})

test("a plugin-root failure carries the exact hook-entry sentence", () => {
  const failed: RawRun = {
    stdout: "",
    stderr: "Methodology plugin root not found — fail-closed blocked.",
    status: null,
    failed: true,
  }
  assert.match(interpret("pre", failed).reason ?? "", /plugin root not found/)
})

test("audit and session_start surface additionalContext", () => {
  assert.equal(
    interpret("audit", raw(envelope({ additionalContext: "note" }))).context,
    "note",
  )
  assert.equal(
    interpret("session_start", raw(envelope({ additionalContext: "ctx" }))).context,
    "ctx",
  )
})

test("stop surfaces its report reason and never denies", () => {
  const result = interpret("stop", raw(JSON.stringify({ decision: "allow", reason: "left open" })))
  assert.equal(result.effect, "allow")
  assert.equal(result.reason, "left open")
})

test("the warning queue is per-session and drained on flush", () => {
  flushWarnings("s1")
  flushWarnings("s2")
  queueWarnings("s1", ["a", "b"])
  queueWarnings("s2", ["c"])
  // one session's warnings never leak into another's system prompt
  assert.deepEqual(flushWarnings("s1"), ["a", "b"])
  assert.deepEqual(flushWarnings("s1"), [])
  assert.deepEqual(flushWarnings("s2"), ["c"])
  // an absent session id is a no-op, not a shared bucket
  queueWarnings(undefined, ["ignored"])
  assert.deepEqual(flushWarnings(undefined), [])
})

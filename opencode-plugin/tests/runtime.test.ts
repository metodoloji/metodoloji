// End-to-end: the bridge must speak the real engine's contract over a real
// subprocess. No mocks — spawnEngine launches hooks/engine/main.py exactly as
// the OpenCode hooks do, so a contract drift fails here.

import assert from "node:assert/strict"
import { mkdirSync, mkdtempSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { test } from "node:test"

import { interpret } from "../src/decide.ts"
import { run } from "../src/engine.ts"
import { discoverRoot, resolvePython, spawnEngine } from "../src/runtime.ts"

function codeProject(): string {
  const root = mkdtempSync(join(tmpdir(), "metodoloji-oc-"))
  mkdirSync(join(root, "src"), { recursive: true })
  return root
}

test("the methodology root and a python interpreter are discoverable", () => {
  assert.ok(discoverRoot(), "expected the checkout root next to hooks/engine/main.py")
  assert.ok(resolvePython(), "expected python3/python/py on PATH")
})

test("a write to a code path is denied through the real engine", () => {
  const cwd = codeProject()
  const decision = interpret(
    "pre",
    spawnEngine("pre", { tool_name: "write", tool_input: { path: "src/a.py" }, cwd }, 10_000),
  )
  assert.equal(decision.effect, "deny")
  assert.match(decision.reason ?? "", /src\/a\.py/)
})

test("a shell write to a code path is denied through the real engine", () => {
  const cwd = codeProject()
  const decision = run("pre", {
    tool_name: "shell",
    tool_input: { command: "echo x > src/a.py" },
    cwd,
  })
  assert.equal(decision.effect, "deny")
})

test("a patch touching a code path is denied through the real engine", () => {
  const cwd = codeProject()
  const patch = "*** Begin Patch\n*** Add File: src/a.py\n+x\n*** End Patch\n"
  const decision = run("pre", {
    tool_name: "patch",
    tool_input: { patchText: patch },
    cwd,
  })
  assert.equal(decision.effect, "deny")
})

test("the apply_patch alias is gated identically", () => {
  const cwd = codeProject()
  const patch = "*** Begin Patch\n*** Add File: src/a.py\n+x\n*** End Patch\n"
  const decision = run("pre", {
    tool_name: "apply_patch",
    tool_input: { patchText: patch },
    cwd,
  })
  assert.equal(decision.effect, "deny")
})

test("a multi-file patch is gated on every path it touches", () => {
  const cwd = codeProject()
  mkdirSync(join(cwd, "scratch"), { recursive: true })
  // First target is free-zone; only a guard that checks the whole patch denies.
  const patch =
    "*** Begin Patch\n*** Add File: scratch/notes.md\n+x\n*** Add File: src/a.py\n+y\n*** End Patch\n"
  const decision = run("pre", {
    tool_name: "patch",
    tool_input: { patchText: patch },
    cwd,
  })
  assert.equal(decision.effect, "deny")
  assert.match(decision.reason ?? "", /src\/a\.py/)
})

test("a read-only tool is allowed without warnings", () => {
  const cwd = codeProject()
  const decision = run("pre", { tool_name: "read", tool_input: { filePath: "src/a.py" }, cwd })
  assert.equal(decision.effect, "allow")
  assert.deepEqual(decision.warnings, [])
})

test("a free-zone write is allowed", () => {
  const cwd = codeProject()
  const decision = run("pre", {
    tool_name: "write",
    tool_input: { filePath: "scratch/notes.md" },
    cwd,
  })
  assert.equal(decision.effect, "allow")
})

test("session_start returns injectable context", () => {
  const cwd = codeProject()
  const decision = run("session_start", { cwd })
  assert.equal(decision.effect, "allow")
  assert.ok((decision.context ?? "").length > 0)
})

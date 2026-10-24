// The 1.x adapter must reach the same real engine the V2 adapter does. These
// tests invoke the hooks directly (no OpenCode server) so a mapping drift in
// v1/index.ts fails here instead of silently disabling the gate on 1.x.

import assert from "node:assert/strict"
import { mkdirSync, mkdtempSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { test } from "node:test"

import plugin from "../v1/index.ts"

function codeProject(): string {
  const root = mkdtempSync(join(tmpdir(), "metodoloji-oc-v1-"))
  mkdirSync(join(root, "src"), { recursive: true })
  return root
}

test("the v1 guard denies a code write through the real engine", async () => {
  const hooks: any = await plugin({ directory: codeProject(), client: {} })
  await assert.rejects(
    hooks["tool.execute.before"](
      { tool: "write", sessionID: "s1", callID: "c1" },
      { args: { path: "src/a.py", content: "x" } },
    ),
    /src\/a\.py/,
  )
})

test("the v1 session context is injectable", async () => {
  const hooks: any = await plugin({ directory: codeProject(), client: {} })
  const system: string[] = []
  await hooks["experimental.chat.system.transform"]({ sessionID: "s1" }, { system })
  assert.ok(system.join("\n").trim().length > 0)
})

test("the v1 adapter registers both custom tools", async () => {
  const hooks: any = await plugin({ directory: process.cwd(), client: {} })
  assert.deepEqual(Object.keys(hooks.tool).sort(), ["metodoloji_audit", "metodoloji_verify"])
})

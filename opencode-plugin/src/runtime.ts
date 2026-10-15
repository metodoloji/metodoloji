// runtime.ts — the single place that knows HOW to reach the Python engine.
//
// The gate logic is NOT reimplemented here: every hook spawns the same
// `hooks/engine/main.py` the Claude Code and OpenHands adapters call, so the
// three runtimes can never disagree about a decision. This module only answers
// three questions: where is the methodology root, which Python runs it, and how
// do we speak its JSON contract. Fail-closed texts are byte-for-byte the
// sentences hooks/scripts/hook-entry.sh and run-hook.sh print, so a bridge-side
// failure and a shell-side failure read identically to the model.

import { spawnSync } from "node:child_process"
import { existsSync, readdirSync, statSync } from "node:fs"
import { homedir } from "node:os"
import { dirname, join, resolve } from "node:path"
import { fileURLToPath } from "node:url"

export const RUNTIME = "opencode"

// Per-mode budgets mirror hooks/hooks.json (guard 10s, audit 2s, stop 5s).
export const TIMEOUTS: Record<string, number> = {
  pre: 10_000,
  guard: 10_000,
  quality: 10_000,
  deploy: 10_000,
  audit: 2_000,
  stop: 5_000,
  session_start: 5_000,
}

export const FAIL_NO_ENGINE =
  "Methodology hook engine could not run (no python or missing engine) — fail-closed blocked."
export const FAIL_NO_ROOT =
  "Methodology plugin root not found — fail-closed blocked."

const ENGINE_REL = join("hooks", "engine", "main.py")

let cachedRoot: string | undefined
let cachedPython: string | null | undefined

function isRoot(dir: string | undefined | null): boolean {
  if (!dir) return false
  try {
    return existsSync(join(dir, ENGINE_REL))
  } catch {
    return false
  }
}

// The checkout case: this file is <root>/opencode-plugin/src/runtime.ts, so two
// directories up is the methodology root that owns hooks/.
function selfRoot(): string | undefined {
  try {
    const here = dirname(fileURLToPath(import.meta.url))
    return resolve(here, "..", "..")
  } catch {
    return undefined
  }
}

function childDirs(base: string): string[] {
  try {
    return readdirSync(base, { withFileTypes: true })
      .filter((entry) => entry.isDirectory())
      .map((entry) => join(base, entry.name))
  } catch {
    return []
  }
}

// `cache/<org>/<name>/<version>` — run-hook.sh scans the two-level glob so an
// install that places the version dir under a different org layout is still found.
function nestedChildDirs(base: string): string[] {
  return childDirs(base).flatMap((dir) => childDirs(dir))
}

function mtime(dir: string): number {
  try {
    return statSync(dir).mtimeMs
  } catch {
    return 0
  }
}

// A stale cache must never shadow a live checkout (run-hook.sh's rule).
function newestFirst(dirs: string[]): string[] {
  return dirs.sort((a, b) => mtime(b) - mtime(a))
}

// Order mirrors the candidate list hooks/hooks.json dispatches (the first hop
// Claude Code runs) so both layers resolve the same root when several installs
// coexist: explicit env roots, then this checkout, then the fixed marketplace
// checkout, then the versioned caches (newest first), then the legacy install.
function candidateRoots(env: NodeJS.ProcessEnv, self: string | undefined): string[] {
  const home = homedir()
  const caches = [
    ...nestedChildDirs(join(home, ".claude", "plugins", "cache", "metodoloji")),
    ...childDirs(join(home, ".claude", "plugins", "cache", "yunusgungor", "metodoloji")),
    ...childDirs(join(home, ".openhands", "plugins", "cache")),
  ]
  return [
    env.METODOLOJI_PLUGIN_ROOT ?? "",
    env.CLAUDE_PLUGIN_ROOT ?? "",
    self ?? "",
    join(home, ".claude", "plugins", "marketplaces", "metodoloji"),
    ...newestFirst(caches),
    join(home, ".openhands", "plugins", "installed", "metodoloji"),
  ]
}

export function discoverRoot(env: NodeJS.ProcessEnv = process.env): string | undefined {
  if (cachedRoot && isRoot(cachedRoot)) return cachedRoot
  cachedRoot = undefined
  for (const candidate of candidateRoots(env, selfRoot())) {
    if (isRoot(candidate)) {
      cachedRoot = resolve(candidate)
      return cachedRoot
    }
  }
  return undefined
}

export function resolvePython(): string | undefined {
  if (cachedPython !== undefined) return cachedPython ?? undefined
  for (const candidate of ["python3", "python", "py"]) {
    try {
      const probe = spawnSync(candidate, ["-c", "import sys; sys.exit(0)"], {
        encoding: "utf-8",
        timeout: 5_000,
      })
      if (!probe.error && probe.status === 0) {
        cachedPython = candidate
        return candidate
      }
    } catch {
      // try the next interpreter name
    }
  }
  cachedPython = null
  return undefined
}

export interface RawRun {
  stdout: string
  stderr: string
  status: number | null
  failed: boolean
}

export function spawnEngine(
  mode: string,
  payload: Record<string, unknown>,
  timeoutMs: number,
  env: NodeJS.ProcessEnv = process.env,
): RawRun {
  const root = discoverRoot(env)
  if (!root) return { stdout: "", stderr: FAIL_NO_ROOT, status: null, failed: true }
  const python = resolvePython()
  if (!python) return { stdout: "", stderr: FAIL_NO_ENGINE, status: null, failed: true }

  const engine = join(root, ENGINE_REL)
  const childEnv: NodeJS.ProcessEnv = {
    ...env,
    HOOK_TYPE: mode,
    METODOLOJI_RUNTIME: RUNTIME,
    METODOLOJI_PLUGIN_ROOT: root,
    CLAUDE_PLUGIN_ROOT: env.CLAUDE_PLUGIN_ROOT || root,
  }
  // The payload carries `cwd`; the engine reads the project root from it, so we
  // never interpolate a path into a shell and never depend on process.cwd().
  const result = spawnSync(python, [engine, `--runtime=${RUNTIME}`, mode], {
    input: JSON.stringify(payload ?? {}),
    encoding: "utf-8",
    timeout: timeoutMs,
    env: childEnv,
  })
  const failed = Boolean(result.error) || result.status === null
  return {
    stdout: typeof result.stdout === "string" ? result.stdout : "",
    stderr: typeof result.stderr === "string" ? result.stderr : "",
    status: result.status,
    failed,
  }
}

// Custom tools wrap plugin-owned Python scripts. Python (never `sh`) keeps the
// wrapper cross-platform — Windows installs have no POSIX shell.
export function runPluginScript(
  scriptRel: string,
  args: string[],
  env: NodeJS.ProcessEnv = process.env,
): { ok: boolean; output: string } {
  const root = discoverRoot(env)
  if (!root) return { ok: false, output: FAIL_NO_ROOT }
  const python = resolvePython()
  if (!python) return { ok: false, output: FAIL_NO_ENGINE }
  const result = spawnSync(python, [join(root, scriptRel), ...args], {
    encoding: "utf-8",
    timeout: 60_000,
    env: { ...env, METODOLOJI_PLUGIN_ROOT: root },
  })
  const output = `${result.stdout ?? ""}${result.stderr ?? ""}`.trim()
  const ok = !result.error && result.status === 0
  return { ok, output: output || (ok ? "(no output)" : FAIL_NO_ENGINE) }
}

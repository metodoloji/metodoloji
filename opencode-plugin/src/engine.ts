// engine.ts — orchestrates one engine call: spawn, then interpret.
//
// Every hook goes through `run()`, so the spawn budget and the fail-closed
// policy live in one place and no hook can accidentally default to open.

import { interpret, type EngineResult } from "./decide.ts"
import { spawnEngine, TIMEOUTS } from "./runtime.ts"

export function run(
  mode: string,
  payload: Record<string, unknown>,
  timeoutMs?: number,
): EngineResult {
  const budget = timeoutMs ?? TIMEOUTS[mode] ?? 5_000
  return interpret(mode, spawnEngine(mode, payload, budget))
}

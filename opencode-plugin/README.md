# metodoloji-opencode-plugin

OpenCode adapter for the **metodoloji** methodology plugin (V2 and 1.x).

This package is a thin TypeScript bridge. It registers the same gate chain the
Claude Code and OpenHands adapters run, but it does **not** reimplement any
decision — every hook spawns the shared Python engine
(`hooks/engine/main.py --runtime=opencode`) and maps its JSON contract back to
OpenCode.

- Guard (`execute.before`) — **fail-closed** write gate (`throw` = deny).
- Audit (`execute.after`) — log-only.
- Session context (`session.hook("context")`) — once per session.
- Stop (`session.idle`) — report-only, once per session.
- `permission.evaluate` — optional fallback deny layer.
- Custom tools — `metodoloji_verify`, `metodoloji_audit`.

See [`../docs/OPENCODE.md`](../docs/OPENCODE.md) for installation, options, the
portable skills setup and troubleshooting.

## Layout

```
index.ts         V2 package entry (re-exports src/index.ts)
src/runtime.ts   plugin-root + python discovery, engine spawn, fail-closed texts
src/map.ts       OpenCode call → engine payload (deliberately thin)
src/decide.ts    engine JSON → allow/deny/context (+ warning queue)
src/engine.ts    spawn + interpret in one place
src/index.ts     Plugin.define + hook registration
v1/index.ts      OpenCode 1.x entry (tool.execute.before/after, event, …)
tests/           node:test contract tests (incl. a real engine round-trip)
```

> The root `index.ts` is **required** for V2: OpenCode resolves a local plugin
> directory as `<dir>/index.ts` and ignores `package.json` `exports`. Without it
> the plugin is skipped silently (no gate at all). Do not delete it.
>
> OpenCode 1.x cannot load the V2 entry. Point `plugin` at `opencode-plugin/v1`
> instead; it maps the 1.x hooks onto the same engine.

## Test

```sh
node --test "tests/*.test.ts"
```

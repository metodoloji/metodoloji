// index.ts — the package entry OpenCode actually loads.
//
// Why this file exists next to `src/index.ts`: OpenCode 2.x resolves a local
// plugin entry as `<plugin-dir>/index.ts` (or `.js`) and does NOT read
// `package.json` `exports`/`main` for a configured plugin directory. With only
// `src/index.ts` present the plugin is skipped silently — no error, and the
// write gate never runs (a quiet fail-OPEN, the one outcome the guard exists to
// prevent). Verified against OpenCode v2.0.29 with a probe package: a directory
// holding `src/index.ts` + `exports: {".": "./src/index.ts"}` is not loaded,
// while the same directory with a root `index.ts` loads.
//
// The definition itself stays in `src/index.ts` so the whole implementation
// lives under `src/`; this file is only the loader-facing re-export.
export { default } from "./src/index.ts"

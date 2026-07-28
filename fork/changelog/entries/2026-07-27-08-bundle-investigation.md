# Bundle splitting: investigated, no change — wrong optimization for a local-disk Electron app

**Date:** 2026-07-27
**Type:** Docs
**Branch:** `perf/ui-latency`

<!-- No code change. Records why the "split the 28.5MB bundle" plan was dropped. -->

## Why

The build prints a warning that the renderer chunk is 28.5MB, and the plan was to
add `manualChunks` and lazy-load mermaid/xterm to shrink it. Investigating turned
up three reasons not to, and one already-done.

## Findings

### The single big chunk is deliberate, and documented

`vite.config.ts` sets `rolldownOptions.output.codeSplitting: false` with a comment
explaining why: Shiki ships many dynamic chunks, and **electron-builder can OOM
scanning thousands of files**, so the build intentionally collapses to one chunk
(~22MB then, 28.5MB now). `chunkSizeWarningLimit: 25000` exists precisely so the
size is a regression alarm, not a nag. Adding `manualChunks` fights this directly
and risks the OOM the comment was written to prevent.

### Bundle *size* is the wrong metric here — this is Electron, loading from disk

Confirmed at `electron/main.ts:8525`: production loads the renderer via
`win.loadURL(pathToFileURL(resolveRendererIndex()))` — a `file://` URL on local
disk. There is no network download. "Smaller bundle for faster download," the
usual reason to code-split, does not apply.

What actually costs time at startup is V8 parse and top-level module evaluation.
V8 parses function bodies lazily (only on first call), so the full 28.5MB is not
parsed eagerly. Code-splitting does not reduce the parse/eval of code that is
eventually used — it only defers it — and here everything ships in one file
anyway.

### mermaid is already deferred

`embeds/registry.tsx` loads it as `lazy(() => import('./mermaid-embed'))`, so
mermaid's module evaluation is deferred until a mermaid embed actually renders,
regardless of chunking. Nothing to do.

### xterm is eager, but the win is unverified and not free

`@xterm/xterm` plus four addons (fit, unicode11, web-links, webgl) are imported
statically through `PersistentTerminal` in `wiring.tsx`, so their module
evaluation runs at startup even before a terminal opens. Deferring that with
`React.lazy` would help **only if** xterm's top-level evaluation is meaningfully
expensive — and it likely isn't: the `Terminal` class is cheap to import, and the
WebGL work happens in `WebglAddon.activate()`, i.e. at `new Terminal()` time, which
already only runs when a terminal is opened.

Making `PersistentTerminal` lazy also adds a Suspense boundary and changes mount
timing for a component wired into the app shell. That is behavior-adjacent risk
for a saving nobody has measured. Not worth it without a profile showing xterm
module-eval as a real startup cost — and `codeSplitting: false` means it wouldn't
even become a separate chunk, only a deferred evaluation within the one bundle.

## Verified

- `codeSplitting: false` and the rationale — `vite.config.ts:55-67`.
- Production renderer loads from `file://` — `electron/main.ts:8525`.
- mermaid lazy, xterm eager — import grep across `src/`.
- Build produces one 28.5MB chunk — `vite build` output.

**Not verified:** the actual startup parse/eval time of the renderer bundle, and
xterm's share of it. No profile was taken. The decision to not split rests on the
Electron-loads-from-disk fact plus the documented OOM risk, not on a startup
measurement — so if someone later profiles renderer boot and finds xterm eval is
in fact expensive, the terminal-lazy option is worth revisiting (it does not need
code-splitting to work).

## Follow-ups

- If renderer cold-boot is ever profiled and xterm/webgl module evaluation shows
  up, lazy-mount `PersistentTerminal` behind Suspense. It defers eval without
  enabling code-splitting, so it does not risk the electron-builder OOM.
- The 28.5MB is worth watching as the regression alarm it was set up to be, but
  it is not itself a load-time problem for a disk-loaded renderer.

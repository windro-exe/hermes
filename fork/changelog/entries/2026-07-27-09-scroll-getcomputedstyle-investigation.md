# Scroll library getComputedStyle: real, but blocked on a patch-package decision

**Date:** 2026-07-27
**Type:** Docs
**Branch:** `perf/ui-latency`

<!-- No code change. The fix requires patching a third-party dep, which needs an
     infra decision (patch-package) that is windro's call. Ready patch below. -->

## Why

`use-stick-to-bottom` (1.1.6) is the single scroll owner for the transcript
(`list.tsx:178`). Two spots call `getComputedStyle`, which forces a style
recalculation:

- **`useStickToBottom.js:85`** — the `scrollTop` setter reads
  `getComputedStyle(scrollRef.current).scrollBehavior` on **every programmatic
  scroll write**. During streaming the spring animation writes `scrollTop` each
  frame, so this runs every frame while tokens arrive — the streaming path.
- **`useStickToBottom.js:285`** — the wheel handler walks up from the event
  target calling `getComputedStyle(element).overflow` until it finds a scrollable
  ancestor. For a deeply nested target (a span inside markdown inside a message)
  that is several `getComputedStyle` calls per wheel event, during manual
  scrolling. In this app the answer is always `scrollRef.current` — it is the
  single scroll owner — so the walk is avoidable entirely.

## Why there is no code change

`node_modules` is gitignored, so editing the installed file is not part of the
fork and is wiped by the next `npm install`. The project uses **npm**
(`package-lock.json`, no `packageManager` field), which has no built-in patch
mechanism. Persisting a dependency patch requires **patch-package**:

- a new `devDependency`,
- a `"postinstall": "patch-package"` script in `package.json` — an **upstream
  file**, so a merge-conflict surface, and a hook that runs on every install,
- a `patches/use-stick-to-bottom+1.1.6.patch` file.

That is a standing infrastructure change to how installs work. Per windro's
workflow (pause at genuine decision points; minimal upstream-file touches), it is
his call, not an autonomous edit. Flagged, not applied.

## Honest scope of the win

Partial, even if patched. The `scrollTop` setter forces layout regardless of the
`getComputedStyle` read: line 89 writes `scrollTop`, line 90 immediately reads it
back (`state.ignoreScrollToTop = scrollRef.current.scrollTop`), and a read after a
layout-affecting write forces a synchronous layout on its own. Removing the
`getComputedStyle` at line 85 removes one forced style-flush per frame but not the
layout from the readback. The wheel-handler walk (285) is the cleaner, fuller win
because the whole ancestor scan is redundant here.

Also note the two style *writes* the setter would otherwise do (lines 87, 91-92)
are already skipped in practice: `scroll-behavior` defaults to `auto`, and the app
only forces `auto` harder under `prefers-reduced-motion`. So the CSS side is
already fine; only the reads remain.

## Not verified

No in-app profile of scroll or streaming frames was taken. The claim that these
`getComputedStyle` calls force per-frame style/layout rests on how the browser
treats `getComputedStyle` after DOM mutation (streaming appends nodes constantly,
dirtying layout), not on a measured frame timeline for this app. Before adopting
patch-package, it is worth one DevTools Performance capture during streaming to
confirm these show up as forced reflows and are worth the infra.

## Ready patch (for if windro says yes to patch-package)

Both edits target `node_modules/use-stick-to-bottom/dist/useStickToBottom.js`:

1. **Setter (line ~85)** — cache the container's `scrollBehavior` and only
   re-read when the element identity changes, or skip the read when
   `scrollRef.current.style.scrollBehavior` is already `"auto"`:

   ```js
   // was: const { scrollBehavior } = getComputedStyle(scrollRef.current);
   const inlineBehavior = scrollRef.current.style.scrollBehavior;
   const scrollBehavior = inlineBehavior || getComputedStyle(scrollRef.current).scrollBehavior;
   ```

2. **Wheel handler (line ~285)** — this app has a single scroll owner, so the
   ancestor walk can short-circuit to `scrollRef.current`:

   ```js
   let element = scrollRef.current ?? target;
   ```

   (The upstream walk exists to support arbitrary nested scrollers; the fork can
   assert its own single-owner invariant. Note this narrows behaviour, so it must
   be a fork patch, not sent upstream.)

## Follow-ups

- Decision for windro: adopt `patch-package`? It unlocks this and any future dep
  fix, at the cost of a postinstall hook and one upstream `package.json` line.
- If yes, profile streaming first (one capture) to confirm the win, then apply
  the two edits above as `patches/use-stick-to-bottom+1.1.6.patch`.
- Upstream option: `use-stick-to-bottom` could take a "scroll element is known"
  fast path for the wheel handler. Worth an issue regardless.

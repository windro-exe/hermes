# Scroll jank fixed: patch-package + use-stick-to-bottom getComputedStyle removal

**Date:** 2026-07-27
**Type:** Performance
**Branch:** `perf/ui-latency`

**Supersedes** `entries/2026-07-27-09-scroll-getcomputedstyle-investigation.md` — that
entry documented the problem and recommended a patch-package decision. windro
approved it ("fix the scroll issue, make it smooth and butter"), so this is the
applied fix.

<!-- Commit sha omitted: ships in the commit it describes. -->

## Why

`use-stick-to-bottom` 1.1.6 is the transcript's single scroll owner
(`list.tsx:178`). Two spots forced synchronous style/layout via `getComputedStyle`:

- The `scrollTop` setter read `getComputedStyle(el).scrollBehavior` on **every
  programmatic scroll write** — every animation frame during streaming autoscroll.
- The wheel handler walked up from the event target calling
  `getComputedStyle(el).overflow` per ancestor level, on **every wheel event**, to
  find the scroll container.

Both force the browser to flush pending style/layout. During streaming the DOM is
being mutated constantly (new tokens), so those reads repeatedly triggered real
reflows on the frame path — the "not butter" feel.

## What changed

### Adopted patch-package

`node_modules` is gitignored and this repo uses npm (no built-in patch support),
so a dependency edit could not otherwise be part of the fork. Added `patch-package`:

- **`package.json`** (root) — added to `devDependencies`, and the root
  `postinstall` now runs `patch-package || true` before its existing echo. The
  `|| true` keeps a devDep-less install from ever failing the hook; a patch that
  stops applying still prints patch-package's own loud warning.
- **`patches/use-stick-to-bottom+1.1.6.patch`** (new) — the committed patch,
  reapplied automatically after every `npm install`.

### The two edits (in the patch)

- **Setter** — a module-level `WeakMap` caches the container's computed
  `scroll-behavior` per element. `scroll-behavior` does not change at runtime for
  the transcript, so the cached value is identical to what `getComputedStyle`
  would return; the forced flush is gone. First call still reads once and stores.
- **Wheel handler** — short-circuits `element` to `scrollRef.current` (the element
  the wheel listener is bound to, and this app's single scroll owner) instead of
  the `getComputedStyle` ancestor walk. The subsequent escape-lock guard
  (`element === scrollRef.current`) is unchanged, so behaviour is identical for
  this app. This narrows the library's general nested-scroller support to the
  app's invariant, so it is a fork patch and explicitly must not go upstream.

## Verified

```bash
npx patch-package                 # -> use-stick-to-bottom@1.1.6 ✔ (applies clean)
# reapplied a second time: still clean (idempotent, the postinstall path)
cd apps/desktop
npx vitest run --project ui src/components/assistant-ui/thread/list.test.ts
# -> 12 passed  (the component that consumes the hook still works)
npx vitest run --project ui src/__fork__/    # -> 10 passed
```

Two fork guards added (`src/__fork__/perf-guards.test.ts`): the patch file exists
and covers both edits, and the root `postinstall` runs `patch-package` with it in
`devDependencies`. These catch the patch being lost on a merge or install.

Patch correctness: the setter cache returns the same value the code read before
(scroll-behavior is static for this element); the wheel short-circuit resolves to
the same element the walk always terminated at, since the listener is bound to
`scrollRef.current` and this app has one scroll owner (confirmed by the code's own
"single scroll owner" comment in `list.tsx`).

**Not verified:**
- No before/after DevTools frame capture during streaming. The fix provably
  removes two forced style reads from the frame path; the felt smoothness
  improvement is inferred from that, not measured on this machine. A Performance
  capture during a long streaming response would confirm the reflow count drop.
- The setter still writes `scrollTop` and reads it back (line 90 upstream), which
  forces layout regardless — so this removes one flush per frame, not all layout.
  The wheel-handler walk removal is the cleaner, fuller win.

## Risk / watch for

- **The patch is pinned to 1.1.6.** If the dependency is bumped, patch-package
  prints a loud failure and the edits silently stop applying (jank returns). The
  guard test catches a lost patch *file*, not a version mismatch — watch for the
  patch-package warning on install after any bump, and regenerate.
- **`|| true` in postinstall** masks a missing patch-package binary (intended) but
  would also mask patch-package itself crashing. Patch-apply failures still warn
  via patch-package's own output, which is not suppressed.
- **The `WeakMap` cache assumes scroll-behavior never changes for the container.**
  True today (nothing sets it at runtime). If a theme ever animates
  scroll-behavior on the transcript, the cache would serve the old value — but
  that would be an unusual thing to animate.

## Follow-ups

- Upstream issue worth filing: `use-stick-to-bottom` could take a "scroll element
  is known" option to skip the wheel-handler walk, and cache scroll-behavior
  itself. The patch narrows behaviour for this app; a general fix belongs upstream.
- If scroll is profiled later and the `scrollTop` readback (line 90) shows as the
  dominant reflow, that is the next thing to address — but it cannot be removed
  without changing the library's escape-detection logic.

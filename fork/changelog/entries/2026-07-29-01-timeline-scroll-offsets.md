# Scroll jank: the timeline tracker was measuring every message on every frame

**Date:** 2026-07-29
**Type:** Performance
**Branch:** `perf/ui-latency`

<!-- Commit sha omitted: ships in the commit it describes. -->

## Why

windro: "can we make the scrolling super smooth". The earlier scroll work
(`2026-07-27-10`, the `use-stick-to-bottom` patch) removed two `getComputedStyle`
calls but was never measured in the app — the entry said so explicitly. So this
round started by building a measurement instead of guessing again.

**Measured, production renderer, 200-turn transcript (400 messages):**

| | before |
|---|---|
| average fps | **21** |
| frame p50 | 34.4ms |
| frame p95 | 52.3ms |
| frame p99 | 930.5ms |
| frames over 16.7ms | **235 of 235** |
| frames over 33ms | 141 |
| longtasks | 6, max 982ms |

Every single frame was dropped. A CPU profile of that scroll pointed straight at
the cause:

```
  3841.1 ms  23.9%  getBoundingClientRect
  3654.4 ms  22.7%  querySelector
```

**46.6% of all sampled time**, both from one call site:
`thread/timeline.tsx`, the conversation-timeline tracker that decides which user
prompt is currently active.

Its scroll handler did this per frame:

```ts
const top = viewport.getBoundingClientRect().top
const offsets = entries.map(entry => {
  const node = viewport.querySelector(`[data-message-id="${CSS.escape(entry.id)}"]`)
  return node ? node.getBoundingClientRect().top - top : null
})
```

That is `1 + 2N` layout operations per frame — with 200 user prompts, 200
`querySelector` calls plus 200 forced reflows, ~47,000 of each across the
measured scroll.

Upstream knew about this hazard. The comment above it says the walk "reads a rect
per user message per scroll frame ... each read forces a full reflow (the single
hottest frame in the multitab profile)", and there is a fast path that skips it
when `data-following === 'true'`. But that only covers the pinned-to-bottom
streaming case. **The moment you scroll up, `following` flips to `false` and the
full walk runs on every frame** — so the cost landed on exactly the manual
scrolling a user does.

## What changed

- **`apps/desktop/src/components/assistant-ui/thread/timeline.tsx`** — the
  offsets are now cached and derived arithmetically.

  The insight: the value wanted is `node.rect.top - viewport.rect.top`, and
  during a scroll `node.rect.top` changes by exactly `-Δ scrollTop` while the
  viewport's own rect stays put. So the whole set can be measured **once** as
  `rect.top - viewportTop + scrollTop` and then derived on any later frame as
  `cached - scrollTop`. No layout reads at all in the steady state.

  The one-off build uses a single `querySelectorAll('[data-message-id]')`
  traversal instead of N individual lookups. `null` keeps its original meaning
  (message not in the DOM, outside the render budget), which
  `activeTimelineIndex` already skips.

  Cache invalidation is on `viewport.scrollHeight` changing, which is what
  happens when the render budget mounts more messages, a tool block expands, or
  an image loads. Steady-state scrolling never rebuilds. The
  `data-following` fast path is untouched.

- **`apps/desktop/scripts/perf/scenarios/scroll.mjs`** (new) — the missing
  measurement. Upstream's `transcript` scenario measures mount cost only; nothing
  measured scrolling. Registered in `scenarios/index.mjs` (two added lines).

## Verified

Same scenario, production renderer, before and after:

| metric | before | after | change |
|---|---|---|---|
| average fps | 21 | **44.4** | 2.1x |
| frame p50 | 34.4ms | **18.7ms** | 46% faster |
| frame p95 | 52.3ms | **32.6ms** | 38% better |
| frame p99 | 930.5ms | **44.4ms** | 95% better |
| frames over 16.7ms | 235 | **150** | 36% fewer |
| frames over 33ms | 141 | **10** | 93% fewer |
| longtasks | 6 | **2** | 67% fewer |

Frame histogram tells it best — before, **not one frame** came in under 16.7ms:

```
before  <=16.7: 0    16.7-33: 94    33-50: 119   50-100: 19   >200: 3
after   <=16.7: 85   16.7-33: 140   33-50: 8     50-100: 1    >200: 1
```

```bash
cd apps/desktop
npx tsc -p . --noEmit                                          # -> clean
npx vitest run --project ui src/components/assistant-ui/thread/ # -> 74 passed
npx vitest run --project ui src/__fork__/                       # -> 16 passed
```

Three guards added, mutation-checked: restoring the per-frame `querySelector`
fails the guard (1 failed / 15 passed), and the fix restores 16/16.

**Two false starts worth recording, because both produced numbers that looked
great and were meaningless:**

1. The first scenario drove scrolling with `Input.dispatchMouseEvent` type
   `mouseWheel`. That **does not scroll the container at all** — it reported
   165fps with zero dropped frames because the page was idle. Caught only because
   the detail output showed `scrolledPx: 0`.
2. `Input.synthesizeScrollGesture` was tried next and **hung**, taking the
   harness instance down with it.

The scenario now drives `scrollTop` from a rAF loop in the page and dispatches
`wheel` events alongside it, **and fails loudly if the container did not move**.
That guard is the only reason the second false start was caught rather than
committed.

**Not verified:**
- The scenario measures the app's per-frame cost, not the browser's
  compositor-thread scrolling. Real wheel input has momentum and compositor
  hand-off this does not reproduce. It is a fair relative measure, not an
  absolute fps a user sees.
- 44fps is better, not perfect: 150 of 235 frames are still over 16.7ms and p50
  is 18.7ms (~53fps). Something else is still costing ~2-3ms per frame. Not
  chased in this round.
- Synthetic transcript content, not windro's real sessions (no Shiki-highlighted
  code, no KaTeX, no tool blocks). Real content is likely heavier.

## Risk / watch for

- **The cache can go stale if content changes height without changing
  `scrollHeight`.** Possible in principle (a swap of equal-height content) and
  would leave the active tick pointing at the wrong prompt until the next
  height change. The tick is a coarse indicator, so the failure is cosmetic.
- **It assumes the viewport's own position is fixed during a scroll.** True here;
  a layout that moved the viewport mid-scroll (window resize during scroll) would
  need a rebuild that the `scrollHeight` check would not catch.
- **The guards are source-level.** Removing the cache changes no behaviour, only
  speed, so nothing else in the suite would fail — a source assertion is the only
  option, and it will trip on a rename or reformat.

## Follow-ups

- ~2-3ms of per-frame cost remains unaccounted for. A fresh CPU profile of the
  post-fix scroll is the next step, and now cheap to run:
  `npm run perf -- scroll --spawn --prod --cpuprofile`.
- The same "measure every message per frame" shape is worth grepping for
  elsewhere — anything reading rects inside a scroll or streaming handler.
- Upstream would probably take the cache: the hazard comment is already there, so
  they understand the problem. Worth offering.

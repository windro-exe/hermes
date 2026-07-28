# Streaming code-card glow: animate opacity, not box-shadow

**Date:** 2026-07-27
**Type:** Performance
**Branch:** `perf/ui-latency`

<!-- Commit sha intentionally omitted: this entry ships in the commit it
     describes. Find it with: git log --oneline -- <path to this file> -->

## Why

While a code block streams, its card runs a "breathing" glow —
`code-card-stream-glow`, `1.8s ease-in-out infinite alternate`. It animated
`box-shadow` and `border-color`, and each keyframe end resolved four
`color-mix()` values.

Neither `box-shadow` nor `border-color` can be animated on the compositor. Every
frame of the breathe repaints the card and its shadow region. This runs for the
entire duration of a streaming code block — i.e. continuously, during the exact
moment the renderer is already busiest laying out and painting incoming tokens.
It is the one always-on paint-animation in the transcript.

This was the only CSS effect in the phase safe to change without altering the
design. The others were checked and deliberately left alone (see below).

## What changed

- **`apps/desktop/src/styles.css`** — the breathing is now an opacity cross-fade
  between two static layers:
  - The card holds the **dim** end of the glow (static `box-shadow` +
    `border-color`).
  - A new `::after` pseudo-element holds the **bright** end (static
    `box-shadow` + a `border`), positioned at `inset: -0.0625rem` so its border
    box lines up with the card's outer edge and overlays the card's border ring
    rather than drawing a second line inside it.
  - The keyframes went from animating two paint properties to `opacity: 0 → 1`.
    Opacity on a promoted layer is compositor-only — the same visual, zero
    per-frame repaints. `will-change: opacity` promotes the layer.

  The `prefers-reduced-motion` opt-out was extended to the `::after` (the
  existing rule only reached the card), holding it at `opacity: 1` so a
  streaming block still reads as active without motion.

## Verified

Rendered the old and new treatments side by side in a headless browser
(dim/bright ends as static frames) and compared: **visually identical** at both
ends of the breathe. Screenshot taken and inspected, not just reasoned about.

```bash
cd apps/desktop && npx vite build --mode production   # -> built, css 319 kB
```

Fork guard added (`src/__fork__/perf-guards.test.ts`, now 8 tests) asserting the
keyframe animates `opacity` and neither `box-shadow` nor `border-color`.
Mutation-checked: reverting the keyframe to a `box-shadow` animation fails it
(1 failed / 7 passed), restoring passes 8.

**Not verified:**
- No DevTools paint-flashing or frame-time capture in the running app. The claim
  that this removes per-frame repaints rests on the CSS fact that `box-shadow`
  and `border-color` are paint properties while `opacity` is compositor-only, not
  on a measurement of this app.
- The visual check was two static frames (the keyframe ends), not the animated
  transition. The in-between opacity values are a linear cross-fade; the old
  in-between was an `ease-in-out` interpolation of shadow blur/spread. The
  easing curve differs slightly mid-breathe, though both ends match and the
  timing function is unchanged.

## Rejected (left alone deliberately)

- **`quest-glow`** — the amber "needs you" pulse on a clarify-blocked session
  dot. Also animates `box-shadow`, but it animates `transform: scale()` in the
  same keyframe (the dot grows as it glows), so splitting the glow onto a
  pseudo-element would decouple the two halves of one visual gesture. It is a
  ~6px dot, one at a time, not in the transcript paint path. Not worth it.

- **The composer `backdrop-filter: blur()`** — `composerSurfaceGlass` in
  `composer-dock.ts`. A backdrop blur over the transcript is genuinely expensive:
  it re-samples the blurred region when content behind it changes, i.e. on every
  streaming flush. **But removing it changes how the app looks** — the composer's
  frosted-glass surface is a deliberate design choice, not an accident. Silently
  flattening it to a solid fill is not a perf fix I get to make unilaterally.
  Flagged here for windro to decide; untouched.

## Risk / watch for

- **Two static values must stay in sync with intent.** The dim end lives on the
  card rule, the bright end on the `::after` rule. Retuning the glow means
  editing both, not one keyframe. The rules carry a comment saying so.
- **`inset: -0.0625rem` assumes the card's border is `0.0625rem` (1px).** If the
  card's border width changes, the `::after` border ring will sit slightly
  inside or outside the card edge. Cosmetic, but that is the coupling.
- **The guard is source-level** — it greps the keyframe body in `styles.css`. A
  reformat or rename fails it spuriously, but removing the optimization changes
  no behaviour and breaks no other test, so a source guard is the only option.

## Follow-ups

- The composer backdrop-blur is the real remaining CSS cost during streaming, and
  it is a product-design call. If windro wants it addressed: options are a solid
  fill (cheapest, changes the look), blurring only when the composer is docked
  over content, or gating the blur behind a "reduce transparency" preference.

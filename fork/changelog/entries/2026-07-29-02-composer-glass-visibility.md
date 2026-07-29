# Composer glass was invisible: dark-on-dark needs a brightness lift, not just alpha

**Date:** 2026-07-29
**Type:** Fixed
**Branch:** `perf/ui-latency`

**Corrects** `entries/2026-07-28-02-composer-glass-setting.md`. That entry added
the lever and verified the CSS mechanism, but its "Not verified" section admitted
nobody had checked the result in the running app. windro did, and reported: "the
blur slider does nothing, I'm actively sliding it but I see no visible blur or
glassy effect." He was right.

**Note on history:** the code for this fix was committed together with the
timeline scroll fix (`1c8009b41`) because of a `git add -A`, so that commit
contains both. This entry documents the glass half.

## Why the slider looked broken

The wiring was never the problem. Verified in the running structure — computed
styles changed exactly as designed:

| lever | fill | backdrop-filter |
|---|---|---|
| 0 | opaque, no alpha | `blur(0px)` |
| 30 | alpha 0.865 | `blur(12.8px)` |
| 100 | alpha 0.55 | `blur(24px)` |

The problem is that this had **almost nothing to show**:

1. **The palette defeats alpha.** `--dt-card` is `#1b1e24` and
   `--dt-background` is `#14161a`. Mixing one over the other at *any* alpha
   lands on nearly the same pixel. Screenshot diff across the entire lever range
   (fully opaque → 55%) measured a mean difference of **2.63 / 255**. Invisible.
2. **A blur needs contrast behind it.** At rest the composer sits over flat
   background, so there is nothing to blur. Only when the thread is scrolled up
   does content pass under it — which is exactly why the original design dipped
   the scrolled-up state to 48%.

So the previous default (30 → 86.5% opaque) was both more opaque than the old
look *and* had no visible frosting. Sliding it changed a couple of pixel values.

## What changed

- **`apps/desktop/src/store/composer-glass.ts`** — the lever now drives two more
  variables, and the alpha range is wider:

  | lever | fill | scrolled | blur | brightness | saturate |
  |---|---|---|---|---|---|
  | 0 | 100% | 100% | `0` | `1` | `1` |
  | 30 (default) | 82% | 74.5% | 0.92rem | 1.135 | 1.18 |
  | 100 | 40% | 15% | 1.9rem | 1.45 | 1.6 |

  `backdrop-brightness` is what makes it visible. Lifting the brightness of
  whatever is behind the panel makes it read as frosted glass *against its
  surroundings* even when what's behind is flat colour — which is the common
  case here. Alpha alone could never do that with this palette.

  At lever 0 every multiplier is exactly `1` and blur is `0`, so "solid" means
  the backdrop is genuinely untouched and the filter does no work at all.

- **`apps/desktop/src/components/chat/composer-dock.ts`** — the filter now reads
  all three vars (`blur`, `saturate`, `brightness`) in both the Tailwind
  shorthand and the `-webkit-` arbitrary property.

- **`apps/desktop/src/styles.css`** — `:root` defaults updated to match
  `composerGlassVars(30)` exactly.

## Verified

Rebuilt a faithful replica of the app's real composer stack — `composer-root` →
fade layer → wrapper → `surface` (`isolate`, `z-4`) → glass (`-z-10`, fill +
backdrop-filter) — with transcript content genuinely overlapping the composer,
then measured screenshots with PIL.

Composer-band average luminance across the lever:

```
              lever 0    lever 30   lever 100
before          38.0       39.9       44.6
after           38.0       42.2       57.2
```

Full-range pixel difference (lever 0 → 100): **2.63 → 4.80 mean**, and the
adjacent step 30 → 100 went **0.95 → 2.75** (2.9x). The band luminance is the
meaningful number: a 38 → 57 lift is plainly visible, where 38 → 45 was not.

```bash
cd apps/desktop
npx tsc -p . --noEmit                                       # -> clean
npx vite build --mode production                            # -> built
npx vitest run --project ui src/store/composer-glass.test.ts # -> 9 passed
```

All five custom properties confirmed in the built CSS, including
`-webkit-backdrop-filter:blur(var(--composer-glass-blur)) saturate(var(--composer-glass-saturate)) brightness(var(--composer-glass-brightness))`.

A test was added asserting brightness rises monotonically and is exactly `1` at
lever 0 — the specific thing whose absence made the slider look broken.

**Also worth recording:** the first replica of this measurement was wrong. Its
transcript was only 6 lines at the top of the box, so nothing was behind the
composer at all, and the "luminance_sd" figures it produced were measuring the
composer's own label text. The numbers above come from the corrected version,
where a JS probe confirms `transcriptBottom > surfaceTop` before measuring.

**Not verified:**
- Not checked in light mode or against other themes. Light palettes have more
  contrast between card and background, so alpha does more work there and the
  brightness lift may read as too strong. Worth a look if windro uses light mode.
- The measurement is a faithful replica, not the running app. The structure,
  class semantics and computed values match, but it is still a replica.
- No measurement of what the brightness filter costs. `backdrop-filter` with
  three functions is more work than one; lever 0 remains the free option.

## Risk / watch for

- **The `:root` defaults duplicate `composerGlassVars(30)`.** Change the mapping
  or the default and those five literals in `styles.css` must move with it, or a
  fresh profile paints with stale values until the store's first write.
- **`brightness` above 1 lightens anything behind the composer**, including
  transcript text that passes under it while scrolled up. At lever 100 (1.45)
  that is a deliberate frosted look; someone who wants a neutral panel should sit
  near the low end.
- **Three filter functions instead of one.** If the composer ever becomes a
  measured cost during streaming, this is the place to look, and lever 0 disables
  it entirely.

## Follow-ups

- If it still reads flat to windro, the honest next lever is the *fill colour*
  rather than its alpha — a composer surface that is a visibly different hue from
  the background would show glass at any alpha. That is a design change, not a
  tuning one.
- The scrolled-up state now reaches 15% at lever 100, which is very transparent.
  Worth checking it stays readable with real content behind it.

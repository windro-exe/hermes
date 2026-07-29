# Composer glass: less transparent by default, with a user-adjustable lever

**Date:** 2026-07-28
**Type:** Changed
**Branch:** `perf/ui-latency`

**Resolves the open question from**
`entries/2026-07-27-06-code-card-glow.md`, which flagged the composer
`backdrop-filter` as a real per-flush cost but deliberately left it alone because
removing it changes how the app looks. windro's call: "reduce the transparency but
keep it glassy by adding blur, and make it a settings option the user can adjust."

<!-- Commit sha omitted: ships in the commit it describes. -->

## Why

Two problems with the old hard-coded composer surface:

1. **Too transparent.** The fill was `72%` card at rest and dropped to **`48%`**
   once the thread scrolled up — so more than half transparent exactly when there
   was content behind it to bleed through.
2. **`backdrop-filter` is not free.** Blurring the backdrop over the transcript
   means the blurred region is re-sampled whenever content behind it changes —
   during streaming, that is every flush. It was a fixed `0.75rem` blur with no
   way to turn it off.

Rather than pick one compromise for everyone, this exposes the tradeoff as a
single lever and moves the default toward opaque.

## What changed

### The lever

- **`apps/desktop/src/store/composer-glass.ts`** (new) — a 0–100 store following
  the exact shape of `store/translucency.ts` (versioned localStorage key, clamp,
  `atom` with SSR guard, `subscribe` → persist + apply). Default **30**.

  `composerGlassVars(level)` maps the lever to three CSS values. It is exported
  because the mapping is the part worth pinning:

  | lever | rest fill | scrolled-up fill | blur |
  |---|---|---|---|
  | 0 | 100% | 100% | `0` (filter off) |
  | 30 (default) | 86.5% | 79.3% | 0.8rem |
  | 100 | 55% | 31% | 1.5rem |
  | *(old hard-coded)* | *72%* | *48%* | *0.75rem* |

  At **0 both** strengths reach 100% — a "solid" setting that still went
  translucent on scroll would be a bug — and the blur is emitted as `0`, not
  `0rem`, so the filter is a genuine no-op. The default is more opaque than the
  old look at both states while blurring slightly *harder*, which is what keeps
  it reading as frosted glass rather than flat.

- **`apps/desktop/src/main.tsx`** — side-effect import so the persisted level is
  applied before first paint. nanostores' `subscribe` fires immediately with the
  current value, which is what makes the bare import sufficient (same trick
  `translucency` uses).

### Wiring it to the surface

- **`apps/desktop/src/styles.css`** — the `--composer-fill` ladder now reads
  `var(--composer-fill-strength)` / `var(--composer-fill-strength-scrolled)`
  instead of literals, with the lever-30 values as `:root` defaults so the vars
  are always defined. The ladder itself is untouched — it stays the single source
  of truth for *how* the fill is composed; the store only feeds it numbers.

- **`apps/desktop/src/components/chat/composer-dock.ts`** — the blur radius comes
  from `--composer-glass-blur` via Tailwind's
  `backdrop-blur-(--composer-glass-blur)` shorthand (already used in this file for
  `bg-(--composer-fill)`), plus the matching `-webkit-` arbitrary property. No
  comma-fallback inside the arbitrary value — Tailwind mangles those — hence the
  `:root` default.

### The setting

- **`apps/desktop/src/app/settings/appearance-settings.tsx`** — a `ListRow` with a
  0–100 range input, step 5, live percentage readout and haptic feedback,
  immediately after Window Translucency. Copied structurally from the
  translucency row so it matches.

- **`apps/desktop/src/i18n/en.ts` + `types.ts`** — `composerGlassTitle` /
  `composerGlassDesc`. Also **`zh.ts`**, because `en` and `zh` are typed as full
  `Translations` while `ar`/`ja`/`zh-hant` go through `defineLocale`, which
  deep-merges partial overrides onto `en` — so only those two needed the keys.

## Verified

Rendered old / lever 0 / lever 30 / lever 100 side by side in a headless browser
over a mock transcript (prose + a code block) and inspected the screenshot:

- **old** — text and the code block clearly legible through the surface
- **lever 0** — fully solid, nothing shows through, no blur
- **lever 30** — noticeably more opaque than old, still visibly frosted
- **lever 100** — most transparent, strongest blur

Both filter properties confirmed present in the built CSS (Tailwind composes the
standard one through its var chain):

```
--tw-backdrop-blur:blur(var(--composer-glass-blur))
-webkit-backdrop-filter:blur(var(--composer-glass-blur)) saturate(1.12)
--composer-fill-strength:86.5%   --composer-fill-strength-scrolled:79.3%
--composer-glass-blur:.8rem
```

Lightning CSS also emitted an opaque `--composer-fill:var(--dt-card)` fallback
outside the `@supports (color-mix)` guard, so a browser without `color-mix` gets
a solid composer rather than a broken one.

```bash
cd apps/desktop
npm run typecheck                                      # -> clean (3 tsconfigs)
npx vite build --mode production                       # -> built, css 320 kB
npx vitest run --project ui src/store/composer-glass.test.ts   # -> 8 passed
npx vitest run --project ui                            # -> 1 failed | 2546 passed | 1 skipped
```

The single failure is the pre-existing `en-IN` locale bug documented in entry 05
(`toLocaleString` gives `12,34,567`, the test hardcodes `1,234,567`) — unrelated,
and present before this change.

8 tests cover the mapping: opaque-with-no-blur at 0, more-opaque-than-old at the
default, monotonic across the range, scrolled-never-more-opaque-than-rest,
clamping of out-of-range/fractional input, no negative percentages, all three
properties written to the target, and no throw without a document.

**Not verified:**
- No measurement of the actual paint cost saved at lever 0 versus 30. The claim
  that dropping the filter is cheaper rests on how `backdrop-filter` works, not on
  a profile of this app. If windro wants the perf win he should try 0 and compare
  by feel — that is the honest instruction.
- Not checked in light mode or against every theme. The lever scales
  `--dt-card` mixes, so it should follow any theme, but only the dark palette was
  rendered.
- The persisted value round-trip was tested at the store level, not by launching
  the app, changing the slider, and restarting.

## Risk / watch for

- **The `:root` defaults duplicate `composerGlassVars(30)`.** If the mapping or
  the default level changes, those three literals in `styles.css` must be updated
  to match, or a fresh profile (before the store's first write) will paint with
  stale values. The CSS carries a comment saying so.
- **`--composer-fill` has other consumers** — the slash/`@` completion drawer and
  the `?` help popover paint the same var, and the `:has()` rule that forces an
  opaque fill while a drawer is open is untouched. Those follow the lever
  automatically, which is intended, but it means the lever affects more than the
  input box.
- **Tailwind's `backdrop-blur-(--var)` shorthand** is what emits the standard
  property. If that class is ever rewritten to a bracket form with a comma
  fallback it will silently stop generating, leaving only the `-webkit-` rule.
- **The scrolled-up dip scales with the lever**, so it is not a fixed 24-point
  drop like the original. At low levels the two states are nearly identical by
  design; someone expecting the old dramatic dip on scroll will need a higher
  lever.

## Follow-ups

- The lever is a single number driving three values. If windro wants independent
  control (opaque fill *and* heavy blur, say) it would need to split into two
  settings — worth doing only if the coupled mapping feels wrong in use.
- `quest-glow` still animates `box-shadow` (entry 06 explains why it was left).
  Unrelated to this, but it is the last paint-animating effect left.

/**
 * Composer glass — how see-through the composer surface is, and how hard the
 * backdrop is blurred behind it.
 *
 * One lever, 0–100.
 *
 *   0   = solid. Opaque fill, blur switched off entirely.
 *   30  = default. Noticeably more opaque than the old hard-coded look, with a
 *         slightly stronger blur so it still reads as frosted glass.
 *   100 = maximum glass. Most transparent, strongest blur.
 *
 * Why this is a setting and not a fixed value: `backdrop-filter` over the
 * transcript is not free — the blurred region is re-sampled whenever content
 * behind it changes, which during streaming is every flush. The old fill was
 * also quite transparent (72% card at rest, 48% once the thread scrolled up),
 * which made text behind it bleed through. Rather than pick one compromise, the
 * lever exposes the tradeoff: 0 is the cheapest and most readable, 100 is the
 * prettiest.
 *
 * Applied as CSS custom properties on the document root rather than in JS style
 * writes, so the composer's existing `--composer-fill` ladder in styles.css
 * stays the single source of truth for *how* the fill is composed — this only
 * feeds it numbers.
 */

import { atom } from 'nanostores'

import { persistString, storedString } from '@/lib/storage'

const KEY = 'hermes.desktop.composerGlass.v1'

/** Default lever position. Lower than the old look on purpose (windro's ask:
 *  less transparent, keep it glassy). */
export const COMPOSER_GLASS_DEFAULT = 30

const clamp = (n: number): number => Math.min(100, Math.max(0, Math.round(n)))

const read = (): number => {
  const raw = storedString(KEY)

  if (raw === null || raw === '') {
    return COMPOSER_GLASS_DEFAULT
  }

  const n = Number(raw)

  return Number.isFinite(n) ? clamp(n) : COMPOSER_GLASS_DEFAULT
}

export interface ComposerGlassVars {
  /** `--composer-glass-blur`: backdrop blur radius. `0` disables the filter. */
  blur: string
  /**
   * `--composer-glass-brightness`: backdrop brightness multiplier.
   *
   * This is what makes the effect actually *visible*. Measured on the real
   * palette: `--dt-card` (#1b1e24) and `--dt-background` (#14161a) are nearly
   * the same colour, so mixing card over background at any alpha barely changes
   * a pixel — a screenshot diff between a fully opaque composer and a 55% one
   * came out at a mean of 2.63/255. Blur alone is invisible too, because a blur
   * needs contrast behind it and at rest the composer sits over flat
   * background. Lifting the backdrop's brightness makes the panel read as
   * frosted glass against its surroundings even with nothing behind it.
   */
  brightness: string
  /** `--composer-glass-saturate`: backdrop saturation, lifted with the lever. */
  saturate: string
  /** `--composer-fill-strength`: card percentage in the resting fill. */
  strength: string
  /** `--composer-fill-strength-scrolled`: card percentage once the thread is
   *  scrolled up, where the original design dipped more transparent. */
  strengthScrolled: string
}

/**
 * Map the 0–100 lever to the three CSS values.
 *
 * Exported for tests — the mapping is the part worth pinning, since the numbers
 * have to stay monotonic and reach exactly opaque at 0.
 *
 * - strength falls 100% → 55% across the range.
 * - the scrolled-up state dips further as the lever rises, so at 0 BOTH states
 *   are fully opaque (a "solid" setting that still went translucent on scroll
 *   would be a bug), while at the top it still has the original design's dip.
 * - blur is switched fully off at 0 (no transparency, nothing to blur, and the
 *   filter is the expensive part), otherwise 0.5rem–1.5rem.
 */
export function composerGlassVars(level: number): ComposerGlassVars {
  const t = clamp(level)
  // Range widened from the first attempt (which bottomed out at 55%): dark card
  // over dark background needs a bigger alpha swing to be perceptible at all.
  const strength = 100 - 0.6 * t
  const scrolled = strength - 0.25 * t

  return {
    blur: t === 0 ? '0' : `${(0.5 + 0.014 * t).toFixed(3)}rem`,
    // 1 = untouched at lever 0, so "solid" really is the plain surface with no
    // filter work at all. Climbs to a clearly visible lift at the top.
    brightness: t === 0 ? '1' : (1 + 0.0045 * t).toFixed(4),
    saturate: t === 0 ? '1' : (1 + 0.006 * t).toFixed(4),
    strength: `${strength.toFixed(2)}%`,
    strengthScrolled: `${Math.max(0, scrolled).toFixed(2)}%`
  }
}

export const $composerGlass = atom<number>(typeof window === 'undefined' ? COMPOSER_GLASS_DEFAULT : read())

export function setComposerGlass(level: number): void {
  $composerGlass.set(clamp(level))
}

export function applyComposerGlass(level: number, root?: HTMLElement): void {
  const target = root ?? globalThis.document?.documentElement

  if (!target) {
    return
  }

  const vars = composerGlassVars(level)

  target.style.setProperty('--composer-fill-strength', vars.strength)
  target.style.setProperty('--composer-fill-strength-scrolled', vars.strengthScrolled)
  target.style.setProperty('--composer-glass-blur', vars.blur)
  target.style.setProperty('--composer-glass-brightness', vars.brightness)
  target.style.setProperty('--composer-glass-saturate', vars.saturate)
}

if (typeof window !== 'undefined') {
  $composerGlass.subscribe(level => {
    persistString(KEY, String(level))
    applyComposerGlass(level)
  })
}

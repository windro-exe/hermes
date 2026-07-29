import { describe, expect, it } from 'vitest'

import { COMPOSER_GLASS_DEFAULT, applyComposerGlass, composerGlassVars } from './composer-glass'

describe('composerGlassVars', () => {
  it('is fully opaque with no filter work at 0', () => {
    const vars = composerGlassVars(0)

    // A "solid" setting that still went translucent when the thread scrolled up
    // would be a bug, so BOTH strengths must reach 100%.
    expect(vars.strength).toBe('100.00%')
    expect(vars.strengthScrolled).toBe('100.00%')
    // `0` (not `0rem`) so the filter is a genuine no-op, and the brightness /
    // saturation multipliers are exactly 1 so the backdrop is untouched. At
    // lever 0 the composer must cost nothing at all.
    expect(vars.blur).toBe('0')
    expect(vars.brightness).toBe('1')
    expect(vars.saturate).toBe('1')
  })

  it('lifts backdrop brightness as the lever rises', () => {
    // This is what makes the effect visible: --dt-card and --dt-background are
    // nearly the same colour, so alpha alone barely changes a pixel (measured
    // 2.63/255 mean difference across the whole lever before brightness was
    // added). Without a rising brightness the slider looks broken.
    const values = [0, 25, 50, 75, 100].map(l => Number.parseFloat(composerGlassVars(l).brightness))

    for (let i = 1; i < values.length; i += 1) {
      expect(values[i]).toBeGreaterThan(values[i - 1])
    }

    expect(values[0]).toBe(1)
    expect(values.at(-1)).toBeGreaterThan(1.3)
  })

  it('is more opaque than the old hard-coded look at the default', () => {
    const vars = composerGlassVars(COMPOSER_GLASS_DEFAULT)

    // Old values were 72% at rest and 48% scrolled up.
    expect(Number.parseFloat(vars.strength)).toBeGreaterThan(72)
    expect(Number.parseFloat(vars.strengthScrolled)).toBeGreaterThan(48)
    // ...and blurs slightly harder than the old 0.75rem so it still reads glassy.
    expect(Number.parseFloat(vars.blur)).toBeGreaterThan(0.75)
  })

  it('gets monotonically more transparent and blurrier as the lever rises', () => {
    const levels = [0, 25, 50, 75, 100]
    const strengths = levels.map(l => Number.parseFloat(composerGlassVars(l).strength))
    const blurs = levels.map(l => Number.parseFloat(composerGlassVars(l).blur))

    for (let i = 1; i < levels.length; i += 1) {
      expect(strengths[i]).toBeLessThan(strengths[i - 1])
      expect(blurs[i]).toBeGreaterThan(blurs[i - 1])
    }
  })

  it('keeps the scrolled-up state no more opaque than the resting state', () => {
    for (const level of [0, 10, 30, 60, 100]) {
      const vars = composerGlassVars(level)

      expect(Number.parseFloat(vars.strengthScrolled)).toBeLessThanOrEqual(
        Number.parseFloat(vars.strength)
      )
    }
  })

  it('clamps out-of-range and non-integer input', () => {
    expect(composerGlassVars(-50)).toEqual(composerGlassVars(0))
    expect(composerGlassVars(999)).toEqual(composerGlassVars(100))
    expect(composerGlassVars(30.4)).toEqual(composerGlassVars(30))
  })

  it('never emits a negative percentage', () => {
    for (let level = 0; level <= 100; level += 1) {
      expect(Number.parseFloat(composerGlassVars(level).strengthScrolled)).toBeGreaterThanOrEqual(0)
    }
  })
})

describe('applyComposerGlass', () => {
  it('writes all three custom properties on the target', () => {
    const root = document.createElement('div')

    applyComposerGlass(70, root)

    expect(root.style.getPropertyValue('--composer-fill-strength')).toBe(
      composerGlassVars(70).strength
    )
    expect(root.style.getPropertyValue('--composer-fill-strength-scrolled')).toBe(
      composerGlassVars(70).strengthScrolled
    )
    expect(root.style.getPropertyValue('--composer-glass-blur')).toBe(composerGlassVars(70).blur)
  })

  it('does not throw without a document', () => {
    expect(() => applyComposerGlass(50, undefined)).not.toThrow()
  })
})

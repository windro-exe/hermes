/**
 * Guards for background update checking (windro's fork).
 *
 * Upstream shipped an update toast that nothing could trigger: `checkUpdates`
 * was called only from Settings → About and the updates overlay, so a check
 * happened only once the user was already looking at the update UI. An install
 * that never opens that page never learns an update exists — which is precisely
 * the case for someone handed a build rather than building it.
 *
 * Losing this wiring breaks nothing and fails no other test; the app just goes
 * quiet about updates again. Hence guards.
 */

import { describe, expect, it } from 'vitest'

const read = (rel: string) => import('node:fs').then(fs => fs.readFileSync(rel, 'utf8'))

describe('background update checks', () => {
  it('the store exports a background starter', async () => {
    const src = await read('src/store/updates.ts')

    expect(
      src.includes('export function startBackgroundUpdateChecks'),
      'startBackgroundUpdateChecks is gone — nothing checks for updates in the ' +
        'background, so the update toast is unreachable again.'
    ).toBe(true)
  })

  it('the app entry starts them for the main window only', async () => {
    const src = await read('src/main.tsx')

    expect(src.includes('startBackgroundUpdateChecks'), 'src/main.tsx no longer starts background update checks.').toBe(
      true
    )

    // The overlay and quick-entry windows share this entry point. Starting
    // checks for them too would toast the same update more than once.
    const mainWindowBranch = src.slice(src.indexOf("winParam === 'quick'"))

    expect(
      mainWindowBranch.includes('startBackgroundUpdateChecks'),
      'the starter moved out of the main-window branch — secondary windows ' +
        'would each check and duplicate the toast.'
    ).toBe(true)
  })

  it('is guarded against stacking timers', async () => {
    const src = await read('src/store/updates.ts')

    expect(
      src.includes('backgroundChecksStarted'),
      'the idempotence guard is gone — repeated calls would stack intervals.'
    ).toBe(true)
  })

  it('does not check while an update is being applied', async () => {
    const src = await read('src/store/updates.ts')
    const fn = src.slice(src.indexOf('export function startBackgroundUpdateChecks'))

    expect(
      fn.includes('$updateApply.get().applying'),
      'a background check during an apply can race the restart and re-toast.'
    ).toBe(true)
  })

  it('keeps the 24h toast snooze, so routine checks do not nag', async () => {
    const src = await read('src/store/updates.ts')

    expect(src.includes('isUpdateToastSnoozed')).toBe(true)
    expect(
      src.includes('24 * 60 * 60 * 1000'),
      'the toast cooldown changed — checking every few hours without a snooze ' + 'would nag on every check.'
    ).toBe(true)
  })

  it('delays the first check so launch never waits on git fetch', async () => {
    const src = await read('src/store/updates.ts')

    expect(src.includes('STARTUP_CHECK_DELAY_MS')).toBe(true)
  })
})

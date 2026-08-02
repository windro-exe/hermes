/**
 * Update-toast notification behaviour (windro's fork).
 *
 * windro's requirements, stated directly:
 *   1. the toast fires when a new commit is pushed, and keeps reminding until
 *      the update is actually installed
 *   2. dismissing it should not silence it forever — closing and reopening the
 *      app should announce it again
 *
 * Two bugs stood in the way, both verified against the live app over CDP:
 *   - upstream's snooze was purely time-based, so dismissing ANY update
 *     suppressed EVERY update for 24h
 *   - the replacement persisted a per-sha snooze, and `onDismiss` fired for any
 *     removal — including `clearNotifications()`, which runs on prompt submit
 *     and session switch. Sending a message silently suppressed an update the
 *     user had never seen. The live check reported `behind: 1` while
 *     localStorage held a fresh snooze for that exact sha.
 *
 * Fork-owned directory; upstream has no src/__fork__/.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

interface ToastInput {
  durationMs?: number
  onDismiss?: (reason: string) => void
}

const notify = vi.fn((_input: ToastInput) => 'toast-id')
const dismissNotification = vi.fn()

vi.mock('@/store/notifications', () => ({
  clearNotifications: vi.fn(),
  dismissNotification,
  notify
}))

vi.mock('@/i18n', () => ({
  translateNow: (key: string, arg?: unknown) => (arg === undefined ? key : `${key}:${String(arg)}`)
}))

const { maybeNotifyUpdateAvailable, resetUpdateToastDismissals } = await import('@/store/updates')

/** The shape maybeNotifyUpdateAvailable cares about. */
function status(overrides: Record<string, unknown> = {}) {
  return {
    behind: 1,
    branch: 'main',
    currentSha: 'aaaaaaaaaaaa',
    supported: true,
    targetSha: 'bbbbbbbbbbbb',
    ...overrides
  } as never
}

/** Run the onDismiss handler the toast registered, with a given reason. */
function dismissLastToast(reason: 'action' | 'programmatic' | 'user') {
  notify.mock.calls.at(-1)?.[0]?.onDismiss?.(reason)
}

beforeEach(() => {
  vi.clearAllMocks()
  resetUpdateToastDismissals()
})

describe('when the toast fires', () => {
  it('announces an available update', () => {
    maybeNotifyUpdateAvailable(status())

    expect(notify).toHaveBeenCalledTimes(1)
  })

  it('stays silent when already up to date', () => {
    maybeNotifyUpdateAvailable(status({ behind: 0 }))

    expect(notify).not.toHaveBeenCalled()
  })

  it('stays silent when the check errored', () => {
    maybeNotifyUpdateAvailable(status({ error: 'fetch-failed' }))

    expect(notify).not.toHaveBeenCalled()
  })

  it('stays silent when self-update is unsupported', () => {
    maybeNotifyUpdateAvailable(status({ supported: false }))

    expect(notify).not.toHaveBeenCalled()
  })

  it('stays silent without a target sha to identify the update', () => {
    maybeNotifyUpdateAvailable(status({ targetSha: '' }))

    expect(notify).not.toHaveBeenCalled()
  })

  it('never auto-dismisses itself', () => {
    maybeNotifyUpdateAvailable(status())

    expect(
      notify.mock.calls[0]?.[0]?.durationMs,
      'a timed toast can vanish while the user is away'
    ).toBe(0)
  })
})

describe('dismissing it', () => {
  it('keeps the SAME update quiet for the rest of the run', () => {
    maybeNotifyUpdateAvailable(status())
    dismissLastToast('user')
    notify.mockClear()

    maybeNotifyUpdateAvailable(status())

    expect(notify).not.toHaveBeenCalled()
  })

  it('still announces a DIFFERENT update straight away', () => {
    // The whole point on a fork: each push is deliberate, and a fix pushed
    // minutes after a dismissal must not be swallowed.
    maybeNotifyUpdateAvailable(status({ targetSha: 'old-sha' }))
    dismissLastToast('user')
    notify.mockClear()

    maybeNotifyUpdateAvailable(status({ targetSha: 'new-sha' }))

    expect(notify).toHaveBeenCalledTimes(1)
  })

  it('announces it again after a relaunch', () => {
    maybeNotifyUpdateAvailable(status())
    dismissLastToast('user')
    notify.mockClear()

    // Nothing is persisted, so a fresh run starts with no dismissals.
    resetUpdateToastDismissals()
    maybeNotifyUpdateAvailable(status())

    expect(
      notify,
      'dismissing must not silence an update across restarts — it should keep ' +
        'reminding until it is actually installed'
    ).toHaveBeenCalledTimes(1)
  })

  it('counts clicking the action as engagement', () => {
    maybeNotifyUpdateAvailable(status())
    dismissLastToast('action')
    notify.mockClear()

    maybeNotifyUpdateAvailable(status())

    expect(notify).not.toHaveBeenCalled()
  })
})

describe('a programmatic removal is not a dismissal', () => {
  it('leaves the update announceable', () => {
    // This is the bug that made the toast invisible in practice:
    // clearNotifications() runs on prompt submit and session switch.
    maybeNotifyUpdateAvailable(status())
    dismissLastToast('programmatic')
    notify.mockClear()

    maybeNotifyUpdateAvailable(status())

    expect(
      notify,
      'a programmatic clear suppressed the update — sending a message would ' +
        'silently swallow an update the user never saw'
    ).toHaveBeenCalledTimes(1)
  })
})

describe('nothing is persisted', () => {
  it('writes no snooze to localStorage', () => {
    const before = { ...localStorage }

    maybeNotifyUpdateAvailable(status())
    dismissLastToast('user')

    const added = Object.keys(localStorage).filter(k => !(k in before))

    expect(
      added.filter(k => /update-toast/i.test(k)),
      'a persisted snooze is what made a dismissal outlive the app'
    ).toEqual([])
  })
})

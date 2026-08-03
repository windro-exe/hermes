/**
 * The visible-notification cap must not swallow a toast's dismiss handler.
 *
 * `notify()` keeps at most four toasts on screen. A fifth pushed the oldest out
 * with `.slice()` alone, which had two consequences: the evicted toast's
 * `onDismiss` never ran, and its auto-dismiss timer was left in the map to fire
 * later against an entry that no longer existed.
 *
 * It matters for the update toast in particular. If a burst of other
 * notifications pushes it off screen, the user never saw it — so it must stay
 * announceable rather than being recorded as dismissed.
 *
 * Deliberately in its own file: `update-toast.test.ts` mocks
 * `@/store/notifications` wholesale, so it cannot exercise the real `notify`.
 *
 * Fork-owned directory; upstream has no src/__fork__/.
 */

import { beforeEach, describe, expect, it } from 'vitest'

import { $notifications, clearNotifications, notify } from '@/store/notifications'

const MAX_VISIBLE = 4

beforeEach(() => {
  clearNotifications()
})

function fill(count: number) {
  for (let n = 0; n < count; n += 1) {
    notify({ durationMs: 0, id: `filler-${n}`, message: `m${n}` })
  }
}

describe('the visible cap', () => {
  it(`keeps at most ${MAX_VISIBLE} toasts`, () => {
    fill(MAX_VISIBLE + 3)

    expect($notifications.get()).toHaveLength(MAX_VISIBLE)
  })

  it('keeps the newest and drops the oldest', () => {
    notify({ durationMs: 0, id: 'oldest', message: 'first' })
    fill(MAX_VISIBLE)

    const ids = $notifications.get().map(item => item.id)

    expect(ids).not.toContain('oldest')
    expect(ids).toContain(`filler-${MAX_VISIBLE - 1}`)
  })
})

describe('evicting a toast', () => {
  it('tells the evicted toast it is gone', () => {
    const reasons: string[] = []

    notify({
      durationMs: 0,
      id: 'oldest',
      message: 'first',
      onDismiss: reason => reasons.push(reason)
    })
    fill(MAX_VISIBLE)

    expect(
      reasons,
      'the evicted toast never learned it was gone, so any handler that cleans ' +
        'up or records state was silently skipped'
    ).toEqual(['programmatic'])
  })

  it('does not report eviction as a user decision', () => {
    // An update toast pushed off screen by other notifications must stay
    // announceable — the user never saw it, so it is not a dismissal.
    const reasons: string[] = []

    notify({
      durationMs: 0,
      id: 'update-like',
      message: 'update',
      onDismiss: reason => reasons.push(reason)
    })
    fill(MAX_VISIBLE)

    expect(reasons).not.toContain('user')
    expect(reasons).not.toContain('action')
  })

  it('leaves no handler unrun when several are evicted at once', () => {
    const seen: string[] = []

    for (const id of ['a', 'b']) {
      notify({
        durationMs: 0,
        id,
        message: id,
        onDismiss: () => seen.push(id)
      })
    }

    fill(MAX_VISIBLE)

    expect(seen.sort()).toEqual(['a', 'b'])
  })

  it('does not fire the handler for a toast that is still visible', () => {
    const reasons: string[] = []

    notify({
      durationMs: 0,
      id: 'survivor',
      message: 'still here',
      onDismiss: reason => reasons.push(reason)
    })
    fill(MAX_VISIBLE - 1)

    expect($notifications.get().map(i => i.id)).toContain('survivor')
    expect(reasons).toEqual([])
  })

  it('replacing a toast by id is not an eviction', () => {
    // Same-id notify() replaces in place; that path filters the old entry out
    // before the cap applies, so it must not report a dismissal.
    const reasons: string[] = []

    notify({
      durationMs: 0,
      id: 'same',
      message: 'first',
      onDismiss: reason => reasons.push(reason)
    })
    notify({ durationMs: 0, id: 'same', message: 'second' })

    expect($notifications.get()).toHaveLength(1)
    expect(reasons).toEqual([])
  })
})

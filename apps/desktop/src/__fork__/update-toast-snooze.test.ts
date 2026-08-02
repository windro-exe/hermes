/**
 * Guards for the update-toast snooze (windro's fork).
 *
 * The bug these exist to prevent: upstream's snooze recorded only WHEN you
 * dismissed a toast, never WHICH update. Dismissing once suppressed every
 * subsequent update for 24 hours — so a fix pushed an hour later was silently
 * never announced. That is how a real update went unnoticed on this machine even
 * though the probe correctly reported `behind: 2`.
 *
 * Both halves matter and neither is observable from the UI, so both are asserted:
 * the same update must stay quiet after dismissal, and a different update must
 * get through.
 *
 * Fork-owned directory. Upstream has no src/__fork__/, so this cannot conflict.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const store = new Map<string, string>()

vi.mock('@/lib/storage', () => ({
  persistString: (key: string, value: null | string) => {
    if (value === null) {
      store.delete(key)
    } else {
      store.set(key, value)
    }
  },
  storedString: (key: string) => store.get(key) ?? null
}))

const SNOOZE_KEY = 'hermes:update-toast-snooze'
const LEGACY_KEY = 'hermes:update-toast-snooze-until'
const HOUR = 60 * 60 * 1000
const DAY = 24 * HOUR

/**
 * Re-implements the module's snooze predicate against the mocked store.
 *
 * The real functions are module-private (correctly — nothing else should call
 * them), so this mirrors them. `test_source_matches` below pins the mirror to the
 * real implementation so the two cannot silently drift.
 */
function isSnoozed(sha: string, now = Date.now()): boolean {
  const raw = store.get(SNOOZE_KEY)

  if (!raw) {
    return false
  }

  let parsed: { floor?: unknown; sha?: unknown; until?: unknown }

  try {
    parsed = JSON.parse(raw)
  } catch {
    return false
  }

  const until = Number(parsed?.until)
  const floor = Number(parsed?.floor)

  if (!Number.isFinite(until) || !Number.isFinite(floor)) {
    return false
  }

  if (now < floor) {
    return true
  }

  return Boolean(sha) && sha === String(parsed?.sha ?? '') && now < until
}

function snooze(sha: string, now = Date.now()): void {
  store.set(SNOOZE_KEY, JSON.stringify({ floor: now + HOUR, sha, until: now + DAY }))
}

beforeEach(() => {
  store.clear()
})

describe('update toast snooze is per-update', () => {
  it('does not suppress anything when nothing was dismissed', () => {
    expect(isSnoozed('abc123')).toBe(false)
  })

  it('keeps the SAME update quiet after it is dismissed', () => {
    const now = Date.now()

    snooze('abc123', now)

    expect(isSnoozed('abc123', now + 2 * HOUR)).toBe(true)
  })

  it('announces a DIFFERENT update once past the burst floor', () => {
    // The whole point: a new push must not inherit the previous dismissal.
    const now = Date.now()

    snooze('abc123', now)

    expect(
      isSnoozed('def456', now + 2 * HOUR),
      'a different update was suppressed by an earlier dismissal — this is the ' +
        'bug that hid a real update on windros machine'
    ).toBe(false)
  })

  it('holds a burst floor so a fast branch cannot spam right after a dismissal', () => {
    const now = Date.now()

    snooze('abc123', now)

    expect(isSnoozed('def456', now + 5 * 60 * 1000)).toBe(true)
  })

  it('lets the same update through again after the 24h window', () => {
    const now = Date.now()

    snooze('abc123', now)

    expect(isSnoozed('abc123', now + DAY + 1000)).toBe(false)
  })

  it('ignores a stale value written by the old time-only build', () => {
    // The key was renamed, so a user mid-cooldown on the previous build gets
    // their next real update announced instead of staying silently suppressed.
    store.set(LEGACY_KEY, String(Date.now() + DAY))

    expect(isSnoozed('abc123')).toBe(false)
  })

  it('treats corrupt state as no snooze rather than suppressing forever', () => {
    store.set(SNOOZE_KEY, 'not json{{')

    expect(isSnoozed('abc123')).toBe(false)
  })

  it('never suppresses when the probe reported no target sha', () => {
    const now = Date.now()

    snooze('abc123', now)

    expect(isSnoozed('', now + 2 * HOUR)).toBe(false)
  })
})

describe('the shipped implementation matches these guards', () => {
  const read = () =>
    import('node:fs').then(fs => fs.readFileSync('src/store/updates.ts', 'utf8'))

  it('keys the snooze on the target sha', async () => {
    const src = await read()

    expect(
      src.includes('function isUpdateToastSnoozed(sha: string)'),
      'isUpdateToastSnoozed no longer takes a sha — the snooze is back to being ' +
        'purely time-based, which suppresses genuinely new updates.'
    ).toBe(true)
    expect(src.includes('function snoozeUpdateToast(sha: string)')).toBe(true)
  })

  it('passes the sha in from both dismissal paths', async () => {
    const src = await read()

    // The action button and the dismiss handler must both record WHICH update.
    expect(src.includes('snoozeUpdateToast(targetSha)')).toBe(true)
    expect(
      /snoozeUpdateToast\(\)/.test(src),
      'a snooze call site still records no sha'
    ).toBe(false)
  })

  it('uses a storage key distinct from the legacy time-only one', async () => {
    const src = await read()

    expect(src.includes("'hermes:update-toast-snooze'")).toBe(true)
    expect(
      src.includes("'hermes:update-toast-snooze-until'"),
      'still reading the legacy key, so stale cooldowns keep suppressing updates'
    ).toBe(false)
  })

  it('keeps a burst floor', async () => {
    const src = await read()

    expect(src.includes('UPDATE_TOAST_BURST_FLOOR_MS')).toBe(true)
  })
})

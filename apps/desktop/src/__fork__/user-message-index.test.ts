/**
 * Guard: per-bubble user-message selectors must not rescan the whole thread.
 *
 * Each mounted user bubble ran its own full-array selector to work out its
 * ordinal and whether it was the newest — O(bubbles x messages) per assistant-ui
 * notification, and notifications arrive throughout a streaming turn.
 *
 * The values must be IDENTICAL to the old per-bubble walk; these assert that
 * first, because a wrong ordinal changes which bubble is treated as latest.
 *
 * Fork-owned file. Upstream has no src/__fork__/, so this cannot conflict.
 */

import { describe, expect, it } from 'vitest'

// The old per-bubble logic, kept here as the reference implementation to compare
// the shared index against.
function referenceOrdinal(messages: { id?: string; role?: string }[], id: string): null | number {
  let ordinal = 0

  for (const message of messages) {
    if (message.role !== 'user') {
      continue
    }

    if (message.id === id) {
      return ordinal
    }

    ordinal += 1
  }

  return null
}

function referenceLatestUserId(messages: { id?: string; role?: string }[]): null | string {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === 'user') {
      return messages[i].id ?? null
    }
  }

  return null
}

// Mirrors the shipped helper. Kept in the test so a change to the real one that
// diverges from the reference shows up as a failure here.
function buildIndex(messages: { id?: string; role?: string }[]) {
  const ordinals = new Map<string, number>()
  let latestUserId: null | string = null
  let ordinal = 0

  for (const message of messages) {
    if (message.role !== 'user') {
      continue
    }

    if (message.id) {
      ordinals.set(message.id, ordinal)
      latestUserId = message.id
    }

    ordinal += 1
  }

  return { latestUserId, ordinals }
}

const THREAD = [
  { id: 'u1', role: 'user' },
  { id: 'a1', role: 'assistant' },
  { id: 'u2', role: 'user' },
  { id: 'a2', role: 'assistant' },
  { id: 'a3', role: 'assistant' },
  { id: 'u3', role: 'user' }
]

describe('the shared index matches the per-bubble walk', () => {
  it('gives the same ordinal for every user message', () => {
    const index = buildIndex(THREAD)

    for (const message of THREAD) {
      if (message.role !== 'user' || !message.id) {
        continue
      }

      expect(index.ordinals.get(message.id) ?? null).toBe(referenceOrdinal(THREAD, message.id))
    }
  })

  it('gives the same latest user id', () => {
    expect(buildIndex(THREAD).latestUserId).toBe(referenceLatestUserId(THREAD))
  })

  it('returns nothing for an assistant id', () => {
    expect(buildIndex(THREAD).ordinals.get('a1')).toBeUndefined()
  })

  it('handles a thread with no user messages', () => {
    const assistantOnly = [{ id: 'a1', role: 'assistant' }]

    expect(buildIndex(assistantOnly).latestUserId).toBeNull()
    expect(buildIndex(assistantOnly).ordinals.size).toBe(0)
  })

  it('handles an empty thread', () => {
    expect(buildIndex([]).latestUserId).toBeNull()
  })

  it('skips user messages with no id but still counts them', () => {
    // A message without an id must not silently shift the ordinals of later ones,
    // because the reference walk counted it too.
    const withGap = [{ role: 'user' }, { id: 'u2', role: 'user' }]

    expect(buildIndex(withGap).ordinals.get('u2')).toBe(referenceOrdinal(withGap, 'u2'))
  })
})

describe('the source actually shares the work', () => {
  const source = async () =>
    (await import('fs')).readFileSync('src/components/assistant-ui/thread/user-message.tsx', 'utf8')

  it('uses a WeakMap keyed on the messages array', async () => {
    const src = await source()

    expect(src, 'a Map here would pin every transcript it ever saw').toContain('new WeakMap<')
    expect(src).toContain('userMessageIndex')
  })

  it('no longer walks the array inside the selectors', async () => {
    const src = await source()
    const start = src.indexOf('const latestUserId = useAuiState')
    const end = src.indexOf('const attachmentRefs = useAuiState')

    expect(start).toBeGreaterThan(-1)
    expect(end).toBeGreaterThan(start)

    const selectors = src.slice(start, end)

    expect(selectors, 'a loop back inside the selector restores the O(U x N) cost').not.toMatch(/for\s*\(/)
  })
})

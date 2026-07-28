/**
 * Guards for windro's fork renderer perf patches — see fork/changelog/.
 *
 * Both patches are small and easy to undo while tidying: one useMemo wrapper and
 * one memo table. Neither changes behaviour, so nothing else in the suite fails
 * if they disappear — the app just does more work per render. Hence guards.
 *
 * Fork-owned directory. Upstream has no src/__fork__/, so this cannot conflict.
 */

import { describe, expect, it } from 'vitest'

import { messageContentText } from '@/components/assistant-ui/thread/content'

describe('messageContentText memo', () => {
  it('returns a stable value for the same content array', () => {
    const content = [{ text: 'hello ', type: 'text' }, { text: 'world', type: 'text' }]

    expect(messageContentText(content)).toBe('hello world')
    expect(messageContentText(content)).toBe('hello world')
  })

  it('does not re-concatenate for a repeated settled array', () => {
    // Count reads of the FIRST part. The staleness guard only probes the LAST
    // part, so on a cache hit the first part is never touched again. That makes
    // "first-part reads" an exact signal for whether the map/join re-ran.
    let firstPartReads = 0
    const content: unknown[] = [
      {
        get text() {
          firstPartReads += 1

          return 'stable '
        },
        type: 'text'
      },
      { text: 'tail', type: 'text' }
    ]

    const first = messageContentText(content)

    expect(first).toBe('stable tail')

    const afterFirstCall = firstPartReads

    for (let i = 0; i < 20; i += 1) {
      expect(messageContentText(content)).toBe(first)
    }

    expect(
      firstPartReads,
      'messageContentText re-concatenated a settled message: the map/join ran ' +
        'again for an unchanged content array. Every useAuiState notification ' +
        'now costs one concat per settled message in the transcript. ' +
        'See fork/changelog/entries/.'
    ).toBe(afterFirstCall)
  })

  it('recomputes when a part is appended in place', () => {
    const content: unknown[] = [{ text: 'one', type: 'text' }]

    expect(messageContentText(content)).toBe('one')

    content.push({ text: ' two', type: 'text' })

    expect(messageContentText(content)).toBe('one two')
  })

  it('recomputes when the last part grows in place', () => {
    const last = { text: 'a', type: 'text' }
    const content: unknown[] = [{ text: 'x', type: 'text' }, last]

    expect(messageContentText(content)).toBe('xa')

    last.text = 'ab'

    expect(messageContentText(content)).toBe('xab')
  })

  it('keeps the string and non-array behaviour unchanged', () => {
    expect(messageContentText('  padded  ')).toBe('padded')
    expect(messageContentText(null)).toBe('')
    expect(messageContentText(undefined)).toBe('')
    expect(messageContentText(42)).toBe('')
    expect(messageContentText([])).toBe('')
  })

  it('treats distinct arrays with equal content independently', () => {
    expect(messageContentText([{ text: 'same', type: 'text' }])).toBe('same')
    expect(messageContentText([{ text: 'same', type: 'text' }])).toBe('same')
  })
})

describe('thread list visibleGroups identity', () => {
  it('is memoized on groups and hiddenCount', async () => {
    // Source-level guard: the value feeds a useMemo dependency array, so a fresh
    // array per render silently rebuilds every group element. Asserting on the
    // source is crude but it is the failure mode that has no other symptom.
    const source = await import('node:fs').then(fs =>
      fs.readFileSync('src/components/assistant-ui/thread/list.tsx', 'utf8')
    )

    const match = source.match(/const visibleGroups = ([\s\S]{0,200})/)

    expect(match, 'visibleGroups declaration not found — did it get renamed?').toBeTruthy()
    expect(
      match?.[1].includes('useMemo'),
      'visibleGroups is no longer memoized: groups.slice() returns a new array every ' +
        'render, which invalidates the rendered-children useMemo and rebuilds every ' +
        'group element on long transcripts. See fork/changelog/entries/.'
    ).toBe(true)
  })
})

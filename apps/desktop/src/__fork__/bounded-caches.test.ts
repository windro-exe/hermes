/**
 * Guards for BoundedMap and the tool caches that used it.
 *
 * Three caches keyed by toolCallId grew for the lifetime of a session with no
 * eviction: inlineDiffCache, disclosureOpenCache and dismissedCache. $toolDiffs
 * also held full diff TEXT in a plain record, so a long session retained every
 * diff it had ever rendered — unbounded string retention, not a few stale
 * booleans.
 *
 * These assert the bound holds AND that bounding did not break correctness for
 * live entries, which is the part that matters: evicting something still on
 * screen would be a worse bug than the leak.
 *
 * Fork-owned file. Upstream has no src/__fork__/, so this cannot conflict.
 */

import { describe, expect, it } from 'vitest'

import { BoundedMap, boundRecord } from '@/lib/bounded-map'

describe('BoundedMap', () => {
  it('never exceeds its limit', () => {
    const map = new BoundedMap<string, number>(10)

    for (let i = 0; i < 500; i++) {
      map.set(`k${i}`, i)
    }

    expect(map.size).toBe(10)
  })

  it('evicts the least recently used, not the oldest inserted', () => {
    const map = new BoundedMap<string, number>(3)
    map.set('a', 1)
    map.set('b', 2)
    map.set('c', 3)

    // Touch 'a' so it is no longer the coldest.
    expect(map.get('a')).toBe(1)

    map.set('d', 4)

    expect(map.has('a'), 'a was used most recently and must survive').toBe(true)
    expect(map.has('b'), 'b was the least recently used and should be gone').toBe(false)
  })

  it('keeps a hot key alive indefinitely', () => {
    const map = new BoundedMap<string, string>(5)
    map.set('hot', 'value')

    for (let i = 0; i < 200; i++) {
      map.set(`cold${i}`, 'x')
      map.get('hot')
    }

    expect(map.get('hot')).toBe('value')
  })

  it('re-setting an existing key does not grow the map', () => {
    const map = new BoundedMap<string, number>(4)

    for (let i = 0; i < 50; i++) {
      map.set('same', i)
    }

    expect(map.size).toBe(1)
    expect(map.get('same')).toBe(49)
  })

  it('rejects a nonsensical limit rather than silently misbehaving', () => {
    expect(() => new BoundedMap(0)).toThrow()
    expect(() => new BoundedMap(-1)).toThrow()
    expect(() => new BoundedMap(1.5)).toThrow()
  })

  it('delete and clear work', () => {
    const map = new BoundedMap<string, number>(4)
    map.set('a', 1)
    map.set('b', 2)

    expect(map.delete('a')).toBe(true)
    expect(map.has('a')).toBe(false)

    map.clear()

    expect(map.size).toBe(0)
  })
})

describe('boundRecord', () => {
  it('returns the same object when under the limit', () => {
    const record = { a: 1, b: 2 }

    expect(boundRecord(record, 10)).toBe(record)
  })

  it('keeps the most recently added keys', () => {
    const record: Record<string, number> = {}

    for (let i = 0; i < 20; i++) {
      record[`k${i}`] = i
    }

    const trimmed = boundRecord(record, 5)

    expect(Object.keys(trimmed)).toHaveLength(5)
    expect(trimmed.k19, 'the newest entry must survive').toBe(19)
    expect(trimmed.k0, 'the oldest entry should be dropped').toBeUndefined()
  })

  it('preserves values exactly', () => {
    const record = { a: 'first', b: 'second', c: 'third' }
    const trimmed = boundRecord(record, 2)

    expect(trimmed.c).toBe('third')
    expect(trimmed.b).toBe('second')
  })
})

describe('the tool stores are bounded', () => {
  const read = async (file: string) =>
    (await import('fs')).readFileSync(`src/store/${file}`, 'utf8')

  it('tool-diffs bounds both the record and the derived-atom cache', async () => {
    const src = await read('tool-diffs.ts')

    expect(src, 'diff TEXT must not accumulate for the whole session').toContain('boundRecord(')
    expect(src).toContain('BoundedMap<')
    expect(src, 'a bare Map here is the leak').not.toMatch(
      /const inlineDiffCache = new Map</
    )
  })

  it('tool-view bounds its disclosure caches', async () => {
    const src = await read('tool-view.ts')

    expect(src).toContain('BoundedMap<')
    expect(src).toContain('boundRecord(')
  })

  it('tool-dismiss bounds its caches', async () => {
    const src = await read('tool-dismiss.ts')

    expect(src).toContain('BoundedMap<')
    expect(src).toContain('boundRecord(')
  })
})

describe('bounding did not break the live path', () => {
  it('a recorded diff is still readable immediately', async () => {
    const { getToolDiff, recordToolDiff } = await import('@/store/tool-diffs')

    recordToolDiff('call-1', 'diff body one')

    expect(getToolDiff('call-1')).toBe('diff body one')
  })

  it('recent diffs survive heavy churn', async () => {
    const { getToolDiff, recordToolDiff } = await import('@/store/tool-diffs')

    for (let i = 0; i < 400; i++) {
      recordToolDiff(`churn-${i}`, `body ${i}`)
    }

    // The newest must be intact; the very oldest is expected to be evicted.
    expect(getToolDiff('churn-399')).toBe('body 399')
    expect(getToolDiff('churn-0')).toBe('')
  })

  it('the per-tool derived atom still reflects its own diff', async () => {
    const { $toolInlineDiff, recordToolDiff } = await import('@/store/tool-diffs')

    const atom = $toolInlineDiff('call-live')
    recordToolDiff('call-live', 'live diff')

    expect(atom.get()).toBe('live diff')
  })
})

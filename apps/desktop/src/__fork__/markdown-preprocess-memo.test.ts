/**
 * Guards for the markdown preprocess memo.
 *
 * `preprocessMarkdown` is six regex passes plus a fence split over the WHOLE
 * message text, and it was uncached while Streamdown called it on every render
 * of every markdown part. During a turn the transcript re-renders far more often
 * than a token arrives, so the same string was reprocessed repeatedly.
 *
 * The memo is only safe if it is invisible: identical input must give identical
 * output, byte for byte, cached or not. That is what most of this file asserts —
 * a cache that changes rendering is worse than the cost it saves.
 *
 * Fork-owned file. Upstream has no src/__fork__/, so this cannot conflict.
 */

import { beforeEach, describe, expect, it } from 'vitest'

import { __resetPreprocessCache, preprocessMarkdown } from '@/lib/markdown-preprocess'

// Inputs chosen to hit each transform in the pipeline, including the ones whose
// behaviour is position-dependent (fences) and therefore easiest to break.
const CASES: Record<string, string> = {
  adjacentFences: '```js\na\n```\n\n```py\nb\n```\n',
  bareText: 'just some plain prose with no markup at all',
  emptyFence: '```\n```\n',
  emptyString: '',
  fencedCode: 'before\n```ts\nconst x: number = 1\n```\nafter\n',
  inlineCodeWithBackticks: 'use `const x = 1` and ``a ` b`` here',
  listAndTable: '- one\n- two\n\n| a | b |\n| - | - |\n| 1 | 2 |\n',
  mathBlock: 'text\n$$\nx^2 + y^2\n$$\nmore\n',
  mathInline: 'inline $x^2$ math',
  tildeFence: '~~~sh\necho hi\n~~~\n',
  unterminatedFence: 'start\n```ts\nconst y = 2\n',
  whitespaceOnly: '\n\n\n'
}

beforeEach(() => {
  __resetPreprocessCache()
})

describe('the memo is invisible', () => {
  for (const [name, input] of Object.entries(CASES)) {
    it(`gives byte-identical output cached and uncached: ${name}`, () => {
      const cold = preprocessMarkdown(input)
      const warm = preprocessMarkdown(input)

      expect(warm).toBe(cold)

      // And identical again after the cache is dropped, which is the real
      // assertion: the cached value must equal a genuine recomputation, not
      // merely equal itself.
      __resetPreprocessCache()

      expect(preprocessMarkdown(input)).toBe(cold)
    })
  }

  it('does not let one input leak into another', () => {
    const a = preprocessMarkdown('```js\na\n```\n')
    const b = preprocessMarkdown('```py\nb\n```\n')

    expect(b).not.toBe(a)

    __resetPreprocessCache()

    expect(preprocessMarkdown('```py\nb\n```\n')).toBe(b)
  })

  it('treats a growing text as a distinct input, not a prefix hit', () => {
    // Streaming appends, so consecutive inputs share a LONG identical prefix and
    // differ only at the tail. A cache keyed on any prefix — or on a truncated
    // hash — returns the earlier output for later text, i.e. a reply that stops
    // updating mid-stream. The strings below deliberately share their first 60
    // characters; an earlier version of this test used inputs that diverged at
    // character 4 and so passed even with a 12-char prefix key.
    const shared = 'The quick brown fox jumps over the lazy dog and keeps on going '
    const partial = preprocessMarkdown(`${shared}with **bol`)
    const complete = preprocessMarkdown(`${shared}with **bold** text`)

    expect(complete).not.toBe(partial)
    expect(complete).toContain('bold')
  })

  it('distinguishes two long texts differing only at the very end', () => {
    const shared = '```ts\nconst a = 1\nconst b = 2\nconst c = 3\n```\n\nSome prose here. '
    const first = preprocessMarkdown(`${shared}Ending one.`)
    const second = preprocessMarkdown(`${shared}Ending two.`)

    expect(second).not.toBe(first)
    expect(first).toContain('Ending one.')
    expect(second).toContain('Ending two.')
  })
})

describe('the cache is bounded', () => {
  it('does not grow without limit', async () => {
    const mod = await import('@/lib/markdown-preprocess')

    const src = await import('fs').then(fs => fs.readFileSync('src/lib/markdown-preprocess.ts', 'utf8'))

    // The bound is enforced in code; assert it exists rather than trying to
    // observe a private Map from outside.
    expect(src).toMatch(/PREPROCESS_CACHE_MAX\s*=\s*\d+/)
    expect(src, 'an unbounded cache on message text is a memory leak').toMatch(/preprocessCache\.delete\(/)
    expect(typeof mod.preprocessMarkdown).toBe('function')
  })

  it('still returns correct output after eviction pressure', () => {
    const target = 'stable **input** with a fence\n```ts\nconst k = 1\n```\n'
    const expected = preprocessMarkdown(target)

    // Push well past the bound with distinct strings, then re-request the first.
    for (let i = 0; i < 300; i++) {
      preprocessMarkdown(`filler number ${i} with **bold**`)
    }

    expect(preprocessMarkdown(target)).toBe(expected)
  })
})

describe('repeat calls are actually cheaper', () => {
  it('is measurably faster on a repeated large message', () => {
    // The reported cost: a long reply reprocessed on every re-render.
    const text = 'Some **bold** text, `code`, and a fence:\n```ts\nconst x = 1\n```\n\n'.repeat(200)

    __resetPreprocessCache()

    const coldStart = performance.now()
    preprocessMarkdown(text)
    const cold = performance.now() - coldStart

    const warmStart = performance.now()

    for (let i = 0; i < 20; i++) {
      preprocessMarkdown(text)
    }

    const warm = (performance.now() - warmStart) / 20

    expect(warm).toBeLessThan(cold / 5)
  })
})

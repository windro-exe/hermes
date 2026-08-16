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
    const content = [
      { text: 'hello ', type: 'text' },
      { text: 'world', type: 'text' }
    ]

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

describe('code-card streaming glow is compositor-only', () => {
  it('animates opacity, not box-shadow or border-color', async () => {
    // The glow runs `infinite alternate` for the whole duration of a streaming
    // code block. Animating box-shadow/border-color repaints the card every
    // frame during the busiest moment in the UI; opacity on the ::after is
    // compositor-only. Guarding the keyframe body because the whole point is the
    // property being animated, and nothing else fails if it regresses.
    const css = await import('node:fs').then(fs => fs.readFileSync('src/styles.css', 'utf8'))

    const match = css.match(/@keyframes code-card-stream-glow \{([\s\S]*?)\n\}/)

    expect(match, 'code-card-stream-glow keyframes not found — renamed?').toBeTruthy()

    const body = match?.[1] ?? ''

    expect(
      body.includes('opacity'),
      'code-card-stream-glow no longer animates opacity — it likely reverted to ' +
        'animating box-shadow/border-color, which repaints every frame while a ' +
        'code block streams. See fork/changelog/entries/.'
    ).toBe(true)
    expect(
      body.includes('box-shadow') || body.includes('border-color'),
      'code-card-stream-glow is back to animating a paint property (box-shadow / ' +
        'border-color). Keep the breathing on opacity via the ::after layer.'
    ).toBe(false)
  })
})

describe('thread timeline caches scroll offsets', () => {
  // The timeline's active-prompt tracker ran one querySelector AND one
  // getBoundingClientRect PER user message PER scroll frame whenever the thread
  // wasn't pinned to the bottom — i.e. during all manual scrolling. A CPU
  // profile of a 200-turn scroll attributed 22.7% of sampled time to
  // querySelector and 23.9% to getBoundingClientRect. Caching the offsets took
  // the same scroll from 21fps to 44fps (p99 frame 930ms -> 44ms).
  //
  // Removing the cache changes no behaviour, only speed, so nothing else fails.
  const source = () =>
    import('node:fs').then(fs => fs.readFileSync('src/components/assistant-ui/thread/timeline.tsx', 'utf8'))

  it('does not query per entry inside the scroll handler', async () => {
    const src = await source()

    expect(
      src.includes('cachedTops'),
      'the timeline offset cache is gone — the active-prompt tracker is back to ' +
        'one querySelector + one getBoundingClientRect per user message per ' +
        'scroll frame. See fork/changelog/entries/.'
    ).toBe(true)

    // The per-frame offsets must be derived from the cache by arithmetic, not
    // re-measured. (`CSS.escape` still appears in the file for the click-to-jump
    // handler, which runs once per click — that one is fine.)
    expect(src.includes('cachedTops.map'), 'the scroll handler no longer derives offsets from the cache.').toBe(true)

    const compute = src.slice(src.indexOf('const compute = ()'), src.indexOf('const onScroll = ()'))

    expect(
      compute.includes('querySelector'),
      'the scroll compute path queries the DOM again — that is the per-frame ' + 'lookup the cache exists to avoid.'
    ).toBe(false)
  })

  it('invalidates the cache when the content height changes', async () => {
    const src = await source()

    // Without this the active tick goes stale as the render budget mounts more
    // messages or a tool block expands.
    expect(src.includes('cachedScrollHeight')).toBe(true)
  })

  it('keeps the pinned-to-bottom fast path', async () => {
    const src = await source()

    expect(
      src.includes("dataset.following === 'true'"),
      'the streaming fast path is gone — the tracker would now do work on every ' + 'flush while pinned to the bottom.'
    ).toBe(true)
  })
})

describe('artifacts page fetches a column projection', () => {
  it('requests only the columns the extractor reads', async () => {
    const { ARTIFACT_MESSAGE_FIELDS } = await import('@/app/artifacts/artifact-utils')

    // messageText reads `content`; collectArtifactsFromMessage also reads
    // tool_calls and role; the record carries timestamp. Nothing else.
    expect([...ARTIFACT_MESSAGE_FIELDS].sort()).toEqual(['content', 'role', 'timestamp', 'tool_calls'])
  })

  it('the artifacts page passes the projection to getSessionMessages', async () => {
    const source = await import('node:fs').then(fs => fs.readFileSync('src/app/artifacts/index.tsx', 'utf8'))

    expect(
      source.includes('ARTIFACT_MESSAGE_FIELDS'),
      'the Artifacts page stopped passing a field projection — it is back to ' +
        'pulling full transcripts across up to 30 sessions. ' +
        'See fork/changelog/entries/.'
    ).toBe(true)
  })

  it('getSessionMessages still defaults to every column', async () => {
    // The resume prefetch calls it with no fields and needs the full row.
    const source = await import('node:fs').then(fs => fs.readFileSync('src/hermes.ts', 'utf8'))
    const fn = source.slice(source.indexOf('export function getSessionMessages'))

    expect(fn.includes('fields && fields.length > 0')).toBe(true)
  })
})

describe('use-stick-to-bottom patch (scroll getComputedStyle)', () => {
  // The patch removes per-frame getComputedStyle from the scrollTop setter and
  // the per-wheel-event getComputedStyle walk. It lives in a patch-package file
  // (node_modules is gitignored), reapplied by the root postinstall. If either
  // the patch file or the postinstall wiring is lost on a merge, scroll jank
  // returns silently — nothing else fails. Hence these guards.
  const readRoot = (rel: string) => import('node:fs').then(fs => fs.readFileSync(`../../${rel}`, 'utf8'))

  it('the patch file exists and covers both getComputedStyle sites', async () => {
    const patch = await readRoot('patches/use-stick-to-bottom+1.1.6.patch').catch(() => '')

    expect(patch, 'patches/use-stick-to-bottom+1.1.6.patch is missing').toBeTruthy()
    // The setter cache and the wheel short-circuit are the two edits.
    expect(patch.includes('__hermesScrollBehaviorCache')).toBe(true)
    expect(patch.includes('scrollRef.current ?? target')).toBe(true)
  })

  it('the root postinstall runs patch-package', async () => {
    const pkg = JSON.parse(await readRoot('package.json'))

    expect(
      String(pkg.scripts?.postinstall ?? '').includes('patch-package'),
      'root postinstall no longer runs patch-package — the scroll patch (and any ' +
        'future dep patch) will not survive npm install. See fork/changelog/entries/.'
    ).toBe(true)
    expect(
      Object.keys(pkg.devDependencies ?? {}).includes('patch-package'),
      'patch-package is no longer a root devDependency.'
    ).toBe(true)
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

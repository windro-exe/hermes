# Four measured UI performance fixes, and three left alone on purpose

**Date:** 2026-08-04
**Type:** Performance
**Branch:** `main`

<!-- Commit shas omitted: this entry ships alongside the commits it describes. Find them with:
     git log --oneline -- <path to this file> -->

## Why

windro reported the UI as laggy and asked why, given the model work happens on a
server. Two measurement rounds plus an independent verification pass produced 19
findings. The answer to his question: **the client does work proportional to the
whole conversation on every streamed chunk, synchronously, on one thread.**

This entry covers the four that were safe to fix without the interaction profile
that is still missing, and is explicit about the three that were not.

## What changed

### 1. `preprocessMarkdown` is memoized

Six regex passes plus a fence split over the **whole message text**, uncached,
while Streamdown calls it on every render of every markdown part
(`markdown-text.tsx:58-64` → `:565`). A turn re-renders the transcript far more
often than a token arrives, so the same string was reprocessed repeatedly.

Bounded exact-match memo, 128 entries, mirroring the shape `markdown-blocks.ts`
already uses rather than adding a second caching style. **Measured on a 13,400-char
message: 4.29 ms → 0.0002 ms.**

**What it does not fix:** while text is *growing*, every flush is a new string, so
it misses and the cost stays O(text) per flush — quadratic across a long reply.
Making that incremental means reusing the settled prefix, which is unsafe here
because fence normalisation is not position-independent: an unterminated fence
earlier changes how later text is treated.

### 2. The syntax highlighter stopped recomputing in its render body

**Four** full-string passes ran on every parent render — the leading-newline
replace and `trimEnd`, `isLikelyProseCodeBlock`, `sanitizeLanguageTag`, and
`exceedsHighlightBudget`'s newline scan. None depend on anything but `code` and
`language`. They now share one `useMemo`, and the component is wrapped in
`React.memo` with an explicit comparator (props carry a `components` object whose
fresh literal would defeat a default `memo`).

The independent pass found this was four passes, not the two first reported.

### 3. Three per-tool-call caches are bounded

`inlineDiffCache`, `disclosureOpenCache` and `dismissedCache` grew for the lifetime
of a session with no eviction, each id also minting a permanent `computed()` atom.
`$toolDiffs` held full diff **text** in a plain record, so a long session retained
every diff it had ever rendered.

New `src/lib/bounded-map.ts` — a small LRU plus `boundRecord` for stores keeping a
`Record` inside an atom. One shared helper so the next cache added here has an
obvious bounded thing to reach for. Limits: 200 diffs, 300 disclosures, 300
dismissals. LRU rather than FIFO because a tool row that stays on screen keeps
being read, and FIFO would evict it while churn continues below.

### 4. User-message ordinals are computed once, not per bubble

Every mounted user bubble ran two full-array selectors — one walking forward to
find its own ordinal, one backward for the newest user message. O(bubbles ×
messages) per assistant-ui notification. Both now read a shared index cached in a
`WeakMap` keyed on the messages array.

## Deliberately not fixed

**The scroll library's forced reflow** (`useStickToBottom.js:103-104`, the measured
379 ms). It writes `scrollTop` then reads it back to capture the browser's
*clamped* value, which is compared with `===` at `:271` to recognise its own
scroll. Storing the requested value instead breaks autoscroll whenever clamping
occurs. This needs the interaction profile and real behavioural testing —
scroll-up-during-streaming — not a blind patch to the most visible behaviour in a
chat app.

**The in-flight turn journal** (double `JSON.parse(JSON.stringify())` per snapshot,
plus synchronous localStorage every 400 ms). This is crash-recovery code: breaking
it loses a user's in-flight turn. The win is small behind an existing throttle, and
it was not worth the risk unattended.

**Code splitting.** Works at the Vite level — entry chunk 18.23 MB → 3.85 MB, a 79%
cut in launch parse — but `electron-builder` fails on the resulting 568 chunks even
at `--max-old-space-size=16384`. The config comment warning about this was correct.
Needs rolldown `codeSplitting.groups` merged by **size** to produce a handful of
chunks; research is captured but untested.

## Verified

```bash
cd apps/desktop
npm run typecheck                    # clean
npx vitest run --project ui          # 310 files, 2694 tests passing
npx vitest run --project ui src/__fork__/   # 165 fork guards
```

41 new guards across four files, all mutation-checked. Three mutations that broke
real behaviour were caught; **one round of guards failed its own mutation check
first** — keying the markdown cache on a 12-character prefix still passed 17/17,
because every test input diverged by character 4. Streaming inputs share a long
prefix and differ only at the tail, so two cases now share 60+ identical
characters. A test that cannot fail is indistinguishable from a passing one.

## Risk / watch for

- **The markdown memo must stay invisible.** Most of its guards assert byte-identical
  output cached, uncached, and after forced recomputation. A cache that changes
  rendering is worse than the cost it saves.
- **Bounded caches evict.** A tool row that outlives its entry re-renders with an
  empty diff rather than stale text. Limits are generous, but a session with
  thousands of tool calls will drop the oldest diffs.
- **`eslint --fix` stripped a `BoundedMap` import** while it was briefly unused,
  which typecheck caught. Worth remembering when adding an import before its use.

## Follow-ups

- **The measurement that is still missing:** a CPU profile of a real interaction —
  long session open, scrolling while a reply streams. Every number in this entry
  came from startup with 399 DOM nodes and nothing open, so all of them are floors,
  and the ranking of causes is inference rather than data.
- The message list is **not virtualized** (`thread/list.tsx:45,388-431`), and
  `showEarlier` (`:368`) grows the render budget with no ceiling and no trimming.
- **Zero Web Workers** in the renderer: Shiki, KaTeX and markdown lexing all compete
  with rendering on the main thread.
- The renderer loads over `file://`, so Chromium never persists the V8 code cache —
  the full parse is paid on every launch. Fixable with a custom `app://` scheme
  registered with `codeCache: true`, but it changes how the renderer loads.

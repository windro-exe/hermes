# Streaming renderer: two real fixes, and four claims that didn't survive measurement

**Date:** 2026-07-27
**Type:** Performance
**Branch:** `perf/ui-latency`

<!-- Commit sha intentionally omitted: this entry ships in the commit it
     describes, so any sha here would be a guess or go stale on amend/rebase.
     Find it with: git log --oneline -- <path to this file> -->

## Why

The plan for this phase listed five streaming-path fixes. Measuring them first
killed three and confirmed two. The rejections are the more useful half of this
entry, because they are exactly the "problem" a future agent would otherwise
re-discover from the same stale reasoning.

Read the rejections before touching the streaming path again.

## What changed

### `visibleGroups` got a fresh array identity every render

- **`apps/desktop/src/components/assistant-ui/thread/list.tsx`** — wrapped in
  `useMemo` keyed on `[groups, hiddenCount]`.

  `const visibleGroups = hiddenCount > 0 ? groups.slice(hiddenCount) : groups`
  returns a new array on every render once anything is hidden. `visibleGroups` is
  a dependency of the `useMemo` that builds the rendered group elements, so that
  memo was invalidated on *every* render and rebuilt every element.

  The shape of the bug is what makes it worth fixing: the `hiddenCount === 0`
  branch already returns the stable `groups` reference, so short transcripts were
  fine. It only degraded once the render budget started hiding groups — i.e.
  precisely on the long transcripts where rebuilding all group elements costs the
  most. That is also why it went unnoticed.

### `messageContentText` re-concatenated on every store notification

- **`apps/desktop/src/components/assistant-ui/thread/content.ts`** — added a
  `WeakMap` memo on the array path.

  This function runs inside `useAuiState` selectors, which re-execute on every
  store notification. For a *settled* message it did
  `content.map(partText).join('').trim()` every time, so a transcript of settled
  messages re-concatenated all of its text on notifications that had nothing to
  do with any of it.

  Keyed on the content array reference, which is stable for a settled message.
  Guarded against in-place growth by also storing the part count and the last
  part's text length — if a part is appended or the tail grows, the key no longer
  matches and it recomputes. `WeakMap` so dropped messages are not retained.

  Streaming was already safe here: `AssistantMessage` selects `''` while
  `status.type === 'running'`, so the concat never ran mid-turn. The win is
  settled messages only.

## Rejected after measurement

### Bounding `preprocessMarkdown` to the tail — rejected, ~20x smaller than claimed

The plan said this pass "walks the whole message on every flush, ~30 times a
second" and estimated 45–140ms/s of CPU. Benchmarked with vitest on synthetic
messages of realistic shape (headings, prose, URLs, inline code, inline math, a
fenced code block every third section):

```
    545 chars  median 0.04ms  p95 0.09ms  -> at 30 flush/s =  1ms/s CPU
   2165 chars  median 0.11ms  p95 0.14ms  -> at 30 flush/s =  3ms/s CPU
   6499 chars  median 0.29ms  p95 0.32ms  -> at 30 flush/s =  9ms/s CPU
  13059 chars  median 0.56ms  p95 0.63ms  -> at 30 flush/s = 17ms/s CPU
```

Worst case is **17ms/s, under 2% of one core**. The original estimate was off by
roughly an order of magnitude.

It is also the riskiest change in the phase. `preprocessMarkdown` cannot be
tail-bounded without breaking correctness: `normalizeFenceBlocks` needs to know
whether a fence opened earlier is still unclosed, and `scrubBacktickNoise` scans
for balanced fences across the whole document. Both are whole-document state
machines. Trading a correctness risk in fence handling for 2% of a core is a bad
deal.

Note that the *remend* half is already tail-bounded upstream —
`preprocessWithTailRepair` is `tailBoundedRemend(preprocessMarkdown(text))`, and
`tailBoundedRemend` exists specifically to replace Streamdown's full-text
`parseIncompleteMarkdown`.

### The "footnote cliff" in `markdown-blocks.ts` — rejected, does not exist

The plan claimed a footnote in a message disables incremental block parsing for
the whole message. There is no footnote handling in that file at all. What is
there is `lexIncrementally`, the incremental lexer added in `bd4953b30`: it finds
a cached prefix, drops the last two content blocks (with a documented reason —
a trailing Setext underline can retroactively merge the previous parse's last two
blocks), and re-lexes only the tail. It also has a defensive reconstruction check
that falls back to a full lex if the `join(blocks) === text` invariant ever fails.

This is the same code upstream's own `apps/desktop/scripts/profile-typing-lag.md`
still describes as "Not fixed: the elephant". **That doc is stale** — the fix
landed 2026-07-18. Anyone reading it will chase a solved problem.

### The settled-text concat during streaming — rejected, already fixed upstream

The plan flagged `assistant-message.tsx` for concatenating message text on every
flush. Upstream already solved it, twice over: `MessageActionProps` takes a lazy
`getMessageText: () => string` accessor rather than the text itself, with a
comment explaining that passing the text re-renders the whole footer at 30Hz; and
the `completedText` selector returns `''` while running. The only gap left was
the settled case, which is the memo above.

### Re-keying the `components` map on `isStreaming` — rejected, wrong frequency

`markdown-text.tsx` has `useMemo(..., [disableArtifacts, isStreaming])`, so the
whole components object is rebuilt when `isStreaming` flips. But it flips **once
per turn**, not per token — the cost is two rebuilds per response, not 30 per
second. Not worth touching.

## Verified

```bash
cd apps/desktop
npm run typecheck                              # -> clean (all three tsconfigs)
npx vitest run --project ui                    # -> 1 failed | 2532 passed | 1 skipped
npx vitest run --project electron              # -> 17 failed | 751 passed | 2 skipped
npx vitest run --project ui src/__fork__/      # -> 7 passed
```

Both suites baselined by stashing the two changed files and re-running:

- `electron`: **17 failed / 751 passed with and without** the changes. All in
  `ssh-config`, `ssh-connection`, `desktop-installation`, `before-pack`,
  `stage-native-deps` — a different vitest project from the one these changes
  touch (`src/` vs `electron/`), so they could not be affected.
- `ui`: the single failure is
  `use-prompt-actions/utils.test.ts > renderRpcResult > session.usage`, identical
  with and without the changes. **Root cause found:** the machine's locale is
  `en-IN`, so `toLocaleString()` groups digits the Indian way — the test got
  `12,34,567` and expects `1,234,567`. An upstream test bug for Indian locales.
  The app's rendered output is arguably correct for this user; only the hardcoded
  expectation is wrong. Not fixed here — out of scope.

Fork guards added in `apps/desktop/src/__fork__/perf-guards.test.ts` (7 tests) and
mutation-checked: reverting both patches produces **2 failed / 5 passed**,
restoring gives 7 passed.

The memo guard is precise rather than timing-based — it counts reads of the
*first* content part via a getter. The staleness check only probes the *last*
part, so a first-part read after the initial call means the map/join re-ran.
Worth noting for anyone editing that test: `partText` reads `.text` twice per
call (once for the `typeof` check, once for the value), which made a naive
read-count assertion fail at first.

**Not verified:**
- No before/after frame-time or CPU measurement in the running app. Both fixes
  remove provable redundant work; neither has an observed effect on perceived
  smoothness.
- The `visibleGroups` fix only engages past the render budget. Not exercised with
  a real long transcript — no measurement of how many group elements were being
  rebuilt per render.

## Risk / watch for

- **The memo assumes content arrays are replaced, not mutated in the middle.**
  Append-style growth is caught by the part-count and tail-length guard. A
  mutation that edits a *middle* part while keeping both the part count and the
  last part's length identical would serve stale text. That does not happen in an
  append-only streaming model, but it is the failure mode to look for if a copy
  button ever shows out-of-date text.
- **`visibleGroups` must stay memoized on both dependencies.** Dropping
  `hiddenCount` would pin the visible window; dropping `groups` would freeze the
  transcript. The guard only checks that a `useMemo` is present, not that the
  dependency list is right.
- **The source-level guard for `visibleGroups` is crude.** It regex-matches the
  declaration in `list.tsx`. A rename or reformat will fail it spuriously — but
  the alternative is no guard at all, since removing the memo changes no
  behaviour and breaks no other test.

## Follow-ups

- Upstream's `apps/desktop/scripts/profile-typing-lag.md` is stale in a way that
  actively misleads: its "Not fixed: the elephant" section describes work that
  landed in `bd4953b30`. Worth an upstream PR; not this fork's job.
- The `en-IN` locale test bug above is a genuine upstream defect. Any hardcoded
  `toLocaleString()` expectation in the suite will fail for non-US locales.
- `preprocessMarkdown` is cheap but not free (0.56ms on a 13KB message). If
  transcripts get much longer, the honest fix is caching on the settled prefix,
  not tail-bounding the fence logic.

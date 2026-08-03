# A toast pushed off screen never learned it was gone

**Date:** 2026-08-03
**Type:** Fixed
**Branch:** `main`

<!-- Commit sha omitted: ships in the commit it describes. Find it with:
     git log --oneline -- <path to this file> -->

## Why

Noted as a follow-up while fixing the update toast, then fixed here.

`notify()` keeps at most four toasts on screen. A fifth pushed the oldest out
with `.slice(0, 4)` and nothing else, which had two consequences:

- the evicted toast's `onDismiss` never ran, so any handler that cleans up or
  records state was silently skipped
- its auto-dismiss timer stayed in the `timers` map, later firing
  `dismissNotification` against an entry that no longer existed

The second is harmless today (dismissing an absent id is a no-op) but it is a
leak, and it means a stray timer outlives the thing it belonged to.

It matters most for the update toast. If a burst of other notifications pushes it
off screen, the user never saw it — so it must stay announceable rather than be
recorded as dismissed.

## What changed

- **`apps/desktop/src/store/notifications.ts`** — the cap is now a named
  constant (`MAX_VISIBLE_NOTIFICATIONS`), and `notify()` computes the evicted
  tail explicitly: it clears each evicted toast's timer and calls its
  `onDismiss('programmatic')`.

  `'programmatic'` is the correct reason: the user never saw these, so nothing
  downstream should treat eviction as a decision. The update toast reads exactly
  this distinction, so an evicted update stays announceable.

  Replacement by id is unaffected — that path filters the old entry out before
  the cap applies, so it reports nothing.

## Verified

```bash
cd apps/desktop
npx tsc -p . --noEmit                                # -> clean
npx eslint src/store/notifications.ts                # -> clean
npx vitest run --project ui src/__fork__/            # -> 61 passed
npx vitest run --project ui src/store/               # -> included above, 540 passed overall
```

New guards in `src/__fork__/notification-eviction.test.ts` (7 tests): the cap
holds, the newest survives, an evicted handler runs exactly once with
`'programmatic'`, several simultaneous evictions all run, a still-visible toast
does not fire, and same-id replacement is not an eviction.

Deliberately a separate file — `update-toast.test.ts` mocks
`@/store/notifications` wholesale, so it cannot exercise the real `notify`.

Mutation-checked:

| mutation | result |
|---|---|
| back to a bare `.slice()` (the original bug) | 2 failed |
| eviction reports `'user'` instead of `'programmatic'` | 2 failed |

**Not verified:** not observed in the running app. Reaching it needs five
concurrent toasts, which is hard to stage deliberately; the behaviour is covered
by unit tests against the real store rather than by an end-to-end sighting.

## Risk / watch for

- **Eviction now runs arbitrary handler code inside `notify()`.** Handlers are
  small today. A handler that itself calls `notify()` would re-enter — not a
  cycle now, but worth remembering.
- **The cap is 4 and now named.** Anything that relies on the old literal will
  not track a change to the constant.

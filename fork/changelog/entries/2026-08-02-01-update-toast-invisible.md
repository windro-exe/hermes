# The update toast fired and something silently dismissed it

**Date:** 2026-08-02
**Type:** Fixed
**Branch:** `main`

## Why

windro pushed a commit and saw no toast. The check was not the problem — attaching
to the running app over CDP showed it working perfectly:

```
supported: true
behind: 1
currentSha: b4043e795   targetSha: 89497f78b
hermesRoot: C:\Users\wnxdd\AppData\Local\hermes\hermes-agent
```

The same probe showed `localStorage` already holding a snooze for **that exact
sha**. So the toast had fired and something dismissed it within about two
minutes, without windro touching it.

Two layers were responsible, and the second one was mine.

**`onDismiss` fired for any removal.** `notifications.ts` called
`onDismiss` from `dismissNotification` and `clearNotifications` alike, with no way
for a handler to tell "the user closed this" from "unrelated code cleared the
tray". And `clearNotifications()` runs on ordinary session activity — three call
sites in `use-prompt-actions`, four in `use-session-actions`, one in `submit.ts`.
Sending a message ran the update toast's dismiss handler.

**That handler wrote a persisted 24h snooze.** My earlier fix replaced upstream's
time-only snooze with a per-sha one, which was the right idea, but it still
persisted to `localStorage`. Combined with the above: a routine action wrote a
day-long suppression for an update the user had never seen, and it survived
restarts.

## What changed

- **`src/store/notifications.ts`** — dismissals now carry a reason.
  `NotificationDismissReason` is `'action' | 'programmatic' | 'user'`;
  `onDismiss` receives it; `dismissNotification` and `clearNotifications` take it
  and default to `'programmatic'`. Defaulting that way is deliberate: any existing
  caller that has not been taught about the distinction is, by definition, not the
  user.

- **`src/components/notifications.tsx`** — the X button reports `'user'`, the
  action button `'action'`, and "Clear all" `'user'`. That last one was
  `onClick={clearNotifications}`, which would have passed the `MouseEvent` as the
  reason — typecheck caught it.

- **`src/store/updates.ts`** — the persisted snooze is gone entirely: no storage
  key, no 24h cooldown, no burst floor. A dismissal is remembered in a module-level
  `Set` of dismissed shas, and only when `reason !== 'programmatic'`. Exported
  `resetUpdateToastDismissals()` as a test seam standing in for a relaunch.

The behaviour windro asked for, precisely:

| situation | before | now |
|---|---|---|
| new commit pushed | suppressed if anything was dismissed in 24h | always announced |
| user closes the toast | silent 24h, across restarts | silent for this run only |
| quit and reopen | still silent | announced again |
| prompt submit / session switch | silently snoozed the update | no effect |

## Fork divergence, on purpose

Two upstream tests in `src/store/updates.test.ts` asserted the *old* rule — a
dismissal starting a 24h clock that also swallowed new commits. They were rewritten
rather than deleted, with the reasoning recorded inline: that rule is correct for
the official repo, which lands on the order of a hundred commits a day, where a
per-commit toast would be spam. This fork is one person's repo where every push is
deliberate and learning that it landed is the whole point.

`src/__fork__/update-toast-snooze.test.ts` was **deleted**. It guarded the 24h
window and burst floor that no longer exist, and every behaviour in it worth
keeping is covered by the replacement.

## Verified

```bash
cd apps/desktop
npm run typecheck                              # -> clean, all three tsconfigs
npx vitest run --project ui                    # -> 1 failed | 2589 passed | 1 skipped
npx vitest run --project ui src/__fork__/      # -> 54 passed
npx vitest run --project ui src/store/updates.test.ts   # -> 26 passed
```

The single remaining failure is the pre-existing en-IN locale bug in
`use-prompt-actions/utils.test.ts` (the machine's locale groups digits
`12,34,567`; the test hardcodes `1,234,567`). Unrelated, and present before this
change.

New guards in `src/__fork__/update-toast.test.ts` (12 tests), mutation-checked
three ways — each of these fails a test:

- making a programmatic clear snooze again
- making a dismissal survive a relaunch
- ignoring the sha, i.e. reverting to upstream's time-only rule

**Not verified:** the fix is not yet in the installed binary. The build at
`C:\Program Files\Hermes` is from 08-02 23:16, before this change, so the app
windro is running still has the old behaviour. It needs a rebuild before the toast
can be observed.

## Risk / watch for

- **`clearNotifications()` now reports `'programmatic'` for every existing
  caller.** That is right for the update toast, but any *other* handler that
  wanted to treat a tray clear as a dismissal would silently stop doing so. Only
  the skew and install-method toasts use `onDismiss`, and both snooze on their own
  persisted keys regardless of reason, so neither changes behaviour.
- **Nothing is persisted, by design.** Someone who dismisses the toast and
  restarts ten times gets ten toasts. That is the requested behaviour ("should
  re-fire after close and reopen"), not an oversight.
- **The in-memory `Set` is module state**, so it leaks between tests in the same
  file. `updates.test.ts` needed `resetUpdateToastDismissals()` in its
  `beforeEach` for exactly this reason — worth remembering when adding cases.

## Follow-ups

- `scripts/perf/diag-updates.mjs` is the CDP probe written to diagnose this. It is
  genuinely useful for any "the UI is not doing the thing" question, but it is
  currently a scratch tool. Either keep it deliberately as a fork tool with a
  short README note, or delete it.
- Two gotchas for whoever next attaches to the packaged app: `launch.mjs`'s
  `attach()` calls `requireDriver()`, which the production build does not expose,
  so use `CDP.open` directly; and `discoverTarget({ match })` matches a URL
  substring, not a predicate.
- The throwaway commit `89497f78b` on `origin/main` exists only so the installed
  build had something to be behind. It appends to `fork/changelog/README.md` and
  should be reverted once testing is done.

# The update toast fired, then a routine action silently dismissed it

**Date:** 2026-08-02
**Type:** Fixed
**Branch:** `main`

**Supersedes** the persisted-snooze design in
`entries/2026-08-02-01-update-toast-invisible.md` — that entry's mechanism is
removed here, for the reasons below.

<!-- Commit sha omitted: ships in the commit it describes. Find it with:
     git log --oneline -- <path to this file> -->

## Why

windro pushed a commit and saw no toast at all. Reading the source proved
nothing, so I attached to the running packaged app over its debug port and asked
it directly (`apps/desktop/scripts/perf/diag-updates.mjs`, added alongside this).

The check was **working perfectly**:

```
supported: true, behind: 1,
currentSha: b4043e795, targetSha: 89497f78b,
hermesRoot: C:\Users\wnxdd\AppData\Local\hermes\hermes-agent
```

And `localStorage` already held a snooze **for that exact target sha**, written
about two minutes earlier — while windro had never seen or touched a toast. So
the toast had fired and something had dismissed it.

Two layers, the second one mine:

1. **`notifications.ts` called `onDismiss` for any removal.** A handler could not
   tell "the user closed this" from "unrelated code called
   `clearNotifications()`". And `clearNotifications()` runs all over the session
   code — `use-prompt-actions` (three sites), `use-session-actions` (four),
   `submit.ts`. Submitting a prompt or switching session fired the update toast's
   `onDismiss`.

2. **That `onDismiss` wrote a persisted 24-hour snooze.** So a routine action
   didn't just hide the toast, it suppressed the update for a day, across
   restarts.

Both together: the toast appeared and was consumed by the next thing windro did,
then stayed suppressed. Indistinguishable from a broken feature.

## What changed

### Dismissals now say why

- **`apps/desktop/src/store/notifications.ts`** — new
  `NotificationDismissReason` (`'action' | 'programmatic' | 'user'`).
  `onDismiss` receives it; `dismissNotification(id, reason)` and
  `clearNotifications(reason)` both default to `'programmatic'`, so every
  existing caller keeps working and is correctly classified as *not* a user
  dismissal.

- **`apps/desktop/src/components/notifications.tsx`** — the ✕ passes `'user'`,
  the action button passes `'action'`, and "Clear all" passes `'user'`. That last
  one was `onClick={clearNotifications}`, which would have handed the `MouseEvent`
  in as the reason — caught by typecheck once the parameter existed.

### The snooze is per-run, not persisted

- **`apps/desktop/src/store/updates.ts`** — the localStorage snooze, its 24-hour
  window and its one-hour burst floor are all gone, replaced by an in-memory
  `Set` of dismissed target shas plus an exported
  `resetUpdateToastDismissals()` test seam. The toast's `onDismiss` only records
  a dismissal when the reason is not `'programmatic'`.

This is the behaviour windro asked for, stated as rules:

| situation | before | now |
|---|---|---|
| new commit pushed | suppressed if anything was dismissed in the last 24h | always announced |
| you close the toast | quiet 24h, across restarts | quiet for this run only |
| quit and reopen | still quiet | announced again |
| prompt submit / session switch | **silently snoozed for 24h** | no effect |
| update applied | — | toast removed, nothing recorded |

So it keeps reminding until the update is actually installed, and dismissing it
buys quiet until you next launch — not a day of silence.

## Verified

Live, over CDP, against the packaged app — the numbers in the *Why* section are
real output from the running renderer, not a simulation.

```bash
cd apps/desktop
npm run typecheck                              # -> clean (3 tsconfigs)
npx eslint src/store/updates.ts src/store/notifications.ts \
           src/components/notifications.tsx    # -> clean
npx vitest run --project ui src/__fork__/      # -> 54 passed
```

New guards in `src/__fork__/update-toast.test.ts` (12 tests) cover each rule in
the table, and are mutation-checked three ways:

| mutation | result |
|---|---|
| programmatic clear snoozes again (the original bug) | 1 failed |
| dismissal persists across relaunch | 2 failed |
| snooze ignores the sha (upstream's time-only behaviour) | 1 failed |

`src/__fork__/update-toast-snooze.test.ts` was **deleted**, not amended: its
remaining assertions guarded the 24-hour window and the burst floor, which are
exactly what this change removes. Everything in it still worth asserting is
covered by the new file.

**Not verified:** the packaged binary at `C:\Program Files\Hermes` was built
before this change, so the behaviour above has not been observed end-to-end in
the installed app — only in unit tests and by reasoning from the live diagnosis.
It needs a rebuild.

## Risk / watch for

- **`onDismiss` handlers now receive an argument.** The two other toasts in
  `updates.ts` (`snoozeSkewToast`, `snoozeInstallMethodToast`) take no parameter
  and are unaffected, but any new handler must decide whether a programmatic
  clear should count.
- **A long-running window announces an update once.** The set lives for the app's
  lifetime, so after a dismissal the six-hourly re-check stays quiet for that
  same sha until relaunch. A new commit still announces immediately. If windro
  leaves the app open for days and wants nagging, that is a deliberate follow-up,
  not an oversight.
- **Nothing is persisted at all now**, so there is no migration and no stale
  state — but also no memory of dismissals across restarts by design. The old
  `hermes:update-toast-snooze-until` and `hermes:update-toast-snooze` keys are
  simply ignored and will sit in localStorage until someone clears it.

## Follow-ups

- The diagnostic (`scripts/perf/diag-updates.mjs`) is worth keeping and worth
  extending: it is the only way to see what the packaged renderer actually
  believes. Note `discoverTarget`'s `match` is a URL substring, not a predicate,
  and `launch.mjs`'s `attach()` requires the perf driver the production build
  does not expose — use `CDP.open` directly.
- `notify()` caps the list at four with `.slice(0, 4)`, which drops the fifth
  notification without calling its `onDismiss`. Harmless for the update toast now
  that dismissal is only a hint, but it means a dropped toast vanishes with no
  handler run at all.

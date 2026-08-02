# Project rules and IDEA.md, editable entirely in the UI

**Date:** 2026-07-29
**Type:** Added
**Branch:** `feat/project-rules`

<!-- Commit sha omitted: ships in the commit it describes.
     Find it with: git log --oneline -- <path to this file> -->

## Why

The loader landed in the previous entry, but using it meant hand-editing markdown
files in a hidden directory. windro asked for the opposite: *"make everything ui
based"*. Rules you can't see or toggle without a text editor are rules you won't
maintain.

## What changed

Sidebar → project kebab menu → **Rules** opens one dialog covering both files the
Python loader reads for a project.

- **`apps/desktop/src/store/project-rules.ts`** (new) — the data layer. Lists,
  reads, writes, creates and deletes `.hermes/rules/*.md`, plus `IDEA.md`.

- **`apps/desktop/src/app/chat/sidebar/projects/project-rules-dialog.tsx`** (new)
  — one card per rule file with an enable toggle, a delete button, and a textarea
  of the rules; an add-file row; and an `IDEA.md` editor at the bottom. Save
  buttons appear only when a draft differs from disk.

- **`project-menu.tsx`** — one added menu item. Enabled whenever the project has a
  folder, *including auto-adopted git repos*: unlike rename and add-folder, rules
  need only a path, not a `projects.db` record.

- **`sidebar/index.tsx`** — the dialog mounted next to the existing
  `<ProjectDialog />`, following the same single-mounted-dialog-reads-an-atom
  pattern.

### The toggle is the file

Disabling a rule file writes `mode: manual` frontmatter, which the loader already
treats as not-always-on and skips. Enabling strips the frontmatter entirely (no
header means always-on).

This is worth calling out because of what it avoids: there is **no separate
enabled/disabled state anywhere** — no database column, no settings key, nothing
that can drift out of sync with the files. It also means a rule disabled in the
desktop is disabled for the CLI and TUI too, which is what anyone would expect
and would not have been true of a UI-only toggle.

### Editing model: one rule per line

A textarea per file, one rule per line, `- ` bullets on disk. Chosen over a
row-per-rule form deliberately: it round-trips a hand-written file with no
markdown parser, and there is no per-row component state that can desynchronise
from disk. Non-bullet prose in an existing file is preserved as its own line
rather than silently dropped.

### Everything goes through `desktop-fs`, not raw IPC

`readDesktopDir` / `readDesktopFileText` / `writeDesktopFileText` /
`trashDesktopPath` rather than `ipcRenderer` directly. Those wrappers fall back to
the gateway's REST filesystem when the desktop is driving a **remote** gateway.
Raw IPC would have worked perfectly on this machine and silently failed for any
remote project — the kind of bug that only shows up in someone else's setup.

## Verified

```bash
cd apps/desktop
npm run typecheck                          # -> clean (all three tsconfigs)
npx eslint <the four touched files>        # -> clean
npx vitest run --project ui src/__fork__/  # -> 36 passed
npx vitest run --project ui src/app/chat/sidebar/ src/store/   # -> 577 passed
npx vitest run --project ui                # -> 2570 passed, 1 failed, 1 skipped
```

The single full-suite failure is
`use-prompt-actions/utils.test.ts > renderRpcResult > session.usage`, the
pre-existing **en-IN locale** bug already documented in
`2026-07-27-05-streaming-renderer.md`: this machine's locale groups digits the
Indian way, so the test gets `12,34,567` where it hardcodes `1,234,567`. Nothing
to do with this change.

20 new store tests, and their central purpose is asserting the store agrees with
`_rule_is_always_on` in `agent/prompt_builder.py`. If those two ever disagree, the
UI shows a rule as active that the agent never receives — precisely the failure
this feature exists to prevent.

### A bug my own test caught

`splitFrontmatter` returned the frontmatter block *without* its trailing newline,
so saving a file that already had frontmatter emitted `---- rule` — the closing
fence glued to the first bullet, which the loader then reads as body text rather
than a header. Fixed at both points (normalise on split, guard on serialise) and
mutation-verified: reverting both lines fails exactly two tests.

**Not verified:** the dialog has not been driven in the running app. Typecheck,
lint, and the store's logic are covered; the rendering, the toggle's visual
state, and the save-button behaviour are not. That needs the rebuilt binary.

## Risk / watch for

- **Two implementations of one rule-activation policy.** The store's
  `readFrontmatterFlags` mirrors the Python `_rule_is_always_on`. They are kept in
  step by tests on both sides, but a future change to either must update both.
  This is the main maintenance cost of the feature.
- **Deleting a rule file uses the OS trash**, not an unlink, so a mis-click is
  recoverable. Worth preserving.
- **The dialog reloads from disk after every mutation.** Simple and always
  correct, but it means an external edit made while the dialog is open is picked
  up on the next toggle or save rather than live.
- **UI strings are inline English**, with no i18n keys added. Deliberate — adding
  keys means touching every locale file, and this is a fork feature. It does mean
  the dialog stays English regardless of the app's language setting.

## Follow-ups

- i18n keys for the dialog, if this ever goes upstream.
- **The journal** — `.hermes/journal/YYYY-MM-DD.md`, appended by the agent on
  request, never injected, with a UI browser to read and search it. The remaining
  piece of the agreed scope.
- A rename affordance for rule files (currently: create the new one, delete the
  old).
- The dialog is reachable only from the project kebab menu. A settings entry point
  would help discoverability.

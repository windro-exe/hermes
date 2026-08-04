# Connect a GitHub account, and clone or create a repo when making a project

**Date:** 2026-08-04
**Type:** Added
**Branch:** `main`

<!-- Commit sha omitted: ships in the commit it describes. -->

## Why

Requested: creating a project should offer a folder **or** GitHub — pick an existing
repo and clone it, or create a new one — and a folder that is already a repo should
be offered a remote rather than nagged about it.

It also completes something that already existed but could not be reached. Upstream
already ships branch-based sessions: `worktree-dialog.tsx` creates or opens a branch
worktree and `requestStartWorkSession(path)` anchors a session to that checkout, so
each branch gets its own directory, files panel, terminal and lane. That machinery
needs the project folder to be a git repo. windro's folders were not, so the feature
was invisible to him. `git init` on create (previous entry) plus a remote here makes
it usable.

## What changed

### GitHub account

- **`electron/github-ops.ts`** (new) — identify, list repos, create repo, clone,
  connect-remote. Nothing else talks to GitHub.
- **`electron/main.ts`** — token stored through `encryptDesktopSecret` (Electron
  `safeStorage` → OS keychain) in a `0600` file under userData, reusing the pattern
  the native OAuth tokens already use. Five IPC handlers plus `hermes:git:remoteList`.
- **`src/store/github.ts`** (new) — connection state, repo cache, and the
  operations the UI needs.

**A pasted personal access token, not a device flow.** The device flow needs a
registered GitHub OAuth app, which is account-level setup this fork cannot create on
someone's behalf. A fine-grained PAT needs no registration and can be scoped to a
single repository. Device flow stays open for anyone who registers an app.

### Security properties, and why each one

- **The token never crosses into the renderer.** `status()` answers "connected, and
  as whom" — there is no `getToken`. That keeps it out of the React tree, out of
  devtools, and out of any renderer crash dump.
- **Validated before it is stored.** A typo cannot leave the app believing it is
  connected; the handler calls `identify()` first and only writes on success.
- **Never written into git metadata.** Clone and push pass the credential through a
  per-invocation `http.extraHeader`, so it never lands in `.git/config`, the reflog,
  or the remote URL that `git remote -v` prints. A token embedded in a clone URL is
  the usual shortcut here and it persists on disk.
- **New repositories are private by default.** Publishing someone's working code on
  a misclick is not recoverable the way flipping a private repo public is.

### Project dialog

- **`github-repo-picker.tsx`** (new) — connect / pick / create in one place, because
  from the user's side it is one decision. Repo names are normalised to GitHub's
  allowed characters up front, so the name shown is the name created.
- **`project-dialog.tsx`** — a "From GitHub" button beside "Add folder". Picking a
  repo asks for a parent directory and clones into `<parent>/<repo-name>`, matching
  what `git clone` does in a terminal instead of cloning into a directory that may
  already hold unrelated files. Fills the project name when it is still empty.

### Remote offer

- **`src/store/projects.ts`** — after creating a project, a persistent toast offers
  to connect a remote. Deliberately quiet unless it can help: it needs the GitHub
  bridge, a connected account, and a repo with no `origin`. Offering this with no
  account connected would spring a token prompt nobody asked for.
- **`project-remote-dialog.tsx`** (new) — what the toast's Connect button opens. A
  button that sets an atom nothing reads is worse than no button.

`durationMs: 0` is load-bearing: `clearNotifications()` fires on prompt submit and
session switch, so a timed toast would vanish before it was read. That is the same
trap as the update toast.

**A connected remote with a failed push reports as its own outcome**, not as
success or failure. A protected branch, no permission, or diverged history all land
there, and the remote really is wired.

### Remote-gateway mode

The GitHub surface is Electron-only, and that is deliberate rather than an
oversight: cloning happens on the machine the user is sitting at. `githubAvailable()`
is false without the bridge, so the dialog offers only the local path instead of
showing a button that cannot work. `remoteList` returns `[]` there, which keeps the
remote offer quiet rather than wrong.

## Verified

```bash
cd apps/desktop
npm run typecheck                                  # -> clean (3 tsconfigs)
npx eslint src/app/chat/sidebar src/store/github.ts electron/github-ops.ts  # -> clean
npx vitest run --project ui src/__fork__/          # -> 110 passed
```

23 new guards in `src/__fork__/github-project-flow.test.ts`, mutation-checked on
the three properties whose failure would be silent:

| mutation | result |
|---|---|
| token moved into the clone URL | 1 failed |
| new repos default to public | 1 failed |
| token stored before validation | 1 failed |

One mutation initially appeared "not caught" — the mutation script's `str.replace`
had silently not matched while still printing success. Re-run with an assertion, the
guard failed correctly. Worth recording because a mutation check that cannot fail is
indistinguishable from a passing one.

**Not verified:** none of this was exercised against real GitHub. No token was
connected, no repository listed, created, cloned, or pushed, and the dialog was not
clicked through. Every guard is a unit test or a source-level assertion. The API
shapes come from GitHub's documented REST v3 responses, not from observed traffic —
so field-name mistakes in `toRepo()` would survive all of it. First real use should
be treated as the actual test.

## Risk / watch for

- **Rate limits and pagination.** The list caps at two pages (200 repos) because it
  feeds a picker, not a sync. An account with more will not show everything; search
  filters the fetched set, not GitHub.
- **Clone destination collisions.** Cloning into `<parent>/<repo-name>` will fail if
  that directory exists. Git's own error surfaces, which is honest but not pretty.
- **The token is a PAT with whatever scope the user granted.** Nothing verifies it is
  narrowly scoped; a classic token with full `repo` access works and so does a
  fine-grained one. Worth recommending fine-grained in the UI copy later.
- **`connectRemote` replaces an existing `origin`** rather than refusing. The
  realistic reason one exists is a previous half-finished attempt, but it does mean
  the operation is not purely additive.

## Follow-ups

- Device flow, for anyone who registers an OAuth app — no paste, and revocable
  per-device.
- Surface branch-based sessions more visibly now they can actually work. The
  worktree dialog is reachable from the workspace header, the entered-project view
  and the command palette, but not from the project right-click menu, which is where
  someone would look first.
- Audio attachments are unrelated but were found while testing this: the turn payload
  carries only `attached_images`, so an attached `.ogg` reaches the model as nothing
  at all. `/api/audio/transcribe` already exists for the mic path and could serve it.

# Sign in to GitHub through the browser, and manage it from Settings

**Date:** 2026-08-04
**Type:** Added
**Branch:** `main`

## Why

The first version made you paste a personal access token. windro's reaction was fair:
"we can use web login auth this key is so complicated".

## What changed

**GitHub device flow.** `electron/github-ops.ts` gains `startDeviceFlow` /
`pollDeviceFlow`: the app asks GitHub for a device code, opens the browser to
`github.com/login/device`, and polls until you approve. No token to copy.

Device flow needs a registered OAuth app, so windro created one and gave me the client
id. It is committed in the source — a client id is public by design. The client secret
he also sent is **not used and not stored**: device flow is for public clients that
cannot hold a secret. He was told to rotate it since it had been pasted into a chat.

**Settings -> GitHub** (`src/app/settings/github-settings.tsx`, new `'github'` view):
shows the connected login with a sign-out button, or a sign-in button when not
connected. The token is global to the app, not per project, so signing in once during
project creation shows as connected everywhere — which is what windro asked for.

The paste path is kept as a fallback: it is the only option offline, or when you want a
fine-grained token scoped to a single repo.

## Verified

`npm run typecheck` clean, `npx eslint src/app/settings/` clean, settings tests 237
passing, fork guards mutation-checked three ways (a client_secret appearing in the
bundle, `authorization_pending` treated as fatal, and storing the device token without
validating it each break a test).

**Not verified:** at the time of writing, no sign-in had been performed against real
GitHub. See the agent-auth entry — a later live test found two real bugs that none of
these guards caught.

# Installer points at the fork, so pushing is the whole update mechanism

**Date:** 2026-08-02
**Type:** Changed
**Branch:** `main`

<!-- Commit sha omitted: this entry ships in the commit it describes. -->

## Why

windro wanted an installer he could hand to friends, where pushing to his fork
gives them an update. Two questions had to be answered first: whether that needs
GitHub Actions or published Releases (it does not), and whether the installer
could skip cloning entirely (it cannot, without breaking updates).

The packaged app is only the Electron GUI shell — it contains no Python. On first
launch `bootstrap-runner.ts` downloads `scripts/install.ps1` from
`raw.githubusercontent.com` **at the commit recorded in
`install-stamp.json`**, and that script clones the agent into
`%LOCALAPPDATA%\hermes\hermes-agent`.

Both halves of that were pointed at `NousResearch`, and the first one is fatal
for a fork build: the stamped commit exists only in the fork, so requesting it
from NousResearch is a guaranteed 404 and first launch dies before it starts.

The clone is also what makes self-update work. `checkUpdates()` in
`apps/desktop/electron/main.ts` treats a non-official origin by fetching
`origin/<branch>` and comparing `HEAD` against it — so whatever `install.ps1`
clones becomes the thing updates follow. Pointing it at the fork means a push is
all it takes; leave it on NousResearch and installs silently track upstream.

## What changed

- **`apps/desktop/electron/bootstrap-runner.ts`** — the raw install-script URL.
- **`scripts/install.ps1`** — a new `$RepoSlug` drives `$RepoUrlSsh`,
  `$RepoUrlHttps` and the three archive-zip fallback URLs.
- **`scripts/install.sh`** — the same via `$REPO_SLUG`.

Each site carries a comment cross-referencing the other two: they must agree, or
the bootstrap and the updater disagree about which repo is upstream.

## Why the clone stays

`install.ps1` clones `--depth 1` — one commit, no history. That already satisfies
"not the whole repo".

Going further and dropping git is the trap. `install.ps1` has a zip fallback, and
it cannot be the default: with no `.git`, `checkUpdates()` reports
`not-a-git-checkout` and the install never sees another update. The alternative —
bundling CPython and the agent into the executable — would need
`electron-updater` (not a dependency here) plus published Releases and a CI
workflow, which is exactly the machinery this avoids.

## Verified

```bash
git rev-parse HEAD                      # -> 2e845306…, present on origin/main
curl -o /dev/null -w '%{http_code}' \
  https://raw.githubusercontent.com/windro-xdd/hermes-agent/2e845306…/scripts/install.ps1
# -> 200
```

The served script contains `$RepoSlug = "windro-xdd/hermes-agent"`, so the
bootstrap fetches a script that clones the fork. Electron tests 17 pass;
`tsc -p tsconfig.electron.json` clean. `npm run dist` produced
`Hermes-0.17.0-win-x64.exe` (112.6 MB, NSIS, `oneClick: false`) and a 126.5 MB
MSI.

**Not verified:** the installer has not been run. Everything upstream of the
bootstrap checks out, but no machine has executed it against this fork yet — the
first real proof is a clean VM or a friend's machine.

## Risk / watch for

- **Three URLs must stay in step.** If a future merge reverts one, the failure is
  asymmetric and confusing: an installer that clones upstream still *works*, it
  just quietly stops following the fork.
- **The build is unsigned** (`code signing skipped` in the build log).
  SmartScreen will warn; signing needs a paid certificate.
- **The stamped commit must be pushed before sharing a build.** The stamp pins
  the commit the bootstrap fetches from, so building from an unpushed commit
  produces an installer that 404s on first launch.
- **A git pull updates the Python agent, not the Electron shell.** UI changes
  still require installing a new `.exe`; the status bar's sha comes from a live
  `git rev-parse HEAD`, so it reflects the agent checkout.

## Follow-ups

- Friends need their own model provider — the kiro proxy on `127.0.0.1:8081` is
  local to windro's machine.
- First launch clones, creates a venv and installs Python dependencies: several
  minutes, and it must not be interrupted.

<!-- Copy this file into entries/ as YYYY-MM-DD-NN-short-slug.md and fill every section. -->
<!-- Read fork/changelog/README.md first — the hard rules there are not optional. -->

# The installer and first-launch bootstrap pointed at a different, stale repo

**Date:** 2026-08-08
**Type:** Fixed
**Branch:** fix/installer-fork-slug


## Why

Three fork-owned edits carried the slug `windro-xdd/hermes-agent`. Pushes now go to
`windro-exe/hermes`, so all three were pointed somewhere else:

- `scripts/install.ps1` — `$RepoSlug`, which becomes the managed checkout's `origin`
- `scripts/install.sh` — `REPO_SLUG`, same role on Linux/macOS
- `apps/desktop/electron/bootstrap-runner.ts` — the `raw.githubusercontent.com` URL the
  desktop app fetches its install script from on first launch

`entries/2026-08-02-01-installer-points-at-fork.md` explains why these exist: the clone's
`origin` is what self-update fetches, so the slug *is* the update channel. A wrong slug
does not just break a download, it decides which repo every installed copy follows.

**Supersedes the "Why" of `entries/2026-08-08-01-fork-rules-remote-correction.md` on one
point.** That entry (and the `AGENTS.md` wording it introduced) claimed the repo was
"renamed `hermes-agent` -> `hermes` and transferred to a new account," and that GitHub's
redirect meant a wrong push would still land correctly. **Both halves are false.** They
are two separate repos:

- `windro-xdd/hermes-agent` — created 2026-07-25, a real GitHub fork of
  `NousResearch/hermes-agent`, last pushed 2026-08-05T16:35:39Z, **not archived, still
  live**, frozen at `9e118284c`.
- `windro-exe/hermes` — created 2026-08-05T16:43:35Z, eight minutes after that last push,
  `fork: false` and `parent: null`. A standalone repo that the tree was pushed into.

That distinction is the whole reason this matters. A redirect would have made the stale
slug harmless. Instead the old slug resolves to a real repo that stopped receiving
commits, so an install would succeed, self-update would keep working, and it would track
a dead line of development on an account that is being abandoned — with no error anywhere.
The desktop bootstrap is the one piece that fails loudly, and only because
`raw.githubusercontent.com` does not resolve a commit that isn't in the repo you name.

Ordering note for anyone rebuilding: `bootstrap-runner.ts` is bundled into the shipped
app, so an installer built before this fix has the 404 URL baked in. Fix, then package.

## What changed

- **`scripts/install.ps1`** (upstream file, existing fork block) — `$RepoSlug` now
  `windro-exe/hermes`. `$RepoUrlSsh` / `$RepoUrlHttps` derive from it and needed no edit.
  Comment rewritten to record that the old slug is a *different live repo*, not a former
  name, and to say plainly not to restore it.
- **`scripts/install.sh`** (upstream file, existing fork block) — same change to
  `REPO_SLUG`, same comment correction. Kept byte-parallel with the PowerShell copy
  because the three sites cross-reference each other.
- **`apps/desktop/electron/bootstrap-runner.ts`** (upstream file, existing fork block) —
  raw URL host path now `windro-exe/hermes`. Comment records the measured 404 and the
  fact that raw does not cross repos, which is the trap that makes this one fail while
  the other two fail silently.
- **`AGENTS.md`** (fork-owned FORK-RULES block) — replaced the incorrect
  rename/transfer/redirect sentence with the two-separate-repos account, and added that
  this repo is not a GitHub fork (`parent: null`), which is why there is no upstream link
  in the UI and why `upstream` is absent locally too.

All three code sites are edits to lines this fork already owned — no new upstream surface.
No behavioural change beyond the destination string.

## Verified

```
git ls-remote https://github.com/windro-xdd/hermes-agent.git HEAD  -> 9e118284c
git ls-remote https://github.com/windro-exe/hermes.git HEAD        -> c301cf51f
gh api /repos/windro-xdd/hermes-agent
  -> fork: true,  parent: NousResearch/hermes-agent, archived: false,
     created_at: 2026-07-25T15:44:42Z, pushed_at: 2026-08-05T16:35:39Z
gh api /repos/windro-exe/hermes
  -> fork: false, parent: (none),               archived: false,
     created_at: 2026-08-05T16:43:35Z
gh api /repos/windro-xdd/hermes-agent/commits/<sha>
  -> 9e118284c present; b7ad171d0 absent; c301cf51f absent
git rev-list --count 9e118284c..main                              -> 5
HEAD https://raw.githubusercontent.com/windro-xdd/hermes-agent/b7ad171d068e44e6e2f2c1896dbc93baace07e32/scripts/install.ps1
  -> HTTP 404
grep -r 'windro-xdd' -- after the edit, only historical mentions remain
  (this entry, entry 2026-08-08-01, entry 2026-08-02-01, and the AGENTS.md note)
```

Not verified: no test suite was run. This checkout still has no Python environment (`uv`
0.12.2 is installed, `uv sync` has not run), so `pytest` cannot execute. The desktop TS
was not typechecked either — `npm ci` completed at the repo root, but under npm 11 six
packages' install scripts are gated pending approval, including `electron`'s postinstall
that downloads the runtime binary, so a desktop build has not yet been attempted. Recorded
as an open gap rather than a pass.

Not verified: that the new raw URL returns 200. The commit carrying this fix does not
exist upstream of itself, so the URL cannot be exercised until after this lands and an app
is stamped with a commit that is on `main`. Check it right after merging:
`curl -sI https://raw.githubusercontent.com/windro-exe/hermes/$(git rev-parse main)/scripts/install.ps1`

Not verified: whether any already-installed copy is pointing at the old repo. Anything
installed from a script fetched before this change has `origin` set to the stale repo and
will not migrate on its own — see Follow-ups.

## Risk / watch for

- These three sites must move together. Two of them fail *silently* when wrong, which is
  why each comment now names the other two. If a future change edits one, grep for the
  slug before committing.
- The old repo is the live hazard, not a historical footnote. While
  `windro-xdd/hermes-agent` keeps existing, every wrong reference resolves to something
  plausible. Archiving or deleting it converts these from silent-wrong to loud-wrong,
  which is strictly better.
- Deleting the old repo has the opposite cost though: any installed copy still pointed at
  it starts failing its update fetch instead of quietly stagnating. Decide deliberately.
- `9e118284c` is quoted as the old repo's frozen HEAD. It is accurate today; if anything
  is ever pushed to that repo again this entry's account of it goes stale.
- This repo not being a GitHub fork means no "N commits behind" UI and no compare view
  against upstream. Divergence has to be measured with an explicit `upstream` remote,
  which per FORK-RULES must be added deliberately and never auto-synced.

## Follow-ups

- Already-installed copies keep the stale `origin`. A one-line migration is possible in
  the installer (`git remote set-url origin` when the existing remote matches the old
  slug) — not done here because it widens a small string fix into behaviour, and this
  fork's install base is believed to be one machine. Revisit if that stops being true.
- Decide whether to archive `windro-xdd/hermes-agent`. Archiving is the low-risk half of
  the trade above: it stops accidental pushes landing there without breaking reads.
- `uv sync` is still the gate on every Verified section in this repo having real test
  output. Until it runs, entries have to disclose an untested change.

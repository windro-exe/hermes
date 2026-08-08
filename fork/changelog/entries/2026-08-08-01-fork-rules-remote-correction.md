<!-- Copy this file into entries/ as YYYY-MM-DD-NN-short-slug.md and fill every section. -->
<!-- Read fork/changelog/README.md first — the hard rules there are not optional. -->

# FORK-RULES named a remote the fork no longer pushes to

**Date:** 2026-08-08
**Type:** Docs
**Branch:** docs/fork-rules-remote-correction


## Why

The `FORK-RULES` block at the top of `AGENTS.md` opens by telling every agent that
`origin` is `windro-xdd/hermes-agent` and that it is "the only place you push." Both
halves became false on 2026-08-08: the repo was renamed `hermes-agent` -> `hermes` and
transferred to a new account, so `origin` is now `windro-exe/hermes`. `windro-xdd` is an
abandoned account.

This is the one block whose entire job is preventing a push to the wrong remote, and it
was the thing stating the wrong remote. It also instructs agents to persist it to
long-term memory and re-read it every session, so the wrong fact propagates into every
future session instead of being noticed once.

The failure mode is quiet rather than loud. GitHub redirects both renames and transfers,
so a push to the old path currently lands in the right repo and looks fine. That
redirect dies if `windro-xdd` is deleted or if anyone reclaims the old name.

Two adjacent claims in the same block were stale for the same reason and are corrected
together, because leaving known-false lines in the block an agent is told to trust
defeats the point:

- "Current work: UI latency fixes on branch `perf/ui-latency`" — that work is merged
  (`8b819e69e`) and the branch is deleted. `git branch -a` shows only `main`. An agent
  following the line goes hunting for a branch that does not exist, or worse concludes
  the work was lost.
- "This directory is his live install" — asserted unconditionally. Not true of the
  2026-08-08 checkout, which has no venv and no `AppData\Local\hermes`.

## What changed

- **`AGENTS.md`** (fork-owned block only — the `FORK-RULES` region, not upstream's guide
  below it). Three edited paragraphs, no removals:
  - *Remotes* — now names `windro-exe/hermes`, records the rename/transfer so a future
    reader understands why old references exist, and warns that the redirect makes a
    wrong push look successful. Also states that `upstream` is **not configured in this
    checkout** and to confirm with `git remote -v` first; previously the block asserted
    upstream existed, which is what a diff-from-merge-base command would silently fail on.
  - *Live install* — turned from an assertion into an instruction to check, with the
    2026-08-08 checkout named as a counter-example. The rebuild command is unchanged and
    still applies where it IS the live install.
  - *Current work* — says nothing is in flight, names the merge commit and the changelog
    entry for the finished UI work, and keeps the still-true warning that upstream's
    `profile-typing-lag.md` is stale.

No upstream file was touched. The whole change is inside the fork's own
`FORK-RULES: START..END` region, which is designed to be deleted wholesale later.

## Verified

```
git -C . remote -v
  -> origin  git@gh-personal:windro-exe/hermes.git (fetch/push)   # only remote; no upstream
git branch -a --format='%(refname:short)'
  -> main, origin, origin/main                                    # no perf/ui-latency
git log --oneline --all --grep=ui-latency
  -> 8b819e69e Merge branch 'perf/ui-latency': UI latency work    # work is in main
Test-Path fork/changelog/entries/2026-08-04-09-ui-perf-four-fixes.md  -> True
Test-Path venv/Scripts/python.exe, .venv/Scripts/python.exe           -> False, False
Test-Path $env:LOCALAPPDATA\hermes, $env:USERPROFILE\.hermes          -> False, False
```

Not verified: no test suite was run. This checkout has no Python environment yet (`uv`
was installed at 0.12.2 but `uv sync` has not been run), so `pytest` cannot execute.
The change is confined to markdown inside a fork-owned block with no importable code, so
there is nothing for a test to exercise — but that is reasoning, not a green run, and it
is recorded here as such.

Also not verified: whether the old `windro-xdd/hermes-agent` path still redirects on a
real push. Inferred from GitHub's documented rename/transfer redirect behaviour, not
tested — testing it would mean pushing to the wrong remote on purpose.

## Risk / watch for

- The corrected remote is only correct until the next account move. If `origin` changes
  again, this block is the first thing to update, before any code.
- `upstream` is described as absent. The moment someone runs
  `git remote add upstream https://github.com/NousResearch/hermes-agent.git`, that
  sentence becomes stale in the opposite direction. It says to check with
  `git remote -v`, which stays true either way — keep that phrasing if editing.
- The *Current work* paragraph goes stale by design every time a branch is cut. Treat it
  as a slot to overwrite, not a fact to preserve.
- The `8b819e69e` sha is quoted deliberately (a merge commit already on `main`, not this
  entry's own commit). It would break if `main` is ever rebased or history rewritten.

## Follow-ups

- The block still says his data lives in `AppData\Local\hermes\` and to back it up with
  `hermes backup`. That directory does not exist on this machine. The *rule* is still
  correct and conditional ("never touch it"), so it was left alone — but if Hermes is
  reinstalled somewhere else, confirm the path rather than trusting it.
- `uv sync` still needs to run before any change to this repo can be test-verified. Until
  then every entry will have an empty Verified section for tests, which the changelog
  rules treat as a defect to disclose rather than hide.

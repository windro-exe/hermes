# `hermes update` no longer resets away unpushed commits

**Date:** 2026-08-02
**Type:** Fixed
**Branch:** `main`

<!-- Commit sha omitted: ships in the commit it describes. Find it with:
     git log --oneline -- <path to this file> -->

## Why

windro pressed Update while a finished fix sat committed but unpushed on `main`.
Local was 1 ahead and 1 behind origin, so `git pull --ff-only` failed, and
`hermes_cli/main.py` fell back to `git reset --hard origin/<branch>`. HEAD moved
onto the remote commit and the fix was no longer on any branch.

It was recovered from the reflog, but that is luck rather than design. The
important detail is what the existing safety net does and does not cover:

- the autostash before the pull saves **uncommitted** edits
- an **unpushed commit** is not stashed, and exists on no remote

So `reset --hard` is the one operation in the update path that can destroy work
with no copy anywhere. And the trigger — local carrying commits origin has not
seen — is the *normal* state on a fork whenever a change is in progress.

The reset itself is upstream's and is right for the case it was written for: a
mangled checkout, or upstream force-pushing, where local history is worth less
than the remote's. It just never distinguished that from "this fork has work of
its own".

## What changed

- **`hermes_cli/main.py`** — new `_count_commits_not_on_remote(git_cmd, cwd,
  branch)`, counting `origin/<branch>..HEAD`. It **fails safe**: any probe error
  returns 1, on the reasoning that a refused update is an inconvenience the user
  can resolve, while a wrongly-permitted reset is lost work.

- **`hermes_cli/main.py`** — the `pull --ff-only` failure branch consults it
  before resetting. When local is ahead it prints the offending commits and the
  three ways forward, restores the autostash so the working tree is not left
  stranded, and exits non-zero:

  ```
  ✗ Update stopped: 1 local commit(s) on 'main' are not on origin.
    Fast-forward is not possible, and resetting to the remote would delete them.

    Your commits:
      6e0ed0bb fix(updates): the toast fired, then a session action silently…

    Pick one, then re-run the update:
      git push origin main        # keep them
      git rebase origin/main      # replay onto the remote
      git reset --hard origin/main  # discard them (destructive)
  ```

**Deliberately narrow.** Only the ahead case is refused. A diverged history with
nothing unique locally — the force-push and mangled-checkout cases the reset
exists for — still resets exactly as before. One upstream file, one added
function and one added block, no removals.

## Verified

The helper, against real git repositories (a bare remote plus a clone):

| case | result |
|---|---|
| in sync | 0 |
| 1 unpushed commit | 1 |
| 2 unpushed commits | 2 |
| after pushing | 0 |
| 1 ahead **and** 1 behind (windro's exact shape) | 1 |
| unknown branch | 1 (fails safe) |
| not a git repository | 1 (fails safe) |

```bash
venv/Scripts/python.exe -m pytest tests/fork/test_update_unpushed_guard.py -q
# -> 12 passed
venv/Scripts/python.exe -m pytest tests/fork/ -q
# -> 95 passed
```

Mutation-checked:

| mutation | result |
|---|---|
| guard removed entirely (original behaviour) | 2 failed |
| guard warns but falls through instead of exiting | 1 failed |
| probe error returns 0 instead of failing safe | 3 failed |

No regressions: `test_update_venv_health.py` + `test_backup.py` give 6 failed /
171 passed **both with and without** the change — the same pre-existing failures.

**Not verified:** `hermes update` was never run end to end against this guard.
`tests/hermes_cli/test_cmd_update.py` is excluded on this machine because it
spawns real `hermes gateway run` processes and leaks them (56 strays, once). The
guard is covered by unit tests on the helper plus source-order assertions that it
runs before the reset, exits, and restores the stash — not by an observed refusal.
Worth doing deliberately in a scratch clone.

## Risk / watch for

- **Source-order assertions are brittle by nature.** Three guards locate the
  block by its comment text and the reset by its literal `git` arguments. A
  reformat or a reworded comment fails them spuriously — accepted, because
  removing the guard changes no behaviour that any other test observes.
- **`auto_stash_ref` is dereferenced in the new block.** It is assigned in both
  arms of the if/else above, and upstream already reads it unconditionally a few
  lines later, so it cannot be unbound. Worth re-checking if that stashing
  section is ever restructured.
- **The guard fires on any ahead-ness, including commits windro does not care
  about.** That is the intended bias, but it means a stray local commit now
  blocks updates until it is pushed, rebased, or discarded. The message spells
  out all three.

## Follow-ups

- The desktop Update button reaches this same code path via `hermes-setup.exe` →
  `hermes update`, so it inherits the guard. Untested through that route.
- `git reset --hard HEAD` appears twice more in the update flow (around the
  syntax-guard rollback and the stash-conflict cleanup). Both reset to `HEAD`
  rather than to a remote, so neither can drop a commit — but they are worth a
  look if this class of bug shows up again.

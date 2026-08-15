# The last three CI failures: exact-argv test doubles, and a guard the mocks tripped

**Date:** 2026-08-14
**Type:** Fixed
**Branch:** `fix/ci-green-remaining`

<!-- Commit sha intentionally omitted: this entry ships in the commit it
     describes. Find it with:
     git log --oneline -- fork/changelog/entries/2026-08-14-04-ci-green-exact-argv-tests.md -->

## Why

After `2026-08-14-03` fixed the 8 `test_tui_gateway_server.py` failures, CI on `dev`
was still red with three more, in files that change did not touch:

```
FAILED tests/hermes_cli/test_update_autostash.py::test_cmd_update_falls_back_to_reset_when_ff_only_fails
FAILED tests/hermes_cli/test_update_autostash.py::test_cmd_update_skips_stash_restore_when_reset_fails
FAILED tests/hermes_cli/test_web_server_fs.py::test_fs_default_cwd_falls_back_when_terminal_cwd_is_invalid
```

All three are upstream tests describing behaviour this fork deliberately changed, so
in each case the test was stale rather than the code being wrong. Two distinct causes.

**The fs one — a fork behaviour change with no test update.** `c802ad5bc` ("a session
with no project must default to home, not the process cwd") changed `_fs_default_cwd`
to fall back to `Path.home()`. Upstream's test still asserted `Path.cwd()`. The fork's
reasoning is in the docstring and holds: `Path.cwd()` is wherever the backend happened
to be launched, so with the common `terminal.cwd: .` the file browser opened an
arbitrary directory. `gateway/cwd_placeholder.py` is the canonical resolver and it
answers "home". Code correct, test not updated at the time.

**The update ones — the test double could not tell two questions apart.** The fake
`subprocess.run` answered every `rev-list` with one number, but the update path asks
`rev-list --count` two *opposite* questions:

* `HEAD..origin/<branch>` — how many commits are incoming. Non-zero means proceed.
* `origin/<branch>..HEAD` — how many local commits are not on the remote, via
  `_count_commits_not_on_remote`. Non-zero means **refuse**, because a reset would
  destroy work that exists nowhere else (the guard from
  `2026-08-02-03-update-unpushed-guard.md`).

So a test setting up "3 new commits upstream" was simultaneously claiming "3 unpushed
local commits", the fork's guard fired first, and `cmd_update` exited 1 before ever
reaching the reset it was asserting. The guard was doing exactly its job; the mock
was describing an impossible repo.

## What changed

Tests only. No production code was touched in this change.

- **`tests/hermes_cli/test_web_server_fs.py`** — the fallback test now expects
  `Path.home()` and additionally asserts the result is **not** the process cwd, with a
  `FORK:` docstring naming `c802ad5bc`. Pinning the negative is the point: it is what
  fails if the drift back to `Path.cwd()` ever returns.

- **`tests/hermes_cli/test_update_autostash.py`** — three related fixes:

  1. `_make_update_side_effect` gains an `ahead_count` parameter (default `"0"`) and
     now answers by **range direction**, so the incoming-count and local-only-count
     questions get different numbers. Default "0" — nothing unpushed — is the state
     these tests actually describe.
  2. A new module-level `git_args()` helper strips the leading `git` and any global
     `-c key=value` pair from a recorded argv. Eleven `if cmd == ["git", ...]`
     dispatch branches and two assertions now compare through it.
  3. The reset assertion compares the trailing arguments rather than the whole argv.

  This second one was a **latent Windows-only break, not cosmetic**. `cmd_update`
  builds `git_cmd` as `["git", "-c", "windows.appendAtomically=false"]` on win32 and
  plain `["git"]` elsewhere (`hermes_cli/main.py:11904`). An exact-argv comparison can
  therefore only match on POSIX; on Windows every branch fell through to the
  catch-all, which returns `stdout=""`, and the update then died on
  `int("")` at `main.py:12041`. That is why this file showed **6** local failures
  against CI's 2 — CI runs Linux. These tests were green in CI while testing nothing
  on the platform Hermes ships an installer for.

## Verified

Both target files, all green:

```bash
.venv/Scripts/python.exe -m pytest tests/hermes_cli/test_update_autostash.py \
    tests/hermes_cli/test_web_server_fs.py -q -p no:randomly
# -> 59 passed, 1 skipped   (was: 7 failed, 52 passed, 1 skipped)
```

Fork suite unaffected:

```bash
.venv/Scripts/python.exe -m pytest tests/fork/ -q -p no:randomly   # -> 191 passed
.venv/Scripts/python.exe -m ruff check <both files>                 # -> All checks passed!
```

Attribution checked rather than assumed. A stashed baseline on clean `dev` reported
**6 failures** in `test_update_autostash.py`; CI's run `31819784089` listed only the
**2** named above. The extra 4 were pre-existing Windows-only failures, not caused by
this branch — confirmed by running the baseline with all changes stashed before
touching them.

**A wrong turn worth recording:** my first direction-matcher used
`if "..HEAD" in joined`. `HEAD..origin/main` *contains* the substring `..HEAD`, so it
answered the local-only count to the incoming-count question and broke the four
extras/dependency tests that had been passing. Caught it by running the file rather
than the two target tests. The matcher now checks the range **token**
(`part.startswith("origin/") and part.endswith("..HEAD")`). Substring tests on git
ranges are a trap: the two directions share characters.

**Not verified:** the POSIX half. `git_args()` is a no-op when `git_cmd == ["git"]`,
so behaviour on Linux should be identical to the old exact comparisons, but I could
not run this file on Linux from here. CI on this branch is that check.

## Risk / watch for

- **`git_args()` drops every `-c k=v` pair, not just the Windows one.** If a test ever
  needs to assert that a specific `-c` flag was passed, it must compare the raw argv
  instead — `git_args()` would hide exactly that. No current test does.
- **The helper assumes `-c` always takes a separate following token.** True for git's
  global `-c key=value` form. `git -ckey=value` (no space) would break the skip; git
  accepts it, and the codebase does not use it.
- **`ahead_count` defaults to "0", which is the permissive answer.** A future test
  that means to exercise the unpushed-commits guard must pass it explicitly. Worth a
  dedicated test — see Follow-ups.
- **Both edited files are upstream's**, so they are merge-conflict candidates. Each
  edit carries a `FORK:` comment naming the reason and the commit, so the correct
  resolution stays discoverable.

## Follow-ups

- **No test covers the unpushed-commits guard through `cmd_update`.** The guard from
  `2026-08-02-03` is only exercised here by accident, and this change removes that
  accident. A test passing `ahead_count="2"` and asserting the refusal message plus
  exit code 1 would pin real, load-bearing behaviour — the thing that stopped windro
  losing a finished fix.
- **Other exact-argv tests probably have the same latent Windows break.** This file
  was found via CI; a sweep for `cmd == ["git"` across `tests/` would show whether
  other suites are silently no-op on Windows.
- **`tests/hermes_cli/test_projects_db.py`** still has 7 Windows path failures
  (`/tmp/hermes` vs `C:\tmp\hermes`), pre-existing on `main`, untouched here.
- **`js-autofix` still needs the repo setting** enabled: Settings → Actions → General
  → "Allow GitHub Actions to create and approve pull requests".

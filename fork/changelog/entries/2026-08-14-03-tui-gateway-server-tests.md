# A swallowed TypeError meant session rows were never written, and CI stayed red

**Date:** 2026-08-14
**Type:** Fixed
**Branch:** `fix/tui-gateway-server-tests`

<!-- Commit sha intentionally omitted: this entry ships in the commit it
     describes. Find it with:
     git log --oneline -- fork/changelog/entries/2026-08-14-03-tui-gateway-server-tests.md -->

## Why

CI had failed on every push to `main` since 2026-08-09 — 12 consecutive runs. Once a
red signal is permanent it stops being a signal, which is what made the `dev` branch
worth adding first (`2026-08-14-02-dev-branch-workflow.md`). This is the first change
through that flow, and it clears the gate.

8 tests in `tests/test_tui_gateway_server.py` were failing, from two unrelated fork
commits. One of them was hiding a real bug, not just a test mismatch.

**The real bug (6 tests).** `26cd4d9a7` added `project_id=` to the
`db.create_session(...)` call in `_ensure_session_db_row`. Three lines below sat
`except Exception: logger.debug(...)`. Any DB object whose signature predates that
kwarg — a test double, or an older `SessionDB` on a half-updated install — raises
`TypeError`, which that handler swallowed at **debug** level. The visible effect is
that the INSERT never happens: no session row, and the only trace is a debug line
nobody reads. A session that never persists is a session the user loses on resume.
Production was unaffected because the real `SessionDB.create_session` takes
`**kwargs`, so this was reachable but not yet reached — the tests were the messenger.

**The test mismatch (2 tests).** `5940ca566` added a broader truncation guard
returning error `4029`, placed *before* upstream's existing empty-truncation guard
(`4028`). Two upstream tests assert `4028` for a submit that carries no confirmation
at all, and that case is now caught by the earlier, broader guard.

Researched which side was wrong before touching either. Upstream's current
`tui_gateway/server.py` has **no truncation guard at all** — fetched
`raw.githubusercontent.com/NousResearch/hermes-agent/main/tui_gateway/server.py`
(599,194 bytes) and grepped: zero hits for `confirm_truncate`, `confirm_empty_truncate`,
`4028` or `4029`. The fork's base commit `9e118284c` does have the `4028` guard
(`git show 9e118284c:tui_gateway/server.py` → line 10901). So upstream removed or
relocated that whole area after the fork's base, and both codes are now fork-only
behaviour. Neither is "wrong" — they refuse different things, and both are reachable.
The tests were describing the old single-guard world.

## What changed

- **`tui_gateway/server.py`** — `_ensure_session_db_row`, one reworked block. The
  call arguments move into a `kwargs` dict and `project_id` is added **only when
  set**. `SessionDB.create_session` already defaults it to `None`, so passing `None`
  and omitting it produce the identical row; omitting it means a narrower callee is
  not handed a kwarg it cannot take. A `TypeError` from the call is now caught
  separately and **retried once without** `project_id`, so the row still lands
  instead of being dropped. The blanket `except Exception` was raised from `debug` to
  `warning`, because failing to persist a session is user-visible data loss and
  hiding it at debug is what let this sit unnoticed.

  The function keeps its never-raise contract deliberately: it has four call sites
  and **three are not wrapped in a try** (verified by walking the enclosing blocks —
  only `:3423` sits inside one), and `prompt.submit` is among the unprotected ones.
  An earlier draft of this fix let a non-`project_id` `TypeError` propagate; that
  would have turned a silent row-skip into a crashed `prompt.submit`. Both handlers
  now log and return.

- **`tests/test_tui_gateway_server.py`** (upstream file) — two tests updated to match
  the fork's two-guard behaviour, with `FORK:` comments naming why.
  `test_prompt_submit_refuses_empty_truncation_without_confirm` now expects `4029`
  for the unconfirmed case, **and additionally asserts `4028` still fires** when
  `confirm_truncate=true` is sent but the cut would empty the transcript — so the
  behaviour the test was originally written to pin is still covered rather than
  quietly dropped. `test_prompt_submit_can_truncate_before_user_ordinal` now sends
  `confirm_truncate: True` alongside the ordinal, which is what this repo's own
  client sends in real traffic (see `use-prompt-actions/rewind.ts`).

No production behaviour changed for the truncation guards; only the tests moved.

## Verified

Target file, all green:

```bash
.venv/Scripts/python.exe -m pytest tests/test_tui_gateway_server.py -q -p no:randomly
# -> 472 passed   (was: 8 failed, 464 passed)
```

Neighbours — checked against a stashed baseline rather than assumed, because
`tests/tui_gateway/` is not clean on this machine:

```bash
git stash -q && pytest tests/tui_gateway/ -q -p no:randomly | grep -E '^(FAILED|ERROR)' | sort > base.txt
git stash pop -q &&  pytest tests/tui_gateway/ -q -p no:randomly | grep -E '^(FAILED|ERROR)' | sort > mine.txt
diff base.txt mine.txt
# -> no differences; 15 lines each (6 failed + 9 errors) before and after
```

Those 15 pre-existing problems are Windows/environment artifacts, not fork bugs:

- `test_session_cwd_follow.py` — 9 errors in the full run, **9 passed when run
  alone**. Cross-file state pollution; CI's subprocess-per-test-file isolation
  (`run_tests_parallel.py`) is why this does not fail there.
- `test_projects_rpc.py` (22 passed alone), `test_subagent_child_mirror.py` (10
  passed alone) — same pollution pattern.
- `test_compute_host.py` — fails alone: asserts a child's reported `host_pid` equals
  `proc.pid` (17484 vs 26768). A Windows process-spawn indirection, unrelated.
- `test_compute_host_phase1.py` — fails alone: expects 32 written log lines, gets 30.
  Also unrelated to this change.

Lint clean:

```bash
.venv/Scripts/python.exe -m ruff check tui_gateway/server.py tests/test_tui_gateway_server.py
# -> All checks passed!
```

**Not verified:** the `TypeError` retry path was exercised only by the upstream test
doubles that triggered the original bug — I did not construct an actual older
`SessionDB` to prove the real-world half of that scenario. Also did not run the full
Python suite locally (it exceeds the practical timeout here); CI on this branch is
the check for that, and it is the point of the new flow.

## Risk / watch for

- **The retry is `project_id`-specific by design.** If a future fork field is added
  to this call the same way, it will hit the same swallowed-`TypeError` shape and the
  retry will not know to drop it. If a second such field appears, generalise the
  retry to strip fork-only keys rather than adding a second special case.
- **`except Exception` at `warning` will now be noisy if a real persist failure
  exists.** That is intended. If the logs fill with "failed to persist session row",
  that is a bug to fix, not a level to lower.
- **The two edited tests are upstream's**, so they are merge-conflict candidates.
  Both edits carry `FORK:` comments explaining the two-guard ordering; on conflict,
  the resolution is to keep the fork's expectations as long as
  `truncation_confirmed` still runs before the empty-truncation check.
- **`4028` is now only reachable with `confirm_truncate` set.** If someone reorders
  the two guards, that path becomes dead and the new assertion in the first test is
  what will catch it.

## Follow-ups

- **`tests/hermes_cli/test_projects_db.py`** still has 7 Windows path failures
  (`/tmp/hermes` vs `C:\tmp\hermes`), confirmed pre-existing on `main`. Untouched here.
- **`js-autofix` fails on a repository setting**, not code: Settings → Actions →
  General → "Allow GitHub Actions to create and approve pull requests". It will keep
  failing on every JS/TS push to `main` until that is enabled.
- **The 9 `test_session_cwd_follow.py` errors deserve a look** — they pass in
  isolation, so some earlier test file in `tests/tui_gateway/` leaks module-level
  state. Harmless in CI, but it makes local full-directory runs hard to read.

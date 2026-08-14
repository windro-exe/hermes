# `main` becomes production; work lands on `dev` and reaches `main` by PR only

**Date:** 2026-08-14
**Type:** Changed
**Branch:** `chore/dev-branch-workflow`

<!-- Commit sha intentionally omitted: this entry ships in the commit it
     describes. Find it with:
     git log --oneline -- fork/changelog/entries/2026-08-14-02-dev-branch-workflow.md -->

## Why

Every fork change so far has been committed on a topic branch and then merged
straight to `main`, and `main` is not an ordinary branch here — it is the release
channel. The installers clone it, `hermes update` pulls it, and the desktop's Update
button tracks it. A broken commit on `main` is a broken commit on windro's running
install, with no gate in between.

The cost of that is already visible and was the trigger for this change. CI on this
repo has failed on **every one of the last 12 pushes to `main`**, going back to
2026-08-09:

```
gh run list --workflow=ci.yml --branch main --limit 12
# -> 12 rows, all "failure"
```

Two fork commits are responsible (details in Follow-ups). Because nothing gated
`main`, both landed, and the red signal became the normal state — at which point it
stopped carrying information. A dev branch exists so the next one of those is caught
on a branch nobody installs from.

windro asked for this as "a dev build, so instead of pushing directly to main and
breaking things without testing."

## What changed

No production code. Two files, both documentation-or-CI plumbing.

- **`.github/workflows/ci.yml`** (upstream file) — one added entry in the `push:`
  branch list, `[main]` becomes `[main, dev]`, plus a `FORK:` comment above `on:`
  explaining why. This is the load-bearing part of the change: CI here triggers on
  `pull_request:` (any branch) and `push: branches: [main]`. Without `dev` in that
  list, a direct push to `dev` would run **no CI at all**, making the integration
  branch the one place nothing is checked — the exact inverse of the intent. A PR
  from `dev` to `main` was already covered by the `pull_request:` trigger.

  Touching an upstream file was necessary: the trigger list has to live in the
  workflow that owns the trigger, and a fork-owned workflow duplicating the whole
  orchestrator would be far worse to maintain. The change is one list element and a
  comment block, which is about as small as a divergence gets.

- **`AGENTS.md`** (upstream file, inside the existing `FORK-RULES` block) — rewrote
  the "Never commit to `main`" paragraph to state the production/dev split and name
  the five places `main` is hardcoded, so a future agent does not try to "fix" the
  workflow by renaming the default branch. Also corrected three stale claims in the
  same block while they were in front of me:

  - It said this checkout "has no venv and no `AppData\Local\hermes`, so nothing here
    is running." Both halves are false now. The dev checkout has `.venv/` (dot
    prefix), and `AppData\Local\hermes\hermes-agent` is a *separate* clone with its
    own `venv/` — that second one is what `which hermes` resolves to.
  - "Current work" named `perf/ui-latency` as finished but did not mention that
    `fix/common-bugs` and `feat/nested-projects` are also merged and deleted.
  - Added the Windows-path test note (see Verified) so the next agent does not chase
    7 pre-existing failures.

**Deliberately NOT changed:** which branch GitHub considers default. `main` stays
default and stays the update target. `FORK_DEFAULT_BRANCH` (`hermes_fork.py:42`),
`install.ps1`'s `-Branch` default (`:18`), `install.sh`'s `BRANCH` (`:85`),
`DEFAULT_UPDATE_BRANCH` (`apps/desktop/electron/main.ts:585`) and
`_resolve_update_branch` (`hermes_cli/main.py:10629`) all hardcode `"main"`, and
`resolveHealedBranch` treats `main` as the fallback when a tracked branch disappears.
Pointing production at a differently-named branch would mean editing all five and
weakening that fallback, for no gain — "production" is a policy about who may push,
not a name.

## Verified

Branch protection on `main`, applied via the API and read back:

```bash
gh api repos/windro-exe/hermes/branches/main/protection --jq \
  '{enforce_admins: .enforce_admins.enabled, prs: .required_pull_request_reviews != null, force: .allow_force_pushes.enabled, checks: .required_status_checks.contexts}'
# -> see the run log in the session; asserted enforce_admins true, force pushes false
```

A direct push to `main` is refused rather than silently accepted:

```bash
git push origin HEAD:main    # -> rejected by remote (protected branch)
```

The YAML still parses, and the trigger list is what I intended:

```bash
python -c "import yaml;d=yaml.safe_load(open('.github/workflows/ci.yml',encoding='utf-8'));print(d['on' if 'on' in d else True]['push']['branches'])"
# -> ['main', 'dev']
```

Fork test suite unaffected by the doc/CI edits:

```bash
.venv/Scripts/python.exe -m pytest tests/fork/ -q     # -> 191 passed
```

**Not verified:** whether a `dev → main` PR actually blocks on the required check
until the 8 failing tests are fixed. That is only observable once such a PR exists,
which is the next change. Also did not verify the `js-autofix` failure is gone — it
fails on a repository *setting* ("GitHub Actions is not permitted to create or
approve pull requests"), not on anything in the tree, and I did not change repo
settings beyond branch protection.

## Risk / watch for

- **`ci.yml` is a hot upstream file.** The `on:` block is near the top, which the
  changelog README already flags as the highest-conflict region. If a merge drops
  `dev` from the push list, CI on `dev` silently stops running — no error, just
  nothing. Check `on.push.branches` after any upstream merge.
- **Protection is repo state, not code.** Nothing in the tree enforces it. If the
  rule is ever deleted in Settings, the workflow degrades to the old behaviour with
  no visible signal. The `AGENTS.md` paragraph is the only durable record of intent.
- **`enforce_admins` applies to windro too.** That is deliberate — an agent acting
  with his credentials is indistinguishable from him at the git layer, so an
  admin-exempt rule would not actually gate anything. The escape hatch is to turn
  the rule off in Settings, not to force-push through it.
- **A red required check blocks `dev → main` merges.** Until the 8 tests are fixed,
  the gate will refuse the very PR that fixes them. Fix them in that PR (that is the
  plan) or the first merge will need the rule temporarily relaxed.

## Follow-ups

- **Fix the 8 failing tests in `tests/test_tui_gateway_server.py`** — the first PR
  through the new flow. Two independent causes:
  1. `5940ca566` changed the unconfirmed-truncation JSON-RPC error code to `4029`;
     the upstream test asserts `4028` at `tests/test_tui_gateway_server.py:3447`
     and `:3463`. Decide whether the new code is correct and update the test, or
     reuse `4028`.
  2. `26cd4d9a7` added `project_id=` to the `db.create_session(...)` call at
     `tui_gateway/server.py:2497`. Upstream's `_FakeDB.create_session` has a fixed
     signature without it, so the call raises `TypeError`, which the
     `except Exception: logger.debug(...)` immediately below swallows. The row is
     never written and 6 `test_ensure_session_db_row_*` tests see `created == []`.
     Real production is fine — `SessionDB.create_session` takes `**kwargs`.
- **That `except Exception` is worth narrowing.** It converts "we failed to persist
  the session row" into a debug line, which is how a signature mismatch went
  unnoticed. A session that never persists is a session the user loses on resume.
- **7 failures in `tests/hermes_cli/test_projects_db.py` are Windows-only and
  pre-existing** — upstream asserts `/tmp/hermes` where Windows normalizes to
  `C:\tmp\hermes`. Confirmed present on `main` before this change. Not this fork's
  bug; left alone.
- **`js-autofix` needs a repo setting flipped**, not a code change: Settings →
  Actions → General → "Allow GitHub Actions to create and approve pull requests".
  Until then that workflow fails on every push to `main` that touches JS/TS.

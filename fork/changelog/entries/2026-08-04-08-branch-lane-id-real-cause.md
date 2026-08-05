# One session under two lanes: the real cause, after a wrong fix

**Date:** 2026-08-04
**Type:** Fixed
**Branch:** `main`

**Supersedes** `entries/2026-08-04-01-branch-lane-id.md`, whose fix was based on a
misreading and has been reverted.

## Why

windro's `Asthra HR Admin` project rendered **one** session under **two** sidebar lanes,
labelled `main` and `Asthra HR admin`. The database has exactly one session
(`20260804_002810_524651`), so the sidebar was drawing it twice.

## The first fix was wrong

The earlier entry claimed the frontend "baked the display fallback into the lane id" and
changed it to use the raw branch. That reasoning came from reading
`_branch_lane_id` (`tui_gateway/project_tree.py:59`) in isolation:

```python
return f"{repo_root}::branch::{(branch or '').strip()}"
```

It does not normalise — but **every caller does**, at `:229`, `:245` and `:253`:

```python
b = (branch or "").strip() or DEFAULT_BRANCH_LABEL
```

with a comment above saying that exists so "a repo never shows two 'main' lanes". So the
frontend's original `main` normalisation **already matched** the backend, and the change
*introduced* the mismatch it claimed to remove.

The guard did not catch it because it compared against a Python-mirroring helper written
from the same misreading. The test encoded the mistake.

## The actual cause

`Asthra HR Admin` is **not a git repo** — `git_branch` and `git_repo_root` are both null.
Both `_place` call sites (`:337`, `:634`) pass `session.git_repo_root` as
`persisted_root`, so with no git probe and no persisted root it falls to
`_place_by_heuristic`, whose final line (`:215`) is:

```python
return _placement(path, path, base, path, True, False)
```

Lane id = the bare **path**, label = the **folder name**. Meanwhile the frontend computes
`<path>::branch::main` labelled `main`. Neither id nor label matches, and
`overlayRepoLanes`'s path-match rung was gated on `!placed.isMain` — so a main lane could
never match by path, and a second lane was created.

Confirmed by running `_place` against the real session: it returns lane label `main` when
`persisted_root` is set and `Asthra HR admin` when it is empty. Exactly the two labels on
screen.

## What changed

**`apps/desktop/src/app/chat/sidebar/projects/workspace-groups.ts`**

- Reverted to normalising the branch before building the id, matching the backend.
- `overlayRepoLanes` now also matches by path **when the session has no recorded
  branch** — the only case where the backend may key the lane differently.

The narrowing matters: the frontend marks every branch lane `isMain: true`, so matching
all main lanes by path collapsed `feature/x` into `main`. My own test caught that.

## Verified

Removing the path rung fails with
`expected ['main', 'Asthra HR admin'] to equal ['Asthra HR admin']` — the reported bug,
reproduced. 6 guards, upstream lane tests 63 passing, 178 total.

Getting a guard that could actually fail took three attempts. `overlayRepoLanes` prunes
empty lanes, so a fixture seeding the backend's lane *without* a session let the
duplicate collapse to one and passed with the bug present. A fixture must put the session
in the backend's lane, as the backend does.

## Risk / watch for

- Only branchless sessions take the new rung. Real repos already agreed on
  `::branch::<branch>` and are untouched.
- windro's decision: lanes should key off **real git branches only**. With `git init` now
  running on project create, the heuristic path becomes the legacy case. Worth removing
  once no project relies on it.

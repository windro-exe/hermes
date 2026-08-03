# One session rendered under two lanes: the label fallback leaked into the lane id

**Date:** 2026-08-04
**Type:** Fixed
**Branch:** `main`

<!-- Commit sha omitted: ships in the commit it describes. Find it with:
     git log --oneline -- <path to this file> -->

## Why

windro opened a project session and saw the same session twice in the sidebar —
once under a lane labelled `main`, once under one labelled `Asthra HR admin`.

The database had **one** row. Verified directly:

```
id=20260804_00281  msgs=17  source=desktop  branch=None
   cwd='C:\Users\wnxdd\Documents\Asthra HR admin'
   repo_root=None
```

So this was never duplicated data — the sidebar built two lanes for one session,
because the frontend and backend disagreed on that lane's id.

Backend, `tui_gateway/project_tree.py:59`, whose docstring states the shape
outright — *"`<repoRoot>::branch::<branch>` (or `::branch::`)"*:

```python
return f"{repo_root}::branch::{(branch or '').strip()}"
```

A session with no branch gets the **empty** suffix. The frontend, in
`liveLaneForRepo`, substituted the display fallback *before* building the id:

```js
const branch = (session.git_branch || '').trim() || DEFAULT_BRANCH_LABEL
id: branchLaneId(repoRoot, branch)     // -> "<repo>::branch::main"
```

Two different ids, so the reconciler treated them as separate lanes and rendered
the session under both. `branchLaneId`'s own comment already required agreement —
*"The one definition of a main-checkout lane id (must match the backend tree)"* —
so nothing in the codebase disagreed with the rule; it simply wasn't enforced.

**Why it stayed hidden:** it only fires outside a git repo. In a real checkout
`git_branch` is set, both sides agree, and lanes are correct.
`Documents\Asthra HR admin` isn't a repo, so `git_branch` and `git_repo_root` are
both null and every session there took the fallback path.

## What changed

`apps/desktop/src/app/chat/sidebar/projects/workspace-groups.ts` — the id is built
from the raw branch, the label keeps the fallback:

```js
const rawBranch = (session.git_branch || '').trim()

return {
  id: branchLaneId(repoRoot, rawBranch),      // matches the backend
  isMain: true,
  label: rawBranch || DEFAULT_BRANCH_LABEL,   // still reads "main"
  path: repoRoot,
  sessions: []
}
```

Identity and presentation separated. Nothing else moved.

## Verified

```bash
cd apps/desktop
npx tsc -p . --noEmit                                    # -> clean
npx vitest run --project ui src/__fork__/branch-lane-id.test.ts  # -> 13 passed
npx vitest run --project ui src/app/chat/sidebar/         # -> 92 passed
```

New guards in `src/__fork__/branch-lane-id.test.ts` (13 tests) restate the
backend's formula **independently** rather than calling `branchLaneId` — a test
that reuses the code under test cannot detect the two sides drifting apart. They
cover blank branch values (`null`, `undefined`, `''`, whitespace), real branch
names, that `main` never appears in an id merely because the label says `main`,
and that a branchless session yields exactly one lane after reconciliation.

Mutation-checked: reintroducing the original expression (`|| DEFAULT_BRANCH_LABEL`
before the id) fails *does not split into two lanes when git_branch is null*.

**Not verified:** not seen in the packaged app. The fix is in the renderer, so it
needs a rebuild before windro sees the duplicate disappear.

## Risk / watch for

- **The label fallback is now the only place `DEFAULT_BRANCH_LABEL` is used.** If
  someone reintroduces it into an id — for a worktree or kanban lane, say — the
  same class of split returns. The guard only covers the branch lane.
- **`isMain: true` is unchanged for branchless lanes**, so they still sort in the
  trunk tier via `laneRank`. That looked deliberate and is out of scope here, but
  it means a non-repo folder's lane pins high in the list.
- Two sides still encode the same string format in two languages. The guard
  detects drift but cannot prevent it; a shared contract test against the live
  backend payload would be stronger.

## Follow-ups

- This is step 1 of the git/project work windro asked for (`git init` on project
  create, GitHub connect/select/clone, then branch-based sessions via worktrees).
  It goes first precisely because new branch lanes would otherwise inherit this
  split.
- Sessions in a non-repo folder produce a lane labelled `main` that has no branch
  behind it. Once `git init` on project create lands, that case largely disappears
  for new projects — but existing folderless projects will still show it.

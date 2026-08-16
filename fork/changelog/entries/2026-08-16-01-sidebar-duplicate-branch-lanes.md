# One repo showed a lane per branch its sessions were ever created on

**Date:** 2026-08-16
**Type:** Fixed
**Branch:** `feat/agent-session-fork`

<!-- Commit sha omitted: this entry ships in the commit it documents. -->

## Symptom

windro's sidebar listed `feat/agent-session-fork` **twice** under the Hermes
project — once with a house icon, once with a git-branch icon — alongside lanes
for branches that only existed because a session had been created while they were
checked out.

He reported it as "git branch based session ... this one is bugged i don't know
why".

## What was actually wrong

Not the branch-based grouping, and not the session rows. The **home-lane fold**
was silently disabled.

`mergeRepoWorktreeGroups` (`workspace-groups.ts:251`) collapses every
main-checkout lane into ONE lane labelled by the checkout's live branch. A repo
directory is only ever on one branch, so this is what stops stale per-session
branch values from multiplying into lanes. It runs only when the git probe found
a main worktree:

```ts
const mainWorktree = (discoveredWorktrees ?? []).find(w => w.isMain)
const homeBranch = mainWorktree && !mainWorktree.detached ? ... : ''
if (homeBranch) { /* fold */ } else { reconciled.push(...mainGroups) }
```

`discoveredWorktrees` arrived as `undefined`, so the `else` ran and every recorded
lane survived.

The reason is a **path separator mismatch**, confirmed on his machine rather than
reasoned about:

```
$ git worktree list --porcelain
worktree C:/wnx-projects/personal/hermes      <- POSIX separators, on Windows
```

`electron/git-worktree-ops.ts:42` stores that verbatim (`line.slice(9).trim()`),
so `useRepoWorktreeMap` keys its map with forward slashes. But `repo.path` and
every `git_repo_root` in `state.db` carry native backslashes
(`C:\wnx-projects\personal\hermes`). `entered-content.tsx:64` looked the map up
with a raw string index:

```ts
discoveredWorktrees={repo.path ? repoWorktrees?.[repo.path] : undefined}
```

`map['C:\\...']` against a `'C:/...'` key never matches. Every other path
comparison in that module already goes through `pathKey`, which the file's own
docstring calls "separator/case/trailing-slash agnostic" — this one call site
didn't.

His actual data, for the record: two sessions in the repo, one stamped
`git_branch='dev'`, one `NULL` (→ labelled `main`), while HEAD was on
`feat/agent-session-fork`. Three lanes, one repo.

## Fix

- Exported `pathKey` from `workspace-groups.ts`.
- New `projects/worktree-lookup.ts` with `lookupRepoWorktrees(map, repoPath)`:
  exact-key hit first (no scan when the map already holds native paths), then a
  `pathKey`-normalised comparison.
- `entered-content.tsx` calls it instead of indexing raw.

Extracted to its own module rather than left inline in the component because
inline it could only be exercised through a full render, and the bug is invisible
at that level — AGENTS.md's "extract the logic" rule applied.

## Not changed, deliberately

**Sessions still record the branch checked out at creation time and never
re-stamp it.** That looks like the bug and isn't: a session's history belongs to
the branch it happened on. The fold is the mechanism that makes stale values
harmless, so the fold was the thing to repair. Re-stamping rows on every checkout
would rewrite history to match the present.

**No change to `_branch_lane_id` / `branchLaneId` id spelling.** A previous fork
commit changed that on a misreading and introduced the mismatch it claimed to fix;
`workspace-groups.ts:477-485` and `src/__fork__/branch-lane-id.test.ts` both carry
warnings about it. Left alone.

## Verified

- `src/__fork__/worktree-probe-path-key.test.ts` — 11 tests. Asserts observable
  lane counts and lookup results, never id spelling (the rule
  `branch-lane-id.test.ts` exists to enforce).
- **Confirmed the test catches the bug:** reverted `lookupRepoWorktrees` to
  `return map[repoPath]` → 2 failed / 9 passed; restored → 11 passed. A guard that
  passes either way is worthless.
- Covered: POSIX-keyed map found via native query; exact-key fast path; distinct
  repos stay distinct; trailing separator + case folding on Windows;
  undefined/empty inputs; no-probe (remote backend) leaves lanes untouched;
  detached HEAD does not fold into a named lane.
- `src/__fork__` 198 passed; `src/app/chat/sidebar` 92 passed.
- `tsc -p .` clean; `eslint src/ electron/` 0 errors; renderer build ok.

**Mistake made along the way:** the first version of the test omitted `isMain`
from its `HermesGitWorktree` fixtures. `isMain` is what `mergeRepoWorktreeGroups`
looks for, so the probe could never match and two assertions failed for a reason
that had nothing to do with the bug. Caught by dumping the function's real output
instead of adjusting the assertions to match.

**Not verified:** the sidebar itself. Per project rule, windro relaunches the dev
app and reports what he sees.

import type { HermesGitWorktree } from '@/global'

import { pathKey } from './workspace-groups'

/**
 * Look a repo's `git worktree list` result up out of the probe map.
 *
 * FORK. Extracted from `entered-content.tsx` so the lookup is testable on its
 * own — inline in the component it could only be exercised through a full render,
 * and the bug it fixes is invisible at that level.
 *
 * The bug: `useRepoWorktreeMap` keys its map with the path handed to
 * `git worktree list`, but git's `--porcelain` output reports POSIX separators
 * even on Windows — `worktree C:/wnx-projects/personal/hermes` — and
 * electron/git-worktree-ops.ts stores that verbatim. Meanwhile `repo.path` and
 * every session row carry native backslashes. So the original
 * `repoWorktrees?.[repo.path]` was comparing `C:\...` against a `C:/...` key,
 * always missed, and handed `mergeRepoWorktreeGroups` an undefined probe.
 *
 * Consequence: no main worktree found → no `homeBranch` → the fold that
 * collapses every main-checkout lane into ONE home lane never ran. A repo then
 * showed a separate lane for each branch its sessions were created on, plus the
 * live-branch home lane. Sessions store the branch that was checked out at
 * creation time and never re-stamp it, so those stale values are normal — the
 * fold is what keeps them from multiplying into duplicate lanes.
 *
 * Tries the exact key first: when the map already holds native paths (any
 * non-Windows host, or a probe fed a native path) this costs one hit and no scan.
 */
export function lookupRepoWorktrees(
  map: Record<string, HermesGitWorktree[]> | undefined,
  repoPath: null | string | undefined
): HermesGitWorktree[] | undefined {
  if (!map || !repoPath) {
    return undefined
  }

  const exact = map[repoPath]

  if (exact) {
    return exact
  }

  const wanted = pathKey(repoPath)

  if (!wanted) {
    return undefined
  }

  for (const [key, worktrees] of Object.entries(map)) {
    if (pathKey(key) === wanted) {
      return worktrees
    }
  }

  return undefined
}

/**
 * Guard: one repo must show ONE home lane, not one lane per historical branch.
 *
 * The bug windro reported: the sidebar listed `feat/agent-session-fork` twice —
 * once with a house icon, once with a git-branch icon — plus lanes for branches
 * his sessions merely happened to be created on.
 *
 * Mechanism, confirmed against his real data rather than inferred:
 *
 *  - `git worktree list --porcelain` reports POSIX separators even on Windows.
 *    His checkout printed `worktree C:/wnx-projects/personal/hermes`, while every
 *    session row and `repo.path` carry `C:\wnx-projects\personal\hermes`.
 *    electron/git-worktree-ops.ts takes that path verbatim (`line.slice(9)`), so
 *    the renderer's probe map is keyed with forward slashes.
 *  - `entered-content.tsx` indexed that map with a RAW string: `map[repo.path]`.
 *    Backslashes never match forward slashes, so the lookup returned undefined.
 *  - With no main worktree, `mergeRepoWorktreeGroups` computes no `homeBranch`
 *    and takes its `else` branch, which keeps every main lane as-is instead of
 *    folding them into a single home lane. One lane per stored `git_branch`,
 *    plus the live-branch lane.
 *
 * A session records the branch checked out WHEN IT WAS CREATED and never
 * re-stamps it, so the stored values legitimately diverge from the live branch
 * (his rows held 'dev' and null while HEAD was on feat/agent-session-fork). The
 * fold is what makes that harmless. It has to actually run.
 *
 * Following the rule established in branch-lane-id.test.ts: assert on OBSERVABLE
 * lane counts and labels, never on id spelling — a mirror helper written from the
 * same misreading would just validate the mistake.
 */

import { describe, expect, it } from 'vitest'

import { mergeRepoWorktreeGroups, pathKey } from '@/app/chat/sidebar/projects/workspace-groups'
import type { SidebarSessionGroup, SidebarWorkspaceTree } from '@/app/chat/sidebar/projects/workspace-groups'
import { lookupRepoWorktrees } from '@/app/chat/sidebar/projects/worktree-lookup'
import type { HermesGitWorktree } from '@/global'
import type { SessionInfo } from '@/types/hermes'

/** Native Windows spelling — what repo.path and every session row hold. */
const REPO_NATIVE = 'C:\\wnx-projects\\personal\\hermes'
/** What `git worktree list --porcelain` actually prints on the same machine. */
const REPO_FROM_GIT = 'C:/wnx-projects/personal/hermes'

const LIVE_BRANCH = 'feat/agent-session-fork'

function session(id: string, branch: null | string): SessionInfo {
  return {
    cwd: REPO_NATIVE,
    git_branch: branch,
    git_repo_root: REPO_NATIVE,
    id,
    last_active: 1,
    source: 'desktop',
    started_at: 1,
    title: id
  } as SessionInfo
}

function mainLane(branch: string, sessions: SessionInfo[]): SidebarSessionGroup {
  return {
    id: `${REPO_NATIVE}::branch::${branch}`,
    isMain: true,
    label: branch,
    path: REPO_NATIVE,
    sessions
  }
}

/** windro's actual state: two sessions created on two different branches. */
function repo(): SidebarWorkspaceTree {
  return {
    groups: [mainLane('dev', [session('a', 'dev')]), mainLane('main', [session('b', null)])],
    id: REPO_NATIVE,
    path: REPO_NATIVE
  } as SidebarWorkspaceTree
}

describe('worktree probe path keying', () => {
  it('folds every main lane into one home lane when the probe path matches', () => {
    const groups = mergeRepoWorktreeGroups(repo(), [
      { branch: LIVE_BRANCH, detached: false, isMain: true, locked: false, path: REPO_NATIVE }
    ])

    const mains = groups.filter(g => g.isMain)

    expect(mains).toHaveLength(1)
    expect(mains[0].label).toBe(LIVE_BRANCH)
    // Both sessions survive the fold — collapsing lanes must not drop rows.
    expect(mains[0].sessions.map(s => s.id).sort()).toEqual(['a', 'b'])
  })

  it('still folds when git reports the path with POSIX separators (the bug)', () => {
    // This is the real-world input: the probe path came from git, the tree from
    // the session rows. Before the fix the lookup in entered-content.tsx missed
    // and this produced three lanes: dev, main, and the home lane.
    const groups = mergeRepoWorktreeGroups(repo(), [
      { branch: LIVE_BRANCH, detached: false, isMain: true, locked: false, path: REPO_FROM_GIT }
    ])

    const mains = groups.filter(g => g.isMain)

    expect(mains).toHaveLength(1)
    expect(mains[0].label).toBe(LIVE_BRANCH)
  })

  it('treats the two spellings as the same repo', () => {
    // The property the fix relies on. If this ever fails, the lookup helper in
    // entered-content.tsx is silently back to raw-string matching.
    expect(pathKey(REPO_FROM_GIT)).toBe(pathKey(REPO_NATIVE))
  })

  it('keeps distinct repos distinct', () => {
    expect(pathKey('C:/work/alpha')).not.toBe(pathKey('C:\\work\\beta'))
  })

  it('leaves lanes alone when there is no probe at all (remote backend)', () => {
    // No worktree probe → no homeBranch → the recorded lanes are all we have,
    // and folding them would invent a branch label the backend never reported.
    const groups = mergeRepoWorktreeGroups(repo(), undefined)

    expect(
      groups
        .filter(g => g.isMain)
        .map(g => g.label)
        .sort()
    ).toEqual(['dev', 'main'])
  })

  it('does not fold a detached HEAD into a named home lane', () => {
    // detached === true means there IS no branch; mergeRepoWorktreeGroups
    // deliberately computes no homeBranch in that case.
    const groups = mergeRepoWorktreeGroups(repo(), [
      { branch: null, detached: true, isMain: true, locked: false, path: REPO_FROM_GIT }
    ])

    expect(
      groups
        .filter(g => g.isMain)
        .map(g => g.label)
        .sort()
    ).toEqual(['dev', 'main'])
  })
})

describe('probe map lookup (the actual bug site)', () => {
  const MAIN: HermesGitWorktree = {
    branch: LIVE_BRANCH,
    detached: false,
    isMain: true,
    locked: false,
    path: REPO_FROM_GIT
  }

  it('finds the repo when git keyed the map with POSIX separators', () => {
    // Exactly the shape useRepoWorktreeMap produces on Windows: the KEY is the
    // path git printed, the QUERY is repo.path. A raw map[repo.path] index
    // returns undefined here, which is what silently killed the home-lane fold.
    const map: Record<string, HermesGitWorktree[]> = { [REPO_FROM_GIT]: [MAIN] }

    // The raw index the component used to do — undefined, hence the dead fold.
    expect(map[REPO_NATIVE]).toBeUndefined()
    expect(lookupRepoWorktrees(map, REPO_NATIVE)).toEqual([MAIN])
  })

  it('still takes the fast path when the key already matches', () => {
    const map = { [REPO_NATIVE]: [MAIN] }

    expect(lookupRepoWorktrees(map, REPO_NATIVE)).toEqual([MAIN])
  })

  it('does not match a different repo that merely normalises similarly', () => {
    const map = { 'C:/work/alpha': [MAIN] }

    expect(lookupRepoWorktrees(map, 'C:\\work\\beta')).toBeUndefined()
  })

  it('handles trailing separators and case on Windows', () => {
    const map = { 'C:/Work/Alpha/': [MAIN] }

    expect(lookupRepoWorktrees(map, 'c:\\work\\alpha')).toEqual([MAIN])
  })

  it('returns undefined for missing map or path instead of throwing', () => {
    expect(lookupRepoWorktrees(undefined, REPO_NATIVE)).toBeUndefined()
    expect(lookupRepoWorktrees({ [REPO_FROM_GIT]: [MAIN] }, undefined)).toBeUndefined()
    expect(lookupRepoWorktrees({ [REPO_FROM_GIT]: [MAIN] }, '')).toBeUndefined()
  })
})

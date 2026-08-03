/**
 * Guard: the frontend and backend must agree on a branch lane's id.
 *
 * windro saw ONE session rendered twice in a project — under a lane labelled
 * "main" and another labelled with the folder name. The database had exactly one
 * row; both lanes were for it.
 *
 * Cause: `liveLaneForRepo` substituted the DISPLAY fallback into the IDENTITY.
 *
 *   const branch = (session.git_branch || '').trim() || DEFAULT_BRANCH_LABEL
 *   id: branchLaneId(repoRoot, branch)        // -> "<repo>::branch::main"
 *
 * while the backend keys a branchless session on the empty string
 * (`_branch_lane_id` in tui_gateway/project_tree.py, docstring:
 * "<repoRoot>::branch::<branch>` (or `::branch::`)") -> "<repo>::branch::".
 * Two ids, so the reconciler produced two lanes.
 *
 * It only surfaces outside a git repo, where `git_branch` is null — which is
 * exactly the case windro hit, because `Documents\Asthra HR admin` is not a repo.
 * `branchLaneId`'s own comment already demanded the ids match, so nothing in the
 * codebase disagreed with the rule; it just wasn't enforced anywhere.
 *
 * These tests encode the backend's formula independently. If either side changes
 * its shape, this fails rather than silently splitting lanes again.
 *
 * Fork-owned directory; upstream has no src/__fork__/.
 */

import { describe, expect, it } from 'vitest'

import {
  branchLaneId,
  DEFAULT_BRANCH_LABEL,
  overlayRepoLanes
} from '@/app/chat/sidebar/projects/workspace-groups'
import type { SessionInfo } from '@/types/hermes'

const REPO = 'C:\\Users\\wnxdd\\Documents\\Asthra HR admin'

/**
 * The backend's id formula, restated here on purpose.
 *
 * Mirrors tui_gateway/project_tree.py:
 *   f"{repo_root}::branch::{(branch or '').strip()}"
 * Deliberately NOT calling branchLaneId — a test that reuses the code under test
 * cannot detect the two drifting apart.
 */
function backendBranchLaneId(repoRoot: string, branch: null | string): string {
  return `${repoRoot}::branch::${(branch ?? '').trim()}`
}

function session(overrides: Partial<SessionInfo> = {}): SessionInfo {
  return {
    cwd: REPO,
    git_branch: null,
    git_repo_root: null,
    id: 'session-1',
    last_active: 2,
    message_count: 3,
    source: 'desktop',
    started_at: 1,
    ...overrides
  } as SessionInfo
}

describe('branch lane ids match the backend', () => {
  it('agrees for a session with no recorded branch', () => {
    // The regression. A folder that is not a git repo records no branch.
    expect(branchLaneId(REPO, '')).toBe(backendBranchLaneId(REPO, null))
  })

  it.each([null, undefined, '', '   '])('agrees for a blank branch value: %p', blank => {
    expect(branchLaneId(REPO, blank as string | undefined)).toBe(backendBranchLaneId(REPO, blank as null | string))
  })

  it.each(['main', 'master', 'feature/thing', 'release-1.2'])('agrees for branch %s', branch => {
    expect(branchLaneId(REPO, branch)).toBe(backendBranchLaneId(REPO, branch))
  })

  it('never bakes the display fallback into an id', () => {
    // The specific mistake: "main" must not appear in the id just because the
    // label says "main".
    expect(branchLaneId(REPO, '')).not.toContain(DEFAULT_BRANCH_LABEL)
  })
})

describe('a branchless session produces exactly one lane', () => {
  /**
   * Lanes the sidebar renders for one repo holding one session.
   *
   * Seeded the way the backend snapshot arrives — a single lane keyed on the raw
   * branch and labelled from the folder — then reconciled against the live
   * session cache, which is where the synthesised lane used to appear.
   */
  function lanesFor(target: SessionInfo) {
    const backendLane = {
      id: backendBranchLaneId(REPO, target.git_branch ?? null),
      isMain: true,
      label: 'Asthra HR admin',
      path: REPO,
      sessions: [target]
    }

    const repo = {
      groups: [backendLane],
      id: REPO,
      label: 'Asthra HR admin',
      path: REPO,
      sessionCount: 1
    }

    return overlayRepoLanes(repo as never, [target], new Set()).groups
  }

  it('does not split into two lanes when git_branch is null', () => {
    const lanes = lanesFor(session())

    expect(
      lanes.length,
      'one session rendered under two lanes — the frontend synthesised a lane id ' +
        'the backend does not use'
    ).toBe(1)
  })

  it('keeps a single lane when the branch is known', () => {
    const lanes = lanesFor(session({ git_branch: 'main' }))

    expect(lanes.length).toBe(1)
  })

  it('still labels a branchless lane readably', () => {
    // Losing the id fallback must not leave the lane blank in the UI.
    const lanes = lanesFor(session())

    expect(lanes[0].label.trim().length).toBeGreaterThan(0)
  })
})

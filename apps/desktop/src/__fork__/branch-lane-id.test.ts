/**
 * Guard: one session must never render under two lanes.
 *
 * The bug: project "Asthra HR admin" showed the SAME session under a lane called
 * "main" and another called "Asthra HR admin". The folder is not a git repo, so
 * git_branch and git_repo_root are both null.
 *
 * Mechanism. With no repo and no persisted root the backend falls to its path-only
 * heuristic (tui_gateway/project_tree.py:215) and emits a lane keyed on the BARE
 * PATH, labelled with the FOLDER NAME. The frontend computes `<path>::branch::main`
 * labelled "main". overlayRepoLanes matched neither by id nor by label, and skipped
 * its path rung because the lane is main — so it created a second lane.
 *
 * Two earlier mistakes this file exists to prevent repeating:
 *  1. I "fixed" it by making liveLaneForRepo build the id from the RAW branch,
 *     believing the backend did too. Every backend caller normalises first
 *     (project_tree.py:229/245/253); only _branch_lane_id itself does not. That
 *     change introduced a mismatch instead of removing one.
 *  2. The guard I wrote then compared against a mirror helper I authored from the
 *     same misreading, so it validated the mistake. It also asserted an id must
 *     never contain the fallback label — the opposite of correct behaviour.
 *
 * So the assertions here are about OBSERVABLE lane counts, not about id spelling.
 */

import { describe, expect, it } from 'vitest'

import { branchLaneId, DEFAULT_BRANCH_LABEL, overlayRepoLanes } from '@/app/chat/sidebar/projects/workspace-groups'
import type { SidebarSessionGroup, SidebarWorkspaceTree } from '@/app/chat/sidebar/projects/workspace-groups'
import type { SessionInfo } from '@/types/hermes'

const REPO = 'C:\\Users\\me\\Documents\\Asthra HR admin'

function session(overrides: Partial<SessionInfo> = {}): SessionInfo {
  return {
    cwd: REPO,
    git_branch: null,
    id: 's1',
    last_active: 2,
    started_at: 1,
    ...overrides
  } as SessionInfo
}

/** A repo node holding whatever lane the backend sent. */
function repoWith(lane: Partial<SidebarSessionGroup>): SidebarWorkspaceTree {
  return {
    groups: [{ id: REPO, isMain: true, label: 'Asthra HR admin', path: REPO, sessions: [], ...lane }],
    id: REPO,
    label: 'Asthra HR admin',
    path: REPO,
    sessionCount: 0
  } as SidebarWorkspaceTree
}

function lanesFor(repo: SidebarWorkspaceTree, live: SessionInfo[]): SidebarSessionGroup[] {
  return overlayRepoLanes(repo, live, new Set<string>()).groups
}

describe('a session never renders under two lanes', () => {
  it('reuses the path-only heuristic lane the backend sends for a non-repo folder', () => {
    // The exact reported case: lane keyed on the bare path, labelled by folder.
    //
    // The lane is seeded WITH the session because that is what the backend sends —
    // it only emits non-empty lanes. Seeding it empty makes this test useless: the
    // empty lane gets pruned, so a duplicate collapses back to one and the
    // assertion passes even when the bug is present. That mistake made an earlier
    // version of this guard survive its own mutation check.
    const repo = repoWith({ id: REPO, label: 'Asthra HR admin' })

    repo.groups[0].sessions = [session()]

    const lanes = lanesFor(repo, [session()])

    expect(
      lanes.map(g => g.label),
      'a second lane was created — this is the duplicate windro reported'
    ).toEqual(['Asthra HR admin'])
    expect(lanes[0].sessions.map((s: SessionInfo) => s.id)).toEqual(['s1'])
  })

  it('reuses a branch-keyed main lane when the backend resolved the repo', () => {
    // With a real repo the backend emits `<root>::branch::main`.
    const lanes = lanesFor(repoWith({ id: branchLaneId(REPO, DEFAULT_BRANCH_LABEL), label: DEFAULT_BRANCH_LABEL }), [
      session()
    ])

    expect(lanes.length).toBe(1)
  })

  it('reuses the lane for a session with a real branch', () => {
    const lanes = lanesFor(repoWith({ id: branchLaneId(REPO, 'main'), label: 'main' }), [
      session({ git_branch: 'main' })
    ])

    expect(lanes.length).toBe(1)
  })

  it('does not merge a feature branch into main', () => {
    // The opposite failure the path rung could cause: every lane this code builds
    // carries isMain: true, so matching on path alone would collapse distinct
    // branches into one lane.
    //
    // main is seeded WITH a session on purpose — overlayRepoLanes prunes lanes that
    // end up empty, so a session-less main would vanish and the assertion would
    // read as a merge when nothing merged.
    const repo = repoWith({ id: branchLaneId(REPO, 'main'), label: 'main' })

    repo.groups[0].sessions = [session({ git_branch: 'main', id: 'on-main' })]

    const lanes = lanesFor(repo, [session({ git_branch: 'feature/x', id: 'on-feature' })])
    const byLabel = new Map(lanes.map(g => [g.label, g.sessions.map(s => s.id)]))

    expect([...byLabel.keys()].sort(), 'a feature branch must get its own lane').toEqual(['feature/x', 'main'])
    expect(byLabel.get('main')).toEqual(['on-main'])
    expect(byLabel.get('feature/x')).toEqual(['on-feature'])
  })

  it('keeps one lane when several sessions share a branch', () => {
    const lanes = lanesFor(repoWith({ id: REPO, label: 'Asthra HR admin' }), [
      session({ id: 's1' }),
      session({ id: 's2' })
    ])

    expect(lanes.length).toBe(1)
    expect(lanes[0].sessions.map((s: SessionInfo) => s.id).sort()).toEqual(['s1', 's2'])
  })

  it('still labels the lane readably', () => {
    const lanes = lanesFor(repoWith({ id: REPO, label: 'Asthra HR admin' }), [session()])

    expect(lanes[0].label.trim().length).toBeGreaterThan(0)
  })
})

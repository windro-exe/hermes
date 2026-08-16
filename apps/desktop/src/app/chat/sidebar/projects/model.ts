import { useStore } from '@nanostores/react'
import { useEffect, useMemo, useState } from 'react'

import type { HermesGitWorktree } from '@/global'
import type { SessionInfo } from '@/hermes'
import { desktopGit } from '@/lib/desktop-git'
import { mapPool } from '@/lib/pool'
import { $sidebarWorkspaceNodeOpen, toggleWorkspaceNodeCollapsed } from '@/store/layout'
import { $worktreeRefreshToken } from '@/store/projects'

import { sessionRecency, type SidebarProjectTree } from './workspace-groups'

// Page size when revealing more already-loaded rows within a workspace group.
export const SIDEBAR_GROUP_PAGE = 5

// Recent sessions previewed under each project in the overview.
export const PROJECT_PREVIEW_COUNT = 3

// Max concurrent `git worktree list` probes when a project spans many repos.
const WORKTREE_PROBE_CONCURRENCY = 4

const pathListKey = (paths: string[]): string =>
  paths
    .map(path => path.trim())
    .filter(Boolean)
    .sort((a, b) => a.localeCompare(b))
    .join('\n')

// Every session in a project, across its repos/worktrees (order-agnostic).
const projectSessions = (project: SidebarProjectTree): SessionInfo[] =>
  project.repos.flatMap(repo => repo.groups.flatMap(group => group.sessions))

export const projectTreeCwd = (project: SidebarProjectTree): null | string =>
  project.path || project.repos.find(repo => repo.path)?.path || null

// Overview rows carry their activity stamp from the backend (lanes are empty in
// overview mode), falling back to loaded session times when present.
const projectActivityTime = (project: SidebarProjectTree): number =>
  Math.max(
    project.lastActive ?? 0,
    projectSessions(project).reduce((latest, s) => Math.max(latest, sessionRecency(s)), 0)
  )

// The project's most-recent sessions, for the overview preview under each row.
export const latestProjectSessions = (project: SidebarProjectTree, limit: number): SessionInfo[] =>
  [...projectSessions(project)].sort((a, b) => sessionRecency(b) - sessionRecency(a)).slice(0, limit)

/** Sessions in a project INCLUDING its sub-projects (backend-rolled). */
export const subtreeSessionCount = (project: SidebarProjectTree): number =>
  project.totalSessionCount ?? project.sessionCount

export function sortProjectsForOverview(
  projects: SidebarProjectTree[],
  activeProjectId: null | string
): SidebarProjectTree[] {
  return [...projects].sort((a, b) => {
    const aActive = Boolean(activeProjectId && a.id === activeProjectId && !a.isAuto)
    const bActive = Boolean(activeProjectId && b.id === activeProjectId && !b.isAuto)

    if (aActive !== bActive) {
      return aActive ? -1 : 1
    }

    if (!a.isAuto !== !b.isAuto) {
      return a.isAuto ? 1 : -1
    }

    // Subtree-inclusive: a namespace project ("official") holds no sessions of
    // its own, and must not sink below empty projects because of it.
    const aHasSessions = subtreeSessionCount(a) > 0
    const bHasSessions = subtreeSessionCount(b) > 0

    if (aHasSessions !== bHasSessions) {
      return aHasSessions ? -1 : 1
    }

    return (
      projectActivityTime(b) - projectActivityTime(a) ||
      a.label.localeCompare(b.label, undefined, { sensitivity: 'base' })
    )
  })
}

// Layer the user's manual drag-order over the deterministic sort.
//
// This can't just be `orderByIds`: that surfaces every id missing from the saved
// order at the TOP, which is right for sessions (a new chat should not sink) but
// wrong here. The overview also lists repos found by the disk scan that have
// zero Hermes sessions, and those arrive continuously — so once the user dragged
// anything, every freshly-scanned checkout jumped above the projects they
// actually work in.
//
// Fresh projects keep their place in the deterministic sort instead: ones with
// real activity go on top (a project you just started still surfaces), and
// zero-session discoveries sink below the hand-ordered list.
export function orderProjectsByIds(projects: SidebarProjectTree[], orderIds: string[]): SidebarProjectTree[] {
  if (!orderIds.length) {
    return projects
  }

  const byId = new Map(projects.map(project => [project.id, project]))
  const ordered = orderIds.map(id => byId.get(id)).filter((p): p is SidebarProjectTree => Boolean(p))
  const seen = new Set(ordered.map(project => project.id))
  const fresh = projects.filter(project => !seen.has(project.id))

  if (!fresh.length) {
    return ordered
  }

  return [
    ...fresh.filter(project => subtreeSessionCount(project) > 0),
    ...ordered,
    ...fresh.filter(project => subtreeSessionCount(project) <= 0)
  ]
}

/** True when any project in the overview is nested under another. */
export const hasNestedProjects = (projects: SidebarProjectTree[]): boolean =>
  projects.some(project => Boolean(project.parentId))

export interface ProjectOverviewRowEntry {
  project: SidebarProjectTree
  /** Indent level actually rendered — recomputed from the visible ancestors, so
   *  a child whose parent is filtered out of the overview still lines up. */
  depth: number
  /** Direct children, whether or not they are currently expanded. */
  childCount: number
}

/**
 * Order the overview's flat project list into a visible tree: each project is
 * followed by its children, and a collapsed project's whole subtree is omitted.
 *
 * The backend keeps the payload flat on purpose (every consumer that resolves a
 * cwd or a session to a project scans that list), so the hierarchy is drawn here
 * from `parentId`. Roots keep whatever order the caller already sorted them into;
 * children follow their parent in that same relative order.
 *
 * `isOpen` is asked only about projects that actually have children, so a leaf's
 * disclosure state (which toggles its session preview) never hides anything.
 */
export function arrangeProjectRows(
  projects: SidebarProjectTree[],
  isOpen: (project: SidebarProjectTree) => boolean
): ProjectOverviewRowEntry[] {
  const present = new Set(projects.map(project => project.id))
  const childrenOf = new Map<string, SidebarProjectTree[]>()
  const roots: SidebarProjectTree[] = []

  for (const project of projects) {
    const parentId = project.parentId ?? ''

    // A child whose parent is missing here (archived, dismissed, or filtered)
    // becomes a root rather than disappearing with it.
    if (parentId && present.has(parentId)) {
      const bucket = childrenOf.get(parentId)

      bucket ? bucket.push(project) : childrenOf.set(parentId, [project])
    } else {
      roots.push(project)
    }
  }

  // A `parentId` cycle leaves its members unreachable from every root, so a
  // plain root-down walk would drop them from the sidebar entirely. Find them
  // first (ignoring collapse) and treat them as roots: a corrupt edge should
  // cost the indent, not the project.
  const reachable = new Set<string>()

  const mark = (project: SidebarProjectTree): void => {
    if (reachable.has(project.id)) {
      return
    }

    reachable.add(project.id)

    for (const child of childrenOf.get(project.id) ?? []) {
      mark(child)
    }
  }

  for (const root of roots) {
    mark(root)
  }

  const rows: ProjectOverviewRowEntry[] = []
  const walked = new Set<string>()

  const walk = (project: SidebarProjectTree, depth: number): void => {
    if (walked.has(project.id)) {
      return
    }

    walked.add(project.id)
    const children = childrenOf.get(project.id) ?? []

    rows.push({ childCount: children.length, depth, project })

    if (children.length && isOpen(project)) {
      for (const child of children) {
        walk(child, depth + 1)
      }
    }
  }

  for (const project of [...roots, ...projects.filter(p => !reachable.has(p.id))]) {
    walk(project, 0)
  }

  return rows
}

// Project drill-in lanes are git-driven: source them from `git worktree list` so
// linked worktrees still appear even when their sessions aren't in the recents
// payload currently loaded in memory.
export function useRepoWorktreeMap(
  repoPaths: string[],
  enabled: boolean
): [Record<string, HermesGitWorktree[]>, boolean] {
  const [map, setMap] = useState<Record<string, HermesGitWorktree[]>>({})
  const [loading, setLoading] = useState(false)
  const key = useMemo(() => pathListKey(repoPaths), [repoPaths])
  // Refetch when a worktree is added/removed so a new lane shows immediately.
  const refreshToken = useStore($worktreeRefreshToken)

  useEffect(() => {
    const git = desktopGit()

    if (!enabled || !repoPaths.length || !git?.worktreeList) {
      setMap({})
      setLoading(false)

      return
    }

    let cancelled = false

    setLoading(true)
    // Bounded so a many-repo project doesn't spawn a `git` process per repo at once.
    void mapPool(repoPaths, WORKTREE_PROBE_CONCURRENCY, async repoPath => {
      try {
        return [repoPath, await git.worktreeList(repoPath)] as const
      } catch {
        return [repoPath, []] as const
      }
    })
      .then(entries => void (cancelled || setMap(Object.fromEntries(entries))))
      .finally(() => void (cancelled || setLoading(false)))

    return () => {
      cancelled = true
    }
  }, [enabled, key, repoPaths, refreshToken])

  return [map, loading]
}

// Persisted open/collapse for a repo/worktree node. Lets a project's folder
// layout auto-restore when you enter it, and survive reloads.
//
// State is stored as the RESOLVED boolean per node (see `$sidebarWorkspaceNodeOpen`),
// so a node whose `defaultOpen` flips — an empty worktree/branch lane defaults
// collapsed, then defaults open once it holds a session — keeps whatever the
// user explicitly chose instead of having it silently reinterpreted. An absent
// id follows `defaultOpen`, so empty lanes still start collapsed until opened.
export function useWorkspaceNodeOpen(id: string, defaultOpen = true): [boolean, () => void] {
  const state = useStore($sidebarWorkspaceNodeOpen)

  return [state[id] ?? defaultOpen, () => toggleWorkspaceNodeCollapsed(id, defaultOpen)]
}

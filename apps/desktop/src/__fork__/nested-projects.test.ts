import { describe, expect, it } from 'vitest'

import {
  arrangeProjectRows,
  hasNestedProjects,
  orderProjectsByIds,
  sortProjectsForOverview
} from '@/app/chat/sidebar/projects'
import type { SidebarProjectTree } from '@/app/chat/sidebar/projects'

// FORK: projects nest to unlimited depth. The backend payload stays FLAT (every
// consumer that resolves a cwd or a session to a project scans that list), so
// the sidebar draws the hierarchy from `parentId` — that arrangement is what
// these cover.

const project = (id: string, over: Partial<SidebarProjectTree> = {}): SidebarProjectTree => ({
  id,
  label: id,
  path: `/repos/${id}`,
  repos: [],
  sessionCount: 0,
  ...over
})

const ids = (rows: ReturnType<typeof arrangeProjectRows>) => rows.map(row => row.project.id)
const allOpen = () => true
const allClosed = () => false

describe('arrangeProjectRows', () => {
  it('places each child directly after its parent', () => {
    const rows = arrangeProjectRows(
      [
        project('official'),
        project('personal'),
        project('nettacker', { parentId: 'os-projects' }),
        project('os-projects', { parentId: 'official' })
      ],
      allOpen
    )

    expect(ids(rows)).toEqual(['official', 'os-projects', 'nettacker', 'personal'])
  })

  it('reports depth from the visible ancestors, not the backend field', () => {
    const rows = arrangeProjectRows(
      [
        project('official', { depth: 0 }),
        // A stale/absent depth from the payload must not misalign the indent.
        project('os-projects', { depth: 7, parentId: 'official' })
      ],
      allOpen
    )

    expect(rows.map(row => row.depth)).toEqual([0, 1])
  })

  it('counts direct children whether or not they are expanded', () => {
    const projects = [
      project('official'),
      project('os-projects', { parentId: 'official' }),
      project('docs', { parentId: 'official' }),
      project('nettacker', { parentId: 'os-projects' })
    ]

    expect(arrangeProjectRows(projects, allOpen).map(row => row.childCount)).toEqual([2, 1, 0, 0])
    expect(arrangeProjectRows(projects, allClosed).map(row => row.childCount)).toEqual([2])
  })

  it('hides a collapsed project’s whole subtree, not just its children', () => {
    const projects = [
      project('official'),
      project('os-projects', { parentId: 'official' }),
      project('nettacker', { parentId: 'os-projects' }),
      project('personal')
    ]

    const rows = arrangeProjectRows(projects, node => node.id !== 'official')

    expect(ids(rows)).toEqual(['official', 'personal'])
  })

  it('promotes a child whose parent is absent from the overview', () => {
    // The parent was archived or dismissed. A project the user created must not
    // vanish along with it.
    const rows = arrangeProjectRows([project('orphan', { parentId: 'p_gone' })], allOpen)

    expect(ids(rows)).toEqual(['orphan'])
    expect(rows[0].depth).toBe(0)
  })

  it('survives a parentId cycle', () => {
    const rows = arrangeProjectRows(
      [project('a', { parentId: 'b' }), project('b', { parentId: 'a' })],
      allOpen
    )

    // Both are reachable as roots-of-last-resort; neither recurses forever.
    expect(ids(rows).sort()).toEqual(['a', 'b'])
  })

  it('leaves a flat list untouched', () => {
    const projects = [project('one'), project('two'), project('three')]
    const rows = arrangeProjectRows(projects, allOpen)

    expect(ids(rows)).toEqual(['one', 'two', 'three'])
    expect(rows.every(row => row.depth === 0 && row.childCount === 0)).toBe(true)
  })
})

describe('hasNestedProjects', () => {
  it('is false for a flat overview and true once anything has a parent', () => {
    expect(hasNestedProjects([project('one'), project('two')])).toBe(false)
    expect(hasNestedProjects([project('one'), project('two', { parentId: 'one' })])).toBe(true)
  })
})

describe('subtree-inclusive ordering', () => {
  it('keeps a namespace project above empty ones on its children’s activity', () => {
    // `official` holds no sessions itself; all the work lives in its subtree.
    const namespace = project('official', { lastActive: 9000, totalSessionCount: 4 })
    const empty = project('idle', { lastActive: 0 })

    expect(sortProjectsForOverview([empty, namespace], null).map(p => p.id)).toEqual([
      'official',
      'idle'
    ])
  })

  it('does not sink a namespace project below a hand-ordered list', () => {
    const namespace = project('official', { totalSessionCount: 2 })
    const ordered = project('ordered')

    expect(orderProjectsByIds([ordered, namespace], ['ordered']).map(p => p.id)).toEqual([
      'official',
      'ordered'
    ])
  })
})

/**
 * Guard: the Files panel and status bar follow the SESSION, not the last project.
 *
 * windro opened a session whose stored cwd was `Documents\tset`, and the Files
 * panel, status bar and the agent's file tools all operated on
 * `Documents\Asthra HR admin` — the project he had entered earlier. The agent read
 * that project's package.json and described the wrong product as the current one.
 *
 * Cause: `$currentCwd` is a persisted global written only on entering a project.
 * Nothing wrote it back from the active session, so it kept the last folder
 * forever, across restarts. The bar confidently naming a folder the session was
 * not using is what made this take two audits to find.
 *
 * Fork-owned file. Upstream has no src/__fork__/, so this cannot conflict.
 */

import { beforeEach, describe, expect, it } from 'vitest'

import { $activeSessionCwd, $activeSessionId, $currentCwd, $selectedStoredSessionId, $sessions } from '@/store/session'
import type { SessionInfo } from '@/types/hermes'

const ASTHRA = 'C:\\Users\\wnxdd\\Documents\\Asthra HR admin'
const TSET = 'C:\\Users\\wnxdd\\Documents\\tset'
const HOME = 'C:\\Users\\wnxdd'

function session(id: string, cwd: null | string): SessionInfo {
  return { cwd, id, message_count: 1 } as SessionInfo
}

beforeEach(() => {
  $sessions.set([])
  $activeSessionId.set(null)
  $selectedStoredSessionId.set(null)
  $currentCwd.set('')
})

describe('$activeSessionCwd', () => {
  it('reports the focused session cwd, not the remembered project', () => {
    // The exact reported shape: remembered global still on Asthra, session in tset.
    $currentCwd.set(ASTHRA)
    $sessions.set([session('s-tset', TSET), session('s-asthra', ASTHRA)])
    $activeSessionId.set('s-tset')

    expect($activeSessionCwd.get()).toBe(TSET)
  })

  it('follows a switch between sessions in different projects', () => {
    $currentCwd.set(ASTHRA)
    $sessions.set([session('s-tset', TSET), session('s-asthra', ASTHRA)])

    $activeSessionId.set('s-asthra')
    expect($activeSessionCwd.get()).toBe(ASTHRA)

    $activeSessionId.set('s-tset')
    expect($activeSessionCwd.get()).toBe(TSET)
  })

  it('reports the real directory for a plain non-project session', () => {
    // NOT a "no folder" state: such a session genuinely runs in the home dir —
    // its terminal and file browser really are there — so that is the honest
    // answer. windro was explicit about this.
    $currentCwd.set(ASTHRA)
    $sessions.set([session('s-home', HOME)])
    $activeSessionId.set('s-home')

    expect($activeSessionCwd.get()).toBe(HOME)
  })

  it('falls back to the remembered cwd when nothing is focused', () => {
    // A fresh window before any session is selected still needs a root.
    $currentCwd.set(ASTHRA)
    $sessions.set([session('s-tset', TSET)])

    expect($activeSessionCwd.get()).toBe(ASTHRA)
  })

  it('falls back when the focused row has no cwd rather than blanking', () => {
    $currentCwd.set(ASTHRA)
    $sessions.set([session('s-none', null)])
    $activeSessionId.set('s-none')

    expect($activeSessionCwd.get()).toBe(ASTHRA)
  })

  it('honours the selected stored session when none is live', () => {
    $currentCwd.set(ASTHRA)
    $sessions.set([session('s-tset', TSET)])
    $selectedStoredSessionId.set('s-tset')

    expect($activeSessionCwd.get()).toBe(TSET)
  })

  it('prefers the live session over the stored selection', () => {
    $sessions.set([session('s-tset', TSET), session('s-asthra', ASTHRA)])
    $selectedStoredSessionId.set('s-asthra')
    $activeSessionId.set('s-tset')

    expect($activeSessionCwd.get()).toBe(TSET)
  })
})

describe('the surfaces that showed the wrong project', () => {
  it('the Files panel reads the session cwd', async () => {
    const src = await import('fs').then(fs => fs.readFileSync('src/app/right-sidebar/index.tsx', 'utf8'))

    expect(src).toContain('$activeSessionCwd')
    expect(src, 'the Files panel must not read the persisted global directly').not.toMatch(/useStore\(\$currentCwd\)/)
  })

  it('the status bar reads the session cwd', async () => {
    const src = await import('fs').then(fs => fs.readFileSync('src/app/shell/hooks/use-statusbar-items.tsx', 'utf8'))

    expect(src).toContain('$activeSessionCwd')
    expect(src, 'the status bar must not read the persisted global directly').not.toMatch(/useStore\(\$currentCwd\)/)
  })
})

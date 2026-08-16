import { beforeEach, describe, expect, it } from 'vitest'

import {
  $currentCwd,
  getProfileForScopedKeys,
  getRememberedRoute,
  getRememberedWorkspaceCwd,
  rehydrateProfileScopedSessionState,
  setConnection,
  setCurrentCwd,
  setProfileForScopedKeys,
  setRememberedRoute
} from '@/store/session'

/**
 * FORK regression tests for profile-scoped storage keys.
 *
 * `lastSessionId` was scoped per profile for #63590, but two neighbouring keys
 * were not, and both undid that fix in practice:
 *
 *   - `lastRoute` is PREFERRED over the remembered session id when restoring, so
 *     a flat route key sent a relaunch straight into another profile's session.
 *   - the workspace-cwd key namespaces by profile for remote connections but not
 *     local ones, so every local profile shared one "last folder" — which seeds
 *     new sessions and new terminals, meaning the agent could read and write in
 *     another profile's project directory.
 */

// Must match session.ts exactly — note the hyphen.
const WORKSPACE_KEY = 'hermes.desktop.workspace-cwd'
const ROUTE_KEY = 'hermes.desktop.lastRoute'

describe('profile-scoped storage keys', () => {
  beforeEach(() => {
    window.localStorage.clear()
    setConnection(null)
    setProfileForScopedKeys('default')
    $currentCwd.set('')
  })

  describe('setProfileForScopedKeys', () => {
    it('reports whether the profile actually moved', () => {
      expect(setProfileForScopedKeys('work')).toBe(true)
      expect(setProfileForScopedKeys('work')).toBe(false)
      expect(getProfileForScopedKeys()).toBe('work')
    })

    it('normalises empty and blank values to default', () => {
      setProfileForScopedKeys('work')
      setProfileForScopedKeys('   ')
      expect(getProfileForScopedKeys()).toBe('default')

      setProfileForScopedKeys('work')
      setProfileForScopedKeys(null)
      expect(getProfileForScopedKeys()).toBe('default')
    })
  })

  describe('workspace cwd (local connections)', () => {
    it('keeps the bare key for the default profile so upgrades survive', () => {
      // An existing install already has a value under the unsuffixed key; it must
      // remain readable rather than silently resetting to empty.
      window.localStorage.setItem(WORKSPACE_KEY, 'C:/existing/install')
      setProfileForScopedKeys('default')

      expect(getRememberedWorkspaceCwd()).toBe('C:/existing/install')
    })

    it('suffixes the key for a non-default profile', () => {
      window.localStorage.setItem(WORKSPACE_KEY, 'C:/profile/default')
      window.localStorage.setItem(`${WORKSPACE_KEY}.work`, 'C:/profile/work')

      setProfileForScopedKeys('work')
      expect(getRememberedWorkspaceCwd()).toBe('C:/profile/work')

      setProfileForScopedKeys('default')
      expect(getRememberedWorkspaceCwd()).toBe('C:/profile/default')
    })

    it('does NOT leak one profile folder into another', () => {
      // The reported bug: enter a project in one profile, switch, and the other
      // profile inherits that folder as its own.
      setProfileForScopedKeys('a')
      setCurrentCwd('C:/projects/alpha')

      setProfileForScopedKeys('b')
      expect(getRememberedWorkspaceCwd()).toBe('')
    })

    it('remembers each profile independently', () => {
      setProfileForScopedKeys('a')
      setCurrentCwd('C:/projects/alpha')
      setProfileForScopedKeys('b')
      setCurrentCwd('C:/projects/beta')

      setProfileForScopedKeys('a')
      expect(getRememberedWorkspaceCwd()).toBe('C:/projects/alpha')
      setProfileForScopedKeys('b')
      expect(getRememberedWorkspaceCwd()).toBe('C:/projects/beta')
    })
  })

  describe('remembered route', () => {
    it('keeps the bare key for the default profile', () => {
      window.localStorage.setItem(ROUTE_KEY, '/skills')
      expect(getRememberedRoute('default')).toBe('/skills')
    })

    it('does not restore another profile session route', () => {
      // The exact shape of #63590 via the adjacent key: a session route stored
      // under profile A must not be what profile B reopens.
      setRememberedRoute('/session/abc-from-a', 'a')

      expect(getRememberedRoute('b')).toBeNull()
      expect(getRememberedRoute('a')).toBe('/session/abc-from-a')
    })

    it('falls back to the mirrored active profile when none is passed', () => {
      setProfileForScopedKeys('a')
      setRememberedRoute('/session/from-a')

      setProfileForScopedKeys('b')
      expect(getRememberedRoute()).toBeNull()

      setProfileForScopedKeys('a')
      expect(getRememberedRoute()).toBe('/session/from-a')
    })
  })

  describe('rehydrateProfileScopedSessionState', () => {
    it('re-reads $currentCwd for the newly active profile', () => {
      // $currentCwd is seeded from storage at module load, before the profile is
      // known. Without a re-read, a switch leaves the previous profile's folder in
      // the atom — and that value seeds new sessions and new terminals.
      setProfileForScopedKeys('a')
      setCurrentCwd('C:/projects/alpha')
      expect($currentCwd.get()).toBe('C:/projects/alpha')

      setProfileForScopedKeys('b')
      rehydrateProfileScopedSessionState()

      expect($currentCwd.get()).toBe('')
    })

    it('restores the folder when switching back', () => {
      setProfileForScopedKeys('a')
      setCurrentCwd('C:/projects/alpha')
      setProfileForScopedKeys('b')
      setCurrentCwd('C:/projects/beta')

      setProfileForScopedKeys('a')
      rehydrateProfileScopedSessionState()
      expect($currentCwd.get()).toBe('C:/projects/alpha')
    })
  })

  describe('remote connections keep their existing scheme', () => {
    it('still namespaces by baseUrl and profile', () => {
      setConnection({
        baseUrl: 'https://box.example',
        isFullscreen: false,
        mode: 'remote',
        nativeOverlayWidth: 0,
        profile: 'work'
      } as never)

      window.localStorage.setItem(
        `${WORKSPACE_KEY}.remote.${encodeURIComponent('https://box.example')}.${encodeURIComponent('work')}`,
        'C:/remote/work'
      )

      expect(getRememberedWorkspaceCwd()).toBe('C:/remote/work')
    })
  })
})

describe('new-chat draft is per profile', () => {
  beforeEach(() => {
    window.localStorage.clear()
    setProfileForScopedKeys('default')
  })

  it('does not restore another profile new-chat draft', async () => {
    const { stashSessionDraft, takeSessionDraft } = await import('@/store/composer')

    setProfileForScopedKeys('a')
    stashSessionDraft(null, 'half-written in A', [])

    setProfileForScopedKeys('b')
    // The bug: profile B opened a new chat prefilled with A's text, and pressing
    // Enter sent it into B's conversation.
    expect(takeSessionDraft(null).text).toBe('')

    setProfileForScopedKeys('a')
    expect(takeSessionDraft(null).text).toBe('half-written in A')
  })

  it('keeps the bare bucket for the default profile so drafts survive upgrade', async () => {
    const { stashSessionDraft, takeSessionDraft } = await import('@/store/composer')

    setProfileForScopedKeys('default')
    stashSessionDraft(null, 'existing draft', [])

    expect(takeSessionDraft(null).text).toBe('existing draft')
  })

  it('session-scoped drafts are unaffected (already unique per profile)', async () => {
    const { stashSessionDraft, takeSessionDraft } = await import('@/store/composer')

    setProfileForScopedKeys('a')
    stashSessionDraft('session-123', 'tied to a session', [])

    setProfileForScopedKeys('b')
    expect(takeSessionDraft('session-123').text).toBe('tied to a session')
  })
})

describe('project scope resets on a profile switch', () => {
  it('drops a project id that does not exist in the new profile', async () => {
    const { $activeGatewayProfile } = await import('@/store/profile')
    const { $projectScope, ALL_PROJECTS } = await import('@/store/projects')

    $activeGatewayProfile.set('a')
    $projectScope.set('p_deadbeef')
    expect($projectScope.get()).toBe('p_deadbeef')

    $activeGatewayProfile.set('b')

    // Left alone, resolveNewSessionCwd looks this id up in $projectTree and
    // resolves the PREVIOUS profile's project path for the new chat.
    expect($projectScope.get()).toBe(ALL_PROJECTS)
  })

  it('does not reset when the profile has not actually changed', async () => {
    const { $activeGatewayProfile } = await import('@/store/profile')
    const { $projectScope } = await import('@/store/projects')

    $activeGatewayProfile.set('a')
    $projectScope.set('p_keepme')
    $activeGatewayProfile.set('a')

    expect($projectScope.get()).toBe('p_keepme')
  })
})

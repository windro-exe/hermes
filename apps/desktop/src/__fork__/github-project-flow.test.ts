/**
 * Guards for the GitHub project flow (windro's fork).
 *
 * The properties worth pinning here are the security ones and the
 * degrade-gracefully ones — the parts whose failure is silent rather than loud.
 *
 * Fork-owned directory; upstream has no src/__fork__/.
 */

import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { beforeEach, describe, expect, it, vi } from 'vitest'

import { $github, $githubRepos, githubAvailable, normalizeRepoName } from '@/store/github'

const DESKTOP_ROOT = join(__dirname, '..', '..')

function source(relative: string): string {
  return readFileSync(join(DESKTOP_ROOT, relative), 'utf8')
}

beforeEach(() => {
  $github.set({ connected: false, login: null })
  $githubRepos.set(null)
  // Cast rather than @ts-expect-error: the property is optional, so deleting it
  // is legal and the directive was unused.
  delete (window as unknown as { hermesDesktop?: unknown }).hermesDesktop
})

describe('repository name normalisation', () => {
  it('replaces characters GitHub would silently rewrite', () => {
    // GitHub turns anything outside [A-Za-z0-9._-] into a hyphen server-side, so
    // normalising up front means the name shown is the name created.
    expect(normalizeRepoName('My App')).toBe('My-App')
    expect(normalizeRepoName('a/b\\c')).toBe('a-b-c')
    expect(normalizeRepoName('emoji 🎉 name')).toBe('emoji-name')
  })

  it('keeps the characters GitHub allows', () => {
    expect(normalizeRepoName('hermes-agent')).toBe('hermes-agent')
    expect(normalizeRepoName('my_project.v2')).toBe('my_project.v2')
  })

  it('trims leading and trailing hyphens', () => {
    expect(normalizeRepoName('  -weird-  ')).toBe('weird')
    expect(normalizeRepoName('!!!')).toBe('')
  })

  it('caps the length', () => {
    expect(normalizeRepoName('x'.repeat(300))).toHaveLength(100)
  })
})

describe('degrades when there is no desktop bridge', () => {
  it('reports GitHub as unavailable rather than throwing', () => {
    expect(githubAvailable()).toBe(false)
  })

  it('is available once the bridge exists', () => {
    ;(window as unknown as { hermesDesktop: unknown }).hermesDesktop = { github: {} }

    expect(githubAvailable()).toBe(true)
  })
})

describe('the token never reaches the renderer', () => {
  it('has no renderer-side API that returns a token', () => {
    const store = source('src/store/github.ts')

    // `status` deliberately answers "connected, and as whom". A getToken would
    // put the credential in the React tree, in devtools, and in any renderer
    // crash dump.
    expect(store).not.toMatch(/getToken|readToken|\btoken\b\s*:\s*string\s*}/)
  })

  it('does not persist the token anywhere in the renderer', () => {
    const store = source('src/store/github.ts')

    expect(store).not.toContain('localStorage')
    expect(store).not.toContain('persistString')
  })

  it('encrypts at rest in the main process', () => {
    const main = source('electron/main.ts')

    expect(main).toContain('_githubTokenPath')
    expect(main, 'the GitHub token is written without encryptDesktopSecret').toMatch(/encryptDesktopSecret\(token\)/)
    // Scoped to the writer rather than a byte-distance guess, which was brittle.
    const writer = main.slice(main.indexOf('function _writeGithubToken'))
    const body = writer.slice(0, writer.indexOf('\n}'))

    expect(body, 'the GitHub token file should be owner-only').toContain('mode: 0o600')
  })

  it('validates before storing, so a typo cannot look connected', () => {
    const main = source('electron/main.ts')
    const handler = main.slice(main.indexOf("ipcMain.handle('hermes:github:connect'"))
    const validate = handler.indexOf('githubIdentify(')
    const persist = handler.indexOf('_writeGithubToken(')

    expect(validate).toBeGreaterThan(-1)
    expect(persist).toBeGreaterThan(-1)
    expect(validate, 'the token is stored before GitHub confirms it').toBeLessThan(persist)
  })
})

describe('the token stays out of git metadata', () => {
  it('clones with an auth header, not a credential in the URL', () => {
    const ops = source('electron/github-ops.ts')

    // A token embedded in the remote URL lands in .git/config and in every
    // `git remote -v`; an extraHeader is per-invocation.
    expect(ops).toContain('http.extraHeader')
    expect(ops).not.toMatch(/https:\/\/\$\{token\}|:\$\{token\}@/)
  })

  it('defaults new repositories to private', () => {
    const ops = source('electron/github-ops.ts')

    // Publishing someone's working code on a misclick is not recoverable the way
    // flipping a private repo public is.
    expect(ops).toMatch(/private:\s*options\.private\s*\?\?\s*true/)
  })
})

describe('remote-gateway mode', () => {
  it('has no REST clone, so the UI must not offer one', () => {
    const bridge = source('src/lib/desktop-git.ts')

    // Cloning happens on the machine the user sits at; the GitHub surface is
    // Electron-only and `githubAvailable()` gates the button.
    expect(bridge).not.toContain("gitPost('clone'")
  })

  it('reports no remotes rather than failing the offer probe', () => {
    const bridge = source('src/lib/desktop-git.ts')

    expect(bridge).toMatch(/remoteList:\s*async\s*\(\)\s*=>\s*\[\]/)
  })
})

describe('the remote-connect offer stays quiet unless it can help', () => {
  const projects = () => source('src/store/projects.ts')

  it('requires the GitHub bridge', () => {
    expect(projects()).toMatch(/if \(!dir \|\| !githubAvailable\(\)\)/)
  })

  it('requires a connected account', () => {
    // Otherwise accepting the offer opens a token prompt the user never asked for.
    expect(projects()).toMatch(/if \(!status\.connected\)/)
  })

  it('does not offer when the repo already has a remote', () => {
    expect(projects()).toMatch(/remotes && remotes\.length > 0/)
  })

  it('persists until dismissed', () => {
    // clearNotifications() fires on prompt submit and session switch, so a timed
    // toast would vanish before it was read — the update-toast lesson.
    const src = projects()
    const offer = src.slice(src.indexOf('async function offerRemoteForProject'))

    expect(offer.slice(0, offer.indexOf('\n}'))).toContain('durationMs: 0')
  })

  it('is wired to something that can act on it', () => {
    // A button that sets an atom nothing reads is worse than no button.
    const dialog = source('src/app/chat/sidebar/projects/project-remote-dialog.tsx')

    expect(dialog).toContain('$projectRemotePrompt')
    expect(dialog).toContain('connectRemoteToRepo')
    expect(source('src/app/chat/sidebar/index.tsx')).toContain('<ProjectRemoteDialog />')
  })
})

describe('failed pushes are reported honestly', () => {
  it('separates connecting the remote from pushing to it', () => {
    const ops = source('electron/github-ops.ts')

    // A protected branch or diverged history should not read as "nothing
    // happened" when the remote was in fact wired.
    expect(ops).toMatch(/pushError/)
    expect(ops).toMatch(/pushed:\s*(true|false)/)
  })

  it('surfaces the distinction in the UI', () => {
    const dialog = source('src/app/chat/sidebar/projects/project-remote-dialog.tsx')

    expect(dialog).toContain('push pending')
  })
})

describe('the repo list is bounded', () => {
  it('stops after two pages', () => {
    const ops = source('electron/github-ops.ts')

    // This feeds a picker, not a sync; an account with hundreds of repos must not
    // stall project creation.
    expect(ops).toMatch(/for \(const page of \[1, 2\]\)/)
  })
})

describe('connect failures surface instead of silently doing nothing', () => {
  it('notifies when the bridge is missing', async () => {
    const notifications = await import('@/store/notifications')
    const spy = vi.spyOn(notifications, 'notifyError').mockReturnValue('id')
    const { connectGitHub } = await import('@/store/github')

    const result = await connectGitHub('ghp_whatever')

    expect(result).toBeNull()
    expect(spy).toHaveBeenCalled()
    spy.mockRestore()
  })
})

describe('device-flow sign-in', () => {
  const ops = () => source('electron/github-ops.ts')

  it('uses a public client id and no secret', () => {
    // Device flow is defined for PUBLIC clients: there is no secret, and shipping
    // one in a desktop app would not be secret anyway.
    const src = ops()

    expect(src).toContain('DEVICE_CLIENT_ID')
    expect(src, 'a client secret must never be in the bundle').not.toMatch(/client_secret/i)
  })

  it('does not borrow another product client id', () => {
    // Reusing the GitHub CLI's id is a known trick and it makes the consent screen
    // lie about who is asking.
    expect(ops()).not.toMatch(/178c6fc778ccc68e1d6a/i)
  })

  it('honours the poll interval and slow_down', () => {
    const store = source('src/store/github.ts')

    // Ignoring either gets the flow rate-limited, which surfaces later as an
    // unexplained failure.
    expect(store).toContain('start.interval')
    expect(store).toMatch(/slowDown/)
  })

  it('distinguishes pending from failed', () => {
    // authorization_pending is the normal state for most of the flow; treating it
    // as an error would abort every sign-in.
    const src = ops()

    expect(src).toContain('authorization_pending')
    expect(src).toContain('expired_token')
    expect(src).toContain('access_denied')
  })

  it('validates the device-flow token before storing it, like the paste path', () => {
    const main = source('electron/main.ts')
    const handler = main.slice(main.indexOf("ipcMain.handle('hermes:github:devicePoll'"))
    const body = handler.slice(0, handler.indexOf('\n})'))
    const validate = body.indexOf('githubIdentify(')
    const persist = body.indexOf('_writeGithubToken(')

    expect(validate).toBeGreaterThan(-1)
    expect(persist).toBeGreaterThan(-1)
    expect(validate).toBeLessThan(persist)
  })

  it('opens the browser from the main process', () => {
    // Not a window.open from web content.
    const main = source('electron/main.ts')
    const handler = main.slice(main.indexOf("ipcMain.handle('hermes:github:deviceStart'"))

    expect(handler.slice(0, 500)).toContain('shell.openExternal')
  })

  it('can be cancelled', () => {
    const store = source('src/store/github.ts')

    expect(store).toContain('cancelGitHubDeviceFlow')
  })
})

describe('the connection is one account for the whole app', () => {
  it('stores the token in a single place, not per project', () => {
    // So signing in from the project dialog shows as signed in in Settings, and
    // creating a second project never asks again.
    const main = source('electron/main.ts')
    const writer = main.slice(main.indexOf('function _githubTokenPath'))

    expect(writer.slice(0, 200)).toContain("'github-token.json'")
    expect(main, 'the token path must not be keyed by project or folder').not.toMatch(
      /github-token[^\n]*\$\{(project|folder|cwd)/
    )
  })

  it('settings and the project dialog read the same store', () => {
    const settings = source('src/app/settings/github-settings.tsx')
    const picker = source('src/app/chat/sidebar/projects/github-repo-picker.tsx')

    for (const src of [settings, picker]) {
      expect(src).toContain("from '@/store/github'")
      expect(src).toContain('$github')
    }
  })

  it('offers sign out in settings', () => {
    const settings = source('src/app/settings/github-settings.tsx')

    expect(settings).toContain('disconnectGitHub')
    expect(settings).toContain('Sign out')
  })

  it('is reachable as its own settings view', () => {
    expect(source('src/app/settings/types.ts')).toContain("| 'github'")
    expect(source('src/app/settings/index.tsx')).toContain('<GitHubSettings />')
  })

  it('says signing out does not revoke on GitHub', () => {
    // Removing a local token and revoking access are different things, and
    // implying otherwise would leave someone believing they had revoked it.
    expect(source('src/app/settings/github-settings.tsx')).toMatch(/does not revoke/i)
  })
})

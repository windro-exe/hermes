// GitHub connection + repository picking for the project flow.
//
// The token lives in the main process only (encrypted through Electron
// safeStorage). This store holds just the derived facts the UI needs: are we
// connected, as whom, and which repositories can be picked.
//
// Remote-gateway mode has no equivalent, on purpose. Cloning a repository is an
// operation on the machine the user is sitting at, and the desktop's GitHub bridge
// is an Electron IPC surface. When it is absent, `githubAvailable()` is false and
// the dialog offers only the local-folder path rather than showing a button that
// cannot work.

import { atom } from 'nanostores'

import type { HermesGitHubRepo } from '@/global'
import { notifyError } from '@/store/notifications'

export interface GitHubConnection {
  /** Set when a stored token was rejected — expired or revoked. */
  error?: string
  connected: boolean
  login: null | string
}

export const $github = atom<GitHubConnection>({ connected: false, login: null })
export const $githubRepos = atom<HermesGitHubRepo[] | null>(null)
export const $githubBusy = atom(false)

function bridge() {
  return window.hermesDesktop?.github
}

/** Whether this build can talk to GitHub at all (false in remote-gateway mode). */
export function githubAvailable(): boolean {
  return Boolean(bridge())
}

/**
 * Refresh the connection state.
 *
 * Validates against GitHub rather than trusting the presence of a stored token, so
 * a revoked token surfaces as disconnected-with-a-reason here instead of as a
 * confusing failure on the first repository call.
 */
export async function refreshGitHubStatus(): Promise<GitHubConnection> {
  const api = bridge()

  if (!api) {
    const offline: GitHubConnection = { connected: false, login: null }
    $github.set(offline)

    return offline
  }

  try {
    const status = await api.status()

    const next: GitHubConnection = {
      connected: Boolean(status.connected),
      login: status.login ?? null,
      ...(status.error ? { error: status.error } : {})
    }

    $github.set(next)

    return next
  } catch {
    const failed: GitHubConnection = { connected: false, login: null }
    $github.set(failed)

    return failed
  }
}

/** Store a token after GitHub confirms it. Returns the login, or null on failure. */
export async function connectGitHub(token: string): Promise<null | string> {
  const api = bridge()

  if (!api) {
    notifyError(new Error('GitHub is only available in the desktop app.'), 'GitHub unavailable')

    return null
  }

  $githubBusy.set(true)

  try {
    const identity = await api.connect(token)
    $github.set({ connected: true, login: identity.login })
    // Drop any cached list from a previous account.
    $githubRepos.set(null)

    return identity.login
  } catch (error) {
    notifyError(error, 'Could not connect to GitHub')

    return null
  } finally {
    $githubBusy.set(false)
  }
}

/** Shown while the browser half of device-flow sign-in is pending. */
export interface DeviceFlowPending {
  expiresAt: number
  userCode: string
  verificationUri: string
}

export const $githubDeviceFlow = atom<DeviceFlowPending | null>(null)

let deviceFlowCancelled = false

/** Abandon a pending sign-in. The browser tab is the user's to close. */
export function cancelGitHubDeviceFlow(): void {
  deviceFlowCancelled = true
  $githubDeviceFlow.set(null)
}

/**
 * Sign in through GitHub's device flow.
 *
 * Resolves with the login on success, or null if it was cancelled, expired, or
 * failed. The polling loop lives here rather than in the main process so the UI can
 * show the code, show progress, and cancel.
 *
 * GitHub's `interval` is respected and `slow_down` widens it — ignoring either gets
 * the flow rate-limited, which surfaces as an unexplained failure well after the
 * cause.
 */
export async function signInWithGitHub(): Promise<null | string> {
  const api = bridge()

  if (!api?.deviceStart) {
    notifyError(new Error('GitHub sign-in is only available in the desktop app.'), 'GitHub unavailable')

    return null
  }

  deviceFlowCancelled = false
  $githubBusy.set(true)

  try {
    const start = await api.deviceStart()

    $githubDeviceFlow.set({
      expiresAt: Date.now() + start.expiresIn * 1000,
      userCode: start.userCode,
      verificationUri: start.verificationUri
    })

    let waitMs = Math.max(start.interval, 1) * 1000
    const deadline = Date.now() + start.expiresIn * 1000

    while (Date.now() < deadline) {
      if (deviceFlowCancelled) {
        return null
      }

      await new Promise(resolve => setTimeout(resolve, waitMs))

      if (deviceFlowCancelled) {
        return null
      }

      const result = await api.devicePoll(start.deviceCode)

      if (result.connected && result.login) {
        $github.set({ connected: true, login: result.login })
        $githubDeviceFlow.set(null)
        $githubRepos.set(null)

        return result.login
      }

      if (result.slowDown) {
        waitMs += 5000
      }
    }

    notifyError(new Error('The sign-in code expired before it was approved.'), 'GitHub sign-in timed out')

    return null
  } catch (error) {
    notifyError(error, 'GitHub sign-in failed')

    return null
  } finally {
    $githubDeviceFlow.set(null)
    $githubBusy.set(false)
  }
}

export async function disconnectGitHub(): Promise<void> {
  try {
    await bridge()?.disconnect()
  } catch {
    // Clearing local state is what matters; a failed call still leaves the UI
    // consistent on the next status refresh.
  }

  $github.set({ connected: false, login: null })
  $githubRepos.set(null)
}

/** Load the repository list, most recently pushed first. Cached until reconnect. */
export async function loadGitHubRepos(force = false): Promise<HermesGitHubRepo[]> {
  const api = bridge()

  if (!api) {
    return []
  }

  const cached = $githubRepos.get()

  if (cached && !force) {
    return cached
  }

  $githubBusy.set(true)

  try {
    const repos = await api.listRepos()
    $githubRepos.set(repos)

    return repos
  } catch (error) {
    notifyError(error, 'Could not load your GitHub repositories')
    $githubRepos.set([])

    return []
  } finally {
    $githubBusy.set(false)
  }
}

/**
 * GitHub's own rules for a repository name, applied before we ask.
 *
 * GitHub silently rewrites anything outside `[A-Za-z0-9._-]` to a hyphen, so a
 * project called "My App" becomes "My-App" server-side. Normalising here means the
 * name shown in the dialog is the name that gets created.
 */
export function normalizeRepoName(raw: string): string {
  return raw
    .trim()
    .replace(/[^A-Za-z0-9._-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 100)
}

export async function createGitHubRepo(options: {
  description?: string
  name: string
  private?: boolean
}): Promise<HermesGitHubRepo | null> {
  const api = bridge()

  if (!api) {
    return null
  }

  const name = normalizeRepoName(options.name)

  if (!name) {
    notifyError(new Error('Give the repository a name.'), 'Cannot create repository')

    return null
  }

  $githubBusy.set(true)

  try {
    const repo = await api.createRepo({ ...options, name })
    // Keep the picker honest without a round trip.
    $githubRepos.set([repo, ...($githubRepos.get() ?? [])])

    return repo
  } catch (error) {
    notifyError(error, 'Could not create the repository')

    return null
  } finally {
    $githubBusy.set(false)
  }
}

/** Clone into `targetDir`. Returns the path on success, null on failure. */
export async function cloneGitHubRepo(cloneUrl: string, targetDir: string): Promise<null | string> {
  const api = bridge()

  if (!api) {
    return null
  }

  $githubBusy.set(true)

  try {
    const result = await api.clone({ cloneUrl, targetDir })

    return result.path
  } catch (error) {
    notifyError(error, 'Clone failed')

    return null
  } finally {
    $githubBusy.set(false)
  }
}

/**
 * Point an existing local repo at a GitHub remote and push its branch.
 *
 * Reports a failed push separately from a failed connect: wiring the remote is the
 * durable half, and a rejected push (protected branch, no permission, diverged
 * history) is worth telling the user about without implying nothing happened.
 */
export async function connectRemoteToRepo(
  repoDir: string,
  cloneUrl: string
): Promise<{ ok: boolean; pushError: null | string; pushed: boolean }> {
  const api = bridge()

  if (!api) {
    return { ok: false, pushError: null, pushed: false }
  }

  $githubBusy.set(true)

  try {
    const result = await api.connectRemote({ cloneUrl, repoDir })

    return { ok: true, pushError: result.pushError, pushed: result.pushed }
  } catch (error) {
    notifyError(error, 'Could not connect the remote')

    return { ok: false, pushError: null, pushed: false }
  } finally {
    $githubBusy.set(false)
  }
}

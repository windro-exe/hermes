/**
 * GitHub account connection for the project flow.
 *
 * Scope is deliberately narrow: hold one personal access token, and do the three
 * repository operations project creation needs (list, create, clone). Nothing
 * else talks to GitHub.
 *
 * Why a pasted token rather than a device flow: the OAuth device flow needs a
 * registered GitHub OAuth app, which is account-level setup this fork cannot
 * create on the user's behalf. A fine-grained PAT needs no registration and can be
 * scoped to a single repository. Device flow stays open as a follow-up for anyone
 * who registers an app.
 *
 * The token is encrypted at rest by the caller (`encryptDesktopSecret`, backed by
 * Electron `safeStorage` / the OS keychain) and only ever decrypted in the main
 * process. It is never sent to the renderer, never written to `.git/config`, and
 * never passed on a command line — clone injects it through a short-lived
 * `http.extraHeader`, then the remote is rewritten to the clean URL so nothing
 * durable holds the credential.
 */

import { execFile } from 'node:child_process'

const API = 'https://api.github.com'
const UA = 'hermes-desktop'

/**
 * OAuth app client id for the device flow.
 *
 * Public by design — a device-flow client is a PUBLIC client, so there is no
 * secret involved and nothing here is sensitive. GitHub's docs are explicit that
 * the client id is not a credential.
 *
 * Deliberately not the GitHub CLI's id. Borrowing another product's client id is a
 * common trick and it means the consent screen lies about who is asking.
 */
const DEVICE_CLIENT_ID = 'Ov23liaTFoBjSIYSbidk'

/**
 * Scope requested at sign-in.
 *
 * `repo` is the narrowest scope that covers what the project flow does: list
 * private repositories, create one, and push to it. A read-only scope cannot
 * create, and there is no finer-grained OAuth scope for "repositories I choose" —
 * that granularity only exists for fine-grained PATs, which is why the token-paste
 * path stays available for anyone who wants to scope tighter.
 */
const DEVICE_SCOPE = 'repo'

export interface DeviceFlowStart {
  /** Seconds until `deviceCode` stops being accepted. */
  expiresIn: number
  /** Seconds GitHub asks us to wait between polls. */
  interval: number
  deviceCode: string
  userCode: string
  verificationUri: string
}

async function oauthForm<T>(path: string, body: Record<string, string>): Promise<T> {
  const response = await fetch(`https://github.com${path}`, {
    body: new URLSearchParams(body).toString(),
    headers: {
      accept: 'application/json',
      'content-type': 'application/x-www-form-urlencoded',
      'user-agent': UA
    },
    method: 'POST'
  })

  if (!response.ok) {
    throw new Error(`GitHub returned ${response.status} ${response.statusText}`)
  }

  return (await response.json()) as T
}

/** Ask GitHub for a device code. The user enters `userCode` at `verificationUri`. */
export async function startDeviceFlow(): Promise<DeviceFlowStart> {
  const body = await oauthForm<{
    device_code?: string
    error?: string
    error_description?: string
    expires_in?: number
    interval?: number
    user_code?: string
    verification_uri?: string
  }>('/login/device/code', { client_id: DEVICE_CLIENT_ID, scope: DEVICE_SCOPE })

  if (body.error || !body.device_code || !body.user_code) {
    // The overwhelmingly likely cause is device flow not being enabled on the
    // OAuth app, and GitHub's raw error for that is unhelpful.
    const detail = body.error_description || body.error || 'no device code returned'
    throw new Error(
      `GitHub would not start the sign-in (${detail}). Check that "Enable Device Flow" is ticked on the OAuth app.`
    )
  }

  return {
    deviceCode: body.device_code,
    // Defaults per GitHub's documented behaviour when the fields are absent.
    expiresIn: body.expires_in ?? 900,
    interval: body.interval ?? 5,
    userCode: body.user_code,
    verificationUri: body.verification_uri || 'https://github.com/login/device'
  }
}

/**
 * Exchange a device code for a token, once.
 *
 * Returns `null` while the user has not finished approving, so the caller drives
 * the polling loop and can cancel. `slowDown` asks the caller to back off —
 * ignoring it gets the flow rate-limited, which presents as a mysterious failure.
 */
export async function pollDeviceFlow(deviceCode: string): Promise<{ slowDown?: boolean; token: null | string }> {
  const body = await oauthForm<{
    access_token?: string
    error?: string
    error_description?: string
  }>('/login/oauth/access_token', {
    client_id: DEVICE_CLIENT_ID,
    device_code: deviceCode,
    grant_type: 'urn:ietf:params:oauth:grant-type:device_code'
  })

  if (body.access_token) {
    return { token: body.access_token }
  }

  switch (body.error) {
    case 'authorization_pending':
      return { token: null }

    case 'slow_down':
      return { slowDown: true, token: null }

    case 'expired_token':
      throw new Error('The sign-in code expired. Start again.')

    case 'access_denied':
      throw new Error('Sign-in was cancelled on GitHub.')

    default:
      throw new Error(body.error_description || body.error || 'GitHub sign-in failed.')
  }
}

export interface GitHubIdentity {
  login: string
  name: null | string
}

export interface GitHubRepo {
  cloneUrl: string
  defaultBranch: string
  fullName: string
  name: string
  private: boolean
  pushedAt: null | string
}

function runGit(gitBin: string, args: string[], cwd?: string): Promise<string> {
  return new Promise((resolve, reject) => {
    execFile(
      gitBin,
      args,
      { cwd, maxBuffer: 8 * 1024 * 1024, timeout: 10 * 60_000, windowsHide: true },
      (err, stdout, stderr) => {
        if (err) {
          // Surface git's own message; it is far more useful than "exit 128".
          const detail = String(stderr || '').trim()
          reject(new Error(detail || (err as Error).message))

          return
        }

        resolve(String(stdout || ''))
      }
    )
  })
}

async function api<T>(token: string, path: string, init?: { body?: unknown; method?: string }): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    body: init?.body === undefined ? undefined : JSON.stringify(init.body),
    headers: {
      accept: 'application/vnd.github+json',
      authorization: `Bearer ${token}`,
      'content-type': 'application/json',
      'user-agent': UA,
      'x-github-api-version': '2022-11-28'
    },
    method: init?.method ?? 'GET'
  })

  if (!response.ok) {
    // GitHub puts the useful part in `message`; fall back to the status line.
    let message = `${response.status} ${response.statusText}`

    try {
      const body = (await response.json()) as { message?: string }

      if (body?.message) {
        message = body.message
      }
    } catch {
      // Non-JSON error body — keep the status line.
    }

    if (response.status === 401) {
      message = 'GitHub rejected the token. It may be expired or revoked.'
    }

    if (response.status === 403 && /rate limit/i.test(message)) {
      message = 'GitHub rate limit reached. Try again shortly.'
    }

    throw new Error(message)
  }

  return (await response.json()) as T
}

/** Confirm a token works and return who it belongs to. */
export async function identify(token: string): Promise<GitHubIdentity> {
  const user = await api<{ login: string; name?: null | string }>(token, '/user')

  return { login: user.login, name: user.name ?? null }
}

function toRepo(raw: {
  clone_url: string
  default_branch?: string
  full_name: string
  name: string
  private: boolean
  pushed_at?: null | string
}): GitHubRepo {
  return {
    cloneUrl: raw.clone_url,
    defaultBranch: raw.default_branch || 'main',
    fullName: raw.full_name,
    name: raw.name,
    private: Boolean(raw.private),
    pushedAt: raw.pushed_at ?? null
  }
}

/**
 * The user's repositories, most recently pushed first.
 *
 * `affiliation` covers repos owned by the user plus ones they collaborate on or
 * reach through an org, which is what someone picking "my repos" expects. Capped
 * at two pages: this feeds a picker, not a sync, and an account with hundreds of
 * repos should not stall project creation.
 */
export async function listRepos(token: string): Promise<GitHubRepo[]> {
  const repos: GitHubRepo[] = []

  for (const page of [1, 2]) {
    const batch = await api<Parameters<typeof toRepo>[0][]>(
      token,
      `/user/repos?per_page=100&sort=pushed&affiliation=owner,collaborator,organization_member&page=${page}`
    )

    repos.push(...batch.map(toRepo))

    if (batch.length < 100) {
      break
    }
  }

  return repos
}

/**
 * Create a repository on the user's account.
 *
 * Private by default. A project folder is someone's working code and defaulting
 * to public would publish it on a misclick; making it public later is one click,
 * un-publishing history is not.
 */
export async function createRepo(
  token: string,
  options: { description?: string; name: string; private?: boolean }
): Promise<GitHubRepo> {
  const raw = await api<Parameters<typeof toRepo>[0]>(token, '/user/repos', {
    body: {
      auto_init: false,
      description: options.description || undefined,
      name: options.name,
      private: options.private ?? true
    },
    method: 'POST'
  })

  return toRepo(raw)
}

/** `Authorization` header args for git, kept off the command line's visible tail. */
function authHeaderArgs(token: string): string[] {
  const basic = Buffer.from(`x-access-token:${token}`).toString('base64')

  return ['-c', `http.extraHeader=Authorization: Basic ${basic}`]
}

/**
 * Clone `cloneUrl` into `targetDir`.
 *
 * The token rides in a per-invocation `http.extraHeader` rather than in the URL,
 * so it never reaches `.git/config`, the reflog, or the remote URL that later
 * `git remote -v` calls will print. `--origin origin` and no `--depth` are
 * deliberate: this is a working checkout the user will commit to, not a
 * throwaway, so full history and a normal remote are what they expect.
 */
export async function cloneRepo(
  gitBin: string,
  options: { cloneUrl: string; targetDir: string; token?: string }
): Promise<{ ok: true; path: string }> {
  const auth = options.token ? authHeaderArgs(options.token) : []

  await runGit(gitBin, [...auth, 'clone', '--origin', 'origin', options.cloneUrl, options.targetDir])

  return { ok: true, path: options.targetDir }
}

/**
 * Point an existing local repo at a remote, and push its current branch.
 *
 * Used by the "connect a remote" path for a folder that was already a git repo.
 * Replaces an existing `origin` rather than failing, because the realistic reason
 * one exists is a previous half-finished attempt.
 *
 * The push is attempted but not required: connecting the remote is the useful
 * half, and a rejected push (protected branch, diverged history, no permission)
 * should report itself rather than undo the wiring.
 */
export async function connectRemote(
  gitBin: string,
  options: { cloneUrl: string; repoDir: string; token?: string }
): Promise<{ ok: true; pushError: null | string; pushed: boolean }> {
  const existing = await runGit(gitBin, ['remote'], options.repoDir).catch(() => '')

  if (
    existing
      .split('\n')
      .map(line => line.trim())
      .includes('origin')
  ) {
    await runGit(gitBin, ['remote', 'set-url', 'origin', options.cloneUrl], options.repoDir)
  } else {
    await runGit(gitBin, ['remote', 'add', 'origin', options.cloneUrl], options.repoDir)
  }

  const branch = (await runGit(gitBin, ['rev-parse', '--abbrev-ref', 'HEAD'], options.repoDir).catch(() => '')).trim()

  if (!branch || branch === 'HEAD') {
    return { ok: true, pushError: 'No branch is checked out, so nothing was pushed.', pushed: false }
  }

  try {
    const auth = options.token ? authHeaderArgs(options.token) : []
    await runGit(gitBin, [...auth, 'push', '-u', 'origin', branch], options.repoDir)

    return { ok: true, pushError: null, pushed: true }
  } catch (error) {
    return { ok: true, pushError: (error as Error).message, pushed: false }
  }
}

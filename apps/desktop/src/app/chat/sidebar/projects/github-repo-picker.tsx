// Connect a GitHub account and pick (or create) a repository, inline in the
// project dialog.
//
// Three states in one component, because they are one decision from the user's
// side: not connected → paste a token; connected → choose an existing repo;
// or name a new one. Splitting them across dialogs would make "I just want my repo
// as a project" a multi-step journey.
//
// Nothing here ever sees the token after it is submitted. `connectGitHub` hands it
// to the main process, which validates and encrypts it; this component only learns
// the resulting login.

import { useStore } from '@nanostores/react'
import { useEffect, useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { Input } from '@/components/ui/input'
import type { HermesGitHubRepo } from '@/global'
import { cn } from '@/lib/utils'
import {
  $github,
  $githubBusy,
  $githubDeviceFlow,
  $githubRepos,
  cancelGitHubDeviceFlow,
  connectGitHub,
  createGitHubRepo,
  disconnectGitHub,
  loadGitHubRepos,
  normalizeRepoName,
  refreshGitHubStatus,
  signInWithGitHub
} from '@/store/github'

const TOKEN_HELP = 'github.com → Settings → Developer settings → Personal access tokens'

export function GitHubRepoPicker({
  defaultRepoName,
  disabled,
  onCancel,
  onPick
}: {
  defaultRepoName?: string
  disabled?: boolean
  onCancel: () => void
  onPick: (repo: HermesGitHubRepo) => void
}) {
  const connection = useStore($github)
  const repos = useStore($githubRepos)
  const busy = useStore($githubBusy)
  const pending = useStore($githubDeviceFlow)

  const [token, setToken] = useState('')
  const [filter, setFilter] = useState('')
  const [newRepoName, setNewRepoName] = useState(defaultRepoName ? normalizeRepoName(defaultRepoName) : '')
  const [creating, setCreating] = useState(false)

  useEffect(() => {
    void refreshGitHubStatus().then(status => {
      if (status.connected) {
        void loadGitHubRepos()
      }
    })
  }, [])

  const visible = useMemo(() => {
    const needle = filter.trim().toLowerCase()
    const list = repos ?? []

    if (!needle) {
      // Capped so a large account renders a picker, not a wall.
      return list.slice(0, 60)
    }

    return list.filter(repo => repo.fullName.toLowerCase().includes(needle)).slice(0, 60)
  }, [filter, repos])

  const locked = Boolean(disabled) || busy

  // Sign-in is in progress: the browser is open and we are polling.
  if (pending) {
    return (
      <div className="flex flex-col gap-2 rounded-md border border-(--ui-stroke-secondary) p-2.5">
        <span className="text-[0.75rem] font-medium">Finish signing in on GitHub</span>
        <span className="text-[0.6875rem] text-(--ui-text-tertiary)">
          Your browser should have opened. Enter this code there:
        </span>
        <div className="flex items-center gap-2">
          <code className="rounded bg-(--ui-control-hover-background) px-2 py-1 font-mono text-[0.9375rem] tracking-[0.15em]">
            {pending.userCode}
          </code>
          <Button
            onClick={() => void navigator.clipboard?.writeText(pending.userCode)}
            size="xs"
            type="button"
            variant="ghost"
          >
            Copy
          </Button>
        </div>
        <span className="text-[0.625rem] text-(--ui-text-quaternary)">{pending.verificationUri}</span>
        <div className="flex items-center gap-2">
          <span className="text-[0.6875rem] text-(--ui-text-tertiary)">Waiting for approval…</span>
          <Button
            className="ml-auto"
            onClick={() => cancelGitHubDeviceFlow()}
            size="sm"
            type="button"
            variant="ghost"
          >
            Cancel
          </Button>
        </div>
      </div>
    )
  }

  if (!connection.connected) {
    return (
      <div className="flex flex-col gap-2 rounded-md border border-(--ui-stroke-secondary) p-2.5">
        <span className="text-[0.75rem] font-medium">Connect GitHub</span>
        <Button disabled={locked} onClick={() => void signInWithGitHub()} size="sm" type="button">
          <Codicon name="github" size="0.75rem" />
          Sign in with GitHub
        </Button>
        <span className="text-[0.625rem] text-(--ui-text-quaternary)">
          Opens github.com in your browser. Nothing to copy or paste.
        </span>
        <details className="mt-1">
          <summary className="cursor-pointer text-[0.6875rem] text-(--ui-text-tertiary)">
            Use a token instead
          </summary>
          <span className="mt-1 block text-[0.6875rem] text-(--ui-text-tertiary)">
            Paste a personal access token with <span className="font-medium">repo</span> access. Useful offline, or to
            scope a fine-grained token to a single repository. Stored encrypted by your operating system and never
            leaves this machine.
          </span>
        </details>
        {connection.error && (
          <span className="text-[0.6875rem] text-(--ui-text-danger)">{connection.error}</span>
        )}
        <Input
          disabled={locked}
          onChange={event => setToken(event.target.value)}
          onKeyDown={event => {
            if (event.key === 'Enter' && token.trim()) {
              event.preventDefault()
              void connectGitHub(token.trim()).then(login => {
                if (login) {
                  setToken('')
                  void loadGitHubRepos(true)
                }
              })
            }
          }}
          placeholder="ghp_… or github_pat_…"
          type="password"
          value={token}
        />
        <span className="text-[0.625rem] text-(--ui-text-quaternary)">{TOKEN_HELP}</span>
        <div className="flex items-center gap-2">
          <Button
            disabled={locked || !token.trim()}
            onClick={() =>
              void connectGitHub(token.trim()).then(login => {
                if (login) {
                  setToken('')
                  void loadGitHubRepos(true)
                }
              })
            }
            size="sm"
            type="button"
          >
            Connect
          </Button>
          <Button disabled={locked} onClick={onCancel} size="sm" type="button" variant="ghost">
            Cancel
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-2 rounded-md border border-(--ui-stroke-secondary) p-2.5">
      <div className="flex items-center gap-2">
        <Codicon className="shrink-0 text-(--ui-text-tertiary)" name="github" size="0.75rem" />
        <span className="min-w-0 flex-1 truncate text-[0.75rem]">{connection.login}</span>
        <Button
          className="shrink-0 text-[0.625rem] text-(--ui-text-quaternary)"
          disabled={locked}
          onClick={() => void disconnectGitHub()}
          size="xs"
          type="button"
          variant="text"
        >
          Disconnect
        </Button>
      </div>

      {creating ? (
        <>
          <span className="text-[0.6875rem] text-(--ui-text-tertiary)">
            Creates a private repository on your account. Nothing is pushed until you choose to.
          </span>
          <Input
            disabled={locked}
            onChange={event => setNewRepoName(event.target.value)}
            placeholder="repository-name"
            value={newRepoName}
          />
          {newRepoName.trim() && normalizeRepoName(newRepoName) !== newRepoName.trim() && (
            <span className="text-[0.625rem] text-(--ui-text-quaternary)">
              Will be created as <span className="font-medium">{normalizeRepoName(newRepoName)}</span> — GitHub only
              allows letters, numbers, dots, hyphens and underscores.
            </span>
          )}
          <div className="flex items-center gap-2">
            <Button
              disabled={locked || !normalizeRepoName(newRepoName)}
              onClick={() =>
                void createGitHubRepo({ name: newRepoName, private: true }).then(repo => {
                  if (repo) {
                    onPick(repo)
                  }
                })
              }
              size="sm"
              type="button"
            >
              Create and use
            </Button>
            <Button disabled={locked} onClick={() => setCreating(false)} size="sm" type="button" variant="ghost">
              Back
            </Button>
          </div>
        </>
      ) : (
        <>
          <Input
            disabled={locked}
            onChange={event => setFilter(event.target.value)}
            placeholder="Search your repositories…"
            value={filter}
          />
          <ul className="flex max-h-48 flex-col gap-0.5 overflow-y-auto">
            {repos === null ? (
              <li className="px-1 py-1 text-[0.6875rem] text-(--ui-text-quaternary)">Loading…</li>
            ) : visible.length === 0 ? (
              <li className="px-1 py-1 text-[0.6875rem] text-(--ui-text-quaternary)">
                {filter.trim() ? 'No repository matches that.' : 'No repositories found on this account.'}
              </li>
            ) : (
              visible.map(repo => (
                <li key={repo.fullName}>
                  <button
                    className={cn(
                      'flex w-full items-center gap-2 rounded-md px-2 py-1 text-left text-[0.75rem]',
                      'hover:bg-(--ui-control-hover-background) disabled:opacity-50'
                    )}
                    disabled={locked}
                    onClick={() => onPick(repo)}
                    type="button"
                  >
                    <Codicon
                      className="shrink-0 text-(--ui-text-quaternary)"
                      name={repo.private ? 'lock' : 'repo'}
                      size="0.75rem"
                    />
                    <span className="min-w-0 flex-1 truncate" title={repo.fullName}>
                      {repo.fullName}
                    </span>
                  </button>
                </li>
              ))
            )}
          </ul>
          <div className="flex items-center gap-2">
            <Button disabled={locked} onClick={() => setCreating(true)} size="sm" type="button" variant="ghost">
              <Codicon name="add" size="0.75rem" />
              New repository
            </Button>
            <Button disabled={locked} onClick={() => void loadGitHubRepos(true)} size="sm" type="button" variant="ghost">
              Refresh
            </Button>
            <Button className="ml-auto" disabled={locked} onClick={onCancel} size="sm" type="button" variant="ghost">
              Cancel
            </Button>
          </div>
        </>
      )}
    </div>
  )
}

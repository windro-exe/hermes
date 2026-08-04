// GitHub account, in Settings.
//
// The connection is global — one token for the app, not per project — so signing in
// here means project creation never asks again, and signing in from the project
// dialog shows up here. Both surfaces read the same store, which reads the same
// encrypted file in the main process, so there is no state to keep in sync.

import { useStore } from '@nanostores/react'
import { useEffect } from 'react'

import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import {
  $github,
  $githubBusy,
  $githubDeviceFlow,
  cancelGitHubDeviceFlow,
  disconnectGitHub,
  githubAvailable,
  refreshGitHubStatus,
  signInWithGitHub
} from '@/store/github'

export function GitHubSettings() {
  const connection = useStore($github)
  const busy = useStore($githubBusy)
  const pending = useStore($githubDeviceFlow)

  useEffect(() => {
    void refreshGitHubStatus()
  }, [])

  if (!githubAvailable()) {
    return (
      <div className="flex flex-col gap-2 p-4">
        <span className="text-[0.8125rem] font-medium">GitHub</span>
        <span className="text-[0.75rem] text-(--ui-text-tertiary)">
          Not available on this connection. Cloning and signing in happen on the machine running the desktop app, so a
          remote gateway cannot do it for you.
        </span>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-3 p-4">
      <div className="flex flex-col gap-1">
        <span className="text-[0.8125rem] font-medium">GitHub</span>
        <span className="text-[0.75rem] text-(--ui-text-tertiary)">
          Used when a project clones or creates a repository. Signing in once covers every project.
        </span>
      </div>

      {pending ? (
        <div className="flex flex-col gap-2 rounded-md border border-(--ui-stroke-secondary) p-3">
          <span className="text-[0.75rem]">Enter this code in your browser:</span>
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
            <Button className="ml-auto" onClick={() => cancelGitHubDeviceFlow()} size="sm" type="button" variant="ghost">
              Cancel
            </Button>
          </div>
          <span className="text-[0.6875rem] text-(--ui-text-quaternary)">{pending.verificationUri}</span>
        </div>
      ) : connection.connected ? (
        <div className="flex items-center gap-2 rounded-md border border-(--ui-stroke-secondary) p-3">
          <Codicon className="shrink-0 text-(--ui-text-tertiary)" name="github" size="0.875rem" />
          <div className="flex min-w-0 flex-1 flex-col">
            <span className="truncate text-[0.75rem] font-medium">{connection.login}</span>
            <span className="text-[0.6875rem] text-(--ui-text-quaternary)">Signed in</span>
          </div>
          <Button disabled={busy} onClick={() => void disconnectGitHub()} size="sm" type="button" variant="ghost">
            Sign out
          </Button>
        </div>
      ) : (
        <div className="flex flex-col gap-2 rounded-md border border-(--ui-stroke-secondary) p-3">
          {connection.error && (
            <span className="text-[0.6875rem] text-(--ui-text-danger)">
              {connection.error} Sign in again to reconnect.
            </span>
          )}
          <Button className="self-start" disabled={busy} onClick={() => void signInWithGitHub()} size="sm" type="button">
            <Codicon name="github" size="0.75rem" />
            Sign in with GitHub
          </Button>
          <span className="text-[0.6875rem] text-(--ui-text-quaternary)">
            Opens github.com in your browser. The token is stored encrypted by your operating system and never leaves
            this machine.
          </span>
        </div>
      )}

      <span className="text-[0.6875rem] text-(--ui-text-quaternary)">
        Signing out removes the stored token from this machine. It does not revoke it on GitHub — do that under Settings
        → Applications there if you want it gone for good.
      </span>
    </div>
  )
}

// Connect an existing project's repo to a GitHub remote.
//
// Opened from the "This project has no remote" toast. Separate from
// ProjectDialog because it is not part of creating anything — the project already
// exists, this only wires it to GitHub and pushes the current branch.

import { useStore } from '@nanostores/react'

import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { connectRemoteToRepo } from '@/store/github'
import { notify } from '@/store/notifications'
import { $projectRemotePrompt } from '@/store/projects'

import { GitHubRepoPicker } from './github-repo-picker'

/** Trailing path segment, as a sensible default repository name. */
function folderLeaf(folder: string): string {
  const parts = folder.replace(/[/\\]+$/, '').split(/[/\\]/)

  return parts[parts.length - 1] || folder
}

export function ProjectRemoteDialog() {
  const prompt = useStore($projectRemotePrompt)
  const folder = prompt?.folder ?? ''

  const close = () => $projectRemotePrompt.set(null)

  return (
    <Dialog onOpenChange={open => !open && close()} open={Boolean(prompt)}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Connect a GitHub remote</DialogTitle>
          <DialogDescription>
            Pick or create a repository for <span className="font-medium">{folderLeaf(folder)}</span>. Its current
            branch is pushed, and nothing local is rewritten.
          </DialogDescription>
        </DialogHeader>

        {prompt && (
          <GitHubRepoPicker
            defaultRepoName={folderLeaf(folder)}
            onCancel={close}
            onPick={repo => {
              void connectRemoteToRepo(folder, repo.cloneUrl).then(result => {
                if (!result.ok) {
                  return
                }

                close()

                // A connected-but-unpushed remote is a real outcome, not a
                // failure: protected branches and diverged history both land here,
                // and saying "done" would be a lie while saying "failed" would
                // imply the remote was not wired.
                notify({
                  kind: result.pushed ? 'success' : 'warning',
                  message: result.pushed
                    ? `Pushed to ${repo.fullName}.`
                    : `Remote set to ${repo.fullName}, but the push did not complete: ${result.pushError ?? 'unknown reason'}`,
                  title: result.pushed ? 'Connected to GitHub' : 'Remote connected, push pending'
                })
              })
            }}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}

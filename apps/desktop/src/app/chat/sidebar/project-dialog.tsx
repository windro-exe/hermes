import { useStore } from '@nanostores/react'
import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog'
import { GenerateButton } from '@/components/ui/generate-button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Tip } from '@/components/ui/tooltip'
import type { HermesGitHubRepo } from '@/global'
import { useI18n } from '@/i18n'
import { type ProjectIdeaTemplate, randomIdeaTemplates } from '@/lib/project-idea-templates'
import { cn } from '@/lib/utils'
import { cloneGitHubRepo, githubAvailable } from '@/store/github'
import { notifyError } from '@/store/notifications'
import {
  $projectDialog,
  addProjectFolder,
  closeProjectDialog,
  createProject,
  generateProjectIdea,
  pickProjectFolder,
  renameProject
} from '@/store/projects'

import { GitHubRepoPicker } from './projects/github-repo-picker'

// Single dialog mounted once in the sidebar; it renders create / rename /
// add-folder flows driven by the $projectDialog atom. Folders are chosen via
// the native directory picker (reused from the default-project-dir setting).
export function ProjectDialog() {
  const { t } = useI18n()
  const p = t.sidebar.projects
  const state = useStore($projectDialog)
  const open = state !== null
  const mode = state?.mode ?? 'create'

  const [name, setName] = useState('')
  const [folders, setFolders] = useState<string[]>([])
  const [idea, setIdea] = useState('')
  const [templates, setTemplates] = useState<ProjectIdeaTemplate[]>([])
  const [generatingIdea, setGeneratingIdea] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [showGitHub, setShowGitHub] = useState(false)
  const [cloning, setCloning] = useState(false)
  const nameRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (open) {
      setName(state?.name ?? '')
      setFolders([])
      setIdea('')
      setTemplates(randomIdeaTemplates())
      setGeneratingIdea(false)
      setSubmitting(false)

      if (mode !== 'add-folder') {
        window.setTimeout(() => nameRef.current?.select(), 0)
      }
    }
  }, [open, mode, state?.name])

  const onOpenChange = (next: boolean) => {
    if (!next) {
      closeProjectDialog()
    }
  }

  // One submit beat for every flow: guard re-entry, run the write, close on
  // success, surface a toast on failure. Callers pass only the write.
  const runSubmit = async (write: () => Promise<unknown>) => {
    if (submitting) {
      return
    }

    setSubmitting(true)

    try {
      await write()
      closeProjectDialog()
    } catch (err) {
      notifyError(err, p.createFailed)
    } finally {
      setSubmitting(false)
    }
  }

  /**
   * Clone a picked GitHub repo and add the checkout as a project folder.
   *
   * The user chooses a PARENT directory and the repo lands in a subfolder named
   * after it, which is what `git clone` does in a terminal and avoids cloning into
   * a directory that already holds unrelated files.
   *
   * Also fills the project name when it is still empty: having just picked a
   * repository, being asked to name the project again is busywork.
   */
  const cloneAndAdd = async (repo: HermesGitHubRepo) => {
    setCloning(true)

    try {
      const parent = await pickProjectFolder()

      if (!parent) {
        return
      }

      const separator = parent.includes('\\') ? '\\' : '/'
      const target = `${parent.replace(/[/\\]+$/, '')}${separator}${repo.name}`
      const cloned = await cloneGitHubRepo(repo.cloneUrl, target)

      if (!cloned) {
        return
      }

      setFolders(prev => (prev.includes(cloned) ? prev : [...prev, cloned]))
      setName(prev => prev.trim() || repo.name)
      setShowGitHub(false)
    } catch (err) {
      notifyError(err, p.createFailed)
    } finally {
      setCloning(false)
    }
  }

  const pickFolder = async () => {
    try {
      const dir = await pickProjectFolder()

      if (!dir) {
        return
      }

      const projectId = state?.projectId

      if (mode === 'add-folder' && projectId) {
        await runSubmit(() => addProjectFolder(projectId, dir))

        return
      }

      setFolders(prev => (prev.includes(dir) ? prev : [...prev, dir]))
    } catch (err) {
      notifyError(err, p.createFailed)
    }
  }

  const submit = async () => {
    const trimmed = name.trim()
    const projectId = state?.projectId

    if (mode === 'rename' && projectId) {
      if (trimmed) {
        await runSubmit(() => renameProject(projectId, trimmed))
      }

      return
    }

    // A project owns sessions by folder (cwd-prefix), so creation requires at
    // least one — a folder-less project couldn't hold a session anyway.
    if (mode === 'create' && trimmed && folders.length) {
      await runSubmit(() =>
        createProject({
          folders,
          idea: idea.trim() || undefined,
          name: trimmed,
          parent: state?.parentId,
          use: true
        })
      )
    }
  }

  const generateIdea = async () => {
    if (generatingIdea) {
      return
    }

    setGeneratingIdea(true)

    try {
      // Pass the current draft so a rough idea gets sharpened instead of
      // replaced. Empty box still means "invent one".
      const text = await generateProjectIdea(name, idea)

      if (text) {
        setIdea(text)
      }
    } finally {
      setGeneratingIdea(false)
    }
  }

  const parentLabel = state?.parentLabel

  const title =
    mode === 'rename'
      ? p.renameTitle
      : mode === 'add-folder'
        ? p.addFolderTitle
        : parentLabel
          ? p.createSubTitle(parentLabel)
          : p.createTitle

  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent className="max-w-md" onInteractOutside={event => event.preventDefault()}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {mode === 'create' && (
            <DialogDescription>{parentLabel ? p.createSubDesc(parentLabel) : p.createDesc}</DialogDescription>
          )}
        </DialogHeader>

        {mode !== 'add-folder' && (
          <Input
            autoFocus
            disabled={submitting}
            onChange={event => setName(event.target.value)}
            onKeyDown={event => {
              if (event.key === 'Enter') {
                event.preventDefault()
                void submit()
              } else if (event.key === 'Escape') {
                onOpenChange(false)
              }
            }}
            placeholder={p.namePlaceholder}
            ref={nameRef}
            value={name}
          />
        )}

        {mode === 'create' && (
          <div className="flex flex-col gap-1.5">
            <span className="text-[0.6875rem] font-medium text-(--ui-text-tertiary)">{p.foldersLabel}</span>
            {folders.length === 0 ? (
              <span className="text-[0.75rem] text-(--ui-text-quaternary)">{p.noFolders}</span>
            ) : (
              <ul className="flex flex-col gap-1">
                {folders.map((folder, index) => (
                  <li
                    className={cn(
                      'flex items-center gap-2 rounded-md bg-(--ui-control-hover-background) px-2 py-1 text-[0.75rem]'
                    )}
                    key={folder}
                  >
                    <Codicon className="shrink-0 text-(--ui-text-tertiary)" name="folder" size="0.75rem" />
                    <span className="min-w-0 flex-1 truncate" title={folder}>
                      {folder}
                    </span>
                    {index === 0 && (
                      <span className="shrink-0 text-[0.625rem] uppercase text-(--ui-text-quaternary)">
                        {p.primaryBadge}
                      </span>
                    )}
                    <Tip label={p.removeFolder}>
                      <Button
                        aria-label={p.removeFolder}
                        className="size-5 shrink-0 text-(--ui-text-quaternary) hover:text-foreground"
                        onClick={() => setFolders(prev => prev.filter(f => f !== folder))}
                        size="icon-xs"
                        type="button"
                        variant="ghost"
                      >
                        <Codicon name="close" size="0.75rem" />
                      </Button>
                    </Tip>
                  </li>
                ))}
              </ul>
            )}
            <div className="flex items-center gap-1">
              <Button disabled={submitting} onClick={() => void pickFolder()} size="sm" type="button" variant="ghost">
                <Codicon name="add" size="0.75rem" />
                {p.addFolder}
              </Button>
              {/* Only offered when the GitHub bridge exists — absent in
                  remote-gateway mode, where cloning onto this machine is not
                  what the user would get. */}
              {githubAvailable() && !showGitHub && (
                <Button
                  disabled={submitting || cloning}
                  onClick={() => setShowGitHub(true)}
                  size="sm"
                  type="button"
                  variant="ghost"
                >
                  <Codicon name="github" size="0.75rem" />
                  From GitHub
                </Button>
              )}
              {cloning && <span className="text-[0.6875rem] text-(--ui-text-tertiary)">Cloning…</span>}
            </div>
            {showGitHub && (
              <GitHubRepoPicker
                defaultRepoName={name}
                disabled={submitting || cloning}
                onCancel={() => setShowGitHub(false)}
                onPick={repo => void cloneAndAdd(repo)}
              />
            )}
          </div>
        )}

        {mode === 'create' && (
          <div className="flex flex-col gap-1.5">
            <span className="text-[0.6875rem] font-medium text-(--ui-text-tertiary)">{p.ideaLabel}</span>
            <div className="relative">
              <Textarea
                className="min-h-20 pr-8 text-[0.8125rem]"
                disabled={submitting}
                onChange={event => setIdea(event.target.value)}
                placeholder={p.ideaPlaceholder}
                value={idea}
              />
              <GenerateButton
                className="absolute top-1 right-1"
                disabled={submitting}
                generating={generatingIdea}
                generatingLabel={p.ideaGenerating}
                label={p.ideaGenerate}
                onGenerate={() => void generateIdea()}
              />
            </div>
            {/* The GenerateButton's spinner is a 12px icon in the textarea's
                corner — easy to miss, so a request looked like it did nothing.
                This states it plainly while the one-shot call is in flight. */}
            {generatingIdea && (
              <p className="flex items-center gap-1.5 text-[0.6875rem] text-(--ui-text-secondary)">
                <Codicon name="loading" size="0.75rem" spinning />
                <span>{p.ideaGenerating}</span>
              </p>
            )}
            <div className="flex flex-wrap items-center gap-1">
              {templates.map(template => (
                <button
                  className="flex items-center gap-1 rounded-full border border-(--ui-stroke-tertiary) px-2 py-0.5 text-[0.6875rem] text-(--ui-text-secondary) transition-colors hover:border-(--ui-stroke-secondary) hover:bg-(--ui-control-hover-background) hover:text-foreground disabled:opacity-50"
                  disabled={submitting}
                  key={template.label}
                  onClick={() => setIdea(template.idea)}
                  type="button"
                >
                  <span aria-hidden>{template.emoji}</span>
                  {template.label}
                </button>
              ))}
              <Tip label={p.ideaShuffle}>
                <Button
                  aria-label={p.ideaShuffle}
                  className="size-5 text-(--ui-text-quaternary) hover:text-foreground"
                  disabled={submitting}
                  onClick={() => setTemplates(randomIdeaTemplates())}
                  size="icon-xs"
                  type="button"
                  variant="ghost"
                >
                  <Codicon name="refresh" size="0.75rem" />
                </Button>
              </Tip>
            </div>
          </div>
        )}

        {mode === 'add-folder' && (
          <Button disabled={submitting} onClick={() => void pickFolder()} type="button">
            <Codicon name="folder-opened" size="0.875rem" />
            {p.addFolder}
          </Button>
        )}

        {mode !== 'add-folder' && (
          <DialogFooter>
            <Button disabled={submitting} onClick={() => onOpenChange(false)} type="button" variant="ghost">
              {t.common.cancel}
            </Button>
            <Button
              disabled={submitting || !name.trim() || (mode === 'create' && folders.length === 0)}
              onClick={() => void submit()}
              type="button"
            >
              {mode === 'rename' ? t.common.save : p.create}
            </Button>
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  )
}

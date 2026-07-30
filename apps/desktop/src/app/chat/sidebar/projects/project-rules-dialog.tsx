// Project rules + intent, edited entirely in the UI.
//
// One dialog for both files the Python loader reads for a project:
//   .hermes/rules/*.md  — standing instructions, one `- ` bullet per rule
//   IDEA.md             — what the project is for
//
// Editing is a textarea per rule file, one rule per line. That is deliberate
// over a row-per-rule form: it round-trips hand-written files without a
// markdown parser, keyboard editing stays fast, and there is no per-row state to
// desynchronise from disk.
import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useState } from 'react'

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
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Tip } from '@/components/ui/tooltip'
import { readDesktopFileText, writeDesktopFileText } from '@/lib/desktop-fs'
import { cn } from '@/lib/utils'
import { notifyError } from '@/store/notifications'
import {
  $projectRulesDialog,
  closeProjectRules,
  createRuleFile,
  deleteRuleFile,
  joinProjectPath,
  loadProjectRules,
  type RuleFile,
  RULES_DIR,
  saveRuleFile,
  setRuleFileEnabled
} from '@/store/project-rules'

function RuleFileCard({
  file,
  onChanged
}: {
  file: RuleFile
  onChanged: () => Promise<void>
}) {
  const [draft, setDraft] = useState(file.rules.join('\n'))
  const [busy, setBusy] = useState(false)

  // Re-sync when the file is reloaded from disk (toggle, external edit).
  useEffect(() => {
    setDraft(file.rules.join('\n'))
  }, [file.rules])

  const dirty = draft !== file.rules.join('\n')

  const save = async () => {
    setBusy(true)

    try {
      await saveRuleFile({
        frontmatter: file.frontmatter,
        path: file.path,
        rules: draft.split('\n')
      })
      await onChanged()
    } catch (err) {
      notifyError(err, `Could not save ${file.name}`)
    } finally {
      setBusy(false)
    }
  }

  const toggle = async () => {
    setBusy(true)

    try {
      await setRuleFileEnabled(file, !file.enabled)
      await onChanged()
    } catch (err) {
      notifyError(err, `Could not ${file.enabled ? 'disable' : 'enable'} ${file.name}`)
    } finally {
      setBusy(false)
    }
  }

  const remove = async () => {
    setBusy(true)

    try {
      await deleteRuleFile(file.path)
      await onChanged()
    } catch (err) {
      notifyError(err, `Could not delete ${file.name}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="rounded-lg border border-border/60 p-2.5">
      <div className="mb-2 flex items-center gap-2">
        <Tip label={file.enabled ? 'Loaded into every prompt — click to disable' : 'Not loaded — click to enable'}>
          <Button
            aria-label={file.enabled ? `Disable ${file.name}` : `Enable ${file.name}`}
            aria-pressed={file.enabled}
            disabled={busy}
            onClick={toggle}
            size="icon"
            variant="ghost"
          >
            <Codicon name={file.enabled ? 'check' : 'circle-large-outline'} />
          </Button>
        </Tip>

        <span className={cn('flex-1 truncate font-mono text-xs', !file.enabled && 'text-muted-foreground')}>
          {file.name}
        </span>

        {file.pathScoped && (
          <Tip label="This file is scoped to specific paths. Path scoping isn't active yet, so it is not sent to the agent.">
            <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[0.65rem] text-amber-500">path-scoped</span>
          </Tip>
        )}

        {dirty && (
          <Button disabled={busy} onClick={save} size="sm">
            Save
          </Button>
        )}

        <Tip label={`Delete ${file.name}`}>
          <Button aria-label={`Delete ${file.name}`} disabled={busy} onClick={remove} size="icon" variant="ghost">
            <Codicon name="trash" />
          </Button>
        </Tip>
      </div>

      <Textarea
        aria-label={`Rules in ${file.name}`}
        className="min-h-24 font-mono text-xs"
        onChange={event => setDraft(event.target.value)}
        placeholder="One rule per line, e.g.&#10;use pnpm, never npm&#10;tests need the venv activated"
        spellCheck={false}
        value={draft}
      />
    </div>
  )
}

export function ProjectRulesDialog() {
  const target = useStore($projectRulesDialog)
  const [files, setFiles] = useState<RuleFile[]>([])
  const [idea, setIdea] = useState('')
  const [ideaOnDisk, setIdeaOnDisk] = useState('')
  const [newName, setNewName] = useState('')
  const [loading, setLoading] = useState(false)

  const projectPath = target?.projectPath ?? ''
  const ideaPath = projectPath ? joinProjectPath(projectPath, 'IDEA.md') : ''

  const refresh = useCallback(async () => {
    if (!projectPath) {
      return
    }

    setLoading(true)

    try {
      setFiles(await loadProjectRules(projectPath))

      try {
        const { text } = await readDesktopFileText(ideaPath)

        setIdea(text)
        setIdeaOnDisk(text)
      } catch {
        // No IDEA.md yet.
        setIdea('')
        setIdeaOnDisk('')
      }
    } catch (err) {
      notifyError(err, 'Could not read project rules')
    } finally {
      setLoading(false)
    }
  }, [ideaPath, projectPath])

  useEffect(() => {
    if (target) {
      void refresh()
    }
  }, [refresh, target])

  const addFile = async () => {
    if (!projectPath) {
      return
    }

    try {
      await createRuleFile(projectPath, newName || 'rules')
      setNewName('')
      await refresh()
    } catch (err) {
      notifyError(err, 'Could not create the rule file')
    }
  }

  const saveIdea = async () => {
    try {
      await writeDesktopFileText(ideaPath, idea.endsWith('\n') ? idea : `${idea}\n`)
      setIdeaOnDisk(idea)
    } catch (err) {
      notifyError(err, 'Could not save IDEA.md')
    }
  }

  return (
    <Dialog onOpenChange={open => !open && closeProjectRules()} open={Boolean(target)}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Rules — {target?.projectName}</DialogTitle>
          <DialogDescription>
            Standing instructions for this project. Every enabled rule is sent to the agent at the start of each
            session in this folder, so keep them short and specific. Stored in {RULES_DIR}.
          </DialogDescription>
        </DialogHeader>

        <div className="max-h-[55vh] space-y-3 overflow-y-auto pr-1">
          {loading && files.length === 0 ? (
            <p className="text-xs text-muted-foreground">Loading…</p>
          ) : null}

          {!loading && files.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              No rules yet. Add a file below — one is usually enough to start.
            </p>
          ) : null}

          {files.map(file => (
            <RuleFileCard file={file} key={file.path} onChanged={refresh} />
          ))}

          <div className="flex items-center gap-2">
            <Input
              aria-label="New rule file name"
              className="h-8 font-mono text-xs"
              onChange={event => setNewName(event.target.value)}
              onKeyDown={event => {
                if (event.key === 'Enter') {
                  event.preventDefault()
                  void addFile()
                }
              }}
              placeholder="style.md"
              value={newName}
            />
            <Button onClick={addFile} size="sm" variant="secondary">
              <Codicon name="add" />
              Add file
            </Button>
          </div>

          <div className="rounded-lg border border-border/60 p-2.5">
            <div className="mb-2 flex items-center gap-2">
              <span className="flex-1 font-mono text-xs">IDEA.md</span>
              {idea !== ideaOnDisk && (
                <Button onClick={saveIdea} size="sm">
                  Save
                </Button>
              )}
            </div>
            <Textarea
              aria-label="What this project is for"
              className="min-h-20 text-xs"
              onChange={event => setIdea(event.target.value)}
              placeholder="What is this project for? Intent the agent can't work out by reading the code."
              value={idea}
            />
          </div>
        </div>

        <DialogFooter>
          <Button onClick={closeProjectRules} variant="secondary">
            Done
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

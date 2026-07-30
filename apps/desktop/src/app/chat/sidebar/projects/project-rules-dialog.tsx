// Project rules + intent, edited entirely in the UI.
//
// One flat list of SHORT, one-line rules — the shape of an allowlist entry, not
// an essay. Each row is one rule; there is no file browser, because the unit the
// user thinks in is the rule, not the file.
//
// On disk they are `- ` bullets in `.hermes/rules/rules.md`, which is what
// agent/prompt_builder.py loads. The loader still scans the whole directory, so
// extra files written by hand keep working — this list just edits the one file
// it found (or creates the default).
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
import { notifyError } from '@/store/notifications'
import {
  $projectRulesDialog,
  closeProjectRules,
  joinProjectPath,
  loadProjectRules,
  type RuleFile,
  RULES_DIR,
  saveRuleList
} from '@/store/project-rules'

export function ProjectRulesDialog() {
  const target = useStore($projectRulesDialog)
  const [rules, setRules] = useState<string[]>([])
  const [source, setSource] = useState<null | Pick<RuleFile, 'frontmatter' | 'path'>>(null)
  const [draft, setDraft] = useState('')
  const [idea, setIdea] = useState('')
  const [ideaOnDisk, setIdeaOnDisk] = useState('')
  const [busy, setBusy] = useState(false)

  const projectPath = target?.projectPath ?? ''
  const ideaPath = projectPath ? joinProjectPath(projectPath, 'IDEA.md') : ''

  const refresh = useCallback(async () => {
    if (!projectPath) {
      return
    }

    try {
      const files = await loadProjectRules(projectPath)
      // Flatten every rule file into one list. Order follows the loader's own
      // deterministic file order, so what you see matches what the agent gets.
      const flat = files.flatMap(file => file.rules)
      const primary = files[0]

      setRules(flat)
      setSource(primary ? { frontmatter: primary.frontmatter, path: primary.path } : null)
    } catch (err) {
      notifyError(err, 'Could not read project rules')
    }

    try {
      const { text } = await readDesktopFileText(ideaPath)

      setIdea(text)
      setIdeaOnDisk(text)
    } catch {
      setIdea('')
      setIdeaOnDisk('')
    }
  }, [ideaPath, projectPath])

  useEffect(() => {
    if (target) {
      void refresh()
    }
  }, [refresh, target])

  const persist = async (next: string[]) => {
    setBusy(true)

    try {
      await saveRuleList(projectPath, next, source ?? undefined)
      setRules(next)
      await refresh()
    } catch (err) {
      notifyError(err, 'Could not save the rules')
    } finally {
      setBusy(false)
    }
  }

  const addRule = async () => {
    const rule = draft.trim()

    if (!rule) {
      return
    }

    setDraft('')
    await persist([...rules, rule])
  }

  const editRule = async (index: number, text: string) => {
    const next = [...rules]

    next[index] = text
    await persist(next.filter(rule => rule.trim()))
  }

  const removeRule = async (index: number) => {
    await persist(rules.filter((_, i) => i !== index))
  }

  const saveIdea = async () => {
    try {
      await writeDesktopFileText(ideaPath, idea.endsWith('\n') ? idea : `${idea}\n`, { mkdirp: true })
      setIdeaOnDisk(idea)
    } catch (err) {
      notifyError(err, 'Could not save IDEA.md')
    }
  }

  return (
    <Dialog onOpenChange={open => !open && closeProjectRules()} open={Boolean(target)}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Rules — {target?.projectName}</DialogTitle>
          <DialogDescription>
            Short standing instructions for this project, one per line. Every rule is sent to the agent at the start of
            each session in this folder — keep them specific. Stored in {RULES_DIR}.
          </DialogDescription>
        </DialogHeader>

        <div className="max-h-[50vh] space-y-1.5 overflow-y-auto pr-1">
          {rules.length === 0 ? (
            <p className="py-1 text-xs text-muted-foreground">No rules yet. Add one below.</p>
          ) : null}

          {rules.map((rule, index) => (
            <div className="flex items-center gap-1.5" key={`${index}-${rule}`}>
              <Codicon className="shrink-0 text-muted-foreground" name="circle-small-filled" size="0.75rem" />
              <Input
                aria-label={`Rule ${index + 1}`}
                className="h-8 flex-1 text-xs"
                defaultValue={rule}
                disabled={busy}
                onBlur={event => {
                  if (event.target.value.trim() !== rule) {
                    void editRule(index, event.target.value)
                  }
                }}
                onKeyDown={event => {
                  if (event.key === 'Enter') {
                    event.currentTarget.blur()
                  }
                }}
              />
              <Tip label="Remove this rule">
                <Button
                  aria-label={`Remove rule ${index + 1}`}
                  disabled={busy}
                  onClick={() => void removeRule(index)}
                  size="icon"
                  variant="ghost"
                >
                  <Codicon name="close" size="0.8rem" />
                </Button>
              </Tip>
            </div>
          ))}

          <div className="flex items-center gap-1.5 pt-1">
            <Codicon className="shrink-0 text-muted-foreground" name="add" size="0.75rem" />
            <Input
              aria-label="New rule"
              className="h-8 flex-1 text-xs"
              disabled={busy}
              onChange={event => setDraft(event.target.value)}
              onKeyDown={event => {
                if (event.key === 'Enter') {
                  event.preventDefault()
                  void addRule()
                }
              }}
              placeholder="e.g. use pnpm, never npm"
              value={draft}
            />
            <Button disabled={busy || !draft.trim()} onClick={addRule} size="sm" variant="secondary">
              Add
            </Button>
          </div>
        </div>

        <div className="border-t border-border/60 pt-2.5">
          <div className="mb-1.5 flex items-center gap-2">
            <span className="flex-1 text-xs text-muted-foreground">
              What this project is for <span className="font-mono">(IDEA.md)</span>
            </span>
            {idea !== ideaOnDisk && (
              <Button onClick={saveIdea} size="sm">
                Save
              </Button>
            )}
          </div>
          <Textarea
            aria-label="What this project is for"
            className="min-h-16 text-xs"
            onChange={event => setIdea(event.target.value)}
            placeholder="Intent the agent can't work out by reading the code."
            value={idea}
          />
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

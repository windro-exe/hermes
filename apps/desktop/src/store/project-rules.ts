// Per-project rules, edited entirely from the UI.
//
// Storage is `.hermes/rules/*.md` in the project folder — the same files
// agent/prompt_builder.py loads into the system prompt. The file format is
// deliberately trivial (one `- ` bullet per rule) so that a file edited by hand
// round-trips through this form without a markdown parser, and so the loader's
// reader and this writer cannot disagree.
//
// Everything routes through src/lib/desktop-fs, not raw IPC, because those
// helpers transparently fall back to the gateway's REST filesystem when the
// desktop is driving a REMOTE gateway. Using ipcRenderer directly would work
// locally and silently break for remote projects.
import { atom } from 'nanostores'

import { readDesktopDir, readDesktopFileText, trashDesktopPath, writeDesktopFileText } from '@/lib/desktop-fs'
import { notifyError } from '@/store/notifications'

/** Directory the Python loader scans, relative to the project folder. */
export const RULES_DIR = '.hermes/rules'

/**
 * Frontmatter written to disable a rule file.
 *
 * The loader treats any explicit non-always mode as "not active", so a disabled
 * file needs no separate state anywhere — the toggle is the file. That also
 * means a rule disabled here stays disabled for the CLI and TUI, not just the
 * desktop.
 */
const DISABLED_FRONTMATTER = '---\nmode: manual\n---\n'

export interface RuleFile {
  /** File name, e.g. `style.md`. */
  name: string
  /** Absolute path on the project's machine. */
  path: string
  /** One entry per `- ` bullet, with the marker stripped. */
  rules: string[]
  /** False when frontmatter opts the file out of always-on loading. */
  enabled: boolean
  /**
   * True when the file is scoped to paths/globs. Those are parsed by the loader
   * but not yet honoured, so the UI has to say so rather than imply they work.
   */
  pathScoped: boolean
  /** Raw frontmatter block, preserved verbatim across edits. */
  frontmatter: string
}

export interface ProjectRulesTarget {
  projectName: string
  projectPath: string
}

export const $projectRulesDialog = atom<null | ProjectRulesTarget>(null)

export function openProjectRules(project: { label: string; path: null | string }): void {
  if (!project.path) {
    notifyError(
      new Error('This project has no folder on disk, so there is nowhere to keep rules.'),
      'No project folder'
    )

    return
  }

  $projectRulesDialog.set({ projectName: project.label, projectPath: project.path })
}

export function closeProjectRules(): void {
  $projectRulesDialog.set(null)
}

/** Join without assuming a separator — project paths may be Windows or POSIX. */
export function joinProjectPath(base: string, ...parts: string[]): string {
  const sep = base.includes('\\') && !base.includes('/') ? '\\' : '/'
  const trimmed = base.replace(/[/\\]+$/, '')

  return [trimmed, ...parts].join(sep)
}

/** Split a rule file into its frontmatter block and body. */
function splitFrontmatter(text: string): { body: string; frontmatter: string } {
  const clean = text.replace(/^\ufeff/, '')

  if (!clean.startsWith('---')) {
    return { body: clean, frontmatter: '' }
  }

  const end = clean.indexOf('\n---', 3)

  if (end === -1) {
    return { body: clean, frontmatter: '' }
  }

  // Normalised to always end in a newline. Without it, serializeRules would
  // concatenate the closing fence with the first bullet and emit `---- rule`,
  // which the loader then reads as body text rather than frontmatter.
  return {
    body: clean.slice(end + 4).replace(/^\n+/, ''),
    frontmatter: `${clean.slice(0, end + 4).replace(/\n+$/, '')}\n`
  }
}

/**
 * Mirrors `_rule_is_always_on` in agent/prompt_builder.py. Kept in sync by the
 * fork guard tests — if these two disagree, the UI shows a rule as active that
 * the agent never sees, which is the exact failure this feature exists to avoid.
 */
function readFrontmatterFlags(frontmatter: string): { enabled: boolean; pathScoped: boolean } {
  if (!frontmatter) {
    return { enabled: true, pathScoped: false }
  }

  const lower = frontmatter.toLowerCase()
  const modeMatch = lower.match(/^\s*(?:mode|trigger)\s*:\s*(.+)$/m)
  const alwaysApply = lower.match(/^\s*alwaysapply\s*:\s*(true|false)\s*$/m)
  const hasPaths = /^\s*(?:paths|globs|applyto)\s*:\s*\S/m.test(lower)

  if (alwaysApply?.[1] === 'true') {
    return { enabled: true, pathScoped: hasPaths }
  }

  if (modeMatch) {
    const mode = modeMatch[1].trim().replace(/["']/g, '').replace(/\s/g, '_')
    const always = ['always', 'always_on', 'alwayson', 'always-apply', 'alwaysapply'].includes(mode)

    return { enabled: always, pathScoped: hasPaths || !always }
  }

  if (alwaysApply?.[1] === 'false') {
    return { enabled: false, pathScoped: hasPaths }
  }

  return { enabled: !hasPaths, pathScoped: hasPaths }
}

/** Bullet lines -> rule strings. Non-bullet prose is preserved as its own rule
 *  so hand-written files never silently lose content on save. */
function parseRules(body: string): string[] {
  return body
    .split('\n')
    .map(line => line.trim())
    .filter(Boolean)
    .map(line => line.replace(/^[-*]\s+/, ''))
}

function serializeRules(frontmatter: string, rules: string[]): string {
  const body = rules
    .map(rule => rule.trim())
    .filter(Boolean)
    .map(rule => `- ${rule}`)
    .join('\n')

  // Belt and braces with splitFrontmatter's normalisation: a caller passing
  // hand-built frontmatter must not be able to glue the fence to the body.
  const header = frontmatter && !frontmatter.endsWith('\n') ? `${frontmatter}\n` : frontmatter

  return `${header}${body}\n`
}

export async function loadProjectRules(projectPath: string): Promise<RuleFile[]> {
  const dir = joinProjectPath(projectPath, ...RULES_DIR.split('/'))

  let entries: Awaited<ReturnType<typeof readDesktopDir>>

  try {
    entries = await readDesktopDir(dir)
  } catch {
    // No .hermes/rules yet — an empty list, not an error.
    return []
  }

  const files = (entries.entries ?? [])
    .filter(entry => !entry.isDirectory && entry.name.toLowerCase().endsWith('.md'))
    .sort((left, right) => left.name.toLowerCase().localeCompare(right.name.toLowerCase()))

  const loaded: RuleFile[] = []

  for (const entry of files) {
    try {
      const { text } = await readDesktopFileText(entry.path)
      const { body, frontmatter } = splitFrontmatter(text)
      const flags = readFrontmatterFlags(frontmatter)

      loaded.push({
        enabled: flags.enabled,
        frontmatter,
        name: entry.name,
        path: entry.path,
        pathScoped: flags.pathScoped,
        rules: parseRules(body)
      })
    } catch {
      // Skip unreadable files rather than failing the whole list.
    }
  }

  return loaded
}

export async function saveRuleFile(file: Pick<RuleFile, 'frontmatter' | 'path' | 'rules'>): Promise<void> {
  await writeDesktopFileText(file.path, serializeRules(file.frontmatter, file.rules))
}

export async function createRuleFile(projectPath: string, name: string): Promise<string> {
  const safe = name.trim().replace(/[^a-zA-Z0-9._-]/g, '-') || 'rules'
  const fileName = safe.toLowerCase().endsWith('.md') ? safe : `${safe}.md`
  const path = joinProjectPath(projectPath, ...RULES_DIR.split('/'), fileName)

  await writeDesktopFileText(path, '')

  return path
}

export async function deleteRuleFile(path: string): Promise<void> {
  await trashDesktopPath(path)
}

/**
 * Enable/disable a whole rule file by rewriting its frontmatter.
 *
 * Disabling writes `mode: manual`, which the loader treats as not-always-on, so
 * the file stops reaching the agent without being deleted. Enabling strips the
 * frontmatter entirely — a file with no header is always-on, matching Cline and
 * Claude Code (and deliberately not Copilot, whose inverted default is a
 * documented source of "why isn't my rule working").
 */
export async function setRuleFileEnabled(file: RuleFile, enabled: boolean): Promise<void> {
  const frontmatter = enabled ? '' : DISABLED_FRONTMATTER

  await writeDesktopFileText(file.path, serializeRules(frontmatter, file.rules))
}

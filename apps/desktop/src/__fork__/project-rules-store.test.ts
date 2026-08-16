/**
 * Guards for the project-rules UI store (windro's fork).
 *
 * The critical property under test is agreement with the Python loader. This
 * store decides what the UI shows as "enabled"; agent/prompt_builder.py decides
 * what the agent actually receives. If they disagree, the UI cheerfully reports
 * a rule as active that the agent never sees — the exact failure this whole
 * feature exists to prevent, and the most-reported bug in every tool that ships
 * rules files.
 *
 * Fork-owned directory. Upstream has no src/__fork__/, so this cannot conflict.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const readDesktopDir = vi.fn()
const readDesktopFileText = vi.fn()

const writeDesktopFileText = vi.fn<(path: string, content: string) => Promise<{ path: string }>>(async path => ({
  path
}))

const trashDesktopPath = vi.fn(async () => undefined)

vi.mock('@/lib/desktop-fs', () => ({
  readDesktopDir,
  readDesktopFileText,
  trashDesktopPath,
  writeDesktopFileText
}))

vi.mock('@/store/notifications', () => ({ notifyError: vi.fn() }))

const {
  $projectRulesDialog,
  closeProjectRules,
  joinProjectPath,
  loadProjectRules,
  openProjectRules,
  saveRuleFile,
  setRuleFileEnabled
} = await import('@/store/project-rules')

function dirWith(names: string[]) {
  readDesktopDir.mockResolvedValue({
    entries: names.map(name => ({ isDirectory: false, name, path: `/p/.hermes/rules/${name}` }))
  })
}

function fileContents(map: Record<string, string>) {
  readDesktopFileText.mockImplementation(async (path: string) => {
    const name = path.split('/').pop() ?? ''

    if (!(name in map)) {
      throw new Error('ENOENT')
    }

    return { byteSize: map[name].length, path, text: map[name] }
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  closeProjectRules()
})

describe('joinProjectPath', () => {
  it('keeps Windows separators for Windows paths', () => {
    expect(joinProjectPath('C:\\code\\app', '.hermes', 'rules')).toBe('C:\\code\\app\\.hermes\\rules')
  })

  it('uses forward slashes for POSIX paths', () => {
    expect(joinProjectPath('/home/w/app', 'IDEA.md')).toBe('/home/w/app/IDEA.md')
  })

  it('does not double a trailing separator', () => {
    expect(joinProjectPath('/home/w/app/', 'IDEA.md')).toBe('/home/w/app/IDEA.md')
  })
})

describe('opening the dialog', () => {
  it('refuses a project with no folder', () => {
    openProjectRules({ label: 'No folder', path: null })

    expect($projectRulesDialog.get()).toBeNull()
  })

  it('opens for a project with a folder', () => {
    openProjectRules({ label: 'App', path: '/p' })

    expect($projectRulesDialog.get()).toEqual({ projectName: 'App', projectPath: '/p' })
  })
})

describe('loadProjectRules matches the Python loader', () => {
  it('treats no frontmatter as enabled', async () => {
    dirWith(['style.md'])
    fileContents({ 'style.md': '- use pnpm\n- no comments\n' })

    const [file] = await loadProjectRules('/p')

    expect(file.enabled).toBe(true)
    expect(file.pathScoped).toBe(false)
    expect(file.rules).toEqual(['use pnpm', 'no comments'])
  })

  it('treats a description-only header as enabled', async () => {
    dirWith(['a.md'])
    fileContents({ 'a.md': '---\ndescription: gotchas\n---\n- venv needed\n' })

    const [file] = await loadProjectRules('/p')

    expect(file.enabled).toBe(true)
  })

  it('treats an explicit non-always mode as disabled', async () => {
    dirWith(['a.md'])
    fileContents({ 'a.md': '---\nmode: manual\n---\n- off\n' })

    const [file] = await loadProjectRules('/p')

    expect(file.enabled).toBe(false)
  })

  it('flags path-scoped files as not active', async () => {
    dirWith(['a.md'])
    fileContents({ 'a.md': '---\npaths: ["src/**"]\n---\n- scoped\n' })

    const [file] = await loadProjectRules('/p')

    expect(file.enabled).toBe(false)
    expect(file.pathScoped).toBe(true)
  })

  it('honours alwaysApply true over path scoping, like Cursor', async () => {
    dirWith(['a.md'])
    fileContents({ 'a.md': '---\nalwaysApply: true\nglobs: "src/**"\n---\n- forced\n' })

    const [file] = await loadProjectRules('/p')

    expect(file.enabled).toBe(true)
  })

  it('strips bullet markers and blank lines', async () => {
    dirWith(['a.md'])
    fileContents({ 'a.md': '- one\n\n* two\n   \n- three\n' })

    const [file] = await loadProjectRules('/p')

    expect(file.rules).toEqual(['one', 'two', 'three'])
  })

  it('preserves frontmatter verbatim for round-tripping', async () => {
    dirWith(['a.md'])
    fileContents({ 'a.md': '---\ndescription: keep me\n---\n- x\n' })

    const [file] = await loadProjectRules('/p')

    expect(file.frontmatter).toBe('---\ndescription: keep me\n---\n')
  })

  it('never glues the closing fence to the first rule on save', async () => {
    // Regression: splitFrontmatter used to return the block without a trailing
    // newline, so saving a file that already had frontmatter emitted
    // `---- rule`, which the loader reads as body text, not frontmatter.
    dirWith(['a.md'])
    fileContents({ 'a.md': '---\ndescription: keep me\n---\n- one\n' })

    const [file] = await loadProjectRules('/p')

    await saveRuleFile({ frontmatter: file.frontmatter, path: file.path, rules: file.rules })

    const written = writeDesktopFileText.mock.calls.at(-1)?.[1] ?? ''

    expect(written).not.toContain('----')
    expect(written).toBe('---\ndescription: keep me\n---\n- one\n')
  })

  it('sorts files deterministically', async () => {
    dirWith(['zebra.md', 'alpha.md'])
    fileContents({ 'alpha.md': '- a\n', 'zebra.md': '- z\n' })

    const files = await loadProjectRules('/p')

    expect(files.map(f => f.name)).toEqual(['alpha.md', 'zebra.md'])
  })

  it('ignores non-markdown files and directories', async () => {
    readDesktopDir.mockResolvedValue({
      entries: [
        { isDirectory: false, name: 'notes.txt', path: '/p/.hermes/rules/notes.txt' },
        { isDirectory: true, name: 'nested', path: '/p/.hermes/rules/nested' },
        { isDirectory: false, name: 'ok.md', path: '/p/.hermes/rules/ok.md' }
      ]
    })
    fileContents({ 'ok.md': '- kept\n' })

    const files = await loadProjectRules('/p')

    expect(files.map(f => f.name)).toEqual(['ok.md'])
  })

  it('returns an empty list when the directory does not exist', async () => {
    readDesktopDir.mockRejectedValue(new Error('ENOENT'))

    expect(await loadProjectRules('/p')).toEqual([])
  })

  it('skips an unreadable file instead of failing the whole list', async () => {
    dirWith(['bad.md', 'good.md'])
    readDesktopFileText.mockImplementation(async (path: string) => {
      if (path.endsWith('bad.md')) {
        throw new Error('EACCES')
      }

      return { byteSize: 1, path, text: '- fine\n' }
    })

    const files = await loadProjectRules('/p')

    expect(files.map(f => f.name)).toEqual(['good.md'])
  })
})

describe('the enable toggle is the file', () => {
  const file = {
    enabled: true,
    frontmatter: '',
    name: 'a.md',
    path: '/p/.hermes/rules/a.md',
    pathScoped: false,
    rules: ['keep this rule']
  }

  it('disabling writes a non-always mode the loader will skip', async () => {
    await setRuleFileEnabled(file, false)

    const [, content] = writeDesktopFileText.mock.calls[0]

    expect(content).toContain('mode: manual')
    expect(content, 'the rule body must survive being disabled').toContain('- keep this rule')
  })

  it('enabling strips the frontmatter entirely', async () => {
    await setRuleFileEnabled({ ...file, enabled: false, frontmatter: '---\nmode: manual\n---\n' }, true)

    const [, content] = writeDesktopFileText.mock.calls[0]

    expect(content).not.toContain('mode:')
    expect(content).toBe('- keep this rule\n')
  })

  it('round-trips: disable then enable returns the original body', async () => {
    await setRuleFileEnabled(file, false)

    const disabled = writeDesktopFileText.mock.calls[0][1] ?? ''

    expect(disabled).toBe('---\nmode: manual\n---\n- keep this rule\n')

    writeDesktopFileText.mockClear()
    await setRuleFileEnabled({ ...file, enabled: false, frontmatter: '---\nmode: manual\n---\n' }, true)

    expect(writeDesktopFileText.mock.calls[0][1]).toBe('- keep this rule\n')
  })
})

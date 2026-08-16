/**
 * Guards for the project-rules inspector UI (windro's fork).
 *
 * Two surfaces, both of which failed him in a specific way:
 *
 *   1. The context item in the status bar was `hidden: !contextUsage`, and that
 *      label is empty until a session reports token usage. So the one panel that
 *      shows what went into the prompt was invisible until AFTER you had already
 *      sent a prompt — exactly backwards for checking whether a rule is live.
 *
 *   2. The Rules row showed a token count and nothing else. A count answers
 *      "how much", never "did my rule land", and the second is the only question
 *      that comes up when a rule seems ignored.
 *
 * Fork-owned directory; upstream has no src/__fork__/.
 */

import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { ContextUsagePanel } from '@/app/shell/context-usage-panel'
import type { ContextBreakdown, ProjectRulesDetail, UsageStats } from '@/types/hermes'

const EMPTY_USAGE = {
  cache_read_tokens: 0,
  cache_write_tokens: 0,
  calls: 0,
  input: 0,
  output: 0,
  reasoning_tokens: 0,
  total: 0
} as unknown as UsageStats

function breakdown(rulesDetail?: Partial<ProjectRulesDetail>): ContextBreakdown {
  return {
    categories: [
      { color: '#888', id: 'rules', label: 'Rules', tokens: 1200 },
      { color: '#777', id: 'conversation', label: 'Conversation', tokens: 5000 }
    ],
    context_max: 200_000,
    context_percent: 3,
    context_used: 6200,
    estimated_total: 6200,
    ...(rulesDetail
      ? {
          rules_detail: {
            cwd: 'C:\\Users\\wnxdd\\Documents\\test',
            dir: 'C:\\Users\\wnxdd\\Documents\\test\\.hermes\\rules',
            idea: false,
            rules: [],
            stale: false,
            ...rulesDetail
          }
        }
      : {})
  }
}

function mount(bd: ContextBreakdown) {
  // Matches the panel's generic `requestGateway` signature rather than the
  // narrower shape vi.fn infers from the return value.
  const requestGateway = vi.fn(async () => bd) as unknown as <T = unknown>(
    method: string,
    params?: Record<string, unknown>
  ) => Promise<T>

  return {
    requestGateway,
    ...render(<ContextUsagePanel currentUsage={EMPTY_USAGE} requestGateway={requestGateway} sessionId="s1" />)
  }
}

describe('the Rules row', () => {
  it('offers a way to see the rules, not just their token cost', async () => {
    mount(breakdown({ rules: [{ state: 'live', text: 'you are ochumaa' }] }))

    expect(await screen.findByText('Rules')).toBeTruthy()
    expect(screen.getByRole('button', { name: /show/i })).toBeTruthy()
  })

  it('lists the rules when expanded', async () => {
    mount(
      breakdown({
        rules: [
          { state: 'live', text: 'you are ochumaa' },
          { state: 'live', text: 'always answer in lowercase' }
        ]
      })
    )

    fireEvent.click(await screen.findByRole('button', { name: /show/i }))

    expect(screen.getByText('you are ochumaa')).toBeTruthy()
    expect(screen.getByText('always answer in lowercase')).toBeTruthy()
  })

  it('distinguishes a switched-off rule from a path-scoped one', async () => {
    // Different causes, different fixes: one is a toggle, the other is a feature
    // that does not exist yet. Collapsing them sends the user hunting for a
    // switch that would not help.
    mount(
      breakdown({
        rules: [
          { state: 'off', text: 'disabled rule' },
          { state: 'scoped', text: 'scoped rule' }
        ]
      })
    )

    fireEvent.click(await screen.findByRole('button', { name: /show/i }))

    expect(screen.getByText(/^off$/i)).toBeTruthy()
    expect(screen.getByText(/path-scoped/i)).toBeTruthy()
  })

  it('warns when the running prompt is older than the rules on disk', async () => {
    mount(breakdown({ rules: [{ state: 'live', text: 'a rule' }], stale: true }))

    // The badge is the answer to "I saved a rule and nothing changed".
    expect(await screen.findByText(/changed/i)).toBeTruthy()
  })

  it('does not warn when the prompt is current', async () => {
    mount(breakdown({ rules: [{ state: 'live', text: 'a rule' }], stale: false }))

    await screen.findByText('Rules')

    expect(screen.queryByText(/changed/i)).toBeNull()
  })

  it('says so when the project has no rules directory', async () => {
    mount(breakdown({ dir: null, rules: [] }))

    fireEvent.click(await screen.findByRole('button', { name: /show/i }))

    expect(screen.getByText(/no project rules/i)).toBeTruthy()
  })

  it('reports that IDEA.md was loaded', async () => {
    mount(breakdown({ idea: true, rules: [{ state: 'live', text: 'a rule' }] }))

    fireEvent.click(await screen.findByRole('button', { name: /show/i }))

    expect(screen.getByText(/IDEA\.md loaded/i)).toBeTruthy()
  })

  it('degrades to the plain row when the backend sends no detail', async () => {
    // An older gateway, or a surface that does not supply it.
    mount(breakdown())

    expect(await screen.findByText('Rules')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /show/i })).toBeNull()
  })
})

import { useEffect, useMemo, useRef, useState } from 'react'

import { useI18n } from '@/i18n'
import { compactNumber } from '@/lib/format'
import { cn } from '@/lib/utils'
import type {
  ContextBreakdown,
  ContextUsageCategory,
  ProjectRuleDetail,
  ProjectRulesDetail,
  UsageStats
} from '@/types/hermes'

interface ContextUsagePanelProps {
  currentUsage: UsageStats
  onUsageSnapshot?: (usage: Pick<UsageStats, 'context_max' | 'context_percent' | 'context_used'>) => void
  requestGateway: <T = unknown>(method: string, params?: Record<string, unknown>) => Promise<T>
  sessionId: string | null
}

/**
 * English fallbacks for the rules inspector.
 *
 * The strings are optional in `Translations` so this fork did not have to
 * translate them into five other locales to ship. Anything missing falls back
 * here rather than rendering `undefined`.
 */
const RULES_COPY_FALLBACK = {
  hide: 'hide',
  ideaLoaded: 'IDEA.md loaded',
  none: 'No project rules in this folder',
  show: 'show',
  staleBadge: 'changed',
  staleHint: 'Rules changed since this session started — your next message picks them up.',
  stateLive: 'live',
  stateOff: 'off',
  stateScoped: 'path-scoped, not active yet'
}

export function ContextUsagePanel({
  currentUsage,
  onUsageSnapshot,
  requestGateway,
  sessionId
}: ContextUsagePanelProps) {
  const { t } = useI18n()
  const copy = t.shell.statusbar.contextUsagePanel
  const [breakdown, setBreakdown] = useState<ContextBreakdown | null>(null)
  const [loading, setLoading] = useState(false)
  const onUsageSnapshotRef = useRef(onUsageSnapshot)
  onUsageSnapshotRef.current = onUsageSnapshot

  useEffect(() => {
    if (!sessionId) {
      setBreakdown(null)
      setLoading(false)

      return
    }

    let cancelled = false
    setLoading(true)

    void requestGateway<ContextBreakdown>('session.context_breakdown', { session_id: sessionId })
      .then(data => {
        if (!cancelled) {
          setBreakdown(data)
          onUsageSnapshotRef.current?.({
            context_max: data.context_max,
            context_percent: data.context_percent,
            context_used: data.context_used
          })
        }
      })
      .catch(() => {
        if (!cancelled) {
          setBreakdown(null)
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [requestGateway, sessionId])

  const contextMax = breakdown?.context_max ?? currentUsage.context_max ?? 0
  const contextUsed = breakdown?.context_used ?? currentUsage.context_used ?? 0

  const contextPercent = Math.max(
    0,
    Math.min(100, Math.round(breakdown?.context_percent ?? currentUsage.context_percent ?? 0))
  )

  const categories = useMemo(
    () =>
      (breakdown?.categories ?? []).map(category => ({
        ...category,
        label: copy.categories[category.id as keyof typeof copy.categories] ?? category.label
      })),
    [breakdown?.categories, copy]
  )

  const segmentTotal = categories.reduce((sum, category) => sum + category.tokens, 0) || contextUsed || 1

  const rulesDetail = breakdown?.rules_detail
  const rulesCopy = { ...RULES_COPY_FALLBACK, ...(copy.rules ?? {}) }
  const [rulesOpen, setRulesOpen] = useState(false)

  return (
    <div className="flex w-72 flex-col gap-3 p-3 text-[0.75rem]" data-slot="context-usage-panel">
      <div className="flex items-baseline justify-between gap-2">
        <p className="font-medium text-foreground">{copy.title}</p>

        <span className="text-[0.6875rem] text-muted-foreground">
          {copy.tokenSummary(`~${compactNumber(contextUsed)}`, compactNumber(contextMax))}
        </span>
      </div>

      <p className="text-[0.6875rem] text-foreground">{copy.percentFull(contextPercent)}</p>

      <ContextUsageBar categories={categories} segmentTotal={segmentTotal} />

      <ul className="flex flex-col gap-1.5">
        {categories.map(category => (
          <li className="flex flex-col gap-1" key={category.id}>
            <div className="flex items-center justify-between gap-2">
              <span className="flex min-w-0 items-center gap-2">
                <span className="size-2 shrink-0 rounded-[2px]" style={{ background: category.color }} />

                <span className="truncate text-muted-foreground">{category.label}</span>

                {/* A token count answers "how much", never "did my rule land".
                    Those are different questions, and only the second one comes
                    up when a rule appears to be ignored. */}
                {category.id === 'rules' && rulesDetail && (
                  <button
                    aria-expanded={rulesOpen}
                    className="shrink-0 text-[0.625rem] text-muted-foreground underline decoration-dotted hover:text-foreground"
                    onClick={() => setRulesOpen(open => !open)}
                    type="button"
                  >
                    {rulesOpen ? rulesCopy.hide : rulesCopy.show}
                  </button>
                )}

                {category.id === 'rules' && rulesDetail?.stale && (
                  <span
                    className="shrink-0 rounded bg-amber-500/15 px-1 text-[0.625rem] text-amber-500"
                    title={rulesCopy.staleHint}
                  >
                    {rulesCopy.staleBadge}
                  </span>
                )}
              </span>

              <span className="shrink-0 tabular-nums text-foreground">{compactNumber(category.tokens)}</span>
            </div>

            {category.id === 'rules' && rulesOpen && rulesDetail && (
              <ProjectRulesDetailView copy={rulesCopy} detail={rulesDetail} />
            )}
          </li>
        ))}
      </ul>

      {loading && <p className="text-[0.6875rem] text-muted-foreground">{copy.loading}</p>}

      {!loading && !categories.length && <p className="text-[0.6875rem] text-muted-foreground">{copy.empty}</p>}
    </div>
  )
}

/** The rules the agent is (or isn't) acting on, and why. */
function ProjectRulesDetailView({ copy, detail }: { copy: typeof RULES_COPY_FALLBACK; detail: ProjectRulesDetail }) {
  const stateLabel: Record<ProjectRuleDetail['state'], string> = {
    live: copy.stateLive,
    off: copy.stateOff,
    scoped: copy.stateScoped
  }

  return (
    <div className="ml-4 flex flex-col gap-1 border-l border-border/50 pl-2">
      {detail.stale && <p className="text-[0.625rem] text-amber-500">{copy.staleHint}</p>}

      {!detail.dir && <p className="text-[0.625rem] text-muted-foreground">{copy.none}</p>}

      {detail.rules.map((rule, index) => (
        <div className="flex items-start justify-between gap-2" key={`${rule.state}-${index}-${rule.text}`}>
          <span
            className={cn(
              'min-w-0 flex-1 break-words text-[0.625rem]',
              rule.state === 'live' ? 'text-foreground' : 'text-muted-foreground line-through'
            )}
          >
            {rule.text}
          </span>

          {rule.state !== 'live' && (
            <span className="shrink-0 text-[0.5625rem] text-muted-foreground">{stateLabel[rule.state]}</span>
          )}
        </div>
      ))}

      {detail.idea && <p className="text-[0.625rem] text-muted-foreground">{copy.ideaLoaded}</p>}
    </div>
  )
}

function ContextUsageBar({
  categories,
  segmentTotal
}: {
  categories: readonly ContextUsageCategory[]
  segmentTotal: number
}) {
  return (
    <div
      className={cn(
        'flex h-1.5 overflow-hidden rounded-full',
        categories.length ? 'bg-(--ui-stroke-tertiary)' : 'dither bg-(--ui-bg-elevated)'
      )}
      data-slot="context-usage-bar"
    >
      {categories.map(category => (
        <span
          className="h-full min-w-px"
          key={category.id}
          style={{
            background: category.color,
            width: `${(category.tokens / segmentTotal) * 100}%`
          }}
        />
      ))}
    </div>
  )
}

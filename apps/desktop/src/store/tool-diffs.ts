import { atom, computed, type ReadableAtom } from 'nanostores'

import { BoundedMap, boundRecord } from '@/lib/bounded-map'

// Diff TEXT is the memory-relevant one here: this record held every diff the
// session had ever rendered. Bounded to the most recent tool calls — anything
// older has scrolled far out of view, and a row that somehow outlives its entry
// re-renders with an empty diff rather than stale text.
const MAX_TOOL_DIFFS = 200

const $toolDiffs = atom<Record<string, string>>({})

// Per-tool derived atoms, cached by toolCallId. A `ToolEntry` subscribes only
// to its own id's diff, so recording a diff for one tool re-renders that one
// row -- not every mounted tool row. computed() only notifies when the derived
// string actually changes, so unrelated writes to the map are inert here.
const inlineDiffCache = new BoundedMap<string, ReadableAtom<string>>(MAX_TOOL_DIFFS)

export function recordToolDiff(toolCallId: string, diff: string) {
  if (!toolCallId || !diff) {
    return
  }

  const current = $toolDiffs.get()

  if (current[toolCallId] === diff) {
    return
  }

  $toolDiffs.set(boundRecord({ ...current, [toolCallId]: diff }, MAX_TOOL_DIFFS))
}

export function getToolDiff(toolCallId: string): string {
  return toolCallId ? $toolDiffs.get()[toolCallId] || '' : ''
}

export function $toolInlineDiff(toolCallId: string): ReadableAtom<string> {
  let cached = inlineDiffCache.get(toolCallId)

  if (!cached) {
    cached = computed($toolDiffs, diffs => (toolCallId ? diffs[toolCallId] || '' : ''))
    inlineDiffCache.set(toolCallId, cached)
  }

  return cached
}

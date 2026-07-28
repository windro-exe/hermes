const EMPTY_ATTACHMENT_REFS: string[] = []

export function partText(part: unknown): string {
  if (typeof part === 'string') {
    return part
  }

  if (!part || typeof part !== 'object') {
    return ''
  }

  const row = part as { text?: unknown; type?: unknown }

  return (!row.type || row.type === 'text') && typeof row.text === 'string' ? row.text : ''
}

// Memo for messageContentText's array path. This runs inside useAuiState
// selectors, which re-execute on every store notification — so for a settled
// message the whole transcript re-concatenated its text on notifications that
// had nothing to do with it. Streaming is already guarded at the call site
// (AssistantMessage returns '' while status is 'running'), so the win here is on
// settled messages in a long transcript.
//
// Keyed on the array reference, which is stable for a settled message. The
// stored length pair is a cheap guard against in-place growth: if a part is
// appended, or the last part's text grows, the key no longer matches and we
// recompute. WeakMap so nothing is retained after a message is dropped.
const contentTextMemo = new WeakMap<object, { lastLength: number; parts: number; text: string }>()

function lastPartLength(content: readonly unknown[]): number {
  return content.length === 0 ? 0 : partText(content[content.length - 1]).length
}

export function messageContentText(content: unknown): string {
  if (typeof content === 'string') {
    return content.trim()
  }

  if (!Array.isArray(content)) {
    return ''
  }

  const cached = contentTextMemo.get(content)
  const parts = content.length
  const lastLength = lastPartLength(content)

  if (cached && cached.parts === parts && cached.lastLength === lastLength) {
    return cached.text
  }

  const text = content.map(partText).join('').trim()

  contentTextMemo.set(content, { lastLength, parts, text })

  return text
}

// Cheap streaming-stable "does this message have visible text" check: returns
// on the first non-whitespace text part without concatenating the whole
// message. Used as a useAuiState selector so its boolean output stays stable
// across token flushes (flips false→true once per turn).
export function contentHasVisibleText(content: unknown): boolean {
  if (typeof content === 'string') {
    return content.trim().length > 0
  }

  if (!Array.isArray(content)) {
    return false
  }

  for (const part of content) {
    if (partText(part).trim().length > 0) {
      return true
    }
  }

  return false
}

export function messageAttachmentRefs(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return EMPTY_ATTACHMENT_REFS
  }

  return value.every(ref => typeof ref === 'string') ? value : EMPTY_ATTACHMENT_REFS
}

export function pickPrimaryPreviewTarget(targets: string[]): string[] {
  if (targets.length <= 1) {
    return targets
  }

  const localUrl = targets.find(value => /^https?:\/\/(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])/i.test(value))

  return [localUrl || targets[targets.length - 1]]
}

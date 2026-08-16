import {
  AssistantRuntimeImpl,
  BaseAssistantRuntimeCore,
  ExternalStoreThreadListRuntimeCore,
  ExternalStoreThreadRuntimeCore,
  hasUpcomingMessage
} from '@assistant-ui/core/internal'
import {
  type AssistantRuntime,
  type ExternalStoreAdapter,
  fromThreadMessageLike,
  generateId,
  type ThreadMessage,
  useRuntimeAdapters
} from '@assistant-ui/react'
import { useEffect, useMemo, useState } from 'react'

const EMPTY_ARRAY = Object.freeze([])

const shallowEqual = (a: object, b: object): boolean => {
  const aKeys = Object.keys(a)

  if (aKeys.length !== Object.keys(b).length) {
    return false
  }

  for (const key of aKeys) {
    if (a[key as keyof typeof a] !== b[key as keyof typeof b]) {
      return false
    }
  }

  return true
}

const getThreadListAdapter = (store: ExternalStoreAdapter) => store.adapters?.threadList ?? {}

/**
 * Write only the items whose (message, parentId) pair actually moved.
 *
 * `useRuntimeMessageRepository` caches normalized ThreadMessages by source
 * identity, so a settled turn keeps the SAME object across renders. That makes
 * an identity check a sound "did this change?" test: during streaming exactly
 * one item — the growing tail — differs, and the other N-1 writes were pure
 * overhead that grew with transcript length.
 *
 * Returns false when the export is stale (an id in `existing` is gone, or an
 * incoming message has no repository entry yet), so the caller falls back to
 * the full rebuild rather than guessing.
 */
function applyChangedMessages(
  repository: ExternalStoreThreadRuntimeCore['repository'],
  existing: readonly { message: ThreadMessage; parentId: string | null }[],
  incoming: readonly { message: ThreadMessage; parentId: string | null }[]
): boolean {
  if (existing.length !== incoming.length) {
    return false
  }

  const existingById = new Map(existing.map(item => [item.message.id, item]))

  for (const item of incoming) {
    const current = existingById.get(item.message.id)

    if (!current) {
      return false
    }

    // Reference identity, not deep equality: the conversion cache guarantees a
    // stable object for an unchanged turn, and a changed turn is a new object.
    if (current.message !== item.message || current.parentId !== item.parentId) {
      repository.addOrUpdateMessage(item.parentId, item.message)
    }
  }

  return true
}

/**
 * Order `items` so a message is always inserted after its parent.
 *
 * `addOrUpdateMessage` has two hard requirements, both confirmed by probing the
 * repository directly: the parent must already exist ("addOrUpdateMessage: Parent
 * message not found"), and a message may not be linked beneath something that is
 * currently its own descendant ("performOp/link: A message with the same id
 * already exists in the parent tree" — worded as an id clash, but it is a cycle
 * check).
 *
 * A plain linear transcript already satisfies this, so this is a no-op for the
 * common case and returns the input array untouched. It matters when the incoming
 * order does not happen to be parent-first, where replaying it verbatim throws and
 * leaves a half-built tree that every later sync reconciles against — the state
 * behind an unrecoverable "workspace failed to render".
 *
 * Entries whose parent is absent from `items` are emitted in their original
 * relative order at the end, so a genuinely malformed input still reaches the
 * repository and surfaces its own error rather than being silently dropped here.
 */
function parentsBeforeChildren<T extends { message: { id: string }; parentId: null | string }>(
  items: readonly T[]
): readonly T[] {
  const byId = new Map(items.map(item => [item.message.id, item]))
  const ordered: T[] = []
  const placed = new Set<string>()
  const visiting = new Set<string>()
  let needsReorder = false

  const place = (item: T): void => {
    const id = item.message.id

    if (placed.has(id) || visiting.has(id)) {
      return
    }

    visiting.add(id)

    const parentId = item.parentId
    const parent = parentId ? byId.get(parentId) : undefined

    if (parent && !placed.has(parentId as string)) {
      needsReorder = true
      place(parent)
    }

    visiting.delete(id)
    placed.add(id)
    ordered.push(item)
  }

  for (const item of items) {
    place(item)
  }

  // Preserve identity when nothing moved — callers compare arrays, and a fresh
  // array for every sync would defeat that.
  return needsReorder ? ordered : items
}

export function syncRepositoryIncrementally(
  runtime: ExternalStoreThreadRuntimeCore,
  messageRepository: NonNullable<ExternalStoreAdapter['messageRepository']>
): readonly ThreadMessage[] {
  const repository = (runtime as unknown as { repository: ExternalStoreThreadRuntimeCore['repository'] }).repository
  const incoming = messageRepository.messages
  const existing = repository.export().messages
  const headId = messageRepository.headId ?? incoming.at(-1)?.message.id ?? null

  // A thread switch swaps in a fully-DISJOINT transcript (no id carries over).
  // Reconciling two unrelated trees in place — grafting the new chain onto the
  // old one, then pruning — can strand a stale head/branch, so there's nothing
  // to preserve: clear the tree first (leaves→root), then rebuild clean.
  const incomingIds = new Set(incoming.map(({ message }) => message.id))
  const disjoint = existing.length > 0 && !existing.some(({ message }) => incomingIds.has(message.id))

  // Re-parenting has to take the same clear-and-rebuild path.
  //
  // `addOrUpdateMessage` cannot move a message that is already in the tree: the
  // repository walks up from the new parent and throws if it meets the message
  // itself ("performOp/link: A message with the same id already exists in the
  // parent tree" — misleading wording for what is really a cycle check). So if a
  // message keeps its id but acquires a different parent, updating it in place
  // can link it beneath its own descendant and take the whole thread render down
  // with an unrecoverable error — which is what happened to windro's session: it
  // rendered as "workspace failed to render" with a Retry that could not
  // succeed, because every retry replayed the same restructure.
  //
  // Detected up front rather than caught after the fact, because a partially
  // mutated tree is not safe to keep reconciling against.
  const existingParents = new Map(existing.map(({ message, parentId }) => [message.id, parentId ?? null]))

  const reparented = incoming.some(
    ({ message, parentId }) => existingParents.has(message.id) && existingParents.get(message.id) !== (parentId ?? null)
  )

  // Steady-state streaming: same message set, one item changed. Skip the
  // whole-transcript rewrite, the prune scan, and the second export. resetHead
  // deletes the head's descendants, so it only runs when the head really moved.
  if (!disjoint && !reparented && applyChangedMessages(repository, existing, incoming)) {
    if (repository.headId !== headId) {
      repository.resetHead(headId)
    }

    return repository.getMessages()
  }

  if (disjoint || reparented) {
    for (const { message } of [...existing].reverse()) {
      repository.deleteMessage(message.id)
    }
  }

  try {
    for (const { message, parentId } of parentsBeforeChildren(incoming)) {
      repository.addOrUpdateMessage(parentId, message)
    }
  } catch (error) {
    // Last resort. The check above covers the restructure we understand, but a
    // throw here leaves a half-updated tree that every later sync reconciles
    // against, so the thread stays broken until the app restarts — the user sees
    // a dead "workspace failed to render" with a Retry that cannot work.
    //
    // Rebuilding from empty is always representable: `incoming` is a complete
    // transcript, not a delta. The cost is one full re-render, which is the
    // correct trade against an unrecoverable thread. Logged rather than
    // swallowed, because reaching this means the up-front detection missed a case
    // and that is worth knowing.
    console.warn('[hermes] incremental thread sync failed; rebuilding from scratch', error)

    for (const { message } of [...repository.export().messages].reverse()) {
      repository.deleteMessage(message.id)
    }

    for (const { message, parentId } of parentsBeforeChildren(incoming)) {
      repository.addOrUpdateMessage(parentId, message)
    }
  }

  for (const { message } of repository.export().messages) {
    if (!incomingIds.has(message.id)) {
      repository.deleteMessage(message.id)
    }
  }

  repository.resetHead(headId)

  return repository.getMessages()
}

class IncrementalExternalStoreThreadRuntimeCore extends ExternalStoreThreadRuntimeCore {
  override __internal_setAdapter(store: ExternalStoreAdapter): void {
    if (!store.messageRepository) {
      super.__internal_setAdapter(store)

      return
    }

    const self = this as unknown as {
      _assistantOptimisticId: null | string
      _capabilities: object
      _messages: readonly ThreadMessage[]
      _notifyEventSubscribers: (event: string, payload: object) => void
      _notifySubscribers: () => void
      _store?: ExternalStoreAdapter
    }

    if (self._store === store) {
      return
    }

    const isRunning = store.isRunning ?? false
    this.isDisabled = store.isDisabled ?? false

    const oldStore = self._store
    self._store = store

    if (this.extras !== store.extras) {
      this.extras = store.extras
    }

    const newSuggestions = store.suggestions ?? EMPTY_ARRAY

    if (!shallowEqual(this.suggestions, newSuggestions)) {
      this.suggestions = newSuggestions
    }

    const newCapabilities = {
      switchToBranch: store.setMessages !== undefined,
      switchBranchDuringRun: false,
      edit: store.onEdit !== undefined,
      reload: store.onReload !== undefined,
      cancel: store.onCancel !== undefined,
      speech: store.adapters?.speech !== undefined,
      dictation: store.adapters?.dictation !== undefined,
      voice: store.adapters?.voice !== undefined,
      unstable_copy: store.unstable_capabilities?.copy !== false,
      attachments: !!store.adapters?.attachments,
      feedback: !!store.adapters?.feedback,
      queue: false
    }

    if (!shallowEqual(self._capabilities, newCapabilities)) {
      self._capabilities = newCapabilities
    }

    if (oldStore && oldStore.isRunning === store.isRunning && oldStore.messageRepository === store.messageRepository) {
      self._notifySubscribers()

      return
    }

    if (self._assistantOptimisticId) {
      this.repository.deleteMessage(self._assistantOptimisticId)
      self._assistantOptimisticId = null
    }

    const messages = syncRepositoryIncrementally(this, store.messageRepository)

    if (messages.length > 0) {
      this.ensureInitialized()
    }

    if ((oldStore?.isRunning ?? false) !== (store.isRunning ?? false)) {
      self._notifyEventSubscribers(store.isRunning ? 'runStart' : 'runEnd', {})
    }

    // metadata.isOptimistic keeps this placeholder ephemeral: core evicts
    // off-branch optimistic messages on head moves and omits them from export().
    if (hasUpcomingMessage(isRunning, messages)) {
      const optimisticId = generateId()
      this.repository.addOrUpdateMessage(
        messages.at(-1)?.id ?? null,
        fromThreadMessageLike({ role: 'assistant', content: [], metadata: { isOptimistic: true } }, optimisticId, {
          type: 'running'
        })
      )
      self._assistantOptimisticId = optimisticId
    }

    this.repository.resetHead(self._assistantOptimisticId ?? messages.at(-1)?.id ?? null)
    self._messages = this.repository.getMessages()
    self._notifySubscribers()
  }
}

class IncrementalExternalStoreRuntimeCore extends BaseAssistantRuntimeCore {
  threads: ExternalStoreThreadListRuntimeCore

  constructor(adapter: ExternalStoreAdapter) {
    super()

    this.threads = new ExternalStoreThreadListRuntimeCore(
      getThreadListAdapter(adapter),
      () => new IncrementalExternalStoreThreadRuntimeCore(this._contextProvider, adapter)
    )
  }

  setAdapter(adapter: ExternalStoreAdapter): void {
    this.threads.__internal_setAdapter(getThreadListAdapter(adapter))
    this.threads.getMainThreadRuntimeCore().__internal_setAdapter(adapter)
  }
}

export function useIncrementalExternalStoreRuntime<T extends ThreadMessage>(
  store: ExternalStoreAdapter<T>
): AssistantRuntime {
  const [runtime] = useState(() => new IncrementalExternalStoreRuntimeCore(store as ExternalStoreAdapter))

  useEffect(() => {
    runtime.setAdapter(store as ExternalStoreAdapter)
  })

  const { modelContext } = useRuntimeAdapters() ?? {}

  useEffect(() => {
    if (!modelContext) {
      return undefined
    }

    return runtime.registerModelContextProvider(modelContext)
  }, [modelContext, runtime])

  return useMemo(() => new AssistantRuntimeImpl(runtime), [runtime])
}

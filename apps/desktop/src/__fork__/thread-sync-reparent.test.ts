/**
 * Guard: a restructured transcript must not take the thread render down.
 *
 * windro opened a project session and got a dead end:
 *
 *   "workspace" failed to render
 *   MessageRepository(performOp/link): A message with the same id already exists
 *   in the parent tree.
 *
 * with a Retry button that could never succeed, because every retry replayed the
 * same restructure. The session was unusable.
 *
 * The message is misleading. assistant-ui raises it from a CYCLE check — it walks
 * up from the new parent and throws if it meets the message itself
 * (@assistant-ui/core message-repository.ts). Nothing had a duplicate id: the
 * database held 89 messages with zero repeated ids, and running the real
 * converter over that transcript produced zero duplicates either.
 *
 * The real cause is that `syncRepositoryIncrementally` updated messages in place
 * on the assumption that `addOrUpdateMessage` can re-parent an existing message.
 * It cannot. When a message keeps its id but gains a different parent, updating it
 * in place can link it beneath its own descendant, and the throw leaves a
 * half-mutated tree that every later sync reconciles against — so the thread stays
 * broken until the app restarts.
 *
 * Fork-owned directory; upstream has no src/__fork__/.
 */

import { fromThreadMessageLike, getAutoStatus, MessageRepository } from '@assistant-ui/core/internal'
import type { ExportedMessageRepository, ThreadMessage } from '@assistant-ui/react'
import { describe, expect, it, vi } from 'vitest'

import { syncRepositoryIncrementally } from '@/lib/incremental-external-store-runtime'

const STATUS = getAutoStatus(false, false, false, false, undefined)

function message(id: string, text: string): ThreadMessage {
  return fromThreadMessageLike({ content: [{ text, type: 'text' }], role: 'assistant' }, id, STATUS)
}

function runtimeWith(items: { message: ThreadMessage; parentId: null | string }[]) {
  const repository = new MessageRepository()

  for (const { message: item, parentId } of items) {
    repository.addOrUpdateMessage(parentId, item)
  }

  if (items.length > 0) {
    repository.resetHead(items.at(-1)?.message.id ?? null)
  }

  return { repository } as unknown as Parameters<typeof syncRepositoryIncrementally>[0]
}

function chain(messages: ThreadMessage[]) {
  return messages.map((item, index) => ({
    message: item,
    parentId: index === 0 ? null : messages[index - 1].id
  }))
}

function exported(
  items: { message: ThreadMessage; parentId: null | string }[],
  headId?: string
): ExportedMessageRepository {
  // A real export names the LEAF of the chain as head. `resetHead` deletes the
  // head's descendants, so naming an interior node legitimately prunes the tail —
  // which is a fixture error, not a bug in the code under test.
  return { headId: headId ?? items.at(-1)?.message.id ?? null, messages: items }
}

/** The ids the repository currently holds, head-first order not assumed. */
function idsIn(runtime: Parameters<typeof syncRepositoryIncrementally>[0]): string[] {
  const repository = (runtime as unknown as { repository: MessageRepository }).repository

  return repository
    .export()
    .messages.map(({ message: item }) => item.id)
    .sort()
}

describe('a reordered transcript renders instead of throwing', () => {
  it('survives a message being linked under its own current descendant', () => {
    // The exact trigger, established by probing the repository directly: it
    // throws only on a genuine CYCLE — linking a node under something that is
    // still its own descendant.
    //
    // Order is what makes this fatal rather than harmless. The update loop walks
    // `incoming` in transcript order, so listing `a` (now parented to b) BEFORE
    // `b` (now root) links a under b while b is still a's child. Reverse the two
    // entries and the same restructure applies cleanly — which is why an earlier
    // version of this test passed against the unfixed code and proved nothing.
    const a = message('a', 'first')
    const b = message('b', 'second')
    const runtime = runtimeWith(chain([a, b]))

    const cycleOrder = [
      { message: a, parentId: b.id },
      { message: b, parentId: null }
    ]

    expect(() => syncRepositoryIncrementally(runtime, exported(cycleOrder, 'a'))).not.toThrow()
    expect(idsIn(runtime)).toEqual(['a', 'b'])
  })

  it('reverses a whole chain without throwing', () => {
    const messages = Array.from({ length: 6 }, (_, index) => message(`m-${index}`, `body ${index}`))
    const runtime = runtimeWith(chain(messages))

    expect(() => syncRepositoryIncrementally(runtime, exported(chain([...messages].reverse())))).not.toThrow()
    expect(idsIn(runtime)).toEqual(['m-0', 'm-1', 'm-2', 'm-3', 'm-4', 'm-5'])
  })

  it('rebuilds rather than half-updating, so the next sync is still sane', () => {
    // The property that made the original failure permanent: a partially mutated
    // tree poisons every later reconcile.
    const messages = ['a', 'b', 'c'].map(id => message(id, id))
    const runtime = runtimeWith(chain(messages))

    syncRepositoryIncrementally(runtime, exported(chain([...messages].reverse())))

    const settled = chain(messages)

    expect(() => syncRepositoryIncrementally(runtime, exported(settled))).not.toThrow()
    expect(idsIn(runtime)).toEqual(['a', 'b', 'c'])
  })
})

describe('the fast path is still taken when nothing restructures', () => {
  it('writes only the changed tail for a pure text update', () => {
    // The optimisation this file exists for must not be lost to the new check:
    // same ids, same parents, one body changed.
    const settled = Array.from({ length: 50 }, (_, index) => message(`m-${index}`, `body ${index}`))
    const runtime = runtimeWith(chain(settled))
    const repository = (runtime as unknown as { repository: MessageRepository }).repository
    const addOrUpdate = vi.spyOn(repository, 'addOrUpdateMessage')

    const changed = [...settled.slice(0, -1), message('m-49', 'body 49 extended')]
    syncRepositoryIncrementally(runtime, exported(chain(changed)))

    expect(
      addOrUpdate.mock.calls.length,
      'a plain delta now rewrites the whole transcript — the incremental path was lost'
    ).toBeLessThan(settled.length)
  })

  it('still clears and rebuilds for a fully disjoint thread switch', () => {
    const first = ['a', 'b'].map(id => message(id, id))
    const runtime = runtimeWith(chain(first))
    const second = ['x', 'y'].map(id => message(id, id))

    syncRepositoryIncrementally(runtime, exported(chain(second)))

    expect(idsIn(runtime), 'stale messages survived a thread switch').toEqual(['x', 'y'])
  })
})
